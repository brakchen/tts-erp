"""DB-backed tests for the TikTok shop-authorization orchestration:
CSRF state registration/consumption + the full auth_code → authorized
shops → per-shop credentials + channel-account bootstrap.

Under test: :mod:`tts_erp_v2.proxy.tiktok_oauth` (Lane E — the
half of the OAuth lifecycle that v1's oauth_receiver used to own and
that ``token_service.py`` deliberately does NOT carry: register_state /
pop_state live here per its docstring).

Design contract (from tts-partner-api-docs/Authorization overview.md +
live-upstream confirmation 2026-09-06):
* the redirect callback carries ``code`` + ``state``;
* ``state`` is server-generated, single-use, and validated on callback;
* the ``auth_code`` is exchanged once via ``grant_type=authorized_code``
  for a **user-level** token (open_id/seller_* — NO shop identity);
* the per-shop ``shop_id`` + ``shop_cipher`` come from TikTok's
  "Get Authorized Shops" (``/authorization/202309/shops``); each shop
  becomes an ``integration.credentials.external_account_id`` +
  ``commerce.shops.shop_id`` row (jobs fan out over credentials).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text

pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]

TEST_SHOP_ID = "TEST_OAUTH_SHOP_1"  # TEST_ prefix = sentinel (conftest cleanup)
TEST_SHOP_ID_2 = "TEST_OAUTH_SHOP_2"
AT_KEY = "access_token"
RT_KEY = "refresh_token"


def _grant_payload() -> dict[str, Any]:
    """The /token/get response shape the flow consumes (user-level)."""
    return {
        AT_KEY: "TTP_grant_at_abc",
        RT_KEY: "TTP_grant_rt_abc",
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "account_name": "Test Seller VN",  # fallback only (seller name)
        "region": "VN",  # fallback only (seller_base_region)
        "seller_type": "CROSS_BORDER",
        "user_type": 5,
        "granted_scopes": ["seller.order.read", "seller.product.read"],
        "_upstream_data_keys": [
            "access_token",
            "open_id",
            "seller_base_region",
            "seller_name",
            "user_type",
        ],
    }


def _shops_payload() -> list[dict[str, Any]]:
    """The Get Authorized Shops list the flow materializes rows from."""
    return [
        {
            "shop_id": TEST_SHOP_ID,
            "shop_cipher": "grant_cipher_abc",
            "account_name": "Test Shop VN",
            "region": "VN",
            "seller_type": "CROSS_BORDER",
            "_raw_keys": ["id", "cipher", "name", "region", "seller_type"],
        }
    ]


class _FakeCtx:
    """Mutations point for the flow's two outbound stubs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_erp_v2.proxy.tiktok_oauth as flow

        self.grant: dict[str, Any] = _grant_payload()
        self.shops: list[dict[str, Any]] = _shops_payload()
        self.exchange_calls: list[str] = []
        self.shops_calls: list[str] = []

        def _fake_exchange(*, auth_code: str) -> dict[str, Any]:
            self.exchange_calls.append(auth_code)
            return self.grant

        def _fake_shops(*, access_token: str) -> list[dict[str, Any]]:
            self.shops_calls.append(access_token)
            return list(self.shops)

        monkeypatch.setattr(flow, "exchange_auth_code", _fake_exchange)
        monkeypatch.setattr(flow, "fetch_authorized_shops", _fake_shops)


@pytest.fixture()
def fake_exchange(monkeypatch: pytest.MonkeyPatch) -> _FakeCtx:
    """Stub the two upstream calls (token/get + authorized shops)."""
    return _FakeCtx(monkeypatch)


# ─── register_state / pop_state ─────────────────────────────────────


def test_register_state_round_trip(db_session, fernet_key: str) -> None:
    """register_state returns the raw token (for the URL) + persists a
    sha256 hash row that pop_state can later validate."""
    from tts_erp_v2.db.models.integration import OAuthState
    from tts_erp_v2.proxy.tiktok_oauth import pop_state, register_state

    raw, expires_at = register_state(db_session)
    assert isinstance(raw, str) and len(raw) >= 32
    assert expires_at > datetime.now(UTC)

    status, sid = pop_state(db_session, raw)
    assert status == "ok"
    assert sid is not None

    row = db_session.execute(
        select(OAuthState).where(OAuthState.id == sid)
    ).scalar_one()
    assert row.provider == "tiktok"
    assert row.consumed_at is not None


def test_register_state_is_unguessable(db_session, fernet_key: str) -> None:
    """Two registrations must yield distinct raw states."""
    from tts_erp_v2.proxy.tiktok_oauth import register_state

    raw1, _ = register_state(db_session)
    raw2, _ = register_state(db_session)
    assert raw1 != raw2


