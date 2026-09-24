from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import CONFIG_DEFAULT, APPEARANCE_CONFIG_DEFAULT

# 配置文件放在 GsCore data 目录下，避免插件升级/卸载时丢失
CONFIG_PATH = get_res_path('TodayWaifu') / 'config.json'

# 兼容旧版本：把插件目录下的 config.json 一次性迁移到 data 目录
_LEGACY_CONFIG_PATH = Path(__file__).parent / 'config.json'
if _LEGACY_CONFIG_PATH.is_file() and not CONFIG_PATH.is_file():
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_bytes(_LEGACY_CONFIG_PATH.read_bytes())
    except OSError:
        pass

_LEGACY_KEYS_TO_REMOVE = [
    'DailyWifeGalleryApiUrl',
    'DailyWifeNormalGalleryApiUrl',
    'DailyWifeLoliApiUrl',
    'DailyShotaGalleryApiUrl',
    'DailyWifePgrGalleryApiUrl',
    'DailyWifeRandomGalleryApiUrl',
]

# 在加载配置前清理底层 json 文件中的旧配置项，防止 GsCore 保持旧值不覆盖
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
    # 彻底移除旧配置残留
    for _k in _LEGACY_KEYS_TO_REMOVE:
        if _k in DailyWifeConfig.config:
            del DailyWifeConfig.config[_k]
    for _key, _url in _FORCED_REMOTE_URLS.items():
        if _key in DailyWifeConfig.config:
            DailyWifeConfig.config[_key].data = _url
    DailyWifeConfig.write_config()
    try:
        # 清除旧版迁移标记
        _old_marker = CONFIG_PATH.parent / '.remote_urls_v2_migrated'
        if _old_marker.is_file():
            _old_marker.unlink()
        _FORCED_URL_MIGRATION_MARKER.touch()
    except OSError:
        pass

DailyWifeShowConfig = StringConfig(
    '今日老婆外观配置',
    get_res_path('TodayWaifu') / 'show_config.json',
    APPEARANCE_CONFIG_DEFAULT,
)
# Junction 加载时 Path.resolve() 跟踪到真实路径，导致 plugin_name 自动检测失败
# 手动补回正确值，确保 webconsole 能关联到本插件的配置
DailyWifeConfig.plugin_name = 'TodayWaifu'
DailyWifeShowConfig.plugin_name = 'TodayWaifu'
