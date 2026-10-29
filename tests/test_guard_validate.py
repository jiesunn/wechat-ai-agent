"""形式校验、长度截断与重复抑制的行为契约。"""

from __future__ import annotations

import pytest

from wechat_agent.guard.validate import (
    DEFAULT_MAX_CHARS,
    DuplicateSuppressor,
    has_meaningful_content,
    truncate_text,
)


class FakeClock:
    """可手动快进的单调时钟，避免测试真的等待 30 秒。"""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("text", ["", "   ", "\n\t\r  "])
def test_blank_text_has_no_meaningful_content(text: str) -> None:
    assert has_meaningful_content(text) is False


@pytest.mark.parametrize("text", ["😀", "😀🎉😀", "😀 😀", "，。！？", "...", "，。 😀 !!!"])
def test_emoji_and_punctuation_only_are_rejected(text: str) -> None:
    assert has_meaningful_content(text) is False


@pytest.mark.parametrize("text", ["嗯 😀", "hello!!!", "123", "汉字，标点"])
def test_alnum_or_chinese_content_survives_form_check(text: str) -> None:
    assert has_meaningful_content(text) is True


def test_text_at_limit_is_not_marked_truncated() -> None:
    text = "a" * DEFAULT_MAX_CHARS

    result, truncated = truncate_text(text)

    assert result == text
    assert truncated is False


def test_overlong_text_is_truncated_but_kept() -> None:
    result, truncated = truncate_text("a" * (DEFAULT_MAX_CHARS + 1))

    assert truncated is True
    assert len(result) == DEFAULT_MAX_CHARS


def test_truncated_text_still_has_content_for_downstream() -> None:
    result, truncated = truncate_text("有用内容" * 1000)

    assert truncated is True
    assert result
    assert has_meaningful_content(result) is True


def test_custom_limit_is_honored() -> None:
    assert truncate_text("abcdef", max_chars=3) == ("abc", True)
    assert truncate_text("abc", max_chars=3) == ("abc", False)


def test_first_text_is_allowed_and_repeat_is_suppressed() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello") is False
    assert suppressor.is_duplicate("wxid_alice", "hello") is True


def test_trailing_space_is_a_duplicate_after_normalization() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello") is False
    assert suppressor.is_duplicate("wxid_alice", "hello ") is True


def test_collapsed_whitespace_is_a_duplicate() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello   world") is False
    assert suppressor.is_duplicate("wxid_alice", " hello\nworld ") is True


def test_different_contacts_do_not_share_window() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello") is False
    assert suppressor.is_duplicate("wxid_bob", "hello") is False


def test_repeat_is_allowed_after_window_expires() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello") is False
    clock.advance(31)
    assert suppressor.is_duplicate("wxid_alice", "hello") is False


def test_duplicate_hits_do_not_extend_the_window() -> None:
    clock = FakeClock()
    suppressor = DuplicateSuppressor(clock=clock)

    assert suppressor.is_duplicate("wxid_alice", "hello") is False
    clock.advance(10)
    assert suppressor.is_duplicate("wxid_alice", "hello") is True
    clock.advance(21)
    assert suppressor.is_duplicate("wxid_alice", "hello") is False
