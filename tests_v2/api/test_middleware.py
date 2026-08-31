"""Auth + rate-limit middleware behavior on top of tts_erp_v2 models.

These tests pin the role behavior that v2 endpoints will rely on:
- 401 when no key, when key is unknown, when key is disabled/expired
- 403 when role is below the path requirement
- 200 when role meets requirement
- /healthz and /endpoints are public even in enforce mode
- Rate limit: sliding-window per-key bucket caps requests to limit
  and returns 429 with Retry-After
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tts_erp_v2.app import app


pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def test_healthz_public_when_auth_enforce(api_client):
    """healthz must be reachable without any Authorization header."""
    r = api_client.get("/healthz")
    assert r.status_code == 200, r.text


def test_endpoints_public_when_auth_enforce(api_client):
    """endpoints index is exempt — operator UX."""
    r = api_client.get("/endpoints")
    # endpoints may list nothing in v2-only mode; 200 is the contract
    assert r.status_code == 200, r.text


def test_v2_endpoint_requires_key(api_client):
    """No Authorization header → 401."""
    r = api_client.get("/v2/commerce/sales-orders")
    assert r.status_code == 401, r.text
    assert "bearer" in r.text.lower() or "missing" in r.text.lower()


def test_v2_endpoint_rejects_unknown_key(api_client):
    """A key whose hash is not in api_keys → 401."""
    fake = "ttserp_admin_definitely_not_a_real_key_zz"
    r = api_client.get(
        "/v2/commerce/sales-orders",
        headers={"Authorization": f"Bearer {fake}"},
    )
    assert r.status_code == 401, r.text


def test_v2_endpoint_rejects_disabled_key(api_client, bad_key):
    """A disabled key → 401."""
    r = api_client.get(
        "/v2/commerce/sales-orders",
        headers={"Authorization": f"Bearer {bad_key}"},
    )
    assert r.status_code == 401, r.text


def test_readonly_cannot_post_manual_costs(api_client, readonly_key):
    """readonly role is below readwrite; POST /v2/reporting/manual-costs → 403."""
    body = {
        "channel_product_external_id": "TEST_ext_ro_post",
        "unit_cost": "12.34",
        "currency": "USD",
    }
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json=body,
    )
    assert r.status_code == 403, r.text


def test_readwrite_can_read_commerce(api_client, readwrite_key):
    """readwrite role satisfies /v2/commerce/* GET (which is readonly)."""
    r = api_client.get(
        "/v2/commerce/sales-orders",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text


def test_admin_satisfies_all_endpoints(api_client, admin_key):
    """admin role (level 3) satisfies both readonly and readwrite paths."""
    r = api_client.get(
        "/v2/commerce/sales-orders",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text


def test_x_api_key_header_also_accepted(api_client, readonly_key):
    """X-API-Key header is the documented fallback to Authorization."""
    r = api_client.get(
        "/v2/commerce/sales-orders",
        headers={"X-API-Key": readonly_key},
    )
    assert r.status_code == 200, r.text


def test_rate_limit_returns_429_with_retry_after(api_client, readonly_key, monkeypatch):
    """Burst above the per-key limit → 429 with Retry-After header."""
    # Force a tiny limit so the test stays fast.
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "3")
    from tts_erp_v2.middleware.rate_limit import reset_shared

    reset_shared(limit=3)
    headers = {"Authorization": f"Bearer {readonly_key}"}
    last_status = None
    last_retry = None
    for _ in range(8):
        r = api_client.get("/v2/commerce/sales-orders", headers=headers)
        last_status = r.status_code
        if r.status_code == 429:
            last_retry = r.headers.get("retry-after")
            break
    reset_shared()
    assert last_status == 429, last_status
    assert last_retry is not None
    assert int(last_retry) >= 1


# ---------------------------------------------------------------------------
# 2026-09-01: regression coverage for the daqiang.nat100.top redirect loop.
# NGINX in production was observed forwarding some routes with the
# TTS_ERP_EXTERNAL_PREFIX intact (not stripped by ``proxy_pass ... /;``)
# while stripping it for others. The auth middleware classified against
# canonical internal paths (e.g. ``/v2/auth/login``), missed the prefixed
# forms, and produced an infinite 302 chain. These tests pin the fix:
# strip the prefix in required_role() and in the 302 ``next`` value so
# classification is idempotent regardless of what NGINX forwards.
# ---------------------------------------------------------------------------


def test_prefixed_login_path_is_exempt(api_client, readonly_key, monkeypatch):
    """``/tts/v2/auth/login`` (with prefix) must render 200, not 302.

    Pre-fix this returned 302 → /tts/v2/auth/login?next=/tts/v2/auth/login?…
    The login page itself is in EXEMPT_PATHS at /v2/auth/login (no prefix),
    so without the strip the prefixed form was treated as protected.
    """
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", "/tts")
    # Reload the module so the new env value is read by _strip_external_prefix
    # (which captures the env on each call; the AuthMiddleware reads it on
    # every request). No reload needed — the function is env-driven per call.
    r = api_client.get(
        "/tts/v2/auth/login",
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    assert r.status_code == 200, (
        f"prefixed login path 302-loop bug: got {r.status_code} "
        f"Location={r.headers.get('location')!r}\n{r.text[:200]}"
    )
    assert "login" in r.text.lower(), "expected login form HTML"


def test_prefixed_protected_path_redirect_uses_internal_next(
    api_client, monkeypatch
):
    """A protected route hit with the prefix must 302 with ``next=<internal>``,
    NOT ``next=<prefixed>``. The login page prepends the prefix when
    rendering the form's hidden field, so passing the prefixed form
    would double-stack on every redirect and produce an infinite loop.

    Unauthenticated request: the test relies on auth being enforced and
    the caller presenting no credentials. We deliberately do NOT take
    a `readonly_key` fixture to avoid any chance of the conftest
    auto-injecting a bearer.
    """
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", "/tts")
    # follow_redirects=False so we observe the 302 itself instead of
    # the auto-followed 200 login form. We're testing the Location
    # header contract, not the login form rendering.
    r = api_client.get(
        "/tts/v2/pages/manual-costs",
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert r.status_code == 302, (
        f"expected 302 → login (browser flow), got {r.status_code} "
        f"Location={r.headers.get('location')!r}\n{r.text[:200]}"
    )
    loc = r.headers.get("location", "")
    # Location must start with the prefixed login URL (browser navigation)
    assert loc.startswith("/tts/v2/auth/login?next="), loc
    # The next= value must be the INTERNAL path, not the prefixed path
    next_part = loc.split("next=", 1)[1]
    # Internal path must NOT start with /tts/ — that would compound on
    # each redirect. The login page will prepend /tts/ when rendering.
    assert not next_part.startswith("/tts/"), (
        f"next value still has the external prefix — loop bug: {next_part!r}"
    )
    assert next_part.startswith("/v2/"), (
        f"next value should be the internal path, got {next_part!r}"
    )


def test_prefixed_auth_path_does_not_compound(monkeypatch):
    """Walking the redirect chain with prefixed paths must terminate at
    the login form (200), not loop forever.

    Pre-fix: each redirect appended another /tts/v2/auth/login?next=… and
    the chain never terminated. Post-fix: the chain terminates at the
    first 200 (the login form).
    """
    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", "/tts")
    client = TestClient(app, follow_redirects=False)
    # Walk up to 10 redirects manually. Pre-fix this would never converge.
    url = "/tts/v2/pages/manual-costs"
    headers = {"Accept": "text/html,application/xhtml+xml"}
    seen = []
    for _ in range(10):
        r = client.get(url, headers=headers)
        seen.append((r.status_code, url))
        if r.status_code != 302:
            break
        loc = r.headers.get("location", "")
        # Resolve relative to the same host (TestClient uses base_url)
        if loc.startswith("/"):
            url = loc
        else:
            break
    # The chain must terminate (NOT all 302s)
    final_status = seen[-1][0]
    assert final_status == 200, (
        f"redirect chain did not terminate; saw: {seen}"
    )


def test_auth_mode_off_lets_requests_through(api_client_off):
    """When TTS_ERP_AUTH_MODE=off, no auth header is needed."""
    r = api_client_off.get("/v2/commerce/sales-orders")
    # 200, not 401
    assert r.status_code == 200, r.text
