"""Shared helpers for v1 → v2 migration scripts.

Four concerns live here:
  1. Time conversion (epoch seconds, epoch milliseconds, GMT+8 text)
  2. Database connection helpers (source DB, target DB, oauth DB)
  3. Batching + dry-run reporting utilities
  4. Production-migration kill-switch (``TTS_ERP_ALLOW_PROD_MIGRATION``)

Anything script-specific stays in its own ``migrate_*.py`` module.
"""
from __future__ import annotations

import os
import sys
import urllib.parse
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

# Best-effort load of .env so the scripts work when invoked directly
# (without going through ``tts_erp_v2.db.base`` / tests_v2 conftest).
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:  # pragma: no cover — dotenv is optional.
    pass

# Fixed UTC+8 offset (no DST zoneinfo dependency at runtime).
_GMT8 = timezone(timedelta(hours=8))

# Sentinel: the well-known test-shop row that we always drop from the v1
# mirror tables. It exists in `public.shops` and `oauth_receiver.oauth_tokens`
# because the legacy FastAPI startup-lifespan ``backfill`` propagates it.
# Real production shops are 18-19 digit strings (TikTok IDs); ``MOCK_SHOP_12345``
# is the only synthetic one observed.
MOCK_SHOP_ID = "MOCK_SHOP_12345"


def is_real_shop_id(shop_id: str | None) -> bool:
    """Return True iff the shop id should be carried over to v2.

    Drops the synthetic MOCK_SHOP_12345. Empty / None inputs are also
    rejected — those would violate NOT NULL constraints downstream.
    """
    return bool(shop_id) and shop_id != MOCK_SHOP_ID


# ─── prod migration kill-switch ──────────────────────────────────────
#
# 2026-08-30 incident: ``tests_v2/migration/conftest._ensure_migrations_applied``
# used to be ``autouse=True``, so any pytest run under ``tests_v2/`` replayed
# ``migrate_shops.run(dry_run=False)`` against the PRODUCTION database and
# overwrote ``integration.credentials.ciphertext`` with the legacy
# (non-JSON-envelope) format. ``load_credentials()`` then started throwing
# ``json.decoder.JSONDecodeError`` in the sync worker; full sync outage
# for ~22 hours.
#
# The fixture was made opt-in afterwards, but the scripts themselves had
# no second line of defense — a future ``autouse=True`` regression or a
# stray ``scripts/test.sh migration`` invocation could replay the same
# disaster. This guard closes that loop: every ``run(dry_run=False)`` and
# ``re_encrypt_credentials.main()`` refuses to write to the prod DB unless
# the operator explicitly sets the env var.

PROD_GUARD_ENV = "TTS_ERP_ALLOW_PROD_MIGRATION"


def is_prod_migration_allowed() -> bool:
    """Return True iff the prod-migration kill-switch is set.

    True only when ``$TTS_ERP_ALLOW_PROD_MIGRATION == "1"``. Any other
    value (unset, empty string, ``"true"``, ``"yes"``, ``"0"``) is
    treated as NOT allowed — the explicit ``"1"`` literal avoids
    accidental opt-in via shell truthiness.
    """
    return os.environ.get(PROD_GUARD_ENV) == "1"


def require_prod_guard(dry_run: bool, *, action: str) -> None:
    """Refuse to write to the production DB unless the kill-switch is set.

    dry-run paths (``dry_run=True``) skip the check — they don't write.
    Real runs require ``$TTS_ERP_ALLOW_PROD_MIGRATION == "1"``; otherwise
    the function prints the refusal reason on stderr and raises
    ``SystemExit(2)``. Exit code 2 keeps it distinguishable from the
    per-script exit codes (0 / 1) and matches the convention used by
    ``re_encrypt_credentials.main()``.
    """
    if dry_run:
        return
    if is_prod_migration_allowed():
        return
    print(
        f"REFUSED: {action} would write to the production DB.\n"
        f"  Set the env var to opt in: export {PROD_GUARD_ENV}=1\n"
        f"  (dry_run=True does not require this gate.)\n"
        f"  Background: 2026-08-30 incident — autouse test fixture "
        f"overwrote\n"
        f"  integration.credentials.ciphertext with the legacy format and "
        f"broke\n"
        f"  the sync worker. This guard requires explicit opt-in for "
        f"every real run.",
        file=sys.stderr,
    )
    raise SystemExit(2)


# ─── time conversion ──────────────────────────────────────────────────


