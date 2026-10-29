"""游标持久化：原子写 data/cursor.json，进程重启后从上次位置继续。"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Final

CURSOR_FILE_NAME: Final = "cursor.json"
CURSOR_FILE_MODE: Final = 0o600
PRIVATE_DIR_MODE: Final = 0o700
CURSOR_TEMP_PREFIX: Final = ".cursor."


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
        """临时文件 + fsync + os.replace；写坏也不会破坏上一份游标。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir.chmod(PRIVATE_DIR_MODE)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=CURSOR_TEMP_PREFIX,
            suffix=".tmp",
            dir=self._data_dir,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"cursor": cursor}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, CURSOR_FILE_MODE)
            os.replace(temp_name, self._path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise
