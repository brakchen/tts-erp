"""Weak entity-tags and RFC 9110 ``If-None-Match`` evaluation for monitor delivery.

Deliberately small and pure: no DB, no HTTP, no logging. The existing runtime
config snapshot endpoint compares ``if-none-match`` to a single generated
string, which is neither RFC-correct nor safe to copy
(docs/monitor-config-sync.md §6.2): a client may send ``*``, a list of tags,
or the strong spelling of a weak tag.

RFC 9110 references:
  §8.8.2 strong/weak comparison — weak comparison ignores the ``W/`` prefix.
  §8.8.3 If-None-Match — ``*`` matches any current representation; otherwise a
  comma-separated list is matched with the weak comparison function.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

WEAK_PREFIX = "W/"


def canonical_json_bytes(material: Mapping[str, Any]) -> bytes:
    """Stable serialization so the same configuration always hashes the same."""

    return json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _opaque_tag(tag: str) -> str:
    """Strip the weak prefix, leaving the quoted opaque-tag (§8.8.3)."""

    candidate = tag.strip()
    if candidate[:2].upper() == WEAK_PREFIX:
        candidate = candidate[2:].strip()
    return candidate


def parse_entity_tag_list(header: str) -> list[str]:
    """Split an ``If-None-Match`` field value into its entity-tags.

    A naive ``split(",")`` is wrong: RFC 9110's ``etagc`` is
    ``%x21 / %x23-7E / obs-text`` and ``%x2C`` (comma) sits inside
    ``%x23-7E``, so ``W/"a,b"`` is one tag, not two. This walks the field
    value and only treats a comma outside a quoted string as a separator,
    also honouring ``\\`` escapes inside the quoted string.

    Unparsable members are skipped rather than raising: a malformed header must
    degrade to "no match" (a 200 with the full body), never to a 304 or a 500.
    """

    tags: list[str] = []
    index = 0
    length = len(header)
    while index < length:
        start = index
        while index < length and header[index] in " \t":
            index += 1
        if index < length and header[index : index + 2].upper() == WEAK_PREFIX:
            index += 2
            while index < length and header[index] in " \t":
                index += 1
        if index >= length or header[index] != '"':
            # Not an entity-tag; skip to the next separator.
            index = start
            while index < length and header[index] != ",":
                index += 1
            index += 1
            continue
        index += 1  # opening quote
        while index < length:
            char = header[index]
            if char == "\\" and index + 1 < length:
                index += 2
                continue
            if char == '"':
                index += 1
                break
            index += 1
        else:
            # Unterminated quoted string: nothing more to consume.
            break
        tags.append(header[start:index].strip())
        while index < length and header[index] in " \t":
            index += 1
        if index < length and header[index] == ",":
            index += 1
    return tags


def is_not_modified(if_none_match: str | None, current: str) -> bool:
    """Return True when ``If-None-Match`` selects the current representation.

    ``None``/blank means the header was absent, which never matches. ``*`` matches
    because the caller only reaches this point with a representation in hand.
    """

    if if_none_match is None:
        return False
    header = if_none_match.strip()
    if not header:
        return False
    if header == "*":
        return True
    current_opaque = _opaque_tag(current)
    # A list member matches on weak comparison, so `W/"x"` matches `"x"`.
    return any(
        _opaque_tag(member) == current_opaque
        for member in parse_entity_tag_list(header)
    )


def weak_etag(material: Mapping[str, Any]) -> str:
    """Build the weak entity-tag covering a delivery's non-sensitive version facts.

    Only semantic version material goes in (seller/shop, schema, current and
    force version + content hash). Request ids, timestamps and any random value
    are deliberately excluded so a retry of the same configuration revalidates.
    """

    digest = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    return f'{WEAK_PREFIX}"monitor-{digest}"'


DELIVERY_CACHE_CONTROL = "private, no-cache"
DELIVERY_VARY = "Authorization"


def delivery_headers(etag: str) -> dict[str, str]:
    """Headers every delivery response carries, 200 and 304 alike.

    ``private, no-cache`` plus ``Vary: Authorization`` keeps shared caches and
    cross-credential proxies from serving one shop's plaintext to another.
    """

    return {
        "ETag": etag,
        "Cache-Control": DELIVERY_CACHE_CONTROL,
        "Vary": DELIVERY_VARY,
    }
