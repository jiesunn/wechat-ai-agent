"""短期窗口：容量淘汰、发送失败不写 assistant、联系人隔离与时间戳。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from wechat_agent.memory.short_term import DEFAULT_MAX_ENTRIES, ShortTermMemory
from wechat_agent.memory.store import MemoryRecord, MemoryStore

CONTACT = "wxid_alice"


class FakeClock:
    """从指定时刻起每次调用前进 1 秒的时钟。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._moment = start if start is not None else datetime(2026, 10, 29, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self._moment
        self._moment += timedelta(seconds=1)
        return moment


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "data")


@pytest.fixture
def memory(store: MemoryStore) -> ShortTermMemory:
    return ShortTermMemory(store, clock=FakeClock())


def test_window_keeps_last_20_and_evicts_oldest(memory: ShortTermMemory) -> None:
    for index in range(1, 22):
        memory.append_user(CONTACT, f"message-{index}")

    entries = memory.entries(CONTACT)

    assert len(entries) == DEFAULT_MAX_ENTRIES == 20
    assert "message-1" not in [entry.content for entry in entries]
    assert entries[0].content == "message-2"
    assert entries[-1].content == "message-21"


def test_failed_send_does_not_append_assistant(
    memory: ShortTermMemory,
    store: MemoryStore,
) -> None:
    memory.append_user(CONTACT, "在吗")

    appended = memory.append_assistant(CONTACT, "在的", sent=False)

    assert appended is None
    assert [entry.role for entry in memory.entries(CONTACT)] == ["user"]
    assert [entry.role for entry in store.load(CONTACT).short_term] == ["user"]


def test_successful_send_appends_assistant_after_user(memory: ShortTermMemory) -> None:
    memory.append_user(CONTACT, "在吗")
    memory.append_assistant(CONTACT, "在的", sent=True)

    assert [(entry.role, entry.content) for entry in memory.entries(CONTACT)] == [
        ("user", "在吗"),
        ("assistant", "在的"),
    ]


def test_contacts_are_isolated(memory: ShortTermMemory) -> None:
    memory.append_user("wxid_alice", "A 的消息")
    memory.append_user("wxid_bob", "B 的消息")
    memory.append_assistant("wxid_alice", "回复 A", sent=True)

    assert [entry.content for entry in memory.entries("wxid_alice")] == ["A 的消息", "回复 A"]
    assert [entry.content for entry in memory.entries("wxid_bob")] == ["B 的消息"]


def test_timestamps_are_utc_aware_iso(store: MemoryStore) -> None:
    clock = FakeClock(datetime(2026, 10, 29, 17, 30, tzinfo=timezone(timedelta(hours=8))))
    memory = ShortTermMemory(store, clock=clock)

    first = memory.append_user(CONTACT, "第一条")
    second = memory.append_assistant(CONTACT, "第二条", sent=True)

    assert first.ts == "2026-10-29T09:30:00+00:00"
    assert second.ts == "2026-10-29T09:30:01+00:00"
    for entry in memory.entries(CONTACT):
        parsed = datetime.fromisoformat(entry.ts)
        assert parsed.utcoffset() == timedelta(0)


def test_entries_and_summary_survive_restart(store: MemoryStore) -> None:
    store.save(
        CONTACT,
        MemoryRecord(
            summary="用户喜欢美式。",
            summary_updated_at="2026-10-28T08:00:00+00:00",
            summarized_upto=4,
            pending_since="2026-10-28T08:05:00+00:00",
        ),
    )
    memory = ShortTermMemory(store, clock=FakeClock())
    memory.append_user(CONTACT, "重启前的消息")

    restarted = ShortTermMemory(
        store,
        clock=FakeClock(datetime(2026, 10, 30, 9, 0, tzinfo=UTC)),
    )

    assert [entry.content for entry in restarted.entries(CONTACT)] == ["重启前的消息"]
    record = store.load(CONTACT)
    assert record.summary == "用户喜欢美式。"
    assert record.summary_updated_at == "2026-10-28T08:00:00+00:00"
    assert record.summarized_upto == 4
    assert record.pending_since == "2026-10-28T08:05:00+00:00"
