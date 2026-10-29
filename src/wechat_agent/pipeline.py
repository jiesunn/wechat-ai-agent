"""消息管线：有界队列、单调度 worker 与每联系人串行处理。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Final, Protocol

from wechat_agent.guard.chain import FilterChain
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.sender import MessageSender
from wechat_agent.wechat.models import NormalizedMessage
from wechat_agent.wechat.sender import contact_hash

__all__ = ["DEFAULT_QUEUE_SIZE", "DEFAULT_SHUTDOWN_TIMEOUT", "Pipeline", "ReplyGenerator"]

DEFAULT_QUEUE_SIZE: Final = 100
DEFAULT_SHUTDOWN_TIMEOUT: Final = 5.0

logger = logging.getLogger(LOGGER_NAME)


class ReplyGenerator(Protocol):
    """根据准入后的文本生成回复；返回 None 表示静默。"""

    async def __call__(
        self,
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str | None: ...


class Pipeline:
    """有界队列 + 单调度 worker；同一联系人串行，不同联系人可并发。"""

    def __init__(
        self,
        *,
        chain: FilterChain,
        sender: MessageSender,
        reply_generator: ReplyGenerator,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size 必须为正数")
        if shutdown_timeout < 0:
            raise ValueError("shutdown_timeout 不能为负数")
        self._chain = chain
        self._sender = sender
        self._reply_generator = reply_generator
        self._shutdown_timeout = shutdown_timeout
        self._queue: asyncio.Queue[NormalizedMessage] = asyncio.Queue(maxsize=queue_size)
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: set[asyncio.Task[None]] = set()
        self._dispatcher: asyncio.Task[None] | None = None
        self._stopping = False
        self._stopped = asyncio.Event()

    @property
    def stopping(self) -> bool:
        return self._stopping

    def start(self) -> None:
        if self._stopping:
            raise RuntimeError("管线正在停机，不能启动")
        if self._dispatcher is not None:
            raise RuntimeError("管线已启动")
        self._dispatcher = asyncio.create_task(self._dispatch())

    def submit(self, message: NormalizedMessage) -> bool:
        """非阻塞入队；停机中或队列已满时告警并返回 False。"""
        if self._stopping:
            _log_reject(message, "pipeline_stopping", self._queue.maxsize)
            return False
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            _log_reject(message, "queue_full", self._queue.maxsize)
            return False
        return True

    async def stop(self, *, timeout: float | None = None) -> None:
        """停止取新消息，等待在途消息；超时后取消剩余任务。可重复调用。"""
        if self._stopping:
            await self._stopped.wait()
            return
        self._stopping = True
        try:
            dispatcher = self._dispatcher
            self._dispatcher = None
            if dispatcher is not None:
                dispatcher.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatcher
            if self._queue.qsize():
                logger.warning("停机时仍有排队消息未处理", extra={"queued": self._queue.qsize()})
            await self._wait_inflight(self._shutdown_timeout if timeout is None else timeout)
        finally:
            self._stopped.set()

    async def _dispatch(self) -> None:
        while True:
            message = await self._queue.get()
            task = asyncio.create_task(self._process_in_order(message))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _process_in_order(self, message: NormalizedMessage) -> None:
        lock = self._locks.setdefault(message.from_user_id, asyncio.Lock())
        async with lock:
            try:
                await self._handle(message)
            except Exception as exc:
                logger.error(
                    "消息处理异常",
                    extra={
                        "msg_id": message.msg_id,
                        "contact_hash": contact_hash(message.from_user_id),
                        "error_type": type(exc).__name__,
                    },
                )

    async def _handle(self, message: NormalizedMessage) -> None:
        result = await self._chain.evaluate(message)
        if not result.allowed:
            return
        reply = await self._reply_generator(
            message,
            text=result.text,
            truncated=result.truncated,
        )
        if not reply:
            return
        sent = await self._sender.send_text(
            message.from_user_id,
            reply,
            context_token=message.context_token,
        )
        if not sent:
            logger.warning(
                "回复发送失败",
                extra={
                    "msg_id": message.msg_id,
                    "contact_hash": contact_hash(message.from_user_id),
                },
            )

    async def _wait_inflight(self, timeout: float) -> None:
        pending = {task for task in self._inflight if not task.done()}
        if not pending:
            return
        _, still_pending = await asyncio.wait(pending, timeout=timeout)
        if not still_pending:
            return
        logger.warning("停机等待超时，取消在途消息", extra={"pending": len(still_pending)})
        for task in still_pending:
            task.cancel()
        await asyncio.gather(*still_pending, return_exceptions=True)


def _log_reject(message: NormalizedMessage, reason: str, queue_size: int) -> None:
    logger.warning(
        "消息入队被拒",
        extra={
            "msg_id": message.msg_id,
            "contact_hash": contact_hash(message.from_user_id),
            "reason": reason,
            "queue_size": queue_size,
        },
    )
