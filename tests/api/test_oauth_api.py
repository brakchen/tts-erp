"""API contract tests for /v2/oauth/tiktok/{authorize,callback} — the
TikTok seller authorization flow (new-shop onboarding).

Full contract: tech-doc/api/tiktok-shop-oauth.md. Key facts tested here:

* ``authorize`` requires readwrite-or-above and returns a TikTok link
  carrying the registered single-use state. (Generation is harmless —
  a single-use CSRF row + a URL — so the threshold sits one notch
  above the readonly HTML shell it lives behind.)
* ``callback`` is PUBLIC (TikTok redirect target — no API key) and is
  the only place the flow does anything; bad/forged states fail closed.
* The happy path bootstraps ``integration.credentials`` +
  ``commerce.shops`` rows (TEST_ sentinel ids; wiped by conftest).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

AUTHZ = "/v2/oauth/tiktok/authorize"
CALLBACK = "/v2/oauth/tiktok/callback"

TEST_SHOP_ID = "TEST_OAUTH_SHOP_API_1"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _state_from_url(authorize_url: str) -> str:
    qs = parse_qs(urlparse(authorize_url).query)
    assert "state" in qs, f"no state in {authorize_url}"
    return qs["state"][0]


def _grant_payload() -> dict[str, Any]:
    return {
        "access_token": "TTP_grant_at_api",
        "refresh_token": "TTP_grant_rt_api",
        "shop_cipher": "api_grant_cipher",
        "expires_at": "2026-10-01T00:00:00+00:00",  # string form OK here
        "shop_id": TEST_SHOP_ID,
        "account_name": "TEST Seller API",
        "region": "VN",
        "seller_type": "CROSS_BORDER",
        "user_type": 5,
        "granted_scopes": ["seller.order.read"],
    }


@pytest.fixture()
def app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorize-link + token env for the app process."""
    monkeypatch.setenv("TIKTOK_SERVICE_ID", "test_service_api_1")
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_api")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_api")


