"""发送端：DRY_RUN 实现、iLink sendmessage 与文本分片发送。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pytest
from pytest import LogCaptureFixture

from wechat_agent.logging import LOGGER_NAME
from wechat_agent.sender import DryRunSender, build_sender
from wechat_agent.wechat.models import TransportError
from wechat_agent.wechat.sender import ILinkSender, contact_hash
from wechat_agent.wechat.splitter import split_text


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


class RecordingPostTransport:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_on_call = fail_on_call

    async def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((path, dict(payload)))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise TransportError("发送失败")
        return {"ret": 0}


def _long_text() -> str:
    sentence = "这是一个用于验证分片逻辑的中文句子，长度保持适中。"
    paragraph = "".join([sentence] * 6)
    return "\n\n".join([paragraph] * 30)


async def test_ilink_sender_backfills_sendmessage_fields() -> None:
    transport = RecordingPostTransport()
    sender = ILinkSender(transport)

    ok = await sender.send_text("wxid_alice", "你好", context_token="ctx-1", msg_id="msg-1")

    assert ok is True
    assert len(transport.calls) == 1
    path, payload = transport.calls[0]
    assert path == "sendmessage"
    msg = payload["msg"]
    assert msg["to_user_id"] == "wxid_alice"
    assert msg["context_token"] == "ctx-1"
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["item_list"] == [{"type": 1, "text_item": {"text": "你好"}}]
    assert isinstance(msg["client_id"], str)
    assert msg["client_id"]


async def test_long_text_is_sent_in_three_ordered_fragments() -> None:
    text = _long_text()
    expected = split_text(text)
    transport = RecordingPostTransport()
    sender = ILinkSender(transport)

    ok = await sender.send_text("wxid_alice", text, context_token="ctx-1", msg_id="msg-1")

    assert ok is True
    assert len(expected) == 3
    sent = [payload["msg"]["item_list"][0]["text_item"]["text"] for _, payload in transport.calls]
    assert sent == expected


async def test_failure_stops_remaining_fragments(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    text = _long_text()
    transport = RecordingPostTransport(fail_on_call=2)
    sender = ILinkSender(transport)

    ok = await sender.send_text("wxid_alice", text, context_token="ctx-1", msg_id="msg-1")

    assert ok is False
    assert len(transport.calls) == 2
    records = [r for r in caplog.records if "分片发送失败" in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    assert record.msg_id == "msg-1"
    assert record.fragment_index == 2
    assert record.fragment_total == 3
    assert record.contact_hash == contact_hash("wxid_alice")
    assert text[:20] not in caplog.text


async def test_missing_context_token_skips_send_and_warns(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    transport = RecordingPostTransport()
    sender = ILinkSender(transport)

    ok = await sender.send_text(
        "wxid_alice",
        "机密正文ABC",
        context_token=None,
        msg_id="msg-9",
    )

    assert ok is False
    assert transport.calls == []
    records = [r for r in caplog.records if "context_token" in r.getMessage()]
    assert len(records) == 1
    assert records[0].msg_id == "msg-9"
    assert "机密正文ABC" not in caplog.text


async def test_send_logs_omit_body_and_raw_contact(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    transport = RecordingPostTransport()
    sender = ILinkSender(transport)
    body = "机密正文ABC"

    ok = await sender.send_text("wxid_alice", body, context_token="ctx-1", msg_id="msg-1")

    assert ok is True
    records = [r for r in caplog.records if "分片发送成功" in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    assert record.msg_id == "msg-1"
    assert record.fragment_index == 1
    assert record.fragment_total == 1
    assert record.contact_hash == contact_hash("wxid_alice")
    assert body not in caplog.text
    assert "wxid_alice" not in caplog.text


async def test_blank_text_is_not_sent(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    transport = RecordingPostTransport()
    sender = ILinkSender(transport)

    ok = await sender.send_text("wxid_alice", " \n\n ", context_token="ctx-1", msg_id="msg-2")

    assert ok is False
    assert transport.calls == []
    assert any("文本为空" in record.getMessage() for record in caplog.records)
