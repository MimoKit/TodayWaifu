"""内置台词库的分发契约：覆盖角色对照表里的全部角色、结构完整、长度受控。

背景：台词库原本只存在于 data 目录、插件仓库里没有，别人装完插件后台词功能会
静默为空（get_role_quote 返回空串，daily.py 直接跳过，不报错也不提示）。现在随仓库
分发一份完整的内置库，运行时只读这一份——曾额外播种到 data 并优先读那份，结果是
升级后的新库永远被旧副本挡住，故已移除该路径。
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
BUNDLED = ROOT / 'role_quotes.json'
ROLE_MAP = ROOT / 'role_id_map.json'


def _resolved_bundled_path() -> Path:
    """按插件代码的算法解析内置库路径，用于校验两者一致。

    resource_paths.py 位于 <插件根>/TodayWaifu/，故 BASE_DIR = Path(__file__).parent.parent
    解析出来就是插件根目录。
    """
    resource_paths = (PACKAGE / 'resource_paths.py').read_text(encoding='utf-8')
    marker = 'BUNDLED_ROLE_QUOTES_PATH = '
    line = next(item for item in resource_paths.splitlines() if item.startswith(marker))
    expr = line[len(marker) :].strip()
    return eval(expr, {'BASE_DIR': ROOT, 'ROLE_QUOTES_FILE_NAME': 'role_quotes.json'})


# role_quotes.py 会截断超长台词；卡片排版允许到 30 字
MAX_QUOTE_LENGTH = 30
# 抽取需要多样性，每个角色至少几条；库里绝大多数角色是 5 条以上
MIN_QUOTES_PER_ROLE = 3
# 对照表角色的覆盖率下限。允许新角色先进对照表、台词后补（运行时回退兜底台词），
# 但整体覆盖率塌下来就说明内置库出问题了。
MIN_COVERAGE_RATIO = 0.9


def _normalize(name: str) -> str:
    """与插件 _normalize_role_name 保持一致的点号归一。"""
    return name.replace('・', '·').replace('•', '·').strip()


def _mapped_names(*sections: str) -> set[str]:
    """取 role_id_map.json 指定分节里的角色名（wife/husband 是鸣潮，nte 是异环）。"""
    role_map = json.loads(ROLE_MAP.read_text(encoding='utf-8'))
    names: set[str] = set()
    for section in sections:
        names |= {_normalize(v) for v in (role_map.get(section) or {}).values()}
    return names


class BundledQuotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(BUNDLED.read_text(encoding='utf-8'))
        cls.quotes: dict[str, list[str]] = cls.payload['role_quotes']

    def test_bundle_ships_with_the_plugin(self) -> None:
        self.assertTrue(BUNDLED.is_file(), '内置台词库必须随插件一起分发')

    def test_bundle_covers_most_mapped_roles(self) -> None:
        """内置库要覆盖绝大多数对照表角色，但不强制要求逐个齐全。

        新角色先加进 role_id_map.json、台词后补是正常节奏：没收录的角色在运行时
        会回退 default_quotes（见 get_role_quote / daily.py 的 `if quote:`），
        不会静默没台词，所以不该因为少一个角色就把 CI 卡红。

        这条断言真正要守的是「台词库整体塌掉」那类回归（曾出现内置库缺失、
        装完插件后台词功能静默为空），因此用覆盖率下限而不是精确相等。
        """
        mapped = _mapped_names('wife', 'husband', 'nte')
        bundled = {_normalize(name) for name in self.quotes}
        missing = sorted(mapped - bundled)
        covered = len(mapped) - len(missing)
        ratio = covered / len(mapped) if mapped else 1.0
        self.assertGreaterEqual(
            ratio,
            MIN_COVERAGE_RATIO,
            f'内置库只覆盖了 {covered}/{len(mapped)} 个对照表角色，疑似台词库缺失；未收录: {missing}',
        )

    def test_pgr_roles_are_bundled_too(self) -> None:
        """战双角色不在 role_id_map 里，用已知角色名兜底校验它们确实进来了。"""
        bundled = set(self.quotes)
        for name in ('露西亚', '丽芙', '七实', '卡列尼娜', '比安卡', '薇拉'):
            self.assertIn(name, bundled, f'战双角色 {name} 应包含在内置库中')

    def test_every_role_has_enough_quotes(self) -> None:
        for name, quotes in self.quotes.items():
            self.assertGreaterEqual(len(quotes), MIN_QUOTES_PER_ROLE, name)

    def test_quotes_fit_the_card(self) -> None:
        for name, quotes in self.quotes.items():
            for quote in quotes:
                self.assertLessEqual(len(quote), MAX_QUOTE_LENGTH, f'{name}: {quote}')
                self.assertTrue(quote.strip(), f'{name} 存在空台词')

    def test_default_quotes_present(self) -> None:
        defaults = self.payload.get('default_quotes')
        self.assertIsInstance(defaults, list)
        self.assertTrue(defaults, '需要兜底台词，否则未收录角色仍会静默没有台词')


def _function_source(path: Path, name: str) -> str:
    """截取某个顶层函数的源码，避免把后续函数的实现算进来。"""
    source = path.read_text(encoding='utf-8')
    start = source.index(f'def {name}(')
    rest = source[start:]
    nxt = rest.find('\ndef ', 1)
    return rest if nxt == -1 else rest[:nxt]


class BundledPathTests(unittest.TestCase):
    def test_quotes_ignore_any_data_copy(self) -> None:
        """台词库路径只能落到内置文件：曾优先读 data 副本，升级后的新库永远被挡住。"""
        body = _function_source(PACKAGE / 'resource_paths.py', 'role_quotes_path')
        self.assertIn('BUNDLED_ROLE_QUOTES_PATH', body)
        self.assertNotIn('data_root()', body, 'role_quotes_path 不得再读 data 目录')

        source = (PACKAGE / 'resource_paths.py').read_text(encoding='utf-8')
        self.assertNotIn('user_role_quotes_path', source, '用户副本路径与播种函数应已移除')
        self.assertNotIn('ensure_role_quotes_seeded', source)

    def test_bundled_path_points_to_plugin_root(self) -> None:
        """内置库与 ICON.png / role_id_map.json 同级，放插件根即可，不要另建 data 目录。"""
        source = (PACKAGE / 'resource_paths.py').read_text(encoding='utf-8')
        self.assertIn('BUNDLED_ROLE_QUOTES_PATH = BASE_DIR / ROLE_QUOTES_FILE_NAME', source)

    def test_bundled_path_resolves_to_the_shipped_file(self) -> None:
        """守卫：解析出来的内置库路径必须真实存在（曾因多写一层 parent 而静默失效）。"""
        path = _resolved_bundled_path()
        self.assertTrue(path.is_file(), f'内置台词库路径不可达: {path}')
        self.assertEqual(path.resolve(), BUNDLED.resolve())


if __name__ == '__main__':
    unittest.main()
