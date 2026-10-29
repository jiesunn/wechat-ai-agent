"""提示注入防护：注入样本下结构不变、泄露 system 片段被拦截、正常回复不误伤。"""

from __future__ import annotations

from typing import Any

import pytest

from wechat_agent.config import DEFAULT_FALLBACK_REPLY_TEXT
from wechat_agent.llm.guard import (
    INJECTION_CATEGORIES,
    INJECTION_SAMPLES,
    MIN_LEAK_CHARS,
    InjectionSample,
    sanitize_reply,
    verify_structure,
)
from wechat_agent.llm.prompt import build_reply_messages
from wechat_agent.memory.store import MemoryEntry

CUSTOM_PERSONA = (
    "你是「小七」，一个只讲重点的中文助手。"
    "你记得用户的偏好，回答控制在三句话以内，不使用 Markdown。"
)
SUMMARY = "用户喜欢喝美式，周三要交周报。"


def _history() -> list[MemoryEntry]:
    return [
        MemoryEntry(role="user", content="最近工作好多", ts="2026-10-29T10:00:00+00:00"),
        MemoryEntry(role="assistant", content="辛苦了，注意休息", ts="2026-10-29T10:00:05+00:00"),
    ]


@pytest.mark.parametrize(
    "sample",
    INJECTION_SAMPLES,
    ids=lambda sample: f"{sample.category}-{sample.text[:8]}",
)
def test_injection_samples_keep_the_structure(sample: InjectionSample) -> None:
    messages = build_reply_messages(
        system_prompt=CUSTOM_PERSONA,
        summary=SUMMARY,
        history=_history(),
        user_text=sample.text,
    )

    assert verify_structure(messages) is True
    assert messages[0]["role"] == "system"
    assert sample.text not in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": sample.text}
    assert sum(message["content"] == sample.text for message in messages) == 1


def test_injection_samples_cover_all_requested_categories() -> None:
    assert INJECTION_CATEGORIES == {
        "override",
        "role_play",
        "delimiter",
        "encoding",
        "multilingual",
    }


def test_verify_structure_rejects_user_text_pasted_into_system() -> None:
    leaked = "这是一段很长很长的用户文本，被直接拼进了系统的提示词"
    messages = [
        {"role": "system", "content": f"你是助手。\n{leaked}"},
        {"role": "user", "content": leaked},
    ]

    assert verify_structure(messages) is False


def test_verify_structure_rejects_user_text_in_other_roles() -> None:
    text = "这是一段很长的用户文本，被复制到了别的角色消息里"
    messages = [
        {"role": "system", "content": "你是助手。"},
        {"role": "user", "content": text},
        {"role": "assistant", "content": text},
        {"role": "user", "content": "继续"},
    ]

    assert verify_structure(messages) is False


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "user", "content": "在吗"}],
        [
            {"role": "system", "content": "人设"},
            {"role": "system", "content": "第二份人设"},
            {"role": "user", "content": "在吗"},
        ],
        [{"role": "system", "content": "人设"}, {"role": "assistant", "content": "你好"}],
        [
            {"role": "system", "content": "人设"},
            {"role": "tool", "content": "工具输出"},
            {"role": "user", "content": "在吗"},
        ],
        [{"role": "system", "content": None}, {"role": "user", "content": "在吗"}],
        ["不是消息对象"],
    ],
)
def test_verify_structure_rejects_malformed_shapes(messages: list[Any]) -> None:
    assert verify_structure(messages) is False


def test_verify_structure_accepts_normal_reply_structure() -> None:
    messages = build_reply_messages(
        system_prompt=CUSTOM_PERSONA,
        summary=SUMMARY,
        history=_history(),
        user_text="今晚喝什么好",
    )

    assert verify_structure(messages) is True


def test_reply_leaking_a_system_fragment_is_replaced() -> None:
    reply = "好的。" + CUSTOM_PERSONA[: MIN_LEAK_CHARS + 5] + "……"

    assert sanitize_reply(reply, CUSTOM_PERSONA) == DEFAULT_FALLBACK_REPLY_TEXT


def test_short_system_fragment_below_threshold_is_not_replaced() -> None:
    fragment = CUSTOM_PERSONA[: MIN_LEAK_CHARS - 1]
    reply = f"我记得你说过：{fragment}"

    assert sanitize_reply(reply, CUSTOM_PERSONA) == reply


def test_normal_reply_is_returned_unchanged() -> None:
    reply = "周三要交周报，今天先写个提纲吧，别熬太晚。"

    assert sanitize_reply(reply, CUSTOM_PERSONA) == reply
    assert sanitize_reply(reply, "") == reply


def test_custom_fallback_text_is_used() -> None:
    reply = CUSTOM_PERSONA

    assert sanitize_reply(reply, CUSTOM_PERSONA, fallback="换个话题吧") == "换个话题吧"
