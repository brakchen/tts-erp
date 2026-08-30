"""TDD tests for jobs.tiktok.logistics — tracking events for shipped orders.

The job sources targets from ``integration.raw_records`` (the raw
orders-search payloads) and pulls the per-order tracking list from
``GET /fulfillment/202309/orders/{order_id}/tracking``. Response shape
follows the legacy v1 contract:

    {"code": 0, "data": {"tracking": [{"action_code", "description",
                                        "update_time_millis", ...}]}}
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
    Shipment,
    SyncIssue,
    TrackingEvent,
)
from tts_erp_v2.jobs.tiktok import logistics as logistics_job
from tts_erp_v2.sync_worker import watermarks
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job


class FakeProxy:
    """Responds to the tracking endpoint; key by order_id."""

    def __init__(self, *, tracking_by_order: dict[str, dict] | None = None):
        self.tracking_by_order = dict(tracking_by_order or {})
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        # path looks like /fulfillment/202309/orders/<order_id>/tracking
        for order_id, payload in self.tracking_by_order.items():
            if f"/orders/{order_id}/tracking" in path:
                return payload
        return {"code": 0, "data": {"tracking": []}}


def _make_account_with_order(session, *, shop_id: str = "TEST_TT_LOG_SHOP",
                             external_order_id: str = "TEST_SO_L") -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id=shop_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id=shop_id,
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id=external_order_id,
        status="SHIPPED",
    )
    session.add(so)
    session.flush()
    return acct


def _seed_orders_raw_record(
    session,
    *,
    external_order_id: str,
    tracking_number: str = "TN1",
    provider_id: str = "UPS",
    provider_name: str = "UPS",
    package_id: str | None = None,
) -> RawRecord:
    payload: dict[str, Any] = {
        "id": external_order_id,
        "tracking_number": tracking_number,
        "shipping_provider_id": provider_id,
        "shipping_provider_name": provider_name,
    }
    if package_id is not None:
        payload["packages"] = [{"id": package_id}]
    rr = RawRecord(
        endpoint="/order/202309/orders/search",
        external_id=external_order_id,
        payload=payload,
    )
    session.add(rr)
    session.flush()
    return rr


def _tracking_payload(*events: dict) -> dict:
    return {"code": 0, "data": {"tracking": list(events)}}


def test_logistics_writes_shipment_and_tracks_watermark(db_session) -> None:
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_L",
        tracking_number="TN1",
        package_id="PKG1",
    )
    proxy = FakeProxy(
        tracking_by_order={
            "TEST_SO_L": _tracking_payload(
                {
                    "action_code": 20101,
                    "description": "Order placed.",
                    "update_time_millis": 1_700_002_100_000,
                    "location": "HCMC",
                },
            ),
        },
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 1
    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG1")
    ).scalar_one()
    assert shipment.tracking_number == "TN1"
    assert shipment.provider_id == "UPS"
    events = db_session.execute(
        select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id)
    ).scalars().all()
    assert len(events) == 1
    assert events[0].action_code == 20101
    assert events[0].description == "Order placed."
    # Watermark advanced to the max update_time_millis seen.
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.logistics", scope=account.external_account_id
    )
    assert cursor == 1_700_002_100_000


def test_logistics_synthesizes_package_id_from_order_id(db_session) -> None:
    """When the order payload has no packages list, fall back to the
    order_id as the synthetic external_package_id."""
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_L",
        tracking_number="TN1",
        package_id=None,  # no packages in payload
    )
    proxy = FakeProxy(
        tracking_by_order={
            "TEST_SO_L": _tracking_payload(
                {
                    "action_code": 20101,
                    "description": "Order placed.",
                    "update_time_millis": 1_700_002_100_000,
                },
            ),
        },
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 1
    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "TEST_SO_L")
    ).scalar_one()
    assert shipment.tracking_number == "TN1"


def test_logistics_unknown_order_writes_sync_issue(db_session) -> None:
    """RawRecord references an order_id that has no matching
    SalesOrder → UNKNOWN_ORDER issue, no Shipment written."""
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_NOT_FOUND",
        tracking_number="TN_GHOST",
        package_id="PKG_GHOST",
    )
    proxy = FakeProxy()
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    # There must be ≥1 failed row (the ghost order); the production DB
    # may also contribute unrelated rows, so we assert on the issue +
    # the absence of a Shipment for the ghost package specifically.
    assert result.rows_failed >= 1
    ghost_issues = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == "tiktok.logistics",
            SyncIssue.external_id == "PKG_GHOST",
        )
    ).scalars().all()
    assert len(ghost_issues) == 1
    assert ghost_issues[0].issue_type == "UNKNOWN_ORDER"
    ghost_shipments = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG_GHOST")
    ).scalars().all()
    assert ghost_shipments == []


def test_logistics_fetch_events_disabled_skips_tracking(db_session) -> None:
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_L",
        tracking_number="TN2",
        package_id="PKG2",
    )
    proxy = FakeProxy()
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
            "fetch_events": False,
        },
    )
    assert result.rows_inserted == 1
    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG2")
    ).scalar_one()
    # No tracking endpoint was called.
    assert all("tracking" not in path for _method, path in proxy.calls)
    events_for_shipment = db_session.execute(
        select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id)
    ).scalars().all()
    assert events_for_shipment == []


def test_logistics_skips_orders_without_tracking_number(db_session) -> None:
    """RawRecords whose payload lacks tracking_number are not selected
    as targets — the per-order tracking call is pointless without a tn."""
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_L",
        tracking_number="TN_OK",
        package_id="PKG_OK",
    )
    # Second record with no tracking_number — should be ignored.
    RawRecord(
        endpoint="/order/202309/orders/search",
        external_id="TEST_SO_NO_TN",
        payload={"id": "TEST_SO_NO_TN"},
    )
    db_session.flush()
    proxy = FakeProxy(
        tracking_by_order={
            "TEST_SO_L": _tracking_payload(
                {
                    "action_code": 20101,
                    "description": "Order placed.",
                    "update_time_millis": 1_700_002_100_000,
                },
            ),
        },
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted >= 1
    # The order with a tracking_number was hit on the wire; the one
    # without was NOT hit.
    assert any("TEST_SO_L" in path for _m, path in proxy.calls)
    assert not any("TEST_SO_NO_TN" in path for _m, path in proxy.calls)
    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG_OK")
    ).scalar_one()
    assert shipment.tracking_number == "TN_OK"


def test_logistics_excludes_terminal_status_orders(db_session) -> None:
    """Orders whose Shipment is already DELIVERED / RETURNED_TO_SELLER
    are excluded — their tracking never changes."""
    account = _make_account_with_order(
        db_session, shop_id="TEST_TT_LOG_SHOP_TERM",
        external_order_id="TEST_SO_DONE",
    )
    so_done = db_session.execute(
        select(SalesOrder).where(SalesOrder.external_order_id == "TEST_SO_DONE")
    ).scalar_one()
    # Pre-existing terminal shipment for this order.
    db_session.add(
        Shipment(
            sales_order_id=so_done.id,
            external_package_id="PKG_DONE",
            status="DELIVERED",
        )
    )
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_DONE",
        tracking_number="TN_DONE",
        package_id="PKG_DONE_RR",
    )
    # Also seed a fresh, non-terminal order under the same shop.
    so_open = SalesOrder(
        channel_account_id=account.id,
        external_order_id="TEST_SO_OPEN",
        status="SHIPPED",
    )
    db_session.add(so_open)
    db_session.flush()
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_OPEN",
        tracking_number="TN_OPEN",
        package_id="PKG_OPEN",
    )
    proxy = FakeProxy(
        tracking_by_order={
            "TEST_SO_OPEN": _tracking_payload(
                {
                    "action_code": 20101,
                    "description": "Order placed.",
                    "update_time_millis": 1_700_002_100_000,
                },
            ),
        },
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    # Only TEST_SO_OPEN was hit on the wire; TEST_SO_DONE was excluded.
    assert any("TEST_SO_OPEN" in path for _m, path in proxy.calls)
    assert not any("TEST_SO_DONE" in path for _m, path in proxy.calls)
    # The terminal order's Shipment row was NOT updated (status still
    # DELIVERED, not the active event classification).
    done_ship = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG_DONE")
    ).scalar_one()
    assert done_ship.status == "DELIVERED"


def test_logistics_classifies_delivered_status(db_session) -> None:
    """An action_code of 50101 marks the shipment DELIVERED."""
    account = _make_account_with_order(db_session)
    _seed_orders_raw_record(
        db_session,
        external_order_id="TEST_SO_L",
        tracking_number="TN_D",
        package_id="PKG_D",
    )
    proxy = FakeProxy(
        tracking_by_order={
            "TEST_SO_L": _tracking_payload(
                {
                    "action_code": 20101,
                    "description": "Order placed.",
                    "update_time_millis": 1_700_002_100_000,
                },
                {
                    "action_code": 50101,
                    "description": "Delivered.",
                    "update_time_millis": 1_700_002_200_000,
                },
            ),
        },
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_inserted == 1
    shipment = db_session.execute(
        select(Shipment).where(Shipment.external_package_id == "PKG_D")
    ).scalar_one()
    assert shipment.status == "DELIVERED"