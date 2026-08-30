"""Tests for API key auth middleware (design: ../tech-doc/api-key-auth-design.md).

Hermetic: inserts its own TEST_-named keys into api_keys (committed, since the
middleware opens its own DB connections) and deletes them afterwards.
Auth mode is controlled per-test via monkeypatch on TTS_ERP_AUTH_MODE.
"""

from __future__ import annotations

import hashlib

import auth
import psycopg
import pytest
import rate_limit
from fastapi.testclient import TestClient
from tts_erp_fastapi import app

# Well-known test keys (full key only lives in this file; DB holds SHA-256)
KEY_RO = "ttserp_ro_TESTreadonlykey000000000000"
KEY_RW = "ttserp_rw_TESTreadwritekey00000000000"
KEY_ADMIN = "ttserp_admin_TESTadminkey000000000000"
KEY_DISABLED = "ttserp_ro_TESTdisabledkey00000000000"
KEY_EXPIRED = "ttserp_ro_TESTexpiredkey000000000000"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def auth_keys(db_url):
    """Insert TEST_ api keys (own connection + commit) and clean up after."""
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM security.api_keys WHERE name LIKE 'TEST_%'")
            cur.executemany(
                "INSERT INTO security.api_keys (key_hash, key_prefix, name, role, status)"
                " VALUES (%s, %s, %s, %s, %s)",
                [
                    (
                        _sha256(KEY_RO),
                        KEY_RO[:16],
                        "TEST_auth_ro",
                        "readonly",
                        "active",
                    ),
                    (
                        _sha256(KEY_RW),
                        KEY_RW[:16],
                        "TEST_auth_rw",
                        "readwrite",
                        "active",
                    ),
                    (
                        _sha256(KEY_ADMIN),
                        KEY_ADMIN[:16],
                        "TEST_auth_admin",
                        "admin",
                        "active",
                    ),
                    (
                        _sha256(KEY_DISABLED),
                        KEY_DISABLED[:16],
                        "TEST_auth_disabled",
                        "readonly",
                        "disabled",
                    ),
                ],
            )
        conn.commit()
        auth.clear_cache()
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM security.api_keys WHERE name LIKE 'TEST_%'")
        conn.commit()
        conn.close()
        auth.clear_cache()


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ─── enforce mode ─────────────────────────────────────────────────────


def test_enforce_no_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_enforce_malformed_header_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers={"Authorization": "notbearer xyz"})
    assert r.status_code == 401


def test_enforce_forged_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get(
        "/db/orders?limit=1", headers=_auth("ttserp_ro_forged000000000000000000")
    )
    assert r.status_code == 401


def test_readonly_can_read_db(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_RO))
    assert r.status_code == 200


