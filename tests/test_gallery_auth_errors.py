"""图库拒绝请求时的错误映射契约：提示必须指向真实原因，且限流不得触发熔断。

图库（twf-gallery）自 4de7304 起对列表接口与图片下载都强制鉴权，拒绝时返回 401/403/429。
这三种状态码承载的原因并不相同：

  - 令牌缺失 / 错误 / 已被注销  -> 应引导用户更新令牌
  - IP 被临时封禁（多次失败）   -> 应引导用户联系管理员，改令牌无效
  - 请求过于频繁（爬虫判定/限流） -> 应引导用户稍后重试，改令牌同样无效
  - 今日配额用尽                -> 应引导用户次日再试

状态码本身不足以区分上述情形，因此必须解析响应体中的 `error` 字段。若一律提示「检查
令牌」，用户在被限流时会反复修改本已正确的配置，排查方向被彻底带偏。响应体不可读或
格式异常时仍须返回按状态码给出的通用提示：错误映射自身不允许成为新的失败点。

429 还须与 401/403 一样在重试循环内直接抛出、不记入熔断器失败。限流属于客户端侧条件而
非上游故障，一旦计入，单次突发限流即可令图库被误熔断，此后全部请求快速失败并回退本地，
现象从「请求过快」变成「图库已挂」。鉴权映射还必须在全部拉取入口（通用入口、指定 URL
入口、图片下载、萝莉与正太）生效，任一入口遗漏都会让用户重新看到裸的 HTTPError。
"""
import io
import ast
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
GALLERY = PLUGIN / 'gallery.py'


def _load_auth_error_reason():
    """从源码中摘出 `_auth_error_reason` 并单独执行，以便直接验证其映射表。

    gallery.py 依赖 gsuid_core（测试环境未安装），整包导入不可行；被测函数只用到 json
    与 HTTPError，按 AST 抽取后独立编译即可。此举同时把测试约束在错误映射本身：若该
    函数被改名或删除，此处以 AssertionError 显式失败，而不是让后续用例因缺符号而报错。
    """
    source = GALLERY.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_auth_error_reason':
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {'json': json, 'HTTPError': HTTPError}
            exec(compile(module, str(GALLERY), 'exec'), namespace)  # noqa: S102
            return namespace['_auth_error_reason']
    raise AssertionError('gallery.py 里找不到 _auth_error_reason')


auth_error_reason = _load_auth_error_reason()


# 构造带可读响应体的 HTTPError：部分用例刻意省略 body，用于覆盖图库未提供 `error` 字段
# 或响应体为空时映射退化为按状态码提示的分支。响应体以 BytesIO 承载，因为 HTTPError.read()
# 只在存在响应流时才可调用
def _http_error(code: int, body: dict | None = None) -> HTTPError:
    payload = b'' if body is None else json.dumps(body).encode('utf-8')
    return HTTPError('https://twf.xlinxc.cn/v1/api/xwuid/roles', code, 'err', {}, io.BytesIO(payload))


