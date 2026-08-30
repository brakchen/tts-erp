"""Tests for tts_erp_v2.jobs.miaoshou.shops."""
from __future__ import annotations

from sqlalchemy import select

from tts_erp_v2.db.models.integration import SyncJob
from tts_erp_v2.jobs.miaoshou.shops import sync_shops


def _shop_payload(shop_id: str, *, name: str = "TEST shop") -> dict:
    return {
        "shopId": shop_id,
        "name": name,
        "platform": "wanshifu",
        "isActive": True,
        "gmtCreate": "2026-01-01 00:00:00",
        "gmtModified": "2026-08-01 12:00:00",
    }


def test_sync_shops_writes_shops_and_sync_job_row(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    def side_effect(*, path, body, **_kwargs):
        page = int(body.get("pageNo", 1))
        if page == 1:
            return {
                "result": "success",
                "data": {"shopList": [_shop_payload("17060852")]},
            }
        return {"result": "success", "data": {"shopList": []}}

    fake_client.install(side_effect)

    result = sync_shops(db_session, client=fake_client)
    db_session.commit()

    assert result["upserted"] == 1
    assert result["shops_seen"] == 1
    job = db_session.execute(
        select(SyncJob).where(SyncJob.job_name == "miaoshou.shops")
    ).scalar_one()
    assert job.status == "succeeded"


def test_sync_shops_empty_response_is_noop(
    db_session, fake_client, miaoshou_credentials_row
) -> None:
    fake_client.install(
        lambda **_: {"result": "success", "data": {"shopList": []}}
    )
    result = sync_shops(db_session, client=fake_client)
    db_session.commit()
    assert result["upserted"] == 0
    assert result["shops_seen"] == 0