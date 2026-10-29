"""长期摘要：OR 触发、失败退避重试与合并保留旧要点。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import MemoryRecord, MemoryStore
from wechat_agent.memory.summary import SummaryEngine

CONTACT = "wxid_alice"
OLD_SUMMARY = "偏好：用户喜欢美式咖啡。约定：周五下午三点见面。"
MERGED_SUMMARY = "人物关系：无。偏好：用户喜欢美式咖啡。约定：周五下午三点见面。待办：明天去医院。"


class FakeClock:
    """从指定时刻起每次调用前进 1 秒的时钟，支持手动推进。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start if start is not None else datetime(2026, 10, 29, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        moment = self.now
        self.now += timedelta(seconds=1)
        return moment

    def advance(self, *, minutes: float = 0, seconds: float = 0) -> None:
        self.now += timedelta(minutes=minutes, seconds=seconds)


class FakeClient:
    """记录 prompt 并按序返回结果或抛出异常。"""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "data")


@pytest.fixture
def memory(store: MemoryStore, clock: FakeClock) -> ShortTermMemory:
    return ShortTermMemory(store, clock=clock)


def make_engine(
    store: MemoryStore,
    memory: ShortTermMemory,
    client: FakeClient,
    clock: FakeClock,
) -> SummaryEngine:
    return SummaryEngine(store=store, short_term=memory, client=client, clock=clock)


async def test_count_threshold_triggers_without_idle(
    store: MemoryStore,
    memory: ShortTermMemory,
    clock: FakeClock,
) -> None:
    for index in range(1, 11):
        memory.append_user(CONTACT, f"message-{index}")
    clock.advance(minutes=1)
    client = FakeClient("人物关系：无。偏好：无。约定：无。待办：无。")
    engine = make_engine(store, memory, client, clock)

    assert await engine.maybe_summarize(CONTACT) is True

    assert len(client.calls) == 1
    system, user = client.calls[0]
    for category in ("人物关系", "偏好", "约定", "待办"):
        assert category in system
    for index in range(1, 11):
        assert f"message-{index}" in user
    record = store.load(CONTACT)
    assert record.summary == "人物关系：无。偏好：无。约定：无。待办：无。"
    assert record.summary_updated_at
    assert datetime.fromisoformat(record.summary_updated_at).utcoffset() == timedelta(0)
    assert record.summarized_upto == 10
    assert record.pending_since == ""


async def test_idle_threshold_triggers_without_count(
    store: MemoryStore,
    memory: ShortTermMemory,
    clock: FakeClock,
) -> None:
    for index in range(1, 4):
        memory.append_user(CONTACT, f"idle-{index}")
    client = FakeClient("人物关系：无。偏好：无。约定：无。待办：无。")
    engine = make_engine(store, memory, client, clock)

    assert await engine.maybe_summarize(CONTACT) is False
    assert client.calls == []

    clock.advance(minutes=11)

    assert await engine.maybe_summarize(CONTACT) is True
    assert len(client.calls) == 1
    for index in range(1, 4):
        assert f"idle-{index}" in client.calls[0][1]


async def test_failure_keeps_pending_and_short_term_then_retries(
    store: MemoryStore,
    memory: ShortTermMemory,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for index in range(1, 11):
        memory.append_user(CONTACT, f"message-{index}")
    before = memory.entries(CONTACT)
    client = FakeClient(RuntimeError("boom"), MERGED_SUMMARY)
    engine = make_engine(store, memory, client, clock)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert await engine.maybe_summarize(CONTACT) is False

    assert memory.entries(CONTACT) == before
    record = store.load(CONTACT)
    assert record.summary == ""
    assert record.summary_updated_at == ""
    assert record.summarized_upto == 0
    assert record.pending_since
    assert "长期摘要失败" in caplog.text
    assert "message-1" not in caplog.text
    assert "wxid" not in caplog.text

    assert await engine.maybe_summarize(CONTACT) is False
    assert len(client.calls) == 1

    clock.advance(seconds=61)

    assert await engine.maybe_summarize(CONTACT) is True
    assert len(client.calls) == 2
    record = store.load(CONTACT)
    assert record.summary == MERGED_SUMMARY
    assert record.summary_updated_at
    assert record.summarized_upto == 10
    assert record.pending_since == ""


async def test_merge_keeps_old_summary_facts_in_prompt_and_result(
    store: MemoryStore,
    memory: ShortTermMemory,
    clock: FakeClock,
) -> None:
    store.save(
        CONTACT,
        MemoryRecord(
            summary=OLD_SUMMARY,
            summary_updated_at="2026-10-29T08:00:00+00:00",
            summarized_upto=5,
        ),
    )
    for index in range(1, 11):
        memory.append_user(CONTACT, f"new-{index}")
    client = FakeClient(MERGED_SUMMARY)
    engine = make_engine(store, memory, client, clock)

    assert await engine.maybe_summarize(CONTACT) is True

    system, user = client.calls[0]
    for category in ("人物关系", "偏好", "约定", "待办"):
        assert category in system
    assert OLD_SUMMARY in user
    for index in range(1, 11):
        assert f"new-{index}" in user

    record = store.load(CONTACT)
    assert record.summary == MERGED_SUMMARY
    assert "美式咖啡" in record.summary
    assert "周五下午三点" in record.summary
    assert record.summarized_upto == 15
    assert record.summary_updated_at
    assert record.pending_since == ""
