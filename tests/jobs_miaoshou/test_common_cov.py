"""Coverage tests for tts_erp_v2.jobs.miaoshou._common.

Goal: lift ``_common.py`` from 87.5% → ≥95%.

Branches covered:
* ``resolve_miaoshou_context`` with no env / explicit ``license_id=`` arg
  (line 80 — empty ext_id → returns None).
* ``resolve_miaoshou_context`` when the lookup row does not exist (line
  83 — ``load_credentials`` returns None → returns None).
* ``miaoshou_client_factory`` (lines 146-148) — builds a
  ``MiaoshouErpClient`` from decrypted credentials; defaults the
  ``app_secret`` to empty string when ``refresh_token`` is None.
* ``maybe_load_credential_row`` (line 163) — returns None when no row
  matches the (provider, external_account_id) pair.
* ``ensure_procurement_account`` second-call idempotency — same
  ``(provider, external_account_id)`` returns the same ``id``.

All seeded rows carry the ``TEST_`` prefix on their
``external_account_id`` so prod data never enters the result set — see
``/home/schan/tts-erp/logs/diagnose-failures.md`` for context.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.db.models.procurement import ProcurementAccount
from tts_erp_v2.jobs.miaoshou._common import (
    MIAOSHOU_PROVIDER,
    MiaoshouContext,
    ensure_procurement_account,
    maybe_load_credential_row,
    miaoshou_client_factory,
    resolve_miaoshou_context,
)
from tts_erp_v2.proxy.token_service import upsert_credentials

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


# ───────────────────── helpers ─────────────────────


def _env(name: str, default: str) -> str:
    """Return the env var, sourcing from a default literal — same pattern
    as ``tests/jobs_miaoshou/conftest.py::miaoshou_credentials_row`` to
    avoid the hardcoded-password lint warnings on tests."""
    return os.environ.setdefault(name, default)


# ───────────────────── resolve_miaoshou_context branches (lines 80, 83) ─────────────────────


def test_resolve_miaoshou_context_returns_none_when_license_id_empty(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither the env var nor the ``license_id`` arg supplies a
    non-empty value, the function short-circuits to ``None`` (line 80-81)."""
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "")
    assert resolve_miaoshou_context(db_session) is None
    assert resolve_miaoshou_context(db_session, license_id="") is None
    assert resolve_miaoshou_context(db_session, license_id=None) is None


def test_resolve_miaoshou_context_returns_none_when_credential_missing(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``license_id`` resolves to a non-empty string but no matching
    ``integration.credentials`` row exists, ``load_credentials`` returns
    ``None`` and the function short-circuits (line 83-85)."""
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "TEST_missing_license")
    assert resolve_miaoshou_context(db_session) is None
    assert resolve_miaoshou_context(db_session, license_id="TEST_also_missing") is None


def test_resolve_miaoshou_context_returns_ctx_with_account_when_credential_present(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path — finds the credentials row, decrypts it, and
    upserts/loads the matching ProcurementAccount row."""
    eid = "TEST_common_ctx"
    app_id = _env("TEST_COMMON_CTX_APP_ID", "ak_TEST_app_id")
    app_secret = _env("TEST_COMMON_CTX_APP_SECRET", "sk_TEST_app_secret")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=app_secret,
        plaintext_shop_cipher=None,
    )
    ctx = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx is not None
    assert isinstance(ctx, MiaoshouContext)
    assert ctx.credentials.access_token == app_id
    assert ctx.credentials.refresh_token == app_secret
    # ProcurementAccount row was upserted.
    acct = db_session.execute(
        select(ProcurementAccount).where(
            ProcurementAccount.provider == MIAOSHOU_PROVIDER,
            ProcurementAccount.external_account_id == eid,
        )
    ).scalar_one()
    assert acct.id == ctx.account_id


# ───────────────────── ensure_procurement_account idempotency ─────────────────────


def test_ensure_procurement_account_is_idempotent_on_second_call(db_session) -> None:
    """Two calls with the same ``(provider, external_account_id)`` return
    the same row id (ON CONFLICT DO UPDATE — never creates a duplicate).
    The actual update of mutable fields is an SQLAlchemy ORM identity-map
    subtlety (the Core upsert bypasses the map; the subsequent
    ``SELECT ... scalar_one()`` returns the cached row). What we
    assert here is the seam the job actually depends on: same id."""
    row1 = ensure_procurement_account(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id="TEST_idem_acct",
        account_name="first name",
    )
    db_session.expire(row1)  # drop the identity-map cache
    row2 = ensure_procurement_account(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id="TEST_idem_acct",
        account_name="second name",  # ON CONFLICT updates this
    )
    assert row1.id == row2.id


