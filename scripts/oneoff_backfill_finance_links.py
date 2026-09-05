"""One-off backfill: finance.payouts amounts + settlement_transactions.order_pk.

Background
----------
Audit 2026-09-05 (lane fix: tiktok finance 202309 payload shapes) found two
write-path defects in ``tts_erp_v2/jobs/tiktok/finance.py`` that left real
data on the floor:

1. **Payout amounts never landed.** Live ``/finance/202309/payments``
   payloads carry amounts as NESTED ``{"value": ..., "currency": ...}``
   objects, but ``_parse_payout`` read ``raw.get("amount")`` /
   ``raw.get("currency")`` as flat scalars → every ``finance.payouts`` row
   was written with ``amount = NULL`` and ``currency = NULL`` even though
   ``integration.raw_records.payload`` had the real values.

2. **Transactions never linked to orders.** 202309 ``statement_transactions``
   payloads carry an upstream ``order_id``, but the job never resolved it →
   the whole ``finance.settlement_transactions`` table sat with
   ``order_pk = NULL``, so 订单×结算 couldn't be reconciled at the model
   layer.

The parser fix is in the job itself (future ticks write both fields). This
script repairs the EXISTING rows that predate the fix. It is additive
(UPDATE … SET … WHERE … IS NULL) — never truncates, never deletes.

Why a separate one-off instead of relying on the scheduler
----------------------------------------------------------
The payouts sub-job re-pulls all payments every tick (202309 payloads carry
no ``update_time``, so its cursor never advances), which WOULD auto-fill
payout amounts after a restart. But ``settlement_transactions.order_pk``
will NOT be repaired by a re-sync: the statements cursor has already
advanced past the existing statements, so their transactions are never
re-upserted. Both repairs are therefore done here deterministically.

USAGE
-----
    # 1. dry-run preview (no writes; prints counts it WOULD update)
    python3 scripts/oneoff_backfill_finance_links.py --dry-run

    # 2. actually do it (prints + executes)
    python3 scripts/oneoff_backfill_finance_links.py --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# scripts/ is on sys.path automatically when this file is run directly
# (``python scripts/oneoff_backfill_finance_links.py``), so the helper
# below needs no sys.path juggling before import.
from _db_url import normalize_db_url


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


def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print counts; make NO writes"
    )
    parser.add_argument(
        "--confirm", action="store_true", help="actually execute the repairs"
    )
    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    try:
        import psycopg
    except ImportError:  # pragma: no cover — operator env guard
        sys.exit("psycopg3 not importable; activate the repo venv (.venv)")

    db_url = _resolve_db_url()
    conn = psycopg.connect(db_url)

    # Two additive repairs (UPDATE … WHERE … IS NULL) — never destructive.
    # SQL is inlined at every execute call site: psycopg 3.3 types
    # ``execute(query)`` as LiteralString-only (PEP 675) and the repo's
    # security lint flags any non-literal sink (same pattern as
    # scripts/oneoff_finance_reset.py — no helper indirection).
    with conn:
        count_cur = conn.execute(
            "SELECT count(*) FROM finance.payouts p "
            "WHERE p.amount IS NULL AND EXISTS ("
            "  SELECT 1 FROM integration.raw_records r "
            "  WHERE r.endpoint = '/finance/202309/payments' "
            "    AND r.payload ->> 'id' = p.external_payout_id "
            "    AND jsonb_typeof(r.payload -> 'amount') = 'object' "
            "    AND r.payload -> 'amount' ->> 'value' IS NOT NULL"
            ")",
        )
        row = count_cur.fetchone()
        pending = row[0] if row else 0
        print(f"[payout amount/currency] rows pending repair: {pending}")
        if args.confirm and pending:
            update_cur = conn.execute(
                "UPDATE finance.payouts p "
                "SET amount = (r.payload -> 'amount' ->> 'value')::numeric, "
                "    currency = r.payload -> 'amount' ->> 'currency', "
                "    updated_at = now() "
                "FROM integration.raw_records r "
                "WHERE r.endpoint = '/finance/202309/payments' "
                "  AND r.payload ->> 'id' = p.external_payout_id "
                "  AND jsonb_typeof(r.payload -> 'amount') = 'object' "
                "  AND r.payload -> 'amount' ->> 'value' IS NOT NULL "
                "  AND p.amount IS NULL",
            )
            print(f"[payout amount/currency] updated {update_cur.rowcount} rows")

        count_cur = conn.execute(
            "SELECT count(*) FROM finance.settlement_transactions t "
            "WHERE t.order_pk IS NULL AND EXISTS ("
            "  SELECT 1 FROM integration.raw_records r "
            "  JOIN finance.settlement_statements s "
            "    ON s.id = t.settlement_statement_id "
            "  JOIN finance.payouts p ON p.id = s.payout_id "
            "  JOIN commerce.sales_orders o "
            "    ON o.order_id = r.payload ->> 'order_id' "
            "  WHERE r.endpoint LIKE '%statement_transactions%' "
            "    AND r.external_id = t.external_transaction_id "
            "    AND r.payload ? 'order_id' "
            "    AND o.shop_pk = p.shop_pk"
            ")",
        )
        row = count_cur.fetchone()
        pending = row[0] if row else 0
        print(f"[transaction order_pk] rows pending repair: {pending}")
        if args.confirm and pending:
            update_cur = conn.execute(
                "UPDATE finance.settlement_transactions t "
                "SET order_pk = sub.order_pk, updated_at = now() "
                "FROM ("
                "  SELECT DISTINCT ON (r.external_id) "
                "         r.external_id AS txn_ext, o.id AS order_pk "
                "  FROM integration.raw_records r "
                "  JOIN finance.settlement_transactions t2 "
                "    ON t2.external_transaction_id = r.external_id "
                "  JOIN finance.settlement_statements s "
                "    ON s.id = t2.settlement_statement_id "
                "  JOIN finance.payouts p ON p.id = s.payout_id "
                "  JOIN commerce.sales_orders o "
                "    ON o.order_id = r.payload ->> 'order_id' "
                "   AND o.shop_pk = p.shop_pk "
                "  WHERE r.endpoint LIKE '%statement_transactions%' "
                "    AND r.payload ? 'order_id' "
                "    AND t2.order_pk IS NULL "
                "  ORDER BY r.external_id, r.captured_at DESC "
                ") sub "
                "WHERE t.external_transaction_id = sub.txn_ext "
                "  AND t.order_pk IS NULL",
            )
            print(f"[transaction order_pk] updated {update_cur.rowcount} rows")
    conn.close()
    print("dry-run complete — no writes" if args.dry_run else "backfill complete")


if __name__ == "__main__":
    _run()
