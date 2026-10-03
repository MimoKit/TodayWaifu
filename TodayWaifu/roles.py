"""TodayWaifu 的角色对照表加载与候选角色归并。"""
from __future__ import annotations

import time
import random
from pathlib import Path

from gsuid_core.logger import logger

from .paths import (
    _role_mode,
    _pgr_wife_root,
    _role_map_title,
    _resolve_role_map_path,
    _resolve_role_pile_root,
    _migrate_custom_role_map,
    _nte_static_resource_roots,
    _custom_upload_role_map_path,
    _custom_upload_role_pile_root,
    _resolve_nte_custom_panel_root,
    _resolve_default_role_pile_root,
    _resolve_nte_default_panel_root,
)
from .state import CANDIDATE_CACHE
from .domain import WifeRecord, RoleCandidate
from .aliases import resolve_role_name
from .payloads import RoleAccumulator, NamedRoleAccumulator
from .constants import (
    LOG_PREFIX,
    IMAGE_EXTENSIONS,
    CACHE_TTL_SECONDS,
    EXCLUDED_ROLE_NAMES,
    NTE_DETAIL_CDN_BASE,
    EXCLUDED_ROLE_KEYWORDS,
    NTE_EXCLUDED_ROLE_NAMES,
    NTE_EXCLUDED_ROLE_KEYWORDS,
    _cfg_bool,
)
from .file_cache import prefer_cached_urls, read_file_bytes_cached
from .folder_gallery import scan_named_role_directories
from .role_map_store import loads_role_map

# (路径, 分节) -> (源字节对象, 解析结果)；对照表只有几个，无需设容量上限
_ROLE_MAP_PARSE_CACHE: dict[tuple[str, str | None], tuple[bytes, dict[str, str]]] = {}


def _pick_role_record(
    candidates: tuple['RoleCandidate', ...],
    rng: random.Random,
) -> 'WifeRecord | None':
    """随机挑一个角色，并在该角色的图片里优先挑**已在磁盘缓存中**的那张。

    零点前预热只覆盖每个角色的前几张图，而此前的实现是 `rng.choice(role.images)`：
    角色有 10 张图、只暖 2 张时命中率仅 20%，其余 80% 仍会在零点走网络。改从已缓存的
    图里挑，预热命中率即提升到 100%——随机性依旧保留，只是随机范围收敛到已缓存的那批；
    一张都没缓存时 `prefer_cached_urls` 返回全量列表，行为与原来完全一致。
    """
    if not candidates:
        return None

    role = rng.choice(candidates)
    return WifeRecord.from_role(role, rng.choice(prefer_cached_urls(role.images)))


def _load_role_map(path: Path, section: str | None = None) -> dict[str, str]:
    """读取角色对照表（JSON 或旧 TXT），按 mtime 缓存文件内容避免每次抽签读盘。"""
    try:
        raw = read_file_bytes_cached(path)
    except OSError:
        return {}
    # 字节缓存命中时返回的是同一个 bytes 对象，借此跳过重复的 JSON 解析；
    # 返回副本，因为调用方会就地 update
    cache_key = (str(path), section)
    parsed = _ROLE_MAP_PARSE_CACHE.get(cache_key)
    if parsed is None or parsed[0] is not raw:
        parsed = (raw, loads_role_map(raw.decode('utf-8'), section))
        _ROLE_MAP_PARSE_CACHE[cache_key] = parsed
        logger.debug(f'{LOG_PREFIX} 加载了 {len(parsed[1])} 个角色 ID 映射关系')
    return dict(parsed[1])


def _load_custom_upload_role_map() -> dict[str, str]:
    """读取用户上传的自定义老婆对照表；首次读取时把旧 TXT 迁移为 JSON。"""
    _migrate_custom_role_map()
    map_path = _custom_upload_role_map_path()
    return _load_role_map(map_path) if map_path.is_file() else {}


