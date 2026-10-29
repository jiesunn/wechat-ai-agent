"""接入层端口：接收与发送分离，上层各依赖所需的一半。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from wechat_agent.wechat.models import PollResult


@runtime_checkable
class WeChatReceiver(Protocol):
    """收通道：登录态、长轮询与连接释放；真实实现为 ILinkTransport。"""

    async def login(self) -> None:
        """建立会话；凭证失效时由实现重新走扫码流程，失败抛 TransportError。"""
        ...

    async def poll(self, cursor: str | None) -> PollResult:
        """长轮询一次；首次传 None，空轮询返回相同游标。网络失败抛 TransportError。"""
        ...

    async def close(self) -> None:
        """释放连接；幂等，重复调用不报错。"""
        ...


@runtime_checkable
class WeChatSender(Protocol):
    """发通道：把一条文本发给联系人；实现负责 context_token 回填与文本分片。"""

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
        msg_id: str = "",
    ) -> bool:
        """发送文本；成功返回 True，缺凭据或任一分片失败返回 False。"""
        ...
