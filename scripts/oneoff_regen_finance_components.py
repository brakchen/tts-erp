"""One-off regen: settlement_components full breakdown + transaction_time.

Background
----------
Audit 2026-09-06 (second pass on tiktok.finance) found two more write-path
defects in ``tts_erp_v2/jobs/tiktok/finance.py`` that the first fix
(2026-09-05: payout nested money + transaction order_pk) did not cover:

1. **Components only ever stored the net ``settlement_amount``.** The job's
   ``_COMPONENT_COLUMNS`` allowlist used lowercase stems (``fee``,
   ``refund``, ``platform_commission``…) that never match the upstream 202309
   ``statement_transactions`` ``*_amount`` keys, and the stored
   ``component_code`` was the raw field name in lowercase. The fee/gross/
   refund/tax breakdown lived only in raw payloads, and the codes violated
   the v3 convention (uppercase stem — ``GROSS_SALES``,
   ``PLATFORM_COMMISSION``) that ``migrate_finance.py`` and
   ``db/models/finance.py`` document.

2. **``settlement_transactions.transaction_time`` was NULL for every row.**
   202309 payloads carry no ``transaction_time``; the authoritative
   timestamp is ``order_create_time`` (the endpoint's required sort_field),
   which the parser never read.

The job fix writes both correctly for FUTURE ticks. Existing rows are not
re-upserted (the statements cursor has already passed them), so this script
repairs the CURRENT rows deterministically:

1. DELETE legacy lowercase ``component_code`` rows (only ``settlement_amount``
   exists today — superseded by the uppercase ``SETTLEMENT`` row).
2. Regenerate the full non-zero breakdown for every existing transaction
   from its raw payload. Code derivation mirrors the job's
   ``_write_components``: field name minus ``_amount`` suffix, uppercased
   (``gross_sales_amount`` → ``GROSS_SALES``); zero values skipped; currency
   falls back to the payload/statement currency (``VND``).
3. Backfill ``transaction_time`` from the raw payload's
   ``order_create_time`` where it is NULL.

Additive + idempotent apart from the one-time legacy-code DELETE (that code
shape is impossible under the new job, so re-running is a no-op).

USAGE
-----
    # 1. dry-run preview (no writes; prints counts)
    python3 scripts/oneoff_regen_finance_components.py --dry-run

    # 2. actually do it (prints + executes)
    python3 scripts/oneoff_regen_finance_components.py --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


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
    scheme_end = raw.find("://")
    if "+" in raw[:scheme_end]:
        return "postgresql://" + raw[scheme_end + 3 :]
    return raw


def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument("--confirm", action="store_true", help="execute repairs")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    try:
        import psycopg
    except ImportError:  # pragma: no cover — operator env guard
        sys.exit("psycopg3 not importable; activate the repo venv (.venv)")

    conn = psycopg.connect(_resolve_db_url())

    # SQL is inlined at every execute call site (no helper indirection):
    # psycopg 3.3 types ``execute(query)`` as LiteralString-only (PEP 675)
    # and the repo security lint flags any non-literal sink.

    # ── Step 1: drop legacy lowercase component codes ──────────────────
    row = conn.execute(
        "SELECT count(*) FROM finance.settlement_components "
        "WHERE component_code = lower(component_code)"
    ).fetchone()
    legacy_n = row[0] if row else 0
    print(f"[1/3 legacy lowercase codes] rows to delete: {legacy_n}")
    if args.confirm and legacy_n:
        cur = conn.execute(
            "DELETE FROM finance.settlement_components "
            "WHERE component_code = lower(component_code)"
        )
        print(f"[1/3] deleted {cur.rowcount} rows")

    # ── Step 2: regenerate full non-zero breakdown from raw payloads ───
    row = conn.execute(
        "SELECT count(*) FROM finance.settlement_transactions t "
        "WHERE EXISTS ("
        "  SELECT 1 FROM integration.raw_records r "
        "  WHERE r.endpoint LIKE '%statement_transactions%' "
        "    AND r.external_id = t.external_transaction_id"
        ")"
    ).fetchone()
    txn_n = row[0] if row else 0
    print(f"[2/3 transactions with raw payload] to regenerate: {txn_n}")
    if args.confirm and txn_n:
        cur = conn.execute(
            "INSERT INTO finance.settlement_components "
            "(transaction_id, component_code, amount, currency) "
            "SELECT t.id, "
            "       upper(regexp_replace(k.key, '_amount$', '')), "
            "       (k.value #>> '{}')::numeric, "
            "       COALESCE(rr.payload ->> 'currency', 'VND') "
            "FROM finance.settlement_transactions t "
            "JOIN ("
            "  SELECT DISTINCT ON (external_id) external_id, payload "
            "  FROM integration.raw_records "
            "  WHERE endpoint LIKE '%statement_transactions%' "
            "  ORDER BY external_id, captured_at DESC"
            ") rr ON rr.external_id = t.external_transaction_id "
            "CROSS JOIN LATERAL jsonb_each(rr.payload) k "
            "WHERE k.key LIKE '%\\_amount' "
            "  AND k.value <> '0' "
            "  AND (k.value #>> '{}') IS NOT NULL "
            "ON CONFLICT (transaction_id, component_code) "
            "DO UPDATE SET amount = EXCLUDED.amount, "
            "              currency = EXCLUDED.currency"
        )
        print(f"[2/3] inserted/updated {cur.rowcount} component rows")

    # ── Step 3: backfill transaction_time from order_create_time ──────
    row = conn.execute(
        "SELECT count(*) FROM finance.settlement_transactions t "
        "WHERE t.transaction_time IS NULL AND EXISTS ("
        "  SELECT 1 FROM integration.raw_records r "
        "  WHERE r.endpoint LIKE '%statement_transactions%' "
        "    AND r.external_id = t.external_transaction_id "
        "    AND r.payload ? 'order_create_time'"
        ")"
    ).fetchone()
    time_n = row[0] if row else 0
    print(f"[3/3 transaction_time] rows to backfill: {time_n}")
    if args.confirm and time_n:
        cur = conn.execute(
            "UPDATE finance.settlement_transactions t "
            "SET transaction_time = to_timestamp(( "
            "  SELECT (r.payload ->> 'order_create_time')::bigint "
            "  FROM integration.raw_records r "
            "  WHERE r.endpoint LIKE '%statement_transactions%' "
            "    AND r.external_id = t.external_transaction_id "
            "  ORDER BY r.captured_at DESC LIMIT 1"
            ")) "
            "WHERE t.transaction_time IS NULL AND EXISTS ("
            "  SELECT 1 FROM integration.raw_records r "
            "  WHERE r.endpoint LIKE '%statement_transactions%' "
            "    AND r.external_id = t.external_transaction_id "
            "    AND r.payload ? 'order_create_time'"
            ")"
        )
        print(f"[3/3] updated {cur.rowcount} rows")

    conn.commit()
    conn.close()
    print("dry-run complete — no writes" if args.dry_run else "regen complete")


if __name__ == "__main__":
    _run()
