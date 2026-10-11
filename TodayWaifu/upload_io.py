"""Bounded upload storage; readers only ever see complete image files."""
from __future__ import annotations

import os
import tempfile
from uuid import uuid4
from pathlib import Path

MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ROLE_NAME_LENGTH = 64


def save_upload_bytes(root: Path, prefix: str, suffix: str, data: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'{prefix}_{uuid4().hex}{suffix}'
    descriptor, temporary_name = tempfile.mkstemp(dir=root, prefix='.upload_', suffix='.tmp')
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
