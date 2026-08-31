"""Unit tests for token_service.py — encryption + credentials table IO.

These tests target the *behavioural* contract of the legacy
``tdd/oauth_receiver_core.py`` so the new module is drop-in compatible:

1. Fernet round-trip on ``integration.credentials.ciphertext``.
2. ``upsert_credentials`` upserts by (provider, external_account_id).
3. ``load_credentials`` decrypts and returns plaintext.
4. ``is_expired`` honours ``expires_at`` with a configurable skew.
5. ``refresh_if_needed`` calls the refresher when expired and writes
   the new ciphertext back to the row.

The legacy module exposes ``encrypt``, ``decrypt``, ``db_store_token``,
``db_load_token``, ``refresh_shop_token``. The new module wraps the
table-specific behaviour with a typed interface and is provider-agnostic.

External HTTP (the refresh call) is mocked — never reaches the network.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# Force-load .env so TTS_ERP_DB_URL is set before any module under test.
os.environ.setdefault(
    "TTS_ERP_FERNET_KEY",
    "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
)


pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]


class _FakeResult:
    def __init__(self, ok: bool, **data: Any) -> None:
        self.ok = ok
        self.data = data


class _FakeRefresher:
    def __init__(self, **default: Any) -> None:
        self.defaults = default
        self.calls: list[tuple[str, str]] = []

    def __call__(self, provider: str, external_account_id: str) -> dict:
        self.calls.append((provider, external_account_id))
        return {"ok": True, **self.defaults}


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set TTS_ERP_FERNET_KEY to a known-good Fernet key for the test."""
    key = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", key)
    return key


def test_fernet_round_trip(fernet_key: str) -> None:
    from tts_erp_v2.proxy.token_service import decrypt, encrypt

    plaintext = "sk-tiktok-access-token-XYZ-987"
    blob = encrypt(plaintext)
    assert blob != plaintext.encode("utf-8")
    assert decrypt(blob) == plaintext


def test_decrypt_with_wrong_key_fails(fernet_key: str) -> None:
    from cryptography.fernet import Fernet

    from tts_erp_v2.proxy.token_service import DecryptionError, encrypt

    # Encrypt with the test key.
    blob = encrypt("real-secret")
    # Reset the singleton to use a different key.
    from tts_erp_v2.proxy import token_service

    other_key = Fernet.generate_key()
    token_service._reset_for_testing()
    token_service._configure_fernet(other_key.decode("utf-8"))
    with pytest.raises(DecryptionError):
        token_service.decrypt(blob)


def test_is_expired_respects_skew(fernet_key: str) -> None:
    from tts_erp_v2.proxy.token_service import is_expired

    now = datetime.now(timezone.utc)
    # Expires in 1 hour, default skew 60s → not expired.
    assert not is_expired(now + timedelta(hours=1), now=now)
    # Expires in 30s, default skew 60s → expired (within skew window).
    assert is_expired(now + timedelta(seconds=30), now=now)
    # Already past.
    assert is_expired(now - timedelta(minutes=1), now=now)
    # None expiry → never expires.
    assert not is_expired(None, now=now)


def test_upsert_and_load_credentials(db_session, fernet_key: str) -> None:
    """Persist a row, then load it back and verify plaintext survives."""
    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.token_service import (
        load_credentials,
        upsert_credentials,
    )

    # Upsert initial.
    row = upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_001",
        account_label="TEST shop 1",
        plaintext_access_token="at_plain",
        plaintext_refresh_token="rt_plain",
        plaintext_shop_cipher="cipher_plain",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        granted_scopes=["orders", "products"],
    )
    db_session.commit()

    loaded = load_credentials(db_session, "tiktok", "shop_TEST_001")
    assert loaded is not None
    assert loaded.provider == "tiktok"
    assert loaded.external_account_id == "shop_TEST_001"
    assert loaded.access_token == "at_plain"
    assert loaded.refresh_token == "rt_plain"
    assert loaded.shop_cipher == "cipher_plain"
    assert loaded.granted_scopes == ["orders", "products"]
    assert loaded.expires_at is not None
    # Ciphertext is NOT plaintext on disk — query the row directly to confirm.
    from sqlalchemy import select

    persisted = db_session.execute(
        select(Credentials).where(Credentials.external_account_id == "shop_TEST_001")
    ).scalar_one()
    assert persisted.ciphertext != b"at_plain"
    assert isinstance(row, Credentials)


