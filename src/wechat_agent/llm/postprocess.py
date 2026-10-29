"""降级与输出后处理：失败或空输出走兜底，关闭兜底则静默，脏字符就地清理。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from wechat_agent.config import DEFAULT_FALLBACK_REPLY_TEXT
from wechat_agent.llm.client import LLMError
from wechat_agent.logging import LOGGER_NAME

ZERO_WIDTH_CHARS: Final = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})
KEPT_CONTROL_CHARS: Final = frozenset({"\n", "\t"})

logger = logging.getLogger(LOGGER_NAME)


def clean_reply(reply: str) -> str:
    """去掉零宽字符与 ``\\x00-\\x1f`` 控制字符（保留换行与制表符），再 strip。

    中文标点与 emoji 原样保留；不做任何按类别的字符白名单。
    """
    return "".join(
        char
        for char in reply
        if char not in ZERO_WIDTH_CHARS and (char in KEPT_CONTROL_CHARS or ord(char) >= 0x20)
    ).strip()


async def generate_reply(
    complete: Callable[[], Awaitable[str]],
    *,
    fallback_enabled: bool = True,
    fallback_text: str = DEFAULT_FALLBACK_REPLY_TEXT,
) -> str | None:
    """调用一次补全并对结果降级；返回 ``None`` 表示静默、不发送任何消息。

    ``complete`` 通常就是 ``client.complete(messages)`` 的闭包；超时与失败已由
    5.1 的客户端统一成 ``LLMError``（含 ``RetryableLLMError``），这里不再判断状态码。
    """
    try:
        raw = await complete()
    except LLMError as error:
        logger.warning(
            "LLM 调用失败，走降级",
            extra={
                "reason": "llm_error",
                "error_type": type(error).__name__,
                "status_code": error.status_code,
                "attempts": error.attempts,
            },
        )
        return _fallback_or_silent(fallback_enabled, fallback_text, reason="llm_error")

    reply = clean_reply(raw)
    if not reply:
        logger.warning("LLM 输出为空，走降级", extra={"reason": "empty_reply"})
        return _fallback_or_silent(fallback_enabled, fallback_text, reason="empty_reply")
    return reply


def _fallback_or_silent(enabled: bool, fallback_text: str, *, reason: str) -> str | None:
    if not enabled:
        logger.warning("兜底话术已关闭，本条静默", extra={"reason": reason, "silent": True})
        return None
    text = fallback_text.strip()
    if not text:
        # 兜底文案被配成空白时同样不发空消息
        logger.warning("兜底话术为空，本条静默", extra={"reason": reason, "silent": True})
        return None
    return text
