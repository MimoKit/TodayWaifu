# QQ 官方机器人模式的契约测试。
#
# 该模式的职责边界有三条，任一条被破坏都会造成用户可见的故障：
# 1. 总开关关闭时必须返回 False，让调用方走既有 OB11 发送路径；若误判为已发送，
#    用户会收不到任何结果图。
# 2. 事件来源不是 QQ 官方机器人时同样返回 False；否则普通个人账号会被强制走
#    Markdown 通路，而 OB11 不支持 Markdown，消息会发送失败。
# 3. 上传链路失败必须返回 False 而非抛异常；素材库是外部依赖，其故障只应影响观感。
#
# 另外锁定「@ 仅群聊」「Markdown 展示尺寸只缩不放」两条渲染约定，以及「不再有腾讯云
# COS」这一依赖边界：图床一律走 QQ 开放平台的素材管理。
#
# 本插件包无法作为顶层包导入（其内层模块使用 `..daily_wife_config` 这类二级相对导入，
# 脱离 gsuid_core 运行环境即失败），故沿用本仓库既有做法：按 AST 抽取目标函数，注入
# 替身全局名后单独 exec。抽取以函数名为准，函数在文件内移动到别处不会使测试失效。
import io
import ast
import unittest
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from importlib.util import module_from_spec, spec_from_file_location
from urllib.request import Request

from PIL import Image
from qgbot import md as qmd

ROOT = Path(__file__).resolve().parents[1]

# qq_official 的扩展名 → MIME 映射复用 media_plan，exec 时需注入同名全局。
_spec = spec_from_file_location(
    'todaywaifu_media_plan_for_qq_official', ROOT / 'TodayWaifu' / 'media_plan.py'
)
if _spec is None or _spec.loader is None:
    raise RuntimeError('cannot load media_plan')
_plan = module_from_spec(_spec)
_spec.loader.exec_module(_plan)
_plan_content_type = _plan.content_type_for_extension

_MODULE_PATH = ROOT / 'TodayWaifu' / 'qq_official.py'

_FUNCTION_NAMES = {
    '_qq_bot_mode_enabled',
    '_is_official_qq_bot',
    '_image_extension_and_size',
    '_content_type_for_extension',
    '_markdown_image_size',
    '_upload_image',
    '_markdown_escape',
    '_build_image_markdown',
    '_try_send_official_qq_image',
    '_meme_generator_url',
    '_meme_generator_configured',
    '_multipart_form_data',
    '_generate_petpet_meme_sync',
    '_generate_petpet_meme',
    '_official_markdown_inline_text',
    '_build_petpet_notice_markdown',
    '_build_petpet_notice_text',
    '_is_official_qq_event',
    '_blockquote_markdown',
}

# 测试用配置：素材图床开启，凭据齐全（不指向真实资源）。
_CONFIG = {
    'DailyWifeQqBotMode': True,
    'DailyWifeQqBotMediaEnabled': True,
    'DailyWifeQqBotMediaAppId': 'app-id',
    'DailyWifeQqBotMediaAppSecret': 'app-secret',
    'DailyWifeQqBotMediaApiBase': 'https://api.bot.qq.com',
    'DailyWifeQqBotMediaQuota': 100,
    'DailyWifeQqBotMediaReserve': 10,
}


class _FakeMessage:
    def __init__(self, type: str, data: Any) -> None:
        self.type = type
        self.data = data


def _fake_markdown(content: str, buttons: Any = None) -> list[_FakeMessage]:
    return [_FakeMessage('markdown', content)]


def _fake_image_segment() -> Any:
    raise AssertionError('本测试不应构造图片段')


class _StubLogger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None

    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def debug(self, *args: object, **kwargs: object) -> None:
        return None


def _png(size: tuple[int, int] = (800, 1200)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, 'white').save(buffer, format='PNG')
    return buffer.getvalue()


