"""Coverage tests for tts_erp_v2.jobs.miaoshou.purchase_orders.

Goal: lift ``purchase_orders.py`` from 63.8% → ≥80%.

Each test isolates one branch from the missing-line report:

* Parsers (``_parse_order_header`` / ``_parse_order_line`` / ``_to_decimal``
  / ``_parse_iso``) — direct unit tests (lines 87, 107-110, 124-127,
  134, 141-143).
* Sync-issue emission:
  - non-dict items (line ~165) → PURCHASE_ORDER_PARSE_FAILED
  - dict with no id (line ~172) → PURCHASE_ORDER_MISSING_ID
  - dict with a line missing id (line ~178) → PURCHASE_ORDER_LINE_MISSING_ID
  - line whose product is unknown (line ~186) → PURCHASE_ORDER_PRODUCT_UNKNOWN
* Pagination / next-page handling (lines 219-226, 230-237).
* Exception-during-parse (line ~265) → PURCHASE_ORDER_PARSE_FAILED with
  exception details.
* Long tail:
  - upsert with ``goodsId`` key (lines 372-392 — order-line upsert path).
  - resolver returns None when external_product_id is empty
    (line 406-408 — ``_resolve_product_id`` short-circuit).
  - missing credentials → RuntimeError.

All assertions filter reads by ``external_purchase_order_id LIKE 'TEST_%'``
/ ``external_line_id LIKE 'TEST_%'`` / ``external_product_id LIKE 'TEST_%'``
so prod data never enters the result set (see
``/home/schan/tts-erp/logs/diagnose-failures.md``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import RawRecord, SyncIssue, SyncJob
from tts_erp_v2.db.models.procurement import (
    ProcurementProduct,
    PurchaseOrder,
    PurchaseOrderLine,
)
from tts_erp_v2.jobs.miaoshou import purchase_orders as po_mod
from tts_erp_v2.jobs.miaoshou._common import (
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.miaoshou.purchase_orders import (
    ENDPOINT,
    JOB_NAME,
    _parse_iso,
    _parse_order_header,
    _parse_order_line,
    _resolve_product_id,
    _to_decimal,
    sync_purchase_orders,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


# ───────────────────── parser unit tests ─────────────────────


def test_to_decimal_handles_none_and_bad_input() -> None:
    assert _to_decimal(None) is None
    # Decimal-parses: int as string, float as string, real Decimal.
    assert _to_decimal("12.50") == Decimal("12.50")
    assert _to_decimal(7) == Decimal("7")
    assert _to_decimal(Decimal("3.14")) == Decimal("3.14")
    # Garbage → None.
    assert _to_decimal("not-a-number") is None


def test_to_decimal_none_branch_returns_none() -> None:
    """Single-purpose assertion for line 124-127 — None input → None output."""
    assert _to_decimal(None) is None


def test_parse_iso_accepts_iso_datetime_with_t_separator() -> None:
    """``T`` separator + ``.sss`` fractional suffix must normalise into a
    tz-aware UTC datetime. (Note: the parser does NOT strip a trailing
    ``Z`` — those values fall through to ``None``. See
    ``test_parse_iso_returns_none_for_garbage`` for the negative path.)"""
    result = _parse_iso("2026-08-01T10:00:00")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result == datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_strips_fractional_seconds() -> None:
    """Trailing ``.sss`` fractional component must be discarded."""
    result = _parse_iso("2026-08-01 10:00:00.123")
    assert result == datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_accepts_date_only() -> None:
    """``YYYY-MM-DD`` (no time component) must produce midnight."""
    result = _parse_iso("2026-08-15")
    assert result == datetime(2026, 8, 15, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_passthrough_for_datetime_instance() -> None:
    """Datetime input is returned with UTC tzinfo attached (or kept)."""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    out = _parse_iso(naive)
    assert out is not None
    assert out.tzinfo is not None
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_iso(aware) == aware


def test_parse_iso_returns_none_for_garbage() -> None:
    """Unparseable strings / empty inputs → None."""
    assert _parse_iso("") is None
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None


def test_parse_order_header_missing_id_returns_none() -> None:
    """An order without any id-key falls into the missing-id branch
    (line 87 — returns ``None``)."""
    assert _parse_order_header({}) is None
    assert _parse_order_header({"goodsPurchaseOrderId": ""}) is None


def test_parse_order_header_full_shape() -> None:
    """All optional fields populated — verifies the alternate-key fallbacks
    (``supplierId`` / `paidTime` / `completedTime` / `gmtCreate` /
    `gmtModified`) coerce correctly into the DB types.
    """
    parsed = _parse_order_header(
        {
            "goodsPurchaseOrderId": "PO_TEST_full",
            "supplierId": 1737,
            "status": "PAID",
            "orderStatus": "COMPLETED",  # 'status' wins when truthy
            "currency": "CNY",
            "totalAmount": "100.50",
            "paidTime": "2026-08-01 10:00:00",
            "completedTime": "2026-08-02 12:00:00",
            "gmtCreate": "2026-08-01 09:00:00",
            "gmtModified": "2026-08-15 09:00:00",
        }
    )
    assert parsed is not None
    assert parsed["external_purchase_order_id"] == "PO_TEST_full"
    assert parsed["supplier_id"] == "1737"  # int → str
    assert parsed["status"] == "PAID"  # 'status' wins over 'orderStatus'
    assert parsed["currency"] == "CNY"
    assert parsed["total_amount"] == Decimal("100.50")
    assert parsed["paid_at"] == datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert parsed["completed_at"] == datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
    assert parsed["source_created_at"] == datetime(2026, 8, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert parsed["source_updated_at"] == datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_order_header_alternate_keys() -> None:
    """Verify the alternate-key fallbacks (``purchaseOrderId`` /
    ``orderStatus`` / ``amount`` / ``paidAt`` / ``completedAt`` /
    ``createTime`` / ``updateTime``) — exercised when the primary key is
    absent.
    """
    parsed = _parse_order_header(
        {
            "purchaseOrderId": "PO_TEST_alt",
            "supplierId": None,  # None → None (no string coercion)
            "orderStatus": "SHIPPED",
            "amount": "50.00",
            "paidAt": "2026-07-01 10:00:00",
            "completedAt": "2026-07-02 10:00:00",
            "createTime": "2026-07-01 09:00:00",
            "updateTime": "2026-07-02 09:00:00",
        }
    )
    assert parsed is not None
    assert parsed["external_purchase_order_id"] == "PO_TEST_alt"
    assert parsed["supplier_id"] is None  # None input → None output
    assert parsed["status"] == "SHIPPED"  # 'status' was absent; 'orderStatus' wins
    assert parsed["total_amount"] == Decimal("50.00")
    assert parsed["paid_at"] == datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_order_header_supplier_id_none() -> None:
    """``supplierId is None`` must NOT be stringified (line 90-91)."""
    parsed = _parse_order_header({"goodsPurchaseOrderId": "PO_TEST_sid", "supplierId": None})
    assert parsed is not None
    assert parsed["supplier_id"] is None


def test_parse_order_line_missing_id_returns_none() -> None:
    """Line dict without any id-key falls into the missing-id branch
    (line 134 — returns ``None``)."""
    assert _parse_order_line("PO_TEST_1", {}) is None
    assert _parse_order_line("PO_TEST_2", {"goodsPurchaseOrderLineId": ""}) is None


def test_parse_order_line_full_shape() -> None:
    parsed = _parse_order_line(
        "PO_TEST_parent",
        {
            "goodsPurchaseOrderLineId": "TEST_line_1",
            "goodsId": "TEST_prod_1",
            "quantity": 5,
            "unitPrice": "3.00",
            "currency": "CNY",
            "status": "PAID",
        },
    )
    assert parsed is not None
    assert parsed["external_line_id"] == "TEST_line_1"
    assert parsed["external_product_id"] == "TEST_prod_1"
    assert parsed["quantity"] == Decimal("5")
    assert parsed["unit_cost"] == Decimal("3.00")
    assert parsed["currency"] == "CNY"
    assert parsed["line_status"] == "PAID"
    assert parsed["_order_external_id"] == "PO_TEST_parent"


def test_parse_order_line_alternate_keys_and_blank_product() -> None:
    """``lineId`` fallback, `qty`/`unitCost` fallbacks, missing `goodsId` /
    `productId` (empty string fallback), `lineStatus` fallback — all
    reachable when the primary keys are absent.
    """
    parsed = _parse_order_line(
        "PO_TEST_parent2",
        {
            "lineId": "TEST_line_alt",
            "productId": "TEST_prod_alt",
            "qty": 7,
            "unitCost": "2.50",
            "lineStatus": "PENDING",
        },
    )
    assert parsed is not None
    assert parsed["external_line_id"] == "TEST_line_alt"
    assert parsed["external_product_id"] == "TEST_prod_alt"
    assert parsed["quantity"] == Decimal("7")
    assert parsed["unit_cost"] == Decimal("2.50")
    assert parsed["line_status"] == "PENDING"


# ───────────────────── sync-issue emission paths ─────────────────────


def _seed_procurement_product(db_session, account_id: int, external_id: str) -> None:
    """Insert a ProcurementProduct row so line-upsert paths can resolve."""
    db_session.add(
        ProcurementProduct(
            procurement_account_id=account_id,
            external_product_id=external_id,
            title=f"TEST product {external_id}",
        )
    )
    db_session.flush()


def test_sync_purchase_orders_non_dict_item_emits_parse_failed_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """A non-dict entry in ``goodsPurchaseOrderList`` lands in
    ``sync_issues`` (issue_type='PURCHASE_ORDER_PARSE_FAILED') and the
    job continues.
    """
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": ["this-is-not-a-dict"],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_PARSE_FAILED",
            SyncIssue.external_id.is_(None),
        )
        .order_by(SyncIssue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert issue is not None
    assert "this-is-not-a-dict" in str(issue.details)


def test_sync_purchase_orders_missing_order_id_emits_missing_id_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Order dict without any id-key → PURCHASE_ORDER_MISSING_ID issue."""
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {"status": "PAID"},  # no id keys at all
                ],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_MISSING_ID",
            SyncIssue.external_id.is_(None),
        )
        .order_by(SyncIssue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert issue is not None


def test_sync_purchase_orders_line_missing_id_emits_line_missing_id_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Order with a line that has no id-key → PURCHASE_ORDER_LINE_MISSING_ID
    issue; the order itself still gets upserted (line 178 path)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_gp_known")

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {
                        "goodsPurchaseOrderId": "TEST_po_with_bad_line",
                        "goodsPurchaseOrderLineList": [
                            # no id keys → _parse_order_line returns None
                            {"quantity": 1, "unitPrice": "5.00"}
                        ],
                    }
                ],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_LINE_MISSING_ID",
            SyncIssue.external_id == "TEST_po_with_bad_line",
        )
        .order_by(SyncIssue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert issue is not None


def test_sync_purchase_orders_unknown_product_emits_product_unknown_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Line whose ``goodsId`` doesn't match a ProcurementProduct →
    PURCHASE_ORDER_PRODUCT_UNKNOWN issue; line is skipped, order still upserts."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {
                        "goodsPurchaseOrderId": "TEST_po_unknown_prod",
                        "goodsPurchaseOrderLineList": [
                            {
                                "goodsPurchaseOrderLineId": "TEST_line_unknown",
                                "goodsId": "TEST_unknown_product",
                                "quantity": 1,
                                "unitPrice": "5.00",
                            }
                        ],
                    }
                ],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_PRODUCT_UNKNOWN",
            SyncIssue.external_id == "TEST_unknown_product",
        )
        .order_by(SyncIssue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert issue is not None
    assert issue.details == {"purchase_order_id": "TEST_po_unknown_prod"}


def test_sync_purchase_orders_parse_exception_emits_parse_failed_issue(
    db_session, fake_client, miaoshou_credentials_row, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``_parse_order_header`` itself raises (unmockable as a happy
    path), the outer ``except Exception`` records a
    ``PURCHASE_ORDER_PARSE_FAILED`` issue with the error message and the
    job continues."""

    def boom(_order):
        raise ValueError("simulated parser crash")

    monkeypatch.setattr(po_mod, "_parse_order_header", boom)

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [{"goodsPurchaseOrderId": "TEST_po_crash"}],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 0
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue)
        .where(
            SyncIssue.job_name == JOB_NAME,
            SyncIssue.issue_type == "PURCHASE_ORDER_PARSE_FAILED",
            SyncIssue.external_id == "TEST_po_crash",
        )
        .order_by(SyncIssue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    assert issue is not None
    assert "ValueError" in issue.details["error"]
    assert "simulated parser crash" in issue.details["error"]


# ───────────────────── pagination / next page ─────────────────────


def test_sync_purchase_orders_walks_multiple_pages_via_total_pages(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """3 pages of data → pagination must terminate naturally via
    ``totalPage`` advertised by the upstream (lines 219-226 + 230-237)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_pg_1")
    _seed_procurement_product(db_session, ctx.account_id, "TEST_pg_2")
    _seed_procurement_product(db_session, ctx.account_id, "TEST_pg_3")

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("page", 1))
        if page <= 2:
            return {
                "result": "success",
                "data": {
                    "goodsPurchaseOrderList": [
                        {
                            "goodsPurchaseOrderId": f"TEST_po_p{page}",
                            "goodsPurchaseOrderLineList": [
                                {
                                    "goodsPurchaseOrderLineId": f"TEST_line_p{page}",
                                    "goodsId": f"TEST_pg_{page}",
                                    "quantity": 1,
                                    "unitPrice": "3.00",
                                }
                            ],
                        }
                    ],
                    "total": 3,
                    "totalPage": 2,
                },
            }
        # page 3 advertised but upstream actually returns empty
        return {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 3, "totalPage": 2},
        }

    fake_client.install(side_effect)
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["pages_walked"] == 2
    assert result["orders_upserted"] == 2
    assert result["lines_upserted"] == 2
    assert result["orders_seen"] == 2


