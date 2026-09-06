"""DB-backed tests for the TikTok shop-authorization orchestration:
CSRF state registration/consumption + the full auth_code → credentials
+ channel-account bootstrap.

Under test: :mod:`tts_erp_v2.proxy.tiktok_oauth` (Lane E — the
half of the OAuth lifecycle that v1's oauth_receiver used to own and
that ``token_service.py`` deliberately does NOT carry: register_state /
pop_state live here per its docstring).

Design contract (from tts-partner-api-docs/Authorization overview.md):
* the redirect callback carries ``code`` + ``state``;
* ``state`` is server-generated, single-use, and validated on callback;
* the ``auth_code`` is exchanged once via ``grant_type=authorized_code``;
* a seller grant carries a 19-digit ``shop_id`` which becomes our
  ``integration.credentials.external_account_id`` + the
  ``commerce.shops.shop_id`` row (jobs fan out over credentials).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]

TEST_SHOP_ID = "TEST_OAUTH_SHOP_1"  # TEST_ prefix = sentinel (conftest cleanup)
AT_KEY = "access_token"
RT_KEY = "refresh_token"
SC_KEY = "shop_cipher"


def _grant_payload() -> dict[str, Any]:
    return {
        AT_KEY: "TTP_grant_at_abc",
        RT_KEY: "TTP_grant_rt_abc",
        SC_KEY: "grant_cipher_abc",
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "shop_id": TEST_SHOP_ID,
        "account_name": "Test Seller VN",
        "region": "VN",
        "seller_type": "CROSS_BORDER",
        "user_type": 5,
        "granted_scopes": ["seller.order.read", "seller.product.read"],
    }


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


@pytest.fixture()
def fake_exchange(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub out the upstream /token/get call inside the flow module."""
    import tts_erp_v2.proxy.tiktok_oauth as flow

    payload: dict[str, Any] = dict(_grant_payload())

    def _fake(*, auth_code: str) -> dict[str, Any]:
        payload["auth_code_seen"] = auth_code
        return payload

    monkeypatch.setattr(flow, "exchange_auth_code", _fake)
    return payload


def test_complete_authorization_bootstraps_rows(
    db_session, fernet_key: str, fake_exchange: dict[str, Any]
) -> None:
    """Full flow: state consumed + credentials + channel-account created
    and linked, then the plaintext round-trips through token_service."""
    from tts_erp_v2.db.models.commerce import ChannelAccount
    from tts_erp_v2.proxy.tiktok_oauth import (
        complete_tiktok_authorization,
        pop_state,
        register_state,
    )
    from tts_erp_v2.proxy.token_service import load_credentials

    raw, _ = register_state(db_session)
    out = complete_tiktok_authorization(
        db_session, code="TTP_code_abc", state=raw
    )

    assert out["shop_id"] == TEST_SHOP_ID
    assert out["credential_id"] is not None

    # State consumed (single use).
    status, _ = pop_state(db_session, raw)
    assert status == "reused"

    # Credentials row with decrypted envelope.
    view = load_credentials(db_session, "tiktok", TEST_SHOP_ID)
    assert view is not None
    assert view.access_token == "TTP_grant_at_abc"
    assert view.refresh_token == "TTP_grant_rt_abc"
    assert view.shop_cipher == "grant_cipher_abc"
    assert view.account_label == "Test Seller VN"
    assert view.granted_scopes == ["seller.order.read", "seller.product.read"]
    assert view.expires_at is not None

    # Channel-account row linked to the credential.
    acct = db_session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert acct.account_name == "Test Seller VN"
    assert acct.region == "VN"
    assert acct.seller_type == "CROSS_BORDER"
    assert acct.status == "active"
    assert acct.credential_id == out["credential_id"]
    assert out["account_id"] == acct.id


def test_complete_authorization_idempotent(
    db_session, fernet_key: str, fake_exchange: dict[str, Any]
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
        select(func.count()).select_from(Credentials).where(
            Credentials.provider == "tiktok",
            Credentials.external_account_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    acct_count = db_session.execute(
        select(func.count()).select_from(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == TEST_SHOP_ID,
        )
    ).scalar_one()
    assert cred_count == 1
    assert acct_count == 1
    assert out2["credential_id"] is not None


def test_complete_authorization_unknown_state_rejected(
    db_session, fernet_key: str
) -> None:
    """Callback with a state we never issued → OAuthFlowError, no rows."""
    from sqlalchemy import text

    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
    )

    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(
            db_session, code="code_x", state="forged_state"
        )
    assert ei.value.kind == "state_invalid"

    # Nothing bootstrapped — scope the count to THIS test's sentinel id so
    # strays left by other (aborted) TEST_ runs can't flip the assertion.
    acct_count = db_session.execute(
        text(
            "select count(*) from commerce.shops "
            "where shop_id = :sid"
        ),
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
    db_session, fernet_key: str, fake_exchange: dict[str, Any]
) -> None:
    """A creator/partner grant (user_type 1/3) is not a shop — reject with
    a typed error instead of bootstrapping a row we can't sync."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange["user_type"] = 3  # partner
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "user_type"


def test_complete_authorization_missing_shop_id(
    db_session, fernet_key: str, fake_exchange: dict[str, Any]
) -> None:
    """A grant without a shop_id cannot be keyed — typed error."""
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange["shop_id"] = None
    raw, _ = register_state(db_session)
    with pytest.raises(OAuthFlowError) as ei:
        complete_tiktok_authorization(db_session, code="code_x", state=raw)
    assert ei.value.kind == "missing_shop_id"


def test_complete_authorization_missing_shop_cipher(
    db_session, fernet_key: str, fake_exchange: dict[str, Any]
) -> None:
    """Grant without shop_cipher fails loudly — never writes a credential
    row whose first data job dies on the missing cipher (cross-border
    routing + HMAC signing require it). See the contract note in
    :func:`complete_tiktok_authorization`."""
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.tiktok_oauth import (
        OAuthFlowError,
        complete_tiktok_authorization,
        register_state,
    )

    fake_exchange["shop_cipher"] = None
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
