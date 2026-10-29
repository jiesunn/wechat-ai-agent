"""组合层接线：长文本截断边界、摘要适配与运行时组装冒烟。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

import wechat_agent.main as main_module
from wechat_agent.config import Settings, load_settings
from wechat_agent.guard.chain import FilterChain
from wechat_agent.guard.validate import DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.llm.client import LLMClient
from wechat_agent.logging import LOGGER_NAME
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


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def logs() -> Iterator[list[logging.LogRecord]]:
    target = logging.getLogger(LOGGER_NAME)
    handler = _RecordingHandler()
    previous_level = target.level
    previous_propagate = target.propagate
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    target.propagate = False
    yield handler.records
    target.removeHandler(handler)
    target.setLevel(previous_level)
    target.propagate = previous_propagate


class _FakePipeline:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeClient:
    async def close(self) -> None:
        return


class _FakeTransport:
    def __init__(self, *, stopped: bool, state: dict[str, bool]) -> None:
        self._stopped = stopped
        self._state = state
        self.closed = False

    async def login(self) -> None:
        if self._stopped:
            self._state["stopped"] = True
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    async def close(self) -> None:
        self.closed = True


async def _run_with_login_failure(
    monkeypatch: MonkeyPatch,
    settings: Settings,
    *,
    stopped: bool,
) -> int:
    state = {"stopped": stopped}
    runtime = SimpleNamespace(
        transport=_FakeTransport(stopped=stopped, state=state),
        pipeline=_FakePipeline(),
        client=_FakeClient(),
    )
    monkeypatch.setattr(main_module, "build_runtime", lambda _: runtime)

    class _Controller:
        def __init__(self, callback: Any) -> None:
            self._callback = callback

        def install(self) -> None:
            return

        def uninstall(self) -> None:
            return

        @property
        def stop_requested(self) -> bool:
            return state["stopped"]

        async def wait(self) -> None:
            return

    monkeypatch.setattr(main_module, "ShutdownController", _Controller)
    return await main_module._run(settings)


async def test_run_returns_zero_when_shutdown_requested_during_login(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    logs: list[logging.LogRecord],
) -> None:
    settings = _settings(monkeypatch, tmp_path / "data")

    code = await _run_with_login_failure(monkeypatch, settings, stopped=True)

    assert code == 0
    stop_records = [record for record in logs if "按正常退出处理" in record.getMessage()]
    assert len(stop_records) == 1
    assert stop_records[0].error_type == "RuntimeError"
    assert stop_records[0].exc_info is None
    assert all(record.exc_info is None for record in logs)


async def test_run_returns_one_for_runtime_error_without_shutdown(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    logs: list[logging.LogRecord],
) -> None:
    settings = _settings(monkeypatch, tmp_path / "data")

    code = await _run_with_login_failure(monkeypatch, settings, stopped=False)

    assert code == 1
    error_records = [record for record in logs if record.getMessage() == "运行时异常退出"]
    assert len(error_records) == 1
    assert error_records[0].error_type == "RuntimeError"
    assert error_records[0].exc_info is not None
