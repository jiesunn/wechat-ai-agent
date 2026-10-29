"""准入规则：形式校验、长度截断与重复抑制。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DuplicateSuppressor",
    "has_meaningful_content",
    "truncate_text",
]

DEFAULT_MAX_CHARS = 2000


def has_meaningful_content(text: str) -> bool:
    """去掉空白后至少有一个字母、数字或汉字；纯表情与纯标点返回 False。

    Python 的 ``str.isalnum()`` 对汉字返回 True，因此无需额外判断 CJK 区间；
    全角标点（如 ``，。！？``）返回 False，不会因 ``\\w`` 匹配问题被误判为有内容。
    """
    return any(char.isalnum() for char in text if not char.isspace())


def truncate_text(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, bool]:
    """超过上限时截断并返回 ``(截断后文本, True)``；未超限原样返回并标记 False。"""
    if max_chars < 0:
        raise ValueError("max_chars 不能为负数")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _normalize_text(text: str) -> str:
    """strip 并把连续空白（含换行、制表符）压成一个空格。"""
    return " ".join(text.split())


class DuplicateSuppressor:
    """按 ``(from_user_id, 归一化文本 hash)`` 做滑动窗口去重。

    窗口从最近一次放行的时刻起算；窗口内的重复命中不延长窗口，
    因此同一文本在放行 30 秒后必然能再次通过。
    """

    __slots__ = ("_clock", "_seen", "_window_seconds")

    def __init__(
        self,
        *,
        window_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("window_seconds 不能为负数")
        self._window_seconds = window_seconds
        self._clock = clock
        self._seen: dict[tuple[str, str], float] = {}

    def is_duplicate(self, contact_id: str, text: str) -> bool:
        """窗口内已出现过同一文本时返回 True，且不刷新窗口起点。"""
        now = self._clock()
        self._prune(now)
        digest = hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()
        key = (contact_id, digest)
        last_seen = self._seen.get(key)
        if last_seen is not None and now - last_seen < self._window_seconds:
            return True
        self._seen[key] = now
        return False

    def _prune(self, now: float) -> None:
        """清理窗口外的记录，避免进程长期运行时字典无限增长。"""
        expired = [
            key for key, seen_at in self._seen.items() if now - seen_at >= self._window_seconds
        ]
        for key in expired:
            del self._seen[key]
