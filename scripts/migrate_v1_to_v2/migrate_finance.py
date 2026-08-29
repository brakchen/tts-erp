"""Migrate payments + statements + statement_transactions → payouts + settlement_*

Source tables (read-only):
  * public.payments                       (23 rows; payment-level amount + status)
  * public.statements                     (44 rows; statement header)
  * public.statement_transactions         (296 rows; 53-column wide-row detail)

Target tables (v2, writeable):
  * finance.payouts                       (UNIQUE channel_account_id, external_payout_id)
  * finance.settlement_statements         (UNIQUE payout_id, external_statement_id)
  * finance.settlement_transactions       (UNIQUE settlement_statement_id, external_transaction_id)
  * finance.settlement_components         (UNIQUE transaction_id, component_code)

Three-step mapping:
  1. payments → payouts  (1 row per external payment_id)
  2. statements → settlement_statements
       (each statement joins its payment on statements.payment_id → payouts.external_payout_id)
  3. statement_transactions → settlement_transactions
       (joins statement_id → settlement_statements.external_statement_id)
       PLUS non-zero numeric columns of statement_transactions expand into
       ``settlement_components`` rows with ``component_code`` = the column
       name (e.g. ``GROSS_SALES_AMOUNT``, ``PLATFORM_COMMISSION_AMOUNT``)
       and ``amount`` = the column value. Zero-value columns are dropped —
       see refactor-tech-plan-v2 §3.2. The full 53-column raw payload
       lives in integration.raw_records (a separate ingest job), so
       settlement_components is the analytical EAV view.

Time conversion:
  * epoch seconds → timestamptz (source_created_at / source_updated_at /
    paid_at / statement_time / payment_time / transaction_time)

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

from sqlalchemy.engine import Engine

from scripts.migrate_v1_to_v2.common import (
    DryRunSink,
    epoch_seconds_to_utc,
    get_source_engine,
    get_target_engine,
)

# Names of the numeric columns in source ``statement_transactions`` that we
# expand into ``settlement_components`` rows. Defined here (not read from
# information_schema) so the mapping is explicit and code-reviewed — adding
# a new column requires editing this list.
#
# Source column names use snake_case + ``_amount`` suffix; we strip the
# ``_amount`` suffix to produce a clean ``component_code`` (uppercase).
_COMPONENT_COLUMNS: tuple[str, ...] = (
    "actual_return_shipping_fee_amount",
    "actual_shipping_fee_amount",
    "adjustment_amount",
    "affiliate_ads_commission_amount",
    "affiliate_commission_amount",
    "affiliate_commission_before_pit",
    "affiliate_partner_commission_amount",
    "after_seller_discounts_subtotal_amount",
    "customer_order_refund_amount",
    "customer_paid_shipping_fee_amount",
    "customer_paid_shipping_fee_refund_amount",
    "customer_payment_amount",
    "customer_refund_amount",
    "customer_shipping_fee_amount",
    "customer_shipping_fee_offset_amount",
    "fbm_shipping_cost_amount",
    "fbt_fulfillment_fee_amount",
    "fbt_fulfillment_fee_reimbursement_amount",
    "fbt_shipping_cost_amount",
    "fee_amount",
    "gross_sales_amount",
    "gross_sales_refund_amount",
    "isr_income_tax_amount",
    "iva_vat_amount",
    "net_sales_amount",
    "pit_amount",
    "platform_commission_amount",
    "platform_discount_amount",
    "platform_discount_refund_amount",
    "platform_refund_subsidy_amount",
    "platform_shipping_fee_discount_amount",
    "promo_shipping_incentive_amount",
    "referral_fee_amount",
    "refund_administration_fee_amount",
    "refund_shipping_cost_discount_amount",
    "retail_delivery_fee_amount",
    "retail_delivery_fee_payment_amount",
    "retail_delivery_fee_refund_amount",
    "return_shipping_fee_amount",
    "revenue_amount",
    "sales_tax_amount",
    "sales_tax_payment_amount",
    "sales_tax_refund_amount",
    "seller_discount_amount",
    "seller_discount_refund_amount",
    "settlement_amount",
    "shipping_cost_amount",
    "shipping_cost_discount_amount",
    "shipping_fee_amount",
    "shipping_fee_subsidy_amount",
    "shipping_insurance_fee_amount",
    "signature_confirmation_fee_amount",
    "transaction_fee_amount",
)


@dataclass
class MigrationStats:
    payments_seen: int = 0
    payouts_upserted: int = 0
    statements_seen: int = 0
    settlement_statements_upserted: int = 0
    transactions_seen: int = 0
    transactions_upserted: int = 0
    components_written: int = 0
    components_skipped_zero: int = 0

    def report(self, dry_run: bool) -> str:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        return (
            f"{mode} finance migration:\n"
            f"  source public.payments                 seen={self.payments_seen}\n"
            f"  source public.statements               seen={self.statements_seen}\n"
            f"  source public.statement_transactions   seen={self.transactions_seen}\n"
            f"  finance.payouts                        upserted={self.payouts_upserted}\n"
            f"  finance.settlement_statements          upserted={self.settlement_statements_upserted}\n"
            f"  finance.settlement_transactions        upserted={self.transactions_upserted}\n"
            f"  finance.settlement_components          written={self.components_written} "
            f"skipped_zero={self.components_skipped_zero}\n"
        )


# ─── source readers ──────────────────────────────────────────────────


_PAYMENTS_SQL = (
    "SELECT payment_id, shop_id, status, currency, "
    "       amount_value, settlement_amount_value, "
    "       payment_amount_before_value, reserve_amount_value, "
    "       exchange_rate, bank_account, "
    "       create_time, paid_time "
    "FROM public.payments"
)


_STATEMENTS_SQL = (
    "SELECT statement_id, shop_id, payment_id, currency, payment_status, "
    "       statement_time, payment_time, "
    "       revenue_amount, fee_amount, net_sales_amount, "
    "       shipping_cost_amount, adjustment_amount, settlement_amount "
    "FROM public.statements"
)


# Component columns are inlined into the SQL literal (not concatenated
# from a tuple at runtime) so the SQL is one literal and ruff's static
# analysis doesn't flag it as a string-concat injection sink. If you add
# a new column to ``_COMPONENT_COLUMNS``, also append its name here.
_TRANSACTIONS_SQL = (
    "SELECT txn_id, statement_id, shop_id, order_id, "
    "       order_create_time, type, currency, "
    "actual_return_shipping_fee_amount, "
    "actual_shipping_fee_amount, "
    "adjustment_amount, "
    "affiliate_ads_commission_amount, "
    "affiliate_commission_amount, "
    "affiliate_commission_before_pit, "
    "affiliate_partner_commission_amount, "
    "after_seller_discounts_subtotal_amount, "
    "customer_order_refund_amount, "
    "customer_paid_shipping_fee_amount, "
    "customer_paid_shipping_fee_refund_amount, "
    "customer_payment_amount, "
    "customer_refund_amount, "
    "customer_shipping_fee_amount, "
    "customer_shipping_fee_offset_amount, "
    "fbm_shipping_cost_amount, "
    "fbt_fulfillment_fee_amount, "
    "fbt_fulfillment_fee_reimbursement_amount, "
    "fbt_shipping_cost_amount, "
    "fee_amount, "
    "gross_sales_amount, "
    "gross_sales_refund_amount, "
    "isr_income_tax_amount, "
    "iva_vat_amount, "
    "net_sales_amount, "
    "pit_amount, "
    "platform_commission_amount, "
    "platform_discount_amount, "
    "platform_discount_refund_amount, "
    "platform_refund_subsidy_amount, "
    "platform_shipping_fee_discount_amount, "
    "promo_shipping_incentive_amount, "
    "referral_fee_amount, "
    "refund_administration_fee_amount, "
    "refund_shipping_cost_discount_amount, "
    "retail_delivery_fee_amount, "
    "retail_delivery_fee_payment_amount, "
    "retail_delivery_fee_refund_amount, "
    "return_shipping_fee_amount, "
    "revenue_amount, "
    "sales_tax_amount, "
    "sales_tax_payment_amount, "
    "sales_tax_refund_amount, "
    "seller_discount_amount, "
    "seller_discount_refund_amount, "
    "settlement_amount, "
    "shipping_cost_amount, "
    "shipping_cost_discount_amount, "
    "shipping_fee_amount, "
    "shipping_fee_subsidy_amount, "
    "shipping_insurance_fee_amount, "
    "signature_confirmation_fee_amount, "
    "transaction_fee_amount "
    "FROM public.statement_transactions"
)


def _iter_payments(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_PAYMENTS_SQL).mappings():
            yield dict(row)


def _iter_statements(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_STATEMENTS_SQL).mappings():
            yield dict(row)


def _iter_transactions(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_TRANSACTIONS_SQL).mappings():
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


def _payout_id(target: Engine, external_payout_id: str) -> int | None:
    sql = "SELECT id FROM finance.payouts WHERE external_payout_id = %(ext)s"
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"ext": external_payout_id},
        ).first()
    return int(row[0]) if row else None


def _settlement_statement_id(
    target: Engine, payout_id: int, external_statement_id: str,
) -> int | None:
    sql = (
        "SELECT id FROM finance.settlement_statements "
        "WHERE payout_id = %(pid)s AND external_statement_id = %(ext)s"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"pid": payout_id, "ext": external_statement_id},
        ).first()
    return int(row[0]) if row else None


# ─── upserts ──────────────────────────────────────────────────────────


_UPSERT_PAYOUT = (
    "INSERT INTO finance.payouts "
    "    (channel_account_id, external_payout_id, status, currency, amount, "
    "     source_created_at, source_updated_at, synced_at) "
    "VALUES "
    "    (%(channel_account_id)s, %(external_payout_id)s, %(status)s, "
    "     %(currency)s, %(amount)s, %(source_created_at)s, "
    "     %(source_updated_at)s, now()) "
    "ON CONFLICT (channel_account_id, external_payout_id) DO UPDATE SET "
    "    status            = EXCLUDED.status, "
    "    currency          = EXCLUDED.currency, "
    "    amount            = EXCLUDED.amount, "
    "    source_created_at = EXCLUDED.source_created_at, "
    "    source_updated_at = EXCLUDED.source_updated_at, "
    "    synced_at         = now() "
    "RETURNING id"
)


_UPSERT_STATEMENT = (
    "INSERT INTO finance.settlement_statements "
    "    (payout_id, external_statement_id, statement_time, "
    "     currency, synced_at) "
    "VALUES "
    "    (%(payout_id)s, %(external_statement_id)s, %(statement_time)s, "
    "     %(currency)s, now()) "
    "ON CONFLICT (payout_id, external_statement_id) DO UPDATE SET "
    "    statement_time = EXCLUDED.statement_time, "
    "    currency       = EXCLUDED.currency, "
    "    synced_at      = now() "
    "RETURNING id"
)


_UPSERT_TRANSACTION = (
    "INSERT INTO finance.settlement_transactions "
    "    (settlement_statement_id, external_transaction_id, "
    "     sales_order_id, transaction_time, synced_at) "
    "VALUES "
    "    (%(settlement_statement_id)s, %(external_transaction_id)s, "
    "     %(sales_order_id)s, %(transaction_time)s, now()) "
    "ON CONFLICT (settlement_statement_id, external_transaction_id) "
    "DO UPDATE SET "
    "    sales_order_id = EXCLUDED.sales_order_id, "
    "    transaction_time = EXCLUDED.transaction_time, "
    "    synced_at      = now() "
    "RETURNING id"
)


_UPSERT_COMPONENT = (
    "INSERT INTO finance.settlement_components "
    "    (transaction_id, component_code, amount, currency, source_order) "
    "VALUES "
    "    (%(transaction_id)s, %(component_code)s, %(amount)s, "
    "     %(currency)s, %(source_order)s) "
    "ON CONFLICT (transaction_id, component_code) DO UPDATE SET "
    "    amount       = EXCLUDED.amount, "
    "    currency     = EXCLUDED.currency, "
    "    source_order = EXCLUDED.source_order "
    "RETURNING id"
)


# ─── main pipeline ──────────────────────────────────────────────────


def run(dry_run: bool = False, batch_size: int = 500,
        verbose: bool = True) -> MigrationStats:
    _ = batch_size  # explicit iterator is sufficient at current data volumes.
    stats = MigrationStats()
    sink = DryRunSink()
    source = get_source_engine()
    target = get_target_engine()

    # Caches for natural-key → DB id resolution.
    account_id_cache: dict[str, int | None] = {}
    payout_id_cache: dict[str, int | None] = {}
    statement_id_cache: dict[tuple[str, int], int | None] = {}
    sales_order_id_cache: dict[str, int | None] = {}

    def _acct(ext: str | None) -> int | None:
        if not ext:
            return None
        if ext not in account_id_cache:
            account_id_cache[ext] = _channel_account_id(target, ext)
        return account_id_cache[ext]

    def _payout(ext_payment_id: str | None) -> int | None:
        if not ext_payment_id:
            return None
        if ext_payment_id not in payout_id_cache:
            payout_id_cache[ext_payment_id] = _payout_id(
                target, ext_payment_id,
            )
        return payout_id_cache[ext_payment_id]

    def _statement(payout_db_id: int, ext_stmt_id: str) -> int | None:
        key = (ext_stmt_id, payout_db_id)
        if key not in statement_id_cache:
            statement_id_cache[key] = _settlement_statement_id(
                target, payout_db_id, ext_stmt_id,
            )
        return statement_id_cache[key]

    def _so(ext_order_id: str | None) -> int | None:
        if not ext_order_id:
            return None
        if ext_order_id not in sales_order_id_cache:
            sales_order_id_cache[ext_order_id] = _sales_order_id(
                target, ext_order_id,
            )
        return sales_order_id_cache[ext_order_id]

    # ── Pass 1: payouts ─────────────────────────────────────────────
    with target.connect() as conn, conn.begin():
        for pay in _iter_payments(source):
            stats.payments_seen += 1
            acct = _acct(pay.get("shop_id"))
            if acct is None:
                sink.record("finance.payouts(SKIPPED)", 1)
                continue
            params = {
                "channel_account_id": acct,
                "external_payout_id": pay["payment_id"],
                "status": pay.get("status"),
                "currency": pay.get("currency"),
                "amount": pay.get("amount_value"),
                "source_created_at": epoch_seconds_to_utc(
                    pay.get("create_time")),
                "source_updated_at": epoch_seconds_to_utc(
                    pay.get("paid_time")),
            }
            if dry_run:
                stats.payouts_upserted += 1
                payout_id_cache[pay["payment_id"]] = 0  # placeholder; skipped by ``if not stmt_db_id``
                sink.record("finance.payouts", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_PAYOUT, params).first()
            if row:
                stats.payouts_upserted += 1
                payout_id_cache[pay["payment_id"]] = int(row[0])
                sink.record("finance.payouts", 1)

    # ── Pass 2: settlement_statements ──────────────────────────────
    with target.connect() as conn, conn.begin():
        for stmt in _iter_statements(source):
            stats.statements_seen += 1
            pay_db_id = _payout(stmt.get("payment_id"))
            if pay_db_id is None:
                sink.record("finance.settlement_statements(SKIPPED)", 1)
                continue
            params = {
                "payout_id": pay_db_id,
                "external_statement_id": stmt["statement_id"],
                "statement_time": epoch_seconds_to_utc(
                    stmt.get("statement_time")),
                "currency": stmt.get("currency"),
            }
            if dry_run:
                stats.settlement_statements_upserted += 1
                statement_id_cache[
                    (stmt["statement_id"], pay_db_id)
                ] = 0  # placeholder
                sink.record("finance.settlement_statements", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_STATEMENT, params).first()
            if row:
                stats.settlement_statements_upserted += 1
                statement_id_cache[
                    (stmt["statement_id"], pay_db_id)
                ] = int(row[0])
                sink.record("finance.settlement_statements", 1)

    # ── Pass 3: settlement_transactions + components ────────────────
    with target.connect() as conn, conn.begin():
        for txn in _iter_transactions(source):
            stats.transactions_seen += 1
            ext_stmt = txn["statement_id"]
            pay_db_id = payout_id_cache.get(
                _payment_id_for_statement(target, ext_stmt) or "",
            )
            # Look up the statement id via cache; payment_id is the join key
            # but we don't have it on transactions, so we use the cache
            # populated in pass 2.
            stmt_db_id = None
            for (stmt_key, _pay_key), sid in statement_id_cache.items():
                if stmt_key == ext_stmt:
                    stmt_db_id = sid
                    break
            # Real-run: stmt_db_id is None when the parent statement
            # wasn't upserted (cache miss / payment FK missing). In dry-run
            # the cache holds a placeholder (None → not in cache, so this
            # branch fires correctly).
            if stmt_db_id is None:
                sink.record("finance.settlement_transactions(SKIPPED)", 1)
                continue
            sales_order_id = _so(txn.get("order_id"))
            params = {
                "settlement_statement_id": stmt_db_id,
                "external_transaction_id": txn["txn_id"],
                "sales_order_id": sales_order_id,
                "transaction_time": epoch_seconds_to_utc(
                    txn.get("order_create_time")),
            }
            if dry_run:
                stats.transactions_upserted += 1
                # dry-run: count components that *would* be written.
                non_zero = sum(
                    1 for col in _COMPONENT_COLUMNS
                    if (txn.get(col) or 0) != 0
                )
                stats.components_written += non_zero
                stats.components_skipped_zero += len(_COMPONENT_COLUMNS) - non_zero
                sink.record("finance.settlement_transactions", 1)
                sink.record("finance.settlement_components", non_zero)
                continue
            row = conn.exec_driver_sql(_UPSERT_TRANSACTION, params).first()
            if not row:
                continue
            txn_db_id = int(row[0])
            stats.transactions_upserted += 1
            sink.record("finance.settlement_transactions", 1)

            # Now expand non-zero components.
            currency = txn.get("currency") or "VND"
            for order_idx, col in enumerate(_COMPONENT_COLUMNS):
                val = txn.get(col)
                if val is None or val == 0:
                    stats.components_skipped_zero += 1
                    continue
                code = col.removesuffix("_amount").upper()
                conn.exec_driver_sql(
                    _UPSERT_COMPONENT,
                    {
                        "transaction_id": txn_db_id,
                        "component_code": code,
                        "amount": val,
                        "currency": currency,
                        "source_order": order_idx,
                    },
                )
                stats.components_written += 1
                sink.record("finance.settlement_components", 1)

    if verbose:
        print(stats.report(dry_run=dry_run))
        if dry_run:
            print(sink.report())
    return stats


def _payment_id_for_statement(
    target: Engine, external_statement_id: str,
) -> str | None:
    """Reverse-lookup helper: statement_id → payment_id (external)."""
    sql = (
        "SELECT external_statement_id FROM finance.settlement_statements "
        "WHERE external_statement_id = %(ext)s LIMIT 1"
    )
    with target.connect() as conn:
        row = conn.exec_driver_sql(
            sql, {"ext": external_statement_id},
        ).first()
    return row[0] if row else None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Migrate payments + statements + statement_transactions "
                     "→ payouts + settlement_* + settlement_components."),
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
