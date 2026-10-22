"""官方图库地址的唯一来源。

域名会变（今天 twf.xlinxc.cn，明天可能是别的），所以这里不把地址散落在
constants / config_default / 迁移代码里：

- 部署者可以用环境变量 ``TODAYWAIFU_GALLERY_BASE_URL`` 覆盖，不需要改代码；
- 代码里只保留这一个默认值，迁移逻辑也引用它，保证默认值与强制迁移永远一致。

值应指向图库 API 的版本前缀（默认 ``/v1``），插件会在其后拼接
``/api/xwuid/roles``、``/loli``、``/shota``、``/api/pgr/roles`` 等路径。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_GALLERY_BASE_URL = (
    os.environ.get('TODAYWAIFU_GALLERY_BASE_URL') or 'https://twf.xlinxc.cn/v1'
).strip().rstrip('/')

# 只用于「升级时识别需要强制改写的旧地址」，不会真的去访问。
# 域名变更时用环境变量 TODAYWAIFU_LEGACY_GALLERY_HOSTS 追加旧域名即可，不必改代码。
OFFICIAL_GALLERY_HOST = (urlparse(DEFAULT_GALLERY_BASE_URL).hostname or '').lower()
LEGACY_GALLERY_HOSTS = tuple(
    host.strip().lower()
    for host in (
        os.environ.get('TODAYWAIFU_LEGACY_GALLERY_HOSTS') or 'twfapi.xlinxc.cn'
    ).split(',')
    if host.strip()
)
