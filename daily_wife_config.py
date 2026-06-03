from __future__ import annotations

from pathlib import Path

from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import CONFIG_DEFAULT

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'

DailyWifeConfig = StringConfig(
    'gs_wuwa_daily_wife',
    CONFIG_PATH,
    CONFIG_DEFAULT,
)