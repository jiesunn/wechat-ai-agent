"""归一化消息模型：上层管线只认识这些字段，不感知 iLink 的原始响应结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, cast

PartType = Literal["text", "image", "voice", "file", "video", "unknown"]

KNOWN_PART_TYPES: Final[frozenset[str]] = frozenset({"text", "image", "voice", "file", "video"})


def normalize_part_type(raw_type: object) -> PartType:
    """把来源类型映射为 PartType；无法识别的类型统一归到 unknown，不抛异常。"""
    if isinstance(raw_type, str) and raw_type in KNOWN_PART_TYPES:
        return cast(PartType, raw_type)
    return "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagePart:
    """消息中的一段内容；首版不下载媒体，只保留类型、文本与媒体大小。"""

    type: PartType
    text: str | None = None
    media_size: int | None = None

    def __post_init__(self) -> None:
        normalized = normalize_part_type(self.type)
        if normalized != self.type:
            object.__setattr__(self, "type", normalized)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedMessage:
    """一条入站消息的归一化视图；调用方拿到的永远是这一层，不含原始响应。"""

    msg_id: str
    from_user_id: str
    context_token: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    parts: tuple[MessagePart, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.parts, tuple):
            object.__setattr__(self, "parts", tuple(self.parts))


@dataclass(frozen=True, slots=True, kw_only=True)
class PollResult:
    """一次轮询的结果：本次收到的消息与该次响应携带的最新游标。"""

    messages: tuple[NormalizedMessage, ...] = ()
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            object.__setattr__(self, "messages", tuple(self.messages))


class TransportError(Exception):
    """接入层统一错误：网络失败、协议错误与远端拒绝都归到这里。

    errcode 只由适配层填写（iLink 的非零错误码），不进入归一化消息与业务管线。
    """

    def __init__(self, message: str = "", *, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


class SessionExpired(TransportError):
    """会话失效（iLink `errcode = -14`），上层捕获后走重新登录流程。"""
