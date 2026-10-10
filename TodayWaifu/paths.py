"""TodayWaifu 的路径解析，以及日期/用户键与随机种子助手。

资源解析遵循统一的优先级：用户显式配置的路径 → 框架资源目录 → 插件内置资源。
候选链一旦命中即返回，全部落空则返回 None 或可写目录，由调用方决定降级行为；
此处不复制、不创建内置资源，以免掩盖配置错误或污染用户数据目录。
"""
from __future__ import annotations

import random
from pathlib import Path
from datetime import date
from importlib.util import find_spec

import gsuid_core
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.data_store import get_res_path

from .constants import (
    BASE_DIR,
    LOG_PREFIX,
    PGR_WIFE_DIR_NAME,
    ROLE_MAP_JSON_PATH,
    LOLI_IMAGE_DIR_NAME,
    LEGACY_ROLE_MAP_PATH,
    _cfg,
    _daily_kind_metadata,
)
from .role_map_store import migrate_legacy_text_map
from .daily_repository import ContextKey


def _configured_path(key: str) -> Path | None:
    # 未配置返回 None 而非空 Path，让调用方能把「未设置」与「设置成了当前目录」区分开；
    # 去掉成对引号是因为控制台常把用户粘贴的路径连同引号一并保存。
    raw = str(_cfg(key) or '').strip().strip('"')
    if not raw:
        return None
    path = Path(raw).expanduser()
    logger.debug(f'{LOG_PREFIX} 读取配置路径 {key}: {path}')
    return path


def _role_mode(mode: str) -> str:
    return _daily_kind_metadata(mode).role_mode


def _role_map_title(mode: str) -> str:
    return _daily_kind_metadata(mode).title


def _resolve_role_map_path(mode: str = 'wife') -> Path | None:
    """按优先级定位角色对照表，无可用文件时返回 None。

    解析顺序：分类专属配置 → 老婆分类的旧配置键 → 内置合并 JSON →
    内置旧版 TXT → 插件上级目录的 TXT → 当前工作目录的 TXT。
    首个 `is_file()` 命中即返回；旧配置键只对老婆分类生效，因为它是该分类
    历史的键名，其余分类若沿用会取到语义不符的文件。全部未命中时记 warning
    并返回 None，由调用方转换成面向用户的提示，而不是让上层拿到空路径。
    """
    role_mode = _role_mode(mode)
    if role_mode == 'nte':
        configured = _configured_path('DailyWifeNteRoleMapPath')
    elif role_mode == 'husband':
        configured = _configured_path('DailyWifeHusbandRoleMapPath')
    else:
        configured = _configured_path('DailyWifeWifeRoleMapPath')
    legacy_configured = _configured_path('DailyWifeRoleMapPath') if role_mode == 'wife' else None

    # 内置 role_id_map.json 是唯一内置来源；后面几项只服务于老安装残留与用户自备文件。
    # 候选含 None（未配置项），故下方循环必须先判真再判 is_file，否则会对 None 调用方法。
    candidates = [
        configured,
        legacy_configured,
        ROLE_MAP_JSON_PATH,
        LEGACY_ROLE_MAP_PATH,
        BASE_DIR.parent / ('鸣潮老公面板id对照角色.txt' if role_mode == 'husband' else '鸣潮老婆面板id对照角色.txt'),
        Path.cwd() / ('鸣潮老公面板id对照角色.txt' if role_mode == 'husband' else '鸣潮老婆面板id对照角色.txt'),
    ]
    for path in candidates:
        if path and path.is_file():
            logger.debug(f'{LOG_PREFIX} 成功定位{_role_map_title(role_mode)}角色对照表文件: {path}')
            return path
    logger.warning(f'{LOG_PREFIX} 未能找到{_role_map_title(role_mode)}角色对照表文件')
    return None


def _custom_upload_data_root() -> Path:
    # 固定在框架资源目录下：这是唯一保证可写的位置，其余候选目录可能只读或随部署变化。
    return get_res_path('TodayWaifu')


def _custom_upload_role_map_path() -> Path:
    return _custom_upload_data_root() / 'custom_role_map.json'


def _legacy_custom_upload_role_map_path() -> Path:
    """旧版用户上传对照表路径，仅用于一次性迁移到 JSON。"""
    return _custom_upload_data_root() / 'custom_role_map.txt'


# 进程内一次性标记：旧 TXT 自定义对照表只尝试迁移一次
# 迁移结果不随进程变化，重复扫描磁盘既无收益又会在每次读写路径上增加一次 stat。
_CUSTOM_ROLE_MAP_MIGRATED = False


