"""人设与请求组装：system = 人设 + 长期摘要，用户文本只作为独立 user 消息。

组装与预算裁剪完全复用 4.4 的 ``wechat_agent.memory.prompt.build_messages``，
这里只负责解析人设回退，避免出现第二套消息拼装逻辑。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from wechat_agent.config import DEFAULT_SYSTEM_PROMPT
from wechat_agent.memory.prompt import DEFAULT_MAX_CHARS, ChatMessage, build_messages
from wechat_agent.memory.store import MemoryEntry

DEFAULT_PERSONA: Final = DEFAULT_SYSTEM_PROMPT


def build_reply_messages(
    *,
    user_text: str,
    system_prompt: str | None = None,
    summary: str = "",
    history: Sequence[MemoryEntry] = (),
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[ChatMessage]:
    """按 D5 的组织方式产出 OpenAI 兼容消息列表。

    system 段由 ``build_messages`` 拼成「人设 + 摘要」；``summary`` 为空时不拼占位。
    用户文本只出现在末位 user 消息里，不会被拼进 system。
    """
    return build_messages(
        persona=resolve_persona(system_prompt),
        summary=summary,
        history=history,
        user_text=user_text,
        max_chars=max_chars,
    )


def resolve_persona(system_prompt: str | None) -> str:
    """自定义人设优先；未配置、空串或纯空白时回落到代码内默认人设。"""
    if system_prompt is not None and system_prompt.strip():
        return system_prompt
    return DEFAULT_PERSONA
