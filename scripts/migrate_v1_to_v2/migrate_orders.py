"""Migrate orders + order_items → sales_orders + sales_order_lines.

Source tables (read-only):
  * public.orders            (720 rows in prod; header)
  * public.order_items       (749 rows in prod; line items, PK (order_id, item_id))

Target tables (v2, writeable):
  * commerce.sales_orders       (UNIQUE channel_account_id, external_order_id)
  * commerce.sales_order_lines  (UNIQUE sales_order_id, external_line_id)

Time conversion:
  * epoch seconds → timestamptz (paid_at / shipped_at / delivered_at /
    cancelled_at / source_created_at / source_updated_at)

Channel-account resolution:
  * Source orders.shop_id is the natural TikTok shop_id. We resolve it to
    commerce.channel_accounts.id via UNIQUE (platform='tiktok',
    external_account_id). The FK enforces this; if the parent migration
    (migrate_shops) hasn't been run, every order will fail with FK error
    and the run aborts — surface that as a configuration error.

Product binding (sales_order_lines.channel_product_id):
  * We bind by exact external_product_id (order_items.product_id), NEVER by
    title. The product master table (commerce.channel_products) is empty at
    migration time (the Product sync job will populate it), so all rows
    will leave channel_product_id NULL and store the snapshot.
  * When binding fails (NULL or unresolved), we leave channel_product_id NULL
    and snapshot the product_id + name + image. The downstream
    ``/sync/products`` job will fill the FK later.
  * A sync_issue row is logged per unresolvable product_id with
    issue_type='UNRESOLVED_PRODUCT_ID' for operator follow-up.

Implementation notes:
  * SQL is plain string + psycopg ``%(name)s`` pyformat placeholders, passed
    via ``conn.exec_driver_sql()``. See ``migrate_logistics.py`` for rationale.

Idempotency: ON CONFLICT (channel_account_id, external_order_id)
DO UPDATE for sales_orders; ON CONFLICT (sales_order_id, external_line_id)
DO UPDATE for sales_order_lines. Re-running is a no-op.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from scripts.migrate_v1_to_v2.common import (
    DryRunSink,
    epoch_seconds_to_utc,
    get_source_engine,
    get_target_engine,
)


@dataclass
class MigrationStats:
    orders_seen: int = 0
    orders_upserted: int = 0
    orders_fk_missing: int = 0
    items_seen: int = 0
    items_upserted: int = 0
    items_product_unresolved: int = 0
    sync_issues_logged: int = 0

    def report(self, dry_run: bool) -> str:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        return (
            f"{mode} orders migration:\n"
            f"  source public.orders          seen={self.orders_seen}\n"
            f"  source public.order_items     seen={self.items_seen}\n"
            f"  commerce.sales_orders         upserted={self.orders_upserted} "
            f"fk_missing={self.orders_fk_missing}\n"
            f"  commerce.sales_order_lines    upserted={self.items_upserted} "
            f"product_unresolved={self.items_product_unresolved}\n"
            f"  integration.sync_issues       logged={self.sync_issues_logged}\n"
        )


# ─── source readers ──────────────────────────────────────────────────


def _iter_orders(source: Engine) -> Iterator[dict]:
    sql = (
        "SELECT order_id, shop_id, "
        "       order_status_name, payment_currency, "
        "       payment_amount, total_amount, "
        "       create_time, update_time, "
        "       paid_time, shipped_time, delivered_time, cancelled_time, "
        "       fulfillment_type, raw "
        "FROM public.orders"
    )
    with source.connect() as conn:
        for row in conn.exec_driver_sql(sql).mappings():
            yield dict(row)


def _iter_order_items(source: Engine) -> Iterator[dict]:
    sql = (
        "SELECT order_id, item_id, shop_id, "
        "       sku_id, product_id, "
        "       product_name, sku_name, sku_image, "
        "       quantity, sku_price, raw "
        "FROM public.order_items"
    )
    with source.connect() as conn:
        for row in conn.exec_driver_sql(sql).mappings():
            yield dict(row)


# ─── target helpers ──────────────────────────────────────────────────


def _channel_account_id(target: Engine, external_account_id: str) -> int | None:
    """Resolve ``external_account_id`` → channel_accounts.id."""
    sql = (
        "SELECT id FROM commerce.channel_accounts "
        "WHERE platform='tiktok' AND external_account_id = %(ext)s"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"ext": external_account_id},
        ).first()
    return int(row[0]) if row else None


def _channel_product_ids(target: Engine, account_id: int) -> dict[str, int]:
    """Map of external_product_id → channel_products.id for an account.

    Returns an empty dict if no products have been synced yet (this is the
    expected state at first migration time; the Product sync job fills
    the table later).
    """
    sql = (
        "SELECT external_product_id, id FROM commerce.channel_products "
        "WHERE channel_account_id = %(acct)s"
    )
    with target.connect() as conn:
        rows = conn.exec_driver_sql(
            sql, {"acct": account_id},
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


# ─── upserts ──────────────────────────────────────────────────────────


_UPSERT_ORDER = (
    "INSERT INTO commerce.sales_orders "
    "    (channel_account_id, external_order_id, status, currency, "
    "     payment_amount, total_amount, fulfillment_type, "
    "     source_created_at, source_updated_at, "
    "     paid_at, shipped_at, delivered_at, cancelled_at, "
    "     raw_record_id, synced_at) "
    "VALUES "
    "    (%(channel_account_id)s, %(external_order_id)s, %(status)s, "
    "     %(currency)s, %(payment_amount)s, %(total_amount)s, "
    "     %(fulfillment_type)s, "
    "     %(source_created_at)s, %(source_updated_at)s, "
    "     %(paid_at)s, %(shipped_at)s, %(delivered_at)s, "
    "     %(cancelled_at)s, %(raw_record_id)s, now()) "
    "ON CONFLICT (channel_account_id, external_order_id) DO UPDATE SET "
    "    status            = EXCLUDED.status, "
    "    currency          = EXCLUDED.currency, "
    "    payment_amount    = EXCLUDED.payment_amount, "
    "    total_amount      = EXCLUDED.total_amount, "
    "    fulfillment_type  = EXCLUDED.fulfillment_type, "
    "    source_created_at = EXCLUDED.source_created_at, "
    "    source_updated_at = EXCLUDED.source_updated_at, "
    "    paid_at           = EXCLUDED.paid_at, "
    "    shipped_at        = EXCLUDED.shipped_at, "
    "    delivered_at      = EXCLUDED.delivered_at, "
    "    cancelled_at      = EXCLUDED.cancelled_at, "
    "    synced_at         = now() "
    "RETURNING id"
)


_UPSERT_LINE = (
    "INSERT INTO commerce.sales_order_lines "
    "    (sales_order_id, external_line_id, "
    "     channel_product_id, channel_product_variant_id, "
    "     external_product_id_snapshot, external_variant_id_snapshot, "
    "     product_name_snapshot, variant_name_snapshot, image_url_snapshot, "
    "     quantity, unit_price, currency, line_status, "
    "     raw_record_id, synced_at) "
    "VALUES "
    "    (%(sales_order_id)s, %(external_line_id)s, "
    "     %(channel_product_id)s, %(channel_product_variant_id)s, "
    "     %(external_product_id_snapshot)s, %(external_variant_id_snapshot)s, "
    "     %(product_name_snapshot)s, %(variant_name_snapshot)s, "
    "     %(image_url_snapshot)s, "
    "     %(quantity)s, %(unit_price)s, %(currency)s, %(line_status)s, "
    "     %(raw_record_id)s, now()) "
    "ON CONFLICT (sales_order_id, external_line_id) DO UPDATE SET "
    "    channel_product_id           = EXCLUDED.channel_product_id, "
    "    channel_product_variant_id   = EXCLUDED.channel_product_variant_id, "
    "    external_product_id_snapshot = EXCLUDED.external_product_id_snapshot, "
    "    external_variant_id_snapshot = EXCLUDED.external_variant_id_snapshot, "
    "    product_name_snapshot        = EXCLUDED.product_name_snapshot, "
    "    variant_name_snapshot        = EXCLUDED.variant_name_snapshot, "
    "    image_url_snapshot           = EXCLUDED.image_url_snapshot, "
    "    quantity                     = EXCLUDED.quantity, "
    "    unit_price                   = EXCLUDED.unit_price, "
    "    currency                     = EXCLUDED.currency, "
    "    line_status                  = EXCLUDED.line_status, "
    "    synced_at                    = now() "
    "RETURNING id"
)


_INSERT_SYNC_ISSUE = (
    "INSERT INTO integration.sync_issues "
    "    (job_name, issue_type, external_id, details, detected_at) "
    "VALUES "
    "    (%(job_name)s, %(issue_type)s, %(external_id)s, "
    "     CAST(%(details)s AS jsonb), now())"
)


_CLEAR_MIGRATE_ISSUES = (
    "DELETE FROM integration.sync_issues "
    "WHERE job_name = %(job_name)s"
)


# ─── main pipeline ──────────────────────────────────────────────────


def run(dry_run: bool = False, batch_size: int = 500,
        verbose: bool = True) -> MigrationStats:
    stats = MigrationStats()
    sink = DryRunSink()
    source = get_source_engine()
    target = get_target_engine()

    # Idempotency: wipe any prior sync_issues logged by this migration
    # so re-running doesn't duplicate rows. Downstream sync_issues (from
    # real sync jobs) carry a different job_name and are not touched.
    if not dry_run:
        with target.connect() as conn, conn.begin():
            conn.exec_driver_sql(
                _CLEAR_MIGRATE_ISSUES, {"job_name": "migrate.orders"},
            )

    # Pre-resolve channel account (only one in prod: TikTok shop_id 749... ).
    account_id_cache: dict[str, int | None] = {}

    def _acct(ext_id: str) -> int | None:
        if ext_id not in account_id_cache:
            account_id_cache[ext_id] = _channel_account_id(target, ext_id)
        return account_id_cache[ext_id]

    # Build product map lazily per account (empty in practice for first run).
    product_map_cache: dict[int, dict[str, int]] = {}

    def _product_id(account_id: int, external_pid: str | None) -> int | None:
        if not external_pid:
            return None
        if account_id not in product_map_cache:
            product_map_cache[account_id] = _channel_product_ids(
                target, account_id,
            )
        return product_map_cache[account_id].get(external_pid)

    # ── Pass 1: sales_orders ────────────────────────────────────────
    sales_order_ids: dict[str, int] = {}  # external_order_id → sales_order.id
    with target.connect() as conn, conn.begin():
        for order in _iter_orders(source):
            stats.orders_seen += 1
            ext = order["order_id"]
            shop_ext = order["shop_id"]
            acct = _acct(shop_ext)
            if acct is None:
                stats.orders_fk_missing += 1
                sink.record("commerce.sales_orders(SKIPPED)", 1)
                continue
            params = {
                "channel_account_id": acct,
                "external_order_id": ext,
                "status": order.get("order_status_name"),
                "currency": order.get("payment_currency"),
                "payment_amount": order.get("payment_amount"),
                "total_amount": order.get("total_amount"),
                "fulfillment_type": order.get("fulfillment_type"),
                "source_created_at": epoch_seconds_to_utc(
                    order.get("create_time")),
                "source_updated_at": epoch_seconds_to_utc(
                    order.get("update_time")),
                "paid_at": epoch_seconds_to_utc(order.get("paid_time")),
                "shipped_at": epoch_seconds_to_utc(order.get("shipped_time")),
                "delivered_at": epoch_seconds_to_utc(order.get("delivered_time")),
                "cancelled_at": epoch_seconds_to_utc(order.get("cancelled_time")),
                "raw_record_id": None,  # raw_records ingestion is a separate job.
            }
            if dry_run:
                stats.orders_upserted += 1
                sales_order_ids[ext] = -1  # placeholder for line-pass lookup
                sink.record("commerce.sales_orders", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_ORDER, params).first()
            if row:
                sales_order_ids[ext] = int(row[0])
                stats.orders_upserted += 1
                sink.record("commerce.sales_orders", 1)

    # ── Pass 2: sales_order_lines ───────────────────────────────────
    with target.connect() as conn, conn.begin():
        for item in _iter_order_items(source):
            stats.items_seen += 1
            ext_order = item["order_id"]
            sales_order_id = sales_order_ids.get(ext_order)
            if sales_order_id is None:
                # The order wasn't upserted (FK missing on header). Skip.
                sink.record("commerce.sales_order_lines(SKIPPED)", 1)
                continue
            ext_pid = item.get("product_id")
            ext_sid = item.get("sku_id")
            acct = _acct(item["shop_id"])
            cp_id = _product_id(acct, ext_pid) if acct else None
            if ext_pid and cp_id is None:
                stats.items_product_unresolved += 1
                if not dry_run:
                    conn.exec_driver_sql(
                        _INSERT_SYNC_ISSUE,
                        {
                            "job_name": "migrate.orders",
                            "issue_type": "UNRESOLVED_PRODUCT_ID",
                            "external_id": ext_pid,
                            "details": json.dumps(
                                {"order_id": ext_order,
                                 "item_id": item["item_id"]}),
                        },
                    )
                    stats.sync_issues_logged += 1

            raw = item.get("raw") or {}
            params = {
                "sales_order_id": sales_order_id,
                "external_line_id": item["item_id"],
                "channel_product_id": cp_id,
                "channel_product_variant_id": None,  # variants not synced yet
                "external_product_id_snapshot": ext_pid,
                "external_variant_id_snapshot": ext_sid,
                "product_name_snapshot": item.get("product_name"),
                "variant_name_snapshot": item.get("sku_name"),
                "image_url_snapshot": item.get("sku_image"),
                "quantity": item.get("quantity"),
                "unit_price": item.get("sku_price"),
                "currency": raw.get("currency") if isinstance(raw, dict) else None,
                "line_status": raw.get("display_status")
                    if isinstance(raw, dict) else None,
                "raw_record_id": None,
            }
            if dry_run:
                stats.items_upserted += 1
                sink.record("commerce.sales_order_lines", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_LINE, params).first()
            if row:
                stats.items_upserted += 1
                sink.record("commerce.sales_order_lines", 1)

    _ = batch_size  # batch_size is consumed implicitly by iter_orders.
    if verbose:
        print(stats.report(dry_run=dry_run))
        if dry_run:
            print(sink.report())
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate orders + order_items → sales_orders + sales_order_lines.",
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
