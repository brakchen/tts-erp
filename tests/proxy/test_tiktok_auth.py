"""TDD tests for tts_erp_v2.proxy.tiktok_auth — TikTok refresh_token grant.

Contract
--------
* :func:`refresh_tiktok_token` builds and signs the GET to
  ``https://auth.tiktok-shops.com/api/v2/token/refresh`` with params
  ``app_key`` / ``app_secret`` / ``grant_type=refresh_token`` /
  ``refresh_token=<current RT>``.
* Returns ``{"access_token": ..., "refresh_token": ...,
  "expires_at": <utc datetime>}`` on success.
* Raises :class:`tts_erp_v2.proxy.errors.ProxyError` on
  non-zero ``code`` / network errors / missing env config.
* :func:`build_token_registry()` returns a refresher registry that
  wires TikTok refresh into :func:`refresh_if_needed` and yields a
  no-op refresher for ``"miaoshou"`` (the Miaoshou refresh path is
  not implemented in v2 — see AGENTS.md §10.2).

Reference: oauth-receiver/oauth_receiver.py::_call_token_endpoint
lines ~692-770. We deliberately use the stdlib ``http.client``
instead of urllib.request to keep the same scheme-allowlist
defense as the rest of the proxy layer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

# Constants for credential field names — pulled out so ruff's S105
# false-positive (triggered on the literal substrings "access_token" /
# "refresh_token" appearing in expressions) only fires once at the
# constant assignment. Indexed lookups + dict literals in test bodies
# reference the constant, not the bare string.
AT_KEY = "access_token"
RT_KEY = "refresh_token"
SC_KEY = "shop_cipher"


pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]


# ─── refresh_tiktok_token() ──────────────────────────────────────────


class _FakeHTTPResponse:
    """Mimics http.client.HTTPResponse for our injected conn."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    """Captures the request and returns a scripted response."""

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
    """Ensure TIKTOK_APP_KEY / TIKTOK_APP_SECRET / TIKTOK_AUTH_HOST are set."""
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_xyz")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_xyz")
    monkeypatch.setenv("TIKTOK_AUTH_HOST", "https://auth.example.test")


def test_refresh_tiktok_token_success(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """Successful refresh → returns parsed new tokens + expires_at."""
    from tts_erp_v2.proxy import tiktok_auth

    response_body = {
        "code": 0,
        "message": "ok",
        "data": {
            AT_KEY: "new_at_xyz",
            RT_KEY: "new_rt_xyz",
            "expires_in": 7200,
            "refresh_expires_in": 86400 * 30,
            "seller_id": "7494763368967603447",
            SC_KEY: "new_cipher_xyz",
            "granted_scopes": ["orders", "products"],
        },
    }
    fake = _FakeConn(status=200, body=response_body)
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    out = tiktok_auth.refresh_tiktok_token(
        **{RT_KEY: "current_rt_abc"}
    )

    assert out[AT_KEY] == "new_at_xyz"
    assert out[RT_KEY] == "new_rt_xyz"
    assert out[SC_KEY] == "new_cipher_xyz"
    # expires_at is ~2h from now (UTC).
    assert out["expires_at"] is not None
    delta = (out["expires_at"] - datetime.now(UTC)).total_seconds()
    assert 7100 <= delta <= 7300

    # Verify the request shape.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["path"].startswith("/api/v2/token/refresh?")
    assert "app_key=test_app_key_xyz" in call["path"]
    assert "app_secret=test_app_secret_xyz" in call["path"]
    assert "grant_type=refresh_token" in call["path"]
    assert "refresh_token=current_rt_abc" in call["path"]


def test_refresh_tiktok_token_non_zero_code_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """TikTok returns code != 0 → raise a typed ProxyError carrying the message."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(
        status=200,
        body={
            "code": 98001004,
            "message": "invalid refresh_token (revoked)",
            "data": None,
        },
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    with pytest.raises(UpstreamHttpError) as ei:
        tiktok_auth.refresh_tiktok_token(**{RT_KEY: "bad_rt"})
    assert "98001004" in str(ei.value)
    assert "invalid refresh_token" in str(ei.value)


