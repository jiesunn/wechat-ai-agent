"""应用入口与组合层：只做接线，业务逻辑全部落在已有模块里。"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from wechat_agent.config import ConfigError, Settings, load_settings
from wechat_agent.guard.chain import FilterChain
from wechat_agent.guard.validate import DuplicateSuppressor
from wechat_agent.guard.whitelist import Whitelist
from wechat_agent.llm.client import LLMClient
from wechat_agent.llm.postprocess import generate_reply
from wechat_agent.llm.prompt import build_reply_messages
from wechat_agent.lock import acquire_lock_or_exit
from wechat_agent.logging import LOGGER_NAME, setup_logging
from wechat_agent.memory.short_term import ShortTermMemory
from wechat_agent.memory.store import MemoryStore
from wechat_agent.memory.summary import SummaryEngine
from wechat_agent.multimodal.handlers import DefaultFallbackHandler, MediaHandlerRegistry
from wechat_agent.multimodal.router import MultimodalRouter
from wechat_agent.pipeline import Pipeline
from wechat_agent.sender import MessageSender, build_sender
from wechat_agent.signals import ShutdownController
from wechat_agent.wechat.dedup import SeenIdStore
from wechat_agent.wechat.ilink import ILinkTransport
from wechat_agent.wechat.models import (
    MessagePart,
    NormalizedMessage,
    PollResult,
    SessionExpired,
    TransportError,
)
from wechat_agent.wechat.sender import ILinkSender

SCAN_HINT: Final = "请用微信扫描终端中的二维码，登录成功前会一直等待"

logger = logging.getLogger(LOGGER_NAME)


@dataclass(slots=True)
class Runtime:
    """组合完成的运行期对象；不包含任何业务判断。"""

    settings: Settings
    transport: ILinkTransport
    client: LLMClient
    sender: MessageSender
    pipeline: Pipeline
    router: MultimodalRouter
    store: MemoryStore
    short_term: ShortTermMemory
    summary: SummaryEngine
    seen: SeenIdStore


class _SummaryClient:
    """把 4.3 的 ``complete(system=, user=)`` 端口适配到 5.1 的消息接口。"""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def complete(self, *, system: str, user: str) -> str:
        return await self._client.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )


class _MemorySender:
    """发送成功后写 assistant 记忆，并按 4.3 的策略触发一次摘要检查。"""

    def __init__(
        self,
        inner: MessageSender,
        short_term: ShortTermMemory,
        summary: SummaryEngine,
    ) -> None:
        self._inner = inner
        self._short_term = short_term
        self._summary = summary

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
            await self._summary.maybe_summarize(contact_id)
        return sent


class _MediaReplyHandler:
    """3.3 的 async MediaHandler 端口：纯媒体交给 6.3 router 的降级分支。"""

    def __init__(self, router: MultimodalRouter, sender: MessageSender) -> None:
        self._router = router
        self._sender = sender

    async def handle(self, message: NormalizedMessage) -> None:
        async def no_text(_: str) -> str | None:
            raise RuntimeError("纯媒体不应进入文本生成器")

        reply = await self._router.route(message, generate_text_reply=no_text)
        if reply:
            await self._sender.send_text(
                message.from_user_id,
                reply,
                context_token=message.context_token,
            )


class _ReplyGenerator:
    """把过滤链准入后的文本与记忆、LLM、降级、路由串起来。"""

    def __init__(
        self,
        *,
        settings: Settings,
        store: MemoryStore,
        short_term: ShortTermMemory,
        client: LLMClient,
        router: MultimodalRouter,
    ) -> None:
        self._settings = settings
        self._store = store
        self._short_term = short_term
        self._client = client
        self._router = router

    async def __call__(
        self,
        message: NormalizedMessage,
        *,
        text: str,
        truncated: bool,
    ) -> str | None:
        # 过滤链可能已把文本截断到 max_input_chars；替换文本段后再交给 router，
        # 避免 router 从原始 parts 里重新取到未截断的正文。
        admitted = _with_admitted_text(message, text)

        async def generate_text_reply(admitted_text: str) -> str | None:
            self._short_term.append_user(message.from_user_id, admitted_text)
            history = self._short_term.entries(message.from_user_id)[:-1]
            messages = build_reply_messages(
                system_prompt=self._settings.system_prompt,
                summary=self._store.load(message.from_user_id).summary,
                history=history,
                user_text=admitted_text,
            )
            return await generate_reply(
                lambda: self._client.complete(messages),
                fallback_enabled=self._settings.fallback_reply_enabled,
                fallback_text=self._settings.fallback_reply_text,
            )

        return await self._router.route(admitted, generate_text_reply=generate_text_reply)


def _with_admitted_text(message: NormalizedMessage, text: str) -> NormalizedMessage:
    """用准入后的文本替换消息里的文本段，媒体段与元数据原样保留。"""
    parts: list[MessagePart] = []
    inserted = False
    for part in message.parts:
        if part.type != "text":
            parts.append(part)
            continue
        if not inserted:
            parts.append(MessagePart(type="text", text=text))
            inserted = True
    if not inserted:
        parts.append(MessagePart(type="text", text=text))
    return NormalizedMessage(
        msg_id=message.msg_id,
        from_user_id=message.from_user_id,
        context_token=message.context_token,
        received_at=message.received_at,
        parts=tuple(parts),
    )


def build_runtime(settings: Settings) -> Runtime:
    """按配置组装全部模块；此处不做任何 IO，也不会触发登录。"""
    store = MemoryStore(settings.data_dir)
    short_term = ShortTermMemory(store, max_entries=settings.short_memory_size)
    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        read_timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    summary = SummaryEngine(
        store=store,
        short_term=short_term,
        client=_SummaryClient(client),
        trigger_count=settings.summary_trigger_count,
        idle_minutes=settings.summary_idle_minutes,
    )
    router = MultimodalRouter(
        handlers=MediaHandlerRegistry(
            fallback=DefaultFallbackHandler(settings.unsupported_media_reply)
        )
    )
    transport = ILinkTransport(
        base_url=settings.ilink_base_url,
        bot_type=settings.ilink_bot_type,
        data_dir=settings.data_dir,
    )
    sender = build_sender(dry_run=settings.dry_run, real_sender=ILinkSender(transport))
    media = _MediaReplyHandler(router, sender)
    pipeline = Pipeline(
        chain=FilterChain(
            whitelist=Whitelist(settings.allow_from),
            duplicate_suppressor=DuplicateSuppressor(window_seconds=settings.repeat_window_seconds),
            media_handler=media,
            max_chars=settings.max_input_chars,
        ),
        sender=_MemorySender(sender, short_term, summary),
        reply_generator=_ReplyGenerator(
            settings=settings,
            store=store,
            short_term=short_term,
            client=client,
            router=router,
        ),
    )
    return Runtime(
        settings=settings,
        transport=transport,
        client=client,
        sender=sender,
        pipeline=pipeline,
        router=router,
        store=store,
        short_term=short_term,
        summary=summary,
        seen=SeenIdStore(settings.data_dir),
    )


async def _shutdown(runtime: Runtime) -> None:
    logger.info("收到停机信号，正在停止")
    await runtime.pipeline.stop()
    await runtime.transport.close()


async def _poll_or_stop(
    runtime: Runtime,
    cursor: str | None,
    controller: ShutdownController,
) -> PollResult | None:
    """一次轮询与停机请求竞速；停机优先时返回 None。"""
    poll_task = asyncio.create_task(runtime.transport.poll_with_backoff(cursor))
    stop_task = asyncio.create_task(controller.wait())
    try:
        done, _ = await asyncio.wait(
            {poll_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if poll_task in done:
            return poll_task.result()
        poll_task.cancel()
        with suppress(asyncio.CancelledError):
            await poll_task
        return None
    finally:
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task


async def _poll_forever(runtime: Runtime, controller: ShutdownController) -> None:
    cursor: str | None = None
    while not controller.stop_requested:
        try:
            result = await _poll_or_stop(runtime, cursor, controller)
        except SessionExpired:
            if controller.stop_requested:
                return
            logger.warning("iLink 会话已过期，准备重新扫码登录")
            runtime.transport.credentials_path.unlink(missing_ok=True)
            try:
                await runtime.transport.login()
            except TransportError:
                if controller.stop_requested:
                    return
                raise
            cursor = None
            continue
        if result is None:
            return
        cursor = result.cursor
        for message in result.messages:
            if runtime.seen.is_duplicate(message.msg_id):
                logger.debug("跳过重复消息", extra={"msg_id": message.msg_id})
                continue
            runtime.pipeline.submit(message)


async def _run(settings: Settings) -> int:
    runtime = build_runtime(settings)
    controller = ShutdownController(lambda: _shutdown(runtime))
    controller.install()
    logger.info(
        "应用已启动",
        extra={
            "dry_run": settings.dry_run,
            "data_dir": str(Path(settings.data_dir)),
            "whitelist_enabled": bool(settings.allow_from.strip()),
        },
    )
    try:
        logger.info("正在扫码登录", extra={"hint": SCAN_HINT})
        await runtime.transport.login()
        logger.info("登录成功")
        runtime.pipeline.start()
        logger.info("开始轮询")
        await _poll_forever(runtime, controller)
        return 0
    except TransportError as error:
        if controller.stop_requested:
            return 0
        logger.error("启动失败，无法连接 iLink", extra={"error_type": type(error).__name__})
        return 1
    except Exception as error:  # noqa: BLE001 - 入口兜底，避免把栈直接抛给终端
        if controller.stop_requested:
            logger.info(
                "停机请求期间中断登录或轮询，按正常退出处理",
                extra={"error_type": type(error).__name__},
            )
            return 0
        logger.exception("运行时异常退出", extra={"error_type": type(error).__name__})
        return 1
    finally:
        controller.uninstall()
        await runtime.pipeline.stop()
        await runtime.transport.close()
        await runtime.client.close()
        logger.info("进程已停止")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as error:
        print(f"配置校验失败：{error}", file=sys.stderr)
        return 1
    setup_logging(
        data_dir=settings.data_dir,
        log_dir=settings.log_dir,
        level=settings.log_level,
    )
    lock = acquire_lock_or_exit(settings.data_dir)
    try:
        return asyncio.run(_run(settings))
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
