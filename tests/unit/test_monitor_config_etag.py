"""``If-None-Match`` parsing must be RFC 9110 correct, not string equality.

An entity-tag's opaque-tag may legitimately contain a comma
(``etagc = %x21 / %x23-7E / obs-text``, and 0x2C is in ``%x23-7E``), so a naive
``header.split(",")`` silently mis-parses ``W/"a,b"``. docs/monitor-config-sync.md
§6.2 requires protocol-conformant behaviour covered by protocol cases.
"""

from __future__ import annotations

import pytest

from tts_erp_v2.monitor_config.etag import is_not_modified, weak_etag

CURRENT = weak_etag({"a": 1})  # e.g. W/"monitor-<hex>"


def test_absent_or_blank_header_never_matches() -> None:
    assert is_not_modified(None, CURRENT) is False
    assert is_not_modified("", CURRENT) is False
    assert is_not_modified("   ", CURRENT) is False


def test_star_matches_any_current_representation() -> None:
    assert is_not_modified("*", CURRENT) is True


def test_exact_weak_tag_matches() -> None:
    assert is_not_modified(CURRENT, CURRENT) is True


def test_weak_comparison_ignores_the_weak_prefix() -> None:
    strong = CURRENT.removeprefix("W/")
    assert is_not_modified(strong, CURRENT) is True
    assert is_not_modified(f'  {strong}  ', CURRENT) is True


def test_list_matches_on_any_member() -> None:
    assert is_not_modified(f'"nope", {CURRENT}', CURRENT) is True
    assert is_not_modified(f'{CURRENT}, "nope"', CURRENT) is True
    assert is_not_modified('"a", "b", "c"', CURRENT) is False


@pytest.mark.parametrize(
    ("header", "opaque"),
    [
        ('"a,b"', "a,b"),
        ('W/"a,b"', "a,b"),
        ('"x,y,z"', "x,y,z"),
    ],
)
def test_opaque_tag_may_contain_commas(header: str, opaque: str) -> None:
    """A comma inside quotes is part of the tag, not a list separator."""

    current = f'W/"{opaque}"'
    assert is_not_modified(header, current) is True


def test_comma_inside_a_list_member_is_not_split() -> None:
    current = 'W/"a,b"'
    assert is_not_modified('W/"q,r", W/"a,b"', current) is True
    assert is_not_modified('W/"a", W/"b"', current) is False


def test_star_is_only_special_as_the_whole_field_value() -> None:
    """RFC 9110: a bare ``*`` inside a list is not a wildcard and not a tag."""

    assert is_not_modified('"x", *', CURRENT) is False
    assert is_not_modified('"x", *', 'W/"*"') is False
    # A quoted "*" is an ordinary opaque tag.
    assert is_not_modified('"*"', 'W/"*"') is True


def test_unquoted_garbage_does_not_match() -> None:
    assert is_not_modified("garbage", CURRENT) is False
    assert is_not_modified("W/garbage", CURRENT) is False
