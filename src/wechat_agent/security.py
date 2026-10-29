"""密钥脱敏工具，供日志、异常与终端摘要统一使用。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

MASK_CHAR: Final = "*"
DEFAULT_KEEP_PREFIX: Final = 4
DEFAULT_KEEP_SUFFIX: Final = 4

SENSITIVE_KEY_PATTERN: Final = re.compile(r"[^a-z0-9]+")
SENSITIVE_SEGMENTS: Final = frozenset(
    {
        "token",
        "secret",
        "apikey",
        "authorization",
        "password",
        "passwd",
        "credential",
        "credentials",
    }
)
_AUTH_SCHEME_PATTERN: Final = re.compile(r"^(?:bearer|basic)\s+", re.IGNORECASE)


def is_sensitive_key(key: str) -> bool:
    """按键名判断是否需要脱敏；`max_tokens` 这类计数不算敏感。"""
    lowered = key.lower()
    if "apikey" in SENSITIVE_KEY_PATTERN.sub("", lowered):
        return True
    segments = [segment for segment in SENSITIVE_KEY_PATTERN.split(lowered) if segment]
    return any(segment in SENSITIVE_SEGMENTS or segment.endswith("token") for segment in segments)


def mask_secret(
    value: str,
    *,
    keep_prefix: int = DEFAULT_KEEP_PREFIX,
    keep_suffix: int = DEFAULT_KEEP_SUFFIX,
) -> str:
    """保留首尾若干字符，中间替换为掩码；过短的值整体掩码。"""
    if not value:
        return ""
    if keep_prefix < 0 or keep_suffix < 0:
        raise ValueError("keep_prefix 与 keep_suffix 不能为负数")
    if len(value) <= keep_prefix + keep_suffix:
        return MASK_CHAR * len(value)
    tail = value[len(value) - keep_suffix :] if keep_suffix else ""
    return f"{value[:keep_prefix]}{MASK_CHAR * (len(value) - keep_prefix - keep_suffix)}{tail}"


def mask_authorization(value: str) -> str:
    """保留认证方案（如 Bearer），只对凭证部分脱敏。"""
    match = _AUTH_SCHEME_PATTERN.match(value)
    if match is None:
        return mask_secret(value)
    scheme = value[: match.end()].strip()
    return f"{scheme} {mask_secret(value[match.end() :])}"


def mask_value(key: str, value: object) -> object:
    """按字段名决定是否脱敏单个值；非字符串的敏感值也做掩码。"""
    if not is_sensitive_key(key):
        return value
    if isinstance(value, str):
        if "authorization" in key.lower():
            return mask_authorization(value)
        return mask_secret(value)
    return mask_secret(str(value))


def mask_mapping(data: Mapping[str, object]) -> dict[str, object]:
    """返回脱敏后的副本；嵌套映射与列表递归处理，原数据不变。"""
    return {str(key): _mask_nested(str(key), value) for key, value in data.items()}


def _mask_nested(key: str, value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(child_key): _mask_nested(str(child_key), child)
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_mask_nested(key, item) for item in value]
    return mask_value(key, value)
