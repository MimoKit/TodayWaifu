"""守卫：任何 _cfg('字面量') 引用的键都必须真实存在于 CONFIG_DEFAULT。

背景：daily_wife_config.py 会把一批旧键（*GalleryApiUrl / *LoliApiUrl 等）从
config.json 里删掉，但代码里还留着 6 处 `or _cfg('旧键')`。它们永远取不到值——
get_config 会走兜底分支返回假配置，而且每次调用都往日志里打一条 warning。
这里把「引用必须已定义」固化成契约，避免再退化。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
READERS = ('_cfg', '_cfg_bool', '_cfg_int', '_cfg_probability')


def _defined_keys() -> set[str]:
    tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == 'CONFIG_DEFAULT' and isinstance(node.value, ast.Dict):
                return {
                    key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    raise AssertionError('config_default.py 里没有 CONFIG_DEFAULT')


class ConfigReferenceTests(unittest.TestCase):
    def test_every_cfg_literal_is_a_defined_key(self) -> None:
        defined = _defined_keys()
        offenders: list[str] = []
        for path in sorted(PACKAGE.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in READERS or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value not in defined:
                        offenders.append(f'{path.name}:{node.lineno} -> {first.value}')
        self.assertEqual(offenders, [], '存在引用了未定义配置键的死引用')

    def test_legacy_keys_are_not_referenced_anywhere(self) -> None:
        """被 daily_wife_config 删除的旧键，代码里不应再出现。"""
        legacy = (
            'DailyWifeGalleryApiUrl',
            'DailyWifeNormalGalleryApiUrl',
            'DailyWifeLoliApiUrl',
            'DailyShotaGalleryApiUrl',
            'DailyWifePgrGalleryApiUrl',
            'DailyWifeRandomGalleryApiUrl',
        )
        source = '\n'.join(path.read_text(encoding='utf-8') for path in sorted(PACKAGE.glob('*.py')))
        for key in legacy:
            self.assertNotIn(key, source, f'{key} 已被删除，不应再被引用')


if __name__ == '__main__':
    unittest.main()