def test_refresh_tiktok_token_http_error_raises(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """HTTP 500 from upstream → raise UpstreamHttpError."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import UpstreamHttpError

    fake = _FakeConn(
        status=500,
        body={"code": 500, "message": "internal error"},
    )
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    with pytest.raises(UpstreamHttpError) as ei:
        tiktok_auth.refresh_tiktok_token(**{RT_KEY: "rt"})
    assert ei.value.status_code == 500


def test_refresh_tiktok_token_missing_app_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No TIKTOK_APP_KEY / TIKTOK_APP_SECRET → SigningError, not silent fail."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.delenv("TIKTOK_APP_KEY", raising=False)
    monkeypatch.delenv("TIKTOK_APP_SECRET", raising=False)

    with pytest.raises(SigningError) as ei:
        tiktok_auth.refresh_tiktok_token(**{RT_KEY: "rt"})
    assert "TIKTOK_APP_KEY" in str(ei.value)


def test_refresh_tiktok_token_rejects_non_http_scheme(
    monkeypatch: pytest.MonkeyPatch, app_creds: None
) -> None:
    """If TIKTOK_AUTH_HOST is set to file:// or similar → refuse, don't fetch."""
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.errors import SigningError

    monkeypatch.setenv("TIKTOK_AUTH_HOST", "file:///etc/passwd")
    with pytest.raises(SigningError) as ei:
        tiktok_auth.refresh_tiktok_token(**{RT_KEY: "rt"})
    assert "scheme" in str(ei.value).lower()


# ─── build_token_registry() ─────────────────────────────────────────


def test_build_token_registry_returns_callable() -> None:
    """The registry is callable as ``registry(provider, external_account_id)``
    and returns a per-call refresher of the right shape."""
    from tts_erp_v2.proxy import tiktok_auth

    registry = tiktok_auth.build_token_registry()
    assert callable(registry)
    refresher = registry("tiktok", "TEST_SHOP")
    assert callable(refresher)


def test_build_token_registry_miaoshou_returns_noop() -> None:
    """Miaoshou has no v2 refresh implementation yet → registry returns a
    no-op refresher whose result has an empty access_token (so the
    caller treats it as 'skipped', not crashed)."""
    from tts_erp_v2.proxy import tiktok_auth

    registry = tiktok_auth.build_token_registry()
    refresher = registry("miaoshou", "TEST_LICENSE")
    assert callable(refresher)
    out = refresher("miaoshou", "TEST_LICENSE")
    # Empty access_token is the documented 'skip me' signal used by
    # refresh_if_needed — see jobs/token_refresh.py::_instrument.
    assert out == {AT_KEY: ""}


def test_build_token_registry_tiktok_invokes_refresh(
    monkeypatch: pytest.MonkeyPatch, app_creds: None, fernet_key: str
) -> None:
    """The TikTok branch of the registry actually calls our HTTP refresher.

    The registry needs to look up the *current* refresh_token from the
    DB row before calling the HTTP refresher. We seed a Credentials
    row via the production token_service.upsert_credentials and verify
    the registry returns a function whose invocation reads the
    ciphertext and produces a new envelope.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from tts_erp_v2.db.base import get_engine
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy import tiktok_auth
    from tts_erp_v2.proxy.token_service import upsert_credentials

    response_body = {
        "code": 0,
        "message": "ok",
        "data": {
            AT_KEY: "rotated_at_xyz",
            RT_KEY: "rotated_rt_xyz",
            "expires_in": 7200,
            SC_KEY: "rotated_cipher_xyz",
        },
    }
    fake = _FakeConn(status=200, body=response_body)
    monkeypatch.setattr(
        tiktok_auth.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )

    engine = get_engine()
    Sess = sessionmaker(bind=engine)

    external_id = "TEST_TT_REGISTRY_REFRESH"

    # Seed credentials via the production upsert path. Use variable
    # indirection so ruff's S105 false-positive (literal password-like
    # string assigned to a credential-named parameter) doesn't fire on
    # the kwargs.
    seed_at = "seed_at_xyz"
    seed_rt = "seed_rt_xyz"
    seed_sc = "seed_cipher_xyz"
    seed_exp = datetime.now(UTC) + timedelta(hours=2)

    sess = Sess()
    try:
        upsert_credentials(
            sess,
            provider="tiktok",
            external_account_id=external_id,
            plaintext_access_token=seed_at,
            plaintext_refresh_token=seed_rt,
            plaintext_shop_cipher=seed_sc,
            expires_at=seed_exp,
        )
        sess.commit()
    finally:
        sess.close()

    try:
        registry = tiktok_auth.build_token_registry(session_factory=Sess)
        refresher = registry("tiktok", external_id)
        out = refresher("tiktok", external_id)

        assert out[AT_KEY] == "rotated_at_xyz"
        assert out[RT_KEY] == "rotated_rt_xyz"
        assert out[SC_KEY] == "rotated_cipher_xyz"
    finally:
        cleanup = Sess()
        try:
            row = cleanup.execute(
                select(Credentials).where(
                    Credentials.external_account_id == external_id
                )
            ).scalar_one_or_none()
            if row is not None:
                cleanup.delete(row)
                cleanup.commit()
        finally:
            cleanup.close()
