"""按联系人清空记忆：删除主文件与损坏备份，并丢弃内存中的短期窗口。"""

from __future__ import annotations

import logging
from pathlib import Path

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import CORRUPT_SUFFIX, MemoryStore
from wechat_agent.wechat.sender import contact_hash

logger = logging.getLogger(LOGGER_NAME)


def clear_memory(
    contact_id: str,
    *,
    store: MemoryStore,
    short_term: ShortTermMemory,
) -> bool:
    """清空单个联系人的记忆；删除任意文件时返回 True。

    主文件删除后摘要与落盘短期记录一并消失；``short_term`` 的内存窗口通过
    ``forget`` 失效，避免下一次追加把旧记录重新写回磁盘。日志只含联系人
    哈希与删除结果，不读取也不记录记忆正文。
    """
    path = store.path_for(contact_id)
    removed = _remove_file(path)
    removed = _remove_file(_corrupt_path(path)) or removed
    short_term.forget(contact_id)
    logger.info(
        "已清空联系人记忆",
        extra={
            "contact_hash": contact_hash(contact_id),
            "file_removed": removed,
        },
    )
    return removed


def _corrupt_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{CORRUPT_SUFFIX}")


def _remove_file(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
