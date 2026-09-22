"""TodayWaifu - 鸣潮今日老婆 GsCore 插件

内层包入口：声明插件并导入各功能模块以触发命令注册。
所有业务逻辑分布在本包的各子模块中。
"""
from gsuid_core.sv import Plugins

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 导入顺序即为命令加载顺序（shared 须最先，help 须在 daily 之前避免"今日老婆帮助"被 prefix 拦截）
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
