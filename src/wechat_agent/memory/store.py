"""对话记忆存储：每联系人一个 JSON 文件，原子写复用网关层的临时文件 + os.replace。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.cursor import atomic_write_text, ensure_private_dir
from wechat_agent.wechat.sender import contact_hash

MEMORY_DIR_NAME: Final = "memory"
MEMORY_FILE_SUFFIX: Final = ".json"
CORRUPT_SUFFIX: Final = ".corrupt"

logger = logging.getLogger(LOGGER_NAME)


@dataclass(slots=True)
class MemoryEntry:
    """短期窗口中的一条对话记录。"""

    role: str
    content: str
    ts: str


@dataclass(slots=True)
class MemoryRecord:
    """单个联系人的全部记忆；字段与落盘 JSON 一一对应。"""

    short_term: list[MemoryEntry] = field(default_factory=list)
    summary: str = ""
    summary_updated_at: str = ""
    summarized_upto: int = 0
    pending_since: str = ""


class MemoryStore:
    """按联系人哈希分文件的记忆存储；损坏文件隔离后以空记忆启动。"""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._directory = Path(data_dir) / MEMORY_DIR_NAME

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, contact_id: str) -> Path:
        """文件名只用联系人哈希，原始 wxid 不落盘。"""
        return self._directory / f"{contact_hash(contact_id)}{MEMORY_FILE_SUFFIX}"

    def load(self, contact_id: str) -> MemoryRecord:
        """读取记忆；文件缺失、损坏或内容非法都返回空记忆，不抛异常。"""
        path = self.path_for(contact_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return MemoryRecord()
        except UnicodeDecodeError:
            self._quarantine(path, contact_id, "invalid_encoding")
            return MemoryRecord()
        except OSError as error:
            logger.warning(
                "读取记忆文件失败，按空记忆处理",
                extra={"contact_hash": contact_hash(contact_id), "reason": type(error).__name__},
            )
            return MemoryRecord()

        try:
            payload = json.loads(text)
        except ValueError:
            self._quarantine(path, contact_id, "invalid_json")
            return MemoryRecord()

        record = _record_from_payload(payload)
        if record is None:
            self._quarantine(path, contact_id, "invalid_structure")
            return MemoryRecord()
        return record

    def save(self, contact_id: str, record: MemoryRecord) -> None:
        """原子写：写坏或中途崩溃都不会破坏上一份完整记忆。"""
        ensure_private_dir(self._directory)
        atomic_write_text(
            self.path_for(contact_id),
            json.dumps(_payload_from_record(record), ensure_ascii=False),
        )

    def _quarantine(self, path: Path, contact_id: str, reason: str) -> None:
        """把损坏文件改名为 `<file>.corrupt`，保留现场供排查。"""
        try:
            os.replace(path, path.with_name(f"{path.name}{CORRUPT_SUFFIX}"))
        except OSError as error:
            logger.warning(
                "损坏记忆文件隔离失败，按空记忆处理",
                extra={"contact_hash": contact_hash(contact_id), "reason": type(error).__name__},
            )
            return
        logger.warning(
            "记忆文件损坏，已隔离并以空记忆启动",
            extra={"contact_hash": contact_hash(contact_id), "reason": reason},
        )


def _payload_from_record(record: MemoryRecord) -> dict[str, object]:
    return {
        "short_term": [
            {"role": entry.role, "content": entry.content, "ts": entry.ts}
            for entry in record.short_term
        ],
        "summary": record.summary,
        "summary_updated_at": record.summary_updated_at,
        "summarized_upto": record.summarized_upto,
        "pending_since": record.pending_since,
    }


def _record_from_payload(payload: object) -> MemoryRecord | None:
    """结构不合法返回 None（交给调用方隔离）；缺省字段按空值补齐。"""
    if not isinstance(payload, dict):
        return None
    raw_entries = payload.get("short_term", [])
    if not isinstance(raw_entries, list):
        return None
    entries: list[MemoryEntry] = []
    for raw_entry in raw_entries:
        entry = _entry_from_payload(raw_entry)
        if entry is None:
            return None
        entries.append(entry)

    summary = payload.get("summary", "")
    summary_updated_at = payload.get("summary_updated_at", "")
    summarized_upto = payload.get("summarized_upto", 0)
    pending_since = payload.get("pending_since", "")
    if not isinstance(summary, str):
        return None
    if not isinstance(summary_updated_at, str) or not isinstance(pending_since, str):
        return None
    if isinstance(summarized_upto, bool) or not isinstance(summarized_upto, int):
        return None
    return MemoryRecord(
        short_term=entries,
        summary=summary,
        summary_updated_at=summary_updated_at,
        summarized_upto=summarized_upto,
        pending_since=pending_since,
    )


def _entry_from_payload(payload: object) -> MemoryEntry | None:
    if not isinstance(payload, dict):
        return None
    role = payload.get("role")
    content = payload.get("content")
    ts = payload.get("ts")
    if not isinstance(role, str) or not isinstance(content, str) or not isinstance(ts, str):
        return None
    return MemoryEntry(role=role, content=content, ts=ts)
