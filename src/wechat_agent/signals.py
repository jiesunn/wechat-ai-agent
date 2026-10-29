"""SIGINT / SIGTERM 停机编排：只触发一次，回调可异步等待在途任务。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Final

ShutdownCallback = Callable[[], Awaitable[None] | None]

_SHUTDOWN_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)


class ShutdownController:
    """注册停机信号；首个信号触发回调，回调执行期间后续信号被忽略。"""

    def __init__(
        self,
        callback: ShutdownCallback,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._callback = callback
        self._loop = loop
        self._stop_event = asyncio.Event()
        self._finished_event = asyncio.Event()
        self._requested = False
        self._installed: list[int] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def stop_requested(self) -> bool:
        return self._requested

    def install(self) -> None:
        """在当前事件循环注册 SIGINT / SIGTERM；重复调用不会重复注册。"""
        if self._installed:
            return
        loop = self._ensure_loop()
        for signum in _SHUTDOWN_SIGNALS:
            loop.add_signal_handler(signum, self.request_shutdown, signum)
            self._installed.append(signum)

    def uninstall(self) -> None:
        loop = self._ensure_loop()
        for signum in self._installed:
            loop.remove_signal_handler(signum)
        self._installed.clear()

    def request_shutdown(self, signum: int | None = None) -> None:
        """信号处理器入口；回调执行期间重复调用会被忽略。"""
        if self._requested:
            return
        self._requested = True
        self._stop_event.set()
        self._ensure_loop().call_soon_threadsafe(self._start_callback)

    async def wait(self) -> None:
        """等待停机请求，供主循环在收到信号后停止拉取新消息。"""
        await self._stop_event.wait()

    async def wait_finished(self) -> None:
        """等待停机回调执行完毕，供进程退出前收尾。"""
        await self._finished_event.wait()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    def _start_callback(self) -> None:
        self._task = self._ensure_loop().create_task(self._run_callback())

    async def _run_callback(self) -> None:
        try:
            result = self._callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logging.getLogger(__name__).exception("停机回调执行失败")
        finally:
            self._finished_event.set()
