"""短期对话窗口：按联系人隔离的固定容量 deque，落盘复用 MemoryStore。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from wechat_agent.memory.store import MemoryEntry, MemoryStore
from wechat_agent.wechat.sender import contact_hash

DEFAULT_MAX_ENTRIES: Final = 20
ROLE_USER: Final = "user"
ROLE_ASSISTANT: Final = "assistant"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ShortTermMemory:
    """每个联系人一个 deque(maxlen=N)；窗口变化后立即原子落盘。"""

    def __init__(
        self,
        store: MemoryStore,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries 必须大于 0")
        self._store = store
        self._max_entries = max_entries
        self._clock = clock if clock is not None else utc_now
        self._windows: dict[str, deque[MemoryEntry]] = {}

    def entries(self, contact_id: str) -> tuple[MemoryEntry, ...]:
        """按时间顺序返回窗口快照；首次访问时从磁盘恢复。"""
        return tuple(self._window(contact_id))

    def forget(self, contact_id: str) -> None:
        """丢弃该联系人的内存窗口；下次访问从磁盘重新加载。"""
        self._windows.pop(contact_hash(contact_id), None)

    def append_user(self, contact_id: str, content: str) -> MemoryEntry:
        """收到用户消息时立即追加，后续生成或发送失败都不丢 user 记录。"""
        return self._append(contact_id, ROLE_USER, content)

    def append_assistant(
        self,
        contact_id: str,
        content: str,
        *,
        sent: bool,
    ) -> MemoryEntry | None:
        """只有发送成功才追加 assistant，避免下一轮误以为该回复已送达。"""
        if not sent:
            return None
        return self._append(contact_id, ROLE_ASSISTANT, content)

    def _window(self, contact_id: str) -> deque[MemoryEntry]:
        key = contact_hash(contact_id)
        window = self._windows.get(key)
        if window is None:
            record = self._store.load(contact_id)
            window = deque(record.short_term[-self._max_entries :], maxlen=self._max_entries)
            self._windows[key] = window
        return window

    def _append(self, contact_id: str, role: str, content: str) -> MemoryEntry:
        entry = MemoryEntry(role=role, content=content, ts=self._timestamp())
        window = self._window(contact_id)
        window.append(entry)
        self._persist(contact_id, window)
        return entry

    def _persist(self, contact_id: str, window: deque[MemoryEntry]) -> None:
        """读-改-写：只替换 short_term，保留摘要等字段。"""
        record = self._store.load(contact_id)
        record.short_term = list(window)
        self._store.save(contact_id, record)

    def _timestamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            raise ValueError("clock 必须返回带时区的 datetime")
        return moment.astimezone(UTC).isoformat()
