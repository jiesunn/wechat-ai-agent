"""上下文注入：拼装 system、短期历史与当前消息，超预算时由旧到新裁剪。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, TypedDict, cast

from wechat_agent.memory.store import MemoryEntry

DEFAULT_MAX_CHARS: Final = 8000
SUMMARY_HEADER: Final = "以下是你记得的长期信息："

Role = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: Role
    content: str


def build_messages(
    *,
    persona: str,
    summary: str,
    history: Sequence[MemoryEntry],
    user_text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[ChatMessage]:
    """system 在首位、历史按给定时间序、当前 user 在末位；只裁剪历史。

    ``history`` 应为当前消息之前的短期记录；``max_chars`` 按各消息 content 的
    ``len`` 求和，system 与当前 user 不计入裁剪。
    """
    if max_chars < 0:
        raise ValueError("max_chars 不能为负数")
    system_content = _build_system_content(persona, summary)
    remaining = max(max_chars - len(system_content) - len(user_text), 0)
    messages: list[ChatMessage] = [{"role": "system", "content": system_content}]
    messages.extend(
        {"role": cast(Role, entry.role), "content": entry.content}
        for entry in _take_recent(history, remaining)
    )
    messages.append({"role": "user", "content": user_text})
    return messages


def _build_system_content(persona: str, summary: str) -> str:
    normalized_persona = persona.strip()
    normalized_summary = summary.strip()
    if not normalized_summary:
        return normalized_persona
    return f"{normalized_persona}\n\n{SUMMARY_HEADER}\n{normalized_summary}"


def _take_recent(history: Sequence[MemoryEntry], budget: int) -> list[MemoryEntry]:
    """从最新往回取，直到下一条放不下；返回结果仍按时间序。"""
    if budget <= 0:
        return []
    kept: list[MemoryEntry] = []
    used = 0
    for entry in reversed(history):
        length = len(entry.content)
        if used + length > budget:
            break
        kept.append(entry)
        used += length
    kept.reverse()
    return kept
