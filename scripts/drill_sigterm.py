"""演练：SIGTERM 触发 ShutdownController，等待在途任务完成后再退出。

在当前进程内注册真实的 SIGTERM 处理器，不启动子进程，也不触达网络。
"""

from __future__ import annotations

import asyncio
import os
import signal

from wechat_agent.signals import ShutdownController


async def main() -> int:
    steps: list[str] = []
    inflight = asyncio.Event()

    async def in_flight_work() -> None:
        steps.append("在途消息开始处理")
        await asyncio.sleep(0.05)
        steps.append("在途消息处理完成")
        inflight.set()

    async def on_shutdown() -> None:
        steps.append("收到 SIGTERM，停止取新消息")
        await inflight.wait()

    controller = ShutdownController(on_shutdown)
    controller.install()
    task = asyncio.create_task(in_flight_work())
    try:
        await asyncio.sleep(0.01)
        print(f"1. 模拟轮询中，PID={os.getpid()}，发送 SIGTERM")
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(controller.wait_finished(), timeout=2)
        await task
    finally:
        controller.uninstall()

    for index, step in enumerate(steps, start=1):
        print(f"{index + 1}. {step}")
    print(f"5. stop_requested={controller.stop_requested}")

    assert controller.stop_requested is True
    assert steps[0] == "在途消息开始处理"
    assert "收到 SIGTERM，停止取新消息" in steps
    assert steps[-1] == "在途消息处理完成"
    print("断言通过：SIGTERM 触发停机回调，在途任务完成后再退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
