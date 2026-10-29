"""接入层端口：上层只依赖 WeChatTransport，不感知 iLink 的 HTTP 细节。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from wechat_agent.wechat.models import PollResult


@runtime_checkable
class WeChatTransport(Protocol):
    """收发通道；真实实现为 ILinkTransport，测试注入 FakeTransport。"""

    async def login(self) -> None:
        """建立会话；凭证失效时由实现重新走扫码流程，失败抛 TransportError。"""
        ...

    async def poll(self, cursor: str | None) -> PollResult:
        """长轮询一次；首次传 None，空轮询返回相同游标。网络失败抛 TransportError。"""
        ...

    async def send(self, message_id: str, context_token: str | None, text: str) -> None:
        """发送一条文本；失败抛 TransportError。上游在缺少 context_token 时已跳过。"""
        ...

    async def close(self) -> None:
        """释放连接；幂等，重复调用不报错。"""
        ...
