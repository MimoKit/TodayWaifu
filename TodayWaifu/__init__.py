# ruff: noqa: E402, F401, I001
"""TodayWaifu - 鸣潮今日老婆 GsCore 插件

内层包入口：声明插件并导入各功能模块以触发命令注册。
所有业务逻辑分布在本包的各子模块中。

下面的子模块导入是**有意的副作用导入**：导入即注册各 `@sv.on_xxx` 触发器。
它们必须排在 `Plugins(...)` 之后（须先声明插件再注册触发器），且**顺序即命令
加载顺序**（`shared` 须最先，`help` 须在 `daily` 之前），因此这里显式关闭
E402 / F401 / I001 —— 自动排序会打乱该顺序。
"""
from gsuid_core.sv import Plugins

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 导入顺序即为命令加载顺序
from . import shared       # 公共层：SV 实例、数据模型、工具函数
from . import help         # 帮助命令 + register_help（须在 daily 之前）
from . import normal_wife  # 普通老婆远程图库
from . import daily        # 每日抽取 / 列表 / 娶群友 / 老公
from . import pgr          # 战双本地图库抽取
from . import rob          # 抢老婆
from . import gift         # 送老婆
from . import divorce      # 离婚
from . import loli         # 萝莉 / 下载
from . import shota        # 今日正太（远程图库）
from . import custom_role  # 自定义老婆
from . import status       # core状态统计
