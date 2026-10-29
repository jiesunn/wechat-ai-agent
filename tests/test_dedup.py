"""去重：重复只处理一次、容量淘汰、LRU 语义与重启恢复。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from wechat_agent.wechat.dedup import SeenIdStore
from wechat_agent.wechat.models import NormalizedMessage


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _message(msg_id: str) -> NormalizedMessage:
    return NormalizedMessage(msg_id=msg_id, from_user_id="wxid_alice")


def test_duplicate_message_id_is_reported_once(data_dir: Path) -> None:
    store = SeenIdStore(data_dir, capacity=10)
    message = _message("msg-1")

    assert store.is_duplicate(message.msg_id) is False
    assert store.is_duplicate(message.msg_id) is True
    assert store.is_duplicate(message.msg_id) is True
    assert len(store) == 1


def test_capacity_evicts_oldest(data_dir: Path) -> None:
    store = SeenIdStore(data_dir, capacity=3)
    for msg_id in ("a", "b", "c"):
        assert store.is_duplicate(msg_id) is False

    assert store.is_duplicate("d") is False

    assert len(store) == 3
    assert store.contains("a") is False
    assert store.contains("b") is True
    assert store.contains("d") is True


def test_lru_access_moves_id_to_tail(data_dir: Path) -> None:
    store = SeenIdStore(data_dir, capacity=3)
    for msg_id in ("a", "b", "c"):
        store.is_duplicate(msg_id)

    assert store.is_duplicate("a") is True
    store.is_duplicate("d")

    assert store.contains("a") is True
    assert store.contains("b") is False
    assert store.contains("c") is True


def test_persistence_survives_restart(data_dir: Path) -> None:
    store = SeenIdStore(data_dir, capacity=10)
    assert store.is_duplicate("msg-1") is False
    assert store.is_duplicate("msg-2") is False

    path = store.path
    assert path == data_dir / "seen_ids.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"seen_ids": ["msg-1", "msg-2"]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert list(data_dir.glob(".seen_ids.json.*")) == []

    restarted = SeenIdStore(data_dir, capacity=10)
    assert restarted.is_duplicate("msg-1") is True
    assert restarted.is_duplicate("msg-2") is True
    assert restarted.is_duplicate("msg-3") is False


def test_persistence_keeps_only_most_recent_ids(data_dir: Path) -> None:
    store = SeenIdStore(data_dir, capacity=10, persist_limit=3)
    for msg_id in ("a", "b", "c", "d", "e"):
        store.is_duplicate(msg_id)

    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert payload["seen_ids"] == ["c", "d", "e"]


def test_corrupted_file_is_treated_as_empty(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "seen_ids.json").write_text("{broken json", encoding="utf-8")

    store = SeenIdStore(data_dir)

    assert len(store) == 0
    assert store.is_duplicate("msg-1") is False