def epoch_seconds_to_utc(seconds: int | None) -> datetime | None:
    """Convert an epoch-second integer to an aware UTC ``datetime``.

    Returns ``None`` if the input is falsy or out of range.
    """
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def epoch_ms_to_utc(ms: int | None) -> datetime | None:
    """Convert an epoch-millisecond integer to an aware UTC ``datetime``.

    Used for ``logistics_tracking_events.event_time`` (the rest of the
    legacy tables are epoch seconds). Returns ``None`` for falsy / bad.
    """
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def gmt8_string_to_utc(value: str | None) -> datetime | None:
    """Parse a "YYYY-MM-DD HH:MM:SS" UTC+8 string from miaoshou gmt_*
    columns and return the corresponding aware UTC ``datetime``.

    The miaoshou API returns wall-clock strings in CN local time without
    timezone designator — per tech-doc/data-model-survey.md §1 we treat
    them as UTC+8. Returns ``None`` for falsy / unparseable inputs.
    """
    if not value:
        return None
    if not isinstance(value, str):
        value = str(value)
    s = value.strip()
    if not s:
        return None
    # Accept both "YYYY-MM-DD HH:MM:SS" and "YYYY-MM-DDTHH:MM:SS".
    s = s.replace("T", " ").split(".")[0].split("+")[0].strip()
    # Two acceptable formats.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # We deliberately parse a naive datetime — the next line
            # explicitly tags it as UTC+8 (naive datetime construction is
            # intentional here; the noqa suppresses DTZ007 on that line).
            naive = datetime.strptime(s, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        # Treat as UTC+8 wall clock, convert to UTC.
        return naive.replace(tzinfo=_GMT8).astimezone(timezone.utc)
    return None


# ─── database engines ─────────────────────────────────────────────────


def _psycopg_url() -> str:
    """Return the standard postgresql:// URL from .env.

    Forces the SQLAlchemy driver to ``psycopg`` (v3); the legacy
    ``postgresql://`` URL in .env omits the driver and SA 2.0 in this
    environment would otherwise import psycopg2 (not installed).
    """
    raw = os.environ.get("TTS_ERP_DB_URL")
    if not raw:
        raise RuntimeError(
            "TTS_ERP_DB_URL not configured. Set it in .env or os.environ."
        )
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


def get_source_engine() -> Engine:
    """Read-only engine against the legacy ``public.*`` mirror tables.

    The legacy tables are explicitly read-only for this migration; the
    scripts in this package never write to them.
    """
    url = _psycopg_url()
    # NullPool: migration scripts are short-lived batch processes; a pooled
    # engine per call site exhausts max_connections=100 when tests drive all
    # scripts in one pytest process. Each use opens and closes its own conn.
    return create_engine(url, future=True, pool_pre_ping=True, poolclass=NullPool)


def get_target_engine() -> Engine:
    """Engine against the same tts_erp DB, used for v2 schema writes.

    Reads/writes the nine new schemas (integration/commerce/...).
    """
    url = _psycopg_url()
    return create_engine(url, future=True, pool_pre_ping=True, poolclass=NullPool)


def get_oauth_engine() -> Engine:
    """Engine against the separate ``oauth_receiver`` DB on the same PG host.

    Derives the connection URL from ``TTS_ERP_DB_URL`` by swapping the
    database name. This avoids requiring a separate env var.
    """
    raw = _psycopg_url()
    parsed = urllib.parse.urlparse(raw)
    # Strip /<dbname> and replace with /oauth_receiver.
    new_path = "/oauth_receiver"
    oauth_url = urllib.parse.urlunparse(parsed._replace(path=new_path))
    return create_engine(oauth_url, future=True, pool_pre_ping=True, poolclass=NullPool)


def get_oauth_raw_connection() -> psycopg.Connection:
    """Return a raw psycopg connection to the oauth_receiver database.

    Used when we need to fetch ciphertext blobs (bytea) directly — the
    SQLAlchemy default mapping for ``bytea`` works but raw psycopg is
    marginally more reliable on cross-version PG.
    """
    raw = _psycopg_url()
    parsed = urllib.parse.urlparse(raw)
    user = urllib.parse.unquote(parsed.username or "")
    pwd = urllib.parse.unquote(parsed.password or "")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    return psycopg.connect(
        host=host, port=port, user=user, password=pwd, dbname="oauth_receiver",
        connect_timeout=5,
    )


# ─── batching + reporting ──────────────────────────────────────────────


def iter_batches(items: Iterable[Any], batch_size: int) -> Iterator[list[Any]]:
    """Yield consecutive batches of ``batch_size`` from ``items``.

    ``batch_size`` <= 0 falls back to a single batch containing everything.
    """
    if batch_size <= 0:
        batch_size = max(1, len(list(items)))
    batch: list[Any] = []
    for it in items:
        batch.append(it)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


class DryRunSink:
    """A no-op writer that prints what it *would* have written.

    Mirrors the shape of the small upsert calls the migration scripts
    perform, so a script can be rewritten to delegate writes through
    ``DryRunSink`` when ``--dry-run`` is set. We don't actually wire
    every script through this — the simpler approach is to early-return
    before the ``session.execute`` call. The sink exists to share the
    print format / counters.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def record(self, table: str, n: int) -> None:
        self.counts[table] = self.counts.get(table, 0) + n

    def report(self) -> str:
        lines = ["DRY-RUN PLAN:"]
        for table, n in sorted(self.counts.items()):
            lines.append(f"  - {table}: {n} row(s)")
        return "\n".join(lines)