def _fake_bot(bot_id: str, recorder: '_SentRecorder') -> SimpleNamespace:
    return SimpleNamespace(
        ev=SimpleNamespace(real_bot_id=bot_id, bot_id=bot_id),
        send=recorder.send,
    )


class _SentRecorder:
    """记录 bot.send 收到的消息，用于断言是否走了 Markdown 通路。"""

    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send(self, message: Any) -> None:
        self.messages.append(message)


class _StubRequest:
    """替身请求对象，仅记录构造参数供断言。"""

    def __init__(self, url: str, data: Any = None, headers: Any = None, method: str = 'GET') -> None:
        self.url = url
        self.data = data
        self.headers = headers or {}
        self.method = method


def _stub_urlopen(request: Any, timeout: int = 30) -> Any:
    raise AssertionError('本测试不应发起真实网络请求')


def _load_namespace(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """抽取目标函数并注入替身全局名后 exec，返回其命名空间。

    qq_official.py 引用的框架符号（Bot / MessageSegment / run_blocking / logger 等）
    不随函数体抽取，需在此补齐，否则 exec 后调用会抛 NameError。
    """
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    tree = ast.parse(_MODULE_PATH.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _FUNCTION_NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            # 保留模块常量（Markdown 尺寸上限、扩展名映射、官方 bot id 集合等）
            body.append(node)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            # 带类型标注的常量同样是模块状态（如素材图床的可替换实现），缺了会让被测函数 NameError
            body.append(node)

    values = dict(config if config is not None else _CONFIG)

    async def _run_blocking_inline(func: Any, *args: Any, **kwargs: Any) -> Any:
        # 测试内联执行，避免引入插件线程池
        return func(*args, **kwargs)

    namespace: dict[str, Any] = {
        'Any': Any,
        'Bot': object,
        'MessageSegment': SimpleNamespace(markdown=_fake_markdown, image=_fake_image_segment),
        'BytesIO': io.BytesIO,
        'Image': Image,
        'qmd': qmd,
        'logger': _StubLogger(),
        'run_blocking': _run_blocking_inline,
        '_cfg': lambda key: values.get(key),
        '_cfg_bool': lambda key, default=False: bool(values.get(key, default)),
        '_main_cfg_bool': lambda key, default=False: bool(values.get(key, default)),
        'content_type_for_extension': _plan_content_type,
        'json': __import__('json'),
        're': __import__('re'),
        'urlparse': __import__('urllib.parse', fromlist=['urlparse']).urlparse,
        'urlopen': _stub_urlopen,
        'Request': _StubRequest,
        'HTTPError': __import__('urllib.error', fromlist=['HTTPError']).HTTPError,
        'URLError': __import__('urllib.error', fromlist=['URLError']).URLError,
        'uuid4': __import__('uuid').uuid4,
        'Union': Any,
        'Protocol': object,
        'LOG_PREFIX': '[测试]',
    }
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, 'qq_official', 'exec'), namespace)
    return namespace


def _bind_fake_upload(namespace: dict[str, Any], url: str = 'qqbot-media://image/v1/token') -> list[bytes]:
    """把素材上传替换成「直接返回平台引用」的替身，记录被上传的字节。"""
    uploads: list[bytes] = []

    async def _upload(image: bytes) -> tuple[str, tuple[int, int]]:
        uploads.append(image)
        return url, (800, 1200)

    namespace['_upload_image'] = _upload
    return uploads


class QqOfficialModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_master_switch_off_returns_false(self) -> None:
        # 关闭总开关必须直接放弃本通路：返回 True 会让调用方以为已发送，
        # 用户实际收不到任何结果图。
        namespace = _load_namespace({**_CONFIG, 'DailyWifeQqBotMode': False})
        recorder = _SentRecorder()
        sent = await namespace['_try_send_official_qq_image'](
            _fake_bot('qqgroup:1', recorder), _png(), '文案', 'U1', True
        )
        self.assertFalse(sent)
        self.assertEqual(recorder.messages, [])

    async def test_non_official_platform_returns_false(self) -> None:
        # 个人账号（onebot）不支持 Markdown，必须交回普通发送路径。
        namespace = _load_namespace()
        recorder = _SentRecorder()
        sent = await namespace['_try_send_official_qq_image'](
            _fake_bot('onebot:1', recorder), _png(), '文案', 'U1', True
        )
        self.assertFalse(sent)
        self.assertEqual(recorder.messages, [])

    async def test_unconfigured_media_degrades_instead_of_raising(self) -> None:
        # 总开关开着但图床未启用 / 缺凭据时必须降级：素材库故障不应使整条命令失败。
        namespace = _load_namespace({**_CONFIG, 'DailyWifeQqBotMediaEnabled': False})
        recorder = _SentRecorder()
        sent = await namespace['_try_send_official_qq_image'](
            _fake_bot('qqgroup:1', recorder), _png(), '文案', 'U1', True
        )
        self.assertFalse(sent)
        self.assertEqual(recorder.messages, [])

    async def test_successful_send_uses_markdown_with_platform_media_url(self) -> None:
        for at_user in (True, False):
            with self.subTest(at_user=at_user):
                namespace = _load_namespace({**_CONFIG, 'DailyWifeAtUser': at_user})
                recorder = _SentRecorder()
                uploads = _bind_fake_upload(namespace)
                image = _png()

                sent = await namespace['_try_send_official_qq_image'](
                    _fake_bot('qqgroup:1', recorder), image, '你今天的老婆是今汐', 'OPENID_1', True
                )

                self.assertTrue(sent)
                self.assertEqual(len(recorder.messages), 1)
                markdown = recorder.messages[0][0]
                self.assertEqual(markdown.type, 'markdown')
                self.assertEqual('<qqbot-at-user id="OPENID_1" />' in markdown.data, at_user)
                self.assertIn('你今天的老婆是今汐', markdown.data)
                self.assertIn('qqbot-media://image/v1/token', markdown.data)
                # 原图必须是上传的那一份，不能被提前压成 base64 或改写
                self.assertEqual(uploads, [image])

    async def test_private_chat_markdown_has_no_mention(self) -> None:
        # 私聊下发 @ 标签会被 QQ 原样显示成无意义文本，必须剥离。
        namespace = _load_namespace()
        markdown = namespace['_build_image_markdown']('https://x.test/a.png', (800, 1200), '文案', 'U1', False)
        self.assertNotIn('qqbot-at-user', markdown)
        self.assertIn('文案', markdown)

    async def test_group_markdown_mentions_trigger_user(self) -> None:
        # 旧协议的 `<@id>` 已被平台标记弃用，必须用当前语法。
        namespace = _load_namespace()
        markdown = namespace['_build_image_markdown']('https://x.test/a.png', (800, 1200), '文案', 'U1', True)
        self.assertIn('<qqbot-at-user id="U1" />', markdown)

    def test_markdown_size_only_shrinks_and_never_upscales(self) -> None:
        namespace = _load_namespace()
        size_of = namespace['_markdown_image_size']
        # 小图保持原尺寸，放大只会让低分辨率图更模糊
        self.assertEqual(size_of((100, 100)), (100, 100))
        # 超大图等比缩放进 260x360 边界
        self.assertEqual(size_of((800, 1200)), (240, 360))
        # 尺寸不可信时回落到兜底值
        self.assertEqual(size_of((0, 0)), (260, 360))

    def test_unsupported_format_is_rejected(self) -> None:
        # 仅允许 QQ Markdown 可稳定渲染的位图格式；其余应报错并走降级
        namespace = _load_namespace()
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), 'white').save(buffer, format='BMP')
        with self.assertRaises(RuntimeError):
            namespace['_image_extension_and_size'](buffer.getvalue())

    def test_official_bot_ids_cover_group_and_guild(self) -> None:
        # 群聊 qqgroup 与频道 qqguild 都属官方机器人；onebot 必须排除
        namespace = _load_namespace()
        is_official = namespace['_is_official_qq_bot']
        self.assertTrue(is_official(SimpleNamespace(ev=SimpleNamespace(real_bot_id='qqgroup:1', bot_id=''))))
        self.assertTrue(is_official(SimpleNamespace(ev=SimpleNamespace(real_bot_id='', bot_id='qqguild:2'))))
        self.assertFalse(is_official(SimpleNamespace(ev=SimpleNamespace(real_bot_id='onebot:1', bot_id='onebot:1'))))


