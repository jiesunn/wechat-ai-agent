"""归一化模型与 Transport 端口的行为契约。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fakes.fake_transport import FakeTransport

from wechat_agent.wechat.models import (
    MessagePart,
    NormalizedMessage,
    PollResult,
    SessionExpired,
    TransportError,
    normalize_part_type,
)
from wechat_agent.wechat.transport import WeChatReceiver


def test_unknown_part_type_falls_back_to_unknown() -> None:
    part = MessagePart(type="sticker")  # type: ignore[arg-type]

    assert part.type == "unknown"


@pytest.mark.parametrize("raw_type", [None, "", "sticker", "location", 42])
def test_normalize_part_type_never_raises(raw_type: object) -> None:
    assert normalize_part_type(raw_type) == "unknown"


@pytest.mark.parametrize("raw_type", ["text", "image", "voice", "file", "video"])
def test_known_part_types_are_preserved(raw_type: str) -> None:
    assert normalize_part_type(raw_type) == raw_type


def test_part_allows_missing_optional_fields() -> None:
    part = MessagePart(type="text")

    assert part.text is None
    assert part.media_size is None


def test_message_allows_missing_optional_fields() -> None:
    message = NormalizedMessage(msg_id="msg-1", from_user_id="wxid_alice")

    assert message.context_token is None
    assert message.parts == ()
    assert message.received_at.utcoffset() == timedelta(0)


def test_message_parts_are_stored_as_tuple() -> None:
    message = NormalizedMessage(
        msg_id="msg-1",
        from_user_id="wxid_alice",
        parts=[MessagePart(type="text", text="在吗？")],
    )

    assert isinstance(message.parts, tuple)
    assert message.parts[0].text == "在吗？"


def test_poll_result_messages_are_stored_as_tuple() -> None:
    message = NormalizedMessage(msg_id="msg-1", from_user_id="wxid_alice")

    result = PollResult(messages=[message], cursor="cursor-1")

    assert isinstance(result.messages, tuple)
    assert result.cursor == "cursor-1"


def test_session_expired_is_transport_error() -> None:
    assert issubclass(SessionExpired, TransportError)
    assert issubclass(TransportError, Exception)


def test_session_expired_is_caught_as_transport_error() -> None:
    with pytest.raises(TransportError):
        raise SessionExpired("errcode -14")


def test_fake_transport_satisfies_protocol() -> None:
    assert isinstance(FakeTransport(), WeChatReceiver)


async def test_fake_transport_delivers_queued_messages_and_advances_cursor() -> None:
    transport = FakeTransport()
    message = NormalizedMessage(msg_id="msg-1", from_user_id="wxid_alice")
    transport.push(message)

    first = await transport.poll(None)
    assert first.messages == (message,)
    assert first.cursor == "cursor-1"

    second = await transport.poll(first.cursor)
    assert second.messages == ()
    assert second.cursor == "cursor-1"
    assert transport.poll_count == 2


async def test_fake_transport_raises_injected_poll_error_once() -> None:
    transport = FakeTransport()
    transport.fail_next_poll(SessionExpired("errcode -14"))

    with pytest.raises(SessionExpired):
        await transport.poll(None)

    assert (await transport.poll(None)).messages == ()


async def test_fake_transport_records_login_and_close() -> None:
    transport = FakeTransport()

    await transport.login()
    await transport.close()

    assert transport.login_count == 1
    assert transport.closed is True
