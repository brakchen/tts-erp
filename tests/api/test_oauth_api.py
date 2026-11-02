"""API contract tests for /v2/oauth/tiktok/{authorize,callback} — the
TikTok seller authorization flow (new-shop onboarding).

Full contract: docs/api/tiktok-shop-oauth.md. Key facts tested here:

* ``authorize`` requires readwrite-or-above and returns a TikTok link
  carrying the registered single-use state. (Generation is harmless —
  a single-use CSRF row + a URL — so the threshold sits one notch
  above the readonly HTML shell it lives behind.)
* ``callback`` is PUBLIC (TikTok redirect target — no API key) and is
  the only place the flow does anything; bad/forged states fail closed.
* The happy path authorizes one pre-registered target and persists its
  credentials in place (TEST_ sentinel ids; cleaned by API conftest).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.api.test_frontend_pages import _bootstrap
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import Credentials, OAuthState
from tts_erp_v2.proxy.tiktok_oauth import _state_hash
from tts_erp_v2.proxy.token_service import upsert_tiktok_app_credentials

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

AUTHZ = "/v2/oauth/tiktok/authorize"
CALLBACK = "/v2/oauth/tiktok/callback"

TEST_SHOP_ID = "TEST_OAUTH_SHOP_API_1"
TEST_SERVICE_ID = "TEST_OAUTH_OWN_SERVICE_API"
TEST_APP_KEY = "TEST_OAUTH_OWN_APP_KEY_API"
TEST_APP_SECRET = "TEST_OAUTH_OWN_APP_SECRET_API"


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
        "expires_at": datetime(2026, 12, 1, tzinfo=UTC),
        "shop_id": TEST_SHOP_ID,
        "account_name": "TEST Seller API",
        "region": "VN",
        "seller_type": "CROSS_BORDER",
        "user_type": 5,
        "granted_scopes": ["seller.order.read"],
    }


@pytest.fixture()
def app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Conflicting global defaults must never replace a target shop's own App."""
    monkeypatch.setenv("TIKTOK_SERVICE_ID", "test_service_api_1")
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_api")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_api")


@dataclass(frozen=True, slots=True)
class OAuthTarget:
    shop_pk: int
    shop_id: str
    service_id: str
    app_key: str
    app_revision: int


def _create_target(
    db_engine, *, shop_id: str = TEST_SHOP_ID,
    service_id: str = TEST_SERVICE_ID, app_key: str = TEST_APP_KEY,
    app_secret: str = TEST_APP_SECRET,
) -> OAuthTarget:
    # Requests use the app's own connection; commit this TEST_ target and its
    # shop-owned encrypted App configuration outside db_session's savepoint.
    # API conftest cleanup removes our TEST_ shops and cascades their App rows.
    with Session(db_engine) as session:
        account = ChannelAccount(platform="tiktok", shop_id=shop_id,
            status="active", service_id=service_id)
        session.add(account)
        session.flush()
        app = upsert_tiktok_app_credentials(session, shop_pk=account.id,
            service_id=service_id, app_key=app_key, plaintext_app_secret=app_secret)
        target = OAuthTarget(account.id, shop_id, service_id, app_key, app.revision)
        session.commit()
        return target


@pytest.fixture()
def target_shop(db_engine, app_env: None) -> OAuthTarget:
    """A registered TEST_ target with its own real token_service App owner."""
    return _create_target(db_engine)


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

    def _fake(*, auth_code: str, app_credentials) -> dict[str, Any]:
        payload["auth_code_seen"] = auth_code
        payload["app_service_id_seen"] = app_credentials.service_id
        payload["app_key_seen"] = app_credentials.app_key
        return payload

    def _fake_shops(*, access_token: str, app_credentials) -> list[dict[str, Any]]:
        payload["shops_access_token_seen"] = access_token
        assert app_credentials.service_id == payload["app_service_id_seen"]
        assert app_credentials.app_key == payload["app_key_seen"]
        return shops

    monkeypatch.setattr(flow, "exchange_auth_code", _fake)
    monkeypatch.setattr(flow, "fetch_authorized_shops", _fake_shops)
    return payload


# ─── authorize ───────────────────────────────────────────────────────


def test_authorize_requires_readwrite_or_above(
    api_client, readonly_key, readwrite_key, target_shop: OAuthTarget,
) -> None:
    """The same valid target is denied readonly and accepted readwrite."""
    params = {"format": "json", "shop_pk": target_shop.shop_pk}
    denied = api_client.get(AUTHZ, headers=_bearer(readonly_key), params=params)
    assert denied.status_code == 403, denied.text
    accepted = api_client.get(AUTHZ, headers=_bearer(readwrite_key), params=params)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["shop_id"] == target_shop.shop_id


