"""Tests for migrate_shops.

Covers:
* Dry-run reports correct counts (1 channel account + 1 credential,
  MOCK_SHOP_12345 excluded on both source tables).
* Real-run is idempotent: a second run produces the same final state.
* MOCK_SHOP_12345 never lands in commerce.channel_accounts.
* migration cipher round-trips: the encrypted access_token stays a
  non-NULL bytea blob (we don't decrypt, only verify it's opaque bytes).
"""
from __future__ import annotations

import pytest

from scripts.migrate_v1_to_v2.common import MOCK_SHOP_ID


pytestmark = [
    pytest.mark.domain_migration,
    pytest.mark.layer_integration,
    pytest.mark.slow,
]


def _count(table: str) -> int:
    # ``table`` is a fixed allowlist of fully-qualified v2 table names;
    # we resolve to one of two literal SQL strings to keep ruff's static
    # SQL-injection check happy.
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    table_q = {
        "commerce.channel_accounts":
            "SELECT count(*) FROM commerce.channel_accounts",
        "integration.credentials":
            "SELECT count(*) FROM integration.credentials",
    }
    if table not in table_q:
        raise ValueError(f"unknown table {table!r}")
    with eng.connect() as conn:
        row = conn.exec_driver_sql(table_q[table]).first()
    return int(row[0])


def test_dry_run_reports_expected_counts(dry_run_runner) -> None:
    """Dry-run sees 2 source rows on each side; excludes MOCK + writes
    1 row to each target table (dry-run)."""
    stats = dry_run_runner("shops")
    # 2 shops, 1 mock → 1 real
    assert stats.shops_seen == 2
    assert stats.shops_skipped_mock == 1
    assert stats.accounts_upserted == 1
    # 2 oauth tokens, 1 mock → 1 real
    assert stats.oauth_seen == 2
    assert stats.oauth_skipped_mock == 1
    assert stats.credentials_upserted == 1


def test_real_run_is_idempotent(real_runner) -> None:
    """Re-running migrate_shops in apply mode does not duplicate rows."""
    before_accounts = _count("commerce.channel_accounts")
    before_creds = _count("integration.credentials")
    real_runner("shops")
    after_accounts = _count("commerce.channel_accounts")
    after_creds = _count("integration.credentials")
    assert after_accounts == before_accounts
    assert after_creds == before_creds


def test_mock_shop_never_lands_in_channel_accounts() -> None:
    """Hard guard: MOCK_SHOP_12345 must NEVER appear in the target."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM commerce.channel_accounts "
            "WHERE external_account_id = %(ext)s",
            {"ext": MOCK_SHOP_ID},
        ).first()
    assert int(row[0]) == 0, "MOCK_SHOP_12345 leaked into target"


def test_mock_shop_never_lands_in_credentials() -> None:
    """Same hard guard for credentials (oauth_tokens mock is also dropped)."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT count(*) FROM integration.credentials "
            "WHERE external_account_id = %(ext)s",
            {"ext": MOCK_SHOP_ID},
        ).first()
    assert int(row[0]) == 0, "MOCK_SHOP_12345 leaked into credentials"


def test_real_credentials_cipher_is_nonempty() -> None:
    """The credential row's ciphertext must be a non-NULL bytea blob."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT ciphertext, octet_length(ciphertext) "
            "FROM integration.credentials "
            "WHERE external_account_id != %(mock)s "
            "LIMIT 1",
            {"mock": MOCK_SHOP_ID},
        ).first()
    assert row is not None, "expected at least one real credential row"
    blob = row[0]
    assert blob is not None, "ciphertext must be non-NULL"
    assert isinstance(blob, (bytes, memoryview)), (
        f"expected bytes, got {type(blob)}"
    )
    assert len(blob) > 0, "ciphertext must not be empty"


def test_channel_account_links_to_credential() -> None:
    """The real channel_account row must have a non-NULL credential_id."""
    from tts_erp_v2.db.base import get_engine
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT credential_id FROM commerce.channel_accounts "
            "WHERE external_account_id != %(mock)s "
            "LIMIT 1",
            {"mock": MOCK_SHOP_ID},
        ).first()
    assert row is not None
    assert row[0] is not None, (
        "real shop's channel_account should be linked to a credentials row"
    )