def test_upsert_replaces_existing_row(db_session, fernet_key: str) -> None:
    """Re-upsert must update the same row (UNIQUE(provider, external_account_id))."""
    from sqlalchemy import select

    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.token_service import upsert_credentials

    upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_002",
        plaintext_access_token="at_v1",
        plaintext_refresh_token="rt_v1",
    )
    db_session.commit()

    upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_002",
        plaintext_access_token="at_v2",
        plaintext_refresh_token="rt_v2",
    )
    db_session.commit()

    rows = (
        db_session.execute(
            select(Credentials).where(
                Credentials.external_account_id == "shop_TEST_002"
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].ciphertext != b"at_v1"


def test_load_credentials_returns_none_when_missing(
    db_session, fernet_key: str
) -> None:
    from tts_erp_v2.proxy.token_service import load_credentials

    assert load_credentials(db_session, "tiktok", "shop_TEST_unknown") is None


def test_refresh_if_needed_calls_refresher_when_expired(
    db_session, fernet_key: str
) -> None:
    from tts_erp_v2.proxy.token_service import (
        refresh_if_needed,
        upsert_credentials,
    )

    upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_003",
        plaintext_access_token="stale_at",
        plaintext_refresh_token="stale_rt",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.commit()

    refresher = _FakeRefresher(
        access_token="new_at",
        refresh_token="new_rt",
        shop_cipher="new_cipher",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )

    loaded = refresh_if_needed(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_003",
        refresher=refresher,
    )
    assert loaded.access_token == "new_at"
    assert refresher.calls == [("tiktok", "shop_TEST_003")]


def test_refresh_if_needed_skips_when_fresh(db_session, fernet_key: str) -> None:
    from tts_erp_v2.proxy.token_service import (
        refresh_if_needed,
        upsert_credentials,
    )

    upsert_credentials(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_004",
        plaintext_access_token="fresh_at",
        plaintext_refresh_token="fresh_rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db_session.commit()

    refresher = _FakeRefresher()
    loaded = refresh_if_needed(
        db_session,
        provider="tiktok",
        external_account_id="shop_TEST_004",
        refresher=refresher,
    )
    assert loaded.access_token == "fresh_at"
    assert refresher.calls == []  # not called


def test_refresh_if_needed_missing_row_returns_none(
    db_session, fernet_key: str
) -> None:
    from tts_erp_v2.proxy.token_service import refresh_if_needed

    refresher = _FakeRefresher()
    assert (
        refresh_if_needed(
            db_session,
            provider="tiktok",
            external_account_id="shop_TEST_zzz",
            refresher=refresher,
        )
        is None
    )


# ─── CredentialsView.from_row envelope-format handling ────────────────
#
# The 2026-08-29 v1→v2 migration left some legacy rows whose ciphertext
# is a bare Fernet(access_token) string rather than the JSON envelope we
# expect post-cutover. Production fault 2026-08-30: from_row raised
# DecryptionError → the entire sync-worker tick aborted, no further
# shops got refreshed. The contract (per token_service docstring) is
# degrade-to-single-token-view, not crash.


def test_from_row_legacy_bare_token_degrades_gracefully(
    db_session, fernet_key: str, caplog
) -> None:
    """Non-JSON ciphertext → return a single-token view, log a warning,
    don't raise. The bare plaintext becomes the access_token;
    refresh_token / shop_cipher default to None so any downstream call
    that needs them surfaces as a 106001 / 401 upstream rather than
    crashing the worker.
    """
    from sqlalchemy import select

    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy.token_service import (
        CredentialsView,
        encrypt,
        load_credentials,
    )

    # Encrypt a BARE token (legacy format, NOT a JSON envelope).
    legacy_blob = encrypt("legacy_bare_token_xyz")
    row = Credentials(
        provider="tiktok",
        external_account_id="TEST_LEGACY_BARE_001",
        ciphertext=legacy_blob,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db_session.add(row)
    db_session.commit()

    # from_row must NOT raise; it must return a degraded view.
    caplog.clear()
    with caplog.at_level("WARNING", logger="tts_erp_v2.proxy.token_service"):
        view = CredentialsView.from_row(row)

    assert isinstance(view, CredentialsView)
    assert view.access_token == "legacy_bare_token_xyz"
    assert view.refresh_token is None  # degraded
    assert view.shop_cipher is None  # degraded
    assert view.external_account_id == "TEST_LEGACY_BARE_001"
    # The warning must mention the operator-actionable migration script.
    assert any(
        "re_encrypt" in rec.message or "JSON envelope" in rec.message
        for rec in caplog.records
    ), (
        f"expected degradation warning in caplog; got: {[r.message for r in caplog.records]}"
    )

    # And load_credentials must return the same degraded view (the
    # caller-facing API path) without crashing.
    loaded = load_credentials(db_session, "tiktok", "TEST_LEGACY_BARE_001")
    assert loaded is not None
    assert loaded.access_token == "legacy_bare_token_xyz"
    assert loaded.refresh_token is None
    assert loaded.shop_cipher is None

    # Re-fetch the row to confirm we did not silently corrupt the
    # ciphertext — the original blob must still be on disk.
    persisted = db_session.execute(
        select(Credentials).where(
            Credentials.external_account_id == "TEST_LEGACY_BARE_001"
        )
    ).scalar_one()
    assert persisted.ciphertext == legacy_blob


def test_from_row_invalid_token_raises_decryption_error(
    db_session, fernet_key: str
) -> None:
    """Truly undecryptable ciphertext (wrong Fernet key / tampered blob)
    must still raise DecryptionError. We do NOT silently degrade here:
    there's no usable plaintext to return, and the operator needs to
    know.
    """
    from cryptography.fernet import Fernet

    from tts_erp_v2.db.models.integration import Credentials
    from tts_erp_v2.proxy import token_service
    from tts_erp_v2.proxy.token_service import (
        CredentialsView,
        DecryptionError,
    )

    # Encrypt with a DIFFERENT key than the singleton will use.
    other_key = Fernet.generate_key()
    bad_blob = Fernet(other_key).encrypt(b"not_a_valid_envelope")
    row = Credentials(
        provider="tiktok",
        external_account_id="TEST_INVALID_TOKEN_001",
        ciphertext=bad_blob,
    )
    # Reset the singleton to a stable key, then encrypt nothing —
    # just put the bad blob in directly so decrypt() raises InvalidToken.
    token_service._reset_for_testing()
    token_service._configure_fernet("YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=")

    with pytest.raises(DecryptionError):
        CredentialsView.from_row(row)
