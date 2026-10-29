"""iLink Bot HTTP 适配器：协议常量、认证头、超时与错误码翻译集中在这里。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.security import mask_secret
from wechat_agent.wechat.models import SessionExpired, TransportError

API_PREFIX: Final = "/ilink/bot"
AUTH_TYPE: Final = "ilink_bot_token"
CHANNEL_VERSION: Final = "1.0.0"
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
# getupdates 服务端最长挂起约 35 秒，读超时需留出余量
DEFAULT_READ_TIMEOUT: Final = 40.0
DEFAULT_QR_POLL_INTERVAL: Final = 2.0
CREDENTIALS_FILE_NAME: Final = "credentials.json"
CREDENTIALS_FILE_MODE: Final = 0o600
PRIVATE_DIR_MODE: Final = 0o700
MAX_QR_REFRESH: Final = 3
QR_TERMINAL_STATUSES: Final = frozenset({"confirmed", "expired"})
SESSION_EXPIRED_CODE: Final = -14

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


class ILinkTransport:
    """iLink Bot 的 HTTP 层封装。

    当前提供请求原语、扫码登录与错误翻译；轮询与发送在后续实现中组合
    这些原语，业务管线始终只接触归一化消息与 TransportError。
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
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bot_token = bot_token
        self._bot_type = bot_type
        self._uin = secrets.randbits(32) if uin is None else uin
        self._data_dir = Path(data_dir)
        self._credentials_path = self._data_dir / CREDENTIALS_FILE_NAME
        self._poll_interval = poll_interval
        self._qr_display = qr_display
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
        directory = self._credentials_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(PRIVATE_DIR_MODE)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{CREDENTIALS_FILE_NAME}.",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(credentials.to_payload(), handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, CREDENTIALS_FILE_MODE)
            os.replace(temp_name, self._credentials_path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise

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