class PetpetNoticeTests(unittest.TestCase):
    """摸头提示及昵称转义的契约。"""

    def test_petpet_notice_contains_compliance_warning(self) -> None:
        # 合规提醒是平台要求，不是装饰；删除它会让使用者无从知晓审核风险
        namespace = _load_namespace()
        markdown = namespace['_build_petpet_notice_markdown']('群友甲')
        self.assertIn('头像合规提醒', markdown)
        self.assertIn('群友甲', markdown)
        self.assertIn('可能触发 QQ 平台审核或被举报', markdown)
        text = namespace['_build_petpet_notice_text']('群友甲')
        self.assertIn('头像合规提醒', text)

    def test_notice_escapes_markdown_metacharacters_in_name(self) -> None:
        # 昵称可能带 Markdown 元字符，直接插入会破坏标题结构
        namespace = _load_namespace()
        markdown = namespace['_build_petpet_notice_markdown']('a*b_c[d]')
        self.assertIn('a\\*b\\_c\\[d\\]', markdown)


class PetpetBackendTests(unittest.TestCase):
    """摸头后端调用的契约。"""

    def test_unconfigured_backend_is_reported(self) -> None:
        namespace = _load_namespace({**_CONFIG, 'DailyWifeMemeGeneratorUrl': ''})
        self.assertFalse(namespace['_meme_generator_configured']())
        with self.assertRaises(RuntimeError):
            namespace['_generate_petpet_meme_sync'](_png((64, 64)))

    def test_multipart_body_contains_args_and_image(self) -> None:
        # 后端按 multipart 解析，字段名与文件名写错会得到 4xx 或空图
        namespace = _load_namespace()
        body, boundary = namespace['_multipart_form_data'](
            {'args': '{"circle": false}'},
            [('images', 'avatar.png', 'image/png', b'BINARY')],
        )
        self.assertIn(f'--{boundary}'.encode(), body)
        self.assertIn(b'name="args"', body)
        self.assertIn(b'name="images"; filename="avatar.png"', body)
        self.assertIn(b'Content-Type: image/png', body)
        self.assertIn(b'BINARY', body)
        self.assertTrue(body.endswith(f'--{boundary}--\r\n'.encode()))

    def test_multipart_filename_cannot_break_headers(self) -> None:
        # 文件名里的引号与换行会截断 multipart 头部，使整个报文被后端判为非法
        namespace = _load_namespace()
        body, _boundary = namespace['_multipart_form_data'](
            {},
            [('images', 'a"b\r\nc.png', 'image/png', b'X')],
        )
        self.assertIn(b'filename="a_b__c.png"', body)

    def test_backend_url_default_is_empty(self) -> None:
        # 回归守卫：表情包后端地址是部署者私有资源，绝不能写成仓库默认值，
        # 否则每个安装者的头像都会发到该后端。
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        default = None
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == 'DailyWifeMemeGeneratorUrl':
                    default = value.args[2]
        self.assertIsNotNone(default, '未找到 DailyWifeMemeGeneratorUrl 配置项')
        self.assertEqual(ast.literal_eval(default), '')

    def test_petpet_rejects_non_image_response(self) -> None:
        # 后端返回非图片（如错误 JSON）时必须报错降级，不能把 JSON 当图片发出去
        namespace = _load_namespace({**_CONFIG, 'DailyWifeMemeGeneratorUrl': 'https://meme.test'})

        class _Resp:
            headers = {'content-type': 'application/json'}

            def __enter__(self) -> '_Resp':
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def read(self, _limit: int = -1) -> bytes:
                return b'{"detail":"error"}'

        namespace['urlopen'] = lambda request, timeout=30: _Resp()
        with self.assertRaises(RuntimeError):
            namespace['_generate_petpet_meme_sync'](_png((64, 64)))