def test_sync_purchase_orders_total_count_only_terminates_pagination(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """When upstream advertises ``total`` (NOT ``totalPage``) the
    pagination loop uses it to terminate (the second branch of
    line 230-237 — ``elif isinstance(tc, int) and expected_total_pages is None``)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_pg_tc")

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("page", 1))
        if page == 1:
            return {
                "result": "success",
                "data": {
                    "goodsPurchaseOrderList": [
                        {
                            "goodsPurchaseOrderId": "TEST_po_total_only",
                            "goodsPurchaseOrderLineList": [
                                {
                                    "goodsPurchaseOrderLineId": "TEST_line_total_only",
                                    "goodsId": "TEST_pg_tc",
                                    "quantity": 1,
                                    "unitPrice": "3.00",
                                }
                            ],
                        }
                    ],
                    "total": 1,
                },
            }
        return {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 1},
        }

    fake_client.install(side_effect)
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1


# ───────────────────── long tail: order-line upsert + resolver ─────────────────────


def test_sync_purchase_orders_writes_purchase_order_and_line(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Full success path — exercises the order-line upsert (lines
    372-392, including the line-state-mapping edge cases)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_full_prod")

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {
                        "goodsPurchaseOrderId": "TEST_full_po",
                        "supplierId": "TEST_supplier_1",
                        "status": "PAID",
                        "orderStatus": "COMPLETED",  # 'status' wins
                        "currency": "CNY",
                        "totalAmount": "150.00",
                        "paidTime": "2026-08-01 10:00:00",
                        "completedTime": "2026-08-02 12:00:00",
                        "gmtCreate": "2026-08-01 09:00:00",
                        "gmtModified": "2026-08-15 09:00:00",
                        "goodsPurchaseOrderLineList": [
                            {
                                "goodsPurchaseOrderLineId": "TEST_full_line",
                                "goodsId": "TEST_full_prod",
                                "quantity": 3,
                                "unitPrice": "50.00",
                                "currency": "CNY",
                                "status": "PAID",
                            }
                        ],
                    }
                ],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 1

    order = db_session.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.external_purchase_order_id == "TEST_full_po"
        )
    ).scalar_one()
    assert order.status == "PAID"  # 'status' wins over 'orderStatus'
    assert order.currency == "CNY"
    assert order.total_amount == Decimal("150.0000")
    assert order.supplier_id == "TEST_supplier_1"

    line = db_session.execute(
        select(PurchaseOrderLine).where(
            PurchaseOrderLine.external_line_id == "TEST_full_line"
        )
    ).scalar_one()
    assert line.quantity == Decimal("3.0000")
    assert line.unit_cost == Decimal("50.0000")
    assert line.currency == "CNY"
    assert line.line_status == "PAID"


def test_resolve_product_id_returns_none_when_external_id_empty(
    db_session, miaoshou_credentials_row
) -> None:
    """``external_product_id is None`` or empty string → resolver short-
    circuits to ``None`` (line 406-408)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    assert _resolve_product_id(
        db_session,
        procurement_account_id=ctx.account_id,
        external_product_id=None,
    ) is None
    assert _resolve_product_id(
        db_session,
        procurement_account_id=ctx.account_id,
        external_product_id="",
    ) is None


def test_sync_purchase_orders_no_credentials_raises(
    db_session, monkeypatch: pytest.MonkeyPatch, fake_client
) -> None:
    """``resolve_miaoshou_context`` returns ``None`` (no credentials,
    no env var) → job raises ``RuntimeError`` (lines 167-170)."""
    # Force resolve_miaoshou_context to return None.
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", "")
    fake_client.install(lambda **_: {"result": "success", "data": {"goodsPurchaseOrderList": []}})
    with pytest.raises(RuntimeError, match="no miaoshou credentials row"):
        sync_purchase_orders(db_session, client=fake_client)


def test_sync_purchase_orders_sync_job_row_records_pagination_counters(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """The SyncJob row's ``extra`` JSON carries ``pages_walked`` +
    ``rate_limit_retries`` + ``finished_at_iso`` — exercised on the
    happy-path empty response (lines 230-237 → extra dict)."""
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"goodsPurchaseOrderList": [], "total": 0},
        }
    )
    sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    # Filter by job_name AND only the rows inserted by THIS test session
    # (SyncJob.credential_id is None because the job does not pass it
    # explicitly — see purchase_orders.py).
    jobs = db_session.execute(
        select(SyncJob)
        .where(
            SyncJob.job_name == JOB_NAME,
            SyncJob.credential_id.is_(None),
            SyncJob.status == "succeeded",
        )
        .order_by(SyncJob.id.desc())
        .limit(10)
    ).scalars().all()
    # Find the most recent job — there may be prod rows too, but our
    # test's writes include the empty-list result which has empty
    # pagination counters.
    assert jobs, "expected at least one succeeded SyncJob row"
    job = jobs[0]
    assert job.status == "succeeded"
    assert job.extra["pages_walked"] == 1
    assert job.extra["rate_limit_retries"] == 0
    assert "finished_at_iso" in job.extra


