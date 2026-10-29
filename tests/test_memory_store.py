"""记忆存储：正常读写、写入中断、损坏隔离与权限位。"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.memory.store import MemoryEntry, MemoryRecord, MemoryStore
from wechat_agent.wechat.sender import contact_hash

CONTACT = "wxid_alice"


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "data")


def make_record(*, summary: str = "用户偏好美式咖啡。") -> MemoryRecord:
    return MemoryRecord(
        short_term=[
            MemoryEntry(role="user", content="早上好", ts="2026-10-29T09:00:00+00:00"),
            MemoryEntry(
                role="assistant",
                content="早上好，今天想喝点什么？",
                ts="2026-10-29T09:00:02+00:00",
            ),
        ],
        summary=summary,
        summary_updated_at="2026-10-29T09:05:00+00:00",
        summarized_upto=2,
        pending_since="2026-10-29T09:06:00+00:00",
    )


def test_save_and_load_roundtrip(store: MemoryStore) -> None:
    record = make_record()

    store.save(CONTACT, record)

    path = store.path_for(CONTACT)
    assert path == store.directory / f"{contact_hash(CONTACT)}.json"
    assert "wxid" not in path.name
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "short_term": [
            {"role": "user", "content": "早上好", "ts": "2026-10-29T09:00:00+00:00"},
            {
                "role": "assistant",
                "content": "早上好，今天想喝点什么？",
                "ts": "2026-10-29T09:00:02+00:00",
            },
        ],
        "summary": "用户偏好美式咖啡。",
        "summary_updated_at": "2026-10-29T09:05:00+00:00",
        "summarized_upto": 2,
        "pending_since": "2026-10-29T09:06:00+00:00",
    }
    assert store.load(CONTACT) == record


def test_missing_file_starts_with_empty_memory(store: MemoryStore) -> None:
    assert store.load(CONTACT) == MemoryRecord()


def test_contacts_use_separate_files(store: MemoryStore) -> None:
    alice = make_record(summary="爱丽丝的摘要")
    bob = make_record(summary="鲍勃的摘要")

    store.save("wxid_alice", alice)
    store.save("wxid_bob", bob)

    assert store.path_for("wxid_alice") != store.path_for("wxid_bob")
    assert store.load("wxid_alice") == alice
    assert store.load("wxid_bob") == bob


def test_interrupted_write_keeps_previous_file(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = make_record(summary="上一版完整摘要")
    store.save(CONTACT, original)
    path = store.path_for(CONTACT)
    before = path.read_bytes()

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("模拟写入中断")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        store.save(CONTACT, make_record(summary="新摘要"))

    assert path.read_bytes() == before
    assert store.load(CONTACT) == original
    assert [entry.name for entry in store.directory.iterdir()] == [path.name]


@pytest.mark.parametrize(
    ("raw_text", "expected_reason"),
    [
        ('{"short_term": [', "invalid_json"),
        ('{"short_term": "not-a-list"}', "invalid_structure"),
    ],
)
def test_corrupt_file_is_quarantined_and_starts_empty(
    store: MemoryStore,
    caplog: pytest.LogCaptureFixture,
    raw_text: str,
    expected_reason: str,
) -> None:
    store.directory.mkdir(parents=True)
    path = store.path_for(CONTACT)
    path.write_text(raw_text, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        record = store.load(CONTACT)

    assert record == MemoryRecord()
    corrupt = path.with_name(f"{path.name}.corrupt")
    assert not path.exists()
    assert corrupt.read_text(encoding="utf-8") == raw_text
    reasons = [getattr(entry, "reason", None) for entry in caplog.records]
    assert reasons == [expected_reason]
    assert "wxid" not in caplog.text
    assert raw_text not in caplog.text


def test_invalid_encoding_is_quarantined(store: MemoryStore) -> None:
    store.directory.mkdir(parents=True)
    path = store.path_for(CONTACT)
    path.write_bytes(b"\xff\xfe\x00")

    assert store.load(CONTACT) == MemoryRecord()

    corrupt = path.with_name(f"{path.name}.corrupt")
    assert not path.exists()
    assert corrupt.read_bytes() == b"\xff\xfe\x00"


def test_quarantine_replaces_previous_corrupt_copy(store: MemoryStore) -> None:
    store.directory.mkdir(parents=True)
    path = store.path_for(CONTACT)
    corrupt = path.with_name(f"{path.name}.corrupt")
    corrupt.write_text("旧现场", encoding="utf-8")
    path.write_text("{半截", encoding="utf-8")

    assert store.load(CONTACT) == MemoryRecord()
    assert corrupt.read_text(encoding="utf-8") == "{半截"


def test_directory_and_file_modes_are_tightened(store: MemoryStore) -> None:
    store.directory.mkdir(parents=True)
    os.chmod(store.directory, 0o755)

    store.save(CONTACT, make_record())

    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path_for(CONTACT).stat().st_mode) == 0o600
