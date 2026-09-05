"""Coverage for ``analytics.ad_product_links`` view (ad_raw-derived).

The view exposes the 广告计划(campaign) ↔ 商品(SPU) 关联 from
``post_product_list`` raw dumps, aggregated per (campaign, product) over
every captured day:

- association keys: seller_id / advertiser_id / campaign_id / product_id
- window metadata: observed_days / first_day / last_day
- metrics: order_sku_total (出单数), real_cost_total (广告消耗),
  order_value_total (出单 GMV)
- ERP enrichment: shop_pk / spu_pk LEFT JOINed to
  commerce (NULL when the SPU isn't in the synced TikTok catalog)

Semantics/derivation documented in biz-doc/analytics/ad-product-links-view.md.

Data isolation: TEST_-prefixed seller/advertiser/campaign/product ids, wiped
before and after each test (analytics + commerce rows this file touches).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_ENDPOINT_PRODUCT = "/oec_ads/shopping/v1/oec/stat/post_product_list"

_SELLER = "TEST_SELLER_1"
_ADVERTISER = "TEST_ADVERTISER_1"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_rows(db_engine):
    """Wipe analytics.ad_* + commerce TEST_ rows before and after each test.

    The shared api/conftest.py autouse doesn't apply under tests/analytics/
    (conftest scope), so mirror tests/analytics/test_repository.py's pattern.
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    """只清 ad_raw + commerce TEST_ 行（2026-09-05 reorg：ad_records /
    ad_daily_completeness 等派生表已随 migration 0007 drop）。"""
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(text("DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'"))
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.products_spu "
                "WHERE spu_id LIKE :prefix"
            ),
            {"prefix": "TEST_%"},
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.shops "
                "WHERE shop_id LIKE :prefix"
            ),
            {"prefix": "TEST_%"},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dump_day(db_session, day: str, campaign_id: str, table_rows: list[dict]) -> None:
    """Insert one analytics.ad_raw row (post_product_list) for a campaign/day."""
    db_session.execute(
        text(
            """
            INSERT INTO analytics.ad_raw (
                idempotency_key, seller_id, advertiser_id, endpoint, method,
                day, campaign_id, request, response, captured_at, source,
                protocol_version, schema_version
            ) VALUES (
                :idem, :seller, :advertiser, :endpoint, 'POST',
                :day, :campaign,
                CAST(:request AS JSONB), CAST(:response AS JSONB),
                now(), 'TEST', 2, 1
            )
            """
        ),
        {
            "idem": f"TEST_IDEM_{day}_{campaign_id}",
            "seller": _SELLER,
            "advertiser": _ADVERTISER,
            "endpoint": _ENDPOINT_PRODUCT,
            "day": day,
            "campaign": campaign_id,
            "request": json.dumps({"url": "https://x/post_product_list", "body": {}}),
            "response": json.dumps(
                {
                    "status": 200,
                    "body": {"code": 0, "data": {"table": table_rows}},
                }
            ),
        },
    )
    db_session.flush()


def _row(
    product_id: str,
    *,
    cost: str = "1.50",
    orders: str = "2",
    gmv: str = "10.00",
    name: str = "TEST 商品",
    status: str = "available",
) -> dict:
    return {
        "product_id": product_id,
        "product_name": name,
        "product_status": status,
        "mixed_real_cost": cost,
        "onsite_roi2_shopping_sku": orders,
        "onsite_roi2_shopping_value": gmv,
        "gmv_max_bid_type": "1",
        "onsite_mixed_real_roi2_shopping": "6.67",
        "mixed_real_cost_per_onsite_roi2_shopping_sku": "0.75",
    }


