"""DRY_RUN 发送实现与组合选择。"""

from __future__ import annotations

import pytest

from wechat_agent.sender import DryRunSender, build_sender


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send_text(
        self,
        contact_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> bool:
        self.calls.append((contact_id, text))
        return True


async def test_dry_run_sender_prints_target_and_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender = DryRunSender()

    await sender.send_text("wxid_alice", "在吗？")

    output = capsys.readouterr().out
    assert "DRY_RUN" in output
    assert "wxid_alice" in output
    assert "在吗？" in output


async def test_dry_run_does_not_call_real_sender() -> None:
    real = RecordingSender()
    sender = build_sender(dry_run=True, real_sender=real)

    assert isinstance(sender, DryRunSender)
    await sender.send_text("wxid_alice", "不会真的发送", context_token="ctx-1")
    assert real.calls == []


async def test_non_dry_run_uses_real_sender() -> None:
    real = RecordingSender()
    sender = build_sender(dry_run=False, real_sender=real)

    assert sender is real
    assert await sender.send_text("wxid_alice", "真实发送")
    assert real.calls == [("wxid_alice", "真实发送")]
