"""记忆清空：按联系人删除文件、失效内存窗口与日志隐私。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.memory import clear_memory
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import MemoryRecord, MemoryStore
from wechat_agent.wechat.sender import contact_hash

CONTACT_A = "wxid_alice"
CONTACT_B = "wxid_bob"
SENSITIVE = "银行卡密码是123456"


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "data")


@pytest.fixture
def memory(store: MemoryStore) -> ShortTermMemory:
    return ShortTermMemory(store)


def test_clear_removes_only_target_contact(
    store: MemoryStore,
    memory: ShortTermMemory,
) -> None:
    memory.append_user(CONTACT_A, "A 的旧消息")
    memory.append_assistant(CONTACT_A, "A 的旧回复", sent=True)
    memory.append_user(CONTACT_B, "B 的消息")
    memory.append_assistant(CONTACT_B, "B 的回复", sent=True)
    record_a = store.load(CONTACT_A)
    record_a.summary = "A 的长期摘要"
    store.save(CONTACT_A, record_a)
    path_a = store.path_for(CONTACT_A)
    path_b = store.path_for(CONTACT_B)
    b_before = path_b.read_bytes()

    assert clear_memory(CONTACT_A, store=store, short_term=memory) is True

    assert not path_a.exists()
    assert path_b.exists()
    assert path_b.read_bytes() == b_before
    assert memory.entries(CONTACT_A) == ()
    assert [entry.content for entry in memory.entries(CONTACT_B)] == [
        "B 的消息",
        "B 的回复",
    ]
    assert store.load(CONTACT_A) == MemoryRecord()
    assert store.load(CONTACT_B).summary == ""

    memory.append_user(CONTACT_A, "清空后的新消息")
    assert [entry.content for entry in memory.entries(CONTACT_A)] == ["清空后的新消息"]
    assert [entry.content for entry in store.load(CONTACT_A).short_term] == ["清空后的新消息"]


def test_clear_removes_corrupt_backup_and_reports_file_state(
    store: MemoryStore,
    memory: ShortTermMemory,
) -> None:
    memory.append_user(CONTACT_A, "旧记忆")
    path = store.path_for(CONTACT_A)
    corrupt = path.with_name(f"{path.name}.corrupt")
    corrupt.write_text("{半截", encoding="utf-8")

    assert clear_memory(CONTACT_A, store=store, short_term=memory) is True
    assert not path.exists()
    assert not corrupt.exists()

    corrupt.write_text("只有损坏备份", encoding="utf-8")
    assert clear_memory(CONTACT_A, store=store, short_term=memory) is True
    assert not corrupt.exists()

    assert clear_memory(CONTACT_A, store=store, short_term=memory) is False


def test_clear_log_contains_no_memory_content(
    store: MemoryStore,
    memory: ShortTermMemory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    memory.append_user(CONTACT_A, SENSITIVE)
    memory.append_assistant(CONTACT_A, "好的", sent=True)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert clear_memory(CONTACT_A, store=store, short_term=memory) is True

    assert "已清空联系人记忆" in caplog.text
    assert SENSITIVE not in caplog.text
    assert CONTACT_A not in caplog.text
    records = [
        record for record in caplog.records if record.getMessage() == "已清空联系人记忆"
    ]
    assert len(records) == 1
    assert records[0].contact_hash == contact_hash(CONTACT_A)
    assert records[0].file_removed is True
