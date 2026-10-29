"""iLink Bot HTTP 适配器：协议常量、认证头、超时与错误码翻译集中在这里。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import httpx

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.security import mask_secret
from wechat_agent.wechat.cursor import CursorStore, atomic_write_text, ensure_private_dir
from wechat_agent.wechat.models import (
    KNOWN_PART_TYPES,
    MessagePart,
    NormalizedMessage,
    PartType,
    PollResult,
    SessionExpired,
    TransportError,
)
from wechat_agent.wechat.transport import WeChatReceiver

API_PREFIX: Final = "/ilink/bot"
AUTH_TYPE: Final = "ilink_bot_token"
CHANNEL_VERSION: Final = "1.0.0"
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
# getupdates 服务端最长挂起约 35 秒，读超时需留出余量
DEFAULT_READ_TIMEOUT: Final = 40.0
DEFAULT_QR_POLL_INTERVAL: Final = 2.0
CREDENTIALS_FILE_NAME: Final = "credentials.json"
MAX_QR_REFRESH: Final = 3
QR_TERMINAL_STATUSES: Final = frozenset({"confirmed", "expired"})
SESSION_EXPIRED_CODE: Final = -14
BACKOFF_INITIAL_SECONDS: Final = 2.0
BACKOFF_MAX_SECONDS: Final = 30.0
ITEM_TYPE_TO_PART: Final[dict[int, PartType]] = {
    1: "text",
    2: "image",
    3: "voice",
    4: "file",
    5: "video",
}

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True, slots=True, kw_only=True)
class Credentials:
    """扫码得到的运行态凭证；只落盘到 data/credentials.json（0600）。"""

    bot_token: str
    ilink_bot_id: str | None = None
    ilink_user_id: str | None = None
    saved_at: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {"bot_token": self.bot_token}
        if self.ilink_bot_id:
            payload["ilink_bot_id"] = self.ilink_bot_id
        if self.ilink_user_id:
            payload["ilink_user_id"] = self.ilink_user_id
        if self.saved_at:
            payload["saved_at"] = self.saved_at
        return payload


class ILinkTransport(WeChatReceiver):
    """iLink Bot 的 HTTP 层封装。

    当前提供请求原语、扫码登录与长轮询；发送由 ILinkSender 组合 `post` 原语完成，
    业务管线始终只接触归一化消息与 TransportError。
    """

    def __init__(
        self,
        *,
        base_url: str,
        bot_token: str | None = None,
        bot_type: int = 3,
        uin: int | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        data_dir: str | Path = "data",
        poll_interval: float = DEFAULT_QR_POLL_INTERVAL,
        qr_display: Callable[[str], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bot_token = bot_token
        self._bot_type = bot_type
        self._uin = secrets.randbits(32) if uin is None else uin
        self._data_dir = Path(data_dir)
        self._credentials_path = self._data_dir / CREDENTIALS_FILE_NAME
        self._cursor_store = CursorStore(self._data_dir)
        self._poll_interval = poll_interval
        self._qr_display = qr_display
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._consecutive_failures = 0
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=connect_timeout,
                pool=connect_timeout,
            )
        )

    @property
    def headers(self) -> dict[str, str]:
        """每个请求都带 `X-WECHAT-UIN`；拿到 bot_token 后才带认证头。"""
        headers = {"X-WECHAT-UIN": _encode_uin(self._uin)}
        if self._bot_token:
            headers["AuthorizationType"] = AUTH_TYPE
            headers["Authorization"] = f"Bearer {self._bot_token}"
        return headers

    @property
    def base_info(self) -> dict[str, str]:
        return {"channel_version": CHANNEL_VERSION}

    @property
    def timeout(self) -> httpx.Timeout:
        return self._client.timeout

    @property
    def credentials_path(self) -> Path:
        return self._credentials_path

    @property
    def cursor_path(self) -> Path:
        return self._cursor_store.path

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def login(self) -> None:
        """复用已存凭证；没有可用凭证时扫码登录，连续 3 次过期则放弃。"""
        credentials = self.load_credentials()
        if credentials is not None:
            self._bot_token = credentials.bot_token
            logger.info("iLink 复用已有凭证", extra={"ilink_bot_id": credentials.ilink_bot_id})
            return
        await self._login_with_qrcode()

    def load_credentials(self) -> Credentials | None:
        """读取本地凭证；文件缺失、损坏或字段缺失一律视为无凭证。"""
        try:
            payload = json.loads(self._credentials_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return _credentials_from_payload(payload)

    async def poll(self, cursor: str | None) -> PollResult:
        """一次 getupdates：读超时按空轮询返回；成功时立即持久化新游标。"""
        if cursor is None:
            cursor = self._cursor_store.load()
        try:
            payload = await self.post("getupdates", {"get_updates_buf": cursor or ""})
        except TransportError as error:
            if _is_read_timeout(error):
                logger.debug("iLink 长轮询读超时，视为空轮询")
                return PollResult(messages=(), cursor=cursor)
            raise
        next_cursor = payload.get("get_updates_buf")
        if isinstance(next_cursor, str) and next_cursor != cursor:
            # 先落盘再返回消息：崩溃最多重复最后一批，不会丢批
            self._cursor_store.save(next_cursor)
            cursor = next_cursor
        return PollResult(messages=_normalize_messages(payload.get("msgs")), cursor=cursor)

    async def poll_with_backoff(self, cursor: str | None) -> PollResult:
        """只在可重试的网络错误上退避；SessionExpired 与业务 errcode 直接上抛。"""
        while True:
            try:
                result = await self.poll(cursor)
            except SessionExpired:
                raise
            except TransportError as error:
                if error.errcode is not None:
                    raise
                self._consecutive_failures += 1
                delay = _backoff_delay(self._consecutive_failures)
                logger.warning(
                    "iLink 轮询失败，退避后重试",
                    extra={
                        "attempt": self._consecutive_failures,
                        "delay_seconds": delay,
                    },
                )
                await self._sleep(delay)
                continue
            self._consecutive_failures = 0
            return result

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """GET 一个 iLink 接口并返回解析后的 JSON 对象。"""
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST 一个 iLink 接口；`base_info` 由这里统一补齐。"""
        body = dict(payload)
        body.setdefault("base_info", self.base_info)
        return await self._request("POST", path, json_payload=body)

    async def close(self) -> None:
        """关闭自建连接池；外部注入的 client 由注入方负责关闭。"""
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def _login_with_qrcode(self) -> None:
        for attempt in range(1, MAX_QR_REFRESH + 1):
            qrcode_token, content = await self._fetch_qrcode()
            self._show_qrcode(content)
            status, payload = await self._poll_qrcode_status(qrcode_token)
            if status == "confirmed":
                credentials = self._credentials_from_status(payload)
                self._save_credentials(credentials)
                self._bot_token = credentials.bot_token
                logger.info(
                    "iLink 扫码登录成功",
                    extra={
                        "ilink_bot_id": credentials.ilink_bot_id,
                        "bot_token": mask_secret(credentials.bot_token),
                    },
                )
                return
            logger.warning(
                "二维码已过期，重新获取",
                extra={"attempt": attempt, "max_attempts": MAX_QR_REFRESH},
            )
        raise SessionExpired(f"二维码连续 {MAX_QR_REFRESH} 次过期，需人工重新登录")

    async def _fetch_qrcode(self) -> tuple[str, str]:
        payload = await self.get("get_bot_qrcode", params={"bot_type": self._bot_type})
        qrcode_token = payload.get("qrcode")
        if not isinstance(qrcode_token, str) or not qrcode_token:
            raise TransportError("iLink 未返回二维码标识")
        content = payload.get("qrcode_img_content") or payload.get("qrcode_content")
        if not isinstance(content, str) or not content:
            content = qrcode_token
        return qrcode_token, content

    async def _poll_qrcode_status(self, qrcode_token: str) -> tuple[str, dict[str, Any]]:
        while True:
            payload = await self.get("get_qrcode_status", params={"qrcode": qrcode_token})
            status = str(payload.get("status") or "").strip().lower()
            if status in QR_TERMINAL_STATUSES:
                return status, payload
            # wait / scaned：继续轮询，直到确认或过期
            await asyncio.sleep(self._poll_interval)

    def _show_qrcode(self, content: str) -> None:
        if self._qr_display is not None:
            self._qr_display(content)
            return
        print(f"请用微信扫描二维码完成登录：\n{content}")

    def _credentials_from_status(self, payload: Mapping[str, Any]) -> Credentials:
        credentials = _credentials_from_payload(payload)
        if credentials is None:
            raise TransportError("iLink 扫码已确认但未返回 bot_token")
        return Credentials(
            bot_token=credentials.bot_token,
            ilink_bot_id=credentials.ilink_bot_id,
            ilink_user_id=credentials.ilink_user_id,
            saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def _save_credentials(self, credentials: Credentials) -> None:
        try:
            self._write_credentials_atomically(credentials)
        except OSError as error:
            raise TransportError(f"保存凭证失败：{type(error).__name__}") from error

    def _write_credentials_atomically(self, credentials: Credentials) -> None:
        ensure_private_dir(self._data_dir)
        atomic_write_text(
            self._credentials_path,
            json.dumps(credentials.to_payload(), ensure_ascii=False),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                self._url(path),
                params=params,
                json=json_payload,
                headers=self.headers,
            )
        except httpx.HTTPError as error:
            # 网络超时与连接错误没有 errcode，上层据此判断是否可重试
            raise TransportError(f"iLink 请求失败：{type(error).__name__}") from error
        if response.status_code >= 400:
            raise TransportError(f"iLink 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise TransportError("iLink 响应不是合法 JSON") from error
        if not isinstance(payload, dict):
            raise TransportError("iLink 响应不是 JSON 对象")
        self._raise_for_error(payload)
        return payload

    def _url(self, path: str) -> str:
        return f"{self._base_url}{API_PREFIX}/{path.lstrip('/')}"

    def _raise_for_error(self, payload: Mapping[str, Any]) -> None:
        code = _error_code(payload)
        if code is None:
            return
        errmsg = payload.get("errmsg") or payload.get("err_msg") or ""
        detail = f"：{errmsg}" if errmsg else ""
        if code == SESSION_EXPIRED_CODE:
            raise SessionExpired(f"iLink 会话已过期（errcode {code}）{detail}", errcode=code)
        raise TransportError(f"iLink 返回错误 errcode={code}{detail}", errcode=code)


def _credentials_from_payload(payload: Mapping[str, Any]) -> Credentials | None:
    bot_token = payload.get("bot_token")
    if not isinstance(bot_token, str) or not bot_token.strip():
        return None
    return Credentials(
        bot_token=bot_token,
        ilink_bot_id=_optional_str(payload.get("ilink_bot_id")),
        ilink_user_id=_optional_str(payload.get("ilink_user_id")),
        saved_at=_optional_str(payload.get("saved_at")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _encode_uin(uin: int) -> str:
    """X-WECHAT-UIN 是 uint32 十进制字符串的 base64。"""
    return base64.b64encode(str(uin).encode("ascii")).decode("ascii")


def _error_code(payload: Mapping[str, Any]) -> int | None:
    """优先 errcode，兼容只有 ret 的响应；0 或缺失都视为成功。"""
    for key in ("errcode", "ret"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return value
    return None


def _is_read_timeout(error: TransportError) -> bool:
    """只有本地读超时算空轮询；连接超时与其他网络错误仍走退避。"""
    return isinstance(error.__cause__, httpx.ReadTimeout)


def _backoff_delay(failures: int) -> float:
    return min(BACKOFF_INITIAL_SECONDS * (2 ** (failures - 1)), BACKOFF_MAX_SECONDS)


def _normalize_messages(value: object) -> tuple[NormalizedMessage, ...]:
    if not isinstance(value, list):
        return ()
    messages = (_normalize_message(item) for item in value if isinstance(item, Mapping))
    return tuple(message for message in messages if message is not None)


def _normalize_message(raw: Mapping[str, Any]) -> NormalizedMessage | None:
    raw_id = raw.get("msg_id") or raw.get("message_id")
    if raw.get("message_type") in (2, "2"):
        logger.debug(
            "跳过 iLink 自身消息",
            extra={
                "msg_id": str(raw_id) if raw_id is not None else "",
                "reason": "message_type=2",
            },
        )
        return None
    from_user_id = raw.get("from_user_id")
    if raw_id is None or raw_id == "" or not isinstance(from_user_id, str) or not from_user_id:
        logger.warning("iLink 消息缺少 id 或发送者，已跳过")
        return None
    return NormalizedMessage(
        msg_id=str(raw_id),
        from_user_id=from_user_id,
        context_token=_optional_str(raw.get("context_token")),
        received_at=_received_at(raw.get("create_time")),
        parts=_parts_from_items(raw.get("item_list")),
    )


def _parts_from_items(value: object) -> tuple[MessagePart, ...]:
    if not isinstance(value, list):
        return ()
    parts: list[MessagePart] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        part_type = _item_part_type(item.get("type"))
        text = None
        if part_type == "text":
            text_item = item.get("text_item")
            if isinstance(text_item, Mapping):
                text = _optional_str(text_item.get("text"))
        parts.append(MessagePart(type=part_type, text=text, media_size=_media_size(item)))
    return tuple(parts)


def _item_part_type(value: object) -> PartType:
    if isinstance(value, int) and not isinstance(value, bool):
        return ITEM_TYPE_TO_PART.get(value, "unknown")
    if isinstance(value, str) and value.strip().lower() in KNOWN_PART_TYPES:
        return cast(PartType, value.strip().lower())
    return "unknown"


def _media_size(item: Mapping[str, Any]) -> int | None:
    for key in ("media_size", "file_size"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for item_key in ("image_item", "voice_item", "file_item", "video_item"):
        nested = item.get(item_key)
        if not isinstance(nested, Mapping):
            continue
        for key in ("media_size", "file_size"):
            value = nested.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        media = nested.get("media")
        if isinstance(media, Mapping):
            value = media.get("file_size")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _received_at(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10**11 else value
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now(UTC)