# 逐个覆盖图库可能给出的拒绝原因，确保提示与原因一一对应
class AuthErrorReasonTests(unittest.TestCase):
    def test_missing_token_403_tells_user_to_check_token(self) -> None:
        # 403 + token_required 是唯一确实属于「配置问题」的分支，必须点名配置项，
        # 否则用户无从知晓应修改哪一项设置
        msg = auth_error_reason(_http_error(403, {'error': 'token_required'}), what='请求图库接口')
        self.assertIn('令牌', msg)
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_ip_ban_is_reported_as_ban_not_token_problem(self) -> None:
        """IP 被封时改令牌无效，必须明确说是封禁。"""
        # 封禁与令牌失效同为 403，误判会让用户反复更换令牌而问题始终不消失；
        # 断言不得出现配置项名称，防止提示把用户引向错误的排查方向
        msg = auth_error_reason(_http_error(403, {'error': 'ip_banned'}), what='请求图库接口')
        self.assertIn('IP', msg)
        self.assertIn('封禁', msg)
        self.assertNotIn('DailyWifeGalleryToken', msg)

    def test_rate_limited_says_retry_later(self) -> None:
        # 限流是可自愈的：提示须引导等待而非改配置，因此同样不得出现配置项名称
        msg = auth_error_reason(_http_error(429, {'error': 'rate_limited'}), what='请求图库接口')
        self.assertIn('频繁', msg)
        self.assertNotIn('DailyWifeGalleryToken', msg)

    def test_crawl_detected_is_treated_as_throttle(self) -> None:
        # 反爬判定以 429 返回，与限流同属「稍后重试」语义；若漏掉该取值会退化成
        # 按 429 状态码兜底的提示，用户仍能得到正确指引，但错误原因会被抹掉
        msg = auth_error_reason(_http_error(429, {'error': 'crawl_detected'}), what='请求图库接口')
        self.assertIn('频繁', msg)

    def test_quota_exceeded_mentions_quota(self) -> None:
        # 配额用尽同样返回 429，但「稍后重试」是无效指引：重试不会恢复额度，
        # 必须与限流区分，明确指向次日再试或调整配额
        msg = auth_error_reason(_http_error(429, {'error': 'quota_exceeded'}), what='请求图库接口')
        self.assertIn('配额', msg)

    def test_revoked_token_401_mentions_token_update(self) -> None:
        # 401 无响应体时的默认语义是令牌无效或已注销，须给出配置项名称；
        # 否则用户只能看到「拒绝访问」而无从修复
        msg = auth_error_reason(_http_error(401), what='请求图库接口')
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_429_without_body_still_says_retry_later(self) -> None:
        """图库若没给 error 字段，仅凭 429 也要给出正确指引。"""
        # 图库版本升级可能不再返回 error 字段；429 本身已足以判定为限流，
        # 该分支保证诊断信息的可用性不依赖上游响应体的稳定性
        msg = auth_error_reason(_http_error(429), what='请求图库接口')
        self.assertIn('频繁', msg)

    def test_403_without_body_falls_back_to_token_hint(self) -> None:
        # 403 缺少区分依据时按最可能的原因（令牌问题）提示，并保留配置项名；
        # 这是刻意的保守降级，而非把 403 一律等同于令牌错误
        msg = auth_error_reason(_http_error(403), what='请求图库接口')
        self.assertIn('DailyWifeGalleryToken', msg)

    def test_malformed_body_does_not_raise(self) -> None:
        """响应体不是 JSON 时必须优雅降级，不能把解析异常抛给用户。"""
        # 网关或代理在鉴权失败时可能返回 HTML 错误页；若解析异常穿透，用户看到的
        # 将是 JSONDecodeError 而非可操作的提示，因此映射内部必须吞掉解析失败
        err = HTTPError('https://h/x', 403, 'err', {}, io.BytesIO(b'<html>not json</html>'))
        msg = auth_error_reason(err, what='下载图片')
        self.assertIn('下载图片', msg)

    def test_empty_body_does_not_raise(self) -> None:
        # 空响应体不得触发 read 路径上的异常；被拒绝的请求仍须得到一段完整提示
        msg = auth_error_reason(_http_error(403), what='下载图片')
        self.assertIn('下载图片', msg)

    def test_what_prefix_is_used(self) -> None:
        """不同调用点要能区分（列表 vs 图片），便于用户定位。"""
        # 同一套映射服务于列表拉取与图片下载，前缀决定用户能否判断失败发生在哪一步
        self.assertIn('下载图片', auth_error_reason(_http_error(403), what='下载图片'))
        self.assertIn('请求图库接口', auth_error_reason(_http_error(403), what='请求图库接口'))

    def test_all_messages_are_chinese_and_actionable(self) -> None:
        # 面向终端用户的提示必须是中文且以句号收束，作为对全部分支的横向约束：
        # 新增映射分支时若漏掉本地化或标点，会在此处立即暴露
        for err in (_http_error(401), _http_error(403, {'error': 'ip_banned'}), _http_error(429)):
            msg = auth_error_reason(err, what='请求图库接口')
            self.assertTrue(any('\u4e00' <= ch <= '\u9fff' for ch in msg), msg)
            self.assertTrue(msg.endswith('。') or msg.endswith('。'), msg)


