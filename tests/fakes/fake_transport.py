"""测试替身：用队列投递入站消息，并可注入传输错误。"""

from __future__ import annotations

from collections import deque

from wechat_agent.wechat.models import NormalizedMessage, PollResult, TransportError
from wechat_agent.wechat.transport import WeChatReceiver


class FakeTransport(WeChatReceiver):
    """内存版 WeChatReceiver：不触达网络，入站与故障均可编排。"""

    def __init__(self) -> None:
        self.inbox: deque[NormalizedMessage] = deque()
        self.poll_errors: deque[TransportError] = deque()
        self.login_count = 0
        self.poll_count = 0
        self.last_cursor: str | None = None
        self.closed = False
        self._cursor_seq = 0

    def push(self, *messages: NormalizedMessage) -> None:
        """投递入站消息，按投递顺序在后续 poll 中一次返回。"""
        self.inbox.extend(messages)

    def fail_next_poll(self, error: TransportError) -> None:
        """让下一次 poll 抛出指定错误，用于模拟网络失败与会话过期。"""
        self.poll_errors.append(error)

    async def login(self) -> None:
        self.login_count += 1

    async def poll(self, cursor: str | None) -> PollResult:
        self.poll_count += 1
        self.last_cursor = cursor
        if self.poll_errors:
            raise self.poll_errors.popleft()
        if not self.inbox:
            return PollResult(messages=(), cursor=cursor)
        messages = tuple(self.inbox)
        self.inbox.clear()
        self._cursor_seq += 1
        return PollResult(messages=messages, cursor=f"cursor-{self._cursor_seq}")

    async def close(self) -> None:
        self.closed = True
