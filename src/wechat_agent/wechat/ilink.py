"""iLink Bot HTTP 适配器：协议常量、认证头、超时与错误码翻译集中在这里。"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Mapping
from typing import Any, Final

import httpx

from wechat_agent.wechat.models import SessionExpired, TransportError

API_PREFIX: Final = "/ilink/bot"
AUTH_TYPE: Final = "ilink_bot_token"
CHANNEL_VERSION: Final = "1.0.0"
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
# getupdates 服务端最长挂起约 35 秒，读超时需留出余量
DEFAULT_READ_TIMEOUT: Final = 40.0
SESSION_EXPIRED_CODE: Final = -14


class ILinkTransport:
    """iLink Bot 的 HTTP 层封装。

    当前只提供请求原语、请求头与错误翻译；登录、轮询与发送在后续实现中
    组合这些原语，业务管线始终只接触归一化消息与 TransportError。
    """

    def __init__(
        self,
        *,
        base_url: str,
        bot_token: str | None = None,
        uin: int | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bot_token = bot_token
        self._uin = secrets.randbits(32) if uin is None else uin
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
