"""TodayWaifu 的角色对照表加载与候选角色归并。"""
from __future__ import annotations

import random
import time
from pathlib import Path

from gsuid_core.logger import logger

from .constants import (
    CACHE_TTL_SECONDS,
    EXCLUDED_ROLE_KEYWORDS,
    EXCLUDED_ROLE_NAMES,
    IMAGE_EXTENSIONS,
    LOG_PREFIX,
    NTE_DETAIL_CDN_BASE,
    NTE_EXCLUDED_ROLE_KEYWORDS,
    NTE_EXCLUDED_ROLE_NAMES,
    ROLE_MAP_RE,
    _cfg_bool,
    _image_source,
)
from .domain import RoleCandidate, WifeRecord
from .file_cache import read_file_text_cached
from .folder_gallery import scan_named_role_directories
from .paths import (
    _custom_upload_role_map_path,
    _custom_upload_role_pile_root,
    _nte_static_resource_roots,
    _pgr_wife_root,
    _resolve_default_role_pile_root,
    _resolve_nte_custom_panel_root,
    _resolve_nte_default_panel_root,
    _resolve_role_map_path,
    _resolve_role_pile_root,
    _role_map_title,
    _role_mode,
)
from .payloads import NamedRoleAccumulator, RoleAccumulator
from .state import CANDIDATE_CACHE

def _pick_role_record(
    candidates: tuple['RoleCandidate', ...],
    rng: random.Random,
) -> 'WifeRecord | None':
    if not candidates:
        return None

    role = rng.choice(candidates)
    return WifeRecord.from_role(role, rng.choice(role.images))


def _load_role_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    # 角色对照表按 mtime 缓存文件内容，文件变更后自动失效，避免每次抽签都读盘
    try:
        text = read_file_text_cached(path)
    except OSError:
        return result
    for line in text.splitlines():
        match = ROLE_MAP_RE.match(line)
        if not match:
            continue
        role_id, role_name = match.groups()
        role_name = role_name.strip()
        if role_name:
            result[role_id] = role_name
    logger.debug(f'{LOG_PREFIX} 加载了 {len(result)} 个角色 ID 映射关系')
    return result


def _load_custom_upload_role_map() -> dict[str, str]:
    map_path = _custom_upload_role_map_path()
    return _load_role_map(map_path) if map_path.is_file() else {}


def _role_images(role_dir: Path) -> tuple[str, ...]:
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
    return _load_role_map(role_map_path) if role_map_path else {}


def _load_local_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
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
        role_map = _load_role_map(role_map_path)
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


def _load_nte_local_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_map_path = _resolve_role_map_path('nte')
    if role_map_path is None:
        return None, '没有找到异环角色 ID 对照表。'

    try:
        role_map = _load_role_map(role_map_path)
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

        if not images:
            images = (f'{NTE_DETAIL_CDN_BASE}/{role_id}.png',)
        candidates.append(RoleCandidate(role_name, (role_id,), images))

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
    return name.replace('・', '·').replace('•', '·').strip()


_MALE_ROLE_NAMES_NORM = {_normalize_role_name(n) for n in EXCLUDED_ROLE_NAMES}


def _is_male_role(name: str) -> bool:
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


def _gallery_mode_enabled() -> bool:
    return _image_source() == 'gallery'


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
