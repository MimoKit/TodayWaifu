"""TodayWaifu 的模块级常量与配置读取助手（最底层，不依赖其它新拆分模块）。

本模块只允许依赖更底层的模块，以保证任何子模块都可以安全导入它而不构成循环依赖。
所有阈值常量集中于此，便于对照调整并避免散落在业务代码中。
"""
from __future__ import annotations

import re
from pathlib import Path

from .payloads import ConfigValue
from .kind_metadata import DailyKindMetadata, daily_kind_metadata
from ..daily_wife_config import DailyWifeConfig

BASE_DIR = Path(__file__).parent.parent


# 内置角色对照表：单文件按模式分节，替代旧版 wife/husband/nte_role_id_map.txt。
# 采用单一 JSON 而非多个文本文件，是为了让分节的模式信息与角色表保持同源。
ROLE_MAP_JSON_PATH = BASE_DIR / 'role_id_map.json'


# 旧格式路径仅用于读取与迁移，不再作为写入目标；保留常量以便一次性转换后清理。
LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'


HELP_ICON_PATH = BASE_DIR / 'ICON.png'


# 图库基址与接口地址分离：接口路径可能随上游版本调整，基址则用于跨域资源引用。
DEFAULT_GALLERY_BASE_URL = 'https://twfapi.xlinxc.cn'
DEFAULT_GALLERY_API_URL = f'{DEFAULT_GALLERY_BASE_URL}/api/xwuid/roles'


# 异环详情图由第三方 CDN 托管，此处只存基址，具体路径由角色 ID 拼接。
NTE_DETAIL_CDN_BASE = 'https://webstatic.tajiduo.com/bbs/yh-game-records-web-source/character/detail'


PGR_WIFE_DIR_NAME = 'pgr_wife'


# 候选列表（群成员、角色名等）的缓存有效期。取值需短于"用户可感知的过期"，
# 又需覆盖同一轮命令中的多次读取，故取 5 分钟这一折中区间。
CACHE_TTL_SECONDS = 300


# 群成员头像变更频率极低，取 7 天以最大限度减少对外网头像接口的请求；
# 过期后由缓存维护循环按文件 mtime 清理。
MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60


# 维护循环的执行周期。清理属于后台低优先级任务，间隔过短会与业务请求争抢
# 阻塞线程池，过长则过期文件在磁盘上的驻留时间被放大。
CACHE_MAINTENANCE_INTERVAL_SECONDS = 60 * 60


# 单轮维护最多检查的文件数。清理需遍历目录并按 mtime 排序，不设上限时
# 目录规模较大的一次遍历会长时间占用阻塞线程，故按批次限量处理。
CACHE_MAINTENANCE_FILE_LIMIT = 1000


# 响应体大小上限：先于解析限制读取量，避免异常上游返回超大正文耗尽内存。
# 图库接口只返回 JSON 元数据，2 MB 已远超正常响应。
MAX_GALLERY_RESPONSE_BYTES = 2 * 1024 * 1024


# 图片体积上限高于图库接口，因为单张原图本身可达数 MB；
# 10 MB 是"可容忍的最大单图"与内存安全之间的取舍。
MAX_IMAGE_RESPONSE_BYTES = 10 * 1024 * 1024


# 单次命令等待图库图片的上限。命令协程会一直占着 Core 的命令并发额度
# （CommandSemaphore），等待过久会使 bot 的 _process 停止消费队列，进而
# 造成整个 Core 阻塞。因此此处以"放弃等待"而非"放弃下载"来限时：
# 超时仅解除当前协程的等待，底层下载继续执行并写盘，下次请求直接命中缓存。
IMAGE_ACQUIRE_TIMEOUT_SECONDS = 6.0


# 远程请求策略：重试次数必须为 1。此前的 retries=3 配合固定 5 秒间隔会把一次
# 失败放大为 4 倍请求量，且最坏情形下占用约 95 秒，是零点高峰的主要放大源；
# 改为单次重试后，失败请求量恒等于失败次数，重试风暴的乘数被消除。
HTTP_RETRIES = 1