def test_pop_state_is_single_use(db_session, fernet_key: str) -> None:
    """Consuming the same state twice → second call reports 'reused'."""
    from tts_erp_v2.proxy.tiktok_oauth import pop_state, register_state

    raw, _ = register_state(db_session)
    status, _ = pop_state(db_session, raw)
    assert status == "ok"
    status2, sid2 = pop_state(db_session, raw)
    assert status2 == "reused"
    assert sid2 is None


def test_pop_state_unknown(db_session, fernet_key: str) -> None:
    """A state we never registered → 'unknown' (CSRF gate fails closed)."""
    from tts_erp_v2.proxy.tiktok_oauth import pop_state

    status, sid = pop_state(db_session, "never_registered_state")
    assert status == "unknown"
    assert sid is None


def test_pop_state_expired(db_session, fernet_key: str) -> None:
    """An expired-but-unconsumed state → 'expired' (must re-authorize)."""
    from tts_erp_v2.db.models.integration import OAuthState
    from tts_erp_v2.proxy.tiktok_oauth import _state_hash, pop_state

    db_session.add(
        OAuthState(
            provider="tiktok",
            state_hash=_state_hash("stale_state"),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    db_session.commit()

    status, sid = pop_state(db_session, "stale_state")
    assert status == "expired"
    assert sid is None


# ─── complete_tiktok_authorization ──────────────────────────────────


def test_complete_authorization_bootstraps_rows(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """Full flow: state consumed + shops endpoint called with the user
    token + one credentials & channel-account pair per shop, and the
    plaintext round-trips through token_service."""
    from tts_erp_v2.db.models.commerce import ChannelAccount
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        pop_state,
        register_state,
    )
    from tts_erp_v2.proxy.token_service import load_credentials

    raw, _ = register_state(db_session)
    out = complete_tiktok_authorization(db_session, code="TTP_code_abc", state=raw)

    assert fake_exchange.shops_calls == ["TTP_grant_at_abc"]
    assert out["shops"][0]["shop_id"] == TEST_SHOP_ID
    assert out["shops"][0]["credential_id"] is not None

    # State consumed (single use).
    status, _ = pop_state(db_session, raw)
    assert status == "reused"

    # Credentials row with decrypted envelope.
    view = load_credentials(db_session, "tiktok", TEST_SHOP_ID)
    assert view is not None
    want_at = "TTP_grant_at_abc"
    want_rt = "TTP_grant_rt_abc"
    assert view.access_token == want_at
    assert view.refresh_token == want_rt
    assert view.shop_cipher == "grant_cipher_abc"
    assert view.account_label == "Test Shop VN"  # per-shop name from the list
    assert view.granted_scopes == ["seller.order.read", "seller.product.read"]
    assert view.expires_at is not None

    # Channel-account row linked to the credential.
    acct = db_session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert acct.account_name == "Test Shop VN"
    assert acct.region == "VN"
    assert acct.seller_type == "CROSS_BORDER"
    assert acct.status == "active"
    assert acct.credential_id == out["shops"][0]["credential_id"]
    assert out["shops"][0]["account_id"] == acct.id


def test_complete_authorization_multi_shop(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """A seller granting two shops lands two credentials + two channel
    rows (shared user token, per-shop cipher)."""
    from tts_erp_v2.db.models.commerce import ChannelAccount
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange.shops.append(
        {
            "shop_id": TEST_SHOP_ID_2,
            "shop_cipher": "cipher_2",
            "account_name": "Test Shop 2",
            "region": "SG",
            "seller_type": "CROSS_BORDER",
            "_raw_keys": ["id", "cipher", "name", "region", "seller_type"],
        }
    )
    raw, _ = register_state(db_session)
    out = complete_tiktok_authorization(db_session, code="code_1", state=raw)

    assert [s["shop_id"] for s in out["shops"]] == [
        TEST_SHOP_ID,
        TEST_SHOP_ID_2,
    ]
    ids = {s["credential_id"] for s in out["shops"]}
    assert len(ids) == 2

    cred_count = db_session.execute(
        select(func.count())
        .select_from(Credentials)
        .where(
            Credentials.provider == "tiktok",
            Credentials.external_account_id.in_([TEST_SHOP_ID, TEST_SHOP_ID_2]),
        )
    ).scalar_one()
    acct_count = db_session.execute(
        select(func.count())
        .select_from(ChannelAccount)
        .where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id.in_([TEST_SHOP_ID, TEST_SHOP_ID_2]),
        )
    ).scalar_one()
    assert cred_count == 2
    assert acct_count == 2


def test_complete_authorization_idempotent(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """Re-authorizing the SAME shop (renewal) must update in place — one
    credentials row, one channel-account row — never duplicate."""
    from tts_erp_v2.db.models.commerce import ChannelAccount
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        register_state,
    )

    raw, _ = register_state(db_session)
    complete_tiktok_authorization(db_session, code="code_1", state=raw)
    raw2, _ = register_state(db_session)
    out2 = complete_tiktok_authorization(db_session, code="code_2", state=raw2)

    cred_count = db_session.execute(
        select(func.count())
        .select_from(Credentials)
        .where(
            Credentials.provider == "tiktok",
            Credentials.external_account_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    acct_count = db_session.execute(
        select(func.count())
        .select_from(ChannelAccount)
        .where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert cred_count == 1
    assert acct_count == 1
    assert out2["shops"][0]["credential_id"] is not None


def test_complete_authorization_unknown_state_rejected(
    db_session, fernet_key: str
) -> None:
    """Callback with a state we never issued → OAuthFlowError, no rows."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
    )

    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state="forged_state")
    assert ei.value.kind == "state_invalid"

    # Nothing bootstrapped — scope the count to THIS test's sentinel id so
    # strays left by other (aborted) TEST_ runs can't flip the assertion.
    acct_count = db_session.execute(
        text("select count(*) from commerce.shops where shop_id = :sid"),
        {"sid": TEST_SHOP_ID},
    ).scalar_one()
    cred_count = db_session.execute(
        text(
            "select count(*) from integration.credentials "
            "where provider='tiktok' and external_account_id = :sid"
        ),
        {"sid": TEST_SHOP_ID},
    ).scalar_one()
    assert acct_count == 0
    assert cred_count == 0


def test_complete_authorization_unsupported_user_type(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """A creator/partner grant (user_type 1/3) is not a shop — reject with
    a typed error before any upstream shops call."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange.grant["user_type"] = 3  # partner
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "user_type"
    assert fake_exchange.shops_calls == []  # rejected before the shops call


def test_complete_authorization_no_authorized_shop(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """A valid token that grants no shop cannot bootstrap anything."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange.shops = []
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "no_authorized_shop"


def test_complete_authorization_missing_shop_id(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """An authorized-shop entry without a shop_id cannot be keyed."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange.shops[0]["shop_id"] = None
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "missing_shop_id"


def test_complete_authorization_missing_shop_cipher(
    db_session, fernet_key: str, fake_exchange: _FakeCtx
) -> None:
    """A shop without shop_cipher fails loudly — never writes a credential
    row whose first data job dies on the missing cipher (cross-border
    routing + HMAC signing require it)."""
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange.shops[0]["shop_cipher"] = None
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "missing_shop_cipher"
    # No half-broken row left behind for the shop.
    n = db_session.execute(
        select(func.count())
        .select_from(Credentials)
        .where(
            Credentials.provider == "tiktok",
            Credentials.external_account_id == TEST_SHOP_ID,
        )
    ).scalar()
    assert n == 0


def test_complete_authorization_upstream_failure_consumes_state(
    db_session, fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream exchange failure (e.g. auth_code already used) raises and
    the state is spent — the operator must start a fresh link."""
    from tts_erp_v2.proxy.errors import UpstreamHttpError
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        pop_state,
        register_state,
    )

    def _boom(*, auth_code: str) -> dict[str, Any]:
        raise UpstreamHttpError(200, "tiktok token get code=10001 used")

    import tts_erp_v2.proxy.tiktok_oauth as flow

    monkeypatch.setattr(flow, "exchange_auth_code", _boom)

    raw, _ = register_state(db_session)
    with pytest.raises(UpstreamHttpError):
        complete_tiktok_authorization(db_session, code="stale", state=raw)
    status, _ = pop_state(db_session, raw)
    assert status == "reused"


def test_complete_authorization_shops_failure_consumes_state(
    db_session, fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorized-shops upstream failure also raises; state already spent."""
    from tts_erp_v2.proxy.errors import UpstreamHttpError
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        pop_state,
        register_state,
    )

    def _boom(*, access_token: str) -> list[dict[str, Any]]:
        raise UpstreamHttpError(200, "tiktok authorized shops code=10001 denied")

    import tts_erp_v2.proxy.tiktok_oauth as flow

    monkeypatch.setattr(flow, "fetch_authorized_shops", _boom)

    raw, _ = register_state(db_session)
    with pytest.raises(UpstreamHttpError):
        complete_tiktok_authorization(db_session, code="stale", state=raw)
    status, _ = pop_state(db_session, raw)
    assert status == "reused"
