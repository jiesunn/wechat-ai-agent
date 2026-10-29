"""iLink 发送端：按分片调用 sendmessage，失败即停止后续分片。"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Mapping
from typing import Any, Final, Protocol

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.wechat.models import TransportError
from wechat_agent.wechat.splitter import DEFAULT_LIMIT, split_text

SEND_MESSAGE_PATH: Final = "sendmessage"
BOT_MESSAGE_TYPE: Final = 2
MESSAGE_STATE_FINISH: Final = 2
TEXT_ITEM_TYPE: Final = 1
CONTACT_HASH_LENGTH: Final = 12

logger = logging.getLogger(LOGGER_NAME)


class SendMessageTransport(Protocol):
    """发送端只依赖 `post`，测试注入记录型替身即可。"""

    async def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]: ...


def contact_hash(contact_id: str) -> str:
    """日志里用哈希标识联系人，避免出现原始 wxid。"""
    return hashlib.sha256(contact_id.encode("utf-8")).hexdigest()[:CONTACT_HASH_LENGTH]


class ILinkSender:
    def __init__(self, transport: SendMessageTransport, *, limit: int = DEFAULT_LIMIT) -> None:
        self._transport = transport
        self._limit = limit

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
        msg_id: str = "",
    ) -> bool:
        """发送文本；缺 context_token 或任一分片失败都返回 False，不抛异常。"""
        if not context_token:
            logger.warning(
                "缺少 context_token，跳过发送",
                extra={"msg_id": msg_id, "reason": "missing_context_token"},
            )
            return False

        fragments = split_text(text, self._limit)
        if not fragments:
            logger.warning(
                "待发送文本为空，跳过发送",
                extra={"msg_id": msg_id, "reason": "empty_text"},
            )
            return False

        total = len(fragments)
        target_hash = contact_hash(contact_id)
        for index, fragment in enumerate(fragments, start=1):
            log_fields = {
                "msg_id": msg_id,
                "fragment_index": index,
                "fragment_total": total,
                "contact_hash": target_hash,
            }
            try:
                await self._transport.post(
                    SEND_MESSAGE_PATH,
                    self._build_payload(contact_id, context_token, fragment),
                )
            except TransportError:
                logger.warning("iLink 消息分片发送失败", extra=log_fields)
                return False
            logger.info("iLink 消息分片发送成功", extra=log_fields)
        return True

    def _build_payload(
        self,
        contact_id: str,
        context_token: str,
        fragment: str,
    ) -> dict[str, Any]:
        return {
            "msg": {
                "to_user_id": contact_id,
                "client_id": secrets.token_hex(8),
                "message_type": BOT_MESSAGE_TYPE,
                "message_state": MESSAGE_STATE_FINISH,
                "context_token": context_token,
                "item_list": [{"type": TEXT_ITEM_TYPE, "text_item": {"text": fragment}}],
            }
        }
