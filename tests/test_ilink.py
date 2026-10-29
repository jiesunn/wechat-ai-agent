"""iLink HTTP 封装：地址拼接、认证头、base_info、超时与错误码映射。"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pytest import MonkeyPatch

from wechat_agent.config import Settings, load_settings
from wechat_agent.wechat.ilink import (
    API_PREFIX,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ILinkTransport,
)
from wechat_agent.wechat.models import SessionExpired, TransportError

BOT_TOKEN = "bot-token-for-tests"
UIN = 1234567890


def _expected_uin() -> str:
    return base64.b64encode(str(UIN).encode("ascii")).decode("ascii")


@pytest.fixture
def settings(monkeypatch: MonkeyPatch) -> Settings:
    """ILINK_BASE_URL 指向本地假域名，请求必须命中它才算通过。"""
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ILINK_BASE_URL", "https://test.example.com")
    return load_settings(env_file=None)


@pytest.fixture
def api(settings: Settings) -> str:
    return f"{settings.ilink_base_url}{API_PREFIX}"


@pytest.fixture
async def transport(settings: Settings) -> AsyncIterator[ILinkTransport]:
    instance = ILinkTransport(
        base_url=settings.ilink_base_url,
        bot_token=BOT_TOKEN,
        uin=UIN,
    )
    yield instance
    await instance.close()


async def test_get_uses_configured_base_url_and_auth_headers(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.get(f"{api}/get_bot_qrcode").mock(
        return_value=httpx.Response(200, json={"qrcode": "qr-token", "ret": 0})
    )

    payload = await transport.get("get_bot_qrcode", params={"bot_type": 3})

    assert payload["qrcode"] == "qr-token"
    request = route.calls.last.request
    assert request.headers["X-WECHAT-UIN"] == _expected_uin()
    assert request.headers["AuthorizationType"] == "ilink_bot_token"
    assert request.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    assert request.url.params["bot_type"] == "3"


async def test_requests_without_token_omit_authorization_headers(
    settings: Settings,
    api: str,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.get(f"{api}/get_bot_qrcode").mock(
        return_value=httpx.Response(200, json={"qrcode": "qr-token", "ret": 0})
    )
    transport = ILinkTransport(base_url=settings.ilink_base_url, uin=UIN)
    try:
        await transport.get("get_bot_qrcode")
    finally:
        await transport.close()

    request = route.calls.last.request
    assert "Authorization" not in request.headers
    assert "AuthorizationType" not in request.headers
    assert request.headers["X-WECHAT-UIN"] == _expected_uin()


async def test_post_includes_base_info(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(f"{api}/getupdates").mock(
        return_value=httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-1", "msgs": []},
        )
    )

    payload = await transport.post("getupdates", {"get_updates_buf": ""})

    assert payload["get_updates_buf"] == "cursor-1"
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "get_updates_buf": "",
        "base_info": {"channel_version": "1.0.0"},
    }


async def test_connect_and_read_timeouts_are_applied(transport: ILinkTransport) -> None:
    assert transport.timeout.connect == DEFAULT_CONNECT_TIMEOUT
    assert transport.timeout.read == DEFAULT_READ_TIMEOUT


async def test_read_timeout_becomes_transport_error_without_errcode(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(side_effect=httpx.ReadTimeout("read timeout"))

    with pytest.raises(TransportError) as excinfo:
        await transport.post("getupdates", {})

    assert excinfo.value.errcode is None
    assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)


async def test_connect_error_becomes_transport_error_without_errcode(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(TransportError) as excinfo:
        await transport.post("getupdates", {})

    assert excinfo.value.errcode is None
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


async def test_errcode_minus_14_maps_to_session_expired(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=httpx.Response(
            200,
            json={"ret": -1, "errcode": -14, "errmsg": "session timeout"},
        )
    )

    with pytest.raises(SessionExpired) as excinfo:
        await transport.post("getupdates", {})

    assert excinfo.value.errcode == -14
    assert isinstance(excinfo.value, TransportError)
    assert "session timeout" in str(excinfo.value)


async def test_other_errcode_becomes_transport_error_with_code(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=httpx.Response(200, json={"errcode": -2, "errmsg": "invalid param"})
    )

    with pytest.raises(TransportError) as excinfo:
        await transport.post("getupdates", {})

    assert not isinstance(excinfo.value, SessionExpired)
    assert excinfo.value.errcode == -2
    assert "invalid param" in str(excinfo.value)


async def test_ret_is_used_when_errcode_is_absent(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=httpx.Response(200, json={"ret": -1, "errmsg": "busy"})
    )

    with pytest.raises(TransportError) as excinfo:
        await transport.post("getupdates", {})

    assert excinfo.value.errcode == -1


async def test_http_error_status_becomes_transport_error(
    transport: ILinkTransport,
    api: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=httpx.Response(500, json={"errcode": -14})
    )

    with pytest.raises(TransportError) as excinfo:
        await transport.post("getupdates", {})

    assert not isinstance(excinfo.value, SessionExpired)
    assert excinfo.value.errcode is None
    assert "500" in str(excinfo.value)
