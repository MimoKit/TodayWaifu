from __future__ import annotations

from pathlib import Path


def scan_named_role_directories(
    root: Path,
    image_extensions: set[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """读取“角色名/图片”目录，并返回稳定排序后的角色图库。"""
    root.mkdir(parents=True, exist_ok=True)
    extensions = {suffix.casefold() for suffix in image_extensions}
    roles: list[tuple[str, tuple[str, ...]]] = []

    role_dirs = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith('.')
        ),
        key=lambda path: path.name.casefold(),
    )
    for role_dir in role_dirs:
        role_name = role_dir.name.strip()
        if not role_name:
            continue

        seen: set[str] = set()
        images: list[str] = []
        for path in sorted(role_dir.rglob('*'), key=lambda item: str(item).casefold()):
            if (
                not path.is_file()
                or path.name.startswith('.')
                or path.suffix.casefold() not in extensions
            ):
                continue
            resolved = str(path.resolve())
            key = resolved.casefold()
            if key not in seen:
                seen.add(key)
                images.append(resolved)

        if images:
            roles.append((role_name, tuple(images)))
    return tuple(roles)
