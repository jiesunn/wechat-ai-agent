"""端到端集成：六条主路径从消息进入走到回复发出或静默丢弃。

入站用 FakeTransport（路径 5 用真实 ILinkTransport，HTTP 边界由 respx 接管），
出站用记录型发送端口；LLM 统一走真实 LLMClient，HTTP 由 respx mock。
每条路径使用 tmp_path 下的独立 data 目录，测试之间不共享任何磁盘状态。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fakes.fake_transport import FakeTransport
from pytest import MonkeyPatch

from wechat_agent.config import (
    DEFAULT_FALLBACK_REPLY_TEXT,
    DEFAULT_UNSUPPORTED_MEDIA_REPLY,
    Settings,
    load_settings,
)
from wechat_agent.guard.chain import FilterChain
from wechat_agent.guard.validate import DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.llm.client import LLMClient
from wechat_agent.llm.postprocess import generate_reply
from wechat_agent.llm.prompt import build_reply_messages
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import MemoryStore
from wechat_agent.multimodal.router import MultimodalRouter
from wechat_agent.pipeline import Pipeline
from wechat_agent.wechat.dedup import SeenIdStore
from wechat_agent.wechat.ilink import ILinkTransport
from wechat_agent.wechat.models import MessagePart, NormalizedMessage

CONTACT = "wxid_alice"
STRANGER = "wxid_stranger"
USER_TEXT = "你好呀"
REPLY_TEXT = "你好，有什么可以帮你？"
BOT_TOKEN = "bot-token-for-integration"
UIN = 555555
RAW_TEXT_MESSAGE: dict[str, Any] = {
    "message_id": 1001,
    "from_user_id": CONTACT,
    "context_token": "ctx-1",
    "create_time": 1700000000,
    "item_list": [{"type": 1, "text_item": {"text": USER_TEXT}}],
}


class _RecordingSender:
    """出站端口替身：记录发出的每条文本，不触达网络。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        self.sent.append((contact_id, text, context_token))
        return True


class _MemorySender:
    """发送成功后写 assistant 记忆，等价于组合层对 4.2 的接线。"""

    def __init__(self, inner: _RecordingSender, short_term: ShortTermMemory) -> None:
        self._inner = inner
        self._short_term = short_term

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        sent = await self._inner.send_text(contact_id, text, context_token=context_token)
        if sent:
            self._short_term.append_assistant(contact_id, text, sent=True)
        return sent


class _CountingClient(LLMClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages: Sequence[Mapping[str, Any]]) -> str:
        self.calls.append([dict(message) for message in messages])
        return await super().complete(messages)


class _MediaAdapter:
    """3.3 的 async MediaHandler 端口：交给 6.3 router 取话术后由发送端口发出。"""

    def __init__(self, router: MultimodalRouter, sender: _RecordingSender) -> None:
        self._router = router
        self._sender = sender
        self.handled: list[str] = []

    async def handle(self, message: NormalizedMessage) -> None:
        async def unexpected(_: str) -> str | None:
            raise AssertionError("纯媒体不应进入文本生成器")

        reply = await self._router.route(message, generate_text_reply=unexpected)
        if reply:
            await self._sender.send_text(
                message.from_user_id,
                reply,
                context_token=message.context_token,
            )
        self.handled.append(message.msg_id)


