"""Coverage for ``analytics.ad_product_links`` view（live 优先 + daily 过渡回退）。

The view exposes the 广告计划(campaign) ↔ 商品(SPU) 关联 from
``post_product_list`` raw dumps，按 (campaign, product) 聚合。range-aggregate
（Design A，migration 0014）后语义：

- live 有效集：kind='history' 全取；kind='today' 仅当不存在 day_end >= 它的
  history（防跨天推进间隙双计/漏计）。
- 未转换 campaign（无 live 行）回退读 legacy daily 行 —— 口径与迁移前一致。
- observed_days = Σ(day_end-day_start+1)（live 行按覆盖跨度，daily 行计 1）；
  first_day = min(day_start)，last_day = max(day_end)。

Data isolation: TEST_-prefixed seller/advertiser/campaign/product ids。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_ENDPOINT_PRODUCT = "/oec_ads/shopping/v1/oec/stat/post_product_list"

_SELLER = "TEST_SELLER_1"
_ADVERTISER = "TEST_ADVERTISER_1"


@pytest.fixture(autouse=True)
def _wipe_rows(db_engine):
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(text("DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'"))
        conn.execute(
            text("DELETE FROM commerce.products_spu WHERE spu_id LIKE :prefix"),
            {"prefix": "TEST_%"},
        )
        conn.execute(
            text("DELETE FROM commerce.shops WHERE shop_id LIKE :prefix"),
            {"prefix": "TEST_%"},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_raw(
    db_session,
    kind: str,
    day_start: str,
    day_end: str,
    campaign_id: str,
    table_rows: list[dict],
) -> None:
    """Insert one analytics.ad_raw row (post_product_list) for a kind/interval."""
    db_session.execute(
        text(
            """
            INSERT INTO analytics.ad_raw (
                idempotency_key, seller_id, advertiser_id, endpoint, method,
                kind, day_start, day_end, campaign_id, request, response,
                captured_at, source, protocol_version, schema_version
            ) VALUES (
                :idem, :seller, :advertiser, :endpoint, 'POST',
                :kind, CAST(:ds AS date), CAST(:de AS date), :campaign,
                CAST(:request AS JSONB), CAST(:response AS JSONB),
                now(), 'TEST', 3, 2
            )
            """
        ),
        {
            "idem": f"TEST_IDEM_{kind}_{day_start}_{day_end}_{campaign_id}",
            "seller": _SELLER,
            "advertiser": _ADVERTISER,
            "endpoint": _ENDPOINT_PRODUCT,
            "kind": kind,
            "ds": day_start,
            "de": day_end,
            "campaign": campaign_id,
            "request": json.dumps({"url": "https://x/post_product_list", "body": {}}),
            "response": json.dumps(
                {"status": 200, "body": {"code": 0, "data": {"table": table_rows}}}
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


# ─── 过渡期：无 live 行的 campaign 回退 daily（口径与迁移前一致）──────


def test_view_aggregates_spend_and_orders_across_days(db_session):
    """同一 campaign×SPU 跨多天 legacy daily 行 → 1 行,出单量/消耗/GMV 合计。"""
    camp = "TEST_CAMPAIGN_1"
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [_row("TEST_SPU_1", cost="10.00", orders="3", gmv="100.00")])
    _insert_raw(db_session, "daily", "2026-08-02", "2026-08-02", camp,
                [_row("TEST_SPU_1", cost="20.50", orders="5", gmv="250.00")])

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
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", "TEST_CAMP_A", [
        _row("TEST_SPU_1", cost="1.00", orders="1", gmv="5.00"),
        _row("TEST_SPU_2", cost="2.00", orders="2", gmv="9.00"),
    ])
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", "TEST_CAMP_B",
                [_row("TEST_SPU_1", cost="4.00", orders="4", gmv="16.00")])

    rows = _view_rows(db_session)
    assert [(r["campaign_id"], r["product_id"]) for r in rows] == [
        ("TEST_CAMP_A", "TEST_SPU_1"),
        ("TEST_CAMP_A", "TEST_SPU_2"),
        ("TEST_CAMP_B", "TEST_SPU_1"),
    ]
    assert float(rows[0]["real_cost_total"]) == 1.00


def test_view_latest_product_name_and_status_win(db_session):
    """名称/上架状态取覆盖末日（day_end 最大）那一行的值。"""
    camp = "TEST_CAMPAIGN_1"
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [_row("TEST_SPU_1", name="旧名", status="available")])
    _insert_raw(db_session, "daily", "2026-08-02", "2026-08-02", camp,
                [_row("TEST_SPU_1", name="新名", status="unavailable")])

    r = _view_rows(db_session)[0]
    assert r["product_name"] == "新名"
    assert r["product_status"] == "unavailable"


def test_view_handles_missing_or_dirty_metric_fields(db_session):
    """缺失/非数字业绩字段按 0 计,不抛错;无指标旧 dump 仍保留关联行。"""
    camp = "TEST_CAMPAIGN_1"
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [{"product_id": "TEST_SPU_1"}])
    _insert_raw(db_session, "daily", "2026-08-02", "2026-08-02", camp,
                [_row("TEST_SPU_1", cost="-", orders="", gmv="abc")])

    r = _view_rows(db_session)[0]
    assert r["product_id"] == "TEST_SPU_1"
    assert r["observed_days"] == 2
    assert float(r["real_cost_total"]) == 0.0
    assert r["order_sku_total"] == 0
    assert float(r["order_value_total"]) == 0.0


def test_view_left_joins_erp_channel_product_when_known(db_session):
    """SPU 已在 commerce.products_spu 目录 → 带出内部 channel ids,否则 NULL。"""
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
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [_row("TEST_SPU_1"), _row("TEST_SPU_2")])

    rows = {r["product_id"]: r for r in _view_rows(db_session)}
    assert rows["TEST_SPU_1"]["shop_pk"] == acct_id
    assert rows["TEST_SPU_1"]["spu_pk"] is not None
    assert rows["TEST_SPU_2"]["shop_pk"] == acct_id
    assert rows["TEST_SPU_2"]["spu_pk"] is None


# ─── live 语义（migration 0014）──────────────────────────────────────


def test_view_uses_live_history_and_ignores_daily(db_session):
    """S8：有 live history 的 campaign 只看 live 快照，遮蔽其 daily 行。"""
    camp = "TEST_CAMPAIGN_LIVE1"
    # 该 campaign 的 legacy daily 行（应被遮蔽，不参与视图）
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [_row("TEST_SPU_1", cost="999.00", orders="999", gmv="999.00")])
    # live history 整段快照（Design A：聚合后的权威值）
    _insert_raw(db_session, "history", "2026-07-01", "2026-09-05", camp,
                [_row("TEST_SPU_1", cost="30.50", orders="8", gmv="350.00")])

    rows = _view_rows(db_session)
    assert len(rows) == 1
    r = rows[0]
    assert float(r["real_cost_total"]) == 30.50
    assert r["order_sku_total"] == 8
    assert float(r["order_value_total"]) == 350.00
    # 窗口元数据由 live 区间推导：跨度 = (09-05 - 07-01) + 1 = 67 天
    assert r["observed_days"] == 67
    assert r["first_day"].isoformat() == "2026-07-01"
    assert r["last_day"].isoformat() == "2026-09-05"


def test_view_today_row_counts_only_when_after_history(db_session):
    """S8：today 仅当 day_end > history.day_end 计入；跨天过渡间隙不计。"""
    camp = "TEST_CAMPAIGN_LIVE2"
    _insert_raw(db_session, "history", "2026-07-01", "2026-09-09", camp,
                [_row("TEST_SPU_1", cost="100.00", orders="10", gmv="1000.00")])
    # 昨日 today 残留（day_end == history.day_end）→ 不应双计
    _insert_raw(db_session, "today", "2026-09-09", "2026-09-09", camp,
                [_row("TEST_SPU_1", cost="1.00", orders="1", gmv="1.00")])

    r = _view_rows(db_session)[0]
    assert float(r["real_cost_total"]) == 100.00
    assert r["order_sku_total"] == 10

    # 新一天 today（day_end = 09-10 > history day_end）→ 计入
    camp2 = "TEST_CAMPAIGN_LIVE3"
    _insert_raw(db_session, "history", "2026-07-01", "2026-09-09", camp2,
                [_row("TEST_SPU_1", cost="100.00", orders="10", gmv="1000.00")])
    _insert_raw(db_session, "today", "2026-09-10", "2026-09-10", camp2,
                [_row("TEST_SPU_1", cost="5.00", orders="2", gmv="50.00")])

    r2 = _view_rows(db_session, )[-1]
    assert r2["campaign_id"] == camp2
    assert float(r2["real_cost_total"]) == 105.00
    assert r2["order_sku_total"] == 12
    assert r2["last_day"].isoformat() == "2026-09-10"
    assert r2["observed_days"] == 72  # 71 + 1


def test_view_campaign_with_only_today_row_counts(db_session):
    """S==today（无 history）时：live today 行本身即全部数据。"""
    camp = "TEST_CAMPAIGN_LIVE4"
    _insert_raw(db_session, "today", "2026-09-10", "2026-09-10", camp,
                [_row("TEST_SPU_1", cost="3.00", orders="1", gmv="30.00")])
    rows = _view_rows(db_session)
    assert len(rows) == 1
    assert float(rows[0]["real_cost_total"]) == 3.00
    assert rows[0]["observed_days"] == 1


def test_view_empty_live_history_shadows_leftover_daily(db_session):
    """review P2b：live 行即使空表（区间无 SPU 响应行）也代表该 campaign 已由
    live 模型接管——残留 legacy daily 行不得浮出（防重复/口径混用）。
    判定基于 ad_raw 的 live 行存在性，与响应是否含 product 行解耦。"""
    camp = "TEST_CAMPAIGN_P2B"
    _insert_raw(db_session, "daily", "2026-08-01", "2026-08-01", camp,
                [_row("TEST_SPU_P2B", cost="999.00", orders="999", gmv="999.00")])
    # live history 快照：空 product 表（无 product 行），但 live 行真实存在
    _insert_raw(db_session, "history", "2026-07-01", "2026-09-05", camp, [])
    assert _view_rows(db_session) == []