class WifeListMarkdownTests(unittest.TestCase):
    """老婆/老公列表在官方机器人下转 Markdown 引用块的契约。

    官方机器人链路不支持合并转发，且裸文本的多行排版会被折叠成一段，因此列表必须整体
    套成引用块。缺了这一步表现为「列表挤成一坨」或「列表发不出去」。
    """

    def test_blockquote_prefixes_every_line(self) -> None:
        namespace = _load_namespace()
        md = namespace['_blockquote_markdown']('今日老婆列表：\n1. 甲 → 今汐\n2. 乙 → 长离')
        self.assertEqual(
            md.splitlines(),
            ['> 今日老婆列表：', '> 1. 甲 → 今汐', '> 2. 乙 → 长离'],
        )

    def test_blockquote_keeps_blank_lines_inside_the_block(self) -> None:
        # 空行必须保留为单独的 '>'：丢掉会让引用块断开，后续行失去样式
        namespace = _load_namespace()
        md = namespace['_blockquote_markdown']('标题\n\n1. 甲 → 今汐')
        self.assertEqual(md.splitlines(), ['> 标题', '>', '> 1. 甲 → 今汐'])

    def test_blockquote_handles_empty_text(self) -> None:
        # 空列表只有标题，不能返回空串导致消息段内容为空
        namespace = _load_namespace()
        self.assertEqual(namespace['_blockquote_markdown'](''), '>')

    def test_official_event_detected_without_bot_instance(self) -> None:
        # 列表路径只有 Event、拿不到 Bot，故必须支持仅凭事件判断；
        # WS_BOT_ID 也要覆盖：部分适配器只填该字段
        namespace = _load_namespace()
        is_official = namespace['_is_official_qq_event']
        self.assertTrue(is_official(SimpleNamespace(real_bot_id='qqgroup', bot_id='')))
        self.assertTrue(is_official(SimpleNamespace(real_bot_id='', bot_id='qqguild:2')))
        self.assertTrue(is_official(SimpleNamespace(real_bot_id='', bot_id='', WS_BOT_ID='qqgroup')))
        self.assertFalse(is_official(SimpleNamespace(real_bot_id='onebot', bot_id='onebot')))
        self.assertFalse(is_official(SimpleNamespace()))

    def test_list_send_path_consults_official_mode(self) -> None:
        # 回归守卫：列表发送必须先判总开关与官方来源，再走引用块；顺序颠倒会让普通账号
        # 也收到 Markdown（OB11 不支持，消息会发送失败）
        source = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8-sig')
        start = source.index('async def _send_wife_list(')
        end = source.index('\nasync def ', start + 1)
        block = source[start:end]
        self.assertIn('_qq_bot_mode_enabled()', block)
        self.assertIn('_is_official_qq_event(ev)', block)
        self.assertIn('_blockquote_markdown(', block)
        self.assertIn('MessageSegment.markdown(', block)
        # 官方分支必须早于转发分支：否则列表会先被合并转发吃掉
        self.assertLess(block.index('_is_official_qq_event(ev)'), block.index('LIST_FORWARD_THRESHOLD'))


class MemberAvatarCacheTests(unittest.TestCase):
    """群友头像下载继续绕过 qlogo 的长时间缓存。"""

    def test_members_module_cache_busts_qlogo(self) -> None:
        tree = ast.parse((ROOT / 'TodayWaifu' / 'members.py').read_text(encoding='utf-8-sig'))
        functions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {'_cache_busted_avatar_url', '_download_member_avatar'}
        ]
        requests = []

        def open_avatar(request, timeout):
            requests.append(request.full_url)
            return io.BytesIO(b'avatar')

        namespace = {
            'time': SimpleNamespace(time=lambda: 123),
            'urlparse': urlparse,
            'Request': Request,
            'urlopen': open_avatar,
            'logger': _StubLogger(),
            'LOG_PREFIX': '[测试]',
            'URLError': URLError,
            'HTTPError': HTTPError,
        }
        module = ast.Module(body=functions, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, 'member_avatar_download', 'exec'), namespace)
        cases = (
            ('https://qlogo.cn/avatar', 'https://qlogo.cn/avatar?_t=123'),
            ('https://q1.qlogo.cn/g?nk=1#avatar', 'https://q1.qlogo.cn/g?nk=1&_t=123#avatar'),
            ('https://cdn.example.test/a?q=qlogo.cn', 'https://cdn.example.test/a?q=qlogo.cn'),
            ('https://qlogo.cn.example.test/a', 'https://qlogo.cn.example.test/a'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(namespace['_download_member_avatar'](source), b'avatar')
                self.assertEqual(requests[-1], expected)


class QqOfficialConfigContractTests(unittest.TestCase):
    def test_config_page_exists_with_master_switch_first(self) -> None:
        config_source = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        self.assertIn('QQ_BOT_CONFIG_DEFAULT', config_source)
        # 总开关必须是该页第一个配置项
        body = config_source.split('QQ_BOT_CONFIG_DEFAULT', 1)[1]
        first_key = body.index("'DailyWifeQqBotMode'")
        for other in (
            "'DailyWifeQqBotMediaEnabled'",
            "'DailyWifeQqBotMediaAppId'",
            "'DailyWifeMemeGeneratorUrl'",
        ):
            self.assertLess(first_key, body.index(other), f'{other} 不应排在总开关之前')
        self.assertIn("'是否开启QQ机器人模式'", config_source)

    def test_config_page_is_registered_as_third_page(self) -> None:
        # 页面归属由 plugin_name 决定，注册顺序决定页面序号；缺任一项都不会出现在控制台。
        source = (ROOT / 'daily_wife_config.py').read_text(encoding='utf-8-sig')
        self.assertIn("'QQ机器人配置'", source)
        self.assertIn('DailyWifeQqBotConfig.plugin_name', source)
        order = [
            source.index('DailyWifeConfig = StringConfig('),
            source.index('DailyWifeShowConfig = StringConfig('),
            source.index('DailyWifeQqBotConfig = StringConfig('),
        ]
        self.assertEqual(order, sorted(order))

    def test_secrets_are_marked_secret(self) -> None:
        # 密钥项必须以 secret=True 声明，否则会明文回显到控制台。
        tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
        secrets: set[str] = set()
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != 'QQ_BOT_CONFIG_DEFAULT':
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                for keyword in getattr(value, 'keywords', []):
                    if keyword.arg == 'secret' and isinstance(keyword.value, ast.Constant) and keyword.value.value:
                        secrets.add(key.value)
        self.assertEqual(secrets, {'DailyWifeQqBotMediaAppSecret'})

    def test_tencent_cos_is_fully_removed(self) -> None:
        # 图床已统一走 QQ 开放平台的素材管理；COS 的实现、配置键与依赖都不得残留，
        # 否则老用户仍会在控制台看到一堆填了也没用的存储桶选项。
        combined = '\n'.join(
            path.read_text(encoding='utf-8-sig') for path in sorted((ROOT / 'TodayWaifu').glob('*.py'))
        ) + (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        lowered = combined.lower()
        for token in ('cosclient', 'qcloud_cos', 'cospathprefix', 'cossignexpired', 'cosbucket'):
            self.assertNotIn(token, lowered)
        requirements = (ROOT / 'pyproject.toml').read_text(encoding='utf-8-sig')
        self.assertNotIn('cos-python-sdk-v5', requirements)
        # 素材上传与 Markdown 构造一律经 SDK，不再手写协议
        self.assertIn('qqbot-md-sdk', requirements)

    def test_legacy_cos_keys_are_purged_from_existing_config_files(self) -> None:
        # 老用户的 qq_bot_config.json 里还留着六个废弃键（含明文密钥）；配置实例化之前
        # 必须先清掉，否则它们会继续出现在控制台上。
        source = (ROOT / 'daily_wife_config.py').read_text(encoding='utf-8-sig')
        manifest = source[source.index('_LEGACY_QQ_BOT_KEYS'):]
        manifest = manifest[:manifest.index(']')]
        for key in (
            '_DividerQqBotCos',
            'DailyWifeQqBotCosRegion',
            'DailyWifeQqBotCosBucket',
            'DailyWifeQqBotCosSecretId',
            'DailyWifeQqBotCosSecretKey',
            'DailyWifeQqBotCosPathPrefix',
            'DailyWifeQqBotCosSignExpired',
        ):
            self.assertIn(key, manifest)
        # 清理必须发生在实例化之前，否则旧值会被载入并回写
        self.assertLess(source.index('_LEGACY_QQ_BOT_KEYS'), source.index('DailyWifeQqBotConfig = StringConfig('))

    def test_send_paths_consult_the_official_mode(self) -> None:
        # 三条投递路径都必须尝试官方模式，遗漏任一条会让对应功能在官方机器人上收不到图。
        source = (ROOT / 'TodayWaifu' / 'senders.py').read_text(encoding='utf-8')
        for function_name in ('_deliver_role_image', '_deliver_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {function_name}(')
            next_function = source.find('\nasync def ', start + 1)
            block = source[start:next_function if next_function >= 0 else None]
            self.assertIn('_try_send_official_qq_image(', block)

    def test_config_reads_bind_to_the_third_page_instance(self) -> None:
        # 回归守卫：本页配置由独立 StringConfig 实例承载，若误用第 1 页的
        # DailyWifeConfig 取键，取不到只会打一条 warning 并回落到默认值，
        # 表现为「总开关明明打开却始终走 OB11」——一个不报错、只能靠日志发现的故障。
        tree = ast.parse((ROOT / 'TodayWaifu' / 'qq_official.py').read_text(encoding='utf-8-sig'))
        cfg_sources: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name != '_cfg':
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == 'get_config':
                    cfg_sources.append(ast.unparse(sub.value))
        self.assertEqual(cfg_sources, ['DailyWifeQqBotConfig'])

    def test_first_page_instance_is_not_imported(self) -> None:
        # 除第 3 页实例外不得导入 DailyWifeConfig：一旦导入，后续维护很容易顺手用它取键，
        # 重新引入上面那个静默故障。
        #
        # 只检查 import 语句而非全文匹配：模块文档与 ``_cfg`` 的说明里会以 DailyWifeConfig
        # 举例说明「用错实例」的后果，全文子串判断会把这段解释误判为违规。
        for name in ('qq_official.py', 'media_images.py'):
            tree = ast.parse((ROOT / 'TodayWaifu' / name).read_text(encoding='utf-8-sig'))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
            self.assertNotIn('DailyWifeConfig', imported, f'{name} 不应导入第 1 页实例')
        tree = ast.parse((ROOT / 'TodayWaifu' / 'qq_official.py').read_text(encoding='utf-8-sig'))
        imported = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        ]
        self.assertIn('DailyWifeQqBotConfig', imported)


if __name__ == '__main__':
    unittest.main()
