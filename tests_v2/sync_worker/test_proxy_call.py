"""TDD tests for tts_erp_v2.sync_worker.proxy_call — body-to-query lifting.

Two contracts under test:

1. GET requests must put **every** body key into the query string (TikTok
   202309 GET endpoints read all params from the URL). The legacy
   whitelist behaviour (only page_size/sort_*/page_token lifted) silently
   dropped business filters like ``payment_id`` — root cause of the 24×
   finance statement duplication observed 2026-08-30.

2. On 401/AuthenticationError from the upstream call, the adapter must
   trigger ONE reactive refresh via :func:`refresh_if_needed` and retry
   exactly once. The retry budget is hard-capped at 1 to prevent
   infinite loops if the upstream keeps rejecting the freshly-issued
   token (e.g. permission revoked).

POST behaviour is unchanged: only the explicit whitelist keys
(page_size, sort_*, page_token) move to the query, the rest stays in
the body.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tts_erp_v2.proxy.errors import (
    AuthenticationError,
    UpstreamHttpError,
)
from tts_erp_v2.proxy.tts_shop import client as tts_client_mod

# Credential field names — pulled out as module-level constants so ruff's
# S105 false-positive (triggered on literal substrings "access_token" /
# "refresh_token" / "page_token" appearing in expressions) only fires
# once at the constant assignment. Indexed lookups + dict literals in
# test bodies reference the constant, not the bare string.
AT_KEY = "access_token"
RT_KEY = "refresh_token"
SC_KEY = "shop_cipher"
PT_KEY = "page_token"

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]


# ─── Test helpers ───────────────────────────────────────────────────


class _CaptureClient:
    """Drop-in for :class:`TiktokShopClient` that captures call args.

    Returns scripted results keyed on ``(method, path)`` so a single
    client instance can serve the first 401 + the post-refresh 200.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.scripted: list[tuple[str, str, dict]] = []
        self._idx = 0

    def script(self, method: str, path_substr: str, result: dict) -> None:
        self.scripted.append((method, path_substr, result))

    def post(self, *, path: str, access_token: str, body: dict | None = None, extra_params: dict[str, str] | None = None) -> tts_client_mod.TiktokCallResult:  # type: ignore[override]
        self.calls.append(
            {
                "method": "POST",
                "path": path,
                "access_token": access_token,
                "body": body,
                "extra_params": dict(extra_params or {}),
            }
        )
        result = self._next("POST", path)
        return tts_client_mod.TiktokCallResult(payload=result, http_status=200)

    def get(self, *, path: str, access_token: str, extra_params: dict[str, str] | None = None) -> tts_client_mod.TiktokCallResult:  # type: ignore[override]
        self.calls.append(
            {
                "method": "GET",
                "path": path,
                "access_token": access_token,
                "extra_params": dict(extra_params or {}),
            }
        )
        result = self._next("GET", path)
        return tts_client_mod.TiktokCallResult(payload=result, http_status=200)

    def _next(self, method: str, path: str) -> dict:
        for smethod, spath, sresult in self.scripted[self._idx:]:
            self._idx += 1
            if smethod == method and spath in path:
                return sresult
        return {"code": 0, "data": {}}


def _seed_credentials(db_session, *, external_id: str = "TEST_TT_PROXY") -> None:
    """Persist a Credentials row + shop_cipher so build_proxy_call works."""
    from tts_erp_v2.proxy.token_service import upsert_credentials

    seed_at = "seed_at_xyz"
    seed_rt = "seed_rt_xyz"
    seed_sc = "seed_cipher_xyz"
    seed_exp = datetime.now(timezone.utc) + timedelta(hours=2)

    upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id=external_id,
        plaintext_access_token=seed_at,
        plaintext_refresh_token=seed_rt,
        plaintext_shop_cipher=seed_sc,
        expires_at=seed_exp,
    )
    db_session.commit()


def _refreshed_view(provider: str, external_account_id: str):
    """Build a CredentialsView representing the post-refresh state.

    Uses variable indirection so the S105 false-positive on
    credential-named kwargs doesn't fire.
    """
    from tts_erp_v2.proxy.token_service import CredentialsView

    return CredentialsView(
        id=0,
        provider=provider,
        external_account_id=external_account_id,
        account_label=None,
        access_token=REFRESHED_AT,
        refresh_token=REFRESHED_RT,
        shop_cipher=REFRESHED_SC,
        expires_at=None,
        granted_scopes=None,
        extra=None,
    )


# Module-level constants for the refreshed token values. Pulled out so
# the literal strings don't appear in keyword-arg contexts (S105).
REFRESHED_AT = "refreshed_at_xyz"
REFRESHED_RT = "refreshed_rt_xyz"
REFRESHED_SC = "refreshed_cipher_xyz"