def _view_rows(db_session) -> list[dict]:
    """View 行只取本次测试的 TEST seller（视图本身是全库派生，不按 TEST 过滤）。"""
    return [
        dict(r)
        for r in db_session.execute(
            text(
                "SELECT * FROM analytics.ad_product_links "
                "WHERE seller_id = :seller "
                "ORDER BY campaign_id, product_id"
            ),
            {"seller": _SELLER},
        ).mappings()
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_view_aggregates_spend_and_orders_across_days(db_session):
    """同一 campaign×SPU 跨多天 → 1 行,出单量/消耗/GMV 按天合计,窗口元数据正确。"""
    camp = "TEST_CAMPAIGN_1"
    _dump_day(
        db_session,
        "2026-08-01",
        camp,
        [_row("TEST_SPU_1", cost="10.00", orders="3", gmv="100.00")],
    )
    _dump_day(
        db_session,
        "2026-08-02",
        camp,
        [_row("TEST_SPU_1", cost="20.50", orders="5", gmv="250.00")],
    )

    rows = _view_rows(db_session)
    assert len(rows) == 1
    r = rows[0]
    assert r["seller_id"] == _SELLER
    assert r["advertiser_id"] == _ADVERTISER
    assert r["campaign_id"] == camp
    assert r["product_id"] == "TEST_SPU_1"
    assert r["observed_days"] == 2
    assert r["first_day"].isoformat() == "2026-08-01"
    assert r["last_day"].isoformat() == "2026-08-02"
    assert float(r["real_cost_total"]) == 30.50
    assert r["order_sku_total"] == 8
    assert float(r["order_value_total"]) == 350.00


def test_view_rows_are_per_campaign_product_pair(db_session):
    """不同 campaign / 不同 SPU 各自成行;多商品 campaign 不出双计。"""
    # 同一 (campaign, day) 的多个 SPU 在同一条 dump 的 table 数组里
    _dump_day(
        db_session,
        "2026-08-01",
        "TEST_CAMP_A",
        [
            _row("TEST_SPU_1", cost="1.00", orders="1", gmv="5.00"),
            _row("TEST_SPU_2", cost="2.00", orders="2", gmv="9.00"),
        ],
    )
    _dump_day(
        db_session,
        "2026-08-01",
        "TEST_CAMP_B",
        [_row("TEST_SPU_1", cost="4.00", orders="4", gmv="16.00")],
    )

    rows = _view_rows(db_session)
    assert [(r["campaign_id"], r["product_id"]) for r in rows] == [
        ("TEST_CAMP_A", "TEST_SPU_1"),
        ("TEST_CAMP_A", "TEST_SPU_2"),
        ("TEST_CAMP_B", "TEST_SPU_1"),
    ]
    assert float(rows[0]["real_cost_total"]) == 1.00


def test_view_latest_product_name_and_status_win(db_session):
    """名称/上架状态取观测期最后一天那一行的值。"""
    camp = "TEST_CAMPAIGN_1"
    _dump_day(
        db_session,
        "2026-08-01",
        camp,
        [_row("TEST_SPU_1", name="旧名", status="available")],
    )
    _dump_day(
        db_session,
        "2026-08-02",
        camp,
        [_row("TEST_SPU_1", name="新名", status="unavailable")],
    )

    r = _view_rows(db_session)[0]
    assert r["product_name"] == "新名"
    assert r["product_status"] == "unavailable"


def test_view_handles_missing_or_dirty_metric_fields(db_session):
    """缺失/非数字业绩字段按 0 计,不抛错;无指标旧 dump 仍保留关联行。"""
    camp = "TEST_CAMPAIGN_1"
    # 无任何业绩字段的精简行(修复前 schema)——只表达"挂了哪些商品"
    _dump_day(db_session, "2026-08-01", camp, [{"product_id": "TEST_SPU_1"}])
    # 脏数值:空串 / 非数字占位
    _dump_day(
        db_session,
        "2026-08-02",
        camp,
        [_row("TEST_SPU_1", cost="-", orders="", gmv="abc")],
    )

    rows = _view_rows(db_session)
    assert len(rows) == 1
    r = rows[0]
    assert r["product_id"] == "TEST_SPU_1"
    assert r["observed_days"] == 2
    assert float(r["real_cost_total"]) == 0.0
    assert r["order_sku_total"] == 0
    assert float(r["order_value_total"]) == 0.0


def test_view_left_joins_erp_channel_product_when_known(db_session):
    """SPU 已在 commerce.products_spu 目录 → 带出内部 channel ids,否则 NULL。"""
    # ERP 目录里登记一个 TEST 商品
    with db_session.begin_nested():
        acct_id = db_session.execute(
            text(
                "INSERT INTO commerce.shops (platform, shop_id) "
                "VALUES ('tiktok', :ext) RETURNING id"
            ),
            {"ext": _SELLER},
        ).scalar_one()
        db_session.execute(
            text(
                "INSERT INTO commerce.products_spu (shop_pk, spu_id, title) "
                "VALUES (:acct, 'TEST_SPU_1', 'TEST 目录商品')"
            ),
            {"acct": acct_id},
        )

    camp = "TEST_CAMPAIGN_1"
    _dump_day(
        db_session,
        "2026-08-01",
        camp,
        [_row("TEST_SPU_1"), _row("TEST_SPU_2")],
    )

    rows = {r["product_id"]: r for r in _view_rows(db_session)}
    assert rows["TEST_SPU_1"]["shop_pk"] == acct_id
    assert rows["TEST_SPU_1"]["spu_pk"] is not None
    # 同 seller 的另一 SPU 没在目录里：能带出渠道账户（seller 级），但商品 key 为 NULL
    assert rows["TEST_SPU_2"]["shop_pk"] == acct_id
    assert rows["TEST_SPU_2"]["spu_pk"] is None
