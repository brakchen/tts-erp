"""Tests for scripts._db_url.normalize_db_url.

Every script in the project that hands a ``.env`` URL to raw
``psycopg.connect()`` (api_keys.py, sync_cron.py, verify_db.py,
analytics_sync/pg_repositories.py, tdd/_backfill.py, tdd/tts_erp_fastapi.py,
and the cleanup fixture in tdd/conftest.py) goes through
:func:`normalize_db_url` first. The function is tiny, but its
contract is the only thing standing between production scripts
and the ``ProgrammingError: missing "=" after "postgresql+psycopg://..."``
that broke every raw-psycopg caller on the day ``.env`` switched to
the SQLAlchemy form.

These tests pin that contract. They are unit tests on the helper
plus a single integration smoke (``api_keys.py list``) that proves
the wiring is end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts._db_url import normalize_db_url

# ─── normalize_db_url() — the contract every raw-psycopg caller depends on


def test_normalize_strips_psycopg_dialect():
    """The common case: .env uses postgresql+psycopg://... — strip to
    plain postgresql://... for psycopg3's conninfo parser."""
    assert (
        normalize_db_url("postgresql+psycopg://u:p@h:5432/d")
        == "postgresql://u:p@h:5432/d"
    )


def test_normalize_passes_through_plain_postgres():
    """A legacy postgresql:// URL is returned unchanged."""
    assert normalize_db_url("postgresql://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"


def test_normalize_strips_other_dialects():
    """Any ``+dialect`` suffix on the postgresql scheme is removed.
    Defends against future SQLAlchemy drivers (asyncpg, psycopg2, ...)."""
    assert normalize_db_url("postgresql+asyncpg://u:p@h/d") == "postgresql://u:p@h/d"
    assert (
        normalize_db_url("postgresql+psycopg2://legacy-host/x")
        == "postgresql://legacy-host/x"
    )


def test_normalize_rejects_empty():
    """Empty input falls through unchanged so callers can keep their
    own ``if not url: sys.exit(...)`` guard without a confusing
    secondary error."""
    assert normalize_db_url("") == ""


def test_normalize_passes_through_non_postgres_schemes():
    """Non-postgres schemes (sqlite, mysql, ...) are NOT mangled — we
    don't know what the caller meant, so leave the URL alone for them
    to surface their own parser error."""
    assert normalize_db_url("sqlite:///tmp/x.db") == "sqlite:///tmp/x.db"
    assert normalize_db_url("mysql://u:p@h/d") == "mysql://u:p@h/d"


def test_normalize_preserves_url_encoded_password():
    """The whole point of normalisation is to keep the credentials
    intact. A URL-encoded password must round-trip through unchanged."""
    raw = "postgresql+psycopg://postgres:C3j%26l%25u1Lx%25@127.0.0.1:5432/tts_erp"
    out = normalize_db_url(raw)
    assert out == "postgresql://postgres:C3j%26l%25u1Lx%25@127.0.0.1:5432/tts_erp"
    assert "%26" in out  # the % in the password survives


# ─── Integration smoke: every production entry point actually works


def test_api_keys_list_runs_against_real_db(tmp_path, monkeypatch):
    """End-to-end: api_keys.py was the first script to break the day
    ``.env`` switched to postgresql+psycopg://. With normalize_db_url
    applied to its _connect(), it now has to actually query the
    security.api_keys table without raising ProgrammingError.

    Runs the real CLI as a subprocess so we exercise the env-loading
    + psycopg.connect + SELECT path end-to-end. .env is read
    normally (no monkeypatch on the DB URL); the test fails
    immediately if the URL is wrong.
    """
    repo = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(repo / "api_keys.py"), "list"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"api_keys.py list exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The list output should contain at least the header row.
    assert "PREFIX" in result.stdout
    # And — crucially — must NOT contain the tell-tale psycopg error.
    assert 'missing "="' not in result.stdout
    assert 'missing "="' not in result.stderr


def test_verify_db_connects_and_prints_counts():
    """End-to-end: verify_db.py was also a casualty. With the
    DB_URL = normalize_db_url(DB_URL) line, the psycopg.connect at
    the top of the script must succeed.
    """
    repo = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(repo / "verify_db.py")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"verify_db.py exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The script prints a "returns: N rows for shop" line on success.
    assert "returns:" in result.stdout
    assert "cancellations:" in result.stdout
    assert 'missing "="' not in result.stdout
    assert 'missing "="' not in result.stderr
