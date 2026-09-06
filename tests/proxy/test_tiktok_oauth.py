"""TDD tests for the TikTok Shop *authorization (grant)* half of the
OAuth lifecycle — the mirror image of ``test_tiktok_auth.py`` which
covers refresh.

Under test (all in :mod:`tts_erp_v2.proxy.tiktok_auth`):
* :func:`exchange_auth_code` — the outbound ``/api/v2/token/get`` call
  (``grant_type=authorized_code``) that exchanges the callback
  ``auth_code`` for a token envelope. Reference: the "Get Access Token
  API" section of ``tts-partner-api-docs/Authorization overview.md``.
* :func:`build_authorize_url` — the seller authorization link
  (``/open/authorize?service_id=...&state=...``). Reference: the
  "Authorization domains by market" table in the same doc.

Deliberately HTTP-only: no DB, no Fernet. The state-machine /
orchestration layer lives in ``tests/proxy/test_oauth_flow.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

AT_KEY = "access_token"
RT_KEY = "refresh_token"
SC_KEY = "shop_cipher"

pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]


# Reuse the same fake-HTTP harness as test_tiktok_auth.py.
class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    def __init__(self, *, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, path: str, body=None, headers=None) -> None:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers or {},
            }
        )

    def getresponse(self) -> _FakeHTTPResponse:
        raw = json.dumps(self.body).encode("utf-8")
        return _FakeHTTPResponse(self.status, raw)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def app_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token-endpoint env (app_key / app_secret / auth host)."""
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_xyz")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_xyz")
    monkeypatch.setenv("TIKTOK_AUTH_HOST", "https://auth.example.test")


@pytest.fixture()
def authorize_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorize-link env (service_id)."""
    monkeypatch.setenv("TIKTOK_SERVICE_ID", "test_service_id_123")


# ─── exchange_auth_code() ────────────────────────────────────────────


def _fresh_grant_body() -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            AT_KEY: "TTP_grant_at_abc",
            RT_KEY: "TTP_grant_rt_abc",
            "expires_in": 604800,  # 7 days
            "open_id": "7010736057180325637",
            "seller_name": "Test Seller",
            "seller_base_region": "VN",
            "user_type": 5,
            "shop_id": "7494763368967603447",
            SC_KEY: "grant_cipher_abc",
            "seller_type": "CROSS_BORDER",
            "granted_scopes": ["seller.order.read", "seller.product.read"],
        },
    }


def test_exchange_auth_code_success(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """auth_code exchange → parsed envelope incl. shop identity fields."""
    from tts_erp_v2.proxy import tiktok_auth

    fake = _FakeConn(status=200, body=_fresh_grant_body())
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    out = tiktok_auth.exchange_auth_code(auth_code="TTP_FeBoANmHP3yqdoUI9fZOCw")

    assert out[AT_KEY] == "TTP_grant_at_abc"
    assert out[RT_KEY] == "TTP_grant_rt_abc"
    assert out[SC_KEY] == "grant_cipher_abc"
    assert out["shop_id"] == "7494763368967603447"
    assert out["account_name"] == "Test Seller"
    assert out["region"] == "VN"
    assert out["seller_type"] == "CROSS_BORDER"
    assert out["user_type"] == 5
    assert out["granted_scopes"] == ["seller.order.read", "seller.product.read"]
    assert out["expires_at"] is not None
    delta = (out["expires_at"] - datetime.now(UTC)).total_seconds()
    assert 604700 <= delta <= 604900
    # Diagnostic key surface (missing_shop_id / missing_shop_cipher messages
    # append this so a first real run is decidable).
    assert "_upstream_data_keys" in out
    assert set(out["_upstream_data_keys"]) >= {
        "access_token",
        "shop_id",
        "shop_cipher",
    }

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"].startswith("/api/v2/token/get?")
    assert "app_key=test_app_key_xyz" in call["path"]
    assert "app_secret=test_app_secret_xyz" in call["path"]
    assert "grant_type=authorized_code" in call["path"]
    assert "auth_code=TTP_FeBoANmHP3yqdoUI9fZOCw" in call["path"]


def test_exchange_auth_code_absolute_expiry(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """Some upstream shapes report ``access_token_expire_in`` as an absolute
    Unix timestamp instead of ``expires_in`` seconds — parse both."""
    from tts_erp_v2.proxy import tiktok_auth

    body = _fresh_grant_body()
    data = body["data"]
    # Drop the relative field, use the doc's absolute-timestamp shape.
    data.pop("expires_in")
    data["access_token_expire_in"] = int(datetime.now(UTC).timestamp()) + 604800
    fake = _FakeConn(status=200, body=body)
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    out = tiktok_auth.exchange_auth_code(auth_code="code_x")
    assert out["expires_at"] is not None
    delta = (out["expires_at"] - datetime.now(UTC)).total_seconds()
    assert 604700 <= delta <= 604900


def test_exchange_auth_code_non_zero_code_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """Upstream code != 0 (e.g. 10001 auth_code expired / used) → typed error."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(
        status=200,
        body={"code": 10001, "message": "auth code used or expired", "data": None},
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    with pytest.raises(UpstreamHttpError) as ei:
        tiktok_auth.exchange_auth_code(auth_code="stale_code")
    assert "10001" in str(ei.value)
    assert "auth code used" in str(ei.value)


