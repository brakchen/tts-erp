"""TDD tests for jobs.tiktok.logistics — shipments + tracking events."""
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
    def __init__(self, *, shipments_pages, tracking_pages=None):
        self.shipments_pages = list(shipments_pages)
        self.tracking_pages = list(tracking_pages or [])
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if "shipments" in path:
            return (
                self.shipments_pages.pop(0)
                if self.shipments_pages
                else {"code": 0, "data": {"shipments": []}}
            )
        if "tracking" in path:
            return (
                self.tracking_pages.pop(0)
                if self.tracking_pages
                else {"code": 0, "data": {"events": []}}
            )
        return {"code": 404}


def _make_account_with_order(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_LOG_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_TT_LOG_SHOP",
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    so = SalesOrder(
        channel_account_id=acct.id,
        external_order_id="TEST_SO_L",
        status="SHIPPED",
    )
    session.add(so)
    session.flush()
    return acct


def _shipment_payload(pkg_id: str, order_id: str, *, tracking_number: str = "TN1", shipped_time_ms: int = 1_700_002_000_000):
    return {
        "package_id": pkg_id,
        "order_id": order_id,
        "tracking_number": tracking_number,
        "shipping_provider_id": "UPS",
        "shipping_provider_name": "UPS",
        "status": "SHIPPED",
        "shipped_time_ms": shipped_time_ms,
    }


def test_logistics_writes_shipments_and_tracks_watermark(db_session) -> None:
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        shipments_pages=[
            {
                "code": 0,
                "data": {
                    "shipments": [
                        _shipment_payload("PKG1", "TEST_SO_L"),
                    ],
                    "next_page_token": "",
                },
            }
        ],
        tracking_pages=[
            {
                "code": 0,
                "data": {
                    "events": [
                        {
                            "event_key": "E1",
                            "description": "Order placed.",
                            "event_time_ms": 1_700_002_100_000,
                            "location": "HCMC",
                        }
                    ]
                },
            }
        ],
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
    assert shipment.external_package_id == "PKG1"
    events = db_session.execute(
        select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id)
    ).scalars().all()
    assert len(events) == 1
    assert events[0].external_event_key == "E1"
    # Watermark advanced to shipped_time_ms (or larger of seen times)
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.logistics", scope=account.external_account_id
    )
    assert cursor == 1_700_002_100_000


def test_logistics_unknown_order_writes_sync_issue(db_session) -> None:
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        shipments_pages=[
            {
                "code": 0,
                "data": {
                    "shipments": [
                        _shipment_payload("PKG_BAD", "TEST_SO_NOT_FOUND"),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.logistics",
        credential_id=account.credential_id,
        inner=logistics_job.run,
        inner_kwargs={"proxy_call": proxy, "shop_id": account.external_account_id},
    )
    assert result.rows_failed == 1
    issue = db_session.execute(select(SyncIssue).where(SyncIssue.job_name == "tiktok.logistics")).scalar_one()
    assert issue.issue_type == "UNKNOWN_ORDER"


def test_logistics_fetch_events_disabled_skips_tracking(db_session) -> None:
    account = _make_account_with_order(db_session)
    proxy = FakeProxy(
        shipments_pages=[
            {
                "code": 0,
                "data": {
                    "shipments": [_shipment_payload("PKG2", "TEST_SO_L")],
                    "next_page_token": "",
                },
            }
        ]
    )
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
    events_for_shipment = db_session.execute(
        select(TrackingEvent).where(TrackingEvent.shipment_id == shipment.id)
    ).scalars().all()
    assert events_for_shipment == []
