"""TDD tests for :mod:`tts_erp_v2.proxy.tts_shop.products_api`.

Covers the typed wrappers on top of :class:`TiktokShopClient` for the
Partner API product-domain GETs documented in ``tts-partner-api-docs/``.
At this stage we only ship :func:`get_product`; the remaining GETs
(Categories / Brands / Attributes / Category Rules / Submission Records
/ Image Translation Tasks / Listing Prerequisites) are deferred.

Test surface
------------
* happy path — upstream ``code == 0`` → unwrapped ``data`` dict
* envelope errors
  - ``code != 0`` → :class:`UpstreamBusinessError` (code, message, request_id)
  - missing ``code`` → :class:`ProxyError`
  - ``data`` not an object → :class:`ProxyError`
  - response not a JSON object → :class:`ProxyError`
* configuration errors
  - ``channel_account_id`` not in commerce.channel_accounts → :class:`ChannelAccountNotFound`
  - row exists but ``platform != 'tiktok'`` → :class:`ChannelAccountNotFound`
  - credentials missing → :class:`CredentialsMissing`
  - credentials present but ``shop_cipher`` empty → :class:`CredentialsMissing`
* parameter validation
  - mutually exclusive ``return_under_review_version`` + ``return_draft_version`` → ValueError
  - ``locale`` passes through into the ``extra_params``
  - boolean flags default to ``false`` (no extra param emitted)
  - ``return_under_review_version=True`` → param emitted as ``"true"``
  - ``return_draft_version=True`` → param emitted as ``"true"``
  - ``product_id`` is interpolated into the path

Mocking pattern: substitute :class:`TiktokShopClient` with a fake
instance whose ``get()`` returns a predetermined ``TiktokCallResult``.
This exercises the wrapper's envelope parsing + parameter assembly
without hitting the network (the underlying client transport is
already covered by ``test_tts_shop_client.py``).

Isolation: uses the shared ``db_session`` (savepoint rollback) +
``fernet_key`` (TTS_ERP_FERNET_KEY pinned) fixtures from
``tests/proxy/conftest.py``. Channel-account rows are inserted via the
same session so the wrapper's read queries see them without a separate
commit cycle. ``upsert_credentials`` is used for credential seeding so
the Fernet envelope format matches production exactly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.base import Base
from tts_erp_v2.proxy import errors as proxy_errors
from tts_erp_v2.proxy.token_service import upsert_credentials
from tts_erp_v2.proxy.tts_shop import products_api
from tts_erp_v2.proxy.tts_shop.client import TiktokCallResult

pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]

PRODUCT_ID = "1729592969712207008"
SHOP_ID = "TEST_tspapi_shop"


# ---------------------------------------------------------------------------
# Fake client + envelope helpers
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for :class:`TiktokShopClient` that records the invocation."""

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        *,
        path: str,
        access_token: str,
        extra_params: dict[str, str] | None = None,
    ) -> TiktokCallResult:
        self.calls.append(
            {
                "path": path,
                "access_token": access_token,
                "extra_params": dict(extra_params or {}),
            }
        )
        if self.error is not None:
            raise self.error
        assert self.payload is not None, "fake misconfigured"
        return TiktokCallResult(payload=self.payload, http_status=200)


def _success_envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": 0,
        "message": "Success",
        "request_id": "req_success_001",
        "data": data,
    }


def _error_envelope(
    *,
    code: int = 12000002,
    message: str = "product not found",
    request_id: str = "req_err_001",
) -> dict[str, Any]:
    return {"code": code, "message": message, "request_id": request_id}


def _seed_account(
    sess: Session,
    *,
    shop_id: str = SHOP_ID,
    platform: str = "tiktok",
) -> int:
    """Insert a TEST_ channel_accounts row; return its id.

    The row lives inside the test's outer transaction (no explicit
    commit needed — the wrapper's session shares the same connection).
    """
    accounts_tbl = Base.metadata.tables["commerce.channel_accounts"]
    sess.execute(
        insert(accounts_tbl).values(
            platform=platform,
            external_account_id=shop_id,
            account_name=f"TEST acct {shop_id}",
            status="active",
        )
    )
    sess.flush()
    # Pull back the id via the same ORM path the wrapper uses.
    from sqlalchemy import select

    row = sess.execute(
        select(accounts_tbl.c.id).where(accounts_tbl.c.external_account_id == shop_id)
    ).one()
    return int(row[0])


def _seed_credentials(
    sess: Session,
    *,
    shop_id: str = SHOP_ID,
    access_token: str = "TPP_test_at",
    shop_cipher: str | None = "TEST_cipher_abc",
    refresh_token: str | None = "TPP_test_rt",
) -> None:
    """Insert credentials via the production :func:`upsert_credentials` path."""
    upsert_credentials(
        sess,
        provider="tiktok",
        external_account_id=shop_id,
        plaintext_access_token=access_token,
        plaintext_refresh_token=refresh_token,
        plaintext_shop_cipher=shop_cipher,
        account_label=f"TEST shop {shop_id}",
        granted_scopes=["seller.product.basic"],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_product_returns_data_on_success(db_session, fernet_key: str):
    """code=0 → returns the `data` dict verbatim, envelope stripped."""
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    data = {"id": PRODUCT_ID, "title": "Test Tee", "status": "ACTIVATE"}
    fake = _FakeClient(payload=_success_envelope(data))

    result = products_api.get_product(
        session=db_session,
        channel_account_id=acct_id,
        product_id=PRODUCT_ID,
        client=fake,
    )

    assert result == data
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["path"] == f"/product/202309/products/{PRODUCT_ID}"
    assert call["extra_params"] == {"shop_cipher": "TEST_cipher_abc"}
    assert call["access_token"] == "TPP_test_at"


# ---------------------------------------------------------------------------
# Envelope error mapping
# ---------------------------------------------------------------------------


def test_get_product_raises_on_non_zero_code(db_session, fernet_key: str):
    """code != 0 → UpstreamBusinessError carrying upstream code+message+request_id."""
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=_error_envelope(code=12000002, message="product not found"))

    with pytest.raises(products_api.UpstreamBusinessError) as ei:
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )

    assert ei.value.code == 12000002
    assert ei.value.message == "product not found"
    assert ei.value.request_id == "req_err_001"


