"""Tests for the admin-only operational endpoints.

Pins the contract for ``GET /v2/admin/rate-limit`` and
``POST /v2/admin/reset-rate-limit``:

  - admin role can read + reset
  - readwrite / readonly are rejected with 403
  - anonymous is rejected with 401
  - ``new_limit`` is validated (1..1_000_000)
  - omitting ``new_limit`` re-reads the env var
  - ``reset_buckets`` flag controls whether existing per-key counts
    survive the reset
  - after a reset, the new limit takes effect for the very next
    request (regression guard: a key that was throttled at 429
    immediately stops getting 429 after the cap is raised)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ─── GET /v2/admin/rate-limit ──────────────────────────────────────────


def test_get_rate_limit_admin_returns_current_state(
    api_client, admin_key, monkeypatch
):
    """Admin sees the singleton's current limit + window + bucket count."""
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "1000")
    # Reset so the singleton picks up the new env value on next hit.
    from tts_erp_v2.middleware.rate_limit import reset_shared

    reset_shared()
    # Warm the singleton with one authenticated request.
    api_client.get(
        "/v2/llm-context?format=json",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    r = api_client.get(
        "/v2/admin/rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["env_var_name"] == "TTS_ERP_RATE_LIMIT_PER_MIN"
    assert body["env_var_current_value"] == "1000"
    assert body["env_var_effective_value"] == 1000
    assert body["limit"] == 1000
    assert body["window_s"] == 60
    assert body["middleware_initialized"] is True
    assert isinstance(body["active_buckets"], int)
    assert body["active_buckets"] >= 1


def test_get_rate_limit_rejects_non_admin(
    api_client, readwrite_key, readonly_key
):
    """readwrite and readonly get 403 — only admin can read the
    rate-limit config (it exposes cross-tenant state)."""
    for key, role in (
        (readwrite_key, "readwrite"),
        (readonly_key, "readonly"),
    ):
        r = api_client.get(
            "/v2/admin/rate-limit",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 403, (
            f"{role} should be 403, got {r.status_code}: {r.text}"
        )


def test_get_rate_limit_anonymous_is_401(api_client):
    """No bearer → 401 (auth middleware short-circuits before admin check)."""
    r = api_client.get("/v2/admin/rate-limit")
    assert r.status_code == 401


# ─── POST /v2/admin/reset-rate-limit ──────────────────────────────────


def test_post_reset_with_new_limit_admin(api_client, admin_key, monkeypatch):
    """POST with ``new_limit`` rebuilds the singleton and returns the
    change audit. The response body never echoes the bearer token —
    only its 12-char sha256 hex prefix."""
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "100")
    from tts_erp_v2.middleware.rate_limit import reset_shared

    reset_shared()
    r = api_client.post(
        "/v2/admin/reset-rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"new_limit": 500, "reset_buckets": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["old_limit"] in (None, 100)  # could be None if singleton was fresh
    assert body["new_limit"] == 500
    assert body["window_s"] == 60
    assert body["reset_buckets"] is True
    assert body["limit_source"] == "override"
    assert body["env_var_source"] == "TTS_ERP_RATE_LIMIT_PER_MIN"
    # 12-char sha256 hex prefix, NOT the full token
    assert isinstance(body["reset_by"], str)
    assert len(body["reset_by"]) == 12
    assert body["reset_by_role"] == "admin"
    # The full plaintext token must NEVER appear in the response.
    assert admin_key not in r.text


def test_post_reset_with_no_body_rereads_env(api_client, admin_key, monkeypatch):
    """POST with empty body ``{}`` re-reads ``TTS_ERP_RATE_LIMIT_PER_MIN``
    from the env var. This is the canonical hot-reload after editing
    ``.env`` without restarting the service."""
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "7777")
    from tts_erp_v2.middleware.rate_limit import reset_shared, shared_config

    reset_shared(limit=100)  # start with a known-different limit
    pre = shared_config()
    assert pre is not None, "reset_shared(limit=...) must create the singleton"
    assert pre["limit"] == 100
    r = api_client.post(
        "/v2/admin/reset-rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["old_limit"] == 100
    assert body["new_limit"] == 7777  # picked up from env
    assert body["limit_source"] == "env"
    post = shared_config()
    assert post is not None
    assert post["limit"] == 7777


def test_post_reset_buckets_false_preserves_counts(
    api_client, admin_key, monkeypatch
):
    """``reset_buckets=false`` keeps per-key deques — only the cap
    changes. Use case: raising the limit without invalidating
    in-flight state."""
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "100")
    from tts_erp_v2.middleware.rate_limit import reset_shared, shared_config

    # Sane cap (100) so the 2 warmup calls + 1 reset call don't hit 429
    # — the test's invariant is the bucket COUNT, not the cap.
    reset_shared(limit=100)
    for _ in range(2):
        api_client.get(
            "/v2/llm-context",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
    pre = shared_config()
    assert pre is not None
    assert pre["active_buckets"] >= 1

    # Reset with reset_buckets=false — the bucket count should survive.
    r = api_client.post(
        "/v2/admin/reset-rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"new_limit": 1000, "reset_buckets": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["new_limit"] == 1000
    assert body["buckets_cleared"] == 0
    assert body["active_buckets"] >= 1  # preserved


def test_post_reset_buckets_true_clears_counts(
    api_client, admin_key, monkeypatch
):
    """``reset_buckets=true`` (default) drops per-key deques — keys
    that were throttled at 429 get a fresh window."""
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "100")
    from tts_erp_v2.middleware.rate_limit import reset_shared, shared_config

    # Sane cap (100) so warmup + reset stay under the 60s budget.
    reset_shared(limit=100)
    for _ in range(2):
        api_client.get(
            "/v2/llm-context",
            headers={"Authorization": f"Bearer {admin_key}"},
        )
    pre = shared_config()
    assert pre is not None
    assert pre["active_buckets"] >= 1

    r = api_client.post(
        "/v2/admin/reset-rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"new_limit": 1000, "reset_buckets": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["buckets_cleared"] >= 1
    assert body["active_buckets"] == 0


def test_post_reset_validates_new_limit(api_client, admin_key):
    """``new_limit`` must be in [1, 1_000_000] — Pydantic enforces."""
    for bad in (0, -1, 1_000_001, 999_999_999):
        r = api_client.post(
            "/v2/admin/reset-rate-limit",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={"new_limit": bad},
        )
        assert r.status_code == 422, (
            f"new_limit={bad} should be 422, got {r.status_code}: {r.text}"
        )


def test_post_reset_rejects_non_admin(api_client, readwrite_key, readonly_key):
    """readwrite and readonly get 403 — only admin can mutate the
    rate-limit singleton."""
    for key, role in (
        (readwrite_key, "readwrite"),
        (readonly_key, "readonly"),
    ):
        r = api_client.post(
            "/v2/admin/reset-rate-limit",
            headers={"Authorization": f"Bearer {key}"},
            json={"new_limit": 500},
        )
        assert r.status_code == 403, (
            f"{role} should be 403, got {r.status_code}: {r.text}"
        )


def test_post_reset_anonymous_is_401(api_client):
    """No bearer → 401."""
    r = api_client.post(
        "/v2/admin/reset-rate-limit",
        json={"new_limit": 500},
    )
    assert r.status_code == 401


def test_post_reset_new_limit_takes_effect_immediately(
    api_client, admin_key, readwrite_key, monkeypatch
):
    """Regression guard: after a reset raises the cap, a key that was
    429-throttled immediately succeeds.

    Flow:
      1. Set cap=2 and fill the readwrite bucket
      2. The 3rd request returns 429
      3. Admin resets with new_limit=10, reset_buckets=true
      4. The same request now succeeds (200)
    """
    monkeypatch.setenv("TTS_ERP_RATE_LIMIT_PER_MIN", "2")
    from tts_erp_v2.middleware.rate_limit import reset_shared

    reset_shared(limit=2)
    headers = {"Authorization": f"Bearer {readwrite_key}"}
    # Fill the bucket — first 2 are within budget, 3rd is 429.
    for _ in range(2):
        r = api_client.get("/v2/llm-context", headers=headers)
        assert r.status_code == 200, (
            f"warming call should be 200, got {r.status_code}: {r.text}"
        )
    r_throttled = api_client.get("/v2/llm-context", headers=headers)
    assert r_throttled.status_code == 429, (
        f"3rd call should be 429 with cap=2, got {r_throttled.status_code}: "
        f"{r_throttled.text}"
    )
    assert r_throttled.headers.get("retry-after") is not None

    # Admin resets — raise cap to 10 and clear buckets.
    rr = api_client.post(
        "/v2/admin/reset-rate-limit",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"new_limit": 10, "reset_buckets": True},
    )
    assert rr.status_code == 200
    assert rr.json()["new_limit"] == 10

    # Same request that was 429 a moment ago must now succeed.
    r_after = api_client.get("/v2/llm-context", headers=headers)
    assert r_after.status_code == 200, (
        f"post-reset call should be 200, got {r_after.status_code}: "
        f"{r_after.text}"
    )
