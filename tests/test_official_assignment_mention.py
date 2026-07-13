import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OfficialAssignmentMentionTests(unittest.TestCase):
    def test_assignment_passes_official_qq_platform_flag(self) -> None:
        source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8')
        self.assertIn(
            'official_qq_mention=_is_official_markdown_event(ev)',
            source,
        )

    def test_official_and_personal_mentions_use_separate_formats(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('official_qq_mention: bool = False', source)
        self.assertIn("mention_text = f'<@{user_id}>'", source)
        self.assertIn("mention_text = f'{mention_text}\\n{reply_text}'", source)
        self.assertIn('messages.extend(MessageSegment.markdown(mention_text))', source)
        self.assertIn('messages.append(MessageSegment.at(user_id))', source)

    def test_private_assignment_keeps_group_guard(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        self.assertIn(
            "if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):",
            source,
        )


if __name__ == '__main__':
    unittest.main()
