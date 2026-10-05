"""Bound and redact untrusted Artemis diagnostic data before persistence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|credential|api[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL = re.compile(r"https?://[^\s<>\"']+")
_MAX_DEPTH = 6
_MAX_ITEMS = 100
_MAX_STRING = 2000
_MAX_JSON_BYTES = 32_768


def sanitize_text(value: object) -> str:
    """Remove credential-bearing text and bound persisted error strings."""
    text = str(value)
    text = _BEARER.sub("Bearer [REDACTED]", text)

    def clean_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            if not parts.query:
                return raw
            return urlunsplit((parts.scheme, parts.netloc, parts.path, _REDACTED, ""))
        except ValueError:
            return _REDACTED

    return _URL.sub(clean_url, text)[:_MAX_STRING]


def _sanitize(value: Any, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["__truncated_items__"] = True
                break
            key = sanitize_text(raw_key)[:200]
            result[key] = (
                _REDACTED if _SECRET_KEY.search(key) else _sanitize(item, depth + 1)
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        list_result = [_sanitize(item, depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            list_result.append("[TRUNCATED_ITEMS]")
        return list_result
    return sanitize_text(value)


def sanitize_artemis_output(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return JSON-safe diagnostics within fixed recursion and byte budgets."""
    if value is None:
        return None
    sanitized = cast(dict[str, Any], _sanitize(value, 0))
    encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= _MAX_JSON_BYTES:
        return sanitized
    return {
        "truncated": True,
        "preview": sanitize_text(encoded[: _MAX_JSON_BYTES // 2]),
    }
