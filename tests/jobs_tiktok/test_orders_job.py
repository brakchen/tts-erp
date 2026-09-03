"""TDD tests for jobs.tiktok.orders — incremental order sync.

The orders job is the canonical incremental sync:

* Reads ``integration.sync_cursors.cursor_epoch_ms`` for scope=shop_id
  (None on first run).
* POSTs ``/order/202309/orders/search`` with ``update_time_ge`` =
  watermark/1000 (the cursor stores ms; the upstream API wants s).
* Walks every page via ``next_page_token``.
* For each order:
  - Stores the raw JSON in ``integration.raw_records``.
  - Upserts ``commerce.sales_orders`` + ``commerce.sales_order_lines``.
  - On parse failure: writes ``integration.sync_issues``, continues.
* Writes the max ``update_time`` (in ms) seen back to the cursor.

The proxy layer is mocked with a :class:`FakeProxy` that returns
scripted responses keyed on the upstream page state.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    Credentials,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
    SyncJob,
)
from tts_erp_v2.jobs.tiktok import orders as orders_job
from tts_erp_v2.jobs.tiktok.orders import run as run_orders
from tts_erp_v2.sync_worker import watermarks
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]

# ─── Test helpers ─────────────────────────────────────────────────


def _make_credential(session, external_id="TEST_TT_ORD_CRED") -> Credentials:
    cred = Credentials(
        provider="tiktok",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    return cred


def _make_channel_account(session, external_id="TEST_TT_ORD_SHOP") -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id=external_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id=external_id,
        credential_id=cred.id,
    )
    session.add(acct)
    session.flush()
    return acct


def _order_payload(
    *,
    order_id: str,
    update_time: int,
    status: str = "UNSHIPPED",
    currency: str = "USD",
    lines: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    """Construct a realistic-ish /orders/search response item.

    Matches the documented TikTok 202309 spec shape: id, status,
    payment_amount, line_items[]. Anything missing from the spec is
    tolerated by the parser (raw_records keeps the full payload).
    """
    item: dict[str, Any] = {
        "order_id": order_id,
        "order_status": status,
        "currency": currency,
        "payment_amount": {"amount": "12.50", "currency": currency},
        "update_time": update_time,
        "create_time": update_time - 60,
        "line_items": lines or [],
    }
    if extra:
        item.update(extra)
    return item


class FakeProxy:
    """Scripted stand-in for ``TiktokShopClient`` (no real HTTP).

    Pages are keyed by ``next_page_token`` injected into the request
    body. Page 0 is the first call; subsequent pages return whatever
    the test wired up under their respective ``page=`` scripts.

    Each script is a dict matching the upstream envelope shape::

        {"code": 0, "message": "ok", "data": {"orders": [...],
         "next_page_token": "..."}, "request_id": "..."}

    The recorded ``requests`` list lets tests assert on call shape
    (paths, body content, headers) without ever touching the network.
    """

    def __init__(self, pages: list[dict] | None = None) -> None:
        # pages are walked in order. page[0] is the first response.
        # Each response can declare a ``response_label`` that the next
        # request must echo back via ``next_page_token`` to receive the
        # subsequent page. If a request arrives for a label that has no
        # corresponding page, we serve the LAST page (terminal / empty)
        # to avoid infinite loops.
        self._pages = list(pages or [])
        self._page_map: dict[str, dict] = {}
        for idx, p in enumerate(self._pages):
            label = p.get("response_label")
            if label:
                self._page_map[label] = self._pages[idx + 1] if idx + 1 < len(self._pages) else self._pages[-1]
        self._fallback = self._pages[-1] if self._pages else {"code": 0, "data": {"orders": []}}
        self.requests: list[dict[str, Any]] = []

    def __call__(self, method: str, path: str, *, body: dict | None = None, **kw: Any) -> dict:
        self.requests.append({"method": method, "path": path, "body": body or {}})
        if body and "next_page_token" in body:
            return self._page_map.get(body["next_page_token"], self._fallback)
        return self._pages[0] if self._pages else {"code": 0, "data": {"orders": []}}


# ─── Happy path ────────────────────────────────────────────────────


def test_orders_first_run_writes_raw_records_and_normalized_rows(
    db_session,
) -> None:
    """No prior watermark → first run ingests everything; raw_records +
    sales_orders + sales_order_lines all populated."""
    account = _make_channel_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "orders": [
                        _order_payload(
                            order_id="5800000000000001",
                            update_time=1_700_000_100,
                            status="UNSHIPPED",
                            lines=[
                                {
                                    "line_id": "L1",
                                    "product_id": "P1",
                                    "sku_id": "S1",
                                    "product_name": "TEST prod 1",
                                    "sku_name": "TEST sku 1",
                                    "quantity": 1,
                                    "sale_price": {"amount": "12.50", "currency": "USD"},
                                },
                            ],
                        ),
                        _order_payload(
                            order_id="5800000000000002",
                            update_time=1_700_000_200,
                            status="TO_FULFILL",
                        ),
                    ],
                    "next_page_token": "page_2_tok",
                },
            },
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "orders": [
                        _order_payload(
                            order_id="5800000000000003",
                            update_time=1_700_000_300,
                            status="SHIPPED",
                        ),
                    ],
                    "next_page_token": "",
                },
            },
        ]
    )

    sync_row, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )

    # ── sync_jobs row reflects the run ────────────────────────────
    assert sync_row.status == "succeeded"
    assert result.rows_total == 3
    assert result.rows_inserted == 3
    assert sync_row.rows_failed == 0

    # ── raw_records: one per order ─────────────────────────────────
    raw_ids = {
        r.external_id
        for r in db_session.execute(
            select(RawRecord).where(
                RawRecord.external_id.in_([
                    "5800000000000001",
                    "5800000000000002",
                    "5800000000000003",
                ])
            )
        ).scalars().all()
    }
    assert raw_ids == {
        "5800000000000001",
        "5800000000000002",
        "5800000000000003",
    }

    # ── sales_orders: 3 rows, all upserted ─────────────────────────
    order_rows = db_session.execute(
        select(SalesOrder).where(
            SalesOrder.external_order_id.in_([
                "5800000000000001",
                "5800000000000002",
                "5800000000000003",
            ])
        )
    ).scalars().all()
    assert {o.external_order_id for o in order_rows} == {
        "5800000000000001",
        "5800000000000002",
        "5800000000000003",
    }
    assert all(o.channel_account_id == account.id for o in order_rows)
    assert all(o.raw_record_id is not None for o in order_rows)

    # ── sales_order_lines: only the one order had lines ────────────
    lines = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.external_line_id == "L1")
    ).scalars().all()
    assert len(lines) == 1
    assert lines[0].external_line_id == "L1"
    assert lines[0].external_product_id_snapshot == "P1"
    assert lines[0].external_variant_id_snapshot == "S1"

    # ── sync_cursors: watermark advanced to max update_time ms ────
    cursor_value = watermarks.get_cursor(
        db_session, job_name="tiktok.orders", scope=account.external_account_id
    )
    assert cursor_value == 1_700_000_300_000  # ms = s × 1000


def test_orders_second_run_advances_watermark_only(db_session) -> None:
    """A second run with the cursor advanced picks up only newer orders
    and the cursor advances again. Idempotency: re-seen order is upserted
    (update) without duplicating."""
    account = _make_channel_account(db_session)
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.orders",
        scope=account.external_account_id,
        cursor_epoch_ms=1_700_000_300_000,
    )
    db_session.commit()

    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "orders": [
                        _order_payload(
                            order_id="5800000000000004",
                            update_time=1_700_000_400,
                            status="SHIPPED",
                        ),
                    ],
                    "next_page_token": "",
                },
            },
        ]
    )

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )

    assert result.rows_inserted == 1
    assert result.rows_total == 1
    # The second-run request must carry update_time_ge derived from
    # the stored cursor (seconds = ms / 1000).
    assert proxy.requests[0]["body"].get("update_time_ge") == 1_700_000_300
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.orders", scope=account.external_account_id
    )
    assert cursor == 1_700_000_400_000


def test_orders_re_upsert_is_idempotent(db_session) -> None:
    """Re-running on the same window with the same order must update
    the existing sales_orders row, not insert a duplicate."""
    account = _make_channel_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "orders": [
                        _order_payload(
                            order_id="5800000000000099",
                            update_time=1_700_001_000,
                            status="SHIPPED",
                        ),
                    ],
                    "next_page_token": "",
                },
            },
        ]
    )

    # First run
    run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )
    first_count = len(
        db_session.execute(
            select(SalesOrder).where(
                SalesOrder.external_order_id == "5800000000000099"
            )
        ).scalars().all()
    )
    assert first_count == 1

    # Reset watermark so the second run re-sees the same row.
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.orders",
        scope=account.external_account_id,
        cursor_epoch_ms=1_700_000_000_000,
    )
    db_session.commit()

    # Second run on the same order
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )

    # No new row, just an update.
    second_count = len(
        db_session.execute(
            select(SalesOrder).where(
                SalesOrder.external_order_id == "5800000000000099"
            )
        ).scalars().all()
    )
    assert second_count == 1
    assert result.rows_inserted == 1  # upsert counts as insert for JobResult bookkeeping


# ─── Parse failures ────────────────────────────────────────────────


def test_orders_parse_failure_writes_sync_issue_continues(db_session) -> None:
    """An order missing required fields (e.g. order_id) → sync_issues row;
    main job does NOT abort."""
    account = _make_channel_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "orders": [
                        # Missing order_id → ParseError
                        {
                            "order_status": "UNSHIPPED",
                            "currency": "USD",
                            "update_time": 1_700_000_500,
                        },
                        # Valid order follows
                        _order_payload(
                            order_id="5800000000000010",
                            update_time=1_700_000_600,
                            status="UNSHIPPED",
                        ),
                    ],
                    "next_page_token": "",
                },
            },
        ]
    )

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )

    # 2 orders seen, 1 failed to parse, 1 ingested
    assert result.rows_total == 2
    assert result.rows_failed == 1
    assert result.rows_inserted == 1

    # sync_issues: 1 row, issue_type=PARSE_ERROR (or similar)
    issues = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.orders")
    ).scalars().all()
    assert len(issues) == 1
    assert issues[0].job_name == "tiktok.orders"
    assert issues[0].issue_type == "PARSE_ERROR"
    # The valid order still landed
    valid_orders = db_session.execute(
        select(SalesOrder).where(
            SalesOrder.external_order_id == "5800000000000010"
        )
    ).scalars().all()
    assert len(valid_orders) == 1
    assert valid_orders[0].external_order_id == "5800000000000010"


# ─── Upstream errors ───────────────────────────────────────────────


def test_orders_non_zero_upstream_code_fails_sync_job(db_session) -> None:
    """Upstream returning ``code != 0`` aborts the job → status='failed'."""
    account = _make_channel_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 105005,
                "message": "Access denied",
                "data": None,
            },
        ]
    )

    with pytest.raises(orders_job.UpstreamJobError):
        run_with_sync_job(
            db_session,
            job_name="tiktok.orders",
            credential_id=account.credential_id,
            inner=run_orders,
            inner_kwargs={
                "proxy_call": proxy,
                "shop_id": account.external_account_id,
            },
        )

    # Filter by credential_id — prod has 649 tiktok.orders SyncJobs that
    # would otherwise inflate the count.
    rows = db_session.execute(
        select(SyncJob).where(
            SyncJob.job_name == "tiktok.orders",
            SyncJob.credential_id == account.credential_id,
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "105005" in (rows[0].error_message or "")


# ─── Empty pages ───────────────────────────────────────────────────


def test_orders_empty_response_is_a_noop(db_session) -> None:
    """First run with no orders → sync_jobs succeeded, 0 rows, cursor
    stays None (no watermark advance on empty data)."""
    account = _make_channel_account(db_session)
    proxy = FakeProxy(
        pages=[
            {"code": 0, "message": "ok", "data": {"orders": [], "next_page_token": ""}},
        ]
    )

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.orders",
        credential_id=account.credential_id,
        inner=run_orders,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )

    assert result.rows_total == 0
    assert result.rows_inserted == 0
    assert result.cursor is None
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.orders", scope=account.external_account_id
    )
    assert cursor is None