def _role_images(role_dir: Path) -> tuple[str, ...]:
    # 递归扫描以覆盖角色目录下的多级子目录；排序保证同一角色图片顺序稳定，
    # 使抽签结果在相同种子下可复现
    images = [
        path
        for path in role_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(str(path) for path in sorted(images, key=lambda path: str(path).lower()))


def _collect_role_candidates(
    role_map: dict[str, str],
    pile_root: Path,
    default_pile_root: Path | None,
    upload_pile_root: Path | None = None,
    default_name_patterns: tuple[str, ...] = ('role_pile_{role_id}{ext}',),
) -> tuple[RoleCandidate, ...]:
    grouped: dict[str, RoleAccumulator] = {}
    for role_id in sorted(role_map.keys(), key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        if _is_excluded_role(role_name):
            continue

        images: list[str] = []

        # 1. 优先读取 GSCore data 下本插件的自定义老婆图片，避免和 XWUID 自定义面板图目录混在一起
        if upload_pile_root and upload_pile_root.is_dir():
            upload_role_dir = upload_pile_root / role_id
            if upload_role_dir.is_dir():
                images.extend(_role_images(upload_role_dir))

        # 2. 尝试从 XWUID 自定义目录获取
        role_dir = pile_root / role_id
        if role_dir.is_dir():
            images.extend(_role_images(role_dir))

        # 3. 如果没有自定义图片，且存在默认面板目录，尝试获取默认图片
        if not images and default_pile_root and default_pile_root.is_dir():
            for pattern in default_name_patterns:
                for ext in IMAGE_EXTENSIONS:
                    fallback_img = default_pile_root / pattern.format(role_id=role_id, ext=ext)
                    if fallback_img.is_file():
                        images.append(str(fallback_img))
                        break
                if images:
                    break

        if not images:
            continue

        # 同一角色名可能对应多个 ID（跨作品同名或改过 ID），按名归并后图片合并去重
        bucket = grouped.setdefault(role_name, {'role_ids': [], 'images': []})
        bucket['role_ids'].append(role_id)
        bucket['images'].extend(images)

    candidates: list[RoleCandidate] = []
    for role_name, bucket in grouped.items():
        candidates.append(
            RoleCandidate(
                name=role_name,
                role_ids=tuple(str(item) for item in bucket['role_ids']),
                images=tuple(bucket['images']),
            )
        )
    logger.debug(f'{LOG_PREFIX} 成功归并候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda item: item.name))


def _load_custom_upload_candidates() -> tuple[RoleCandidate, ...]:
    # 只扫描上传目录，故传入不存在的占位路径，使 XWUID 自定义目录分支必然短路
    role_map = _load_custom_upload_role_map()
    if not role_map:
        return ()

    upload_pile_root = _custom_upload_role_pile_root()
    if not upload_pile_root.is_dir():
        return ()

    return _collect_role_candidates(
        role_map,
        Path('dummy_non_existent_path'),
        None,
        upload_pile_root,
    )


def _merge_role_candidates(
    base: tuple[RoleCandidate, ...],
    extra: tuple[RoleCandidate, ...],
) -> tuple[RoleCandidate, ...]:
    # 按去符号化后的角色名归并：同一角色在不同数据源里的写法可能有细微差异，
    # ID 与图片列表在此合并去重，避免同一角色以多条候选重复出现
    if not extra:
        return base
    if not base:
        return tuple(sorted(extra, key=lambda item: item.name))

    grouped: dict[str, NamedRoleAccumulator] = {}
    for candidate in (*base, *extra):
        key = _normalize_role_name(candidate.name)
        bucket = grouped.setdefault(
            key,
            {'name': candidate.name, 'role_ids': [], 'images': []},
        )
        for role_id in candidate.role_ids:
            if role_id not in bucket['role_ids']:
                bucket['role_ids'].append(role_id)
        for image in candidate.images:
            if image not in bucket['images']:
                bucket['images'].append(image)

    return tuple(
        sorted(
            (
                RoleCandidate(
                    name=str(bucket['name']),
                    role_ids=tuple(str(item) for item in bucket['role_ids']),
                    images=tuple(str(item) for item in bucket['images']),
                )
                for bucket in grouped.values()
                if bucket['role_ids'] and bucket['images']
            ),
            key=lambda item: item.name,
        )
    )


def _load_mode_role_map(mode: str = 'wife') -> dict[str, str]:
    role_map_path = _resolve_role_map_path(mode)
    if role_map_path is None:
        return {}
    return _load_role_map(role_map_path, _role_mode(mode))


def _load_local_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    """加载本地图片候选，带 TTL 缓存。

    该路径执行 `rglob` 全量目录扫描，代价高昂。图库不可用时每次发送失败都会经
    `_find_local_role_image` 回退至此，若不加缓存，图库一挂即会耗尽插件线程池做目录扫描。
    TTL 到期才会重新扫描，上传图片时由 `_invalidate_candidate_cache` 主动失效，
    因此用户新增图片的可见延迟受 TTL 约束而非立即生效。
    """
    role_mode = _role_mode(mode)
    cache_key = f'local:{role_mode}'
    now = time.time()
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    result = _scan_local_candidates(role_mode)
    # 只缓存成功结果：失败往往是「目录暂时不可用」，不该被缓存住
    if result[0]:
        CANDIDATE_CACHE[cache_key] = (now, result)
    return result


def _scan_local_candidates(
    role_mode: str,
) -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    # 执行一次本地目录全量扫描，返回候选列表或面向用户的失败原因。
    # 失败一律返回可读文案而非抛出异常，因为调用方需要把原因直接回给用户；
    # 且 (None, 原因) 表示扫描失败、((), None) 表示确实无角色，故失败时
    # 不得返回空元组，否则上层只能给出笼统的「没有可用角色」。
    title = _role_map_title(role_mode)
    logger.debug(f'{LOG_PREFIX} 开始从本地加载{title}角色候选列表...')
    role_map_path = _resolve_role_map_path(role_mode)
    if role_map_path is None:
        return None, f'没有找到鸣潮{title}角色 ID 对照表。'

    pile_root = _resolve_role_pile_root()
    default_pile_root = _resolve_default_role_pile_root()
    upload_pile_root = _custom_upload_role_pile_root() if role_mode == 'wife' else None
    if (
        pile_root is None
        and default_pile_root is None
        and (upload_pile_root is None or not upload_pile_root.is_dir())
    ):
        return None, '没有找到 custom_role_pile 或默认 role_pile 图片目录。'

    if pile_root is None:
        pile_root = Path("dummy_non_existent_path")

    try:
        role_map = _load_role_map(role_map_path, role_mode)
        if role_mode == 'wife':
            role_map.update(_load_custom_upload_role_map())
        candidates = _collect_role_candidates(role_map, pile_root, default_pile_root, upload_pile_root)
    except (OSError, ValueError) as exc:
        logger.exception(f'{LOG_PREFIX} 读取本地图片目录失败: {exc}')
        return None, '读取本地图片目录失败。'

    if not candidates:
        logger.warning(f'{LOG_PREFIX} 扫描图片目录完成，但未找到可用角色图片')
        return None, '图片目录里没有找到可用角色图片。'
    return candidates, None


def _load_nte_local_candidates(
    allow_remote_fallback: bool = True,
) -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_map_path = _resolve_role_map_path('nte')
    if role_map_path is None:
        return None, '没有找到异环角色 ID 对照表。'

    try:
        role_map = _load_role_map(role_map_path, 'nte')
    except OSError as exc:
        logger.exception(f'{LOG_PREFIX} 读取异环角色对照表失败: {exc}')
        return None, '读取异环角色 ID 对照表失败。'
    role_map = {
        role_id: role_name
        for role_id, role_name in role_map.items()
        if not _is_excluded_nte_role(role_name)
    }
    if not role_map:
        return None, '异环角色 ID 对照表为空。'

    custom_root = _resolve_nte_custom_panel_root()
    default_root = _resolve_nte_default_panel_root()
    static_roots = _nte_static_resource_roots()
    custom_candidates = _collect_role_candidates(
        role_map,
        custom_root or Path('dummy_non_existent_nte_custom_path'),
        None,
    )
    custom_by_id = {
        role_id: candidate
        for candidate in custom_candidates
        for role_id in candidate.role_ids
    }

    candidates: list[RoleCandidate] = []
    for role_id in sorted(role_map, key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        custom = custom_by_id.get(role_id)
        if custom is not None:
            candidates.append(custom)
            continue

        images: tuple[str, ...] = ()
        for resource_root in static_roots:
            for subdir in (Path('char') / 'fashion', Path('char') / 'lihui'):
                role_dir = resource_root / subdir / role_id
                if role_dir.is_dir():
                    images = _role_images(role_dir)
                if images:
                    break
            if images:
                break

        if not images and default_root is not None:
            for ext in IMAGE_EXTENSIONS:
                default_image = default_root / f'{role_id}{ext}'
                if default_image.is_file():
                    images = (str(default_image),)
                    break

        # 允许远端兜底时用 CDN 占位，实际可用性交给发送阶段的下载失败处理
        if not images and allow_remote_fallback:
            images = (f'{NTE_DETAIL_CDN_BASE}/{role_id}.png',)
        if not images:
            # local 模式跳过本地无图的角色，避免纯本地模式仍去请求官方资源
            continue
        candidates.append(RoleCandidate(role_name, (role_id,), images))

    if not candidates:
        # local 模式下本地一张图都没有：必须给出原因，不能返回空候选让上层只报“没有可用角色”
        return None, '本地没有找到可用的异环角色图片，请准备本地图片或改用 gallery 数据源。'
    logger.debug(f'{LOG_PREFIX} 成功加载异环老婆候选 {len(candidates)} 名')
    return tuple(candidates), None


def _load_pgr_local_candidates() -> tuple[RoleCandidate, ...]:
    # 战双图库 rglob 全量扫描是昂贵操作，接入候选缓存（TTL 见 CACHE_TTL_SECONDS），
    # 上传图片时 _invalidate_candidate_cache 会主动失效
    cache_key = f'pgr_local:{_pgr_wife_root()}'
    now = time.time()
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    rows = scan_named_role_directories(_pgr_wife_root(), IMAGE_EXTENSIONS)
    candidates = tuple(
        RoleCandidate(name=name, role_ids=(name,), images=images)
        for name, images in rows
    )
    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates


def _normalize_role_name(name: str) -> str:
    # 不同来源对间隔号的写法不一致，统一为「·」后再比较，避免同一角色被判为不同名字
    unified = name.replace('・', '·').replace('•', '·').strip()
    # 再按 XWUID 别名表归一，用户可以用「小西王」这类别名称呼角色
    return resolve_role_name(unified)


_MALE_ROLE_NAMES_NORM = {_normalize_role_name(n) for n in EXCLUDED_ROLE_NAMES}


def _is_male_role(name: str) -> bool:
    # 老公对照表可被用户覆盖，优先采用；缺失时退回内置名单，避免用户未配置时误选男性角色
    husband_names = {
        _normalize_role_name(role_name)
        for role_name in _load_mode_role_map('husband').values()
    }
    return _normalize_role_name(name) in (husband_names or _MALE_ROLE_NAMES_NORM)


def _is_excluded_role(name: str) -> bool:
    return any(keyword in name for keyword in EXCLUDED_ROLE_KEYWORDS)


def _is_excluded_nte_role(name: str) -> bool:
    normalized = _normalize_role_name(name)
    if normalized in NTE_EXCLUDED_ROLE_NAMES:
        return True
    return any(keyword in normalized for keyword in NTE_EXCLUDED_ROLE_KEYWORDS)


def _husband_enabled() -> bool:
    return _cfg_bool('DailyWifeHusbandEnabled', False)


def _loli_enabled() -> bool:
    return _cfg_bool('DailyLoliEnabled', True)


def _shota_enabled() -> bool:
    return _cfg_bool('DailyShotaEnabled', True)


def _husband_available() -> bool:
    return _husband_enabled()


def _filter_by_mode(
    candidates: tuple['RoleCandidate', ...],
    mode: str,
) -> tuple['RoleCandidate', ...]:
    role_mode = _role_mode(mode)
    if role_mode == 'normal':
        return candidates
    if role_mode == 'wife' and _cfg_bool('DailyWifeNormalEnabled', False):
        return candidates
    # 兼容整体开关：同时按 ID 与规范化角色名匹配，以便本地图片目录名与对照表键不一致时仍能筛选
    role_map = _load_mode_role_map(mode)
    if role_mode == 'wife':
        role_map.update(_load_custom_upload_role_map())
    allowed_ids = set(role_map)
    allowed_names = {_normalize_role_name(name) for name in role_map.values()}
    return tuple(
        role
        for role in candidates
        if any(role_id in allowed_ids for role_id in role.role_ids)
        or _normalize_role_name(role.name) in allowed_names
    )
