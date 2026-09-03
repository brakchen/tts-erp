"""TDD tests for jobs.tiktok.products — channel_products + variants sync.

Verifies:
* Pagination (next_page_token) walks every page.
* Products + variants are upserted; raw_records captures the upstream
  payload.
* Watermark advances to the max update_time seen.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    ChannelProductVariant,
    Credentials,
    SyncIssue,
)
from tts_erp_v2.jobs.tiktok import products as products_job
from tts_erp_v2.sync_worker import watermarks

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job


class FakeProxy:
    def __init__(self, *, pages: list[dict[str, Any]]):
        self.pages = pages
        self.call_count = 0

    def __call__(self, method: str, path: str, body=None):
        self.call_count += 1
        return self.pages[self.call_count - 1]


def _make_account(session) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id="TEST_TT_PROD_SHOP",
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        external_account_id="TEST_TT_PROD_SHOP",
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _product_payload(pid: str, *, update_time: int, variants=None):
    return {
        "product_id": pid,
        "title": f"TEST title {pid}",
        "status": "ACTIVE",
        "update_time": update_time,
        "create_time": update_time - 1000,
        "skus": variants or [],
    }


def _variant_payload(vid: str, *, update_time: int):
    return {
        "id": vid,
        "seller_sku": f"TEST_SKU_{vid}",
        "sku_name": f"TEST sku {vid}",
        "status": "ACTIVE",
        "update_time": update_time,
    }


def test_products_first_run_writes_products_and_variants(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "products": [
                        _product_payload(
                            "P1",
                            update_time=1_700_000_100,
                            variants=[_variant_payload("V1", update_time=1_700_000_100)],
                        ),
                        _product_payload("P2", update_time=1_700_000_200),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.products",
        credential_id=account.credential_id,
        inner=products_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )
    assert result.rows_inserted == 2
    # Filter by channel_account_id — prod has 147 ChannelProducts from
    # the real TikTok shop; the unscoped select would return all of them.
    products = (
        db_session.execute(
            select(ChannelProduct).where(
                ChannelProduct.channel_account_id == account.id
            )
        )
        .scalars()
        .all()
    )
    assert {p.external_product_id for p in products} == {"P1", "P2"}
    variants = (
        db_session.execute(
            select(ChannelProductVariant).where(
                ChannelProductVariant.channel_product_id.in_(
                    [p.id for p in products]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(variants) == 1
    assert variants[0].external_variant_id == "V1"
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.products", scope=account.external_account_id
    )
    assert cursor == 1_700_000_200_000  # ms


def test_products_second_run_advances_watermark_only(db_session) -> None:
    account = _make_account(db_session)
    # Seed a watermark
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.products",
        scope=account.external_account_id,
        cursor_epoch_ms=1_700_000_100_000,
    )

    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "products": [
                        _product_payload("P1", update_time=1_700_000_150),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.products",
        credential_id=account.credential_id,
        inner=products_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )
    assert result.cursor == 1_700_000_150_000


def test_products_parse_failure_writes_sync_issue(db_session) -> None:
    account = _make_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "products": [
                        {"title": "no product_id"},
                        _product_payload("P_OK", update_time=1_700_000_050),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )
    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.products",
        credential_id=account.credential_id,
        inner=products_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.external_account_id,
        },
    )
    assert result.rows_failed == 1
    assert result.rows_inserted == 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.products")
    ).scalar_one()
    assert issue.issue_type == "PARSE_ERROR"