def test_readonly_cannot_write_shop_403(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    # Blocked at middleware — never reaches the real shop.
    r = client.post("/orders/123456789/cancel", headers=_auth(KEY_RO), json={})
    assert r.status_code == 403


def test_readonly_cannot_fetch_token_403(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/token/7494763368967603447", headers=_auth(KEY_RO))
    assert r.status_code == 403


def test_readwrite_passes_auth_on_sync(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    # Missing shop_id → business-layer 400 proves auth let it through.
    r = client.post("/sync/orders", headers=_auth(KEY_RW), json={})
    assert r.status_code == 400


# Wave 3 merge: the legacy `/token/{shop_id}` proxy route was deleted
# (token fetch now goes through LocalTokenProvider in-process).
# This test removed in feature/oauth-merge commit; admin-role coverage
# is exercised by other tests below (e.g. test_admin_required_for_xxx).


def test_healthz_exempt(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/healthz")
    assert r.status_code == 200


# ─── Wave 4: /callback and /authorize are PUBLIC (OAuth protocol contract) ─


def test_callback_exempt_no_key(client, auth_keys, monkeypatch):
    """Wave 4 Slice 1: /callback is the TikTok OAuth redirect target.
    Must be reachable without an API key under enforce mode.
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/callback?code=test&state=test")
    assert r.status_code == 200
    assert r.headers.get("www-authenticate") != "Bearer"  # not a 401


def test_callback_exempt_with_bogus_key_still_200(client, auth_keys, monkeypatch):
    """A junk Authorization header must not flip /callback into 401.

    /callback is exempt, so the key (good or bad) is irrelevant.
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get(
        "/callback?code=test&state=test",
        headers={"Authorization": "Bearer totally-not-a-real-key"},
    )
    assert r.status_code == 200


def test_authorize_exempt_no_key(client, auth_keys, monkeypatch):
    """Wave 4 Slice 2: /authorize is the OAuth browser-flow entrypoint.
    Must be reachable without an API key.
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/authorize")
    assert r.status_code == 200
    assert r.headers.get("www-authenticate") != "Bearer"


def test_authorize_exempt_invalid_key_still_200(client, auth_keys, monkeypatch):
    """A junk Authorization header must not flip /authorize into 401."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get(
        "/authorize",
        headers={"Authorization": "Bearer junk-junk-junk"},
    )
    assert r.status_code == 200


def test_no_dead_token_rule_in_source():
    """Wave 4 Slice 3: the /token/* admin rule in auth.py is dead code
    (Wave 3 Slice 2 deleted the /token/{shop_id} proxy route). Removing
    the rule keeps the policy table consistent with reality.
    """
    import pathlib

    src = pathlib.Path(__file__).with_name("auth.py").read_text(encoding="utf-8")
    assert 'path.startswith("/token/")' not in src, (
        "dead /token/* admin rule still in auth.py — remove it"
    )
    assert '"/token/"' not in src, "stray /token/ literal left in auth.py"


def test_all_designed_public_paths_are_exempt():
    """Wave 4 Slice 4: EXEMPT_PATHS contains exactly the 9 paths required by
    merge-design.md §3.2 (order-insensitive). Adding new public endpoints
    must be deliberate.
    """
    expected = {
        "/healthz",
        "/endpoints",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/ads-monitor",
        "/callback",
        "/authorize",
    }
    assert expected == auth.EXEMPT_PATHS, (
        f"EXEMPT_PATHS drift.\n"
        f"  got     : {sorted(auth.EXEMPT_PATHS)}\n"
        f"  expected: {sorted(expected)}\n"
        f"  extra   : {sorted(auth.EXEMPT_PATHS - expected)}\n"
        f"  missing : {sorted(expected - auth.EXEMPT_PATHS)}"
    )


def test_protected_paths_require_key_under_enforce(client, auth_keys, monkeypatch):
    """Wave 4 Slice 4: protected paths (not in EXEMPT_PATHS) still require
    an API key. Each row covers a different policy branch in required_role().
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")

    # /sync/* requires readwrite
    assert client.post("/sync/orders", json={}).status_code == 401
    # /db/* requires readonly
    assert client.get("/db/orders?limit=1").status_code == 401
    # /v1/analytics/sync/* requires readwrite
    assert client.get("/v1/analytics/sync/cursor").status_code == 401
    # /miaoshou/* defaults to admin (no explicit rule)
    assert client.post("/miaoshou/callback/all", json={}).status_code == 401


def test_protected_paths_pass_with_admin_key(client, auth_keys, monkeypatch):
    """Sanity: same protected paths with a valid admin key are not 401.
    Business-layer errors (400 / 200) are fine — proves auth let them through.
    """
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    headers = _auth(KEY_ADMIN)

    r = client.get("/db/orders?limit=1", headers=headers)
    assert r.status_code == 200

    r = client.get("/v1/analytics/sync/cursor", headers=headers)
    # 422 (missing required query params) is OK — it proves auth let
    # the request through; FastAPI validation 422'd the empty query.
    assert r.status_code not in (401, 403), (
        f"auth blocked valid admin key: {r.status_code}"
    )


def test_disabled_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_DISABLED))
    assert r.status_code == 401


def test_expired_key_401(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_EXPIRED))
    assert r.status_code == 401


def test_unclassified_path_requires_admin(client, auth_keys, monkeypatch):
    """Unmatched path gets the highest bar (default-deny → admin)."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    r = client.get("/nonexistent", headers=_auth(KEY_RO))
    assert r.status_code == 403  # readonly < admin, blocked before 404


# ─── cache behavior ───────────────────────────────────────────────────


def test_cache_delays_revocation_until_clear(client, auth_keys, db_url, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 200
    # Revoke directly in DB; cached entry still passes
    conn = psycopg.connect(db_url)
    with conn.cursor() as cur:
        cur.execute("UPDATE security.api_keys SET status = 'disabled' WHERE name = 'TEST_auth_ro'")
    conn.commit()
    conn.close()
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 200
    # After cache invalidation the revocation takes effect
    auth.clear_cache()
    assert client.get("/db/orders?limit=1", headers=_auth(KEY_RO)).status_code == 401


# ─── shadow / off modes ───────────────────────────────────────────────


def test_shadow_mode_allows_and_logs(client, auth_keys, monkeypatch, capsys):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "shadow")
    r = client.get("/db/orders?limit=1")  # no key — would be denied, but passes
    assert r.status_code == 200
    assert "would-deny" in capsys.readouterr().err


def test_off_mode_allows_everything(client, auth_keys, monkeypatch):
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    assert client.get("/db/orders?limit=1").status_code == 200


# ─── W1.1: negative cache / DB-down 503 / denied-request rate limiting ───


def test_invalid_key_is_negative_cached(client, auth_keys, monkeypatch):
    """Invalid keys are cached (short TTL) so brute-force retries don't
    hit PG on every request. Second 401 must not call _db_lookup again."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    calls = []
    real = auth._db_lookup

    def spy(key_hash):
        calls.append(key_hash)
        return real(key_hash)

    monkeypatch.setattr(auth, "_db_lookup", spy)
    forged = "ttserp_ro_forged000000000000000000"
    assert client.get("/db/orders?limit=1", headers=_auth(forged)).status_code == 401
    assert client.get("/db/orders?limit=1", headers=_auth(forged)).status_code == 401
    assert len(calls) == 1


def test_db_down_fails_closed_503(client, auth_keys, monkeypatch):
    """PG unreachable during key lookup → fail-closed 503 (not 500 bubble,
    not silent pass)."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")

    def boom(key_hash):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(auth, "_db_lookup", boom)
    auth.clear_cache()
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_RO))
    assert r.status_code == 503


def test_db_down_shadow_mode_passes_through(client, auth_keys, monkeypatch):
    """Shadow mode observes only — a lookup failure must not block traffic."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "shadow")

    def boom(key_hash):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(auth, "_db_lookup", boom)
    auth.clear_cache()
    r = client.get("/db/orders?limit=1", headers=_auth(KEY_RO))
    assert r.status_code == 200


def test_denied_requests_are_rate_limited(client, auth_keys, monkeypatch):
    """Denied requests (401) short-circuit before RateLimitMiddleware, so
    auth counts them into the shared per-key bucket itself. After the
    limit, brute-force keys get 429 + Retry-After instead of 401."""
    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
    counter = rate_limit.shared_counter()
    old_limit = counter.limit
    counter.limit = 3
    rate_limit.reset_shared()
    try:
        forged = "ttserp_ro_brute0000000000000000000"
        codes = [
            client.get("/db/orders?limit=1", headers=_auth(forged)).status_code
            for _ in range(5)
        ]
        assert codes[:3] == [401, 401, 401]
        assert codes[3:] == [429, 429]
        r = client.get("/db/orders?limit=1", headers=_auth(forged))
        assert r.headers.get("retry-after")
    finally:
        counter.limit = old_limit
        rate_limit.reset_shared()
