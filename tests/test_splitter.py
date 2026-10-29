"""文本分片：段落 → 句子 → 硬切的优先级与边界情况。"""

from __future__ import annotations

import pytest

from wechat_agent.wechat.splitter import DEFAULT_LIMIT, split_text


def _long_text() -> str:
    sentence = "这是一个用于验证分片逻辑的中文句子，长度保持适中。"
    paragraph = "".join([sentence] * 6)
    return "\n\n".join([paragraph] * 30)


def test_empty_and_whitespace_only_text_returns_no_fragments() -> None:
    assert split_text("") == []
    assert split_text("  \n\n  ") == []


def test_text_within_limit_is_not_split() -> None:
    text = "甲" * 100

    assert split_text(text, 100) == [text]
    assert split_text(text, 200) == [text]


def test_invalid_limit_is_rejected() -> None:
    with pytest.raises(ValueError):
        split_text("abc", 0)
    with pytest.raises(ValueError):
        split_text("abc", -1)


def test_paragraph_boundary_is_preferred_over_sentence_boundary() -> None:
    first = "甲" * 50 + "。"
    second = "乙" * 50 + "。"
    text = first + "\n\n" + second

    fragments = split_text(text, 100)

    assert fragments == [first + "\n\n", second]


def test_sentence_boundary_is_used_when_paragraph_is_too_long() -> None:
    sentence = "丙" * 30 + "。"
    text = sentence * 100

    fragments = split_text(text, 200)

    assert "".join(fragments) == text
    assert all(len(fragment) <= 200 for fragment in fragments)
    assert all(fragment.endswith("。") for fragment in fragments)


def test_unpunctuated_text_is_hard_cut() -> None:
    text = "x" * 4500

    fragments = split_text(text, 2000)

    assert [len(fragment) for fragment in fragments] == [2000, 2000, 500]


def test_long_mixed_text_uses_three_fragments_without_cutting_sentences() -> None:
    text = _long_text()

    fragments = split_text(text, DEFAULT_LIMIT)

    assert len(fragments) == 3
    assert "".join(fragments) == text
    assert all(len(fragment) <= DEFAULT_LIMIT for fragment in fragments)
    assert all(fragment.rstrip("\n").endswith("。") for fragment in fragments)
