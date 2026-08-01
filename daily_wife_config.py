from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import APPEARANCE_CONFIG_DEFAULT, CONFIG_DEFAULT

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

DailyWifeConfig = StringConfig(
    'TodayWaifu',
    CONFIG_PATH,
    CONFIG_DEFAULT,
)

_LEGACY_DEFAULT_URLS = {
    'DailyWifeGalleryApiUrl': (
        'https://img.xlinxc.cn/api/xwuid/roles',
        'https://img.mimokit.dpdns.org/api/xwuid/roles',
    ),
    'DailyWifeLoliApiUrl': (
        'https://loli.xlinxc.cn',
        'https://loli.mimokit.dpdns.org',
    ),
}
_config_changed = False
for _key, (_old_url, _new_url) in _LEGACY_DEFAULT_URLS.items():
    if DailyWifeConfig.config[_key].data == _old_url:
        DailyWifeConfig.config[_key].data = _new_url
        _config_changed = True
if _config_changed:
    DailyWifeConfig.write_config()

DailyWifeShowConfig = StringConfig(
    '今日老婆外观配置',
    get_res_path('TodayWaifu') / 'show_config.json',
    APPEARANCE_CONFIG_DEFAULT,
)
# Junction 加载时 Path.resolve() 跟踪到真实路径，导致 plugin_name 自动检测失败
# 手动补回正确值，确保 webconsole 能关联到本插件的配置
DailyWifeConfig.plugin_name = 'TodayWaifu'
DailyWifeShowConfig.plugin_name = 'TodayWaifu'
