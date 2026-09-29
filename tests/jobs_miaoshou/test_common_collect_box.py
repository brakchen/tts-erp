"""Tests for tts_erp_v2.jobs.miaoshou.common_collect_box."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tts_erp_v2.db.models.integration import SyncIssue, SyncJob
from tts_erp_v2.db.models.procurement import ProcurementProduct
from tts_erp_v2.jobs.miaoshou.common_collect_box import sync_common_collect_box

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def _box_payload(
    common_id: str,
    *,
    price: float | str | None = 34.0,
    min_price: float | str | None = 34.0,
    max_price: float | str | None = 34.0,
    source_item_id: str = "1053836757309",
    status: str = "success",
) -> dict:
    src = {
        "source": "1688",
        "sourceSite": "",
        "sourceItemId": source_item_id,
        "sourceItemUrl": f"http://detail.1688.com/offer/{source_item_id}.html",
    }
    item: dict = {
        "commonCollectBoxDetailId": common_id,
        "title": f"TEST source {common_id}",
        "price": price,
        "minSkuPrice": min_price,
        "maxSkuPrice": max_price,
        "status": status,
        "gmtCreate": "2026-08-01 10:00:00",
        "gmtModified": "2026-08-15 10:00:00",
        "sourceList": [src],
    }
    return item


def _install_pages(fake_client, pages: list[dict]) -> None:
    """pages[0] is the page-1 response; every later page is empty."""

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("pageNo", body.get("page", 1)))
        if page <= len(pages):
            return pages[page - 1]
        return {"result": "success", "data": {"detailList": [], "total": 0}}

    fake_client.install(side_effect)


def _latest_job(db_session, job_name: str) -> SyncJob:
    return db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == job_name)
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one()


def test_sync_common_collect_box_upserts_source_price_columns(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Page 1 rows land in procurement_products with 货源价 columns filled
    from `price` / `minSkuPrice` / `maxSkuPrice` and sourceList→source fields."""
    _install_pages(
        fake_client,
        [
            {
                "result": "success",
                "data": {
                    "detailList": [
                        _box_payload("ccbd_1"),
                        _box_payload(
                            "ccbd_2",
                            price="29.50",
                            min_price=29.50,
                            max_price=42.00,
                            source_item_id="999",
                        ),
                    ],
                    "total": 2,
                },
            }
        ],
    )

    result = sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()

    assert result["products_upserted"] == 2
    assert result["issues"] == 0
    job = _latest_job(db_session, "miaoshou.common_collect_box")
    assert job.status == "succeeded"

    p1 = db_session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.external_product_id == "ccbd_1"
        )
    ).scalar_one()
    assert p1.title == "TEST source ccbd_1"
    assert p1.source_platform == "1688"
    assert p1.source_item_id == "1053836757309"
    assert p1.source_item_url == "http://detail.1688.com/offer/1053836757309.html"
    assert p1.source_unit_cost == Decimal("34.0000")
    assert p1.source_min_unit_cost == Decimal("34.0000")
    assert p1.source_max_unit_cost == Decimal("34.0000")

    p2 = db_session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.external_product_id == "ccbd_2"
        )
    ).scalar_one()
    assert p2.source_unit_cost == Decimal("29.5000")
    assert p2.source_max_unit_cost == Decimal("42.0000")


def test_sync_common_collect_box_price_falls_back_to_min_sku(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """When `price` is absent, source_unit_cost falls back to minSkuPrice."""
    _install_pages(
        fake_client,
        [
            {
                "result": "success",
                "data": {
                    "detailList": [
                        _box_payload(
                            "ccbd_no_price", price=None, min_price=19.9, max_price=25.0
                        ),
                    ],
                    "total": 1,
                },
            }
        ],
    )
    sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()
    p = db_session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.external_product_id == "ccbd_no_price"
        )
    ).scalar_one()
    assert p.source_unit_cost == Decimal("19.9000")
    assert p.source_min_unit_cost == Decimal("19.9000")


def test_sync_common_collect_box_empty_response(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {"result": "success", "data": {"detailList": [], "total": 0}}
    )
    result = sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()
    assert result["products_upserted"] == 0
    assert result["items_seen"] == 0


def test_sync_common_collect_box_missing_id_records_issue(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Rows without commonCollectBoxDetailId cannot be keyed → sync_issue."""
    bad = _box_payload("ccbd_x")
    bad.pop("commonCollectBoxDetailId")
    _install_pages(
        fake_client,
        [
            {
                "result": "success",
                "data": {"detailList": [bad, _box_payload("ccbd_ok")], "total": 2},
            }
        ],
    )
    result = sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()
    assert result["products_upserted"] == 1
    assert result["issues"] == 1
    issue = db_session.execute(
        select(SyncIssue).where(
            SyncIssue.job_name == "miaoshou.common_collect_box",
            SyncIssue.issue_type == "COMMON_BOX_MISSING_ID",
        )
    ).scalar_one()
    assert issue is not None


def test_sync_common_collect_box_idempotent_price_update(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """Same commonCollectBoxDetailId across runs updates in place (no dup row)."""
    page = {
        "result": "success",
        "data": {"detailList": [_box_payload("ccbd_1", price=30.0)], "total": 1},
    }
    _install_pages(fake_client, [page])
    sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()
    page2 = {
        "result": "success",
        "data": {"detailList": [_box_payload("ccbd_1", price=31.5)], "total": 1},
    }
    _install_pages(fake_client, [page2])
    sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()

    rows = db_session.execute(
        select(func.count())
        .select_from(ProcurementProduct)
        .where(ProcurementProduct.external_product_id == "ccbd_1")
    ).scalar_one()
    assert rows == 1
    p = db_session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.external_product_id == "ccbd_1"
        )
    ).scalar_one()
    assert p.source_unit_cost == Decimal("31.5000")


def test_sync_common_collect_box_empty_source_list_tolerated(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    """sourceList may be empty — row still lands with NULL source fields."""
    item = _box_payload("ccbd_nosrc", source_item_id="x")
    item["sourceList"] = []
    _install_pages(
        fake_client,
        [{"result": "success", "data": {"detailList": [item], "total": 1}}],
    )
    result = sync_common_collect_box(db_session, client=fake_client)
    db_session.commit()
    assert result["products_upserted"] == 1
    p = db_session.execute(
        select(ProcurementProduct).where(
            ProcurementProduct.external_product_id == "ccbd_nosrc"
        )
    ).scalar_one()
    assert p.source_item_id is None
    assert p.source_unit_cost == Decimal("34.0000")
