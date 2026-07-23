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


if __name__ == '__main__':
    unittest.main()
