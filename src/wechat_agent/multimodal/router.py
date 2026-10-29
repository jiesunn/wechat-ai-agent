"""混合消息分流：有文本只把文本交给 LLM，纯媒体走降级，全程不碰媒体内容。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.multimodal.handlers import MediaHandlerRegistry
from wechat_agent.wechat.models import MessagePart, NormalizedMessage

ATTACHMENT_NOTICE: Final = "\n\n（附件暂不支持）"

logger = logging.getLogger(LOGGER_NAME)


class MultimodalRouter:
    """已通过白名单的消息在这里分流。

    只负责三件事：提取文本交给外部生成器、纯媒体交给注册表（未注册或异常都会
    回落默认降级）、混合消息在 LLM 回复末尾追加「附件暂不支持」。不下载媒体、
    不读文件、不判断白名单——白名单外的静默由 3.3 过滤链负责。
    """

    def __init__(self, *, handlers: MediaHandlerRegistry | None = None) -> None:
        self._handlers = handlers if handlers is not None else MediaHandlerRegistry()

    async def route(
        self,
        message: NormalizedMessage,
        *,
        generate_text_reply: Callable[[str], Awaitable[str | None]],
    ) -> str | None:
        """返回要发送的文本；``None`` 表示本条静默。"""
        text = _extract_text(message)
        media = _media_parts(message)
        if text:
            reply = await generate_text_reply(text)
            if reply is None:
                return None
            if not media:
                return reply
            logger.info(
                "混合消息仅用文本生成回复",
                extra={"msg_id": message.msg_id, "media_count": len(media)},
            )
            return f"{reply}{ATTACHMENT_NOTICE}"
        if not media:
            logger.debug("消息既无文本也无媒体，静默", extra={"msg_id": message.msg_id})
            return None
        part = media[0]
        logger.info(
            "纯媒体消息走降级话术",
            extra={
                "msg_id": message.msg_id,
                "media_type": part.type,
                "media_size": part.media_size,
            },
        )
        return self._handlers.handle(part, message)


def _extract_text(message: NormalizedMessage) -> str:
    return "\n".join(
        part.text for part in message.parts if part.type == "text" and part.text
    ).strip()


def _media_parts(message: NormalizedMessage) -> Sequence[MessagePart]:
    return [part for part in message.parts if part.type != "text"]
