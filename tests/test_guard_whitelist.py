"""白名单解析与精确匹配的行为契约。"""

from __future__ import annotations

import pytest

from wechat_agent.guard.whitelist import Whitelist, parse_whitelist


def test_parse_trims_items_and_ignores_empty_ones() -> None:
    assert parse_whitelist(" id_1 , , id_2 ,") == frozenset({"id_1", "id_2"})


def test_parse_deduplicates_items() -> None:
    assert parse_whitelist("id_1, id_1,id_1") == frozenset({"id_1"})


def test_parse_returns_empty_set_for_blank_config() -> None:
    assert parse_whitelist(" , ,, ") == frozenset()


def test_configured_contact_is_allowed() -> None:
    whitelist = Whitelist("id_1, id_12")

    assert whitelist.allows("id_1") is True
    assert whitelist.allows("id_12") is True


def test_unlisted_contact_is_rejected() -> None:
    whitelist = Whitelist("id_1,id_12")

    assert whitelist.allows("id_2") is False


@pytest.mark.parametrize("raw", [None, "", "   ", " , ,"])
def test_empty_whitelist_denies_everyone(raw: str | None) -> None:
    whitelist = Whitelist(raw)

    assert whitelist.allows("") is False
    assert whitelist.allows("id_1") is False


def test_unset_whitelist_defaults_to_deny_all() -> None:
    assert Whitelist().allows("id_1") is False


@pytest.mark.parametrize(
    ("raw", "contact_id"),
    [("id_1", "id_12"), ("id_12", "id_1")],
)
def test_match_is_exact_not_prefix(raw: str, contact_id: str) -> None:
    assert Whitelist(raw).allows(contact_id) is False


def test_wildcard_is_not_supported() -> None:
    whitelist = Whitelist("*")

    assert whitelist.allows("id_1") is False
    assert whitelist.allows("*") is True
