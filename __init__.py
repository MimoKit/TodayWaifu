from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import Plugins, SV

from .daily_wife_config import DailyWifeConfig


Plugins(
    name='gs_wuwa_daily_wife',
    force_prefix=['wl'],
    allow_empty_prefix=False,
)

sv = SV('鸣潮今日老婆')
BASE_DIR = Path(__file__).parent
ROLE_MAP_RE = re.compile(r'^\s*(\d+)\s*[:：]\s*(.+?)\s*$')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[Path, ...]


def _cfg(key: str) -> Any:
    return DailyWifeConfig.get_config(key).data


def _configured_path(key: str) -> Path | None:
    raw = str(_cfg(key) or '').strip().strip('"')
    if not raw:
        return None
    return Path(raw).expanduser()


def _resolve_role_map_path() -> Path | None:
    configured = _configured_path('DailyWifeRoleMapPath')
    candidates = [
        configured,
        BASE_DIR / 'role_id_map.txt',
        BASE_DIR.parent / '鸣潮面板id对照角色.txt',
        Path.cwd() / '鸣潮面板id对照角色.txt',
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def _resolve_role_pile_root() -> Path | None:
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

    try:
        import gsuid_core

        core_root = Path(gsuid_core.__file__).resolve().parents[1]
        candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'custom_role_pile')
    except Exception:
        pass

    for path in candidates:
        if path and path.is_dir():
            return path
    return None


def _load_role_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = ROLE_MAP_RE.match(line)
        if not match:
            continue
        role_id, role_name = match.groups()
        role_name = role_name.strip()
        if role_name:
            result[role_id] = role_name
    return result


def _role_images(role_dir: Path) -> tuple[Path, ...]:
    images = [
        path
        for path in role_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(sorted(images, key=lambda path: str(path).lower()))


def _collect_role_candidates(role_map: dict[str, str], pile_root: Path) -> tuple[RoleCandidate, ...]:
    grouped: dict[str, dict[str, list[Any]]] = {}
    for role_id in sorted(role_map.keys(), key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        role_dir = pile_root / role_id
        if not role_dir.is_dir():
            continue
        images = _role_images(role_dir)
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
    return tuple(sorted(candidates, key=lambda item: item.name))


def _daily_rng(ev: Event) -> random.Random:
    group_id = ev.group_id or 'direct'
    seed = f'{date.today().isoformat()}:{ev.bot_id}:{group_id}:{ev.user_id}'
    return random.Random(seed)


def _build_text(role: RoleCandidate) -> str:
    text = str(_cfg('DailyWifeTextTemplate') or '你今天的老婆是{name}').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
    )
    if bool(_cfg('DailyWifeShowRoleId')):
        text += f'\n角色ID：{"/".join(role.role_ids)}'
    return text


async def _send_daily_wife(bot: Bot, ev: Event):
    role_map_path = _resolve_role_map_path()
    if role_map_path is None:
        return await bot.send('没有找到鸣潮角色 ID 对照表。')

    pile_root = _resolve_role_pile_root()
    if pile_root is None:
        return await bot.send('没有找到 custom_role_pile 图片目录。')

    role_map = _load_role_map(role_map_path)
    candidates = _collect_role_candidates(role_map, pile_root)
    if not candidates:
        return await bot.send('custom_role_pile 里没有找到可用角色图片。')

    rng = _daily_rng(ev)
    role = rng.choice(candidates)
    image = rng.choice(role.images)
    logger.info(f'[gs_wuwa_daily_wife] user={ev.user_id} role={role.name} ids={role.role_ids} image={image}')

    if bool(_cfg('DailyWifeSendText')):
        await bot.send([_build_text(role), MessageSegment.image(image)])
    else:
        await bot.send(MessageSegment.image(image))


@sv.on_fullmatch('今日老婆', block=True)
async def daily_wife(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev)