"""接入层端到端：扫码登录、长轮询、归一化、去重、重登、退避与游标重启。

路径 1/2/3/5/6 的行为发生在 ILinkTransport 内部（协议、凭证、游标、归一化），
用 respx 严格 mock 真实适配器；路径 4 注入 FakeTransport，验证端口可替换。
所有测试都不访问真实网络，respx 未匹配的请求会直接失败。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fakes.fake_transport import FakeTransport
from pytest import MonkeyPatch

from wechat_agent.config import Settings, load_settings
from wechat_agent.wechat.dedup import SeenIdStore
from wechat_agent.wechat.ilink import ILinkTransport
from wechat_agent.wechat.models import MessagePart, NormalizedMessage, SessionExpired

BOT_TOKEN = "bot-token-for-integration"
UIN = 555555
TEXT_MESSAGE: dict[str, Any] = {
    "message_id": 1001,
    "from_user_id": "wxid_alice",
    "context_token": "ctx-1",
    "create_time": 1700000000,
    "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
}
SELF_MESSAGE: dict[str, Any] = {
    "message_id": 2002,
    "from_user_id": "wxid_bot_self",
    "message_type": 2,
    "item_list": [{"type": 1, "text_item": {"text": "自己的回复"}}],
}


class AccessLayerPipeline:
    """归一化之后的最小接入层管线：去重后交给处理函数（业务管线在 3.x）。"""

    def __init__(self, seen: SeenIdStore) -> None:
        self._seen = seen
        self.processed: list[NormalizedMessage] = []

    def feed(self, messages: tuple[NormalizedMessage, ...]) -> None:
        for message in messages:
            if self._seen.is_duplicate(message.msg_id):
                continue
            self.processed.append(message)


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
            poll_interval=0.0,
            qr_display=lambda _: None,
            **overrides,
        )
        created.append(transport)
        return transport

    yield factory
    for transport in created:
        await transport.close()


def _qrcode_response(token: str = "qr-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ret": 0,
            "qrcode": token,
            "qrcode_img_content": f"https://weixin.qq.com/qr/{token}",
        },
    )


def _confirmed_response(bot_token: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ret": 0,
            "status": "confirmed",
            "bot_token": bot_token,
            "ilink_bot_id": "bot-1",
            "ilink_user_id": "user-1",
        },
    )


def _updates_response(
    cursor: str,
    messages: list[dict[str, Any]] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={"ret": 0, "get_updates_buf": cursor, "msgs": messages or []},
    )


async def test_login_poll_normal_flow(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport()
    respx_mock.get(f"{api}/get_bot_qrcode").mock(return_value=_qrcode_response())
    respx_mock.get(f"{api}/get_qrcode_status").mock(return_value=_confirmed_response(BOT_TOKEN))
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=_updates_response("cursor-1", [TEXT_MESSAGE])
    )
    seen = SeenIdStore(data_dir)
    pipeline = AccessLayerPipeline(seen)

    await transport.login()
    result = await transport.poll(None)
    pipeline.feed(result.messages)

    credentials = json.loads(transport.credentials_path.read_text(encoding="utf-8"))
    assert credentials["bot_token"] == BOT_TOKEN
    assert [message.msg_id for message in pipeline.processed] == ["1001"]
    message = pipeline.processed[0]
    assert message.from_user_id == "wxid_alice"
    assert message.context_token == "ctx-1"
    assert message.parts[0].type == "text"
    assert message.parts[0].text == "你好"
    assert seen.contains("1001")
    assert result.cursor == "cursor-1"
    assert json.loads(transport.cursor_path.read_text(encoding="utf-8")) == {"cursor": "cursor-1"}


async def test_session_expired_relogin(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport()
    qr_route = respx_mock.get(f"{api}/get_bot_qrcode").mock(
        side_effect=[_qrcode_response("qr-1"), _qrcode_response("qr-2")]
    )
    status_route = respx_mock.get(f"{api}/get_qrcode_status").mock(
        side_effect=[_confirmed_response("token-1"), _confirmed_response("token-2")]
    )
    poll_route = respx_mock.post(f"{api}/getupdates").mock(
        side_effect=[
            httpx.Response(200, json={"ret": -1, "errcode": -14, "errmsg": "expired"}),
            _updates_response("cursor-2", [TEXT_MESSAGE]),
        ]
    )
    pipeline = AccessLayerPipeline(SeenIdStore(data_dir))

    await transport.login()
    first_credentials = json.loads(transport.credentials_path.read_text(encoding="utf-8"))
    assert first_credentials["bot_token"] == "token-1"

    with pytest.raises(SessionExpired):
        await transport.poll(None)

    transport.credentials_path.unlink()
    assert not transport.credentials_path.exists()

    await transport.login()
    second_credentials = json.loads(transport.credentials_path.read_text(encoding="utf-8"))
    assert second_credentials["bot_token"] == "token-2"

    result = await transport.poll(None)
    pipeline.feed(result.messages)

    assert [message.msg_id for message in pipeline.processed] == ["1001"]
    assert len(qr_route.calls) == 2
    assert len(status_route.calls) == 2
    assert len(poll_route.calls) == 2


async def test_network_backoff_recovers(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    transport = make_transport(bot_token=BOT_TOKEN, sleep=sleep)
    respx_mock.post(f"{api}/getupdates").mock(
        side_effect=[
            httpx.ConnectError("down-1"),
            httpx.ConnectError("down-2"),
            httpx.ConnectError("down-3"),
            _updates_response("cursor-1", [TEXT_MESSAGE]),
        ]
    )
    pipeline = AccessLayerPipeline(SeenIdStore(data_dir))

    result = await transport.poll_with_backoff(None)
    pipeline.feed(result.messages)

    assert delays == [2.0, 4.0, 8.0]
    assert result.cursor == "cursor-1"
    assert [message.msg_id for message in pipeline.processed] == ["1001"]


async def test_duplicate_msg_id_is_skipped(
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    transport = FakeTransport()
    pipeline = AccessLayerPipeline(SeenIdStore(data_dir))
    message = NormalizedMessage(
        msg_id="msg-dup",
        from_user_id="wxid_alice",
        parts=(MessagePart(type="text", text="重复消息"),),
    )

    await transport.login()
    transport.push(message)
    first = await transport.poll(None)
    pipeline.feed(first.messages)

    transport.push(message)
    second = await transport.poll(first.cursor)
    pipeline.feed(second.messages)

    assert [processed.msg_id for processed in pipeline.processed] == ["msg-dup"]
    assert second.messages == (message,)
    assert transport.login_count == 1


async def test_self_message_filtered(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    transport = make_transport(bot_token=BOT_TOKEN)
    respx_mock.post(f"{api}/getupdates").mock(
        return_value=_updates_response("cursor-1", [SELF_MESSAGE, TEXT_MESSAGE])
    )
    pipeline = AccessLayerPipeline(SeenIdStore(data_dir))

    result = await transport.poll(None)
    pipeline.feed(result.messages)

    assert [message.msg_id for message in pipeline.processed] == ["1001"]
    assert all(
        part.text != "自己的回复" for message in pipeline.processed for part in message.parts
    )


async def test_restart_uses_saved_cursor(
    make_transport: Callable[..., ILinkTransport],
    api: str,
    data_dir: Path,
    respx_mock: respx.Router,
) -> None:
    first_transport = make_transport(bot_token=BOT_TOKEN)
    route = respx_mock.post(f"{api}/getupdates").mock(
        side_effect=[
            _updates_response("cursor-new", [TEXT_MESSAGE]),
            _updates_response("cursor-2"),
        ]
    )
    pipeline = AccessLayerPipeline(SeenIdStore(data_dir))

    first = await first_transport.poll(None)
    pipeline.feed(first.messages)

    assert first.cursor == "cursor-new"
    assert json.loads(first_transport.cursor_path.read_text(encoding="utf-8")) == {
        "cursor": "cursor-new"
    }

    restarted = make_transport(bot_token=BOT_TOKEN)
    second = await restarted.poll(None)

    request_body = json.loads(route.calls[1].request.content)
    assert request_body["get_updates_buf"] == "cursor-new"
    assert second.cursor == "cursor-2"
    assert [message.msg_id for message in pipeline.processed] == ["1001"]
