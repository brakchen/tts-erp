"""Adversarial tests for Wave 4 auth whitelist (EXEMPT_PATHS + dead code removal).

QA target: tdd/auth.py EXEMPT_PATHS set and required_role() policy.
Reference: merge-design.md §3.2.
Author: Wave 4 QA agent (third-party / adversarial review).
"""

from __future__ import annotations
import hashlib
import psycopg
import pytest
from fastapi.testclient import TestClient
import auth
from tts_erp_fastapi import app

# ─── fixtures ──────────────────────────────────────────────────────────

KEY_RO = "ttserp_ro_ADVERSARIALreadonlykey0000000000"
KEY_RW = "ttserp_rw_ADVERSARIALreadwritekey00000000"
KEY_ADMIN = "ttserp_admin_ADVERSARIALadminkey00000000"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture()
def adv_keys(db_url):
    """Insert adversarial test keys and clean up after."""
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM security.api_keys WHERE name LIKE 'ADVERSARIAL_%'")
            cur.executemany(
                "INSERT INTO security.api_keys (key_hash, key_prefix, name, role, status)"
                " VALUES (%s, %s, %s, %s, %s)",
                [
                    (
                        _sha256(KEY_RO),
                        KEY_RO[:16],
                        "ADVERSARIAL_auth_ro",
                        "readonly",
                        "active",
                    ),
                    (
                        _sha256(KEY_RW),
                        KEY_RW[:16],
                        "ADVERSARIAL_auth_rw",
                        "readwrite",
                        "active",
                    ),
                    (
                        _sha256(KEY_ADMIN),
                        KEY_ADMIN[:16],
                        "ADVERSARIAL_auth_admin",
                        "admin",
                        "active",
                    ),
                ],
            )
        conn.commit()
        auth.clear_cache()
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM security.api_keys WHERE name LIKE 'ADVERSARIAL_%'")
        conn.commit()
        conn.close()
        auth.clear_cache()


@pytest.fixture()
def client():
    return TestClient(app)


def _collect_all_paths():
    """Walk app.routes recursively, unwrapping _IncludedRouter.

    app.include_router(...) creates an _IncludedRouter wrapper whose
    .original_router.routes holds the real APIRoute objects. We must
    walk into those, otherwise /callback /authorize /healthz (mounted
    via oauth_router) are invisible to this test.

    Use getattr() because BaseRoute abstract class doesn't declare
    .methods/.path/.routes as known attributes — they're added by
    concrete Route subclasses.
    """
    paths: set[str] = set()

    def walk(routes):
        for r in routes:
            methods = getattr(r, "methods", None)
            path = getattr(r, "path", None)
            if methods and path:
                paths.add(path)
            # _IncludedRouter has .original_router (not .routes directly)
            original = getattr(r, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", []) or [])
            # Mount routes have .routes directly
            inner = getattr(r, "routes", None)
            if inner and original is None:
                walk(inner)

    walk(list(app.routes))
    return paths


# ─── required_role() unit tests (no FastAPI roundtrip) ────────────────


class TestExemptPathsContract:
    """Verify EXEMPT_PATHS is exactly the merge-design §3.2 set.

    Drifting the set is a security event — either an attacker opened
    a backdoor (extras) or a critical public endpoint broke (missing).
    """

    def test_exempt_paths_exact_match_to_design(self):
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
            f"EXEMPT_PATHS drifted:\n"
            f"  got      {sorted(auth.EXEMPT_PATHS)}\n"
            f"  expected {sorted(expected)}"
        )

    def test_exempt_paths_does_not_contain_token(self):
        # Per Wave 4 Slice 3: /token/* was a proxy route that no longer
        # exists. It must not be in EXEMPT_PATHS (would expose token
        # reveal endpoint publicly if it ever comes back).
        assert "/token" not in auth.EXEMPT_PATHS
        assert "/token/" not in auth.EXEMPT_PATHS

    def test_no_auth_rule_for_token_in_source(self):
        import pathlib

        src = pathlib.Path(auth.__file__).read_text(encoding="utf-8")
        # No /token/ rule should be left in auth.py
        assert '"/token/"' not in src
        assert 'path.startswith("/token/")' not in src, (
            "dead /token/* admin rule re-added to auth.py"
        )

    def test_callback_and_authorize_return_none_required_role(self):
        # unit-level: required_role() returns None (exempt)
        assert auth.required_role("GET", "/callback") is None
        assert auth.required_role("GET", "/authorize") is None

    def test_unprotected_path_returns_admin_role(self):
        # default-deny: unmatched paths get the highest bar
        assert auth.required_role("GET", "/foo/bar") == 3
        assert auth.required_role("POST", "/anything") == 3

    def test_callback_with_traversal_does_not_match_exempt(self):
        # path normalization happens at FastAPI level, not in required_role.
        # Document that required_role is path-EXACT, not prefix.
        # If someone later changes required_role to prefix-match, the
        # exempt set becomes a backdoor for /callback-anything.
        assert auth.required_role("GET", "/callbackxyz") == 3  # admin, not exempt


