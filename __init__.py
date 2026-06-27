"""TodayWaifu - 鸣潮今日老婆 GsCore 插件

入口文件：声明插件并导入各功能模块以触发命令注册。
所有业务逻辑分布在 twf/ 子包中。
"""
from gsuid_core.sv import Plugins

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 导入顺序即为命令加载顺序（shared 须最先，help 须在 daily 之前避免"今日老婆帮助"被 prefix 拦截）
from .twf import shared       # 公共层：SV 实例、数据模型、工具函数
from .twf import help         # 帮助命令 + register_help（须在 daily 之前）  # noqa: F811
from .twf import daily        # 每日抽取 / 列表 / 娶群友 / 老公
from .twf import rob          # 抢老婆
from .twf import gift         # 送老婆
from .twf import loli         # 萝莉 / 下载
from .twf import custom_role  # 自定义老婆
