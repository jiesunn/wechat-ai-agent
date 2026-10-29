"""演练：服务端返回 -14 时，主循环清理凭证并重新进入扫码登录流程。

只验证到「清理凭证 + 触发重登」；完整重登需要真实扫码，由人工执行。
脚本使用临时 data 目录与 respx，不触达真实网络，也不碰仓库的 data/。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import respx

from wechat_agent.main import _poll_forever
from wechat_agent.signals import ShutdownController
from wechat_agent.wechat.dedup import SeenIdStore
from wechat_agent.wechat.ilink import ILinkTransport
from wechat_agent.wechat.models import TransportError

BASE_URL = "https://ilink.example.com"
API_PREFIX = "/ilink/bot"
TOKEN = "drill-token"


class _PipelineStub:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, message: Any) -> bool:
        self.submitted.append(message.msg_id)
        return True


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        (data_dir / "credentials.json").write_text(
            json.dumps({"bot_token": TOKEN}), encoding="utf-8"
        )
        transport = ILinkTransport(
            base_url=BASE_URL,
            data_dir=data_dir,
            uin=123456,
            bot_type=3,
            poll_interval=0.0,
            qr_display=lambda _: None,
        )
        pipeline = _PipelineStub()
        runtime = SimpleNamespace(
            transport=transport,
            pipeline=pipeline,
            seen=SeenIdStore(data_dir),
        )
        controller = ShutdownController(lambda: None)

        with respx.mock() as router:
            updates = router.post(f"{BASE_URL}{API_PREFIX}/getupdates").mock(
                return_value=httpx.Response(
                    200,
                    json={"ret": -1, "errcode": -14, "errmsg": "session timeout"},
                )
            )
            qrcode = router.get(f"{BASE_URL}{API_PREFIX}/get_bot_qrcode").mock(
                return_value=httpx.Response(500, json={"ret": -1})
            )

            await transport.login()
            print(f"1. 复用已存凭证登录: credentials_exists={transport.credentials_path.exists()}")
            try:
                await _poll_forever(runtime, controller)
            except TransportError as error:
                print(f"2. 重登流程已触发，因未真实扫码而失败: {type(error).__name__}")
            print(f"3. getupdates 调用次数: {updates.call_count}")
            print(f"4. get_bot_qrcode 调用次数: {qrcode.call_count}")
            print(f"5. 凭证已清理: {not transport.credentials_path.exists()}")
            print(f"6. 已提交消息数: {len(pipeline.submitted)}")

            assert updates.call_count == 1
            assert qrcode.call_count == 1
            assert not transport.credentials_path.exists()
            assert pipeline.submitted == []
            assert transport.load_credentials() is None

        await transport.close()

    print("断言通过：-14 被捕获、凭证已清理、重登流程已触发（完整扫码由人工执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
