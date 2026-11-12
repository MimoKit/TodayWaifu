# 正太图库的响应解析与源码接线契约：脏数据逐条丢弃、接口地址宽容归一、抽取链路完整接通。
#
# 正太与萝莉的关键差异是没有本地图片来源，图库不可用时不存在回退路径，失败原因必须原样
# 上报用户（shota.py 的模块约定）。因此解析器的取舍是「单项格式错误跳过以保住正常部分，
# 但整份响应都无可用图片时抛错」——静默返回空集只会让用户看到「没有可用图片」，
# 把接口故障伪装成正常空结果。本文件同时锁定抢/送/离婚三条链路确实接入了正太分支。
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTA_PATH = ROOT / 'TodayWaifu' / 'shota.py'


def _extract_function(name: str, globals_dict: dict[str, Any]) -> Any:
    # 只抽取目标函数并注入替身全局名，使被测逻辑不依赖 gsuid_core 与真实配置系统；
    # 注入的全局名是函数体的隐式输入，缺失会直接抛 NameError。
    tree = ast.parse(SHOTA_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SHOTA_PATH), 'exec'), globals_dict)
    return globals_dict[name]


class ShotaGalleryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        # 逐条用例重新抽取：函数体依赖注入的全局名，共用命名空间会让前一条用例的
        # 注入残留并掩盖注入失效。
        self.parse_urls = _extract_function(
            '_parse_shota_image_urls',
            {'Any': Any},
        )
        self.normalize_url = _extract_function(
            '_normalize_shota_api_url',
            {},
        )

    def test_parses_shota_gallery_payload(self) -> None:
        # 重复 URL 必须并入同一份去重结果而非各占一项：重复项会让该图片在随机选图时
        # 获得更高权重，同一张图反复出现。
        payload = {
            'roles': [
                {
                    'role_ids': ['shota'],
                    'images': [
                        {'url': 'https://zt.mimokit.dpdns.org/shota/1.jpg'},
                        {'url': 'https://zt.mimokit.dpdns.org/shota/2.jpg'},
                        {'url': 'https://zt.mimokit.dpdns.org/shota/1.jpg'},
                    ],
                }
            ]
        }

        self.assertEqual(
            self.parse_urls(payload),
            (
                'https://zt.mimokit.dpdns.org/shota/1.jpg',
                'https://zt.mimokit.dpdns.org/shota/2.jpg',
            ),
        )

    def test_normalize_api_url(self) -> None:
        # 裸主机名补全为 HTTPS：用户常只填域名，直接交给 URL 解析器会得到畸形请求；
        # 空串保持空串而不补默认主机，由调用方转成「未配置接口地址」提示，避免静默
        # 请求到非用户预期的服务。
        self.assertEqual(
            self.normalize_url('zt.mimokit.dpdns.org'),
            'https://zt.mimokit.dpdns.org',
        )
        self.assertEqual(
            self.normalize_url('http://zt.mimokit.dpdns.org'),
            'http://zt.mimokit.dpdns.org',
        )
        self.assertEqual(
            self.normalize_url('https://zt.mimokit.dpdns.org'),
            'https://zt.mimokit.dpdns.org',
        )
        self.assertEqual(self.normalize_url(''), '')

    def test_rejects_empty_or_malformed_payloads(self) -> None:
        # 五类输入分别对应结构缺失、空列表、无 images 字段、空 images、非绝对地址：
        # 全部必须抛错。正太无本地回退，返回空集等于把接口异常伪装成「今日没有正太」。
        invalid_payloads = (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['shota']}]},
            {'roles': [{'role_ids': ['shota'], 'images': []}]},
            {'roles': [{'role_ids': ['shota'], 'images': [{'url': 'invalid-url'}]}]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.parse_urls(payload)

    def test_shota_source_code_contract(self) -> None:
        # 以源码文本为断言对象：以下标识是各链路之间的接线契约（标签名、记录类型、
        # 随机盐、触发器与服务名），漏接任一处都能通过导入与类型检查，
        # 却会让正太在对应流程中静默缺失——例如随机盐与他类共用会让当天记录互相覆盖。
        shota_source = SHOTA_PATH.read_text(encoding='utf-8-sig')
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8-sig')
        gift_source = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8-sig')
        divorce_source = (ROOT / 'TodayWaifu' / 'divorce.py').read_text(encoding='utf-8-sig')

        self.assertIn("role_ids=('shota',)", shota_source)
        self.assertIn("record_type='shota'", shota_source)
        self.assertIn("_daily_rng(ev, user_key, 'shota').choice", shota_source)
        self.assertIn("@shota_sv.on_fullmatch", shota_source)
        self.assertIn('今日正太', shota_source)
        self.assertIn('你今天的正太来啦！', shota_source)

        # Rob / Gift / Divorce support
        # 断言的是「正太被接进这条链路」，不是某个中间函数名——
        # 曾经用 _send_rob_shota 这类一层转发做接线，去掉转发层后要跟着改断言形式。
        self.assertIn("_send_rob_daily(bot, ev, 'shota')", rob_source)
        self.assertIn("_send_gift_daily(bot, ev, 'shota')", gift_source)
        self.assertIn("_accept_gift_daily(bot, ev, 'shota')", gift_source)
        self.assertIn("_reject_gift_daily(bot, ev, 'shota')", gift_source)
        self.assertIn('divorce_shota', divorce_source)


if __name__ == '__main__':
    unittest.main()