# 超时必须小于 IMAGE_ACQUIRE_TIMEOUT_SECONDS，否则超时控制先于网络超时生效，
# 无法区分"上游慢"与"网络故障"，重试与熔断的判定也会失去意义。
GALLERY_HTTP_TIMEOUT_SECONDS = 8


IMAGE_HTTP_TIMEOUT_SECONDS = 8


# 指数退避 + 抖动：固定间隔会让同批失败请求在同一时刻重试，形成同步脉冲
# 而再次压垮上游；抖动则把重试时刻打散到各请求独立的区间内。
RETRY_BASE_DELAY_SECONDS = 1.0


# 退避上限：指数增长必须封顶，否则高次数重试的等待时间将远超单次命令的容忍度。
RETRY_MAX_DELAY_SECONDS = 4.0


# 抖动幅度（正负各半），相对最大退避的占比很小，仅用于打散而不过度拉长总耗时。
RETRY_JITTER_SECONDS = 0.5


# 连续失败达到阈值即熔断，冷却期内直接快速失败、不发起网络请求。
# 阈值取 5 以过滤偶发网络抖动，避免单次失败就切断上游；
# 同时该值需与 HTTP_RETRIES 配合：达到阈值所需的请求数为阈值 × (1 + 重试次数)。
CIRCUIT_FAILURE_THRESHOLD = 5


# 冷却期需长于上游的典型恢复时间，又需短于用户对"功能不可用"的容忍上限；
# 30 秒同时给上游留出恢复窗口，并让插件在此期间稳定降级到本地图库。
CIRCUIT_COOLDOWN_SECONDS = 30.0


# 零点前预热图库图片的时刻（本地时间）与时间上限。
# 日期翻转后 `_daily_rng` 的种子随之改变，每个用户都会抽到新的图片 URL，
# 磁盘缓存全部失效 —— 预热的唯一目的是让 00:00 的抽签直接命中缓存。
# 预热请求受图库的每 IP / 每 token 图片限流约束，不可推迟到 23:50 再突发下载；
# 提前开始并降低速率，可为 00:00 预留完整的缓存窗口。
PREFETCH_HOUR = 23


# 分钟取值需保证从 PREFETCH_HOUR 起的可用时长（至 24:00）不超过 PREFETCH_MAX_SECONDS，
# 否则预热会在零点后仍在运行，与本常量的语义相悖。
PREFETCH_MINUTE = 20


PREFETCH_MAX_SECONDS = 30 * 60


# 预热每次真实下载之间的间隔。当前图库默认图片限流为 60/min，
# 6 秒约等于 10/min，可将绝大多数额度留给用户请求；命中本地缓存时不等待。
PREFETCH_DOWNLOAD_INTERVAL_SECONDS = 6.0


# 启动后延迟补跑一次预热。重启可能发生在零点之后，此时缓存未必完整；
# 已缓存的图片会被跳过，因此补跑的成本通常很低，可以安全地无条件下发。
PREFETCH_STARTUP_DELAY_SECONDS = 60


# 状态页聚合的最小重算间隔。若每次写入都使聚合失效，控制台轮询将次次触发全表聚合，
# 造成读放大；此间隔把重算频率与写入频率解耦。
STATUS_MIN_RECOMPUTE_SECONDS = 30.0


# 每日记录保留天数上限（0 表示永久保留）。表行数随 群 × 用户 × 桶 × 天数 增长，
# 不清理将无限累积，并使按天查询的扫描量持续上升。
DAILY_RECORD_RETENTION_DAYS = 30


# 图库图片磁盘缓存的总容量上限（MB，0 表示不限）。
# 仅按天过期不足以约束容量：若图库 URL 携带签名或时间戳，旧文件永不命中且
# 不断新增，最终占满磁盘，故需在按天过期之外再加总容量上限这一层。
GALLERY_CACHE_MAX_MB = 512


