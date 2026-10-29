"""消息去重：内存 LRU + 原子持久化，作用于归一化后的 msg_id。"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Final

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.cursor import atomic_write_text, ensure_private_dir

SEEN_IDS_FILE_NAME: Final = "seen_ids.json"
DEFAULT_CAPACITY: Final = 1000
PERSIST_LIMIT: Final = 1000

logger = logging.getLogger(LOGGER_NAME)


class SeenIdStore:
    """首次见到的 id 记录并返回 False；再次见到返回 True 并刷新 LRU 位置。"""

    def __init__(
        self,
        data_dir: str | Path = "data",
        *,
        capacity: int = DEFAULT_CAPACITY,
        persist_limit: int = PERSIST_LIMIT,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须大于 0")
        if persist_limit <= 0:
            raise ValueError("persist_limit 必须大于 0")
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / SEEN_IDS_FILE_NAME
        self._capacity = capacity
        self._persist_limit = persist_limit
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._ids)

    def contains(self, message_id: str) -> bool:
        """只查询，不改变 LRU 顺序。"""
        return message_id in self._ids

    def is_duplicate(self, message_id: str) -> bool:
        """首次见到返回 False 并记录；重复命中把 id 移到队尾。"""
        if message_id in self._ids:
            self._ids.move_to_end(message_id)
            return True
        self._ids[message_id] = None
        while len(self._ids) > self._capacity:
            self._ids.popitem(last=False)
        self._save_quietly()
        return False

    def save(self) -> None:
        """按 LRU 顺序落盘，最多保留 persist_limit 条。"""
        ensure_private_dir(self._data_dir)
        recent = list(self._ids)[-self._persist_limit :]
        atomic_write_text(
            self._path,
            json.dumps({"seen_ids": recent}, ensure_ascii=False),
        )

    def _load(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        seen_ids = payload.get("seen_ids")
        if not isinstance(seen_ids, list):
            return
        for message_id in seen_ids[-self._capacity :]:
            if isinstance(message_id, str) and message_id:
                self._ids[message_id] = None

    def _save_quietly(self) -> None:
        try:
            self.save()
        except OSError as error:
            logger.warning(
                "去重记录落盘失败，重启后可能重复处理",
                extra={"reason": type(error).__name__},
            )
