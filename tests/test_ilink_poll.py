"""长轮询：读超时空轮询、游标持久化、指数退避与恢复。"""

from __future__ import annotations

import json
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pytest import MonkeyPatch

from wechat_agent.config import Settings, load_settings
from wechat_agent.wechat.ilink import (
    BACKOFF_INITIAL_SECONDS,
    BACKOFF_MAX_SECONDS,
    ILinkTransport,
)
from wechat_agent.wechat.models import SessionExpired, TransportError

UIN = 987654321
TEXT_MESSAGE: dict[str, Any] = {
    "message_id": 1001,
    "from_user_id": "wxid_alice",
    "context_token": "ctx-1",
    "create_time": 1700000000,
    "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
}


@pytest.fixture
def settings(monkeypatch: MonkeyPatch) -> Settings:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ILINK_BASE_URL", "https://test.example.com")
    return load_settings(env_file=None)


@pytest.fixture
def api(settings: Settings) -> str:
    return f"{settings.ilink_base_url}/ilink/bot"


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
            **overrides,
        )
        created.append(transport)
        return transport

    yield factory
    for transport in created:
        await transport.close()


def _record_delays(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep


def _getupdates(
    respx_mock: respx.Router,
    api: str,
    *results: object,
) -> respx.Route:
    return respx_mock.post(f"{api}/getupdates").mock(side_effect=list(results))


async def test_read_timeout_is_empty_poll_and_retries_same_cursor(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []
    transport = make_transport(sleep=_record_delays(delays))
    route = _getupdates(
        respx_mock,
        api,
        httpx.ReadTimeout("read timeout"),
        httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-1", "msgs": []},
        ),
    )

    first = await transport.poll_with_backoff("cursor-old")
    assert first.messages == ()
    assert first.cursor == "cursor-old"
    assert transport.consecutive_failures == 0

    second = await transport.poll_with_backoff(first.cursor)
    assert second.cursor == "cursor-1"
    assert delays == []
    assert transport.consecutive_failures == 0
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert bodies[0]["get_updates_buf"] == "cursor-old"
    assert bodies[1]["get_updates_buf"] == "cursor-old"


async def test_restart_uses_persisted_cursor(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    first_transport = make_transport()
    route = _getupdates(
        respx_mock,
        api,
        httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-new",
                "msgs": [TEXT_MESSAGE],
            },
        ),
        httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-2", "msgs": []},
        ),
    )

    first = await first_transport.poll(None)

    assert first.cursor == "cursor-new"
    assert len(first.messages) == 1
    message = first.messages[0]
    assert message.msg_id == "1001"
    assert message.from_user_id == "wxid_alice"
    assert message.context_token == "ctx-1"
    assert message.parts[0].type == "text"
    assert message.parts[0].text == "你好"

    path = first_transport.cursor_path
    assert path == data_dir / "cursor.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"cursor": "cursor-new"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert list(data_dir.glob(".cursor.*")) == []

    restarted = make_transport()
    await restarted.poll(None)

    body = json.loads(route.calls[1].request.content)
    assert body["get_updates_buf"] == "cursor-new"


async def test_backoff_sequence_is_2_4_8_16_30(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []
    transport = make_transport(sleep=_record_delays(delays))
    failures = [httpx.ConnectError(f"down-{index}") for index in range(5)]
    _getupdates(
        respx_mock,
        api,
        *failures,
        httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-1", "msgs": []},
        ),
    )

    result = await transport.poll_with_backoff(None)

    assert delays == [
        BACKOFF_INITIAL_SECONDS,
        4.0,
        8.0,
        16.0,
        BACKOFF_MAX_SECONDS,
    ]
    assert result.cursor == "cursor-1"
    assert transport.consecutive_failures == 0


async def test_backoff_resets_after_network_recovery(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []
    transport = make_transport(sleep=_record_delays(delays))
    _getupdates(
        respx_mock,
        api,
        httpx.ConnectError("down-1"),
        httpx.ConnectError("down-2"),
        httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-a", "msgs": []},
        ),
        httpx.ConnectError("down-3"),
        httpx.Response(
            200,
            json={"ret": 0, "get_updates_buf": "cursor-b", "msgs": []},
        ),
    )

    first = await transport.poll_with_backoff(None)
    second = await transport.poll_with_backoff(first.cursor)

    assert (first.cursor, second.cursor) == ("cursor-a", "cursor-b")
    assert delays == [BACKOFF_INITIAL_SECONDS, 4.0, BACKOFF_INITIAL_SECONDS]
    assert transport.consecutive_failures == 0


async def test_session_expired_is_not_retried(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []
    transport = make_transport(sleep=_record_delays(delays))
    _getupdates(
        respx_mock,
        api,
        httpx.Response(200, json={"ret": -1, "errcode": -14}),
    )

    with pytest.raises(SessionExpired):
        await transport.poll_with_backoff(None)

    assert delays == []
    assert transport.consecutive_failures == 0
    assert not transport.cursor_path.exists()


async def test_business_errcode_is_not_retried(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []
    transport = make_transport(sleep=_record_delays(delays))
    _getupdates(
        respx_mock,
        api,
        httpx.Response(200, json={"errcode": -2, "errmsg": "invalid param"}),
    )

    with pytest.raises(TransportError) as excinfo:
        await transport.poll_with_backoff(None)

    assert excinfo.value.errcode == -2
    assert delays == []