class Harness:
    """把 2.x–6.x 的模块按生产接线方式组装起来，全部依赖注入、无全局状态。"""

    def __init__(self, settings: Settings, data_dir: Path) -> None:
        self.settings = settings
        self.store = MemoryStore(data_dir)
        self.short_term = ShortTermMemory(self.store, max_entries=settings.short_memory_size)
        self.sender = _RecordingSender()
        self.client = _CountingClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            max_retries=settings.llm_max_retries,
            read_timeout=settings.llm_timeout_seconds,
            sleep=_no_sleep,
        )
        self.router = MultimodalRouter()
        self.media = _MediaAdapter(self.router, self.sender)
        self.pipeline = Pipeline(
            chain=FilterChain(
                whitelist=Whitelist(settings.allow_from),
                duplicate_suppressor=DuplicateSuppressor(
                    window_seconds=settings.repeat_window_seconds
                ),
                media_handler=self.media,
                max_chars=settings.max_input_chars,
            ),
            sender=_MemorySender(self.sender, self.short_term),
            reply_generator=self._reply_generator,
        )

    async def _reply_generator(
        self,
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str | None:
        async def generate_text_reply(admitted_text: str) -> str | None:
            self.short_term.append_user(message.from_user_id, admitted_text)
            history = self.short_term.entries(message.from_user_id)[:-1]
            messages = build_reply_messages(
                system_prompt=self.settings.system_prompt,
                summary=self.store.load(message.from_user_id).summary,
                history=history,
                user_text=admitted_text,
            )
            return await generate_reply(
                lambda: self.client.complete(messages),
                fallback_enabled=self.settings.fallback_reply_enabled,
                fallback_text=self.settings.fallback_reply_text,
            )

        return await self.router.route(message, generate_text_reply=generate_text_reply)


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def settings(monkeypatch: MonkeyPatch) -> Settings:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.com")
    monkeypatch.setenv("ALLOW_FROM", CONTACT)
    return load_settings(env_file=None)


@pytest.fixture
async def harness(settings: Settings, tmp_path: Path) -> AsyncIterator[Harness]:
    app = Harness(settings, tmp_path / "data")
    yield app
    await app.pipeline.stop()
    await app.client.close()


def _text_message(
    *,
    msg_id: str = "m-1",
    contact: str = CONTACT,
    text: str = USER_TEXT,
    context_token: str | None = "ctx-1",
) -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        from_user_id=contact,
        context_token=context_token,
        parts=(MessagePart(type="text", text=text),),
    )


def _image_message(*, msg_id: str = "m-img") -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        from_user_id=CONTACT,
        context_token="ctx-img",
        parts=(MessagePart(type="image", media_size=2048),),
    )


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
    )


async def _feed(app: Harness, transport: FakeTransport, seen: SeenIdStore) -> None:
    result = await transport.poll(None)
    for message in result.messages:
        if seen.is_duplicate(message.msg_id):
            continue
        assert app.pipeline.submit(message)


async def _settle(pipeline: Pipeline) -> None:
    """让 dispatcher 取走队列消息，再等在途任务完成。"""
    for _ in range(5):
        await asyncio.sleep(0)
    await pipeline.stop()


def _roles(entries: Sequence[Any]) -> list[tuple[str, str]]:
    return [(entry.role, entry.content) for entry in entries]


