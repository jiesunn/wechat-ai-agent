"""多模态处理器：按消息类型分发，未注册或处理器异常都回落到默认降级。"""

from __future__ import annotations

import logging
from typing import Final, Protocol

from wechat_agent.config import DEFAULT_UNSUPPORTED_MEDIA_REPLY
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.models import MessagePart, NormalizedMessage

TEXT_TYPE: Final = "text"
MEDIA_TYPES: Final = frozenset({"image", "voice", "file", "video", "unknown"})

logger = logging.getLogger(LOGGER_NAME)


class MediaHandler(Protocol):
    """多模态处理器端口：返回要发送的降级话术或处理结果。"""

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str: ...


class DefaultFallbackHandler:
    """未注册类型与处理器异常的统一回落。

    首版不下载媒体、不读文件、不落盘，只按类型与大小回一句降级话术。
    """

    def __init__(self, reply: str = DEFAULT_UNSUPPORTED_MEDIA_REPLY) -> None:
        self._reply = reply

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        logger.info(
            "媒体消息使用默认降级话术",
            extra={"media_type": part.type, "media_size": part.media_size},
        )
        return self._reply


class MediaHandlerRegistry:
    """按 ``MessagePart.type`` 分发的处理器表，本身也满足 ``MediaHandler``。

    只收非文本类型：text 始终走正常 LLM 链路。处理器抛出的异常在这里被隔离，
    记 ``media_type`` 与 ``error_type`` 后回落默认处理器，不会冒泡到管线。
    """

    def __init__(self, *, fallback: MediaHandler | None = None) -> None:
        self._handlers: dict[str, MediaHandler] = {}
        self._fallback: MediaHandler = (
            fallback if fallback is not None else DefaultFallbackHandler()
        )

    @property
    def fallback(self) -> MediaHandler:
        return self._fallback

    def register(self, media_type: str, handler: MediaHandler) -> None:
        normalized = media_type.strip().lower()
        if normalized == TEXT_TYPE:
            raise ValueError("text 走正常 LLM 链路，不注册多模态处理器")
        if normalized not in MEDIA_TYPES:
            raise ValueError(f"未知的媒体类型：{media_type}")
        self._handlers[normalized] = handler

    def get(self, media_type: str) -> MediaHandler:
        """按类型取处理器；未注册时返回默认降级处理器。"""
        return self._handlers.get(media_type, self._fallback)

    def handle(self, part: MessagePart, message: NormalizedMessage) -> str:
        handler = self._handlers.get(part.type)
        if handler is None:
            logger.info(
                "媒体类型未注册处理器，回落默认降级",
                extra={"media_type": part.type, "media_size": part.media_size},
            )
            return self._fallback.handle(part, message)
        try:
            return handler.handle(part, message)
        except Exception as error:
            logger.warning(
                "媒体处理器异常，回落默认降级",
                extra={
                    "media_type": part.type,
                    "error_type": type(error).__name__,
                    "media_size": part.media_size,
                },
            )
            return self._fallback.handle(part, message)
