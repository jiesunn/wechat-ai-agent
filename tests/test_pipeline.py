"""管线编排：同联系人串行、跨联系人并发、有界队列与优雅停机。"""

from __future__ import annotations

import asyncio
import logging
import random
import sys
from collections.abc import Callable
from io import StringIO
from unittest.mock import AsyncMock

import pytest

from wechat_agent.guard.chain import FilterChain
from wechat_agent.guard.validate import DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.pipeline import Pipeline, ReplyGenerator
from wechat_agent.sender import DryRunSender, MessageSender, build_sender
from wechat_agent.signals import ShutdownController
from wechat_agent.wechat.models import MessagePart, NormalizedMessage


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        self.sent.append((contact_id, text))
        return True


async def echo_reply(
    message: NormalizedMessage,
    *,
    text: str,
    truncated: bool,
) -> str | None:
    return f"re:{text}"


def make_pipeline(
    *,
    handler: ReplyGenerator = echo_reply,
    sender: MessageSender | None = None,
    queue_size: int = 100,
    shutdown_timeout: float = 5.0,
    whitelist: str = "id_1,id_2",
) -> Pipeline:
    return Pipeline(
        chain=FilterChain(
            whitelist=Whitelist(whitelist),
            duplicate_suppressor=DuplicateSuppressor(),
        ),
        sender=sender if sender is not None else RecordingSender(),
        reply_generator=handler,
        queue_size=queue_size,
        shutdown_timeout=shutdown_timeout,
    )


def text_message(
    text: str,
    *,
    contact_id: str = "id_1",
    msg_id: str = "msg-1",
) -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        from_user_id=contact_id,
        parts=(MessagePart(type="text", text=text),),
    )


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_same_contact_messages_are_processed_in_arrival_order() -> None:
    completed: list[str] = []
    sender = RecordingSender()
    finished = asyncio.Event()

    async def handler(
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str:
        await asyncio.sleep(random.uniform(0.01, 0.03))
        completed.append(text)
        if len(completed) == 3:
            finished.set()
        return f"re:{text}"

    pipeline = make_pipeline(handler=handler, sender=sender)
    pipeline.start()
    try:
        for index in ("1", "2", "3"):
            assert pipeline.submit(text_message(index, msg_id=f"msg-{index}")) is True
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        await pipeline.stop()

    assert completed == ["1", "2", "3"]
    assert sender.sent == [("id_1", "re:1"), ("id_1", "re:2"), ("id_1", "re:3")]


async def test_different_contacts_can_be_processed_concurrently() -> None:
    started: set[str] = set()
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def handler(
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str:
        started.add(message.from_user_id)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return "ok"

    pipeline = make_pipeline(handler=handler)
    pipeline.start()
    try:
        assert pipeline.submit(text_message("a", contact_id="id_1", msg_id="msg-a")) is True
        assert pipeline.submit(text_message("b", contact_id="id_2", msg_id="msg-b")) is True
        await asyncio.wait_for(both_started.wait(), timeout=2)
    finally:
        release.set()
        await pipeline.stop()

    assert started == {"id_1", "id_2"}


async def test_queue_full_rejects_new_messages_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pipeline = make_pipeline(queue_size=2)

    results = [
        pipeline.submit(text_message(str(index), msg_id=f"msg-{index}")) for index in range(1, 6)
    ]

    assert results == [True, True, False, False, False]
    rejected = [record for record in caplog.records if getattr(record, "reason", None)]
    assert [record.msg_id for record in rejected] == ["msg-3", "msg-4", "msg-5"]
    assert all(record.reason == "queue_full" for record in rejected)
    assert all(record.queue_size == 2 for record in rejected)
    await pipeline.stop()


async def test_dry_run_never_calls_real_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    real_sender = AsyncMock()
    real_sender.send_text.return_value = True
    sender = build_sender(dry_run=True, real_sender=real_sender)

    pipeline = make_pipeline(sender=sender)
    pipeline.start()
    try:
        assert pipeline.submit(text_message("hello", msg_id="msg-dry")) is True
        await wait_until(lambda: "[DRY_RUN]" in stream.getvalue())
    finally:
        await pipeline.stop()

    assert isinstance(sender, DryRunSender)
    real_sender.send_text.assert_not_awaited()
    assert "re:hello" in stream.getvalue()


async def test_shutdown_stops_intake_and_drains_inflight() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed: list[str] = []

    async def handler(
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str:
        started.set()
        await release.wait()
        completed.append(text)
        return "ok"

    pipeline = make_pipeline(handler=handler, shutdown_timeout=1.0)
    pipeline.start()
    controller = ShutdownController(pipeline.stop)
    try:
        assert pipeline.submit(text_message("1", msg_id="msg-1")) is True
        await asyncio.wait_for(started.wait(), timeout=2)
        controller.request_shutdown()
        await wait_until(lambda: pipeline.stopping)
        assert pipeline.submit(text_message("2", msg_id="msg-2")) is False
        release.set()
        await asyncio.wait_for(controller.wait_finished(), timeout=2)
    finally:
        release.set()
        await pipeline.stop()

    assert completed == ["1"]
