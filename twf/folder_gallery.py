from __future__ import annotations

from pathlib import Path


def scan_named_role_directories(
    root: Path,
    image_extensions: set[str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    root.mkdir(parents=True, exist_ok=True)
    roles: list[tuple[str, tuple[str, ...]]] = []
    role_dirs = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    )
    for role_dir in role_dirs:
        role_name = role_dir.name.strip()
        if not role_name:
            continue
        images = tuple(
            str(path)
            for path in sorted(
                (
                    path
                    for path in role_dir.rglob('*')
                    if path.is_file() and path.suffix.lower() in image_extensions
                ),
                key=lambda path: str(path).casefold(),
            )
        )
        if images:
            roles.append((role_name, images))
    return tuple(roles)