def test_exchange_auth_code_missing_data_access_token_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """code=0 but data has no access_token → fail loudly, don't store junk."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(
        status=200,
        body={"code": 0, "message": "success", "data": {"granted_scopes": []}},
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    with pytest.raises(UpstreamHttpError) as ei:
        tiktok_auth.exchange_auth_code(auth_code="code_x")
    assert "access_token" in str(ei.value)


def test_exchange_auth_code_missing_app_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No app_key / app_secret → SigningError (fail-closed config), never
    an empty-string HTTP call."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.delenv("TIKTOK_APP_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_APP_SECRET", raising=False)
    with pytest.raises(SigningError) as ei:
        tiktok_auth.exchange_auth_code(auth_code="code_x")
    assert "TIKTOK_APP_KEY" in str(ei.value)


def test_exchange_auth_code_rejects_non_http_scheme(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """file:// token host → refuse before http.client runs."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.setenv("TIKTOK_AUTH_HOST", "file:///etc/passwd")
    with pytest.raises(SigningError) as ei:
        tiktok_auth.exchange_auth_code(auth_code="code_x")
    assert "scheme" in str(ei.value).lower()


# ─── build_authorize_url() ───────────────────────────────────────────


def test_build_authorize_url_default_row_host(
    monkeypatch: pytest.MonkeyPatch, authorize_creds: None
) -> None:
    """Default market domain is the ROW authorize host (production shop is
    VN). URL carries service_id + state."""
    from tts_erp_v2.proxy import tiktok_auth

    url = tiktok_auth.build_authorize_url(state="csrf_state_abc")
    assert url.startswith("https://services.tiktokshop.com/open/authorize?")
    assert "service_id=test_service_id_123" in url
    assert "state=csrf_state_abc" in url


def test_build_authorize_url_us_market_override(
    monkeypatch: pytest.MonkeyPatch, authorize_creds: None
) -> None:
    """US-market apps point at services.us.tiktokshop.com via env override."""
    from tts_erp_v2.proxy import tiktok_auth

    monkeypatch.setenv("TIKTOK_AUTHORIZE_HOST", "https://services.us.tiktokshop.com")
    url = tiktok_auth.build_authorize_url(state="s1")
    assert url.startswith("https://services.us.tiktokshop.com/open/authorize?")


def test_build_authorize_url_missing_service_id_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIKTOK_SERVICE_ID unset → SigningError naming the var (operator must
    copy it from Partner Center App & Service)."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.delenv("TIKTOK_SERVICE_ID", raising=False)
    with pytest.raises(SigningError) as ei:
        tiktok_auth.build_authorize_url(state="s1")
    assert "TIKTOK_SERVICE_ID" in str(ei.value)