async def test_text_loop_end_to_end(
    harness: Harness,
    settings: Settings,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(f"{settings.llm_base_url}/chat/completions").mock(
        return_value=_chat_response(REPLY_TEXT)
    )
    transport = FakeTransport()
    seen = SeenIdStore(tmp_path / "data")
    transport.push(_text_message())
    harness.pipeline.start()

    await transport.login()
    await _feed(harness, transport, seen)
    await _settle(harness.pipeline)

    assert route.call_count == 1
    assert len(harness.client.calls) == 1
    assert harness.client.calls[0][-1] == {"role": "user", "content": USER_TEXT}
    assert harness.sender.sent == [(CONTACT, REPLY_TEXT, "ctx-1")]
    assert _roles(harness.short_term.entries(CONTACT)) == [
        ("user", USER_TEXT),
        ("assistant", REPLY_TEXT),
    ]
    reloaded = ShortTermMemory(MemoryStore(tmp_path / "data")).entries(CONTACT)
    assert _roles(reloaded) == [("user", USER_TEXT), ("assistant", REPLY_TEXT)]


async def test_whitelist_rejection_silent(
    harness: Harness,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    transport = FakeTransport()
    seen = SeenIdStore(tmp_path / "data")
    transport.push(_text_message(msg_id="m-stranger", contact=STRANGER))
    harness.pipeline.start()

    await transport.login()
    await _feed(harness, transport, seen)
    await _settle(harness.pipeline)

    assert harness.client.calls == []
    assert harness.sender.sent == []
    assert harness.short_term.entries(STRANGER) == ()
    assert len(respx_mock.calls) == 0


async def test_duplicate_msg_id_is_skipped(
    harness: Harness,
    settings: Settings,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(f"{settings.llm_base_url}/chat/completions").mock(
        return_value=_chat_response(REPLY_TEXT)
    )
    transport = FakeTransport()
    seen = SeenIdStore(tmp_path / "data")
    message = _text_message(msg_id="m-dup")
    harness.pipeline.start()

    await transport.login()
    transport.push(message)
    await _feed(harness, transport, seen)
    transport.push(message)
    await _feed(harness, transport, seen)
    await _settle(harness.pipeline)

    assert seen.contains("m-dup")
    assert route.call_count == 1
    assert len(harness.client.calls) == 1
    assert harness.sender.sent == [(CONTACT, REPLY_TEXT, "ctx-1")]


async def test_image_falls_back_without_llm(
    harness: Harness,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    transport = FakeTransport()
    seen = SeenIdStore(tmp_path / "data")
    transport.push(_image_message())
    harness.pipeline.start()

    await transport.login()
    await _feed(harness, transport, seen)
    await _settle(harness.pipeline)

    assert harness.media.handled == ["m-img"]
    assert harness.client.calls == []
    assert harness.sender.sent == [(CONTACT, DEFAULT_UNSUPPORTED_MEDIA_REPLY, "ctx-img")]
    assert len(respx_mock.calls) == 0


async def test_restart_restores_cursor_and_memory(
    harness: Harness,
    settings: Settings,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    api = f"{settings.ilink_base_url}/ilink/bot"
    route = respx_mock.post(f"{api}/getupdates").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"ret": 0, "get_updates_buf": "cursor-new", "msgs": [RAW_TEXT_MESSAGE]},
            ),
            httpx.Response(200, json={"ret": 0, "get_updates_buf": "cursor-2", "msgs": []}),
        ]
    )
    respx_mock.post(f"{settings.llm_base_url}/chat/completions").mock(
        return_value=_chat_response(REPLY_TEXT)
    )
    data_dir = tmp_path / "data"
    harness.pipeline.start()

    first = ILinkTransport(
        base_url=settings.ilink_base_url,
        bot_token=BOT_TOKEN,
        uin=UIN,
        data_dir=data_dir,
    )
    try:
        result = await first.poll(None)
        for message in result.messages:
            assert harness.pipeline.submit(message)
        await _settle(harness.pipeline)

        assert json.loads(first.cursor_path.read_text(encoding="utf-8")) == {"cursor": "cursor-new"}
    finally:
        await first.close()

    restarted = ILinkTransport(
        base_url=settings.ilink_base_url,
        bot_token=BOT_TOKEN,
        uin=UIN,
        data_dir=data_dir,
    )
    try:
        second = await restarted.poll(None)
    finally:
        await restarted.close()

    request_body = json.loads(route.calls[1].request.content)
    assert request_body["get_updates_buf"] == "cursor-new"
    assert second.cursor == "cursor-2"

    reloaded = ShortTermMemory(MemoryStore(data_dir)).entries(CONTACT)
    assert _roles(reloaded) == [("user", USER_TEXT), ("assistant", REPLY_TEXT)]


async def test_llm_timeout_uses_fallback(
    harness: Harness,
    settings: Settings,
    tmp_path: Path,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(f"{settings.llm_base_url}/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    transport = FakeTransport()
    seen = SeenIdStore(tmp_path / "data")
    transport.push(_text_message())
    harness.pipeline.start()

    await transport.login()
    await _feed(harness, transport, seen)
    await _settle(harness.pipeline)

    assert route.call_count == 2
    assert len(harness.client.calls) == 1
    assert harness.sender.sent == [(CONTACT, DEFAULT_FALLBACK_REPLY_TEXT, "ctx-1")]
    assert _roles(harness.short_term.entries(CONTACT)) == [
        ("user", USER_TEXT),
        ("assistant", DEFAULT_FALLBACK_REPLY_TEXT),
    ]
