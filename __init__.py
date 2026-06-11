from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import Plugins, SV
from gsuid_core.utils.database.models import CoreUser

from .daily_wife_config import DailyWifeConfig


Plugins(
    name='gs_wuwa_daily_wife',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

sv = SV('鸣潮今日老婆')
BASE_DIR = Path(__file__).parent
DEFAULT_GALLERY_API_URL = 'https://img.xlinxc.cn/api/xwuid/roles'
CACHE_TTL_SECONDS = 300
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
EXCLUDED_ROLE_KEYWORDS = ('漂泊者',)
CANDIDATE_CACHE: tuple[float, tuple['RoleCandidate', ...]] | None = None


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass(frozen=True)
class WifeRecord:
    name: str
    role_ids: tuple[str, ...]
    image: str

    @classmethod
    def from_role(cls, role: RoleCandidate, image: str) -> 'WifeRecord':
        return cls(role.name, role.role_ids, image)

    def to_role(self) -> RoleCandidate:
        return RoleCandidate(self.name, self.role_ids, (self.image,))


def _cfg(key: str) -> Any:
    return DailyWifeConfig.get_config(key).data


def _cfg_bool(key: str, default: bool = False) -> bool:
    value = _cfg(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '开启'}:
            return True
        if text in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '关闭'}:
            return False
    return default


def _gallery_api_url() -> str:
    return str(_cfg('DailyWifeGalleryApiUrl') or DEFAULT_GALLERY_API_URL).strip()


def _gallery_auth_header() -> str | None:
    username = str(_cfg('DailyWifeGalleryUsername') or '').strip()
    password = str(_cfg('DailyWifeGalleryPassword') or '').strip()
    if not username or not password:
        return None
    token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
    return f'Basic {token}'


def _request_headers() -> dict[str, str]:
    headers = {'User-Agent': 'gs_wuwa_daily_wife/1.0'}
    auth = _gallery_auth_header()
    if auth:
        headers['Authorization'] = auth
    return headers


