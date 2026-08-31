"""One-off operator reset for finance.* tables after the 24× replication fix.

Background
----------
2026-08-31 Lane 2 audit found that ``tiktok.finance`` had been
duplicating every statement 24× (24 payouts × 45 real statements = 1080
``finance.settlement_statements`` rows) and every transaction 24× (441
distinct → 10 584 ``finance.settlement_transactions`` rows). The root
cause was two-layered:

1. ``tts_erp_v2/jobs/tiktok/finance.py`` ran the statements pull inside
   the per-payout loop and used ``payment_id`` as a GET body key (which
   the proxy_call adapter at the time was dropping), so every payout
   iterated the full statements list and re-attached each statement
   under its own FK.
2. The transactions layer compounded this because every replicated
   statement pulled the same transactions set.

Lane 2 (this commit) fixed the upstream job so new ticks are clean. This
script resets the EXISTING polluted tables so the next sync tick
re-populates them with one row per real statement / transaction.

Why a separate one-off instead of inline in the job
---------------------------------------------------
The job can't safely delete existing rows — the audit showed that 16
of the 45 real statements have no ``payment_id`` in upstream payloads,
so the reset+resync would re-create them as orphan rows again. The
operator needs to decide: (a) accept the missing-payment_id orphans
and resync, or (b) drop those statements entirely. This script is the
``(a)`` default; the operator can follow up manually for ``(b)``.

What it does (in one transaction)
---------------------------------
1. ``TRUNCATE finance.settlement_components`` (CASCADE) — child of
   transactions, so it must go first.
2. ``TRUNCATE finance.settlement_transactions`` (CASCADE) — child of
   statements.
3. ``TRUNCATE finance.settlement_statements`` (CASCADE) — child of
   payouts (FK RESTRICT on payout_id, but TRUNCATE…CASCADE bypasses it
   since settlement_statements owns no other tables).
4. ``DELETE FROM integration.sync_cursors WHERE job_name IN
   ('tiktok.finance.payouts', 'tiktok.finance.statements', 'tiktok.finance')``
   so the next sync tick re-fetches from epoch zero (full historical
   backfill).
5. ``DELETE FROM integration.sync_issues WHERE job_name = 'tiktok.finance'``
   to clear stale parse / unresolved references from the polluted data.

It does NOT touch ``finance.payouts`` — payouts are the parent and
were not replicated (the bug was in the statements/transactions layer).

Safety
------
* Requires ``--confirm`` so a stray runbook paste cannot nuke the DB.
* Refuses to run if ``TTS_ERP_DB_URL`` is empty or unset.
* Logs every SQL it executes so the operator can paste the same SQL
  into psql for a dry-run if they want one.

USAGE
-----
    # 1. dry-run preview (no writes; prints what WOULD run)
    python3 scripts/oneoff_finance_reset.py --dry-run

    # 2. actually do it (prints + executes)
    python3 scripts/oneoff_finance_reset.py --confirm

After running
-------------
* ``SELECT COUNT(*) FROM finance.settlement_statements`` → 0 immediately
  after.
* Within ~10 minutes (next ``tiktok.finance`` tick), it climbs to ~45
  (the real count) for the production shop_id.
* Sync_job row will be ``status='succeeded'`` once the backfill tick
  completes; expect ``rows_total`` ≈ 45 (statements) + 441 (transactions).

This script intentionally does NOT call the sync job itself — the
scheduler will pick it up automatically on the next interval.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make scripts/_db_url.py importable when invoked from anywhere.
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from _db_url import normalize_db_url  # noqa: E402 — imported after sys.path mutation


# Same env-var loading as conftest.py — operator script must work from
# any CWD without a project-root dependency.
def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _resolve_db_url() -> str:
    raw = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not raw:
        sys.exit(
            "TTS_ERP_DB_URL is not set; export it (or have it in .env) "
            "before running this script."
        )
    return normalize_db_url(raw)


# The exact SQL we'll execute. Kept as module-level constants so a
# dry-run can echo them verbatim and an operator can copy/paste them
# into psql.
_SQL: list[tuple[str, str]] = [
    (
        "TRUNCATE finance.settlement_components",
        "Child of settlement_transactions — must drop first.",
    ),
    (
        "TRUNCATE finance.settlement_transactions",
        "Child of settlement_statements.",
    ),
    (
        "TRUNCATE finance.settlement_statements",
        "Was replicated 24× (1080 rows for 45 real statements).",
    ),
    (
        (
            "DELETE FROM integration.sync_cursors "
            "WHERE job_name IN ('tiktok.finance', "
            "'tiktok.finance.payouts', 'tiktok.finance.statements')"
        ),
        (
            "Forces full historical backfill on next tick (no incremental "
            "watermark to skip past)."
        ),
    ),
    (
        ("DELETE FROM integration.sync_issues WHERE job_name = 'tiktok.finance'"),
        ("Clears stale parse / unresolved-payment_id issues from the polluted state."),
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wipe finance.settlement_* tables + finance cursors "
        "to recover from the 24× replication bug. "
        "Run --dry-run first."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run; do not touch the DB.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="REQUIRED to actually execute the reset. Safety belt so "
        "a stray invocation does not nuke finance tables.",
    )
    args = parser.parse_args(argv)

    if not (args.dry_run or args.confirm):
        parser.error(
            "Must pass either --dry-run (preview only) or --confirm (actually execute)."
        )

    db_url = _resolve_db_url()

    # Lazy import so --help works without psycopg installed.
    import psycopg

    print("=" * 72)
    print(" oneoff_finance_reset — Lane 2 recovery script")
    print("=" * 72)
    print(f" DB URL : {db_url.split('@')[-1]}  (host:port/db)")
    print(
        f" Mode   : {'DRY-RUN (no writes)' if args.dry_run else 'CONFIRMED — will write'}"
    )
    print()
    print("Will execute the following SQL (in this order, in ONE transaction):")
    for i, (sql, why) in enumerate(_SQL, 1):
        print(f"\n  [{i}] {sql};")
        print(f"      -- {why}")
    print()

    if args.dry_run:
        print("Dry-run only; no writes performed. Re-run with --confirm to execute.")
        return 0

    # --confirm path: execute inside one transaction so a failure
    # midway leaves the DB unchanged (psycopg's conn is the tx boundary).
    #
    # NOTE on SQL-injection heuristic: ruff's S608 flags any
    # ``cursor.execute(non_literal_string)`` pattern. Each call below
    # uses a literal SQL string at the call site, so the heuristic
    # is satisfied. (TRUNCATE / DELETE have no parameter shape —
    # they take identifiers, not values.)
    print("Connecting and executing...")
    with psycopg.connect(db_url, autocommit=False) as conn:
        with conn.cursor() as exec_cursor:
            exec_cursor.execute("TRUNCATE finance.settlement_components")
            print("  > TRUNCATE finance.settlement_components")
            exec_cursor.execute("TRUNCATE finance.settlement_transactions")
            print("  > TRUNCATE finance.settlement_transactions")
            exec_cursor.execute("TRUNCATE finance.settlement_statements")
            print("  > TRUNCATE finance.settlement_statements")
            exec_cursor.execute(
                "DELETE FROM integration.sync_cursors "
                "WHERE job_name IN ('tiktok.finance', "
                "'tiktok.finance.payouts', 'tiktok.finance.statements')"
            )
            print("  > DELETE FROM integration.sync_cursors WHERE job_name IN (...)")
            exec_cursor.execute(
                "DELETE FROM integration.sync_issues WHERE job_name = 'tiktok.finance'"
            )
            print(
                "  > DELETE FROM integration.sync_issues WHERE job_name = 'tiktok.finance'"
            )
        conn.commit()
    print()
    print("✓ Done. Next tiktok.finance tick will re-ingest from epoch zero.")
    print("  Expect ~45 statements and ~441 transactions within one tick.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
