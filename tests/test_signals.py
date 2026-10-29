"""停机信号控制器：真实信号触发与重复触发抑制。"""

from __future__ import annotations

import asyncio
import signal

import pytest

from wechat_agent.signals import ShutdownController


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
async def test_signal_triggers_shutdown_callback(signum: int) -> None:
    triggered = asyncio.Event()

    async def on_shutdown() -> None:
        triggered.set()

    controller = ShutdownController(on_shutdown)
    controller.install()
    try:
        signal.raise_signal(signum)
        await asyncio.wait_for(controller.wait_finished(), timeout=2)
    finally:
        controller.uninstall()

    assert controller.stop_requested is True
    assert triggered.is_set()


async def test_repeated_requests_run_callback_once() -> None:
    release = asyncio.Event()
    calls = 0

    async def on_shutdown() -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    controller = ShutdownController(on_shutdown)
    controller.request_shutdown()
    controller.request_shutdown()
    controller.request_shutdown()

    for _ in range(100):
        if calls == 1:
            break
        await asyncio.sleep(0)
    assert calls == 1

    release.set()
    await asyncio.wait_for(controller.wait_finished(), timeout=2)
    assert calls == 1


async def test_wait_returns_after_shutdown_request() -> None:
    controller = ShutdownController(lambda: None)

    controller.request_shutdown()

    await asyncio.wait_for(controller.wait(), timeout=2)
    await asyncio.wait_for(controller.wait_finished(), timeout=2)
    assert controller.stop_requested is True
