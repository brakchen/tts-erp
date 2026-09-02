"""Tests for tts_erp_v2.jobs.miaoshou.collect_box."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import SyncJob
from tts_erp_v2.jobs.miaoshou.collect_box import sync_collect_box

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def _box_payload(detail_id: str, *, status: str = "success") -> dict:
    return {
        "itemId": detail_id,
        "itemTitle": f"TEST box {detail_id}",
        "price": "5.00",
        "currency": "CNY",
        "quantity": 1,
        "status": status,
        "gmtCreate": "2026-08-01 10:00:00",
        "gmtModified": "2026-08-15 10:00:00",
    }


def test_sync_collect_box_walks_pages(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    calls: list[int] = []

    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("pageNo", body.get("page", 1)))
        calls.append(page)
        if page == 1:
            return {
                "result": "success",
                "data": {
                    "collectBoxDetailList": [
                        _box_payload("cbd_1"),
                        _box_payload("cbd_2"),
                    ],
                    "totalPage": 1,
                    "total": 2,
                },
            }
        return {
            "result": "success",
            "data": {"collectBoxDetailList": [], "totalPage": 1, "total": 2},
        }

    fake_client.install(side_effect)

    result = sync_collect_box(db_session, client=fake_client)
    db_session.commit()

    assert result["products_upserted"] >= 2
    job = db_session.execute(
        select(SyncJob)
        .where(SyncJob.job_name == "miaoshou.collect_box")
        .order_by(SyncJob.id.desc())
        .limit(1)
    ).scalar_one()
    assert job.status == "succeeded"


def test_sync_collect_box_empty_response(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {
            "result": "success",
            "data": {"collectBoxDetailList": [], "total": 0},
        }
    )
    result = sync_collect_box(db_session, client=fake_client)
    db_session.commit()
    assert result["products_upserted"] == 0
    assert result["items_seen"] == 0
