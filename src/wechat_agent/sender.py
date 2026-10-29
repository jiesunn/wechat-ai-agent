"""发送端口与 DRY_RUN 实现：上层管线只依赖 MessageSender 接口。"""

from __future__ import annotations

import sys
from typing import Protocol, TextIO


class MessageSender(Protocol):
    """发送端口；真实 iLink 实现与 DryRunSender 都满足该协议。"""

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        """发送一条文本；成功返回 True。"""
        ...


class DryRunSender:
    """只打印拟发送内容、不触达网络，用于联调与首次启动验收。"""

    def __init__(self, *, stream: TextIO | None = None) -> None:
        self._stream = stream

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        target = self._stream if self._stream is not None else sys.stdout
        print(f"[DRY_RUN] 目标={contact_id} 内容={text}", file=target)
        return True


def build_sender(*, dry_run: bool, real_sender: MessageSender) -> MessageSender:
    """在组合层选择发送实现，避免把 DRY_RUN 判断塞进 send_text。"""
    return DryRunSender() if dry_run else real_sender
