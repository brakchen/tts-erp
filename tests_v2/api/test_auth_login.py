"""Browser login + session-cookie tests (see tech-doc/browser-login-design.md).

Covers:
- public login page (GET) + POST key validation (valid / invalid / disabled)
- session cookie issuance flags + /v2/auth/me
- cookie-based access to protected pages + data (no Authorization header)
- role enforcement for cookie sessions (readonly vs readwrite) + CSRF header guard
- tampered / expired cookies rejected
- logout clears the session
- revoked key kills the session (DB re-check per request)
- browser 302 redirect to login (Accept: text/html) + /tts external prefix
- API clients keep JSON 401 (Accept: */* or application/json)
- login brute-force throttle (429)
"""

from __future__ import annotations

import time

from sqlalchemy import update

from tts_erp_v2.db.base import Base
from tts_erp_v2.middleware import session_auth
from tts_erp_v2.middleware.auth import clear_cache


def _login(client, key: str, *, expect: int = 200):
    r = client.post("/v2/auth/login", json={"key": key})
    assert r.status_code == expect, r.text
    return r


# ---------------------------------------------------------------- login page


def test_login_page_public_no_auth(api_client):
    r = api_client.get("/v2/auth/login")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="key"' in r.text


def test_login_success_sets_session_cookie(api_client, readwrite_key):
    r = _login(api_client, readwrite_key)
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "tts_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie  # TTS_ERP_SESSION_SECURE=0 in test env


def test_login_secure_cookie_when_enabled(api_client, readwrite_key, monkeypatch):
    monkeypatch.setenv("TTS_ERP_SESSION_SECURE", "1")
    r = _login(api_client, readwrite_key)
    assert "secure" in r.headers.get("set-cookie", "").lower()


def test_login_invalid_key_401(api_client):
    r = api_client.post(
        "/v2/auth/login", json={"key": "ttserp_admin_not_a_real_key_zz"}
    )
    assert r.status_code == 401, r.text
    assert "set-cookie" not in r.headers


def test_login_disabled_key_401(api_client, bad_key):
    r = api_client.post("/v2/auth/login", json={"key": bad_key})
    assert r.status_code == 401, r.text


def test_login_missing_key_422(api_client):
    r = api_client.post("/v2/auth/login", json={})
    assert r.status_code == 422, r.text


# ------------------------------------------------------------------ who am i


def test_me_unauthenticated(api_client):
    r = api_client.get("/v2/auth/me")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False}


def test_me_authenticated_after_login(api_client, readwrite_key):
    _login(api_client, readwrite_key)
    r = api_client.get("/v2/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["role"] == "readwrite"


# -------------------------------------------------- session cookie access


def test_session_cookie_opens_protected_page(api_client, readwrite_key):
    _login(api_client, readwrite_key)
    r = api_client.get("/v2/pages/manual-costs")  # no Authorization header
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")


def test_session_cookie_reads_data_endpoint(api_client, readwrite_key):
    _login(api_client, readwrite_key)
    r = api_client.get("/v2/reporting/missing-cost-products?limit=5")
    assert r.status_code == 200, r.text


def test_session_role_readonly_cannot_post(api_client, readonly_key):
    _login(api_client, readonly_key)
    body = {
        "channel_product_external_id": "TEST_ext_ro_session",
        "unit_cost": "1",
        "currency": "USD",
    }
    r = api_client.post(
        "/v2/reporting/manual-costs",
        json=body,
        headers={"X-Requested-With": "tts-erp"},
    )
    assert r.status_code == 403, r.text


def test_session_readwrite_passes_role_check(api_client, readwrite_key):
    """readwrite session + CSRF header → auth passes; 404 = product not found."""
    _login(api_client, readwrite_key)
    body = {
        "channel_product_external_id": "TEST_ext_rw_session",
        "unit_cost": "1",
        "currency": "USD",
    }
    r = api_client.post(
        "/v2/reporting/manual-costs",
        json=body,
        headers={"X-Requested-With": "tts-erp"},
    )
    assert r.status_code == 404, r.text  # role OK, product missing → 404, not 401/403


def test_session_post_without_csrf_header_403(api_client, readwrite_key):
    """Cookie-authed POST without X-Requested-With → 403 (CSRF guard)."""
    _login(api_client, readwrite_key)
    body = {
        "channel_product_external_id": "TEST_ext_no_csrf",
        "unit_cost": "1",
        "currency": "USD",
    }
    r = api_client.post("/v2/reporting/manual-costs", json=body)
    assert r.status_code == 403, r.text
    assert "X-Requested-With" in r.text


# ------------------------------------------------------------ cookie safety


def test_tampered_cookie_rejected(api_client, readwrite_key):
    cookie = session_auth.mint_session_cookie(readwrite_key, "readwrite")
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Cookie": f"tts_session={tampered}"},
    )
    assert r.status_code == 401, r.text