# 列表条目超过该数量时改用合并转发发送，避免长消息被平台截断或刷屏。
LIST_FORWARD_THRESHOLD = 10


# 自定义角色的 ID 起始值，取远大于内置角色 ID 的区间，以保证两类 ID 不重叠；
# 一旦下调将与现有内置角色冲突，属于不可回退的取值。
CUSTOM_ROLE_ID_START = 900001


# 上传图片的大小上限，与 MAX_IMAGE_RESPONSE_BYTES 保持一致：
# 用户上传与插件下载走同一条存储链路，容量假设必须相同。
UPLOAD_IMAGE_MAX_BYTES = 10 * 1024 * 1024


# 删除确认的有效期。窗口内可撤销误操作，超时即失效以避免待删状态长期驻留内存。
CUSTOM_ROLE_DELETE_CONFIRM_SECONDS = 120


LOLI_IMAGE_DIR_NAME = 'loli_images'


LOLICONAPP_API_URL = 'https://api.lolicon.app/setu/v2'


# 标签表达式直接透传给上游，语义由上游解析：
# 竖线分隔多个候选标签，逗号后的 `-` 前缀表示排除。
LOLICONAPP_TAGS = '萝莉|ロリ|loli|rori,-hololive'


# 移动端 UA：上游对桌面 UA 返回的图片规格或限流策略不同，
# 固定 UA 可保证返回结果与缓存键稳定，避免同一 URL 随 UA 变化而重复下载。
LOLI_MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.6367.82 Mobile Safari/537.36'
)


LOG_PREFIX = '[鸣潮今日老婆]'


LOLI_DOWNLOAD_LOG_PREFIX = '[今日萝莉下载]'


# 旧版文本角色表的行格式：`ID:名称`，中英文冒号均需兼容，
# 名称前允许空白、非贪婪匹配以避免吞掉行尾内容。
ROLE_MAP_RE = re.compile(r'^\s*(\d+)\s*[:：]\s*(.+?)\s*$')


# 仅按扩展名白名单识别图片，避免把同目录下的非图片文件误当资源加载。
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}


# 以下排除名单用于过滤不符合"老婆/老公"语义的角色（同性别、剧情角色等）。
# 逐个硬编码而非按关键词推断，是因为名单需与上游角色表的人工判定保持一致，
# 无法由名称规则稳定推导。
EXCLUDED_ROLE_NAMES = {
    '仇远',
    '凌阳',
    '卡卡罗',
    '布兰特',
    '忌炎',
    '渊武',
    '相里要',
    '秋水',
    '莫特斐',
    '陆·赫斯',
}


# 主角系角色随版本新增变体，无法穷举全名，故按关键词匹配整体排除。
EXCLUDED_ROLE_KEYWORDS = ('漂泊者',)


NTE_EXCLUDED_ROLE_NAMES = {
    '翳',
    '埃德嘉',
    '白藏',
    '阿德勒',
    '卡厄斯',
}


NTE_EXCLUDED_ROLE_KEYWORDS = (
    '异能者·零',
    '异能者零',
    '男主',
    '女主',
)


def _cfg(key: str) -> ConfigValue:
    return DailyWifeConfig.get_config(key).data


def _cfg_bool(key: str, default: bool = False) -> bool:
    """按宽松语义解析布尔配置项，无法识别时回落到默认值。

    配置项来自 WebConsole 的文本框，实际取值可能是布尔、整数或字符串，
    故对常见真/假写法（含中文"开启/关闭"）逐一归一；未命中任何已知写法时
    不以异常中断调用方，而是返回默认值。
    """
    value = _cfg(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '开启'}:
            return True
        if text in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '关闭'}:
            return False
    return default


