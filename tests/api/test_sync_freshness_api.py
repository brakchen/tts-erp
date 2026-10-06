"""GET /v2/sync/freshness — SPU ROI 页头四类同步时间。

广告取该店 plugin.ad_daily / ad_today 最近写入；订单和物流优先取
extra.shop_id 归因的成功 sync_jobs，没有归因行时回退 sync_cursors；
妙手取注册表内 miaoshou.* 最近一次成功。只断言 TEST_ 行，不碰真实作业。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2.sync_status import _freshness_severity
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import SyncCursor, SyncJob
from tts_erp_v2.db.models.plugin import AdDaily, AdToday

SHOP_ID = "TEST_freshness_shop"
PROBE = "TEST_sync_freshness"

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _aware(minutes_ago: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes_ago)


@pytest.fixture()
def freshness_shop(db_engine) -> Iterator[int]:
    with Session(db_engine) as sess:
        _wipe(sess)
        shop = ChannelAccount(platform="tiktok", shop_id=SHOP_ID, account_name="TEST_freshness")
        sess.add(shop)
        sess.commit()
        shop_pk = shop.id
    yield shop_pk
    with Session(db_engine) as sess:
        _wipe(sess)
        sess.commit()


def _wipe(sess: Session) -> None:
    sess.execute(delete(SyncJob).where(SyncJob.extra["probe"].astext == PROBE))
    sess.execute(delete(SyncCursor).where(SyncCursor.scope == SHOP_ID))
    sess.execute(delete(AdDaily).where(AdDaily.seller_id == SHOP_ID))
    sess.execute(delete(AdToday).where(AdToday.seller_id == SHOP_ID))
    sess.execute(delete(ChannelAccount).where(ChannelAccount.shop_id == SHOP_ID))


def _job(
    *,
    job_name: str,
    status: str,
    started_at: datetime,
    shop_id: str | None = SHOP_ID,
) -> SyncJob:
    extra: dict[str, str] = {"probe": PROBE}
    if shop_id is not None:
        extra["shop_id"] = shop_id
    return SyncJob(
        job_name=job_name,
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=20),
        extra=extra,
    )


def test_freshness_severity_follows_cycle_rule() -> None:
    now = datetime.now(UTC)
    assert _freshness_severity(now - timedelta(minutes=5), 600, now) == "ok"
    assert _freshness_severity(now - timedelta(minutes=15), 600, now) == "warn"
    assert _freshness_severity(now - timedelta(minutes=25), 600, now) == "crit"
    assert _freshness_severity(None, 600, now) == "unknown"
    naive = (now - timedelta(minutes=5)).replace(tzinfo=None)
    assert _freshness_severity(naive, 600, now) == "ok"


def test_freshness_requires_auth(api_client) -> None:
    response = api_client.get("/v2/sync/freshness?shop_pk=1")
    assert response.status_code == 401, response.text


def test_freshness_unknown_shop_is_404(api_client, readonly_key) -> None:
    response = api_client.get(
        "/v2/sync/freshness",
        params={"shop_pk": 2_000_000_000},
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 404, response.text


def test_freshness_reports_shop_job_ad_rows_and_miaoshou(
    api_client, readonly_key, db_engine, freshness_shop: int
) -> None:
    now = datetime.now(UTC)
    ad_at = now - timedelta(minutes=4)
    with Session(db_engine) as sess:
        sess.add_all(
            [
                _job(job_name="tiktok.orders", status="succeeded", started_at=now - timedelta(minutes=6)),
                _job(
                    job_name="tiktok.orders",
                    status="failed",
                    started_at=now - timedelta(hours=3),
                ),
                _job(
                    job_name="tiktok.logistics",
                    status="succeeded",
                    started_at=now - timedelta(minutes=25),
                ),
                _job(
                    job_name="miaoshou.packages",
                    status="succeeded",
                    started_at=now + timedelta(minutes=5),
                    shop_id=None,
                ),
                AdDaily(
                    seller_id=SHOP_ID,
                    advertiser_id="TEST_adv",
                    campaign_id="TEST_c",
                    product_id="TEST_p",
                    endpoint="TEST_ep",
                    day=date(2026, 10, 1),
                    updated_at=ad_at - timedelta(hours=2),
                ),
                AdToday(
                    seller_id=SHOP_ID,
                    advertiser_id="TEST_adv",
                    campaign_id="TEST_c",
                    product_id="TEST_p",
                    endpoint="TEST_ep",
                    day=date(2026, 10, 5),
                    updated_at=ad_at,
                ),
            ]
        )
        sess.commit()

    response = api_client.get(
        "/v2/sync/freshness",
        params={"shop_pk": freshness_shop},
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shop_pk"] == freshness_shop
    assert body["shop_id"] == SHOP_ID
    assert body["server_time"].endswith(("+00:00", "Z"))
    by_key = {item["key"]: item for item in body["sources"]}
    assert list(by_key) == ["ads", "orders", "logistics", "miaoshou"]

    ads = by_key["ads"]
    assert ads["basis"] == "rows"
    assert ads["severity"] == "ok"
    assert ads["synced_at"].endswith(("+00:00", "Z"))
    assert abs(_parse(ads["synced_at"]) - ad_at) < timedelta(seconds=2)

    orders = by_key["orders"]
    assert orders["basis"] == "job"
    assert orders["job_name"] == "tiktok.orders"
    assert orders["last_status"] == "succeeded"
    assert orders["severity"] == "ok"
    assert "最近一次运行失败" not in (orders["detail"] or "")

    logistics = by_key["logistics"]
    assert logistics["basis"] == "job"
    assert logistics["severity"] == "crit"

    miaoshou = by_key["miaoshou"]
    assert miaoshou["scope"] == "system"
    assert miaoshou["job_name"] == "miaoshou.packages"
    assert miaoshou["basis"] == "job"
    assert abs(_parse(miaoshou["synced_at"]) - (now + timedelta(minutes=5, seconds=20))) < timedelta(seconds=2)


def test_freshness_falls_back_to_cursor_and_flags_failed_run(
    api_client, readonly_key, db_engine, freshness_shop: int
) -> None:
    now = datetime.now(UTC)
    cursor_at = (now - timedelta(minutes=8)).replace(tzinfo=None)
    with Session(db_engine) as sess:
        sess.add(
            SyncCursor(
                job_name="tiktok.orders",
                scope=SHOP_ID,
                cursor_epoch_ms=1,
                updated_at=cursor_at,
            )
        )
        sess.add(
            _job(
                job_name="tiktok.logistics",
                status="failed",
                started_at=now - timedelta(minutes=2),
            )
        )
        sess.add(
            _job(
                job_name="tiktok.orders",
                status="succeeded",
                started_at=now - timedelta(minutes=3),
                shop_id="TEST_other_shop",
            )
        )
        sess.commit()

    response = api_client.get(
        "/v2/sync/freshness",
        params={"shop_pk": freshness_shop},
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    by_key = {item["key"]: item for item in response.json()["sources"]}
    orders = by_key["orders"]
    assert orders["basis"] == "cursor"
    assert orders["severity"] == "ok"
    assert abs(_parse(orders["synced_at"]) - cursor_at.replace(tzinfo=UTC)) < timedelta(seconds=2)

    logistics = by_key["logistics"]
    assert logistics["last_status"] == "failed"
    assert logistics["severity"] in {"warn", "crit"}
    assert "最近一次运行失败" in logistics["detail"]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
