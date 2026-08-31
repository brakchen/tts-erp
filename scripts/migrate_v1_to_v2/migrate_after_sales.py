"""Migrate returns + cancellations → cases + case_lines.

Source tables (read-only):
  * public.returns       (31 rows; return/refund request header + raw)
  * public.cancellations (175 rows; cancellation request header + raw)

Target tables (v2, writeable):
  * after_sales.cases        (UNIQUE channel_account_id, external_case_id)
  * after_sales.case_lines   (UNIQUE case_id, external_case_line_id)

Case type mapping (V3 §after-sales):
  * returns.return_type = 'RETURN_AND_REFUND' → case_type = 'RETURN_AND_REFUND'
  * returns.return_type = 'REFUND'            → case_type = 'REFUND_ONLY'
  * cancellations.cancel_type = 'BUYER_CANCEL' → case_type = 'CANCELLATION'
  * cancellations.cancel_type = 'CANCEL'       → case_type = 'CANCELLATION'
  * (all observed rows fall into one of these buckets; the V3 enum has
    CANCELLATION / REFUND_ONLY / RETURN_AND_REFUND)

Line extraction (raw.return_line_items / raw.cancel_line_items):
  * Each ``return_line_items`` entry contains:
        sku_id, sku_name, product_name, product_image,
        order_line_item_id  → maps to sales_order_lines.external_line_id
        return_line_item_id → maps to case_lines.external_case_line_id
        refund_amount.{refund_total, currency}
  * Each ``cancel_line_items`` entry contains:
        sku_id, sku_name, product_name,
        order_line_item_id  → sales_order_lines.external_line_id
        cancel_line_item_id → case_lines.external_case_line_id
    (no refund_amount on cancellation lines)
  * If raw has 0 line_items, no case_lines are emitted (the case row still
    exists as a header).

Time conversion:
  * epoch seconds → timestamptz (created_at_source, updated_at_source)

Implementation notes:
  * SQL is plain string + psycopg ``%(name)s`` pyformat placeholders, passed
    via ``conn.exec_driver_sql()``.

Idempotency: ON CONFLICT on each table's natural key + DO UPDATE.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from scripts.migrate_v1_to_v2.common import (
    DryRunSink,
    epoch_seconds_to_utc,
    get_source_engine,
    get_target_engine,
    require_prod_guard,
)

# ─── case_type mapping helpers ────────────────────────────────────────


def _return_type_to_case_type(return_type: str | None) -> str:
    """Map returns.return_type → after_sales.cases.case_type enum."""
    if return_type == "RETURN_AND_REFUND":
        return "RETURN_AND_REFUND"
    if return_type == "REFUND":
        return "REFUND_ONLY"
    # Fallback: return-and-refund is the broader bucket and safest default.
    return "RETURN_AND_REFUND"


def _cancel_type_to_case_type(cancel_type: str | None) -> str:
    """Map cancellations.cancel_type → after_sales.cases.case_type enum."""
    if cancel_type in ("BUYER_CANCEL", "CANCEL"):
        return "CANCELLATION"
    # Fallback for unknown values: classify as CANCELLATION.
    return "CANCELLATION"


@dataclass
class MigrationStats:
    returns_seen: int = 0
    cancellations_seen: int = 0
    cases_upserted: int = 0
    case_lines_upserted: int = 0
    case_lines_fk_missing: int = 0
    cases_fk_missing: int = 0

    def report(self, dry_run: bool) -> str:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        return (
            f"{mode} after-sales migration:\n"
            f"  source public.returns         seen={self.returns_seen}\n"
            f"  source public.cancellations   seen={self.cancellations_seen}\n"
            f"  after_sales.cases             upserted={self.cases_upserted} "
            f"fk_missing={self.cases_fk_missing}\n"
            f"  after_sales.case_lines        upserted={self.case_lines_upserted} "
            f"fk_missing={self.case_lines_fk_missing}\n"
        )


# ─── source readers ──────────────────────────────────────────────────


_RETURNS_SQL = (
    "SELECT return_id, shop_id, order_id, "
    "       return_status, return_reason, return_type, role, "
    "       create_time, update_time, raw "
    "FROM public.returns"
)


_CANCELLATIONS_SQL = (
    "SELECT cancel_id, shop_id, order_id, "
    "       cancel_status, cancel_reason, cancel_reason_text, cancel_type, "
    "       role, should_replenish_stock, "
    "       create_time, update_time, raw "
    "FROM public.cancellations"
)


def _iter_returns(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_RETURNS_SQL).mappings():
            yield dict(row)


def _iter_cancellations(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_CANCELLATIONS_SQL).mappings():
            yield dict(row)


# ─── target helpers ──────────────────────────────────────────────────


def _channel_account_id(target: Engine, external_account_id: str) -> int | None:
    sql = (
        "SELECT id FROM commerce.channel_accounts "
        "WHERE platform='tiktok' AND external_account_id = %(ext)s"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"ext": external_account_id},
        ).first()
    return int(row[0]) if row else None


def _sales_order_id(target: Engine, external_order_id: str) -> int | None:
    sql = (
        "SELECT id FROM commerce.sales_orders "
        "WHERE external_order_id = %(ext)s LIMIT 1"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"ext": external_order_id},
        ).first()
    return int(row[0]) if row else None


def _sales_order_line_id(
    target: Engine, sales_order_id: int, external_line_id: str,
) -> int | None:
    sql = (
        "SELECT id FROM commerce.sales_order_lines "
        "WHERE sales_order_id = %(soid)s AND external_line_id = %(ext)s"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"soid": sales_order_id, "ext": external_line_id},
        ).first()
    return int(row[0]) if row else None


def _extract_line_items(raw: Any, kind: str) -> list[dict]:
    """Extract return_line_items or cancel_line_items from a raw jsonb blob.

    ``kind`` ∈ {``return_line_items``, ``cancel_line_items``}.
    """
    if not isinstance(raw, dict):
        return []
    items = raw.get(kind)
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


# ─── upserts ──────────────────────────────────────────────────────────


_UPSERT_CASE = (
    "INSERT INTO after_sales.cases "
    "    (channel_account_id, sales_order_id, external_case_id, "
    "     case_type, status, reason_code, reason_text, "
    "     created_at_source, updated_at_source, synced_at) "
    "VALUES "
    "    (%(channel_account_id)s, %(sales_order_id)s, "
    "     %(external_case_id)s, %(case_type)s, %(status)s, "
    "     %(reason_code)s, %(reason_text)s, "
    "     %(created_at_source)s, %(updated_at_source)s, now()) "
    "ON CONFLICT (channel_account_id, external_case_id) DO UPDATE SET "
    "    sales_order_id     = EXCLUDED.sales_order_id, "
    "    case_type          = EXCLUDED.case_type, "
    "    status             = EXCLUDED.status, "
    "    reason_code        = EXCLUDED.reason_code, "
    "    reason_text        = EXCLUDED.reason_text, "
    "    created_at_source  = EXCLUDED.created_at_source, "
    "    updated_at_source  = EXCLUDED.updated_at_source, "
    "    synced_at          = now() "
    "RETURNING id"
)


_UPSERT_CASE_LINE = (
    "INSERT INTO after_sales.case_lines "
    "    (case_id, sales_order_line_id, external_case_line_id, "
    "     quantity, refund_amount, currency, should_replenish_stock) "
    "VALUES "
    "    (%(case_id)s, %(sales_order_line_id)s, %(external_case_line_id)s, "
    "     %(quantity)s, %(refund_amount)s, %(currency)s, "
    "     %(should_replenish_stock)s) "
    "ON CONFLICT (case_id, external_case_line_id) DO UPDATE SET "
    "    sales_order_line_id    = EXCLUDED.sales_order_line_id, "
    "    quantity               = EXCLUDED.quantity, "
    "    refund_amount          = EXCLUDED.refund_amount, "
    "    currency               = EXCLUDED.currency, "
    "    should_replenish_stock = EXCLUDED.should_replenish_stock "
    "RETURNING id"
)


# ─── main pipeline ──────────────────────────────────────────────────


def _process_one(
    *,
    target: Engine,
    external_case_id: str,
    shop_id: str | None,
    external_order_id: str | None,
    case_type: str,
    status: str | None,
    reason_code: str | None,
    reason_text: str | None,
    should_replenish: bool | None,
    create_time,
    update_time,
    raw: Any,
    line_items_key: str,
    external_line_item_key: str,
    stats: MigrationStats,
    sink: DryRunSink,
    dry_run: bool,
) -> int | None:
    """Upsert one case (return or cancellation) + its case_lines.

    Returns the DB id of the upserted case, or 0 in dry-run.
    """
    account_id_cache: dict[str, int | None] = {}
    sales_order_id_cache: dict[str, int | None] = {}

    # Resolve channel_account_id. None key skips the lookup.
    if shop_id is None:
        acct = None
    elif shop_id in account_id_cache:
        acct = account_id_cache[shop_id]
    else:
        acct = _channel_account_id(target, shop_id)
        account_id_cache[shop_id] = acct
    if acct is None:
        stats.cases_fk_missing += 1
        sink.record("after_sales.cases(SKIPPED)", 1)
        return None

    # Resolve sales_order_id. None key skips the lookup.
    if external_order_id is None:
        so_id = None
    elif external_order_id in sales_order_id_cache:
        so_id = sales_order_id_cache[external_order_id]
    else:
        so_id = _sales_order_id(target, external_order_id)
        sales_order_id_cache[external_order_id] = so_id
    if so_id is None:
        stats.cases_fk_missing += 1
        sink.record("after_sales.cases(SKIPPED)", 1)
        return None

    case_params = {
        "channel_account_id": acct,
        "sales_order_id": so_id,
        "external_case_id": external_case_id,
        "case_type": case_type,
        "status": status,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "created_at_source": epoch_seconds_to_utc(create_time),
        "updated_at_source": epoch_seconds_to_utc(update_time),
    }

    case_id: int
    if dry_run:
        case_id = 0
        stats.cases_upserted += 1
        sink.record("after_sales.cases", 1)
    else:
        with target.connect() as conn, conn.begin():
            row = conn.exec_driver_sql(_UPSERT_CASE, case_params).first()
        if not row:
            return None
        case_id = int(row[0])
        stats.cases_upserted += 1
        sink.record("after_sales.cases", 1)

    # Process line items.
    line_items = _extract_line_items(raw, line_items_key)
    if not line_items:
        return case_id

    for line in line_items:
        ext_order_line_id = line.get("order_line_item_id")
        ext_case_line_id = line.get(external_line_item_key)
        if not ext_case_line_id:
            # Synthesize a fallback key from order_line_item_id so the
            # unique constraint doesn't collide on repeated blanks.
            ext_case_line_id = (
                f"synthetic:{external_case_id}:{ext_order_line_id}"
            )
        line_id = (_sales_order_line_id(target, so_id, ext_order_line_id)
                   if ext_order_line_id else None)
        if line_id is None:
            stats.case_lines_fk_missing += 1
            sink.record("after_sales.case_lines(SKIPPED)", 1)
            continue

        refund_amount = None
        currency: str | None = None
        if isinstance(line.get("refund_amount"), dict):
            refund_amount = line["refund_amount"].get("refund_total")
            currency = line["refund_amount"].get("currency")

        line_params = {
            "case_id": case_id,
            "sales_order_line_id": line_id,
            "external_case_line_id": ext_case_line_id,
            "quantity": 1,  # source doesn't expose a quantity per line; 1.
            "refund_amount": refund_amount,
            "currency": currency,
            "should_replenish_stock": should_replenish,
        }
        if dry_run:
            stats.case_lines_upserted += 1
            sink.record("after_sales.case_lines", 1)
            continue
        with target.connect() as conn, conn.begin():
            conn.exec_driver_sql(_UPSERT_CASE_LINE, line_params)
        stats.case_lines_upserted += 1
        sink.record("after_sales.case_lines", 1)
    return case_id


def run(dry_run: bool = False, batch_size: int = 500,
        verbose: bool = True) -> MigrationStats:
    # 2026-08-30 incident guard: refuse to write to prod unless the
    # kill-switch is set. dry_run=True skips the check.
    require_prod_guard(dry_run, action="migrate_after_sales.run()")
    _ = batch_size
    stats = MigrationStats()
    sink = DryRunSink()
    source = get_source_engine()
    target = get_target_engine()

    # ── Pass 1: returns → cases + case_lines ───────────────────────
    for ret in _iter_returns(source):
        stats.returns_seen += 1
        raw = ret.get("raw") or {}
        _process_one(
            target=target,
            external_case_id=ret["return_id"],
            shop_id=ret.get("shop_id"),
            external_order_id=ret.get("order_id"),
            case_type=_return_type_to_case_type(ret.get("return_type")),
            status=ret.get("return_status"),
            reason_code=ret.get("return_reason"),
            reason_text=raw.get("return_reason_text")
                if isinstance(raw, dict) else None,
            should_replenish=None,
            create_time=ret.get("create_time"),
            update_time=ret.get("update_time"),
            raw=raw,
            line_items_key="return_line_items",
            external_line_item_key="return_line_item_id",
            stats=stats,
            sink=sink,
            dry_run=dry_run,
        )

    # ── Pass 2: cancellations → cases + case_lines ────────────────
    for cnc in _iter_cancellations(source):
        stats.cancellations_seen += 1
        raw = cnc.get("raw") or {}
        _process_one(
            target=target,
            external_case_id=cnc["cancel_id"],
            shop_id=cnc.get("shop_id"),
            external_order_id=cnc.get("order_id"),
            case_type=_cancel_type_to_case_type(cnc.get("cancel_type")),
            status=cnc.get("cancel_status"),
            reason_code=cnc.get("cancel_reason"),
            reason_text=cnc.get("cancel_reason_text"),
            should_replenish=cnc.get("should_replenish_stock"),
            create_time=cnc.get("create_time"),
            update_time=cnc.get("update_time"),
            raw=raw,
            line_items_key="cancel_line_items",
            external_line_item_key="cancel_line_item_id",
            stats=stats,
            sink=sink,
            dry_run=dry_run,
        )

    if verbose:
        print(stats.report(dry_run=dry_run))
        if dry_run:
            print(sink.report())
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Migrate returns + cancellations → cases + case_lines."),
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Report the migration plan without writing.")
    p.add_argument("--batch-size", type=int, default=500,
                   help="Rows per upsert batch (default 500).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the final summary print.")
    return p.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    args = _parse_args()
    run(dry_run=args.dry_run, batch_size=args.batch_size,
        verbose=not args.quiet)
    sys.exit(0)