def test_build_authorize_url_rejects_non_http_host(
    monkeypatch: pytest.MonkeyPatch, authorize_creds: None
) -> None:
    """A bogus TIKTOK_AUTHORIZE_HOST scheme is refused up front."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.setenv("TIKTOK_AUTHORIZE_HOST", "javascript:alert(1)")
    with pytest.raises(SigningError) as ei:
        tiktok_auth.build_authorize_url(state="s1")
    assert "scheme" in str(ei.value).lower()


# ─── fetch_authorized_shops() ────────────────────────────────────────


_USER_AT = (
    "TTP_user_at"  # dummy bearer; S105-safe via const (tests/ ruff-ignored anyway)
)


def _shops_body(*raw_shops: dict) -> dict:
    return {"code": 0, "message": "success", "data": {"shops": list(raw_shops)}}


def test_fetch_authorized_shops_success(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """GET /authorization/202309/shops with the x-tts-access-token header
    + HMAC sign returns parsed per-shop identity (id/cipher/name names)."""
    from tts_erp_v2.proxy import tiktok_auth

    fake = _FakeConn(
        status=200,
        body=_shops_body(
            {
                "id": "S1",
                "cipher": "CIPHER1",
                "name": "Shop One",
                "region": "VN",
                "seller_type": "CROSS_BORDER",
            }
        ),
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    shops = tiktok_auth.fetch_authorized_shops(access_token=_USER_AT)
    assert shops == [
        {
            "shop_id": "S1",
            "shop_cipher": "CIPHER1",
            "account_name": "Shop One",
            "region": "VN",
            "seller_type": "CROSS_BORDER",
            "_raw_keys": ["cipher", "id", "name", "region", "seller_type"],
        }
    ]

    call = fake.calls[0]
    assert call["method"] == "GET"
    assert "/authorization/202309/shops" in call["path"]
    assert "app_key=test_app_key_xyz" in call["path"]
    assert "timestamp=" in call["path"]
    assert "sign=" in call["path"]
    headers_lower = {k.lower(): v for k, v in (call["headers"] or {}).items()}
    assert headers_lower.get("x-tts-access-token") == "TTP_user_at"


def test_fetch_authorized_shops_alternate_field_names(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """Upstream naming drift (shop_id/shop_cipher/shop_name/shop_region)
    is absorbed by the tolerant parser."""
    from tts_erp_v2.proxy import tiktok_auth

    fake = _FakeConn(
        status=200,
        body=_shops_body(
            {
                "shop_id": "7494763368967603447",
                "shop_cipher": "cipher_x",
                "shop_name": "Bridge nook",
                "shop_region": "VN",
                "seller_type": "CROSS_BORDER",
            }
        ),
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    shops = tiktok_auth.fetch_authorized_shops(access_token=_USER_AT)
    assert shops[0]["shop_id"] == "7494763368967603447"
    assert shops[0]["shop_cipher"] == "cipher_x"
    assert shops[0]["account_name"] == "Bridge nook"
    assert shops[0]["region"] == "VN"


def test_fetch_authorized_shops_empty_list(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """code=0 with an empty/absent shops array → [] (caller decides)."""
    from tts_erp_v2.proxy import tiktok_auth

    fake = _FakeConn(status=200, body={"code": 0, "message": "success", "data": {}})
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    assert tiktok_auth.fetch_authorized_shops(access_token=_USER_AT) == []


def test_fetch_authorized_shops_nonzero_code_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """Upstream code != 0 → UpstreamHttpError carrying upstream_code."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(
        status=200,
        body={"code": 10001, "message": "not authorized", "data": {}},
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    with pytest.raises(UpstreamHttpError) as ei:
        tiktok_auth.fetch_authorized_shops(access_token=_USER_AT)
    assert ei.value.upstream_code == 10001


def test_fetch_authorized_shops_http_error_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """HTTP 5xx from the endpoint → UpstreamHttpError (no silent [])."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(status=500, body={"code": 0, "message": ""})
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    with pytest.raises(UpstreamHttpError):
        tiktok_auth.fetch_authorized_shops(access_token=_USER_AT)