def _cfg_probability(key: str, default: float = 0.0) -> float:
    """读取概率配置并夹取到 [0, 1]。

    配置为非法数值时回落到默认值而非抛出异常；超出范围的值一律截断，
    因为概率语义下越界值与边界值等价，且放行越界值会使后续的随机判定失真。
    """
    try:
        value = float(_cfg(key))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, value))


# 图片来源开关按功能拆分，未列出的功能继续跟随每日老婆的总开关。
# 之所以按功能而非按角色模式拆分：不同功能的图片供给能力不同，
# 而它们可能共用同一 role_mode（见 `_image_source`）。
_IMAGE_SOURCE_CONFIG_KEYS: dict[str, str] = {
    'wife': 'DailyWifeImageSource',
    'husband': 'DailyWifeImageSource',
    'nte': 'DailyWifeNteImageSource',
    'pgr': 'DailyWifePgrImageSource',
    'loli': 'DailyLoliImageSource',
}

# 默认值与各功能改造前的实际行为一致，确保升级后不改变既有表现：
# 老功能维持纯本地，新接入的远程图库功能默认为 gallery。
_IMAGE_SOURCE_DEFAULTS: dict[str, str] = {
    'wife': 'local',
    'husband': 'local',
    'nte': 'gallery',
    'pgr': 'gallery',
    'loli': 'gallery',
}


def _image_source(kind: str = 'wife') -> str:
    """按功能返回图片来源：local 只用本地图片，gallery 允许使用远程图片。

    必须按功能名而非 role_mode 查询：萝莉的 role_mode 为 wife，若按 role_mode
    取配置，萝莉会错误地读到每日老婆的开关，导致两项功能无法独立控制。
    未知功能名回落到 wife，以保证新增调用点未登记时行为可预测。
    """
    if kind not in _IMAGE_SOURCE_CONFIG_KEYS:
        kind = 'wife'
    value = str(_cfg(_IMAGE_SOURCE_CONFIG_KEYS[kind]) or _IMAGE_SOURCE_DEFAULTS[kind])
    return 'gallery' if value.strip().lower() == 'gallery' else 'local'


def _no_r18_enabled() -> bool:
    """是否要求图库排除 R18 图片。

    仅影响服务端提供 nor18 变体的三个图库（鸣潮 / 萝莉 / 正太）：战双与测试图库
    本身不含 R18 内容，服务端也没有对应端点，套用会直接 404。
    默认关闭，使升级前后行为一致。
    """
    return _cfg_bool('DailyWifeApiNoR18', False)


def _apply_no_r18(url: str) -> str:
    """按开关把接口地址改写到服务端的 nor18 端点。

    地址以调用方给定的为准，只在其后追加路径段，不做整体替换——自定义图库的
    部署地址必须原样保留。已带 nor18 后缀时原样返回，避免重复拼出 /nor18/nor18。
    """
    if not url or not _no_r18_enabled():
        return url
    clean = url.rstrip('/')
    if clean.endswith('/nor18'):
        return clean
    return f'{clean}/nor18'


def _daily_item_title(kind: str) -> str:
    """返回该功能在对外文案中使用的条目标题。"""
    return _daily_kind_metadata(kind).title


def _daily_kind_metadata(kind: str) -> DailyKindMetadata:
    """集中获取功能元数据，使 title / bucket 等派生字段共用同一份查表逻辑。"""
    return daily_kind_metadata(kind)


def _daily_bucket_name(kind: str) -> str:
    """返回每日记录的桶名；桶名决定存取隔离粒度，不可与功能名混用。"""
    return _daily_kind_metadata(kind).bucket


DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')


# 参与每日记录清理与状态聚合的完整功能集合。此处为显式枚举而非动态推导，
# 因为新功能若未登记便不会进入清理范围，会造成记录无界增长。
ALL_DAILY_RECORD_KINDS = ('wife', 'nte', 'pgr', 'husband', 'loli', 'shota', 'normal')
