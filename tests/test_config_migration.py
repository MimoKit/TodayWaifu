# 「一次性覆盖远程图库地址」迁移的回归测试。
#
# 该迁移在 daily_wife_config.py 中于导入期执行：当标记文件不存在时，清掉已停用的旧图库
# 地址键，并在地址为空时写入官方地址，最后落标记。它由 2e95ac9「一次性覆盖远程图库地址」
# 引入，背景是 4a5513e「统一图库接口至 twfapi.xlinxc.cn 并合并配置项」——旧键指向的接口
# 全部下线，升级用户若不迁移就会持续请求失效域名。
#
# 迁移代码在模块顶层，无法通过导入被测模块来单独触发（导入需要 gsuid_core），故本文件
# 用 AST 截取该代码块并注入桩命名空间后 exec。这里有一条比迁移本身更关键的契约：
# 只有首次启动可以改写用户配置。用户自定义地址被覆盖过一次，就不会再收到任何提示。
import ast
import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _migration_module() -> ast.Module:
    # 按 AST 结构而非行号定位代码块：块上没有函数封装，行号会在上游编辑后静默漂移，
    # 悄悄把别的代码当成迁移逻辑来执行
    tree = ast.parse((ROOT / 'daily_wife_config.py').read_text(encoding='utf-8'))
    # 起点取首个顶层 `if not ... .is_file()`：即标记文件存在性判断，块的入口条件。
    # 更靠前的配置复制分支测试的是 BoolOp，不会被此处误命中
    start = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.operand, ast.Call)
        and isinstance(node.test.operand.func, ast.Attribute)
        and node.test.operand.func.attr == 'is_file'
    )
    # 终点取 `DailyWifeShowConfig = StringConfig(...)`：该赋值需要真实的 gsuid_core，
    # 会破坏本用例「免依赖执行」的前提，故在其之前截断
    end = next(
        index
        for index, node in enumerate(tree.body[start:], start)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'DailyWifeShowConfig' for target in node.targets)
    )
    return ast.Module(body=tree.body[start:end], type_ignores=[])


class ConfigMigrationTests(unittest.TestCase):
    def test_forced_remote_urls_are_current(self) -> None:
        # 硬编码比对而非与 config_default 的默认值互证：这个常量是升级路径上唯一被
        # 强制写入用户配置的值，一旦落后于线上域名，所有迁移过的用户都会指向失效接口，
        # 而配置界面里显示的仍是新域名，故障因此难以从表象定位
        tree = ast.parse((ROOT / 'daily_wife_config.py').read_text(encoding='utf-8'))
        forced_urls = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '_FORCED_REMOTE_URLS'
                for target in node.targets
            ):
                forced_urls = ast.literal_eval(node.value)
                break

        self.assertEqual(
            forced_urls,
            {
                'DailyWifeApiUrl': 'https://twf.xlinxc.cn/v1',
            },
        )

    def test_first_start_preserves_custom_value_and_fills_empty_once(self) -> None:
        migration = _migration_module()
        with TemporaryDirectory() as directory:
            # 标记固定在临时目录：迁移以「标记是否存在」判定是否执行，落到真实数据目录
            # 会既污染开发机、又让重复运行之间的判定互相干扰
            marker = Path(directory) / '.remote_urls_v4_migrated'
            # 桩配置对象：迁移只会读写 config 映射与调用 write_config，用计数替身即可
            # 观测「是否发生回写」，无须构造 StringConfig
            config = SimpleNamespace(
                config={
                    'DailyWifeApiUrl': SimpleNamespace(data='https://custom.example.test/gallery'),
                },
                write_count=0,
            )

            def write_config() -> None:
                config.write_count += 1

            config.write_config = write_config
            # 取代模块级常量，使本用例不依赖 daily_wife_config.py 的实际取值（该取值由
            # test_forced_remote_urls_are_current 单独守护），也让写入目标落在临时目录
            namespace = {
                'CONFIG_PATH': marker.parent / 'config.json',
                'DailyWifeConfig': config,
                '_FORCED_URL_MIGRATION_MARKER': marker,
                '_FORCED_REMOTE_URLS': {
                    'DailyWifeApiUrl': 'https://twf.xlinxc.cn/v1',
                },
                '_LEGACY_OFFICIAL_HOSTS': ('twfapi.xlinxc.cn', 'twf.xlinxc.cn'),
                '_LEGACY_KEYS_TO_REMOVE': [
                    'DailyWifeGalleryApiUrl',
                    'DailyWifeNormalGalleryApiUrl',
                    'DailyWifeLoliApiUrl',
                    'DailyShotaGalleryApiUrl',
                    'DailyWifePgrGalleryApiUrl',
                    'DailyWifeRandomGalleryApiUrl',
                ],
            }
            code = compile(migration, '<config-migration>', 'exec')
            exec(code, namespace)

            # 三项分别对应首次迁移的三条义务：用户填过的地址不得被改写、必须回写一次
            # 以持久化清理结果、必须落标记以为后续启动提供幂等依据
            self.assertEqual(
                config.config['DailyWifeApiUrl'].data,
                'https://custom.example.test/gallery',
            )
            self.assertEqual(config.write_count, 1)
            self.assertTrue(marker.is_file())

            config.config['DailyWifeApiUrl'].data = 'https://later.example.test/gallery'
            # 在同一命名空间内二次执行，模拟用户改完地址后重启。此处若再次写入，用户
            # 此后在控制台的每一次修改都会在重启时被官方地址覆盖，且不留任何痕迹
            exec(code, namespace)

            self.assertEqual(
                config.config['DailyWifeApiUrl'].data,
                'https://later.example.test/gallery',
            )
            self.assertEqual(config.write_count, 1)

    def test_legacy_official_host_is_force_rewritten(self) -> None:
        # 旧官方域名已停用，必须强制改写；否则升级用户会一直请求失效接口，
        # 而控制台显示的默认值又是新地址，故障很难从表象定位。
        migration = _migration_module()
        with TemporaryDirectory() as directory:
            marker = Path(directory) / '.remote_urls_v4_migrated'
            config = SimpleNamespace(
                config={
                    'DailyWifeApiUrl': SimpleNamespace(
                        data='https://twfapi.xlinxc.cn/api/xwuid/roles'
                    )
                },
                write_count=0,
            )
            config.write_config = lambda: setattr(config, 'write_count', config.write_count + 1)
            namespace = {
                'CONFIG_PATH': marker.parent / 'config.json',
                'DailyWifeConfig': config,
                '_FORCED_URL_MIGRATION_MARKER': marker,
                '_FORCED_REMOTE_URLS': {'DailyWifeApiUrl': 'https://twf.xlinxc.cn/v1'},
                '_LEGACY_OFFICIAL_HOSTS': ('twfapi.xlinxc.cn', 'twf.xlinxc.cn'),
                '_LEGACY_KEYS_TO_REMOVE': [],
            }
            exec(compile(migration, '<config-migration>', 'exec'), namespace)
            self.assertEqual(
                config.config['DailyWifeApiUrl'].data,
                'https://twf.xlinxc.cn/v1',
            )


if __name__ == '__main__':
    unittest.main()