def _migrate_custom_role_map() -> None:
    """把用户上传的旧 TXT 对照表一次性迁移为 JSON，读写两条路径都会先调用。

    幂等性由 `migrate_legacy_text_map` 保证（JSON 已存在或 TXT 不存在时直接返回
    False），此处的进程内标记只为省去热路径上重复的文件存在性检查。标记在迁移
    尝试之前置位：迁移失败便不再重试，因为失败原因多为磁盘或权限问题，反复
    重试只会给每次读写附加固定的额外开销。
    """
    global _CUSTOM_ROLE_MAP_MIGRATED
    if _CUSTOM_ROLE_MAP_MIGRATED:
        return
    _CUSTOM_ROLE_MAP_MIGRATED = True
    json_path = _custom_upload_role_map_path()
    if migrate_legacy_text_map(_legacy_custom_upload_role_map_path(), json_path):
        logger.info(f'{LOG_PREFIX} 自定义老婆对照表已从 TXT 迁移为 JSON: {json_path}')


def _custom_upload_role_pile_root() -> Path:
    return _custom_upload_data_root() / 'custom_role_pile'


def _loli_image_root() -> Path:
    return _custom_upload_data_root() / LOLI_IMAGE_DIR_NAME


def _writable_role_map_path() -> Path:
    # 写入前先迁移，避免新 JSON 覆盖掉尚未迁移的旧 TXT 数据；
    # 目录按需创建，使首次写入在全新的资源目录下也能成功。
    _migrate_custom_role_map()
    path = _custom_upload_role_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _writable_role_pile_root() -> Path:
    path = _custom_upload_role_pile_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_role_pile_root() -> Path | None:
    """定位用户自定义角色图片目录，未找到时返回 None。

    顺序为：显式配置 → 以当前工作目录为基准的 gsuid_core 与 data 布局 →
    以插件上级目录为基准的同类布局 → Core 包安装位置下的 data 目录。
    最后一项覆盖插件与 Core 安装在不同目录的部署形态；全部候选均需为已存在
    的目录，未命中只记 info 并返回 None，由调用方决定是否再尝试默认图库。
    """
    configured = _configured_path('DailyWifeCustomRolePilePath')
    candidates = [configured] if configured else []
    candidates.extend(
        [
            Path.cwd() / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            Path.cwd() / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            BASE_DIR.parent / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            BASE_DIR.parent / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
        ]
    )

    core_root = Path(gsuid_core.__file__).resolve().parents[1]
    candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'custom_role_pile')

    for path in candidates:
        if path and path.is_dir():
            logger.debug(f'{LOG_PREFIX} 成功定位自定义角色图片目录: {path}')
            return path
    logger.info(f'{LOG_PREFIX} 未能找到自定义角色图片目录 custom_role_pile')
    return None


def _resolve_default_role_pile_root() -> Path | None:
    """定位内置默认角色图片目录，未找到时返回 None。

    与自定义目录不同，此处不接受配置项：它是用户自定义图片缺失时的兜底来源，
    因而只按部署布局探测，未命中时连同自定义目录一起判定为「没有可用图片」。
    """
    candidates = [
        Path.cwd() / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        Path.cwd() / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        BASE_DIR.parent / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        BASE_DIR.parent / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
    ]

    core_root = Path(gsuid_core.__file__).resolve().parents[1]
    candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile')

    for path in candidates:
        if path and path.is_dir():
            logger.debug(f'{LOG_PREFIX} 成功定位默认角色图片目录: {path}')
            return path
    logger.info(f'{LOG_PREFIX} 未能找到默认角色图片目录 role_pile')
    return None


def _resolve_nte_data_dir(config_key: str, relative: Path) -> Path | None:
    # 异环资源的通用探测：配置优先，其次框架资源目录，最后三种部署布局。
    # 与上面两个解析函数共用同一套顺序约定，便于按同一预期排查路径问题。
    configured = _configured_path(config_key)
    candidates = [configured] if configured else []
    candidates.extend(
        [
            get_res_path('NTEUID') / relative,
            Path.cwd() / 'gsuid_core' / 'data' / 'NTEUID' / relative,
            Path.cwd() / 'data' / 'NTEUID' / relative,
            BASE_DIR.parent / 'gsuid_core' / 'data' / 'NTEUID' / relative,
            BASE_DIR.parent / 'data' / 'NTEUID' / relative,
        ]
    )
    for path in candidates:
        if path and path.is_dir():
            return path
    return None


def _resolve_nte_custom_panel_root() -> Path | None:
    # 自定义面板图优先级高于默认立绘：命中时调用方不会再回落到官方图源。
    path = _resolve_nte_data_dir('DailyWifeNteCustomPanelPath', Path('custom') / 'panel')
    if path is None:
        logger.info(f'{LOG_PREFIX} 未能找到 NTEUID 自定义面板图目录')
    else:
        logger.debug(f'{LOG_PREFIX} 成功定位 NTEUID 自定义面板图目录: {path}')
    return path


