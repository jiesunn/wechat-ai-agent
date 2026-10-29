"""过滤链顺序、短路、丢弃日志与多模态分流的行为契约。"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from wechat_agent.guard.chain import (
    CHAIN_STAGES,
    STAGE_DUPLICATE,
    STAGE_FORM,
    STAGE_LLM,
    STAGE_MULTIMODAL,
    STAGE_WHITELIST,
    FilterChain,
    MediaHandler,
)
from wechat_agent.guard.validate import DEFAULT_MAX_CHARS, DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.models import MessagePart, NormalizedMessage
from wechat_agent.wechat.sender import contact_hash


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_chain(
    *,
    whitelist: str = "id_1",
    media_handler: MediaHandler | None = None,
    clock: FakeClock | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> FilterChain:
    return FilterChain(
        whitelist=Whitelist(whitelist),
        duplicate_suppressor=DuplicateSuppressor(clock=clock or FakeClock()),
        media_handler=media_handler,
        max_chars=max_chars,
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


def image_message(*, contact_id: str = "id_1", msg_id: str = "msg-img") -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        from_user_id=contact_id,
        parts=(MessagePart(type="image", media_size=1024),),
    )


def test_chain_declares_fixed_step_order() -> None:
    chain = make_chain()

    assert [step.name for step in chain.steps] == list(CHAIN_STAGES[:-1])
    assert CHAIN_STAGES[-1] == STAGE_LLM


async def test_whitelisted_text_reaches_llm_stage() -> None:
    result = await make_chain().evaluate(text_message("hello"))

    assert result.allowed is True
    assert result.stage == STAGE_LLM
    assert result.text == "hello"
    assert result.truncated is False


async def test_stranger_with_emoji_is_stopped_at_whitelist() -> None:
    result = await make_chain().evaluate(text_message("😀😀", contact_id="id_99"))

    assert result.allowed is False
    assert result.stage == STAGE_WHITELIST
    assert result.reason == "not_in_whitelist"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("   ", "empty_text"),
        ("😀😀", "no_meaningful_content"),
        ("，。！？", "no_meaningful_content"),
    ],
)
async def test_form_step_drops_blank_emoji_and_punctuation(text: str, reason: str) -> None:
    result = await make_chain().evaluate(text_message(text))

    assert result.allowed is False
    assert result.stage == STAGE_FORM
    assert result.reason == reason


async def test_truncation_precedes_dedup_for_long_messages() -> None:
    chain = make_chain()
    prefix = "a" * DEFAULT_MAX_CHARS

    first = await chain.evaluate(text_message(prefix + "尾巴一", msg_id="msg-1"))
    assert first.allowed is True
    assert first.stage == STAGE_LLM
    assert first.truncated is True
    assert len(first.text) == DEFAULT_MAX_CHARS

    second = await chain.evaluate(text_message(prefix + "尾巴二", msg_id="msg-2"))
    assert second.allowed is False
    assert second.stage == STAGE_DUPLICATE
    assert second.reason == "duplicate_text"


async def test_normalized_repeat_is_stopped_at_duplicate() -> None:
    chain = make_chain()

    assert (await chain.evaluate(text_message("hello"))).allowed is True
    result = await chain.evaluate(text_message("hello ", msg_id="msg-2"))

    assert result.allowed is False
    assert result.stage == STAGE_DUPLICATE
    assert result.reason == "duplicate_text"


async def test_drop_log_contains_only_metadata(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    secret = "这条正文不应出现在日志里😀"
    message = text_message(secret, contact_id="wxid_stranger", msg_id="msg-secret")

    result = await make_chain().evaluate(message)

    assert result.stage == STAGE_WHITELIST
    record = next(log for log in caplog.records if getattr(log, "stage", None) == STAGE_WHITELIST)
    assert record.getMessage() == "消息被过滤"
    assert record.msg_id == "msg-secret"
    assert record.contact_hash == contact_hash("wxid_stranger")
    assert len(record.contact_hash) == 12
    assert record.reason == "not_in_whitelist"
    assert "text" not in record.__dict__
    assert secret not in caplog.text


async def test_non_whitelisted_image_never_calls_media_handler() -> None:
    handler = AsyncMock()
    message = image_message(contact_id="id_99")

    result = await make_chain(media_handler=handler).evaluate(message)

    assert result.allowed is False
    assert result.stage == STAGE_WHITELIST
    handler.handle.assert_not_awaited()


async def test_whitelisted_image_is_routed_before_llm() -> None:
    handler = AsyncMock()
    message = image_message()

    result = await make_chain(media_handler=handler).evaluate(message)

    assert result.allowed is False
    assert result.stage == STAGE_MULTIMODAL
    assert result.reason == "media_routed:image"
    handler.handle.assert_awaited_once_with(message)


async def test_text_plus_image_keeps_text_and_skips_handler() -> None:
    handler = AsyncMock()
    message = NormalizedMessage(
        msg_id="msg-mixed",
        from_user_id="id_1",
        parts=(
            MessagePart(type="text", text="看看这个"),
            MessagePart(type="image", media_size=1024),
        ),
    )

    result = await make_chain(media_handler=handler).evaluate(message)

    assert result.allowed is True
    assert result.stage == STAGE_LLM
    assert result.text == "看看这个"
    handler.handle.assert_not_awaited()


async def test_media_without_handler_stops_before_llm() -> None:
    result = await make_chain(media_handler=None).evaluate(image_message())

    assert result.allowed is False
    assert result.stage == STAGE_MULTIMODAL
    assert result.reason == "no_media_handler"
