"""TDD tests for jobs.tiktok.products — products_spu + variants sync.

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
from tts_erp_v2.sync_worker.job_runner import run_with_sync_job

pytestmark = [pytest.mark.domain_commerce, pytest.mark.layer_integration]


class FakeProxy:
    def __init__(self, *, pages: list[dict[str, Any]]):
        self.pages = pages
        self.call_count = 0

    def __call__(self, method: str, path: str, body=None):
        self.call_count += 1
        return self.pages[self.call_count - 1]


def _noop_image_fetcher(*, shop_pk: int, product_id: str) -> dict:
    """Default image fetcher for tests that don't care about images.

    Production callers fall back to the real Get Product proxy; tests
    that aren't exercising the image path use this no-op to avoid
    triggering upstream calls with placeholder credentials.
    """
    return {}


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
        shop_id="TEST_TT_PROD_SHOP",
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
                            variants=[
                                _variant_payload("V1", update_time=1_700_000_100)
                            ],
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
            "shop_id": account.shop_id,
            "image_fetcher": _noop_image_fetcher,
        },
    )
    assert result.rows_inserted == 2
    # Filter by shop_pk — prod has 147 ChannelProducts from
    # the real TikTok shop; the unscoped select would return all of them.
    products = (
        db_session.execute(
            select(ChannelProduct).where(
                ChannelProduct.shop_pk == account.id
            )
        )
        .scalars()
        .all()
    )
    assert {p.spu_id for p in products} == {"P1", "P2"}
    variants = (
        db_session.execute(
            select(ChannelProductVariant).where(
                ChannelProductVariant.spu_pk.in_([p.id for p in products])
            )
        )
        .scalars()
        .all()
    )
    assert len(variants) == 1
    assert variants[0].sku_id == "V1"
    cursor = watermarks.get_cursor(
        db_session, job_name="tiktok.products", scope=account.shop_id
    )
    assert cursor == 1_700_000_200_000  # ms


def test_products_second_run_advances_watermark_only(db_session) -> None:
    account = _make_account(db_session)
    # Seed a watermark
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.products",
        scope=account.shop_id,
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
            "shop_id": account.shop_id,
            "image_fetcher": _noop_image_fetcher,
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
            "shop_id": account.shop_id,
            "image_fetcher": _noop_image_fetcher,
        },
    )
    assert result.rows_failed == 1
    assert result.rows_inserted == 1
    issue = db_session.execute(
        select(SyncIssue).where(SyncIssue.job_name == "tiktok.products")
    ).scalar_one()
    assert issue.issue_type == "PARSE_ERROR"


# ─── SPU / SKU main image extraction ───────────────────────────────
#
# Background (2026-09-05): the products search endpoint
# (/product/202309/products/search) does NOT return image fields; the
# detail endpoint (/product/202309/products/{product_id}) does, via
# `main_images[].urls[]` (SPU) and `skus[].sales_attributes[].sku_img.urls[]`
# (per-SKU). sync job has to call Get Product per SPU to populate
# main_image_url / image_url. See tech-doc/adr/… for the full decision.


def test_parse_product_extracts_main_image_url_from_main_images() -> None:
    raw = {
        "id": "P1",
        "title": "test",
        "main_images": [
            {
                "urls": [
                    "https://p16.example.com/a-orig.jpg",
                    "https://p19.example.com/a-orig.jpg",
                ],
                "thumb_urls": ["https://example.com/a-thumb.jpg"],
            }
        ],
    }
    fields = products_job._parse_product(raw)
    # First URL wins (p16 CDN is the conventional primary).
    assert fields["main_image_url"] == "https://p16.example.com/a-orig.jpg"


def test_parse_product_handles_empty_main_images() -> None:
    # /search returns no main_images at all today; parser must not blow up.
    raw = {"id": "P1", "title": "test"}
    fields = products_job._parse_product(raw)
    assert fields["main_image_url"] is None


def test_parse_variant_extracts_image_from_sales_attributes() -> None:
    raw_sku = {
        "id": "V1",
        "seller_sku": "TEST_SKU_V1",
        "sales_attributes": [
            {
                "name": "Màu sắc",
                "value_name": "Trắng",
                "sku_img": {
                    "urls": ["https://p16.example.com/v1-orig.jpg"],
                    "thumb_urls": ["https://example.com/v1-thumb.jpg"],
                },
            }
        ],
    }
    fields = products_job._parse_variant(raw_sku)
    assert fields["image_url"] == "https://p16.example.com/v1-orig.jpg"


def test_parse_variant_handles_empty_sales_attributes() -> None:
    # Single-attribute SKUs may have no sku_img.
    raw_sku = {"id": "V1", "seller_sku": "TEST_SKU_V1"}
    fields = products_job._parse_variant(raw_sku)
    assert fields["image_url"] is None


def test_products_run_calls_image_fetcher_for_each_spu(db_session) -> None:
    """Each SPU from search must trigger one Get Product call; images land."""
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
                            variants=[
                                _variant_payload("V1", update_time=1_700_000_100)
                            ],
                        ),
                        _product_payload("P2", update_time=1_700_000_200),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )
    fetched: list[str] = []

    def fake_fetcher(*, shop_pk: int, product_id: str) -> dict:
        fetched.append(product_id)
        if product_id == "P1":
            return {
                "id": "P1",
                "main_images": [
                    {"urls": ["https://example.com/p1.jpg"], "thumb_urls": []}
                ],
                "skus": [
                    {
                        "id": "V1",
                        "sales_attributes": [
                            {
                                "name": "Màu sắc",
                                "sku_img": {
                                    "urls": ["https://example.com/p1v1.jpg"],
                                    "thumb_urls": [],
                                },
                            }
                        ],
                    }
                ],
            }
        return {
            "id": "P2",
            "main_images": [
                {"urls": ["https://example.com/p2.jpg"], "thumb_urls": []}
            ],
            "skus": [],
        }

    run_with_sync_job(
        db_session,
        job_name="tiktok.products",
        credential_id=account.credential_id,
        inner=products_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.shop_id,
            "image_fetcher": fake_fetcher,
        },
    )

    # Both SPUs were fetched exactly once.
    assert sorted(fetched) == ["P1", "P2"]

    products = (
        db_session.execute(
            select(ChannelProduct).where(ChannelProduct.shop_pk == account.id)
        )
        .scalars()
        .all()
    )
    by_spu = {p.spu_id: p for p in products}
    assert by_spu["P1"].main_image_url == "https://example.com/p1.jpg"
    assert by_spu["P2"].main_image_url == "https://example.com/p2.jpg"

    variants = (
        db_session.execute(
            select(ChannelProductVariant).where(
                ChannelProductVariant.spu_pk.in_([p.id for p in products])
            )
        )
        .scalars()
        .all()
    )
    assert len(variants) == 1
    assert variants[0].image_url == "https://example.com/p1v1.jpg"


def test_products_run_logs_sync_issue_when_get_product_fails(
    db_session,
) -> None:
    """A failing Get Product for one SPU must NOT abort the whole job;
    the SPU still gets its core fields from search, and a SyncIssue is
    written so an operator can investigate."""

    account = _make_account(db_session)
    proxy = FakeProxy(
        pages=[
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "products": [
                        _product_payload("P_OK", update_time=1_700_000_100),
                        _product_payload("P_FAIL", update_time=1_700_000_200),
                    ],
                    "next_page_token": "",
                },
            }
        ]
    )

    def flaky_fetcher(*, shop_pk: int, product_id: str) -> dict:
        if product_id == "P_FAIL":
            raise products_job.ImageFetchError(
                "upstream 500 /product/202309/products/P_FAIL"
            )
        return {
            "id": "P_OK",
            "main_images": [
                {"urls": ["https://example.com/p_ok.jpg"], "thumb_urls": []}
            ],
            "skus": [],
        }

    _, result = run_with_sync_job(
        db_session,
        job_name="tiktok.products",
        credential_id=account.credential_id,
        inner=products_job.run,
        inner_kwargs={
            "proxy_call": proxy,
            "shop_id": account.shop_id,
            "image_fetcher": flaky_fetcher,
        },
    )
    assert result.rows_inserted == 2
    assert result.rows_failed == 0  # parse OK; only image fetch failed

    products = (
        db_session.execute(
            select(ChannelProduct).where(ChannelProduct.shop_pk == account.id)
        )
        .scalars()
        .all()
    )
    by_spu = {p.spu_id: p for p in products}
    assert by_spu["P_OK"].main_image_url == "https://example.com/p_ok.jpg"
    assert by_spu["P_FAIL"].main_image_url is None  # fetch failed → no image

    issues = (
        db_session.execute(
            select(SyncIssue).where(
                SyncIssue.job_name == "tiktok.products",
                SyncIssue.issue_type == "IMAGE_FETCH_ERROR",
            )
        )
        .scalars()
        .all()
    )
    assert len(issues) == 1
    assert issues[0].external_id == "P_FAIL"
