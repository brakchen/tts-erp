"""Tests for tts_erp_v2.middleware.access_log.AccessLogMiddleware.

The access log is the operator's single source of truth for "who
hit what, how, when" — replacing the previous dance of stitching
together uvicorn's stdout line and ``docker exec nginx-gw cat
/var/log/nginx/access.log``. These tests pin the format so we
don't regress back to that pain.
"""
from __future__ import annotations
import logging
import re
import pytest
from tts_erp_v2.middleware.access_log import (
    AccessLogMiddleware,
    _client_ip,
    _header,
    _is_enabled,
    _key_prefix,
    _truncate_ua,
)

# ─── pure helpers — no app, no DB, no network


def test_client_ip_prefers_x_forwarded_for():
    """Real client IP comes from X-Forwarded-For first hop (the
    original client), not the second (an intermediate proxy)."""
    scope = {
        "headers": [
            (b"x-forwarded-for", b"203.0.113.7, 10.0.0.1"),
            (b"x-real-ip", b"10.0.0.1"),
        ]
    }
    assert _client_ip(scope) == "203.0.113.7"


def test_client_ip_falls_back_to_x_real_ip():
    """When X-Forwarded-For is absent, fall back to X-Real-IP."""
    scope = {"headers": [(b"x-real-ip", b"198.51.100.5")]}
    assert _client_ip(scope) == "198.51.100.5"


def test_client_ip_falls_back_to_scope_client():
    """Direct TCP peer (curl in dev, 127.0.0.1) when no proxy headers."""
    scope = {"headers": [], "client": ("127.0.0.1", 54321)}
    assert _client_ip(scope) == "127.0.0.1"


def test_client_ip_returns_dash_when_nothing_known():
    scope = {"headers": []}
    assert _client_ip(scope) == "-"


def test_header_returns_empty_for_missing():
    assert _header({"headers": []}, "x-forwarded-prefix") == ""


def test_truncate_ua_short_passes_through():
    assert _truncate_ua("curl/8.0") == "curl/8.0"


def test_truncate_ua_long_is_cut():
    ua = "Mozilla/5.0 " + ("x" * 200)
    out = _truncate_ua(ua, limit=30)
    assert len(out) == 30
    assert out.endswith("…")


# ─── enable / disable env-var contract


def test_is_enabled_default_is_true(monkeypatch):
    monkeypatch.delenv("TTS_ERP_ACCESS_LOG", raising=False)
    assert _is_enabled() is True


@pytest.mark.parametrize("off", ["0", "false", "no", "off", "FALSE", " 0 "])
def test_is_enabled_off(monkeypatch, off):
    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", off)
    assert _is_enabled() is False


# ─── key_prefix: log correlation token, never the plaintext key


def test_key_prefix_is_twelve_hex_chars_of_sha256():
    """The 12-char prefix is the same format the access log uses
    (api_key_hash[:12]). Operators grep ``key=abc123def456`` to
    correlate a specific key across many requests; the same prefix
    in the login event log pairs the attempt with subsequent
    successes or other failures."""
    assert _key_prefix("ttserp_rw_anything") == _key_prefix("ttserp_rw_anything")
    out = _key_prefix("ttserp_rw_anything")
    assert len(out) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", out)


def test_key_prefix_distinguishes_keys():
    """Different plaintext keys must produce different prefixes —
    otherwise the log is useless for telling attempts apart."""
    assert _key_prefix("ttserp_rw_alpha") != _key_prefix("ttserp_rw_beta")


# ─── end-to-end: one middleware call writes one structured line


def _make_scope(**overrides) -> dict:
    """Build a minimal ASGI scope dict for the middleware's
    request-side reads (the response side is mocked below)."""
    base = {
        "type": "http",
        "method": "GET",
        "path": "/v2/pages/manual-costs",
        "query_string": b"shop_id=749",
        "headers": [
            (b"host", b"daqiang.nat100.top"),
            (b"user-agent", b"Mozilla/5.0 (X11; Linux) test/1.0"),
            (b"x-forwarded-for", b"203.0.113.7"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-prefix", b"/tts/"),
            (b"content-length", b"0"),
        ],
        "client": ("10.0.0.5", 41052),
    }
    base.update(overrides)
    return base


async def _noop_app(scope, receive, send):
    # Pretend the inner stack set some auth state (as AuthMiddleware
    # would), then respond 200. The middleware's job is to LOG
    # whatever scope state the inner stack left behind, not to
    # produce the response itself.
    scope.setdefault("auth_method", "cookie")
    scope.setdefault("api_key_role", "readwrite")
    scope.setdefault("api_key_hash", "abcdef0123456789" + "f" * 52)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


@pytest.mark.asyncio
async def test_middleware_emits_one_structured_line_per_request(caplog):
    """The headline contract: every request produces exactly one
    log line, on the named logger, with every key=value pair the
    operator needs."""
    caplog.set_level(logging.INFO, logger="tts_erp_v2.access")
    mw = AccessLogMiddleware(_noop_app)

    sent: list[dict] = []

    async def _capture_send(event):
        sent.append(event)

    await mw(_make_scope(), receive=_noop_receive(), send=_capture_send)

    access_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "tts_erp_v2.access"
    ]
    assert len(access_lines) == 1, (
        f"expected exactly one access log line, got {len(access_lines)}: "
        f"{access_lines}"
    )
    line = access_lines[0]
    # Every field the operator promised in the docstring:
    assert "method=GET" in line
    assert "path=/v2/pages/manual-costs?shop_id=749" in line
    assert "status=200" in line
    assert "auth=cookie" in line
    assert "key=abcdef012345" in line  # first 12 chars of the hash
    assert "role=readwrite" in line
    assert "xfp=/tts/" in line
    assert "xfrp=https" in line
    assert "body=0" in line
    # Real client IP from X-Forwarded-For, NOT the direct peer.
    assert "203.0.113.7" in line
    assert "10.0.0.5" not in line  # direct peer must be excluded
    # Timestamp + duration present.
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line)


@pytest.mark.asyncio
async def test_middleware_disabled_via_env_emits_nothing(monkeypatch, caplog):
    """TTS_ERP_ACCESS_LOG=0 silences the middleware — used by the
    api_client conftest so test output isn't drowned."""
    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", "0")
    caplog.set_level(logging.INFO, logger="tts_erp_v2.access")
    mw = AccessLogMiddleware(_noop_app)

    async def _capture_send(event):
        pass

    await mw(_make_scope(), receive=_noop_receive(), send=_capture_send)
    access_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "tts_erp_v2.access"
    ]
    assert access_lines == []


def _noop_receive():
    async def _r():
        return {"type": "http.request", "body": b"", "more_body": False}
    return _r