def test_expired_cookie_rejected(api_client, readwrite_key):
    # exp = now_base + ttl; push the base past ttl so exp is already gone.
    cookie = session_auth.mint_session_cookie(
        readwrite_key,
        "readwrite",
        now=time.time() - session_auth.session_ttl_seconds() - 3600,
    )
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Cookie": f"tts_session={cookie}"},
    )
    assert r.status_code == 401, r.text


def test_logout_clears_session(api_client, readwrite_key):
    _login(api_client, readwrite_key)
    assert api_client.get("/v2/pages/manual-costs").status_code == 200
    r = api_client.post("/v2/auth/logout")
    assert r.status_code == 204, r.text
    assert api_client.get("/v2/pages/manual-costs").status_code == 401


def test_revoked_key_kills_session(api_client, readwrite_key, db_engine):
    _login(api_client, readwrite_key)
    assert api_client.get("/v2/pages/manual-costs").status_code == 200
    # Disable the key, drop the auth cache, then the session must die.
    tbl = Base.metadata.tables["security.api_keys"]
    with db_engine.begin() as conn:
        conn.execute(
            update(tbl).where(tbl.c.name == "TEST_readwrite").values(status="disabled")
        )
    clear_cache()
    r = api_client.get("/v2/pages/manual-costs")
    assert r.status_code == 401, r.text


# ----------------------------------------------------- browser redirect flow


def test_browser_redirect_to_login(api_client):
    r = api_client.get("/v2/pages/manual-costs", headers={"Accept": "text/html"})
    # TestClient follows redirects; the 302 is in history.
    assert r.history and r.history[0].status_code == 302, r.text
    assert (
        r.history[0].headers["location"] == "/v2/auth/login?next=/v2/pages/manual-costs"
    )


def test_browser_redirect_keeps_query(api_client):
    r = api_client.get(
        "/v2/pages/manual-costs?shop_id=749",
        headers={"Accept": "text/html"},
    )
    assert r.history and r.history[0].status_code == 302, r.text
    assert (
        r.history[0].headers["location"]
        == "/v2/auth/login?next=/v2/pages/manual-costs?shop_id=749"
    )


def test_browser_redirect_respects_external_prefix(api_client, monkeypatch):
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", "/tts")
    # Location path stays prefix-free (the NAT nginx re-adds /tts to
    # redirect headers); the next value carries the prefix (client-side
    # consumption bypasses the proxy).
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/tts/v2/auth/login?next=/v2/pages/manual-costs"


def test_api_accept_keeps_json_401(api_client):
    r = api_client.get(
        "/v2/pages/manual-costs",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 401, r.text
    assert "location" not in r.headers
    assert r.json()["detail"]


def test_login_page_validates_next(api_client):
    r = api_client.get("/v2/auth/login", params={"next": "https://evil.example/x"})
    assert r.status_code == 200
    assert 'value="/v2/pages/manual-costs"' in r.text
    assert "evil.example" not in r.text


def test_login_page_injects_next(api_client):
    r = api_client.get(
        "/v2/auth/login", params={"next": "/v2/pages/manual-costs?shop_id=749"}
    )
    assert r.status_code == 200
    assert 'value="/v2/pages/manual-costs?shop_id=749"' in r.text


# ------------------------------------------------------------- login throttle


def test_login_throttle_429(api_client, monkeypatch):
    monkeypatch.setenv("TTS_ERP_LOGIN_RATE_LIMIT", "3")
    session_auth.reset_login_throttle(3)
    statuses = []
    for _ in range(4):
        r = api_client.post("/v2/auth/login", json={"key": "ttserp_admin_nope_nope"})
        statuses.append(r.status_code)
    assert statuses[:3] == [401, 401, 401], statuses
    assert statuses[3] == 429, statuses