def _http_get(url: str, *, timeout: int = 15) -> bytes:
    request = Request(url, headers=_request_headers())
    with urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _fetch_gallery_payload_sync() -> dict[str, Any]:
    api_url = _gallery_api_url()
    if not api_url:
        raise RuntimeError('未配置画廊接口地址。')
    try:
        body = _http_get(api_url, timeout=15)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('画廊账号或密码不正确，接口返回 401。') from exc
        raise RuntimeError(f'请求画廊接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求画廊接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求画廊接口超时。') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError('画廊接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('画廊接口返回格式不正确。')
    return payload


def _is_excluded_role(name: str) -> bool:
    return name in EXCLUDED_ROLE_NAMES or any(keyword in name for keyword in EXCLUDED_ROLE_KEYWORDS)


def _parse_role_candidates(payload: dict[str, Any]) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()

    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name or _is_excluded_role(name):
            continue

        role_ids_data = item.get('role_ids') or []
        role_ids = tuple(str(role_id) for role_id in role_ids_data if str(role_id).strip())

        images: list[str] = []
        for image_item in item.get('images') or []:
            if isinstance(image_item, dict):
                url = str(image_item.get('url') or '').strip()
            else:
                url = str(image_item or '').strip()
            if url.startswith(('http://', 'https://')):
                images.append(url)
        if images:
            candidates.append(RoleCandidate(name=name, role_ids=role_ids, images=tuple(images)))

    return tuple(sorted(candidates, key=lambda role: role.name))


async def _load_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    global CANDIDATE_CACHE

    now = time.time()
    if CANDIDATE_CACHE and now - CANDIDATE_CACHE[0] < CACHE_TTL_SECONDS:
        return CANDIDATE_CACHE[1], None

    try:
        payload = await asyncio.to_thread(_fetch_gallery_payload_sync)
        candidates = _parse_role_candidates(payload)
    except RuntimeError as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取画廊接口失败: {exc}')
        return None, str(exc)
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取画廊接口异常: {exc}')
        return None, '读取画廊接口失败。'

    if not candidates:
        return None, '画廊接口里没有找到可用的角色立绘。'

    CANDIDATE_CACHE = (now, candidates)
    return candidates, None


def _download_image_sync(url: str) -> bytes:
    try:
        return _http_get(url, timeout=20)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('画廊账号或密码不正确，图片返回 401。') from exc
        raise RuntimeError(f'下载图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载图片超时。') from exc


async def _download_image(url: str) -> bytes:
    return await asyncio.to_thread(_download_image_sync, url)


def _daily_rng(ev: Event, user_id: str | int | None = None) -> random.Random:
    group_key = ev.group_id or 'direct'
    target_user_id = ev.user_id if user_id is None else user_id
    seed = f'{date.today().isoformat()}:{target_user_id}:{group_key}'
    return random.Random(seed)


def _is_master(ev: Event) -> bool:
    try:
        masters = core_config.get_config('masters')
    except Exception:
        masters = []
    return str(ev.user_id) in {str(master) for master in masters}


def _event_rng(ev: Event) -> random.Random:
    if bool(_cfg('DailyWifeMasterUnlimited')) and _is_master(ev):
        return random.Random()
    return _daily_rng(ev)


def _wife_data_path() -> Path:
    return BASE_DIR / 'daily_wife_data.json'


def _today_key() -> str:
    return date.today().isoformat()


def _context_key(ev: Event) -> str:
    return f'{ev.bot_id}:{ev.group_id or "direct"}'


def _user_key(ev: Event, user_id: str | int | None = None) -> str:
    return str(ev.user_id if user_id is None else user_id)


def _valid_display_name(value: Any, user_id: str | int | None = None) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    if user_id is not None and text == str(user_id):
        return ''
    return text


def _user_display_name(ev: Event, user_id: str | int | None = None) -> str:
    key = _user_key(ev, user_id)
    if user_id is None or key == str(ev.user_id):
        sender = getattr(ev, 'sender', {}) or {}
        if isinstance(sender, dict):
            for field in ('card', 'nickname', 'name', 'username', 'user_name'):
                value = _valid_display_name(sender.get(field), key)
                if value:
                    return value
    return key


async def _load_group_display_names(ev: Event) -> dict[str, str]:
    if not ev.group_id:
        return {}

    try:
        users = await CoreUser.get_group_all_user(str(ev.group_id))
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取 GsCore 群成员缓存失败: {exc}')
        return {}

    preferred_bot_id = str(getattr(ev, 'real_bot_id', '') or ev.bot_id or '').strip()
    exact: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for user in users or []:
        user_id = str(getattr(user, 'user_id', '') or '').strip()
        if not user_id:
            continue
        name = _valid_display_name(getattr(user, 'user_name', ''), user_id)
        if name:
            fallback[user_id] = name
            if preferred_bot_id and str(getattr(user, 'bot_id', '') or '').strip() == preferred_bot_id:
                exact[user_id] = name
    return exact or fallback


def _load_wife_data() -> dict[str, Any]:
    path = _wife_data_path()
    if not path.is_file():
        return {'days': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取数据文件失败，将使用空数据: {exc}')
        return {'days': {}}
    if not isinstance(data, dict):
        return {'days': {}}
    data.setdefault('days', {})
    return data


def _save_wife_data(data: dict[str, Any]) -> None:
    _wife_data_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _get_today_context(data: dict[str, Any], ev: Event) -> dict[str, Any]:
    day = data.setdefault('days', {}).setdefault(_today_key(), {})
    context = day.setdefault(_context_key(ev), {})
    context.setdefault('wives', {})
    context.setdefault('rob_attempts', {})
    return context


def _record_to_dict(record: WifeRecord, ev: Event | None = None, user_id: str | int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {'name': record.name, 'role_ids': list(record.role_ids), 'image': record.image}
    if ev is not None:
        data.update(
            {
                'user_id': _user_key(ev, user_id),
                'display_name': _user_display_name(ev, user_id),
                'group_id': str(ev.group_id or 'direct'),
                'bot_id': str(ev.bot_id),
                'day': _today_key(),
                'updated_at': int(time.time()),
            }
        )
    return data


def _record_from_dict(data: dict[str, Any]) -> WifeRecord | None:
    try:
        record = WifeRecord(
            name=str(data['name']),
            role_ids=tuple(str(item) for item in data.get('role_ids', ())),
            image=str(data['image']),
        )
    except Exception:
        return None
    if not record.name or not record.image.startswith(('http://', 'https://')):
        return None
    return record


def _get_event_target_user_id(ev: Event) -> str | None:
    for attr in ('at_list', 'at', 'target_id', 'target_user_id'):
        value = getattr(ev, attr, None)
        if value is not None:
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            if isinstance(value, bool):
                continue

            v_str = str(value).strip()
            if v_str and v_str.lower() not in ('none', 'true', 'false', 'all'):
                match = re.search(r'(\d{5,20})', v_str)
                if match:
                    return match.group(1)
                if 'CQ:at' not in v_str and '<at' not in v_str:
                    return v_str

    for attr in ('text', 'raw_text', 'raw_message', 'message', 'original_message'):
        t = getattr(ev, attr, None)
        if t is not None:
            t_str = str(t).strip()
            if t_str:
                match = re.search(r'(?:@|qq=|qq:|QQ=|QQ:)?(\d{5,20})', t_str)
                if match:
                    return match.group(1)

    return None


def _rob_success_rate() -> float:
    try:
        value = float(_cfg('DailyWifeRobSuccessRate'))
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(1.0, value))


def _build_rob_success_text(role: RoleCandidate, target_user_id: str) -> str:
    return str(_cfg('DailyWifeRobSuccessTemplate') or '抢老婆成功！你把对方今天的老婆{name}抢过来了！').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _build_text(role: RoleCandidate) -> str:
    lines = [
        str(_cfg('DailyWifeTextTemplate') or '你今天的老婆是{name}').format(
            name=role.name,
            role_id='/'.join(role.role_ids),
        )
    ]
    if bool(_cfg('DailyWifeShowRoleId')):
        lines.append(f'角色ID：{"/".join(role.role_ids)}')
    return '\n'.join(lines)


async def _ensure_daily_wife_record(ev: Event, user_id: str | int | None = None) -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    key = _user_key(ev, user_id)
    current = context['wives'].get(key)
    if isinstance(current, dict):
        record = _record_from_dict(current)
        if record is not None:
            return record

    candidates, error = await _load_candidates()
    if error or not candidates:
        return None

    rng = _daily_rng(ev, key)
    role = rng.choice(candidates)
    image = rng.choice(role.images)
    record = WifeRecord.from_role(role, image)
    context['wives'][key] = _record_to_dict(record, ev, key)
    _save_wife_data(data)
    return record


def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context['wives'].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


def _save_daily_wife_record(ev: Event, record: WifeRecord, user_id: str | int | None = None) -> None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    key = _user_key(ev, user_id)
    context['wives'][key] = _record_to_dict(record, ev, key)
    _save_wife_data(data)


async def _wife_list_text(ev: Event) -> str:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    wives = context.get('wives', {})
    if not isinstance(wives, dict) or not wives:
        return '今天本群还没有人抽老婆。'

    group_display_names = await _load_group_display_names(ev)
    data_changed = False
    items: list[tuple[int, str, str]] = []
    for user_id, raw_record in wives.items():
        if not isinstance(raw_record, dict):
            continue
        record = _record_from_dict(raw_record)
        if record is None:
            continue
        display_name = _valid_display_name(raw_record.get('display_name'), user_id)
        if not display_name:
            display_name = group_display_names.get(str(user_id), '')
            if display_name:
                raw_record['display_name'] = display_name
                raw_record['display_name_source'] = 'coreuser'
                raw_record['display_name_updated_at'] = int(time.time())
                data_changed = True
        if not display_name:
            display_name = str(user_id)
        updated_at = raw_record.get('updated_at')
        try:
            order = int(updated_at)
        except (TypeError, ValueError):
            order = 0
        items.append((order, display_name, record.name))

    if not items:
        return '今天本群还没有可用的老婆记录。'

    if data_changed:
        _save_wife_data(data)

    items.sort(key=lambda item: (item[0], item[1]))
    lines = ['今日老婆列表：']
    lines.extend(f'{index}.{display_name}今天的老婆是{wife_name}' for index, (_, display_name, wife_name) in enumerate(items, 1))
    return '\n'.join(lines)


async def _send_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
) -> None:
    try:
        image = await _download_image(image_url)
    except RuntimeError as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 下载画廊图片失败: {exc}')
        await bot.send(str(exc))
        return

    messages: list[Any] = []
    if user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await bot.send(messages if len(messages) > 1 else messages[0])


async def _send_daily_wife(bot: Bot, ev: Event):
    candidates, error = await _load_candidates()
    if error or not candidates:
        return await bot.send(error or '没有找到可用角色。')

    rng = _event_rng(ev)
    if bool(_cfg('DailyWifeMasterUnlimited')) and _is_master(ev):
        role = rng.choice(candidates)
        image = rng.choice(role.images)
        record = WifeRecord.from_role(role, image)
    else:
        record = await _ensure_daily_wife_record(ev)
        if record is None:
            return await bot.send('没有找到可用角色。')
        role = record.to_role()
        image = record.image

    _save_daily_wife_record(ev, record)

    logger.info(
        f'[gs_wuwa_daily_wife] user={ev.user_id} group={ev.group_id or "direct"} '
        f'role={role.name} ids={role.role_ids} image={image}'
    )

    text = _build_text(role) if bool(_cfg('DailyWifeSendText')) else None
    await _send_role_image(bot, role, image, text, ev.user_id)


async def _send_rob_wife(bot: Bot, ev: Event):
    if not _cfg_bool('DailyWifeRobEnabled', True):
        return await bot.send('抢老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await bot.send('要抢谁的老婆？请艾特对方或在命令后面写对方 QQ。')

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await bot.send('自己抢自己的老婆也太奇怪了吧！')

    target_record = _get_existing_daily_wife_record(ev, target_user_id)
    if target_record is None:
        return await bot.send('对方今天还没有在当前群发送今日老婆，暂时抢不到他的老婆。')

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    attempts = context.setdefault('rob_attempts', {})
    is_master = _is_master(ev)
    if not is_master and attempts.get(robber_id):
        return await bot.send('今天已经抢过老婆啦，明天再来吧！')

    if not is_master:
        attempts[robber_id] = True

    if random.random() >= _rob_success_rate():
        _save_wife_data(data)
        return await bot.send('抢老婆失败了，还被对方痛扁了一顿！🤣')

    context['wives'][robber_id] = _record_to_dict(target_record, ev, robber_id)
    context['wives'][robber_id]['stolen_from'] = target_user_id
    _save_wife_data(data)

    role = target_record.to_role()
    text = _build_rob_success_text(role, target_user_id)
    await _send_role_image(bot, role, target_record.image, text, robber_id)


async def _send_wife_list(bot: Bot, ev: Event):
    await bot.send(await _wife_list_text(ev))


@sv.on_fullmatch('今日老婆', block=True)
async def daily_wife(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev)


@sv.on_fullmatch(('老婆列表', '今日老婆列表'), block=True)
async def daily_wife_list(bot: Bot, ev: Event):
    await _send_wife_list(bot, ev)


@sv.on_prefix(('抢老婆', '抢今日老婆'), block=True)
async def rob_wife(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@sv.on_fullmatch(('抢老婆', '抢今日老婆'), block=True)
async def rob_wife_at(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)
