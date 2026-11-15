# 命令优先级与帮助路由的回归测试。所守护的故障均来自提交 8dd58e8「修复今日老婆帮助
# 被指定角色命令拦截」与 15f5a2a「修复帮助图 Image 导入」：命令优先级一旦倒挂，
# 「今日老婆 帮助」会被解析为角色名而落入指定老婆分支；帮助模块一旦缺少图形依赖，
# 帮助命令会以 NameError 失败。
#
# 本文件不执行被测代码，而是对源码做正则与子串匹配。该写法对格式变化较为敏感，
# 代价是能直接锁定「优先级数值必须低于指定老婆」与「帮助别名必须在角色解析之前
# 短路」这两条难以从运行时行为稳定观测的契约。断言中的命令别名与源码片段属于测试
# 契约，修改被测源码措辞时须同步维护。
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CommandPriorityTests(unittest.TestCase):
    def test_help_runs_before_specified_wife_prefix(self) -> None:
        source = (ROOT / 'TodayWaifu' / 'shared.py').read_text(encoding='utf-8-sig')
        help_priority = re.search(
            r"help_sv\s*=\s*SV\('今日老婆-帮助',\s*priority=(\d+)\)",
            source,
        )
        specify_priority = re.search(
            r"specify_wife_sv\s*=\s*SV\('今日老婆-指定老婆',\s*pm=1,\s*priority=(\d+)\)",
            source,
        )

        self.assertIsNotNone(help_priority)
        self.assertIsNotNone(specify_priority)
        # 先固定帮助为 0，防止以「只要比指定老婆小即可」为由整体上移优先级；
        # 再要求严格小于指定老婆，二者共同保证帮助在命令分发阶段优先命中
        self.assertEqual(int(help_priority.group(1)), 0)
        self.assertLess(
            int(help_priority.group(1)),
            int(specify_priority.group(1)),
        )

    def test_daily_wife_prefix_routes_help_alias(self) -> None:
        source = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8-sig')
        # 四个别名对应同一「今日老婆」命令入口，帮助分支必须在角色名解析之前短路，
        # 否则「帮助」会被当作待指定角色名并返回「未找到角色」
        self.assertIn("str(ev.command or '').strip() in {'今日老婆', '娶婆娘', 'jrlp', 'qlp'}", source)
        self.assertIn("specified_name == '帮助'", source)
        self.assertIn('from .help import daily_wife_help', source)
        self.assertIn('return await daily_wife_help(bot, ev)', source)


    def test_help_passes_command_icon_directory_to_renderer(self) -> None:
        source = (ROOT / 'TodayWaifu' / 'help.py').read_text(encoding='utf-8-sig')
        # 图标目录须经 extra 注入渲染器：渲染层不感知插件安装位置，路径只能由调用方提供
        self.assertIn("extra['icon_path'] = _ICON_PATH", source)
        self.assertIn("'icon_path'", source)

    def test_help_imports_pil_image(self) -> None:
        source = (ROOT / 'TodayWaifu' / 'help.py').read_text(encoding='utf-8-sig')
        # PIL 导入曾随「移除官方图库」被误删，导致帮助命令 NameError；
        # 该依赖不会被其它模块在 import 期间间接引入，必须由本模块显式声明
        self.assertIn('from PIL import Image', source)
        self.assertIn('Image.open', source)


if __name__ == '__main__':
    unittest.main()
