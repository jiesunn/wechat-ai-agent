"""组合层接线：长文本截断边界、摘要适配与运行时组装冒烟。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from wechat_agent.config import Settings, load_settings
from wechat_agent.guard.chain import FilterChain
from wechat_agent.guard.validate import DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.llm.client import LLMClient
from wechat_agent.main import _SummaryClient, _with_admitted_text, build_runtime
from wechat_agent.memory.summary import SummaryEngine
from wechat_agent.multimodal.router import ATTACHMENT_NOTICE, MultimodalRouter
from wechat_agent.pipeline import Pipeline
from wechat_agent.wechat.ilink import ILinkTransport
from wechat_agent.wechat.models import MessagePart, NormalizedMessage

CONTACT = "wxid_alice"
LONG_TEXT = "长" * 2500


class _FakeLLM:
    def __init__(self, reply: str = "摘要内容") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        self.calls.append([dict(message) for message in messages])
        return self.reply


def _settings(monkeypatch: MonkeyPatch, data_dir: Path) -> Settings:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ILINK_BASE_URL", "https://ilink.example.com")
    monkeypatch.setenv("ALLOW_FROM", CONTACT)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return load_settings(env_file=None)


def test_with_admitted_text_replaces_text_parts_once() -> None:
    message = NormalizedMessage(
        msg_id="m-1",
        from_user_id=CONTACT,
        context_token="ctx-1",
        parts=(
            MessagePart(type="text", text="原始第一段"),
            MessagePart(type="text", text="原始第二段"),
            MessagePart(type="image", media_size=2048),
        ),
    )

    admitted = _with_admitted_text(message, "截断后的文本")

    assert [part.type for part in admitted.parts] == ["text", "image"]
    assert admitted.parts[0].text == "截断后的文本"
    assert admitted.parts[1].media_size == 2048
    assert admitted.msg_id == "m-1"
    assert admitted.context_token == "ctx-1"


async def test_long_text_is_truncated_before_router() -> None:
    message = NormalizedMessage(
        msg_id="m-long",
        from_user_id=CONTACT,
        context_token="ctx-long",
        parts=(
            MessagePart(type="text", text=LONG_TEXT),
            MessagePart(type="image", media_size=1),
        ),
    )
    chain = FilterChain(
        whitelist=Whitelist(CONTACT),
        duplicate_suppressor=DuplicateSuppressor(),
        media_handler=None,
    )

    result = await chain.evaluate(message)

    assert result.allowed is True
    assert len(result.text) == 2000

    admitted = _with_admitted_text(message, result.text)
    captured: list[str] = []

    async def generate(text: str) -> str:
        captured.append(text)
        return "收下了"

    reply = await MultimodalRouter().route(admitted, generate_text_reply=generate)

    assert captured == [result.text]
    assert len(captured[0]) == 2000
    assert reply == f"收下了{ATTACHMENT_NOTICE}"


async def test_summary_client_maps_system_and_user() -> None:
    client = _FakeLLM()

    reply = await _SummaryClient(client).complete(system="系统指令", user="对话内容")

    assert reply == "摘要内容"
    assert client.calls == [
        [
            {"role": "system", "content": "系统指令"},
            {"role": "user", "content": "对话内容"},
        ]
    ]


async def test_build_runtime_wires_all_components(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(monkeypatch, tmp_path / "data")

    runtime = build_runtime(settings)
    try:
        assert isinstance(runtime.transport, ILinkTransport)
        assert isinstance(runtime.client, LLMClient)
        assert isinstance(runtime.pipeline, Pipeline)
        assert isinstance(runtime.summary, SummaryEngine)
        assert runtime.settings is settings
    finally:
        await runtime.transport.close()
        await runtime.client.close()
