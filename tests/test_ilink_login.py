"""二维码登录：扫码确认落盘、凭证复用、损坏回退与过期上限。"""

from __future__ import annotations

import json
import logging
import stat
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pytest import LogCaptureFixture, MonkeyPatch

from wechat_agent.config import Settings, load_settings
from wechat_agent.logging import LOGGER_NAME
from wechat_agent.security import mask_secret
from wechat_agent.wechat.ilink import (
    API_PREFIX,
    CREDENTIALS_FILE_NAME,
    ILinkTransport,
)
from wechat_agent.wechat.models import SessionExpired

BOT_TOKEN = "bot-token-for-tests-0123456789"
UIN = 1234567890
QRCODE_CONTENT = "https://weixin.qq.com/qr/abc"


@pytest.fixture
def settings(monkeypatch: MonkeyPatch) -> Settings:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ILINK_BASE_URL", "https://test.example.com")
    return load_settings(env_file=None)


@pytest.fixture
def api(settings: Settings) -> str:
    return f"{settings.ilink_base_url}{API_PREFIX}"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
async def make_transport(
    settings: Settings,
    data_dir: Path,
) -> AsyncIterator[Callable[..., ILinkTransport]]:
    created: list[ILinkTransport] = []

    def factory(**overrides: Any) -> ILinkTransport:
        transport = ILinkTransport(
            base_url=settings.ilink_base_url,
            uin=UIN,
            data_dir=data_dir,
            poll_interval=0.0,
            **overrides,
        )
        created.append(transport)
        return transport

    yield factory
    for transport in created:
        await transport.close()


def _mock_qrcode(respx_mock: respx.Router, api: str) -> respx.Route:
    return respx_mock.get(f"{api}/get_bot_qrcode").mock(
        return_value=httpx.Response(
            200,
            json={"ret": 0, "qrcode": "qr-1", "qrcode_img_content": QRCODE_CONTENT},
        )
    )


def _confirmed_payload() -> dict[str, Any]:
    return {
        "ret": 0,
        "status": "confirmed",
        "bot_token": BOT_TOKEN,
        "ilink_bot_id": "bot-1",
        "ilink_user_id": "user-1",
    }


async def test_login_persists_credentials_and_masks_token_in_logs(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
    caplog: LogCaptureFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.getLogger(LOGGER_NAME), "propagate", True)
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    displayed: list[str] = []
    transport = make_transport(qr_display=displayed.append)
    qr_route = _mock_qrcode(respx_mock, api)
    status_route = respx_mock.get(f"{api}/get_qrcode_status").mock(
        side_effect=[
            httpx.Response(200, json={"ret": 0, "status": "wait"}),
            httpx.Response(200, json={"ret": 0, "status": "scaned"}),
            httpx.Response(200, json=_confirmed_payload()),
        ]
    )

    await transport.login()

    assert len(qr_route.calls) == 1
    assert len(status_route.calls) == 3
    assert displayed == [QRCODE_CONTENT]
    assert transport.headers["Authorization"] == f"Bearer {BOT_TOKEN}"

    path = transport.credentials_path
    assert path == data_dir / CREDENTIALS_FILE_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["bot_token"] == BOT_TOKEN
    assert payload["ilink_bot_id"] == "bot-1"
    assert payload["ilink_user_id"] == "user-1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    records = [r for r in caplog.records if r.getMessage().startswith("iLink 扫码登录成功")]
    assert len(records) == 1
    record = records[0]
    assert record.bot_token == mask_secret(BOT_TOKEN)
    assert record.ilink_bot_id == "bot-1"
    assert BOT_TOKEN not in caplog.text
    assert all(BOT_TOKEN not in str(value) for value in record.__dict__.values())


async def test_login_raises_session_expired_after_three_expired_qrcodes(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport(qr_display=lambda _: None)
    qr_route = respx_mock.get(f"{api}/get_bot_qrcode").mock(
        side_effect=[
            httpx.Response(200, json={"ret": 0, "qrcode": "qr-1"}),
            httpx.Response(200, json={"ret": 0, "qrcode": "qr-2"}),
            httpx.Response(200, json={"ret": 0, "qrcode": "qr-3"}),
        ]
    )
    status_route = respx_mock.get(f"{api}/get_qrcode_status").mock(
        return_value=httpx.Response(200, json={"ret": 0, "status": "expired"})
    )

    with pytest.raises(SessionExpired):
        await transport.login()

    assert len(qr_route.calls) == 3
    assert len(status_route.calls) == 3
    assert not transport.credentials_path.exists()


async def test_login_keeps_polling_through_wait_and_scaned_before_confirmed(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport(qr_display=lambda _: None)
    _mock_qrcode(respx_mock, api)
    status_route = respx_mock.get(f"{api}/get_qrcode_status").mock(
        side_effect=[
            httpx.Response(200, json={"ret": 0, "status": "wait"}),
            httpx.Response(200, json={"ret": 0, "status": "wait"}),
            httpx.Response(200, json={"ret": 0, "status": "scaned"}),
            httpx.Response(200, json={"ret": 0, "status": "scaned"}),
            httpx.Response(200, json=_confirmed_payload()),
        ]
    )

    await transport.login()

    assert len(status_route.calls) == 5
    assert transport.load_credentials() is not None


async def test_login_reuses_saved_credentials_without_qrcode(
    make_transport: Callable[..., ILinkTransport],
    respx_mock: respx.Router,
) -> None:
    transport = make_transport()
    transport.credentials_path.parent.mkdir(parents=True, exist_ok=True)
    transport.credentials_path.write_text(
        json.dumps({"bot_token": "saved-token", "ilink_bot_id": "bot-saved"}),
        encoding="utf-8",
    )

    await transport.login()

    assert transport.headers["Authorization"] == "Bearer saved-token"
    assert transport.load_credentials() is not None
    assert transport.load_credentials().ilink_bot_id == "bot-saved"


async def test_corrupted_credentials_fall_back_to_qr_login(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport(qr_display=lambda _: None)
    transport.credentials_path.parent.mkdir(parents=True, exist_ok=True)
    transport.credentials_path.write_text("{broken json", encoding="utf-8")
    _mock_qrcode(respx_mock, api)
    respx_mock.get(f"{api}/get_qrcode_status").mock(
        return_value=httpx.Response(200, json=_confirmed_payload())
    )

    await transport.login()

    credentials = transport.load_credentials()
    assert credentials is not None
    assert credentials.bot_token == BOT_TOKEN
