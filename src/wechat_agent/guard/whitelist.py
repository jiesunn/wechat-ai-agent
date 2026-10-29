"""联系人白名单：逗号分隔配置的解析与精确匹配。"""

from __future__ import annotations

__all__ = ["Whitelist", "parse_whitelist"]


def parse_whitelist(raw: str | None) -> frozenset[str]:
    """解析逗号分隔的联系人标识：去空白、去重，空项忽略。"""
    if raw is None:
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


class Whitelist:
    """准入白名单；空名单拒绝全部消息（fail-closed），匹配只做精确比较。"""

    __slots__ = ("_contacts",)

    def __init__(self, raw: str | None = None) -> None:
        self._contacts = parse_whitelist(raw)

    def allows(self, contact_id: str) -> bool:
        """仅当 contact_id 与名单条目完全相等时放行。"""
        return contact_id in self._contacts