@pytest.fixture()
def fake_exchange(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the two upstream calls (token/get + authorized shops)."""
    import tts_erp_v2.proxy.tiktok_oauth as flow

    payload: dict[str, Any] = dict(_grant_payload())
    shops = [
        {
            "shop_id": TEST_SHOP_ID,
            "shop_cipher": "api_grant_cipher",
            "account_name": "TEST Seller API",
            "region": "VN",
            "seller_type": "CROSS_BORDER",
        }
    ]

    def _fake(*, auth_code: str) -> dict[str, Any]:
        payload["auth_code_seen"] = auth_code
        return payload

    def _fake_shops(*, access_token: str) -> list[dict[str, Any]]:
        payload["shops_access_token_seen"] = access_token
        return shops

    monkeypatch.setattr(flow, "exchange_auth_code", _fake)
    monkeypatch.setattr(flow, "fetch_authorized_shops", _fake_shops)
    return payload


# ─── authorize ───────────────────────────────────────────────────────


def test_authorize_requires_readwrite_or_above(
    api_client, readonly_key, readwrite_key
) -> None:
    """readonly is 403; readwrite passes (admin also passes by
    transitivity, but we don't test it here — the role-lattice
    guarantee is in middleware)."""
    r = api_client.get(
        AUTHZ, headers=_bearer(readonly_key), params={"format": "json"}
    )
    assert r.status_code == 403, r.text

    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    assert r.status_code == 200, r.text


def test_authorize_returns_link(
    api_client, readwrite_key, app_env: None, db_session
) -> None:
    """readwrite gets an authorize_url with service_id + a fresh state."""
    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    url = body["authorize_url"]
    assert url.startswith("https://services.tiktokshop.com/open/authorize?")
    assert "service_id=test_service_api_1" in url
    state = _state_from_url(url)
    assert state
    assert body["state"] == state

    # The state is persisted (hash only) — findable by full re-registration
    # count is awkward; assert the returned token round-trips through the
    # flow's own hash by looking the row up via the module.
    from tts_erp_v2.db.models.integration import OAuthState
    from tts_erp_v2.proxy.tiktok_oauth import _state_hash

    row = db_session.execute(
        select(OAuthState).where(OAuthState.state_hash == _state_hash(state))
    ).scalar_one_or_none()
    assert row is not None
    assert row.provider == "tiktok"


def test_authorize_missing_service_id_is_500(
    api_client, readwrite_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No TIKTOK_SERVICE_ID → clear config error, not a bogus link."""
    monkeypatch.delenv("TIKTOK_SERVICE_ID", raising=False)
    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    assert r.status_code == 500, r.text
    assert "TIKTOK_SERVICE_ID" in r.text


# ─── callback ────────────────────────────────────────────────────────


def test_callback_is_public() -> None:
    """No key required — the middleware exempts the exact path. A bare hit
    reports missing_code instead of 401."""
    from tts_erp_v2.app import build_app

    with TestClient(build_app()) as client:
        r = client.get(CALLBACK, params={"format": "json"})
        assert r.status_code == 400, r.text  # missing_code, NOT 401/403
        assert r.json()["kind"] == "missing_code"


def test_callback_denied_reports_auth_denied(
    api_client, admin_key, app_env: None
) -> None:
    """Seller rejecting at the consent screen → TikTok redirects with
    error=auth_denied and no code → we report it, never 401."""
    r = api_client.get(CALLBACK, params={"format": "json", "error": "auth_denied"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["kind"] == "denied"


def test_callback_forged_state_fails_closed(
    api_client, app_env: None, fake_exchange: dict[str, Any]
) -> None:
    """A state we never issued → 400 state_invalid; nothing bootstrapped."""
    r = api_client.get(
        CALLBACK,
        params={"format": "json", "code": "TTP_x", "state": "forged_state"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["kind"] == "state_invalid"
    assert "auth_code_seen" not in fake_exchange  # upstream never called


def test_callback_happy_path_bootstraps_rows(
    api_client,
    readwrite_key,
    db_session,
    app_env: None,
    fake_exchange: dict[str, Any],
) -> None:
    """Full round-trip: authorize → TikTok redirect → rows exist."""
    from tts_erp_v2.db.models.commerce import ChannelAccount
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.token_service import load_credentials

    # 1. Start the flow (readwrite-or-above; admin also passes by transitivity).
    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    assert r.status_code == 200, r.text
    state = _state_from_url(r.json()["authorize_url"])

    # 2. TikTok redirects the seller's browser here (no key!).
    r = api_client.get(
        CALLBACK, params={"format": "json", "code": "TTP_real_code", "state": state}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["kind"] == "authorized"
    shop = body["result"]["shops"][0]
    assert shop["shop_id"] == TEST_SHOP_ID
    assert fake_exchange["auth_code_seen"] == "TTP_real_code"
    expected_at = "TTP_grant_at_api"
    assert fake_exchange["shops_access_token_seen"] == expected_at

    # 3. Rows bootstrapped (committed by the app's own connection).
    acct = db_session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert acct.credential_id == shop["credential_id"]
    cred = db_session.execute(
        select(Credentials).where(
            Credentials.provider == "tiktok",
            Credentials.external_account_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert cred.id == acct.credential_id

    # 4. Decrypted envelope round-trips (real Fernet key from .env).
    view = load_credentials(db_session, "tiktok", TEST_SHOP_ID)
    assert view is not None
    expected_at = "TTP_grant_at_api"
    expected_cipher = "api_grant_cipher"
    assert view.access_token == expected_at
    assert view.shop_cipher == expected_cipher


def test_callback_upgrades_plugin_registered_shop(
    api_client,
    readwrite_key,
    db_engine,
    db_session,
    app_env: None,
    fake_exchange: dict[str, Any],
) -> None:
    """插件注册店铺（credential_id=NULL）拿到 API 授权后走 OAuth callback：
    同一行升级（credential_id 补上），不产生重复行。opened_date 不在
    OAuth upsert 的 set_ 里，人工填的开店日期保留。"""
    from sqlalchemy import text as _text

    from tts_erp_v2.db.models.commerce import ChannelAccount

    with db_engine.begin() as conn:
        conn.execute(
            _text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, opened_date) "
                "VALUES ('tiktok', :sid, 'Plugin Shop', 'active', "
                "        '2026-06-01')"
            ).bindparams(sid=TEST_SHOP_ID)
        )

    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    state = _state_from_url(r.json()["authorize_url"])
    r = api_client.get(
        CALLBACK, params={"format": "json", "code": "TTP_real_code", "state": state}
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    rows = db_session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalars().all()
    assert len(rows) == 1, "OAuth 升级不得产生重复 shops 行"
    acct = rows[0]
    assert acct.credential_id is not None
    assert str(acct.opened_date) == "2026-06-01"  # 人工填写值保留


def test_callback_reusing_state_is_rejected(
    api_client,
    readwrite_key,
    app_env: None,
    fake_exchange: dict[str, Any],
) -> None:
    """The CSRF state is single-use — a second callback with the same
    state gets 400 state_reused and must not re-run the exchange."""
    r = api_client.get(
        AUTHZ, headers=_bearer(readwrite_key), params={"format": "json"}
    )
    state = _state_from_url(r.json()["authorize_url"])

    r1 = api_client.get(
        CALLBACK, params={"format": "json", "code": "code_1", "state": state}
    )
    assert r1.status_code == 200, r1.text

    fake_exchange.pop("auth_code_seen")
    r2 = api_client.get(
        CALLBACK, params={"format": "json", "code": "code_2", "state": state}
    )
    assert r2.status_code == 400, r2.text
    assert r2.json()["kind"] == "state_reused"
    assert "auth_code_seen" not in fake_exchange


def test_callback_html_default_render(api_client, app_env: None) -> None:
    """Without format=json the callback returns an HTML page for the
    seller's browser — still error-classified, no 401."""
    r = api_client.get(CALLBACK)  # no code → 400 HTML page
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]
    assert "No authorization code" in r.text


# ─── onboard console page ────────────────────────────────────────────


def test_onboard_page_redirects_browser_to_login() -> None:
    """Unauthenticated browser GET → 302 to the login page (readonly-exact
    classification), never a raw 401 JSON wall."""
    from tts_erp_v2.app import build_app

    with TestClient(build_app()) as client:
        r = client.get(
            "/v2/oauth/tiktok/onboard",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        loc = r.headers["location"]
        assert "/v2/auth/login" in loc
        assert "next=/v2/oauth/tiktok/onboard" in loc


def test_onboard_page_readonly_can_view(
    api_client, readonly_key, app_env: None
) -> None:
    """The console shell is readonly-viewable; a readonly key can load
    the page (button click will be gated server-side at the JSON
    authorize endpoint, which requires readwrite or above)."""
    r = api_client.get("/v2/oauth/tiktok/onboard", headers=_bearer(readonly_key))
    assert r.status_code == 200, r.text
    assert "新店接入授权" in r.text
    assert 'id="btn-gen"' in r.text
    assert "authorize?format=json" in r.text
    # New error message references the readwrite threshold (not admin)
    # and the new button label "授权新店".
    assert "readwrite" in r.text
    assert "授权新店" in r.text
    # Two-step UX (linkbox + copy button) is gone — click auto-opens.
    assert "id=\"linkbox\"" not in r.text
    assert "id=\"btn-copy\"" not in r.text
    assert "复制链接" not in r.text
    # JS uses window.open for the new flow (visible in the inline script).
    assert "window.open" in r.text


def test_onboard_page_admin_same_shell(api_client, admin_key, app_env: None) -> None:
    """Admin gets the identical shell (the JSON authorize endpoint sits
    one notch lower in role now — readwrite — but the document is the
    same; gating happens on click, not in the HTML itself)."""
    r = api_client.get("/v2/oauth/tiktok/onboard", headers=_bearer(admin_key))
    assert r.status_code == 200, r.text
    assert 'id="btn-gen"' in r.text
    assert "授权新店" in r.text
