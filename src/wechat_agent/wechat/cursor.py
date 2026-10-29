"""网关层私有文件：原子写工具（临时文件 + fsync + os.replace）与游标持久化。"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Final

PRIVATE_DIR_MODE: Final = 0o700
PRIVATE_FILE_MODE: Final = 0o600
PRIVATE_TEMP_SUFFIX: Final = ".tmp"
CURSOR_FILE_NAME: Final = "cursor.json"


def ensure_private_dir(directory: Path) -> None:
    """创建目录并收紧到 0700（mkdir 的 mode 受 umask 影响）。"""
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(PRIVATE_DIR_MODE)


def atomic_write_text(path: Path, text: str, *, mode: int = PRIVATE_FILE_MODE) -> None:
    """同目录临时文件 + fsync + os.replace；替换前旧文件始终完整。"""
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=PRIVATE_TEMP_SUFFIX,
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


class CursorStore:
    """只认一个字段 `cursor`；文件缺失、损坏或字段非法一律按无游标处理。"""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / CURSOR_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> str | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        cursor = payload.get("cursor")
        return cursor if isinstance(cursor, str) else None

    def save(self, cursor: str) -> None:
        """原子写；写坏也不会破坏上一份游标。"""
        ensure_private_dir(self._data_dir)
        atomic_write_text(self._path, json.dumps({"cursor": cursor}, ensure_ascii=False))
