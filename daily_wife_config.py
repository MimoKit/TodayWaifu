from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import CONFIG_DEFAULT, APPEARANCE_CONFIG_DEFAULT

# 配置须落在 GsCore data 目录而非插件目录：插件目录在升级、重装时会被整体覆盖，
# 配置若随代码存放将随升级丢失，故以 data 目录为唯一持久化位置。
CONFIG_PATH = get_res_path('TodayWaifu') / 'config.json'

# 旧版本曾将 config.json 写在插件目录内，此处做一次性搬运以承接老用户的既有配置。
# 仅当旧文件存在且新位置尚无配置时复制：新位置已有文件说明用户在当前版本下改过配置，
# 覆盖会造成新配置丢失。迁移失败（如 data 目录不可写）不得阻断插件加载，故吞掉
# OSError，代价是旧配置不再迁移。
_LEGACY_CONFIG_PATH = Path(__file__).parent / 'config.json'
if _LEGACY_CONFIG_PATH.is_file() and not CONFIG_PATH.is_file():
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_bytes(_LEGACY_CONFIG_PATH.read_bytes())
    except OSError:
        pass

# 历史遗留的图库地址键：图库地址已收敛到统一的 DailyWifeApiUrl，这些键指向的接口
# 均已停用。保留它们只会让控制台暴露失效选项，故集中列出以便统一清除。
_LEGACY_KEYS_TO_REMOVE = [
    'DailyWifeGalleryApiUrl',
    'DailyWifeNormalGalleryApiUrl',
    'DailyWifeLoliApiUrl',
    'DailyShotaGalleryApiUrl',
    'DailyWifePgrGalleryApiUrl',
    'DailyWifeRandomGalleryApiUrl',
]

# 清理必须在配置实例化之前针对底层 JSON 文件执行：StringConfig 以文件内容初始化各配置
# 项，文件里若仍有这些键，旧值会被载入并在后续回写时继续保留，删除对控制面板与运行时
# 都会失效。异常时不中断加载，代价是本次清理未生效（下次启动会重试）。
if CONFIG_PATH.is_file():
    try:
        import json
        _raw_data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        _raw_changed = False
        for _k in _LEGACY_KEYS_TO_REMOVE:
            if _k in _raw_data:
                del _raw_data[_k]
                _raw_changed = True
        if _raw_changed:
            CONFIG_PATH.write_text(json.dumps(_raw_data, ensure_ascii=False, indent=4), encoding='utf-8')
    except Exception:
        pass

DailyWifeConfig = StringConfig(
    'TodayWaifu',
    CONFIG_PATH,
    CONFIG_DEFAULT,
)

_FORCED_URL_MIGRATION_MARKER = CONFIG_PATH.parent / '.remote_urls_v3_migrated'
_FORCED_REMOTE_URLS = {
    'DailyWifeApiUrl': 'https://twfapi.xlinxc.cn',
}
if not _FORCED_URL_MIGRATION_MARKER.is_file():
    # 旧键需在实例化之后再清一次：StringConfig 会把文件中的键载入 config 映射，并可能
    # 按默认值补全条目，只清理文件覆盖不到该路径。
    for _k in _LEGACY_KEYS_TO_REMOVE:
        if _k in DailyWifeConfig.config:
            del DailyWifeConfig.config[_k]
    # 仅在用户未填写地址时写入官方地址：用户自定义值必须保留，否则升级会静默改写其
    # 指向的图库服务。判空兼判空白，避免仅含空格的无效值阻挡迁移。
    for _key, _url in _FORCED_REMOTE_URLS.items():
        if _key in DailyWifeConfig.config:
            _config_item = DailyWifeConfig.config[_key]
            if not str(_config_item.data or '').strip():
                _config_item.data = _url
    DailyWifeConfig.write_config()
    try:
        # 标记文件记录本次强制迁移已执行，使迁移严格幂等：后续启动不再重复清除旧键，
        # 也不会再次改写用户此后填入的地址。
        _old_marker = CONFIG_PATH.parent / '.remote_urls_v2_migrated'
        if _old_marker.is_file():
            _old_marker.unlink()
        _FORCED_URL_MIGRATION_MARKER.touch()
    except OSError:
        pass

# 外观项（图片上传、帮助行数）单独建实例并写入独立文件：它们在 WebConsole 上作为
# 「今日老婆外观配置」独立页面展示，与 'TodayWaifu' 页面的文本配置分开维护，避免
# 图片组件与文本项挤在同一页面并共用同一份 json 文件。
DailyWifeShowConfig = StringConfig(
    '今日老婆外观配置',
    get_res_path('TodayWaifu') / 'show_config.json',
    APPEARANCE_CONFIG_DEFAULT,
)
# 以目录链接（Windows Junction 等）方式加载插件时，Path.resolve() 会跟踪到真实路径，
# 使 plugin_name 的自动推导失败；此处显式回填，确保 WebConsole 能把配置页归属到本插件。
DailyWifeConfig.plugin_name = 'TodayWaifu'
DailyWifeShowConfig.plugin_name = 'TodayWaifu'
