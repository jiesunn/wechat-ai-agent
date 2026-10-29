"""多模态处理器：注册表分发、未注册回落、异常隔离且不发起任何请求。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import get_args

import pytest
import respx

from wechat_agent.config import DEFAULT_UNSUPPORTED_MEDIA_REPLY
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.multimodal.handlers import (
    MEDIA_TYPES,
    DefaultFallbackHandler,
    MediaHandlerRegistry,
)
from wechat_agent.wechat.models import MessagePart, NormalizedMessage, PartType

IMAGE = MessagePart(type="image", media_size=2048)
VOICE = MessagePart(type="voice", media_size=1024)


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _ImageHandler:
    def __init__(self, reply: str = "图片我收到了") -> None:
        self.reply = reply
        self.calls: list[tuple[MessagePart, NormalizedMessage]] = []

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        self.calls.append((part, message))
        return self.reply


class _BoomHandler:
    def __init__(self) -> None:
        self.calls = 0

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        self.calls += 1
        raise RuntimeError("模拟处理器故障")


class _RecordingFallback:
    def __init__(self, reply: str = "自定义兜底") -> None:
        self.reply = reply
        self.calls = 0

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        self.calls += 1
        return self.reply


def _message(*parts: MessagePart) -> NormalizedMessage:
    return NormalizedMessage(msg_id="msg-1", from_user_id="wxid_test", parts=tuple(parts))


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


def test_registered_handler_handles_matching_type() -> None:
    handler = _ImageHandler()
    fallback = _RecordingFallback()
    registry = MediaHandlerRegistry(fallback=fallback)
    registry.register("image", handler)
    message = _message(IMAGE)

    assert registry.handle(IMAGE, message) == "图片我收到了"
    assert handler.calls == [(IMAGE, message)]
    assert fallback.calls == 0


def test_unregistered_type_falls_back_to_default() -> None:
    registry = MediaHandlerRegistry()
    registry.register("image", _ImageHandler())

    assert registry.handle(VOICE, _message(VOICE)) == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    assert registry.get("voice") is registry.fallback
    assert isinstance(registry.fallback, DefaultFallbackHandler)


def test_handler_exception_is_isolated_and_loop_continues(
    logs: list[logging.LogRecord],
) -> None:
    boom = _BoomHandler()
    fallback = _RecordingFallback()
    registry = MediaHandlerRegistry(fallback=fallback)
    registry.register("image", boom)

    assert registry.handle(IMAGE, _message(IMAGE)) == "自定义兜底"
    assert boom.calls == 1
    assert fallback.calls == 1

    replacement = _ImageHandler("恢复后的回复")
    registry.register("image", replacement)
    assert registry.handle(IMAGE, _message(IMAGE)) == "恢复后的回复"
    assert len(replacement.calls) == 1

    warnings = [record for record in logs if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].media_type == "image"
    assert warnings[0].error_type == "RuntimeError"
    assert warnings[0].media_size == 2048


def test_logs_omit_media_content(logs: list[logging.LogRecord]) -> None:
    part = MessagePart(type="image", text="这条媒体正文不该进日志", media_size=2048)

    MediaHandlerRegistry().handle(part, _message(part))

    rendered = " ".join(f"{record.getMessage()} {record.__dict__}" for record in logs)
    assert "这条媒体正文不该进日志" not in rendered
    assert "image" in rendered


def test_text_type_cannot_be_registered() -> None:
    registry = MediaHandlerRegistry()

    with pytest.raises(ValueError):
        registry.register("text", _ImageHandler())


@pytest.mark.parametrize("media_type", ["audio", "image_file", "", "  "])
def test_unknown_type_cannot_be_registered(media_type: str) -> None:
    registry = MediaHandlerRegistry()

    with pytest.raises(ValueError):
        registry.register(media_type, _ImageHandler())


def test_media_types_match_message_part_types() -> None:
    assert MEDIA_TYPES | {"text"} == set(get_args(PartType))


def test_default_fallback_reuses_config_text() -> None:
    assert (
        DefaultFallbackHandler().handle(VOICE, _message(VOICE)) == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    )


def test_custom_fallback_is_used_for_unregistered_types() -> None:
    fallback = _RecordingFallback()
    registry = MediaHandlerRegistry(fallback=fallback)

    assert registry.handle(VOICE, _message(VOICE)) == "自定义兜底"
    assert fallback.calls == 1


def test_handling_media_issues_no_network_requests(respx_mock: respx.Router) -> None:
    registry = MediaHandlerRegistry()
    registry.register("image", _ImageHandler())
    parts = [
        IMAGE,
        VOICE,
        MessagePart(type="file", media_size=1),
        MessagePart(type="video", media_size=1),
        MessagePart(type="unknown"),
    ]

    for part in parts:
        registry.handle(part, _message(part))

    assert len(respx_mock.calls) == 0
