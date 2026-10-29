"""LLM 客户端：直连 OpenAI 兼容接口，超时、重试与错误分类集中在这里。"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

import httpx

from wechat_agent.logging import LOGGER_NAME

CHAT_COMPLETIONS_PATH: Final = "/chat/completions"
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
DEFAULT_READ_TIMEOUT: Final = 30.0
DEFAULT_WRITE_TIMEOUT: Final = 10.0
DEFAULT_POOL_TIMEOUT: Final = 10.0
DEFAULT_MAX_RETRIES: Final = 1
BACKOFF_INITIAL_SECONDS: Final = 0.5
BACKOFF_MAX_SECONDS: Final = 8.0
BACKOFF_JITTER_RATIO: Final = 0.5
RETRYABLE_STATUS_CODES: Final = frozenset({429})

logger = logging.getLogger(LOGGER_NAME)


class LLMError(Exception):
    """LLM 调用失败：不可重试的错误，或可重试错误在重试耗尽后抛出。"""

    def __init__(self, message: str, *, status_code: int | None = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


class RetryableLLMError(LLMError):
    """429 / 5xx / 网络异常重试耗尽后的错误，上层据此决定是否走兜底话术。"""


class LLMClient:
    """一次补全调用：POST `{base_url}/chat/completions`。

    只有 429、5xx 与网络异常重试，4xx 直接失败；重试延迟为指数退避加 jitter。
    ``complete`` 的响应解析有意宽松：``content`` 缺失或为 ``None`` 视为模型空输出，
    返回空字符串交由上层降级；``choices`` / ``message`` 结构缺失属于上游违约，抛
    ``LLMError``，不会让 ``KeyError`` 冒泡。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        pool_timeout: float = DEFAULT_POOL_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_initial: float = BACKOFF_INITIAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        normalized_key = api_key.strip()
        normalized_model = model.strip()
        if not normalized_url:
            raise ValueError("base_url 不能为空")
        if not normalized_key:
            raise ValueError("api_key 不能为空")
        if not normalized_model:
            raise ValueError("model 不能为空")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负数")
        if backoff_initial < 0:
            raise ValueError("backoff_initial 不能为负数")
        self._base_url = normalized_url
        self._api_key = normalized_key
        self._model = normalized_model
        self._max_retries = max_retries
        self._backoff_initial = backoff_initial
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=write_timeout,
                pool=pool_timeout,
            )
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def timeout(self) -> httpx.Timeout:
        return self._client.timeout

    async def complete(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """发送一次补全请求，返回首个 choice 的文本内容。"""
        if not messages:
            raise ValueError("messages 不能为空")
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "stream": False,
        }
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                response = await self._client.post(
                    self._url(),
                    json=body,
                    headers=self._headers,
                )
            except httpx.HTTPError as error:
                self._log_network_error(attempt, attempts, started, error)
                if attempt >= attempts:
                    raise RetryableLLMError(
                        f"LLM 请求失败：{type(error).__name__}",
                        attempts=attempt,
                    ) from error
                await self._sleep(self._backoff_delay(attempt))
                continue

            elapsed_ms = _elapsed_ms(started)
            status_code = response.status_code
            if 200 <= status_code < 300:
                logger.info(
                    "LLM 调用成功",
                    extra={
                        "model": self._model,
                        "status_code": status_code,
                        "elapsed_ms": elapsed_ms,
                        "attempt": attempt,
                    },
                )
                return _extract_content(response)

            retryable = _is_retryable_status(status_code)
            logger.warning(
                "LLM 调用失败",
                extra={
                    "model": self._model,
                    "status_code": status_code,
                    "elapsed_ms": elapsed_ms,
                    "attempt": attempt,
                    "retryable": retryable,
                },
            )
            if retryable and attempt < attempts:
                await self._sleep(self._backoff_delay(attempt))
                continue
            error_type = RetryableLLMError if retryable else LLMError
            raise error_type(
                f"LLM 返回 HTTP {status_code}",
                status_code=status_code,
                attempts=attempt,
            )

        # 循环体内的分支已覆盖所有出口，这里仅用于类型收窄
        raise LLMError("LLM 调用未产生结果")

    async def close(self) -> None:
        """关闭自建连接池；外部注入的 client 由注入方负责关闭。"""
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _url(self) -> str:
        return f"{self._base_url}{CHAT_COMPLETIONS_PATH}"

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避加 jitter：第 n 次失败后等待 base * 2^(n-1) 的 1~1.5 倍。"""
        base = min(self._backoff_initial * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
        return base * (1 + BACKOFF_JITTER_RATIO * random.random())

    def _log_network_error(
        self,
        attempt: int,
        attempts: int,
        started: float,
        error: httpx.HTTPError,
    ) -> None:
        logger.warning(
            "LLM 请求网络异常",
            extra={
                "model": self._model,
                "attempt": attempt,
                "max_attempts": attempts,
                "elapsed_ms": _elapsed_ms(started),
                "error_type": type(error).__name__,
            },
        )


def _is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES or status_code >= 500


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _extract_content(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError as error:
        raise LLMError("LLM 响应不是合法 JSON", status_code=response.status_code) from error
    if not isinstance(payload, Mapping):
        raise LLMError("LLM 响应不是 JSON 对象", status_code=response.status_code)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM 响应缺少 choices", status_code=response.status_code)
    if not isinstance(choices[0], Mapping):
        raise LLMError("LLM 响应 choices[0] 不是对象", status_code=response.status_code)
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise LLMError("LLM 响应 message 不是对象", status_code=response.status_code)
    content = message.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise LLMError("LLM 响应 content 不是字符串", status_code=response.status_code)
    return content