def test_get_product_envelope_missing_code_raises_proxy_error(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    # Missing 'code' key entirely.
    fake = _FakeClient(payload={"message": "weird upstream", "data": {}})

    with pytest.raises(proxy_errors.ProxyError, match="missing 'code'"):
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


def test_get_product_envelope_data_not_object_raises_proxy_error(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload={"code": 0, "message": "Success", "data": "not-a-dict"})

    with pytest.raises(proxy_errors.ProxyError, match="'data' is not an object"):
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


def test_get_product_response_not_mapping_raises_proxy_error(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=["not", "a", "dict"])

    with pytest.raises(proxy_errors.ProxyError, match="not a JSON object"):
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


def test_get_product_channel_account_not_found(db_session, fernet_key: str):
    """Credentials exist but no channel_accounts row matches → ChannelAccountNotFound."""
    _seed_credentials(db_session)  # credentials seeded, no account row
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    with pytest.raises(products_api.ChannelAccountNotFound, match="not found"):
        products_api.get_product(
            session=db_session,
            channel_account_id=999_999,
            product_id=PRODUCT_ID,
            client=fake,
        )

    # Wrapper must NOT have hit the client when config fails.
    assert fake.calls == []


def test_get_product_channel_account_wrong_platform(db_session, fernet_key: str):
    """A miaoshou row must NOT route through the tiktok wrapper."""
    _seed_account(db_session, shop_id="TEST_tspapi_miaoshou", platform="miaoshou")
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    from sqlalchemy import select

    accounts_tbl = Base.metadata.tables["commerce.channel_accounts"]
    miaoshou_id = int(
        db_session.execute(
            select(accounts_tbl.c.id).where(
                accounts_tbl.c.external_account_id == "TEST_tspapi_miaoshou"
            )
        ).one()[0]
    )

    with pytest.raises(products_api.ChannelAccountNotFound, match="not found"):
        products_api.get_product(
            session=db_session,
            channel_account_id=miaoshou_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


def test_get_product_credentials_missing(db_session, fernet_key: str):
    """Account seeded, but no credentials row → CredentialsMissing."""
    acct_id = _seed_account(db_session)
    # Deliberately no _seed_credentials call.
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    with pytest.raises(products_api.CredentialsMissing, match="missing"):
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


def test_get_product_empty_shop_cipher_raises(db_session, fernet_key: str):
    """Credentials present but shop_cipher empty → CredentialsMissing.

    shop_cipher is required for cross-border routing — without it the
    upstream would return 4xx and we'd never know why.
    """
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session, shop_cipher=None)
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    with pytest.raises(products_api.CredentialsMissing, match="shop_cipher"):
        products_api.get_product(
            session=db_session,
            channel_account_id=acct_id,
            product_id=PRODUCT_ID,
            client=fake,
        )


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def test_get_product_mutually_exclusive_flags_raise_value_error():
    """Passing both flags violates the upstream contract — reject early."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        products_api.get_product(
            session=MagicMock(),  # never reached
            channel_account_id=1,
            product_id=PRODUCT_ID,
            return_under_review_version=True,
            return_draft_version=True,
            client=MagicMock(),
        )


def test_get_product_locale_passes_through(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    products_api.get_product(
        session=db_session,
        channel_account_id=acct_id,
        product_id=PRODUCT_ID,
        locale="en-US",
        client=fake,
    )

    assert fake.calls[0]["extra_params"] == {
        "shop_cipher": "TEST_cipher_abc",
        "locale": "en-US",
    }


def test_get_product_under_review_flag_emits_param(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    products_api.get_product(
        session=db_session,
        channel_account_id=acct_id,
        product_id=PRODUCT_ID,
        return_under_review_version=True,
        client=fake,
    )

    assert fake.calls[0]["extra_params"] == {
        "shop_cipher": "TEST_cipher_abc",
        "return_under_review_version": "true",
    }


def test_get_product_draft_flag_emits_param(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    products_api.get_product(
        session=db_session,
        channel_account_id=acct_id,
        product_id=PRODUCT_ID,
        return_draft_version=True,
        client=fake,
    )

    assert fake.calls[0]["extra_params"] == {
        "shop_cipher": "TEST_cipher_abc",
        "return_draft_version": "true",
    }


def test_get_product_default_flags_omit_param(db_session, fernet_key: str):
    acct_id = _seed_account(db_session)
    _seed_credentials(db_session)
    fake = _FakeClient(payload=_success_envelope({"id": PRODUCT_ID}))

    products_api.get_product(
        session=db_session,
        channel_account_id=acct_id,
        product_id=PRODUCT_ID,
        client=fake,
    )

    # Only shop_cipher — neither optional flag was set, no locale.
    assert fake.calls[0]["extra_params"] == {"shop_cipher": "TEST_cipher_abc"}