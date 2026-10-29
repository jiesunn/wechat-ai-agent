"""降级与输出后处理：失败与空输出走兜底、关闭兜底静默、脏字符就地清理。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator

import pytest

from wechat_agent.config import DEFAULT_FALLBACK_REPLY_TEXT
from wechat_agent.llm.client import LLMError, RetryableLLMError
from wechat_agent.llm.postprocess import clean_reply, generate_reply
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.splitter import split_text

FALLBACK = DEFAULT_FALLBACK_REPLY_TEXT


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _raiser(error: Exception) -> Callable[[], Awaitable[str]]:
    async def complete() -> str:
        raise error

    return complete


def _returning(value: str) -> Callable[[], Awaitable[str]]:
    async def complete() -> str:
        return value

    return complete


@pytest.fixture
def logs() -> Iterator[list[logging.LogRecord]]:
    target = logging.getLogger(LOGGER_NAME)
    handler = _RecordingHandler()
    previous_level = target.level
    previous_propagate = target.propagate
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    target.propagate = False
    yield handler.records
    target.removeHandler(handler)
    target.setLevel(previous_level)
    target.propagate = previous_propagate


async def test_timeout_error_falls_back() -> None:
    error = RetryableLLMError("LLM 请求失败：ReadTimeout", attempts=2)

    assert await generate_reply(_raiser(error)) == FALLBACK


async def test_non_retryable_llm_error_falls_back() -> None:
    error = LLMError("LLM 返回 HTTP 400", status_code=400)

    assert await generate_reply(_raiser(error)) == FALLBACK


async def test_empty_output_falls_back() -> None:
    assert await generate_reply(_returning("")) == FALLBACK
    assert await generate_reply(_returning("  \n\t ")) == FALLBACK


async def test_disabled_fallback_is_silent(logs: list[logging.LogRecord]) -> None:
    assert await generate_reply(_returning(""), fallback_enabled=False) is None
    assert (
        await generate_reply(
            _raiser(RetryableLLMError("timeout")),
            fallback_enabled=False,
        )
        is None
    )

    silent_records = [record for record in logs if getattr(record, "silent", False)]
    assert len(silent_records) == 2
    assert {record.reason for record in silent_records} == {"empty_reply", "llm_error"}
    assert all("静默" in record.getMessage() for record in silent_records)


async def test_control_chars_removed_but_punctuation_and_emoji_kept() -> None:
    raw = "\u200b你好，世界！\x00\x07\n\n第二行\t😊\ufeff"

    assert await generate_reply(_returning(raw)) == "你好，世界！\n\n第二行\t😊"


async def test_cleaned_to_empty_falls_back() -> None:
    raw = " \u200b\x00\x07\t\n\ufeff "

    assert clean_reply(raw) == ""
    assert await generate_reply(_returning(raw)) == FALLBACK


async def test_normal_reply_is_returned_stripped() -> None:
    assert clean_reply("  好的，马上到  ") == "好的，马上到"
    assert await generate_reply(_returning("  好的，马上到  ")) == "好的，马上到"


async def test_custom_fallback_text_is_used() -> None:
    assert await generate_reply(_returning(""), fallback_text="稍后再聊") == "稍后再聊"


async def test_blank_fallback_text_stays_silent() -> None:
    assert await generate_reply(_returning(""), fallback_text="   ") is None


async def test_long_reply_is_left_intact_for_the_sender_to_split() -> None:
    long_reply = "第一段，内容比较长。" * 400

    reply = await generate_reply(_returning(long_reply))

    assert reply == long_reply
    assert len(split_text(reply or "")) > 1


async def test_failure_logs_omit_raw_output(logs: list[logging.LogRecord]) -> None:
    await generate_reply(_returning("这条内容不该出现在日志里"), fallback_enabled=False)
    await generate_reply(_raiser(LLMError("LLM 返回 HTTP 500", status_code=500)))

    rendered = " ".join(f"{record.getMessage()} {record.__dict__}" for record in logs)
    assert "这条内容不该出现在日志里" not in rendered
    assert any(getattr(record, "reason", "") == "llm_error" for record in logs)
