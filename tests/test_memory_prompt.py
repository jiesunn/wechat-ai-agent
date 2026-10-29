"""上下文注入：消息顺序、字符预算与由旧到新裁剪。"""

from __future__ import annotations

from typing import Any

from wechat_agent.memory.prompt import DEFAULT_MAX_CHARS, build_messages
from wechat_agent.memory.store import MemoryEntry

PERSONA = "你是一个简洁、友好的中文助手。"
SUMMARY = "偏好：用户喜欢美式咖啡。约定：周五下午三点见面。"
CURRENT = "今天晚上吃什么？"


def make_entry(index: int, *, size: int = 10, role: str = "user") -> MemoryEntry:
    prefix = f"h{index:02d}-"
    return MemoryEntry(
        role=role,
        content=prefix + "x" * max(size - len(prefix), 0),
        ts=f"2026-10-29T09:{index:02d}:00+00:00",
    )


def total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(message["content"]) for message in messages)


def test_under_budget_keeps_all_in_time_order() -> None:
    history = [make_entry(1), make_entry(2, role="assistant"), make_entry(3)]

    messages = build_messages(
        persona=PERSONA,
        summary=SUMMARY,
        history=history,
        user_text=CURRENT,
    )

    assert messages[0]["role"] == "system"
    assert PERSONA in messages[0]["content"]
    assert SUMMARY in messages[0]["content"]
    assert [message["content"] for message in messages[1:-1]] == [
        entry.content for entry in history
    ]
    assert [message["role"] for message in messages[1:-1]] == ["user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": CURRENT}
    assert total_chars(messages) <= DEFAULT_MAX_CHARS


def test_over_budget_drops_oldest_history_first() -> None:
    history = [make_entry(index, size=500) for index in range(1, 21)]

    messages = build_messages(
        persona=PERSONA,
        summary=SUMMARY,
        history=history,
        user_text=CURRENT,
    )

    system_chars = len(messages[0]["content"])
    remaining = DEFAULT_MAX_CHARS - system_chars - len(CURRENT)
    expected_kept = remaining // 500
    assert 0 < expected_kept < len(history)
    assert [message["content"] for message in messages[1:-1]] == [
        entry.content for entry in history[-expected_kept:]
    ]
    assert not any(message["content"].startswith("h01-") for message in messages)
    assert messages[-2]["content"].startswith("h20-")
    assert messages[-1]["content"] == CURRENT
    assert total_chars(messages) <= DEFAULT_MAX_CHARS


def test_extreme_budget_keeps_only_system_and_current() -> None:
    history = [make_entry(index, size=100) for index in range(1, 6)]

    messages = build_messages(
        persona=PERSONA,
        summary=SUMMARY,
        history=history,
        user_text=CURRENT,
        max_chars=1,
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert SUMMARY in messages[0]["content"]
    assert messages[-1]["content"] == CURRENT
    assert total_chars(messages) > 1


def test_trimmed_history_stays_chronological() -> None:
    history = [
        make_entry(1, size=10),
        make_entry(2, size=3000),
        make_entry(3, size=10),
        make_entry(4, size=10),
    ]
    unbounded = build_messages(
        persona=PERSONA,
        summary=SUMMARY,
        history=history,
        user_text=CURRENT,
        max_chars=10**6,
    )
    base_chars = len(unbounded[0]["content"]) + len(CURRENT)

    messages = build_messages(
        persona=PERSONA,
        summary=SUMMARY,
        history=history,
        user_text=CURRENT,
        max_chars=base_chars + 20,
    )

    assert [message["content"] for message in messages[1:-1]] == [
        history[2].content,
        history[3].content,
    ]


def test_empty_summary_adds_no_placeholder_to_system() -> None:
    messages = build_messages(
        persona=PERSONA,
        summary="   ",
        history=[],
        user_text=CURRENT,
    )

    assert messages[0] == {"role": "system", "content": PERSONA}
    assert "摘要" not in messages[0]["content"]