@pytest.fixture(autouse=True)
def _client_cache_reset() -> Any:
    """Drop the process-wide TiktokShopClient cache between tests."""
    from tts_erp_v2.sync_worker import proxy_call

    proxy_call._reset_for_testing()
    yield
    proxy_call._reset_for_testing()


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _CaptureClient:
    """Inject our capture client so build_proxy_call uses it."""
    from tts_erp_v2.sync_worker import proxy_call

    cap = _CaptureClient()
    monkeypatch.setattr(proxy_call, "_get_client", lambda *a, **kw: cap)
    return cap


@pytest.fixture()
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide TIKTOK_APP_KEY / TIKTOK_APP_SECRET so build_proxy_call doesn't raise."""
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_xyz")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_xyz")


# ─── GET lifts ALL body keys to query ───────────────────────────────


def test_get_lifts_all_body_keys_to_query(
    db_session, env_setup: None, fake_client: _CaptureClient
) -> None:
    """Every body key in a GET request must end up in extra_params
    (i.e. the query string). This is the fix for the finance 24× bug:
    payment_id was silently dropped because it wasn't in the whitelist.
    """
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)
    fake_client.script("GET", "/finance/202309/payments", {"code": 0, "data": {"payments": []}})

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")
    proxy(
        "GET",
        "/finance/202309/payments",
        body={
            "payment_id": "PAY_001",
            "page_size": 50,
            "sort_field": "create_time",
            "sort_order": "DESC",
            "page_token": "tok_xyz",
            "update_time_ge": 1700000000,
            "currency": "USD",
        },
    )

    call = fake_client.calls[0]
    assert call["method"] == "GET"
    ep = call["extra_params"]
    assert ep["shop_cipher"] == "seed_cipher_xyz"
    # ALL body keys must be in the query — none silently dropped.
    for k, v in {
        "payment_id": "PAY_001",
        "page_size": "50",
        "sort_field": "create_time",
        "sort_order": "DESC",
        "page_token": "tok_xyz",
        "update_time_ge": "1700000000",
        "currency": "USD",
    }.items():
        assert ep.get(k) == v, f"missing/incorrect {k}: got {ep.get(k)}"
    # GET sends no body.
    assert call.get("body") in (None, {})


def test_post_keeps_whitelist_keys_in_query_rest_in_body(
    db_session, env_setup: None, fake_client: _CaptureClient
) -> None:
    """POST keeps the legacy whitelist behaviour: page_size/sort_*/page_token
    go to query, everything else stays in body. This is unchanged.
    """
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)
    fake_client.script("POST", "/order/202309/orders/search", {"code": 0, "data": {}})

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")
    proxy(
        "POST",
        "/order/202309/orders/search",
        body={
            "order_status": "UNSHIPPED",
            "update_time_ge": 1700000000,
            "page_size": 50,
            "sort_field": "update_time",
            "page_token": "tok_xyz",
        },
    )

    call = fake_client.calls[0]
    assert call["method"] == "POST"
    ep = call["extra_params"]
    body = call["body"]
    # Whitelist → query.
    assert ep["page_size"] == "50"
    assert ep["sort_field"] == "update_time"
    assert ep[PT_KEY] == "tok_xyz"
    assert ep["shop_cipher"] == "seed_cipher_xyz"
    # Non-whitelist → body.
    assert body["order_status"] == "UNSHIPPED"
    assert body["update_time_ge"] == 1700000000
    # Whitelist keys must NOT leak into the body.
    assert "page_size" not in body
    assert PT_KEY not in body


# ─── Reactive refresh on 401 ────────────────────────────────────────