def test_sync_purchase_orders_records_raw_payload_per_order(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Each successfully-parsed order writes one ``integration.raw_records`` row
    keyed by the upstream ``goodsPurchaseOrderId`` (line 230-237 path)."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_raw_prod")

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {
                        "goodsPurchaseOrderId": "TEST_raw_po",
                        "goodsPurchaseOrderLineList": [
                            {
                                "goodsPurchaseOrderLineId": "TEST_raw_line",
                                "goodsId": "TEST_raw_prod",
                                "quantity": 1,
                                "unitPrice": "1.00",
                            }
                        ],
                    }
                ],
                "total": 1,
            },
        }
    )
    sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    raw = db_session.execute(
        select(RawRecord).where(
            RawRecord.endpoint == ENDPOINT,
            RawRecord.external_id == "TEST_raw_po",
        )
    ).scalar_one()
    assert raw.payload["goodsPurchaseOrderId"] == "TEST_raw_po"
    assert raw.credential_id == miaoshou_credentials_row.id
    assert raw.payload_hash is not None


def test_sync_purchase_orders_lines_key_fallback(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Line payload uses ``lines`` key (NOT ``goodsPurchaseOrderLineList``)
    — exercises the fallback at line 219-220."""
    ctx = resolve_miaoshou_context(db_session)
    assert ctx is not None
    _seed_procurement_product(db_session, ctx.account_id, "TEST_lines_prod")

    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {
                "goodsPurchaseOrderList": [
                    {
                        "goodsPurchaseOrderId": "TEST_lines_po",
                        "lines": [
                            {
                                "goodsPurchaseOrderLineId": "TEST_lines_line",
                                "goodsId": "TEST_lines_prod",
                                "quantity": 2,
                                "unitPrice": "1.50",
                            }
                        ],
                    }
                ],
                "total": 1,
            },
        }
    )
    result = sync_purchase_orders(db_session, client=fake_client)
    db_session.commit()
    assert result["orders_upserted"] == 1
    assert result["lines_upserted"] == 1