# ─── FastAPI roundtrip — enforce mode ─────────────────────────────────


class TestEnforceModePublicEndpoints:
    """Verify public endpoints return 200 in enforce mode WITHOUT a key."""

    def test_callback_no_key_enforce_returns_200(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.get("/callback?code=test&state=test")
        assert r.status_code == 200
        # Not a 401: no www-authenticate header
        assert r.headers.get("www-authenticate") != "Bearer"

    def test_callback_junk_key_still_200(self, client, adv_keys, monkeypatch):
        """A junk Authorization header must not flip /callback into 401."""
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.get(
            "/callback?code=test&state=test",
            headers={"Authorization": "Bearer totally-not-a-real-key"},
        )
        assert r.status_code == 200, (
            f"junk key on /callback gave {r.status_code}; exempt is broken"
        )

    def test_authorize_no_key_enforce_returns_200(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.get("/authorize")
        assert r.status_code == 200
        assert r.headers.get("www-authenticate") != "Bearer"

    def test_callback_post_returns_405(self, client, adv_keys, monkeypatch):
        """POST /callback is not a defined route. Auth runs before
        method dispatch, so exempt applies; but FastAPI should 405
        because only GET handler exists.

        We accept either 405 (router-level reject) or 200 (if some
        other handler caught it). The critical thing: NOT 401/403.
        """
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.post("/callback?code=test&state=test")
        assert r.status_code not in (401, 403), (
            f"POST /callback gave {r.status_code}; exempt path wrongly "
            f"triggered auth — leak via auth-bypass on POST is a bug"
        )

    def test_healthz_still_exempt_regression(self, client, adv_keys, monkeypatch):
        """Wave 4 did not break /healthz exemption."""
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_callback_exempt_does_not_leak_key_in_response(
        self, client, adv_keys, monkeypatch
    ):
        """Critical security property: even when a valid key IS sent
        to an exempt endpoint, the handler must NOT receive any
        indication of the key (no echo, no log, no scope-based
        behavior change).

        We verify the response body does not contain the key material.
        """
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        r = client.get(
            "/callback?code=test&state=test",
            headers=_auth(KEY_ADMIN),
        )
        assert r.status_code == 200
        # Response body should not echo any auth header material
        assert KEY_ADMIN not in r.text
        assert "Bearer" not in r.text


class TestEnforceModeProtectedPaths:
    """Verify protected paths still 401 without a key in enforce mode."""

    def test_sync_orders_no_key_401(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        assert client.post("/sync/orders", json={}).status_code == 401

    def test_db_orders_no_key_401(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        assert client.get("/db/orders?limit=1").status_code == 401

    def test_analytics_cursor_no_key_401(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        assert client.get("/v1/analytics/sync/cursor").status_code == 401

    def test_miaoshou_callback_no_key_401(self, client, adv_keys, monkeypatch):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        assert client.post("/miaoshou/callback/all", json={}).status_code == 401

    def test_token_route_no_longer_reachable(self, client, adv_keys, monkeypatch):
        """The legacy /token/{shop_id} proxy was deleted in Wave 3
        Slice 2. Verify it now returns 404 (or 401 if auth fires first
        — both are safe; the point is admin key does NOT reveal tokens).
        """
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        # Even with admin key, the route is gone
        r = client.get("/token/7494763368967603447", headers=_auth(KEY_ADMIN))
        assert r.status_code in (401, 403, 404), (
            f"GET /token/<id> with admin key returned {r.status_code} — "
            f"the deleted route may have come back, or admin can now read tokens"
        )
        # Body must not contain any token material
        assert "access_token" not in r.text
        assert "ROW_" not in r.text

    def test_admin_key_does_not_make_protected_path_open(
        self, client, adv_keys, monkeypatch
    ):
        """A valid key is REQUIRED for protected paths — verify
        admin key gets through but readonly key on /sync/* is still 403.
        """
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "enforce")
        # Admin on /sync/orders → reaches business layer (400 from missing body)
        r = client.post("/sync/orders", headers=_auth(KEY_ADMIN), json={})
        assert r.status_code in (200, 400), (
            f"admin on /sync/orders blocked: {r.status_code}"
        )
        # Readonly on /sync/orders → 403 (insufficient role)
        r = client.post("/sync/orders", headers=_auth(KEY_RO), json={})
        assert r.status_code == 403, (
            f"readonly should be 403 on /sync/orders, got {r.status_code}"
        )


# ─── shadow / off mode coverage for the new public endpoints ──────────


class TestShadowAndOffModes:
    """Wave 4: shadow/off modes must work correctly with the expanded
    EXEMPT_PATHS."""

    def test_shadow_mode_logs_would_deny_on_protected(
        self, client, adv_keys, monkeypatch, capsys
    ):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "shadow")
        r = client.get("/db/orders?limit=1")  # no key
        assert r.status_code == 200  # shadow lets it through
        err = capsys.readouterr().err
        assert "would-deny" in err, f"shadow mode did not log would-deny; got: {err!r}"
        assert "401" in err  # the would-be-deny status

    def test_off_mode_no_auth_no_log(self, client, adv_keys, monkeypatch, capsys):
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
        r = client.get("/db/orders?limit=1")
        assert r.status_code == 200
        err = capsys.readouterr().err
        # off mode never logs anything auth-related
        assert "would-deny" not in err
        assert "[auth]" not in err

    def test_callback_in_shadow_mode_still_200_no_log(
        self, client, adv_keys, monkeypatch, capsys
    ):
        """Exempt + shadow: no key, no log, 200. Critical because
        cpolar tunnel sees anonymous /callback traffic and we don't
        want to spam logs."""
        monkeypatch.setenv("TTS_ERP_AUTH_MODE", "shadow")
        r = client.get("/callback?code=test&state=test")
        assert r.status_code == 200
        err = capsys.readouterr().err
        assert "would-deny" not in err, (
            "exempt endpoint incorrectly logged under shadow mode"
        )


# ─── bypass-attempt adversarial tests ─────────────────────────────────


class TestBypassAttempts:
    """Try to bypass the whitelist via path tricks."""

    def test_path_traversal_prefix_does_not_match_exempt(self, monkeypatch):
        """required_role() uses exact match (in EXEMPT_PATHS), not prefix.
        Verifies that '/callback../etc/passwd' is NOT exempt.
        """
        # /callback/../admin etc. would not reach this function —
        # FastAPI normalizes paths first — but defense-in-depth.
        assert auth.required_role("GET", "/callback/../admin") == 3
        assert auth.required_role("GET", "/callbackbar") == 3
        assert auth.required_role("GET", "/authorize_admin") == 3

    def test_callback_with_query_string_still_exempt(self, monkeypatch):
        # FastAPI parses path before auth; query is invisible to required_role
        assert auth.required_role("GET", "/callback") is None
        # (We don't pass query here; ASGI scope separates them.)

    def test_required_role_no_admin_token_rule(self):
        """The 'if path.startswith("/token/"): admin' branch is dead
        and removed. Verify: /token/foo falls through to default-deny
        (admin), proving the rule was successfully removed without
        weakening the default-deny behavior."""
        # Without explicit rule, falls through to ROLE_LEVEL["admin"]
        assert auth.required_role("GET", "/token/foo") == 3
        assert auth.required_role("GET", "/token/") == 3


# ─── route-surface verification ──────────────────────────────────────


class TestRouteSurface:
    """Static-introspection verification: merged app has no /token/* route."""

    @staticmethod
    def _collect_all_paths():
        return _collect_all_paths()

    def test_no_token_routes_in_app_routes(self):
        all_paths = _collect_all_paths()
        token_paths = {p for p in all_paths if "/token" in p}
        assert token_paths == set(), (
            f"/token/* paths unexpectedly reachable in merged app: {token_paths}"
        )

    def test_callback_and_authorize_and_healthz_in_app(self):
        all_paths = _collect_all_paths()
        for required in ("/callback", "/authorize", "/healthz"):
            assert required in all_paths, f"{required} not found in merged app routes"
