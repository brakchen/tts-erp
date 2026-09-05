"""tiktok.finance — payouts + statements (split, independent cursors).

Lane 2 refactor (2026-08-31). Two sub-jobs share one
``run(session, *, proxy_call, shop_id)`` entry point but own
independent watermarks:

1. **payouts**        → ``finance.payouts``                       cursor=``tiktok.finance.payouts``
2. **statements**     → ``finance.settlement_statements`` + transactions + components
                                                            cursor=``tiktok.finance.statements``

Why statements is no longer nested inside the payout loop
---------------------------------------------------------
Pre-refactor, the statements pull was driven by ``for payout in
raw_payouts: ... GET /finance/202309/statements?payment_id=PAYOUT_X``.
Two defects collided:

* The proxy_call adapter was dropping body keys for GETs (fixed by
  Lane 1), so ``payment_id`` never reached the query string and
  every call returned the full statements list.
* The outer ``for payout`` loop then upserted every returned statement
  under *that* payout's FK. With 24 payouts × 45 real statements the
  table grew to 1080 rows (24× replication). The transactions layer
  compounded this: 441 distinct transactions → 10 584 rows because
  every replicated statement pulled the same transaction set.

Post-refactor, statements is a top-level pull resolved by the upstream
``payment_id`` field on the statement payload. Each statement attaches
to exactly one payout; late statements whose owning payout no longer
updates still get picked up because the statements cursor advances
independently.

Failure semantics
-----------------
Upstream 5xx on the statements / transactions walks are no longer
swallowed (no ``except UpstreamJobError: raw_statements=[]``).
``run_with_sync_job`` will mark the run 'failed' and the watermark
stays put so the next tick re-fetches.

Statements whose upstream payload lacks ``payment_id`` (16 of 45 in
prod) are surfaced as ``STATEMENT_PAYMENT_ID_MISSING`` and skipped —
the cursor does NOT advance past them, so a later payload revision
or a back-fill can still pick them up.

The 53 ``_COMPONENT_COLUMNS`` source keys mirror the upstream 202309
``statement_transactions`` payload field names (same list as the archived
``migrate_finance.py``); each is expanded to a ``settlement_components`` row
with ``component_code`` = field name stripped of ``_amount`` and uppercased
(e.g. ``settlement_amount`` → ``SETTLEMENT``, ``gross_sales_amount`` →
``GROSS_SALES``) — the v3 convention ``db/models/finance.py`` documents.
We only write a row when the source amount is non-zero (v3 rule: never
store 0 amounts — they bloat the table 17x).

Audit 2026-09-06: the pre-audit allowlist used lowercase stems (``fee``,
``refund``, …) that never match upstream ``*_amount`` keys, so ONLY
``settlement_amount`` was ever written (and under the raw lowercase name,
not the documented uppercase code). The fee/gross/refund breakdown lived
only in raw payloads.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ChannelAccount,
    Payout,
    RawRecord,
    SalesOrder,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
    SyncIssue,
)
from tts_erp_v2.sync_worker.job_runner import JobResult

# Umbrella job_name: kept ONLY as the sync_jobs.job_name (so operators
# see one row per tick in the UI) and as the SyncIssue.job_name (so
# /v2/admin/sync-issues?job_name=tiktok.finance still works). It is NOT
# used as a sync_cursors.job_name — that lives under PAYOUTS_JOB_NAME /
# STATEMENTS_JOB_NAME.
JOB_NAME = "tiktok.finance"
PAYOUTS_JOB_NAME = "tiktok.finance.payouts"
STATEMENTS_JOB_NAME = "tiktok.finance.statements"

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
    # Upstream 202309 statement_transactions numeric fields, 1:1 with the
    # archived migrate_finance.py list. ``component_code`` is derived as
    # ``field.removesuffix("_amount").upper()`` (see _write_components).
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


class UpstreamJobError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _epoch_seconds_to_utc(seconds: int | None):
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(_safe_int(seconds), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def _money_of(
    value, *, fallback_currency: str | None = None
) -> tuple[Decimal | None, str | None]:
    """Coerce a TikTok 202309 money field to ``(amount, currency)``.

    Live 202309 ``/payments`` payloads carry amounts as NESTED objects
    ``{"value": "3928553", "currency": "VND"}`` — reading them as flat
    scalars silently drops the field (audit 2026-09-05: every
    ``finance.payouts`` row had amount=NULL currency=NULL). Older / legacy
    shapes (statements payloads, test fixtures) are flat scalars with a
    sibling top-level ``currency``. Handles both.
    """
    if isinstance(value, dict):
        return _to_decimal(value.get("value")), value.get(
            "currency"
        ) or fallback_currency
    return _to_decimal(value), fallback_currency


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

    Raises :class:`UpstreamJobError` on any non-zero ``code`` — callers
    must NOT swallow this. Per the Lane 2 contract, a failed pull MUST
    propagate to ``run_with_sync_job`` so the tick is marked 'failed'
    and the watermark stays put.
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
    # Live 202309 payments: ``amount`` == ``settlement_amount`` ==
    # ``payment_amount_before_exchange`` (exchange_rate="1" for VND
    # stores), all nested objects. Prefer the top-level ``amount`` and
    # fall back to ``settlement_amount`` for payloads that omit it.
    amount, currency = _money_of(
        raw.get("amount"), fallback_currency=raw.get("currency")
    )
    if amount is None:
        amount, currency = _money_of(
            raw.get("settlement_amount"), fallback_currency=raw.get("currency")
        )
    return {
        "external_payout_id": str(pid),
        "status": raw.get("payment_status") or raw.get("status"),
        "currency": currency,
        "amount": amount,
        "source_created_at": _epoch_seconds_to_utc(raw.get("create_time")),
        "source_updated_at": _epoch_seconds_to_utc(raw.get("update_time")),
    }


def _parse_statement(raw: dict, payout_id: int) -> dict:
    sid = raw.get("id") or raw.get("statement_id")
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
    # 202309 transaction payloads carry NO ``transaction_time``; the
    # authoritative timestamp is ``order_create_time`` (also the endpoint's
    # required sort_field). The archived migration mapped it the same way;
    # pre-audit the column stayed NULL for every row.
    return {
        "external_transaction_id": str(tid),
        "transaction_time": _epoch_seconds_to_utc(
            raw.get("transaction_time") or raw.get("order_create_time")
        ),
    }


def _upsert_payout(
    session, *, account_id: int, fields: dict, raw_record_id: int
) -> int:
    insert_values = {
        "shop_pk": account_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(Payout)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["shop_pk", "external_payout_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(Payout).where(
            Payout.shop_pk == account_id,
            Payout.external_payout_id == fields["external_payout_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_statement(session, *, fields: dict, raw_record_id: int) -> int:
    insert_values = {**fields, "raw_record_id": raw_record_id}
    update_cols = {k: insert_values[k] for k in fields if k != "payout_id"}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(SettlementStatement)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["payout_id", "external_statement_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(SettlementStatement).where(
            SettlementStatement.payout_id == fields["payout_id"],
            SettlementStatement.external_statement_id
            == fields["external_statement_id"],
        )
    ).scalar_one()
    return row.id


def _upsert_transaction(
    session, *, stmt_id: int, fields: dict, raw_record_id: int
) -> int:
    insert_values = {
        "settlement_statement_id": stmt_id,
        **fields,
        "raw_record_id": raw_record_id,
    }
    update_cols = {k: insert_values[k] for k in fields}
    update_cols["raw_record_id"] = raw_record_id
    session.execute(
        pg_insert(SettlementTransaction)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["settlement_statement_id", "external_transaction_id"],
            set_=update_cols,
        )
    )
    row = session.execute(
        select(SettlementTransaction).where(
            SettlementTransaction.settlement_statement_id == stmt_id,
            SettlementTransaction.external_transaction_id
            == fields["external_transaction_id"],
        )
    ).scalar_one()
    return row.id


def _write_components(
    session, *, transaction_id: int, raw: dict, default_currency: str | None = None
) -> int:
    """Write non-zero settlement_components rows. Returns count written.

    ``component_code`` = upstream field name stripped of the ``_amount``
    suffix and uppercased (``gross_sales_amount`` → ``GROSS_SALES``), the
    convention shared with the archived migrate_finance.py and with
    ``db/models/finance.py``. ``source_order`` keeps the upstream field
    index for traceability.
    """
    written = 0
    for source_order, col in enumerate(_COMPONENT_COLUMNS):
        amount = _to_decimal(raw.get(col))
        if amount is None or amount == 0:
            continue
        code = col.removesuffix("_amount").upper()
        currency = raw.get(f"{col}_currency") or raw.get("currency") or default_currency
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
                component_code=code,
                amount=amount,
                currency=currency,
                source_order=source_order,
            )
            .on_conflict_do_update(
                index_elements=["transaction_id", "component_code"],
                set_={
                    "amount": amount,
                    "currency": currency,
                    "source_order": source_order,
                },
            )
        )
        written += 1
    return written


# ─── Sub-job: payouts ──────────────────────────────────────────────


def _sync_payouts(
    session: Session,
    *,
    proxy_call: ProxyCall,
    account: ChannelAccount,
    scope: str,
    page_size: int,
) -> JobResult:
    """Pull payouts under cursor ``tiktok.finance.payouts``."""
    from tts_erp_v2.sync_worker import watermarks

    watermark_raw = watermarks.get_cursor(
        session, job_name=PAYOUTS_JOB_NAME, scope=scope
    )
    # The payouts sub-job always writes an epoch_ms int (see set_cursor
    # call below), but get_cursor's return type is int|str|None to
    # accommodate the logistics token-cursor. Narrow to int up-front.
    watermark_ms: int | None = watermark_raw if isinstance(watermark_raw, int) else None
    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        base_body["update_time_ge"] = watermark_ms // 1000

    # Walk the payouts pages. Upstream failures propagate (no silent
    # swallow) so run_with_sync_job marks the tick 'failed' and the
    # watermark stays put.
    raw_payouts = _walk_pages(
        proxy_call,
        endpoint=PAYOUTS_ENDPOINT,
        base_body=base_body,
        items_key="payments",
    )

    total = 0
    inserted = 0
    failed = 0
    max_update_ms: int | None = watermark_ms

    for raw_p in raw_payouts:
        total += 1
        ext_payout_id = str(raw_p.get("payment_id") or raw_p.get("id") or "<unknown>")
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
        _upsert_payout(
            session,
            account_id=account.id,
            fields=p_fields,
            raw_record_id=raw_row.id,
        )

        inserted += 1
        if p_fields["source_updated_at"] is not None:
            update_ms = _safe_int(p_fields["source_updated_at"].timestamp() * 1000)
            if update_ms and (max_update_ms is None or update_ms > max_update_ms):
                max_update_ms = update_ms

    if max_update_ms is not None and (
        watermark_ms is None or max_update_ms > watermark_ms
    ):
        watermarks.set_cursor(
            session,
            job_name=PAYOUTS_JOB_NAME,
            scope=scope,
            cursor_epoch_ms=max_update_ms,
        )

    return JobResult(
        rows_total=total,
        rows_inserted=inserted,
        rows_failed=failed,
        cursor=max_update_ms,
    )


# ─── Sub-job: statements (top-level, not nested in payouts loop) ───


def _sync_statements(
    session: Session,
    *,
    proxy_call: ProxyCall,
    account: ChannelAccount,
    scope: str,
    page_size: int,
) -> JobResult:
    """Pull statements + their transactions under ``tiktok.finance.statements``.

    The statements cursor advances independently of the payouts cursor —
    this is the fix for the late-statement problem: a payout that has
    settled and stopped bumping ``update_time`` can still receive a
    late statement, and we still want to pick it up next tick.
    """
    from tts_erp_v2.sync_worker import watermarks

    watermark_raw = watermarks.get_cursor(
        session, job_name=STATEMENTS_JOB_NAME, scope=scope
    )
    watermark_ms: int | None = watermark_raw if isinstance(watermark_raw, int) else None
    base_body: dict[str, Any] = {"page_size": page_size}
    if watermark_ms:
        # TikTok 202309 statement filter is `statement_time_ge` (seconds).
        base_body["statement_time_ge"] = watermark_ms // 1000

    # Walk the statements pages. Upstream failures propagate (no silent
    # swallow) so run_with_sync_job marks the tick 'failed' and the
    # watermark stays put.
    raw_statements = _walk_pages(
        proxy_call,
        endpoint=STATEMENTS_ENDPOINT,
        base_body=base_body,
        items_key="statements",
    )

    total = 0
    inserted = 0
    failed = 0
    # Track max statement_time ONLY for successfully-resolved statements.
    # Statements we skip (missing payment_id, unresolved payout, parse
    # error) MUST NOT advance the cursor past them — the next tick will
    # re-fetch and try again.
    max_statement_ms: int | None = watermark_ms

    for raw_s in raw_statements:
        total += 1
        ext_stmt_id = str(raw_s.get("id") or raw_s.get("statement_id") or "<unknown>")

        # Step 1: resolve owning payout via upstream ``payment_id``.
        # Production evidence (audit 2026-08-31): 29/45 statements carry
        # ``payment_id``; the other 16 don't (TikTok dropped it from
        # earlier payloads). We MUST NOT silently attach — the FK would
        # be wrong, and we'd lose the chance to back-fill later.
        payment_id = raw_s.get("payment_id")
        if not payment_id:
            failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="STATEMENT_PAYMENT_ID_MISSING",
                    external_id=ext_stmt_id,
                    details={
                        "section": "statements",
                        "reason": "payment_id missing in payload",
                    },
                )
            )
            continue

        payout_row = session.execute(
            select(Payout).where(
                Payout.shop_pk == account.id,
                Payout.external_payout_id == str(payment_id),
            )
        ).scalar_one_or_none()
        if payout_row is None:
            failed += 1
            session.add(
                SyncIssue(
                    job_name=JOB_NAME,
                    issue_type="STATEMENT_PAYOUT_UNRESOLVED",
                    external_id=ext_stmt_id,
                    details={
                        "section": "statements",
                        "reason": "no payouts row for payment_id",
                        "payment_id": str(payment_id),
                    },
                )
            )
            continue

        # Step 2: parse the statement itself.
        try:
            s_fields = _parse_statement(raw_s, payout_id=payout_row.id)
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

        # Step 3: pull transactions for this statement. Upstream failures
        # propagate (no silent swallow) — pre-refactor this was
        # ``except UpstreamJobError: raw_txns=[]`` which masked
        # intermittent 5xx as empty transactions (silent data loss).
        raw_txns = _walk_pages(
            proxy_call,
            endpoint=STATEMENT_TRANSACTIONS_TEMPLATE.format(
                statement_id=s_fields["external_statement_id"]
            ),
            base_body={"page_size": page_size},
            items_key="statement_transactions",
        )

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

            # Link the transaction to its sales order when the upstream
            # payload carries ``order_id`` (real 202309 transactions do).
            # Pre-fix this was never resolved → the whole
            # ``settlement_transactions`` table sat with order_pk NULL and
            # 订单×结算 couldn't be reconciled (audit 2026-09-05). Orders
            # that haven't synced yet (or that we never store) are left
            # NULL and surfaced as a SyncIssue so the gap stays visible.
            order_id_ext = raw_t.get("order_id")
            if order_id_ext:
                order_pk = session.execute(
                    select(SalesOrder.id).where(
                        SalesOrder.shop_pk == account.id,
                        SalesOrder.order_id == str(order_id_ext),
                    )
                ).scalar_one_or_none()
                t_fields["order_pk"] = order_pk
                if order_pk is None:
                    failed += 1
                    session.add(
                        SyncIssue(
                            job_name=JOB_NAME,
                            issue_type="TXN_ORDER_NOT_FOUND",
                            external_id=ext_txn_id,
                            details={
                                "section": "transactions",
                                "order_id": str(order_id_ext),
                            },
                        )
                    )

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
            _write_components(
                session,
                transaction_id=txn_id,
                raw=raw_t,
                default_currency=s_fields.get("currency"),
            )

        # Only advance the cursor past statements we successfully ingested.
        inserted += 1
        if s_fields["statement_time"] is not None:
            stime_ms = _safe_int(s_fields["statement_time"].timestamp() * 1000)
            if stime_ms and (max_statement_ms is None or stime_ms > max_statement_ms):
                max_statement_ms = stime_ms

    if max_statement_ms is not None and (
        watermark_ms is None or max_statement_ms > watermark_ms
    ):
        watermarks.set_cursor(
            session,
            job_name=STATEMENTS_JOB_NAME,
            scope=scope,
            cursor_epoch_ms=max_statement_ms,
        )

    return JobResult(
        rows_total=total,
        rows_inserted=inserted,
        rows_failed=failed,
        cursor=max_statement_ms,
    )


# ─── Orchestrator ──────────────────────────────────────────────────


def run(
    session: Session,
    *,
    proxy_call: ProxyCall,
    shop_id: str,
    page_size: int = 50,
    scope: str | None = None,
) -> JobResult:
    """Top-level entry. Runs both sub-jobs on a single open session.

    Order matters: payouts first, so statements can resolve their
    ``payout_id`` via ``payment_id`` in the same tick (otherwise we'd
    log ``STATEMENT_PAYOUT_UNRESOLVED`` for every statement on the
    first run after a fresh credentials row appears).

    Each sub-job commits its own watermark at the end. ``run_with_sync_job``
    calls ``session.commit()`` once after we return, so both cursor writes
    and all the business rows land in the same transaction.
    """
    cursor_scope = scope or shop_id
    account = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == shop_id,
        )
    ).scalar_one_or_none()
    if account is None:
        raise UpstreamJobError(f"shops row missing for tiktok shop_id={shop_id!r}")

    payouts_result = _sync_payouts(
        session,
        proxy_call=proxy_call,
        account=account,
        scope=cursor_scope,
        page_size=page_size,
    )
    statements_result = _sync_statements(
        session,
        proxy_call=proxy_call,
        account=account,
        scope=cursor_scope,
        page_size=page_size,
    )

    return JobResult(
        rows_total=payouts_result.rows_total + statements_result.rows_total,
        rows_inserted=payouts_result.rows_inserted + statements_result.rows_inserted,
        rows_failed=payouts_result.rows_failed + statements_result.rows_failed,
        # Top-level cursor is None — each sub-job owns its own row in
        # integration.sync_cursors under PAYOUTS_JOB_NAME / STATEMENTS_JOB_NAME.
        cursor=None,
    )


__all__ = [
    "JOB_NAME",
    "PAYOUTS_ENDPOINT",
    "PAYOUTS_JOB_NAME",
    "STATEMENTS_ENDPOINT",
    "STATEMENTS_JOB_NAME",
    "STATEMENT_TRANSACTIONS_TEMPLATE",
    "_COMPONENT_COLUMNS",
    "ParseError",
    "ProxyCall",
    "UpstreamJobError",
    "run",
]