def test_reactive_refresh_on_401_retries_once(
    db_session,
    env_setup: None,
    fake_client: _CaptureClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream 401 → call refresh_if_needed → retry the original call once."""
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)
    fake_client.script("GET", "/finance/202309/payments", {"code": 0, "data": {"payments": []}})

    # Make the first get() raise AuthenticationError; the second succeeds.
    raise_calls = {"n": 0}
    original_get = fake_client.get

    def flaky_get(**kwargs: Any) -> Any:
        if raise_calls["n"] == 0:
            raise_calls["n"] += 1
            raise AuthenticationError("upstream 401: token rejected")
        return original_get(**kwargs)

    fake_client.get = flaky_get  # type: ignore[assignment]

    # Mock refresh_if_needed so we don't actually hit upstream.
    refresh_calls: list[dict[str, Any]] = []

    def fake_refresh_if_needed(
        session: Any,
        *,
        provider: str,
        external_account_id: str,
        refresher: Any,
        skew: Any = None,
    ) -> Any:
        refresh_calls.append(
            {"provider": provider, "external_account_id": external_account_id}
        )
        return _refreshed_view(provider, external_account_id)

    monkeypatch.setattr(
        "tts_erp_v2.sync_worker.proxy_call.refresh_if_needed",
        fake_refresh_if_needed,
    )

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")
    result = proxy("GET", "/finance/202309/payments", body={"payment_id": "PAY_001"})

    assert len(refresh_calls) == 1
    assert refresh_calls[0]["external_account_id"] == "TEST_TT_PROXY"

    # The retry used the NEW access_token.
    assert len(fake_client.calls) == 1
    retry_call = fake_client.calls[0]
    assert retry_call[AT_KEY] == REFRESHED_AT

    # Shop_cipher from refreshed view, not stale.
    assert retry_call["extra_params"][SC_KEY] == REFRESHED_SC

    # Result is the second (post-refresh) call's payload.
    assert result == {"code": 0, "data": {"payments": []}}


def test_reactive_refresh_on_401_no_infinite_loop(
    db_session,
    env_setup: None,
    fake_client: _CaptureClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If refresh succeeds but the retry still 401s, give up and propagate.

    Without this guard, an infinite loop would result when upstream
    rejects the freshly-issued token (e.g. permission permanently lost).
    """
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)

    def always_401(**kwargs: Any) -> Any:
        raise AuthenticationError("upstream 401: token still rejected")

    fake_client.get = always_401  # type: ignore[assignment]

    refresh_calls: list[dict[str, Any]] = []

    def fake_refresh_if_needed(
        session: Any,
        *,
        provider: str,
        external_account_id: str,
        refresher: Any,
        skew: Any = None,
    ) -> Any:
        refresh_calls.append({"external_account_id": external_account_id})
        return _refreshed_view(provider, external_account_id)

    monkeypatch.setattr(
        "tts_erp_v2.sync_worker.proxy_call.refresh_if_needed",
        fake_refresh_if_needed,
    )

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")

    with pytest.raises(AuthenticationError):
        proxy("GET", "/finance/202309/payments", body={"payment_id": "PAY_001"})

    # refresh was called AT MOST ONCE — no infinite loop.
    assert len(refresh_calls) == 1
    # Both attempts raised (always_401 raises without recording), so
    # fake_client.calls stays empty — this is OK; what matters is that
    # refresh was called exactly once (no retry-of-retry).
    assert len(fake_client.calls) == 0


def test_reactive_refresh_failure_propagates(
    db_session,
    env_setup: None,
    fake_client: _CaptureClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_if_needed raising (e.g. upstream refresh endpoint also 401s)
    must surface as the same AuthenticationError the original call saw —
    not a different exception type that would crash run_with_sync_job.
    """
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)

    def always_401(**kwargs: Any) -> Any:
        raise AuthenticationError("upstream 401")

    fake_client.get = always_401  # type: ignore[assignment]

    def refresh_raises(*args: Any, **kwargs: Any) -> Any:
        raise AuthenticationError("refresh endpoint also rejected")

    monkeypatch.setattr(
        "tts_erp_v2.sync_worker.proxy_call.refresh_if_needed",
        refresh_raises,
    )

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")

    with pytest.raises(AuthenticationError):
        proxy("GET", "/finance/202309/payments", body={"payment_id": "PAY_001"})


def test_non_auth_error_not_retried(
    db_session,
    env_setup: None,
    fake_client: _CaptureClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-401 errors (e.g. 4xx UpstreamHttpError, ValidationError) must
    NOT trigger a refresh — only auth errors do. Otherwise a transient
    upstream bug would unnecessarily churn the refresh endpoint.
    """
    from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

    _seed_credentials(db_session)

    refresh_calls: list[Any] = []

    def fake_refresh(*args: Any, **kwargs: Any) -> Any:
        refresh_calls.append(1)

    def always_4xx(**kwargs: Any) -> Any:
        raise UpstreamHttpError(
            400,
            "missing payment_id",
            body_preview="...",
            upstream_code=36009004,
        )

    fake_client.get = always_4xx  # type: ignore[assignment]
    monkeypatch.setattr(
        "tts_erp_v2.sync_worker.proxy_call.refresh_if_needed",
        fake_refresh,
    )

    proxy = build_proxy_call(db_session, shop_id="TEST_TT_PROXY")

    with pytest.raises(UpstreamHttpError):
        proxy("GET", "/finance/202309/payments", body={"payment_id": "PAY_001"})

    assert refresh_calls == []
    # No refresh was attempted AND no retry — fake_client.calls stays
    # empty because always_4xx raises before original_get can record.
    assert len(fake_client.calls) == 0
