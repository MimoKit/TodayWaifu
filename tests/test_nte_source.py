import ast
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _config_entries() -> list[tuple[str, str, str, ast.Call]]:
    source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    entries: list[tuple[str, str, str, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Call)
            ):
                continue
            title = ''
            description = ''
            if value.args and isinstance(value.args[0], ast.Constant):
                title = str(value.args[0].value)
            if len(value.args) > 1 and isinstance(value.args[1], ast.Constant):
                description = str(value.args[1].value)
            entries.append((key.value, title, description, value))
    return entries


def _bool_default(call: ast.Call) -> bool | None:
    if len(call.args) < 3:
        return None
    value = call.args[2]
    if isinstance(value, ast.Constant) and isinstance(value.value, bool):
        return value.value
    return None


def _extract_function(name: str, globals_dict: dict[str, Any]):
    source_path = ROOT / 'twf' / 'shared.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(source_path), 'exec'), globals_dict)
    return globals_dict[name]


@dataclass(frozen=True)
class _RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


class _Logger:
    def debug(self, _message: str) -> None:
        pass


class NteConfigAndCommandTests(unittest.TestCase):
    def test_daily_nte_wife_is_disabled_by_default(self) -> None:
        matches = [
            entry
            for entry in _config_entries()
            if '异环' in f'{entry[0]} {entry[1]} {entry[2]}'
            and '老婆' in f'{entry[0]} {entry[1]} {entry[2]}'
            and '混合' not in f'{entry[0]} {entry[1]} {entry[2]}'
            and _bool_default(entry[3]) is not None
        ]
        self.assertEqual(len(matches), 1, '应有且仅有一个“今日异环老婆”布尔开关')
        self.assertFalse(_bool_default(matches[0][3]))

    def test_daily_nte_wife_command_is_registered(self) -> None:
        daily_source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8')
        self.assertIn('今日异环老婆', daily_source)

        tree = ast.parse(daily_source)
        registered_words: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {'on_command', 'on_prefix', 'on_fullmatch'}:
                continue
            for arg in node.args[:1]:
                for value in ast.walk(arg):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        registered_words.add(value.value)
        self.assertIn('今日异环老婆', registered_words)

    def test_nte_candidate_loader_is_wired_to_local_and_default_piles(self) -> None:
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        tree = ast.parse(shared_source)
        nte_functions = [
            ast.get_source_segment(shared_source, node) or ''
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (
                node.name.lower().startswith('nte')
                or node.name.lower().startswith('_nte')
                or '_nte_' in node.name.lower()
            )
        ]
        nte_source = '\n'.join(nte_functions).lower()
        self.assertTrue(nte_functions, 'shared.py 应提供 NTE 候选加载函数')
        self.assertIn('_collect_role_candidates', nte_source)
        self.assertIn('custom', nte_source, 'NTE 加载器应传入本地自定义立绘目录')
        self.assertIn('default', nte_source, 'NTE 加载器应传入默认立绘目录作为兜底')


class NteCandidateBehaviorTests(unittest.TestCase):
    def _collector(self):
        return _extract_function(
            '_collect_role_candidates',
            {
                'Any': Any,
                'Path': Path,
                'RoleCandidate': _RoleCandidate,
                'IMAGE_EXTENSIONS': ('.png', '.jpg', '.jpeg', '.webp'),
                '_is_excluded_role': lambda _name: False,
                '_role_images': lambda role_dir: tuple(
                    str(path)
                    for path in sorted(role_dir.rglob('*'))
                    if path.is_file()
                    and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
                ),
                'logger': _Logger(),
                'LOG_PREFIX': '[test]',
            },
        )

    def test_nte_local_custom_image_has_priority(self) -> None:
        collect = self._collector()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_root = root / 'custom'
            default_root = root / 'default'
            (custom_root / '1003').mkdir(parents=True)
            default_root.mkdir()
            custom_image = custom_root / '1003' / 'custom.png'
            default_image = default_root / 'role_pile_1003.png'
            custom_image.write_bytes(b'custom')
            default_image.write_bytes(b'default')

            candidates = collect({'1003': '早雾'}, custom_root, default_root)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].images, (str(custom_image),))

    def test_nte_default_image_is_used_when_custom_is_missing(self) -> None:
        collect = self._collector()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_root = root / 'custom'
            default_root = root / 'default'
            custom_root.mkdir()
            default_root.mkdir()
            default_image = default_root / 'role_pile_1003.png'
            default_image.write_bytes(b'default')

            candidates = collect({'1003': '早雾'}, custom_root, default_root)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].images, (str(default_image),))

    def test_mixed_mode_defaults_off_and_includes_all_game_candidate_types(self) -> None:
        entries = _config_entries()
        mixed = [
            entry
            for entry in entries
            if '混合' in f'{entry[0]} {entry[1]} {entry[2]}'
            and '抽取' in f'{entry[0]} {entry[1]} {entry[2]}'
            and _bool_default(entry[3]) is not None
        ]
        self.assertEqual(len(mixed), 1, '应有且仅有一个多游戏混合模式布尔开关')
        self.assertFalse(_bool_default(mixed[0][3]))

        config_key = mixed[0][0]
        runtime_source = '\n'.join(
            (ROOT / 'twf' / name).read_text(encoding='utf-8')
            for name in ('shared.py', 'daily.py')
        )
        self.assertIn(config_key, runtime_source, '混合模式配置必须接入候选加载流程')

        merge = _extract_function(
            '_merge_role_candidates',
            {
                'Any': Any,
                'RoleCandidate': _RoleCandidate,
                '_normalize_role_name': lambda name: name.strip().casefold(),
            },
        )
        wuwa = (_RoleCandidate('今汐', ('1304',), ('wuwa.png',)),)
        nte = (_RoleCandidate('早雾', ('1003',), ('nte.png',)),)
        pgr = (_RoleCandidate('露西亚', ('露西亚',), ('pgr.png',)),)

        result = merge(merge(wuwa, nte), pgr)

        self.assertEqual({candidate.name for candidate in result}, {'今汐', '早雾', '露西亚'})

        self.assertIn('_load_pgr_local_candidates()', runtime_source)


if __name__ == '__main__':
    unittest.main()
