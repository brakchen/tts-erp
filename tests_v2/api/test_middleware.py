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
    """Burst above the per-key limit → 429 + Retry-After header."""
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


def test_auth_mode_off_lets_requests_through(api_client_off):
    """When TTS_ERP_AUTH_MODE=off, no auth header is needed."""
    r = api_client_off.get("/v2/commerce/sales-orders")
    # 200, not 401
    assert r.status_code == 200, r.text
