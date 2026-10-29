"""长期摘要：新增条数或空闲时长触发，失败保留 pending 并退避重试。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import MemoryEntry, MemoryRecord, MemoryStore
from wechat_agent.wechat.sender import contact_hash

DEFAULT_TRIGGER_COUNT: Final = 10
DEFAULT_IDLE_MINUTES: Final = 10.0
DEFAULT_RETRY_BACKOFF_SECONDS: Final = 60.0

SUMMARY_SYSTEM_PROMPT: Final = (
    "你是长期记忆整理助手。请把「旧摘要」与「新增对话」合并成一份新的长期摘要。\n"
    "输出要求：\n"
    "1. 只输出摘要正文，不要解释，不要使用 Markdown 代码块；\n"
    "2. 按「人物关系」「偏好」「约定」「待办」四类结构化要点组织，没有内容的类别写「无」；\n"
    "3. 保留旧摘要中的关键事实，不要编造对话中不存在的信息；\n"
    "4. 控制在 300 字以内。"
)

_ROLE_LABELS: Final = {"user": "用户", "assistant": "助手"}

logger = logging.getLogger(LOGGER_NAME)


class SummaryClient(Protocol):
    """摘要所需的 LLM 端口；真实客户端在组合层注入。"""

    async def complete(self, *, system: str, user: str) -> str: ...


class SummaryEngine:
    """按联系人判断摘要触发条件；失败只落 pending，不向调用方抛异常。"""

    def __init__(
        self,
        *,
        store: MemoryStore,
        short_term: ShortTermMemory,
        client: SummaryClient,
        trigger_count: int = DEFAULT_TRIGGER_COUNT,
        idle_minutes: float = DEFAULT_IDLE_MINUTES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if trigger_count <= 0:
            raise ValueError("trigger_count 必须大于 0")
        if idle_minutes < 0:
            raise ValueError("idle_minutes 不能为负数")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds 不能为负数")
        self._store = store
        self._short_term = short_term
        self._client = client
        self._trigger_count = trigger_count
        self._idle = timedelta(minutes=idle_minutes)
        self._backoff = timedelta(seconds=retry_backoff_seconds)
        self._clock = clock if clock is not None else _utc_now

    async def maybe_summarize(self, contact_id: str) -> bool:
        """满足任一触发条件时调用 LLM；成功 True，未触发/失败/退避 False。"""
        record = self._store.load(contact_id)
        entries = self._short_term.entries(contact_id)
        pending = _pending_entries(entries, record.summary_updated_at)
        now = self._clock().astimezone(UTC)
        if not self._is_due(record, entries, pending, now):
            return False

        try:
            text = await self._client.complete(
                system=SUMMARY_SYSTEM_PROMPT,
                user=_build_user_prompt(record.summary, pending),
            )
            summary = text.strip()
            if not summary:
                raise ValueError("模型返回空摘要")
        except Exception as exc:
            self._record_failure(contact_id, now, exc)
            return False

        latest = self._store.load(contact_id)
        latest.summary = summary
        latest.summary_updated_at = now.isoformat()
        latest.summarized_upto += len(pending)
        latest.pending_since = ""
        try:
            self._store.save(contact_id, latest)
        except OSError as exc:
            logger.warning(
                "长期摘要落盘失败，等待下次重试",
                extra={
                    "contact_hash": contact_hash(contact_id),
                    "error_type": type(exc).__name__,
                },
            )
            return False
        logger.info(
            "长期摘要已更新",
            extra={
                "contact_hash": contact_hash(contact_id),
                "summarized_entries": len(pending),
            },
        )
        return True

    def _is_due(
        self,
        record: MemoryRecord,
        entries: Sequence[MemoryEntry],
        pending: Sequence[MemoryEntry],
        now: datetime,
    ) -> bool:
        if not pending:
            return False
        pending_since = _parse_timestamp(record.pending_since)
        if pending_since is not None and now - pending_since < self._backoff:
            return False
        if len(pending) >= self._trigger_count:
            return True
        last = _parse_timestamp(entries[-1].ts) if entries else None
        return last is not None and now - last >= self._idle

    def _record_failure(self, contact_id: str, now: datetime, error: Exception) -> None:
        latest = self._store.load(contact_id)
        if not latest.pending_since:
            latest.pending_since = now.isoformat()
            try:
                self._store.save(contact_id, latest)
            except OSError as save_error:
                logger.warning(
                    "pending_since 落盘失败",
                    extra={
                        "contact_hash": contact_hash(contact_id),
                        "error_type": type(save_error).__name__,
                    },
                )
        logger.warning(
            "长期摘要失败，保留 pending 等待重试",
            extra={
                "contact_hash": contact_hash(contact_id),
                "error_type": type(error).__name__,
                "reason": "summary_failed",
            },
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _pending_entries(entries: Sequence[MemoryEntry], boundary_text: str) -> list[MemoryEntry]:
    """以 summary_updated_at 为边界取新增条目；窗口淘汰旧记录不影响计数。"""
    boundary = _parse_timestamp(boundary_text)
    if boundary is None:
        return list(entries)
    pending: list[MemoryEntry] = []
    for entry in entries:
        moment = _parse_timestamp(entry.ts)
        if moment is None or moment > boundary:
            pending.append(entry)
    return pending


def _build_user_prompt(summary: str, pending: Sequence[MemoryEntry]) -> str:
    old_summary = summary.strip() or "（暂无）"
    dialogue = "\n".join(
        f"{_ROLE_LABELS.get(entry.role, entry.role)}：{entry.content}" for entry in pending
    )
    return f"【旧摘要】\n{old_summary}\n\n【新增对话】\n{dialogue}"


def _parse_timestamp(text: str) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
