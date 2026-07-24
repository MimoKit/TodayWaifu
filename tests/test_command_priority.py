import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommandPriorityTests(unittest.TestCase):
    def test_help_runs_before_specified_wife_prefix(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        help_priority = re.search(
            r"help_sv\s*=\s*SV\('今日老婆-帮助',\s*priority=(\d+)\)",
            source,
        )
        specify_priority = re.search(
            r"specify_wife_sv\s*=\s*SV\('今日老婆-指定老婆',\s*priority=(\d+)\)",
            source,
        )

        self.assertIsNotNone(help_priority)
        self.assertIsNotNone(specify_priority)
        self.assertEqual(int(help_priority.group(1)), 0)
        self.assertLess(
            int(help_priority.group(1)),
            int(specify_priority.group(1)),
        )

    def test_daily_wife_prefix_routes_help_alias(self) -> None:
        source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')
        self.assertIn("str(ev.command or '').strip() == '今日老婆'", source)
        self.assertIn("specified_name == '帮助'", source)
        self.assertIn('from .help import daily_wife_help', source)
        self.assertIn('return await daily_wife_help(bot, ev)', source)


    def test_help_imports_pil_image(self) -> None:
        source = (ROOT / 'twf' / 'help.py').read_text(encoding='utf-8-sig')
        self.assertIn('from PIL import Image', source)
        self.assertIn('Image.open', source)


if __name__ == '__main__':
    unittest.main()
