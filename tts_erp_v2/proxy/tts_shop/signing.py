"""TikTok Shop HMAC-SHA256 signing (canonical string + URL builder).

Migrated verbatim from the legacy ``tts_signing.py`` (Wave 5). Canonical
format follows AGENTS.md §2.2 — verified against production traffic; do
not edit without a replay test against ``tests/tts_signing_vector``.

The legacy module remains in place for the FastAPI app at :9877 until
the §7.1 cutover; the two implementations are intentionally byte-for-byte
equivalent so the old app can keep running while new code paths (sync-
worker, API v2) import from here.

Exports
-------
* :func:`sign_request` — pure HMAC over a canonical string.
* :func:`build_signed_url` — URL with timestamp + signature.
* :func:`build_canonical` — exposed for tests / debugging.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys


def build_canonical(
    app_secret: str,
    path: str,
    query_params: dict[str, str],
    body: str | None = None,
) -> str:
    """Return the canonical string that gets HMAC'd.

    AGENTS.md §2.2:
        GET (no body):
            {secret}{path}{k1v1}{k2v2}...{secret}
        POST/PUT/PATCH (with body):
            {secret}{path}{k1v1}{k2v2}...{body}{secret}

    Keys are sorted alphabetically. Body is the EXACT raw JSON string
    (no URL-encoding, no whitespace normalisation, no ensure_ascii=False
    round-trip).
    """
    kv_concat = "".join(f"{k}{query_params[k]}" for k in sorted(query_params))
    if body:
        return f"{app_secret}{path}{kv_concat}{body}{app_secret}"
    return f"{app_secret}{path}{kv_concat}{app_secret}"


def sign_request(
    app_secret: str,
    path: str,
    query_params: dict[str, str],
    body: str | None = None,
) -> str:
    """Compute HMAC-SHA256 signature (hex) for a TikTok Partner API call."""
    canonical = build_canonical(app_secret, path, query_params, body)
    sig = hmac.new(
        app_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if os.environ.get("TTS_DEBUG_SIGN") == "1":
        body_repr = repr(body) if body else "None"
        sys.stderr.write(
            f"[tts-erp-debug] path={path}\n"
            f"  kv={''.join(f'{k}{query_params[k]}' for k in sorted(query_params))}\n"
            f"  body={body_repr}\n"
            f"  canonical={canonical!r}\n"
            f"  sig={sig}\n"
        )
    return sig


__all__ = ["sign_request", "build_canonical"]
