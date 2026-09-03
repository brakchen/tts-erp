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


# ─── 2026-08-30 incident guard tests ───────────────────────────────
#
# These pin the SQL and the run() guard so a future regression cannot
# re-introduce the legacy ciphertext overwrite. They are pure string /
# function-level assertions (no DB) so they're cheap and safe to run.


class TestUpsertCredentialSql:
    """_UPSERT_CREDENTIAL must not overwrite ciphertext on conflict.

    On 2026-08-30, the previous ``ON CONFLICT DO UPDATE SET ciphertext =
    EXCLUDED.ciphertext, ...`` overwrote the v2 JSON-envelope ciphertext
    with the legacy Fernet(raw_access_token) format during a test run,
    breaking the sync worker. The VALUES clause still writes ciphertext
    on the *initial* INSERT, but on conflict the existing bytes must be
    preserved.
    """

    def test_ciphertext_not_in_update_clause(self) -> None:
        from scripts.migrate_v1_to_v2.migrate_shops import _UPSERT_CREDENTIAL
        # The UPDATE clause is everything after ``DO UPDATE SET`` and
        # before ``RETURNING``. No form of ``ciphertext = ...`` may
        # appear there — EXCLUDED.ciphertext is exactly the form that
        # caused the incident.
        upper = _UPSERT_CREDENTIAL.upper()
        update_start = upper.index("DO UPDATE SET")
        returning_idx = upper.index("RETURNING")
        update_clause = upper[update_start:returning_idx]
        assert "EXCLUDED.CIPHERTEXT" not in update_clause, (
            "_UPSERT_CREDENTIAL must NOT overwrite ciphertext on conflict "
            "— see 2026-08-30 incident."
        )
        # Also ensure no bare ``CIPHERTEXT = `` assignment is in the
        # update clause (catches a future fix that uses the table name
        # instead of EXCLUDED).
        assert "CIPHERTEXT     =" not in update_clause, (
            "_UPSERT_CREDENTIAL UPDATE clause must not assign ciphertext"
        )

    def test_company_secret_not_in_update_clause(self) -> None:
        from scripts.migrate_v1_to_v2.migrate_shops import _UPSERT_CREDENTIAL
        upper = _UPSERT_CREDENTIAL.upper()
        update_start = upper.index("DO UPDATE SET")
        returning_idx = upper.index("RETURNING")
        update_clause = upper[update_start:returning_idx]
        # Same property for company_secret_ciphertext — only the
        # initial INSERT writes it.
        assert "COMPANY_SECRET_CIPHERTEXT" not in update_clause, (
            "_UPSERT_CREDENTIAL must NOT overwrite "
            "company_secret_ciphertext on conflict."
        )

    def test_ciphertext_in_initial_insert(self) -> None:
        # Sanity check: the VALUES clause DOES include ciphertext, so
        # the first INSERT writes it. The fix only restricts the
        # conflict path.
        from scripts.migrate_v1_to_v2.migrate_shops import _UPSERT_CREDENTIAL
        assert "%(CIPHERTEXT)S" in _UPSERT_CREDENTIAL.upper().replace(
            " ", ""
        ), "VALUES clause must still write ciphertext on initial INSERT"

    def test_metadata_columns_still_updated(self) -> None:
        # The fix only restricts ciphertext; account_label, expires_at,
        # granted_scopes, extra, updated_at must still be updated.
        from scripts.migrate_v1_to_v2.migrate_shops import _UPSERT_CREDENTIAL
        upper = _UPSERT_CREDENTIAL.upper()
        update_start = upper.index("DO UPDATE SET")
        returning_idx = upper.index("RETURNING")
        update_clause = upper[update_start:returning_idx]
        for col in (
            "ACCOUNT_LABEL", "EXPIRES_AT", "GRANTED_SCOPES", "EXTRA",
            "UPDATED_AT",
        ):
            assert col in update_clause, (
                f"metadata column {col} must still be refreshed on conflict"
            )


class TestMigrateShopsRunGuard:
    """run(dry_run=False) must exit 2 without TTS_ERP_ALLOW_PROD_MIGRATION.

    No DB is touched — the guard fires before any engine / connection is
    even created (see require_prod_guard implementation). These tests
    pin the contract so a future regression that removes the guard
    call gets caught immediately.
    """

    def test_real_run_without_env_var_exits_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        # Belt-and-braces: also unset TTS_ERP_DB_URL so a regression
        # that bypasses the guard would fail at engine construction
        # with a clear error rather than silently write to a real DB.
        monkeypatch.delenv("TTS_ERP_ALLOW_PROD_MIGRATION", raising=False)
        monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
        from scripts.migrate_v1_to_v2 import migrate_shops
        with pytest.raises(SystemExit) as excinfo:
            migrate_shops.run(dry_run=False, batch_size=10, verbose=False)
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "REFUSED" in captured.err
        assert "TTS_ERP_ALLOW_PROD_MIGRATION=1" in captured.err

    def test_real_run_with_env_var_proceeds_to_db(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # With the env var set AND TTS_ERP_DB_URL unset, the guard
        # passes and the script tries to construct an engine — which
        # then raises a clear error (not SystemExit 2). This proves
        # the guard is the gating mechanism and not just an incidental
        # env check somewhere downstream.
        monkeypatch.setenv("TTS_ERP_ALLOW_PROD_MIGRATION", "1")
        monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
        from scripts.migrate_v1_to_v2 import migrate_shops
        with pytest.raises(RuntimeError) as excinfo:
            migrate_shops.run(dry_run=False, batch_size=10, verbose=False)
        # The error must come from the engine layer (DB URL missing),
        # NOT from SystemExit 2 (the guard).
        assert "TTS_ERP_DB_URL" in str(excinfo.value) or "configured" in str(
            excinfo.value,
        ), (
            f"expected DB URL error, got: {excinfo.value!r}"
        )

    def test_dry_run_does_not_require_env_var(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # dry_run=True must not trigger the guard. With TTS_ERP_DB_URL
        # unset AND no env var, the guard passes and the script then
        # tries to construct an engine — raising the DB URL error.
        # We assert it's the DB error, not SystemExit 2.
        monkeypatch.delenv("TTS_ERP_ALLOW_PROD_MIGRATION", raising=False)
        monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
        from scripts.migrate_v1_to_v2 import migrate_shops
        with pytest.raises(RuntimeError) as excinfo:
            migrate_shops.run(dry_run=True, batch_size=10, verbose=False)
        assert "TTS_ERP_DB_URL" in str(excinfo.value) or "configured" in str(
            excinfo.value,
        ), (
            f"dry_run should bypass guard and reach DB layer; got: "
            f"{excinfo.value!r}"
        )
