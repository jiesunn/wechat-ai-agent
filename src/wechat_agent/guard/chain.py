"""准入过滤链：固定顺序、逐级短路，丢弃日志不含消息正文。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Protocol

from wechat_agent.guard.validate import (
    DEFAULT_MAX_CHARS,
    DuplicateSuppressor,
    has_meaningful_content,
    truncate_text,
)
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.models import NormalizedMessage
from wechat_agent.wechat.sender import contact_hash

__all__ = [
    "CHAIN_STAGES",
    "STAGE_DUPLICATE",
    "STAGE_FORM",
    "STAGE_LENGTH",
    "STAGE_LLM",
    "STAGE_MULTIMODAL",
    "STAGE_WHITELIST",
    "AdmissionContext",
    "ChainResult",
    "FilterChain",
    "FilterStep",
    "MediaHandler",
]

STAGE_WHITELIST: Final = "whitelist"
STAGE_FORM: Final = "form"
STAGE_LENGTH: Final = "length"
STAGE_DUPLICATE: Final = "duplicate"
STAGE_MULTIMODAL: Final = "multimodal"
STAGE_LLM: Final = "llm"

CHAIN_STAGES: Final[tuple[str, ...]] = (
    STAGE_WHITELIST,
    STAGE_FORM,
    STAGE_LENGTH,
    STAGE_DUPLICATE,
    STAGE_MULTIMODAL,
    STAGE_LLM,
)

logger = logging.getLogger(LOGGER_NAME)


class MediaHandler(Protocol):
    """多模态处理器端口；6.1 的注册表与 6.2 的降级处理器都实现它。"""

    async def handle(self, message: NormalizedMessage) -> None: ...


@dataclass(slots=True)
class AdmissionContext:
    """过滤过程中的消息视图；文本截断与媒体分流状态由步骤就地更新。"""

    message: NormalizedMessage
    text: str
    has_media: bool
    media_type: str | None
    truncated: bool = False
    media_handled: bool = False

    @classmethod
    def from_message(cls, message: NormalizedMessage) -> AdmissionContext:
        text_parts = [part.text for part in message.parts if part.type == "text" and part.text]
        media_types = [part.type for part in message.parts if part.type != "text"]
        return cls(
            message=message,
            text="\n".join(text_parts),
            has_media=bool(media_types),
            media_type=media_types[0] if media_types else None,
        )


class FilterStep(Protocol):
    """单步过滤器：名字用于日志与顺序断言，返回 ``(是否继续, 原因)``。"""

    name: str

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainResult:
    """一次准入的结论；allowed 为 False 时 stage/reason 指出拦截位置。"""

    allowed: bool
    stage: str
    reason: str = ""
    text: str = ""
    truncated: bool = False


class WhitelistStep:
    name = STAGE_WHITELIST

    def __init__(self, whitelist: Whitelist) -> None:
        self._whitelist = whitelist

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]:
        if self._whitelist.allows(context.message.from_user_id):
            return True, ""
        return False, "not_in_whitelist"


class FormStep:
    name = STAGE_FORM

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]:
        if not context.text.strip():
            return (True, "") if context.has_media else (False, "empty_text")
        if not has_meaningful_content(context.text):
            return False, "no_meaningful_content"
        return True, ""


class LengthStep:
    name = STAGE_LENGTH

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self._max_chars = max_chars

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]:
        context.text, context.truncated = truncate_text(
            context.text,
            max_chars=self._max_chars,
        )
        return True, ""


class DuplicateStep:
    name = STAGE_DUPLICATE

    def __init__(self, suppressor: DuplicateSuppressor) -> None:
        self._suppressor = suppressor

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]:
        if not context.text:
            return True, ""
        if self._suppressor.is_duplicate(context.message.from_user_id, context.text):
            return False, "duplicate_text"
        return True, ""


class MultimodalStep:
    name = STAGE_MULTIMODAL

    def __init__(self, handler: MediaHandler | None = None) -> None:
        self._handler = handler

    async def __call__(self, context: AdmissionContext) -> tuple[bool, str]:
        if not context.has_media or context.text.strip():
            return True, ""
        if self._handler is None:
            return False, "no_media_handler"
        await self._handler.handle(context.message)
        context.media_handled = True
        return False, f"media_routed:{context.media_type}"


class FilterChain:
    """固定顺序的准入过滤链；任何一步返回 False 都立即短路。"""

    def __init__(
        self,
        *,
        whitelist: Whitelist,
        duplicate_suppressor: DuplicateSuppressor,
        media_handler: MediaHandler | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.steps: tuple[FilterStep, ...] = (
            WhitelistStep(whitelist),
            FormStep(),
            LengthStep(max_chars),
            DuplicateStep(duplicate_suppressor),
            MultimodalStep(media_handler),
        )

    async def evaluate(self, message: NormalizedMessage) -> ChainResult:
        context = AdmissionContext.from_message(message)
        for step in self.steps:
            keep, reason = await step(context)
            if keep:
                continue
            if not context.media_handled:
                _log_drop(message, step.name, reason)
            return ChainResult(
                allowed=False,
                stage=step.name,
                reason=reason,
                text=context.text,
                truncated=context.truncated,
            )
        return ChainResult(
            allowed=True,
            stage=STAGE_LLM,
            reason="",
            text=context.text,
            truncated=context.truncated,
        )


def _log_drop(message: NormalizedMessage, stage: str, reason: str) -> None:
    """丢弃日志只放消息 id、发送者哈希、阶段与原因，绝不带正文。"""
    logger.info(
        "消息被过滤",
        extra={
            "msg_id": message.msg_id,
            "contact_hash": contact_hash(message.from_user_id),
            "stage": stage,
            "reason": reason,
        },
    )