def test_authorize_returns_link(
    api_client, readwrite_key, target_shop: OAuthTarget, db_session,
) -> None:
    """The link and persisted state bind exactly the target's own App."""
    r = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["shop_id"] == target_shop.shop_id
    assert body["service_id"] == target_shop.service_id
    url = body["authorize_url"]
    assert url.startswith("https://services.tiktokshop.com/open/authorize?")
    assert parse_qs(urlparse(url).query)["service_id"] == [target_shop.service_id]
    state = _state_from_url(url)
    assert state and body["state"] == state
    row = db_session.execute(select(OAuthState).where(
        OAuthState.state_hash == _state_hash(state))).scalar_one()
    assert row.provider == "tiktok" and row.consumed_at is None
    assert row.extra == {
        "expected_shop_pk": target_shop.shop_pk,
        "expected_shop_id": target_shop.shop_id,
        "service_id": target_shop.service_id,
        "app_revision": target_shop.app_revision,
        "created_by": hashlib.sha256(readwrite_key.encode()).hexdigest(),
    }


def test_authorize_missing_target_service_id_is_422(
    api_client, readwrite_key, target_shop: OAuthTarget, db_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing target without Service ID is a 422 configuration error."""
    monkeypatch.delenv("TIKTOK_SERVICE_ID", raising=False)
    with Session(db_engine) as session:
        account = session.get(ChannelAccount, target_shop.shop_pk)
        assert account is not None
        account.service_id = None
        session.commit()
    r = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert r.status_code == 422, r.text
    assert "service id" in r.json()["detail"].lower()


def test_authorize_uses_target_app_ignoring_global_service_override(
    api_client, readwrite_key, target_shop: OAuthTarget, db_session, db_engine,
) -> None:
    """Another shop's App and an extra service_id query cannot choose the App."""
    other = _create_target(db_engine, shop_id="TEST_OAUTH_OTHER_SHOP_API",
        service_id="TEST_OAUTH_OTHER_SERVICE_API", app_key="TEST_OAUTH_OTHER_APP_KEY_API",
        app_secret="TEST_OAUTH_OTHER_APP_SECRET_API")
    assert other.shop_pk != target_shop.shop_pk
    r = api_client.get(AUTHZ, headers=_bearer(readwrite_key), params={
        "format": "json", "shop_pk": target_shop.shop_pk, "service_id": other.service_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service_id"] == target_shop.service_id
    assert parse_qs(urlparse(body["authorize_url"]).query)["service_id"] == [target_shop.service_id]
    assert other.service_id not in body["authorize_url"]
    assert "test_service_api_1" not in body["authorize_url"]
    state = _state_from_url(body["authorize_url"])
    row = db_session.execute(select(OAuthState).where(
        OAuthState.state_hash == _state_hash(state))).scalar_one()
    assert row.extra == {
        "expected_shop_pk": target_shop.shop_pk,
        "expected_shop_id": target_shop.shop_id,
        "service_id": target_shop.service_id,
        "app_revision": target_shop.app_revision,
        "created_by": hashlib.sha256(readwrite_key.encode()).hexdigest(),
    }


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


def test_callback_rejects_app_key_mismatch_before_exchange(
    api_client, readwrite_key, target_shop: OAuthTarget,
    fake_exchange: dict[str, Any], db_session,
) -> None:
    issued = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert issued.status_code == 200, issued.text
    state = _state_from_url(issued.json()["authorize_url"])
    r = api_client.get(CALLBACK, params={"format": "json", "code": "TEST_code_wrong_app",
        "state": state, "app_key": "TEST_WRONG_CALLBACK_APP_KEY"})
    assert r.status_code == 400, r.text
    assert r.json()["kind"] == "app_key_mismatch"
    assert "auth_code_seen" not in fake_exchange
    row = db_session.execute(select(OAuthState).where(
        OAuthState.state_hash == _state_hash(state))).scalar_one()
    assert row.consumed_at is not None
    assert db_session.execute(select(Credentials).where(
        Credentials.provider == "tiktok", Credentials.external_account_id == target_shop.shop_id,
    )).scalar_one_or_none() is None


def test_callback_happy_path_bootstraps_rows(
    api_client, readwrite_key, db_session, target_shop: OAuthTarget,
    fake_exchange: dict[str, Any],
) -> None:
    """Authorize the existing target and persist its own credentials/identity."""
    from tts_erp_v2.proxy.token_service import load_credentials

    issued = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert issued.status_code == 200, issued.text
    state = _state_from_url(issued.json()["authorize_url"])
    response = api_client.get(CALLBACK, params={"format": "json", "code": "TEST_real_code",
        "state": state, "app_key": target_shop.app_key})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["kind"] == "authorized"
    shop = body["result"]["shops"][0]
    assert shop["shop_id"] == target_shop.shop_id
    assert shop["account_id"] == target_shop.shop_pk
    assert fake_exchange["auth_code_seen"] == "TEST_real_code"
    assert fake_exchange["app_service_id_seen"] == target_shop.service_id
    assert fake_exchange["app_key_seen"] == target_shop.app_key
    assert fake_exchange["shops_access_token_seen"] == "TTP_grant_at_api"

    account = db_session.execute(select(ChannelAccount).where(
        ChannelAccount.platform == "tiktok", ChannelAccount.shop_id == target_shop.shop_id,
    )).scalar_one()
    assert account.id == target_shop.shop_pk
    assert account.credential_id == shop["credential_id"]
    cred = db_session.execute(select(Credentials).where(
        Credentials.provider == "tiktok", Credentials.external_account_id == target_shop.shop_id,
    )).scalar_one()
    assert cred.id == account.credential_id
    assert cred.service_id == target_shop.service_id
    assert cred.extra == {"app_revision": target_shop.app_revision}
    # Real encryption/decryption continues through the sole credential owner.
    view = load_credentials(db_session, provider="tiktok", external_account_id=target_shop.shop_id)
    assert view is not None
    assert view.access_token == "TTP_grant_at_api"
    assert view.shop_cipher == "api_grant_cipher"


def test_callback_upgrades_plugin_registered_shop(
    api_client, readwrite_key, db_engine, db_session, target_shop: OAuthTarget,
    fake_exchange: dict[str, Any],
) -> None:
    """A configured plugin shop upgrades in place and keeps its opened_date."""
    with Session(db_engine) as session:
        account = session.get(ChannelAccount, target_shop.shop_pk)
        assert account is not None and account.credential_id is None
        account.account_name = "TEST Plugin Shop"
        account.opened_date = date(2026, 6, 1)
        session.commit()
    issued = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert issued.status_code == 200, issued.text
    state = _state_from_url(issued.json()["authorize_url"])
    response = api_client.get(CALLBACK, params={"format": "json", "code": "TEST_real_code", "state": state})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    rows = db_session.execute(select(ChannelAccount).where(
        ChannelAccount.platform == "tiktok", ChannelAccount.shop_id == target_shop.shop_id,
    )).scalars().all()
    assert len(rows) == 1, "OAuth 升级不得产生重复 shops 行"
    account = rows[0]
    assert account.id == target_shop.shop_pk and account.credential_id is not None
    assert account.account_name == "TEST Plugin Shop"
    assert str(account.opened_date) == "2026-06-01"


def test_callback_reusing_state_is_rejected(
    api_client, readwrite_key, target_shop: OAuthTarget, fake_exchange: dict[str, Any],
) -> None:
    """The second callback rejects consumed state without another exchange."""
    issued = api_client.get(AUTHZ, headers=_bearer(readwrite_key),
        params={"format": "json", "shop_pk": target_shop.shop_pk})
    assert issued.status_code == 200, issued.text
    state = _state_from_url(issued.json()["authorize_url"])
    first = api_client.get(CALLBACK, params={"format": "json", "code": "TEST_code_1", "state": state})
    assert first.status_code == 200, first.text
    fake_exchange.pop("auth_code_seen")
    second = api_client.get(CALLBACK, params={"format": "json", "code": "TEST_code_2", "state": state})
    assert second.status_code == 400, second.text
    assert second.json()["kind"] == "state_reused"
    assert "auth_code_seen" not in fake_exchange


def test_callback_html_default_render(api_client, app_env: None) -> None:
    """Without format=json the callback returns an HTML page for the
    seller's browser — still error-classified, no 401."""
    r = api_client.get(CALLBACK)  # no code → 400 HTML page
    assert r.status_code == 400
    assert "text/html" in r.headers["content-type"]
    assert _bootstrap(r.text)["pageData"]["title"] == "缺少授权码"
    assert r.headers["cache-control"] == "private, no-store"


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
    data = _bootstrap(r.text)
    assert data["pageId"] == "oauth-onboard" and data["navigation"] == []
    assert data["pageData"] == {"kind": "oauth-onboard"}
    assert "readwrite" in r.text and "只读" in r.text
    assert 'href="/v2/pages/shops"' in r.text
    assert "window.open" not in r.text and 'id="btn-gen"' not in r.text
    assert "authorize?format=json" not in r.text



def test_onboard_page_admin_same_shell(api_client, admin_key, app_env: None) -> None:
    """Admin gets the identical shell (the JSON authorize endpoint sits
    one notch lower in role now — readwrite — but the document is the
    same; gating happens on click, not in the HTML itself)."""
    r = api_client.get("/v2/oauth/tiktok/onboard", headers=_bearer(admin_key))
    assert r.status_code == 200, r.text
    assert _bootstrap(r.text)["pageData"] == {"kind": "oauth-onboard"}
    assert 'href="/v2/pages/shops"' in r.text