def _resolve_nte_default_panel_root() -> Path | None:
    # 默认立绘目录缺失并非错误：调用方会改用 NTE_DETAIL_CDN_BASE 指向的官方地址，
    # 故此处只记 info，不升级为 warning，以免本地未装 NTEUID 时反复告警。
    path = _resolve_nte_data_dir('DailyWifeNteDefaultPanelPath', Path('role') / 'detail')
    if path is None:
        logger.info(f'{LOG_PREFIX} 未能找到 NTEUID 默认角色立绘目录，将使用官方资源地址')
    else:
        logger.debug(f'{LOG_PREFIX} 成功定位 NTEUID 默认角色立绘目录: {path}')
    return path


def _nte_static_resource_roots() -> tuple[Path, ...]:
    """收集 NTEUID 随包分发的静态资源目录，按可信度排序返回。

    包名在部署形态间不一致（顶层包、插件命名空间包、命名空间下的同名子包），
    故逐个尝试 `find_spec`；任一导入失败只跳过该项而不中断，缺失某个包名
    不应让其余候选失效。`submodule_search_locations` 覆盖普通包，
    `spec.origin` 覆盖单文件模块，两者都收集后再按顺序去重，避免同一目录被
    重复扫描。返回空元组表示无内置资源可用，调用方将退到默认立绘或官方地址。
    """
    candidates: list[Path] = []
    for module_name in ('NTEUID', 'gsuid_core.plugins.NTEUID', 'gsuid_core.plugins.NTEUID.NTEUID'):
        try:
            spec = find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec is None:
            continue
        locations = list(spec.submodule_search_locations or ())
        if spec.origin:
            locations.append(str(Path(spec.origin).parent))
        for location in locations:
            module_root = Path(location)
            candidates.extend((module_root / 'resource', module_root / 'NTEUID' / 'resource'))

    # 包未安装时仍按源码目录布局尝试，覆盖「同仓库并列 checkout」的开发场景。
    candidates.extend(
        [
            BASE_DIR.parent / 'NTEUID' / 'NTEUID' / 'resource',
            BASE_DIR.parent / 'NTEUID' / 'resource',
        ]
    )
    existing: list[Path] = []
    for path in candidates:
        if path.is_dir() and path not in existing:
            existing.append(path)
    return tuple(existing)


def _pgr_wife_root() -> Path:
    # 未配置时回落到框架资源目录而非报错：战双图库允许用户在部署后再补图，
    # 因此目录可以暂时不存在，由调用方在扫描时得出「无角色文件夹」的结论。
    configured = str(_cfg('DailyWifePgrGalleryPath') or '').strip().strip('"')
    if configured:
        return Path(configured).expanduser()
    return get_res_path('TodayWaifu') / PGR_WIFE_DIR_NAME


def _gallery_image_cache_root() -> Path:
    return _custom_upload_data_root() / 'gallery_image_cache'


def _daily_rng(ev: Event, user_id: str | int | None = None, salt: str = '') -> random.Random:
    """构造抽取的真随机源。

    使用系统熵池构造真随机数生成器（random.SystemRandom），替代基于
    日期、用户ID与群号拼接固定种子的伪随机逻辑。
    「当天同一用户拥有固定老婆」的业务语义由落库记录与每日上下文（DailyWifeRecord）
    负责持久化和拦截，生成新记录时则采用真随机确保结果公平且不可预测。
    """
    logger.debug(f'{LOG_PREFIX} 使用系统真随机源抽取')
    return random.SystemRandom()


def _event_rng(ev: Event) -> random.Random:
    return _daily_rng(ev)


def _today_key() -> str:
    # 以本地日期为准：跨日翻转必须与用户的自然日一致，不能用 UTC 造成提前或延后换日。
    return date.today().isoformat()


def _context_key(ev: Event) -> str:
    # 私聊无群号，统一记为 direct，使私聊记录落在同一个键下而不会互相覆盖。
    return f'{ev.bot_id}:{ev.group_id or "direct"}'


def _user_key(ev: Event, user_id: str | int | None = None) -> str:
    return str(ev.user_id if user_id is None else user_id)


def _daily_context_key(ev: Event) -> ContextKey:
    # 由 _context_key 反解出 bot_id 与 group_id，保证两种键在拼接规则上不会漂移；
    # 私聊在此同样归一为 direct，与 ContextKey 的既有取值保持一致。
    bot_id, _, group_id = _context_key(ev).partition(':')
    return ContextKey(_today_key(), bot_id, group_id or 'direct')
