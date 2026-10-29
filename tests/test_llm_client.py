"""LLM 客户端：超时四维、请求契约、重试边界与宽松的响应解析。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from wechat_agent.llm.client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_POOL_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_WRITE_TIMEOUT,
    LLMClient,
    LLMError,
    RetryableLLMError,
)
from wechat_agent.logging import LOGGER_NAME

BASE_URL = "https://llm.example.com/v1"
API_KEY = "sk-test-key-not-real"
MODEL = "test-model"
MESSAGES = [
    {"role": "system", "content": "你是一个简洁的助手"},
    {"role": "user", "content": "在吗"},
]
OK_BODY = {
    "id": "chatcmpl-1",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "在的"}}],
}


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def sleeps() -> list[float]:
    return []


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


@pytest.fixture
async def client(sleeps: list[float]) -> AsyncIterator[LLMClient]:
    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    instance = LLMClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        sleep=record_sleep,
    )
    yield instance
    await instance.close()


@pytest.fixture
def endpoint() -> str:
    return f"{BASE_URL}/chat/completions"


async def test_timeout_sets_all_four_axes(client: LLMClient) -> None:
    assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT == 10.0
    assert client.timeout.read == DEFAULT_READ_TIMEOUT == 30.0
    assert client.timeout.write == DEFAULT_WRITE_TIMEOUT == 10.0
    assert client.timeout.pool == DEFAULT_POOL_TIMEOUT == 10.0


async def test_request_targets_chat_completions_with_bearer_auth(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(endpoint).mock(return_value=httpx.Response(200, json=OK_BODY))

    content = await client.complete(MESSAGES)

    assert content == "在的"
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "model": MODEL,
        "messages": MESSAGES,
        "stream": False,
    }


async def test_network_timeout_is_retried_then_raises_retryable(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(endpoint).mock(side_effect=httpx.ReadTimeout("read timeout"))

    with pytest.raises(RetryableLLMError) as excinfo:
        await client.complete(MESSAGES)

    assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)
    assert excinfo.value.attempts == 2
    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 0.75


async def test_429_is_retried_and_succeeds(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(endpoint).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    content = await client.complete(MESSAGES)

    assert content == "在的"
    assert route.call_count == 2
    assert len(sleeps) == 1


async def test_400_is_not_retried(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(endpoint).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
    )

    with pytest.raises(LLMError) as excinfo:
        await client.complete(MESSAGES)

    assert not isinstance(excinfo.value, RetryableLLMError)
    assert excinfo.value.status_code == 400
    assert route.call_count == 1
    assert sleeps == []


async def test_retry_limit_stops_after_configured_attempts(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(endpoint).mock(
        return_value=httpx.Response(503, json={"error": {"message": "busy"}})
    )

    with pytest.raises(RetryableLLMError) as excinfo:
        await client.complete(MESSAGES)

    assert excinfo.value.status_code == 503
    assert excinfo.value.attempts == 2
    assert route.call_count == 2
    assert len(sleeps) == 1


async def test_missing_content_returns_empty_string(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(endpoint).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant"}}]},
        )
    )

    assert await client.complete(MESSAGES) == ""


async def test_null_content_returns_empty_string(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(endpoint).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"content": None}}]},
        )
    )

    assert await client.complete(MESSAGES) == ""


async def test_multiple_choices_use_the_first(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(endpoint).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"index": 0, "message": {"content": "第一个"}},
                    {"index": 1, "message": {"content": "第二个"}},
                ]
            },
        )
    )

    assert await client.complete(MESSAGES) == "第一个"


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"index": 0}]},
        {"choices": [{"index": 0, "message": {"content": ["parts"]}}]},
        {"id": "no-choices"},
        [],
    ],
)
async def test_malformed_payloads_raise_llm_error(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    body: object,
) -> None:
    respx_mock.post(endpoint).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(LLMError) as excinfo:
        await client.complete(MESSAGES)

    assert not isinstance(excinfo.value, RetryableLLMError)


async def test_non_json_body_raises_llm_error(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
) -> None:
    respx_mock.post(endpoint).mock(return_value=httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(LLMError):
        await client.complete(MESSAGES)


async def test_empty_messages_are_rejected(client: LLMClient) -> None:
    with pytest.raises(ValueError):
        await client.complete([])


async def test_logs_omit_api_key_and_request_text(
    client: LLMClient,
    endpoint: str,
    respx_mock: respx.Router,
    logs: list[logging.LogRecord],
) -> None:
    respx_mock.post(endpoint).mock(return_value=httpx.Response(200, json=OK_BODY))

    await client.complete(MESSAGES)

    rendered = json.dumps(
        [{key: str(value) for key, value in record.__dict__.items()} for record in logs],
        ensure_ascii=False,
    )
    assert logs, "成功调用应留下一条日志"
    assert MODEL in rendered
    assert "status_code" in rendered
    assert "elapsed_ms" in rendered
    assert API_KEY not in rendered
    assert "在吗" not in rendered
    assert "你是一个简洁的助手" not in rendered