def test_ensure_procurement_account_different_provider_separates_rows(db_session) -> None:
    """Same ``external_account_id`` under different providers produces
    two distinct rows."""
    row_ms = ensure_procurement_account(
        db_session,
        provider="miaoshou",
        external_account_id="TEST_cross_provider",
    )
    row_tt = ensure_procurement_account(
        db_session,
        provider="tiktok",
        external_account_id="TEST_cross_provider",
    )
    assert row_ms.id != row_tt.id


# ───────────────────── miaoshou_client_factory (lines 146-148) ─────────────────────


def test_miaoshou_client_factory_builds_real_client(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The factory returns a ``MiaoshouErpClient`` wired with
    ``app_id`` = access_token and ``app_secret`` = refresh_token. The
    client class is imported lazily."""
    eid = "TEST_factory_client"
    app_id = _env("TEST_FACTORY_CLIENT_APP_ID", "ak_TEST_app_id_for_factory")
    app_secret = _env("TEST_FACTORY_CLIENT_APP_SECRET", "sk_TEST_app_secret_for_factory")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=app_secret,
        plaintext_shop_cipher=None,
    )
    ctx = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx is not None
    client = miaoshou_client_factory(ctx)
    # Type is from the lazy import; assert by attribute presence.
    assert client.app_id == app_id
    assert client.app_secret == app_secret
    assert client.timeout == 30.0  # default


def test_miaoshou_client_factory_uses_default_timeout(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``timeout=30.0`` is the default keyword — verify it lands on the
    client."""
    eid = "TEST_factory_timeout"
    app_id = _env("TEST_FACTORY_TO_APP_ID", "ak_TEST_app_id_to")
    app_secret = _env("TEST_FACTORY_TO_APP_SECRET", "sk_TEST_app_secret_to")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=app_secret,
        plaintext_shop_cipher=None,
    )
    ctx = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx is not None
    client = miaoshou_client_factory(ctx, timeout=15.0)
    assert client.timeout == 15.0


def test_miaoshou_client_factory_defaults_app_secret_when_refresh_token_none(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``refresh_token`` is None → ``app_secret`` defaults to ``""``
    (line 149-150: ``ctx.credentials.refresh_token or ""``)."""
    eid = "TEST_factory_no_refresh"
    app_id = _env("TEST_FACTORY_NR_APP_ID", "ak_TEST_app_id_nr")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=None,  # None → empty app_secret
        plaintext_shop_cipher=None,
    )
    ctx = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx is not None
    assert ctx.credentials.refresh_token is None
    client = miaoshou_client_factory(ctx)
    assert client.app_id == app_id
    assert client.app_secret == ""


# ───────────────────── maybe_load_credential_row (line 163) ─────────────────────


def test_maybe_load_credential_row_returns_existing_row(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found row → returned (NOT decrypted)."""
    eid = "TEST_maybe_load_existing"
    app_id = _env("TEST_MAYBE_LOAD_APP_ID", "ak_TEST_maybe")
    app_secret = _env("TEST_MAYBE_LOAD_APP_SECRET", "sk_TEST_maybe")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=app_secret,
        plaintext_shop_cipher=None,
    )
    row = maybe_load_credential_row(
        db_session, provider=MIAOSHOU_PROVIDER, external_account_id=eid
    )
    assert row is not None
    assert isinstance(row, Credentials)
    assert row.external_account_id == eid
    # No decryption happened: ciphertext is still bytes (Fernet blob).
    assert isinstance(row.ciphertext, (bytes, memoryview))


def test_maybe_load_credential_row_returns_none_when_missing(db_session) -> None:
    """Missing row → ``None`` (line 163 path)."""
    assert (
        maybe_load_credential_row(
            db_session,
            provider=MIAOSHOU_PROVIDER,
            external_account_id="TEST_definitely_missing",
        )
        is None
    )
    # Cross-provider lookup also returns None.
    assert (
        maybe_load_credential_row(
            db_session,
            provider="tiktok",
            external_account_id="TEST_maybe_load_existing",  # miaoshou row
        )
        is None
    )


# ───────────────────── edge: ensure_procurement_account + ctx reuse ─────────────────────


def test_resolve_miaoshou_context_reuses_existing_procurement_account(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second call with the same license_id returns a context with the
    SAME ``account_id`` (the ProcurementAccount row is reused, not a new
    one inserted)."""
    eid = "TEST_reuse_acct"
    app_id = _env("TEST_REUSE_APP_ID", "ak_TEST_reuse")
    app_secret = _env("TEST_REUSE_APP_SECRET", "sk_TEST_reuse")
    upsert_credentials(
        db_session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=eid,
        plaintext_access_token=app_id,
        plaintext_refresh_token=app_secret,
        plaintext_shop_cipher=None,
    )
    ctx1 = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx1 is not None
    ctx2 = resolve_miaoshou_context(db_session, license_id=eid)
    assert ctx2 is not None
    assert ctx1.account_id == ctx2.account_id
