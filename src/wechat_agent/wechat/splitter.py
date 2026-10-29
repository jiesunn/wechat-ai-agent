"""文本分片：段落 → 句子 → 硬切，切出的每片都是原文的连续切片。"""

from __future__ import annotations

from typing import Final

DEFAULT_LIMIT: Final = 2000
PARAGRAPH_SEPARATOR: Final = "\n\n"
SENTENCE_ENDINGS: Final = "。！？"


def split_text(text: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """按优先级把文本切成不超过 limit 的片段；空文本返回空列表。"""
    if limit <= 0:
        raise ValueError("limit 必须大于 0")
    if not text.strip():
        return []

    fragments: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        end = start + limit
        if end >= total:
            tail = text[start:]
            if tail.strip():
                fragments.append(tail)
            break
        cut = _best_cut(text[start:end])
        fragment = text[start : start + cut]
        if fragment.strip():
            fragments.append(fragment)
        start += cut
    return fragments


def _best_cut(window: str) -> int:
    """在窗口内选切点：段落边界优先，其次句子结尾，最后才硬切。"""
    paragraph = window.rfind(PARAGRAPH_SEPARATOR)
    if paragraph > 0:
        return paragraph + len(PARAGRAPH_SEPARATOR)
    sentence = _last_sentence_end(window)
    if sentence > 0:
        return sentence + 1
    return len(window)


def _last_sentence_end(window: str) -> int:
    return max(window.rfind(mark) for mark in SENTENCE_ENDINGS)
