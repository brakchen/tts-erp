"""tiktok.finance — payouts, settlement statements, statement transactions.

Three sub-jobs under one umbrella:

1. **payouts**        → ``finance.payouts``
2. **statements**     → ``finance.settlement_statements`` (per payout)
3. **transactions**   → ``finance.settlement_transactions`` + ``settlement_components``

Incremental cursor is epoch ms (per scope=shop_id). Each sub-step uses
its own endpoint but shares the watermark for simplicity — finance data
is low-volume and refresh-on-tick is fine.

The 58 ``_COMPONENT_COLUMNS`` allowlist mirrors the legacy migration
script's; we only write a ``settlement_components`` row when the source
amount is non-zero (matches the v3 model rule that we never store 0
amounts — they bloat the table 17x).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    Payout,
    RawRecord,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
    SyncIssue,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

JOB_NAME = "tiktok.finance"
# TikTok 202309 finance endpoints (verified live 2026-08-30):
#   GET /finance/202309/payments                     → data.payments
#   GET /finance/202309/statements                   → data.statements
#   GET /finance/202309/statements/{id}/statement_transactions
#                                                    → data.statement_transactions
# The old v2 paths (/payments/list, /settlements/search) 404.
PAYOUTS_ENDPOINT = "/finance/202309/payments"
STATEMENTS_ENDPOINT = "/finance/202309/statements"
STATEMENT_TRANSACTIONS_TEMPLATE = (
    "/finance/202309/statements/{statement_id}/statement_transactions"
)

#: sort_field per endpoint (required — TikTok returns 36009004 otherwise).
_SORT_FIELDS = {
    PAYOUTS_ENDPOINT: "create_time",
    STATEMENTS_ENDPOINT: "statement_time",
}
_TXN_SORT_FIELD = "order_create_time"  # only allowed value for txns
ProxyCall = Callable[..., dict]


# The 58 component columns observed in legacy public.statement_transactions
# (kept in sync with scripts/migrate_v1_to_v2/migrate_finance.py). New
# columns require updating both files.
_COMPONENT_COLUMNS: tuple[str, ...] = (
    "fee", "refund", "settlement_amount", "shipping_fee",
    "transaction_fee", "adjustment_amount", "seller_credit",
    "platform_credit", "settlement_fee", "marketing_promotion_fee",
    "live_gift_fee", "affiliate_commission", "affiliate_commission_fee",
    "platform_commission", "referral_fee", "tax", "import_tax",
    "vat", "duty", "deposit", "deposit_release", "deposit_freeze",
    "deposit_deduct", "reverse_logistics_fee", "logistics_adjustment",
    "return_shipping_fee", "buyer_payment", "buyer_refund",
    "buyer_partial_refund", "buyer_recharge", "buyer_voucher",
    "seller_recharge", "seller_voucher", "co_funding_1",
    "co_funding_2", "co_funding_3", "co_funding_4", "co_funding_5",
    "small_order_compensation", "delivery_failed_compensation",
    "lost_compensation", "damaged_compensation", "counterfeit_compensation",
    "late_delivery_compensation", "shipping_subsidy",
    "return_shipping_subsidy", "platform_subsidy",
    "flash_sale_subsidy", "freeship_subsidy", "live_special_subsidy",
    "influencer_subsidy", "mall_subsidy", "cpa_commission",
    "cpa_commission_fee", "mall_other_fee",
)


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_seconds_to_utc(seconds: int | None):
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(_safe_int(seconds), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce to int without raising; ``None``/garbage → ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _walk_pages(
    proxy_call,
    *,
    endpoint: str,
    base_body: dict,
    items_key: str,
    method: str = "GET",
):
    """Walk every page of a finance endpoint.

    TikTok 202309 finance endpoints are GETs returning a top-level list
    under ``data.<items_key>`` plus ``data.next_page_token``. sort_field
    is required per endpoint (36009004 otherwise); we inject the default.
    """
    collected: list[dict] = []
    next_token: str | None = None
    body = dict(base_body)
    if "sort_field" not in body:
        if "statement_transactions" in endpoint:
            body["sort_field"] = _TXN_SORT_FIELD
        elif endpoint in _SORT_FIELDS:
            body["sort_field"] = _SORT_FIELDS[endpoint]
    while True:
        page_body = dict(body)
        if next_token:
            page_body["page_token"] = next_token
        resp = proxy_call(method, endpoint, body=page_body)
        code = resp.get("code", -1)
        if code != 0:
            raise UpstreamJobError(
                f"{endpoint} non-zero code={code} message={resp.get('message')!r}"
            )
        data = resp.get("data") or {}
        collected.extend(data.get(items_key) or [])
        next_token = data.get("next_page_token") or None
        if not next_token:
            break
    return collected


def _parse_payout(raw: dict) -> dict:
    pid = raw.get("payment_id") or raw.get("id")
    if not pid:
        raise ParseError("payment_id missing")
    return {
        "external_payout_id": str(pid),
        "status": raw.get("payment_status") or raw.get("status"),
        "currency": raw.get("currency"),
        "amount": _to_decimal(raw.get("amount")),
        "source_created_at": _epoch_seconds_to_utc(raw.get("create_time")),
        "source_updated_at": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _parse_statement(raw: dict, payout_id: int) -> dict:
    sid = raw.get("statement_id") or raw.get("id")
    if not sid:
        raise ParseError("statement_id missing")
    period = raw.get("period") or {}
    return {
        "payout_id": payout_id,
        "external_statement_id": str(sid),
        "statement_time": _epoch_seconds_to_utc(raw.get("statement_time")),
        "period_start": period.get("start_date"),
        "period_end": period.get("end_date"),
        "currency": raw.get("currency"),
    }


def _parse_transaction(raw: dict) -> dict:
    tid = raw.get("transaction_id") or raw.get("id")
    if not tid:
        raise ParseError("transaction_id missing")
    return {
        "external_transaction_id": str(tid),
        "transaction_time": _epoch_seconds_to_utc(raw.get("transaction_time")),
    }


def _upsert_payout(session, *, account_id: int, fields: dict, raw_record_id: int) -> int:
    insert_values = {"channel_account_id": account_id, **fields, "raw_record_id": raw_record_id}
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(Payout).values(**insert_values).on_conflict_do_update(
            index_elements=["channel_account_id", "external_payout_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(Payout).where(
            Payout.channel_account_id == account_id,
            Payout.external_payout_id == fields["external_payout_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_statement(session, *, fields: dict, raw_record_id: int) -> int:
    insert_values = {**fields, "raw_record_id": raw_record_id}
    update_cols = {k: insert_values[k] for k in fields if k != "payout_id"}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(SettlementStatement).values(**insert_values).on_conflict_do_update(
            index_elements=["payout_id", "external_statement_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(SettlementStatement).where(
            SettlementStatement.payout_id == fields["payout_id"],
            SettlementStatement.external_statement_id == fields["external_statement_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_transaction(session, *, stmt_id: int, fields: dict, raw_record_id: int) -> int:
    insert_values = {
        "settlement_statement_id": stmt_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(SettlementTransaction).values(**insert_values).on_conflict_do_update(
            index_elements=["settlement_statement_id", "external_transaction_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(SettlementTransaction).where(
            SettlementTransaction.settlement_statement_id == stmt_id,
            SettlementTransaction.external_transaction_id == fields["external_transaction_id"],
        )
    ).scalar_one()
    return row.id


def _write_components(
    session, *, transaction_id: int, raw: dict, default_currency: str | None = None
) -> int:
    """Write non-zero settlement_components rows. Returns count written."""
    written = 0
    for col in _COMPONENT_COLUMNS:
        amount = _to_decimal(raw.get(col))
        if amount is None or amount == 0:
            continue
        currency = (
            raw.get(f"{col}_currency") or raw.get("currency") or default_currency
        )
        if not currency:
            # Without a currency we can't write a valid component row
            # (NOT NULL constraint). Surface as a sync_issue rather than
            # crashing the page.
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="MISSING_CURRENCY",
                    external_id=col,
                    details={"component": col, "transaction_id": transaction_id},
                )
            )
            continue
        session.execute(
            pg_insert(SettlementComponent)
            .values(
                transaction_id=transaction_id,
                component_code=col,
                amount=amount,
                currency=currency,
            )
            .on_conflict_do_update(
                index_elements=["transaction_id", "component_code"],
                set_={"amount": amount, "currency": currency},
            )
        )
        written += 1
    return written


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 50,
    scope: str | None = None,
) -> JobResult:
    from tts_erp_v2.sync_worker import watermarks

    cursor_scope = scope or shop_id
    account = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.external_account_id == shop_id,
        )
    ).scalar_one_or_none()
    if account is None:
        raise UpstreamJobError(
            f"channel_accounts row missing for tiktok shop_id={shop_id!r}"
        )

    watermark_ms = watermarks.get_cursor(
        session, job_name=JOB_NAME, scope=cursor_scope
    )
    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = _safe_int(watermark_ms) // 1000

    raw_payouts = _walk_pages(
        proxy_call,
        endpoint=PAYOUTS_ENDPOINT,
        base_body=base_body,
        items_key="payments",
    )

    total = 0
    inserted = 0
    failed = 0
    components_written = 0
    max_update_ms: int | None = None

    for raw_p in raw_payouts:
        total += 1
        ext_payout_id = str(
            raw_p.get("payment_id") or raw_p.get("id") or "<unknown>"
        )
        try:
            p_fields = _parse_payout(raw_p)
        except ParseError as exc:
            failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="PARSE_ERROR",
                    external_id=ext_payout_id,
                    details={"error": str(exc), "section": "payouts"},
                )
            )
            continue

        raw_row = RawRecord(
            endpoint=PAYOUTS_ENDPOINT,
            external_id=p_fields["external_payout_id"],
            payload=raw_p,
        )
        session.add(raw_row)
        session.flush()
        payout_id = _upsert_payout(
            session,
            account_id=account.id,
            fields=p_fields,
            raw_record_id=raw_row.id,
        )

        # Statements (per payout) — best-effort; some payouts may not
        # have statements yet.
        try:
            raw_statements = _walk_pages(
                proxy_call,
                endpoint=STATEMENTS_ENDPOINT,
                base_body={**base_body, "payment_id": p_fields["external_payout_id"]},
                items_key="statements",
            )
        except UpstreamJobError:
            raw_statements = []

        for raw_s in raw_statements:
            ext_stmt_id = str(
                raw_s.get("statement_id") or raw_s.get("id") or "<unknown>"
            )
            try:
                s_fields = _parse_statement(raw_s, payout_id=payout_id)
            except ParseError as exc:
                failed += 1
                session.add(
                    SyncIssue(
                        job_name=JOB_NAME,
                        issue_type="PARSE_ERROR",
                        external_id=ext_stmt_id,
                        details={"error": str(exc), "section": "statements"},
                    )
                )
                continue

            raw_s_row = RawRecord(
                endpoint=STATEMENTS_ENDPOINT,
                external_id=s_fields["external_statement_id"],
                payload=raw_s,
            )
            session.add(raw_s_row)
            session.flush()
            stmt_id = _upsert_statement(
                session,
                fields=s_fields,
                raw_record_id=raw_s_row.id,
            )

            # Transactions per statement
            try:
                raw_txns = _walk_pages(
                    proxy_call,
                    endpoint=STATEMENT_TRANSACTIONS_TEMPLATE.format(
                        statement_id=s_fields["external_statement_id"]
                    ),
                    base_body={"page_size": page_size},
                    items_key="statement_transactions",
                )
            except UpstreamJobError:
                raw_txns = []

            for raw_t in raw_txns:
                ext_txn_id = str(
                    raw_t.get("transaction_id") or raw_t.get("id") or "<unknown>"
                )
                try:
                    t_fields = _parse_transaction(raw_t)
                except ParseError as exc:
                    failed += 1
                    session.add(
                        SyncIssue(
                            job_name=JOB_NAME,
                            issue_type="PARSE_ERROR",
                            external_id=ext_txn_id,
                            details={"error": str(exc), "section": "transactions"},
                        )
                    )
                    continue

                raw_t_row = RawRecord(
                    endpoint=STATEMENT_TRANSACTIONS_TEMPLATE.format(
                        statement_id=s_fields["external_statement_id"]
                    ),
                    external_id=t_fields["external_transaction_id"],
                    payload=raw_t,
                )
                session.add(raw_t_row)
                session.flush()
                txn_id = _upsert_transaction(
                    session,
                    stmt_id=stmt_id,
                    fields=t_fields,
                    raw_record_id=raw_t_row.id,
                )
                components_written += _write_components(
                    session,
                    transaction_id=txn_id,
                    raw=raw_t,
                    default_currency=s_fields.get("currency"),
                )

        inserted += 1
        update_ms = (
            p_fields.get("source_updated_at")
            and _safe_int(p_fields["source_updated_at"].timestamp() * 1000)
        )
        if update_ms and (max_update_ms is None or update_ms > max_update_ms):
            max_update_ms = update_ms

    new_cursor_ms: int | None = None
    if max_update_ms is not None and (
        watermark_ms is None
        or max_update_ms > _safe_int(watermark_ms)
    ):
        watermarks.set_cursor(
            session,
            job_name=JOB_NAME,
            scope=cursor_scope,
            cursor_epoch_ms=max_update_ms,
        )
        new_cursor_ms = max_update_ms

    return JobResult(
        rows_total=total,
        rows_inserted=inserted,
        rows_failed=failed,
        cursor=new_cursor_ms,
    )


__all__ = [
    "run",
    "JOB_NAME",
    "PAYOUTS_ENDPOINT",
    "STATEMENTS_ENDPOINT",
    "STATEMENT_TRANSACTIONS_TEMPLATE",
    "_COMPONENT_COLUMNS",
    "ProxyCall",
    "UpstreamJobError",
    "ParseError",
]