class ThrottleDoesNotTripBreakerTests(unittest.TestCase):
    """429 必须与 401/403 同类处理：直接抛出，不计入熔断失败。"""

    def setUp(self) -> None:
        # 以源码文本切片做静态断言：限流语义体现在「重试循环里不重试、不喂熔断器」的
        # 分支结构上，仅靠行为测试需要构造熔断器与时钟，反而更难锁定该分支
        self.source = GALLERY.read_text(encoding='utf-8')
        self.helper = self.source[
            self.source.index('def _http_get_with_retry('):self.source.index('def _auth_error_reason(')
        ]

    def test_429_is_in_the_no_retry_branch(self) -> None:
        # 该分支集合一旦丢掉 429，限流请求会被当作可重试的上游抖动：既放大对上游的
        # 请求量，又累积熔断失败次数，最终把限流误判为图库故障
        marker = 'if exc.code in {401, 403, 429}:'
        self.assertIn(marker, self.helper, '429 必须走「不重试且不喂熔断器」的分支')

    def test_branch_raises_without_recording_failure(self) -> None:
        # 分支内必须直接抛出而非继续循环，且在抛出前不得留下 record_failure 调用；
        # 限流是客户端侧条件，计入熔断会让一次突发限流触发全量回退本地
        marker = 'if exc.code in {401, 403, 429}:'
        branch = self.helper[self.helper.index(marker):]
        self.assertIn('raise', branch[:120])
        self.assertNotIn('record_failure', branch[:120])


class AllEntryPointsReportFriendlyErrorsTests(unittest.TestCase):
    """三个拉取入口都必须把 401/403/429 转成中文提示。

    原先 `_fetch_gallery_payload_from_url_sync`（战双 / 测试图库走这条）会让
    HTTPError 直接冒泡，用户只看到 `HTTP Error 403: Forbidden`，无法判断
    是令牌问题还是 IP 被封 —— 排查方向完全跑偏。
    """

    def setUp(self) -> None:
        self.source = GALLERY.read_text(encoding='utf-8')

    def _function_body(self, name: str, next_name: str) -> str:
        # 以相邻函数名为界切出函数体：被测入口数量多且体量小，逐段静态断言比在各条
        # 调用链上构造模拟请求更能直接锁定「入口是否接入了统一映射」
        start = self.source.index(f'def {name}(')
        end = self.source.index(f'def {next_name}(')
        return self.source[start:end]

    def test_url_entry_point_maps_auth_errors(self) -> None:
        # 指定 URL 入口是历史上唯一遗漏映射的路径，必须同时断言「复用统一映射」与
        # 「覆盖 429」，后者缺失时限流仍会退化为上游故障
        body = self._function_body('_fetch_gallery_payload_from_url_sync', '_parse_role_candidates')
        self.assertIn('_auth_error_reason', body)
        self.assertIn('{401, 403, 429}', body)

    def test_default_entry_point_maps_auth_errors(self) -> None:
        body = self._function_body('_fetch_gallery_payload_sync', '_fetch_gallery_payload_from_url_sync')
        self.assertIn('_auth_error_reason', body)

    def test_image_download_maps_auth_errors(self) -> None:
        # 图片下载走独立入口（签名 URL 失效或图片级鉴权拒绝都发生在这里），
        # 遗漏映射会让「列表能拉取、图片下载全失败」的现象缺失可读提示
        body = self._function_body('_download_image_sync', '_download_image')
        self.assertIn('_auth_error_reason', body)

    def test_loli_and_shota_map_auth_errors(self) -> None:
        # 萝莉与正太各有独立模块，容易在修改通用入口时被遗漏；逐文件断言其复用统一
        # 映射并覆盖 429，避免同类错误映射出现多份实现而逐渐分叉
        for name in ('loli.py', 'shota.py'):
            source = (PLUGIN / name).read_text(encoding='utf-8')
            self.assertIn('_auth_error_reason', source, f'{name} 应复用统一错误映射')
            self.assertIn('{401, 403, 429}', source, f'{name} 应覆盖 429')


if __name__ == '__main__':
    unittest.main()
