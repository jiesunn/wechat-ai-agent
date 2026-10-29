"""混合消息分流：文本只走 LLM、纯媒体降级、未知类型降级、零下载请求。"""

from __future__ import annotations

import pytest
import respx

from wechat_agent.config import DEFAULT_UNSUPPORTED_MEDIA_REPLY
from wechat_agent.llm.client import LLMClient
from wechat_agent.multimodal.handlers import MediaHandlerRegistry
from wechat_agent.multimodal.router import ATTACHMENT_NOTICE, MultimodalRouter
from wechat_agent.wechat.models import MessagePart, NormalizedMessage

USER_TEXT = "这张图好看吗"
IMAGE = MessagePart(type="image", media_size=2048)


class _TextGenerator:
    def __init__(self, reply: str | None = "这张图挺好看的") -> None:
        self.reply = reply
        self.texts: list[str] = []

    async def __call__(self, text: str) -> str | None:
        self.texts.append(text)
        return self.reply


class _ImageHandler:
    def __init__(self, reply: str = "图片收到") -> None:
        self.reply = reply
        self.calls = 0

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        self.calls += 1
        return self.reply


class _BoomHandler:
    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        raise RuntimeError("模拟处理器故障")


def _message(*parts: MessagePart) -> NormalizedMessage:
    return NormalizedMessage(msg_id="msg-1", from_user_id="wxid_test", parts=tuple(parts))


async def test_mixed_message_sends_only_text_then_appends_notice() -> None:
    generator = _TextGenerator()
    message = _message(MessagePart(type="text", text=USER_TEXT), IMAGE)

    reply = await MultimodalRouter().route(message, generate_text_reply=generator)

    assert generator.texts == [USER_TEXT]
    assert reply == f"这张图挺好看的{ATTACHMENT_NOTICE}"
    assert "附件暂不支持" in reply


async def test_text_only_message_has_no_attachment_notice() -> None:
    generator = _TextGenerator("今天天气不错")
    message = _message(MessagePart(type="text", text="今天天气怎么样"))

    reply = await MultimodalRouter().route(message, generate_text_reply=generator)

    assert reply == "今天天气不错"
    assert ATTACHMENT_NOTICE not in reply


async def test_pure_media_uses_fallback_without_llm() -> None:
    generator = _TextGenerator()

    reply = await MultimodalRouter().route(
        _message(MessagePart(type="image", media_size=2048)),
        generate_text_reply=generator,
    )

    assert reply == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    assert generator.texts == []


async def test_unknown_type_uses_fallback() -> None:
    generator = _TextGenerator()

    reply = await MultimodalRouter().route(
        _message(MessagePart(type="unknown")),
        generate_text_reply=generator,
    )

    assert reply == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    assert generator.texts == []


async def test_no_media_download_requests(respx_mock: respx.Router) -> None:
    router = MultimodalRouter()
    generator = _TextGenerator()
    messages = [
        _message(MessagePart(type="text", text=USER_TEXT), IMAGE),
        _message(IMAGE),
        _message(MessagePart(type="video", media_size=10240)),
        _message(MessagePart(type="unknown")),
    ]

    for message in messages:
        await router.route(message, generate_text_reply=generator)

    assert len(respx_mock.calls) == 0
    assert len(generator.texts) == 1


async def test_multiple_text_parts_are_joined_in_order() -> None:
    generator = _TextGenerator()
    message = _message(
        MessagePart(type="text", text="第一行"),
        MessagePart(type="text", text="第二行"),
    )

    await MultimodalRouter().route(message, generate_text_reply=generator)

    assert generator.texts == ["第一行\n第二行"]


async def test_blank_text_with_media_falls_back_without_llm() -> None:
    generator = _TextGenerator()
    message = _message(
        MessagePart(type="text", text="   "),
        MessagePart(type="voice", media_size=1024),
    )

    reply = await MultimodalRouter().route(message, generate_text_reply=generator)

    assert reply == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    assert generator.texts == []


async def test_registered_media_handler_is_used() -> None:
    handler = _ImageHandler()
    registry = MediaHandlerRegistry()
    registry.register("image", handler)
    generator = _TextGenerator()

    reply = await MultimodalRouter(handlers=registry).route(
        _message(IMAGE),
        generate_text_reply=generator,
    )

    assert reply == "图片收到"
    assert handler.calls == 1
    assert generator.texts == []


async def test_media_handler_exception_falls_back() -> None:
    registry = MediaHandlerRegistry()
    registry.register("image", _BoomHandler())

    reply = await MultimodalRouter(handlers=registry).route(
        _message(IMAGE),
        generate_text_reply=_TextGenerator(),
    )

    assert reply == DEFAULT_UNSUPPORTED_MEDIA_REPLY


async def test_silent_generator_stays_silent_even_with_media() -> None:
    generator = _TextGenerator(reply=None)
    message = _message(MessagePart(type="text", text=USER_TEXT), IMAGE)

    assert await MultimodalRouter().route(message, generate_text_reply=generator) is None
    assert generator.texts == [USER_TEXT]


async def test_message_without_parts_is_silent() -> None:
    generator = _TextGenerator()

    assert await MultimodalRouter().route(_message(), generate_text_reply=generator) is None
    assert generator.texts == []


async def test_pure_media_never_calls_llm(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.Router,
) -> None:
    calls: list[object] = []

    async def spy(self: LLMClient, messages: object) -> str:
        calls.append(messages)
        return ""

    monkeypatch.setattr(LLMClient, "complete", spy)

    reply = await MultimodalRouter().route(
        _message(MessagePart(type="image", media_size=1)),
        generate_text_reply=_TextGenerator(),
    )

    assert reply == DEFAULT_UNSUPPORTED_MEDIA_REPLY
    assert calls == []
    assert len(respx_mock.calls) == 0
