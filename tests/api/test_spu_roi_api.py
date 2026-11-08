"""TDD 契约测试:GET /v2/analytics/spu-roi(SPU 实际 ROI 看板只读端点)+ /v2/pages/spu-roi。

口径唯一真相 = docs/business/spu-profitability.md v10（汇率必须来自
数据库快照、K1=40 CNY/件、平台佣金基线 0.308）。
本文件锁定:
1. auth:无 key 401 / readonly 200 / admin 200(端点挂 _READONLY_EXACT)
2. 分页 / spu_id 子串搜索 / 默认排序实际 ROI 升序 / sort+order
3. 业务口径:单 SPU 场景(1 有效订单 + 1 已完结退货退款 + 1 已付被取消订单
   的取消退款 case)按 §4.2 公式精确断言 sales/refund_return/net_profit/
   roi_real/roi_breakeven/platform_fee/return_loss/cpa/unit_cost_used;
   取消桶只进信息列不进净额(DEFAULT_K1 + MANUAL 两分支);
   UNPAID 等异常订单的已完结退款按 §4.2 rule 0 防御性进未归属
   (不进 refund_* 桶/行内金额,meta.unattributed_refund_lines +1);
   fee_rate=NaN/Infinity 非有限值 / 超量级(如 1e9999999) → 422 不 500
4. 行范围:无活动 SPU 默认排除、include_all=true 包含
5. totals(跨分页)与行加总一致;meta 字段齐全
6. 页面 GET 200 text/html + 标题 + 静态资源引用 + 设计 token

数据隔离:共享 api conftest 只自动 wipe shops/products_spu/manual_costs/
api_keys;sales_orders/cases/case_lines/ad_raw 需本模块自清(TEST_ 前缀 +
module autouse 前后各 wipe 一次,依赖 api conftest 的 _isolate_state 保证
teardown 顺序:先清子表,再由 conftest 清父表)。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeVar
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from tests._job_invocation import authorize_test_shop
from tts_erp_v2.analytics.spu_profitability import (
    EvidenceKind,
    EvidenceRequest,
    ExactIdsSelection,
    FocusedSelection,
    ProfitScope,
    RowView,
    explain_spu,
    read_overview,
)
from tts_erp_v2.analytics.spu_profitability import _implementation as profitability_impl
from tts_erp_v2.analytics.spu_profitability import _snapshot as profitability_snapshot

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# ─── 口径常量(v7,与实现对齐；期望值推导用)─────────────────────────────
USD_VND = Decimal(26330)
USD_CNY = Decimal("6.7686473")
K1_CNY = Decimal(40)  # v7 D1: 原 30 → 40
FEE_BASELINE = Decimal("0.308")  # D10 实测重定
_Q4 = Decimal("0.0001")
_Q2 = Decimal("0.01")

# v7 框架常量（与实现一致）
RUBRIC_VERSION = "v10"
FEE_NOTE_V7 = (
    "平台佣金=平台从销售额直接扣除的全部费用(抽佣/联盟/运费类)；"
    "v7 已结算=实到账(SETTLEMENT，已含扣费)；未结算=sales×r̂×(1−spu退款率)(D5)；"
    "M19 纯信息列，不进净利"
)
Q = "TEST_ROI_SPU"  # 搜索范围:只命中本模块 TEST SPU
PAID_ORDER_STATUS = "DELIVERED"
DAY = "2026-09-01"


def m4(v: Decimal) -> str:
    return format(v.quantize(_Q4, rounding=ROUND_HALF_UP), ".4f")


def m2(v: Decimal) -> str:
    return format(v.quantize(_Q2, rounding=ROUND_HALF_UP), ".2f")


def cny4_from_usd(value: str | Decimal) -> str:
    """Return the CNY wire amount for a native/legacy USD amount."""
    return m4(Decimal(value) * USD_CNY)


def scenario_a_net_revenue_cny() -> Decimal:
    sales_cny = Decimal(2_633_000) / (USD_VND / USD_CNY)
    return sales_cny * Decimal("0.692") * Decimal("0.8")


def scenario_a_max_ad_spend_cny() -> Decimal:
    return scenario_a_net_revenue_cny() - Decimal(5) * K1_CNY


def scenario_a_net_profit_cny() -> Decimal:
    return scenario_a_max_ad_spend_cny() - Decimal(10) * USD_CNY


# ─── 清理(module autouse)────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _wipe_spu_roi_rows(db_engine, _isolate_state):
    """Setup + teardown 都清 TEST_ 行。

    依赖 tests/api/conftest.py 的 _isolate_state:两个 autouse 同 scope 时
    fixture 依赖保证 setup 顺序(_isolate_state 先)与 teardown 顺序(本 fixture
    先),即本模块先删 订单/case/ad_raw 子表行,conftest 再删 products/shops。
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)


# ─── 在线汇率(2026-09-06:ROI 账页换算已接 fx.* 缓存,D9 常量回退)─────
# autouse 注入一张与旧 D9 常量等值的 USD 快照(2099 时间戳保证最新):既有
# 全部金额/ROI 期望(26330 / 0.14774 派生)不变,同时让实现路径走 fx-cache。
FX_SEED_TS = "2099-09-06T00:00:00+00:00"
FX_SEED_VND = "26330"
FX_SEED_CNY = str(USD_CNY)  # CNY per USD；反向 cny_usd → .4f 0.1477


def _seed_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal insert, bound params
        sid = conn.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
                "VALUES ('USD', :ts, :ts2, now(), 3) RETURNING id"
            ),
            {"ts": FX_SEED_TS, "ts2": "2099-09-07T00:00:00+00:00"},
        ).scalar()
        for code, rate in [("USD", "1"), ("VND", FX_SEED_VND), ("CNY", FX_SEED_CNY)]:
            # pi-lens-ignore: python-sql-injection — literal insert, bound params
            conn.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:sid, 'USD', :c, :r)"
                ),
                {"sid": sid, "c": code, "r": rate},
            )


def _del_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal delete, bound param
        conn.execute(
            text(
                "DELETE FROM fx.exchange_rate_snapshots "
                "WHERE base_code = 'USD' AND upstream_last_update = :ts"
            ),
            {"ts": FX_SEED_TS},
        )


@pytest.fixture(autouse=True)
def _fx_online_consts(db_engine, _isolate_state):
    """ROI 换算走在线 fx 缓存:注入匹配常量快照;teardown 先于 _wipe 移除。"""
    _seed_fx(db_engine)
    yield
    _del_fx(db_engine)


# ─── 冻结投影时钟 ───
# 投影样本窗口由 consistent_read_snapshot 的 datetime.now(UTC) 滚动推导
# ([now-37d, now-8d]，与 w_start/w_end 无关)。种子订单钉死在 2026-09-01，
# 真实时间越过 2026-10-08 后种子滑出窗口 → 投影字段全 0（时间炸弹）。
# 冻结 calculated_at 到本文件最后全绿日 2026-10-06，让投影窗口永久覆盖种子。
# 先例：tests/publishing/test_owner_seams.py 的 SkewedDateTime monkeypatch。
_PROJECTION_FROZEN_NOW = datetime(2026, 10, 6, tzinfo=UTC)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _PROJECTION_FROZEN_NOW.astimezone(tz) if tz else _PROJECTION_FROZEN_NOW


@pytest.fixture(autouse=True)
def _freeze_projection_clock(monkeypatch):
    """冻结投影窗口的滚动时钟，让钉死的种子日期永久落在投影样本窗内。"""
    monkeypatch.setattr(profitability_snapshot, "datetime", _FrozenDateTime)


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(text("DELETE FROM plugin.ad_daily WHERE seller_id LIKE 'TEST_%'"))
        conn.execute(text("DELETE FROM plugin.ad_today WHERE seller_id LIKE 'TEST_%'"))
        conn.execute(
            text(
                "DELETE FROM finance.settlement_components WHERE transaction_id IN ("
                "SELECT id FROM finance.settlement_transactions "
                "WHERE external_transaction_id LIKE 'TEST_%')"
            )
        )
        conn.execute(
            text(
                "DELETE FROM finance.settlement_transactions "
                "WHERE external_transaction_id LIKE 'TEST_%'"
            )
        )
        conn.execute(
            text(
                "DELETE FROM finance.settlement_statements "
                "WHERE external_statement_id LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.case_lines "
                "WHERE case_id IN ("
                "  SELECT id FROM after_sales.cases c "
                "  WHERE c.shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.cases c "
                "WHERE c.shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM fulfillment.tracking_events "
                "WHERE shipment_id IN ("
                "  SELECT sh.id FROM fulfillment.shipments sh "
                "  WHERE sh.order_pk IN ("
                "    SELECT id FROM commerce.sales_orders "
                "    WHERE shop_pk IN ("
                "      SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "    )"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM fulfillment.shipments "
                "WHERE order_pk IN ("
                "  SELECT id FROM commerce.sales_orders "
                "  WHERE shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_order_lines "
                "WHERE order_pk IN ("
                "  SELECT id FROM commerce.sales_orders "
                "  WHERE shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_orders "
                "WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )


# ─── 造数 helpers(handler 用独立 session,必须真 commit)────────────────


def _seed_shop(sess, seller: str, *, region: str | None = "VN") -> int:
    # pi-lens-ignore: python-sql-injection
    shop_pk = sess.execute(
        text(
            "INSERT INTO commerce.shops "
            "(platform, shop_id, account_name, status, region) "
            "VALUES ('tiktok', :sid, :name, 'active', :region) RETURNING id"
        ),
        {"sid": seller, "name": f"{seller} 店铺", "region": region},
    ).scalar_one()
    authorize_test_shop(sess, shop_pk, seller)
    return shop_pk


def _seed_spu(
    sess,
    shop_pk: int,
    spu_id: str,
    *,
    title: str | None = None,
    status: str = "ACTIVATE",
) -> int:
    # pi-lens-ignore: python-sql-injection
    return sess.execute(
        text(
            "INSERT INTO commerce.products_spu "
            "(shop_pk, spu_id, title, status, main_image_url) "
            "VALUES (:shop, :sid, :title, :status, 'https://img.test/x.png') "
            "RETURNING id"
        ),
        {
            "shop": shop_pk,
            "sid": spu_id,
            "title": title or f"{spu_id} 标题",
            "status": status,
        },
    ).scalar_one()


def _seed_ad_dump(
    sess,
    *,
    seller: str,
    product_id: str,
    campaign_id: str,
    spend: str,
    orders: str,
    gmv: str,
    day: str = DAY,
) -> None:
    """post_product_list 一条 ad_daily(1 campaign×SPU×1 day)。

    v8.1（2026-09-15 fix/spu-roi-v81-ad-source）后，_SQL_ROI_AD / _SQL_DETAIL_ADS
    只读 plugin.ad_daily。ad_today 是被遗弃的临时表（merge job 2026-09-13
    禁用前作为当天暂存区），不再进 SQL 取数路径。所以测试夹具必须写
    ad_daily，否则 ROI 计算会拿到 spend=0。
    """
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            """
            INSERT INTO plugin.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            ) VALUES (
                :seller, :advertiser, :campaign, :product_id,
                '/oec_ads/shopping/v1/oec/stat/post_product_list',
                CAST(:day AS date),
                CAST(:spend AS NUMERIC), CAST(:orders AS BIGINT), CAST(:gmv AS NUMERIC),
                NULL, '{}'::JSONB, now()
            )
            ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET
                mixed_real_cost = EXCLUDED.mixed_real_cost,
                onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
                onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
                updated_at = now()
            """
        ),
        {
            "seller": seller,
            "advertiser": "TEST_ADV",
            "campaign": campaign_id,
            "product_id": product_id,
            "day": day,
            "spend": spend,
            "orders": orders,
            "gmv": gmv,
        },
    )


def _seed_order_line(
    sess,
    *,
    shop_pk: int,
    spu_pk: int,
    order_id: str,
    status: str,
    line_ext: str,
    qty: str,
    unit_price: str,
    paid: bool,
    paid_iso: str | None = None,
    order_iso: str | None = None,
) -> int:
    """插入一单(可带多行);返回 order id。

    paid_iso 自定义 paid_at(ISO,默认 2026-09-01),供窗口裁剪测试。
    order_iso 自定义 order_time(默认跟随 paid_iso 或 2026-09-01)——归属口径为
    下单时间优先 COALESCE(order_time, paid_at)，窗口测试用它钉死语义。
    """
    paid_at = None
    if paid:
        paid_at = paid_iso or "2026-09-01T08:00:00+00:00"
    order_time = order_iso or paid_iso or "2026-09-01T08:00:00+00:00"
    # pi-lens-ignore: python-sql-injection
    order_pk = sess.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, paid_at, order_time) "
            "VALUES (:shop, :oid, :status, 'VND', CAST(:paid AS timestamptz), "
            "CAST(:ot AS timestamptz)) "
            "RETURNING id"
        ),
        {"shop": shop_pk, "oid": order_id, "status": status, "paid": paid_at, "ot": order_time},
    ).scalar_one()
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "VALUES (:o, :ext, :spu, CAST(:qty AS numeric), "
            "CAST(:price AS numeric), 'VND')"
        ),
        {
            "o": order_pk,
            "ext": line_ext,
            "spu": spu_pk,
            "qty": qty,
            "price": unit_price,
        },
    )
    return order_pk


def _seed_case(
    sess,
    *,
    shop_pk: int,
    order_pk: int,
    ext_case: str,
    case_type: str,
    status: str,
    lines: list[
        tuple[int, str, str, str | None]
    ],  # (sales_order_line_id, ext, qty, refund_amt)
    updated_iso: str | None = None,
) -> None:
    updated = updated_iso or "2026-09-03T00:00:00+00:00"
    case_pk = sess.execute(
        text(
            "INSERT INTO after_sales.cases "
            "(shop_pk, order_pk, external_case_id, case_type, status, "
            " created_at_source, updated_at_source, currency) "
            "VALUES (:shop, :o, :ec, :ct, :st, "
            " CAST('2026-09-02T00:00:00+00:00' AS timestamptz), "
            " CAST(:updated AS timestamptz), 'VND') "
            "RETURNING id"
        ),
        {
            "shop": shop_pk,
            "o": order_pk,
            "ec": ext_case,
            "ct": case_type,
            "st": status,
            "updated": updated,
        },
    ).scalar_one()
    for line_pk, ext, qty, amt in lines:
        sess.execute(
            text(
                "INSERT INTO after_sales.case_lines "
                "(case_id, sales_order_line_id, external_case_line_id, "
                " quantity, refund_amount, currency) "
                "VALUES (:c, :sl, :ext, CAST(:qty AS numeric), "
                " CAST(:amt AS numeric), 'VND')"
            ),
            {"c": case_pk, "sl": line_pk, "ext": ext, "qty": qty, "amt": amt},
        )


def _seed_settlement(
    sess,
    *,
    order_pk: int,
    external_id: str,
    amount_vnd: str,
    customer_refund_vnd: str | None = None,
) -> None:
    statement_pk = sess.execute(
        text(
            "INSERT INTO finance.settlement_statements "
            "(external_statement_id, statement_time, currency) "
            "VALUES (:statement_id, '2026-09-15T00:00:00+00:00', 'VND') "
            "RETURNING id"
        ),
        {"statement_id": f"TEST_STATEMENT_{external_id}"},
    ).scalar_one()
    transaction_pk = sess.execute(
        text(
            "INSERT INTO finance.settlement_transactions "
            "(settlement_statement_id, external_transaction_id, order_pk, "
            "transaction_time) VALUES (:statement_pk, :external_id, :order_pk, "
            "'2026-09-15T00:00:00+00:00') RETURNING id"
        ),
        {
            "statement_pk": statement_pk,
            "external_id": external_id,
            "order_pk": order_pk,
        },
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO finance.settlement_components "
            "(transaction_id, component_code, amount, currency) "
            "VALUES (:transaction_pk, 'SETTLEMENT', :amount, 'VND')"
        ),
        {"transaction_pk": transaction_pk, "amount": amount_vnd},
    )
    if customer_refund_vnd is not None:
        sess.execute(
            text(
                "INSERT INTO finance.settlement_components "
                "(transaction_id, component_code, amount, currency) "
                "VALUES (:transaction_pk, 'CUSTOMER_REFUND', :amount, 'VND')"
            ),
            {"transaction_pk": transaction_pk, "amount": customer_refund_vnd},
        )


def _fetch_spu_line_id(sess, order_id: str) -> int:
    return sess.execute(
        text(
            "SELECT id FROM commerce.sales_order_lines "
            "WHERE order_pk = (SELECT id FROM commerce.sales_orders "
            "WHERE order_id = :oid LIMIT 1) LIMIT 1"
        ),
        {"oid": order_id},
    ).scalar_one()


def _seed_scenario_a(sess) -> int:
    """主口径场景 SPU A(含默认成本 DEFAULT_K1):

    - 广告:campaign C1 × TEST_ROI_SPU_A,spend=10.00 USD、ad_orders=5、gmv=50
    - 有效销售:1 单(DELIVERED,已付)5 件 × 526,600 VND(=$20/件) → sales=$100
    - 退货退款:1 条已完结 RETURN_AND_REFUND,1 件退款 526,600 VND(=$20)
    - 取消退款:另 1 张 CANCELLED 单(已付后被取消),case 完结,2 行取消:
      1 行有金额(526,600 VND=$20)、1 行金额 NULL(missing_lines=1)

    期望(见测试断言):sales=100.0000 / refund_net=20.0000(取消桶不入净额)/
    net_profit=17.0390 / roi_real=7.56 / roi_breakeven=2.79 / cpa=2.0000。
    """
    seller = "TEST_SELLER_A"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_A")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_A",
        campaign_id="TEST_CAMP_A",
        spend="10.00",
        orders="5",
        gmv="50.00",
    )
    # 有效销售单(1 行 5 件 × $20)
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_A1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_A1",
        qty="5",
        unit_price="526600",
        paid=True,
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_A1")
    # 已付被取消单(不计销售,取消退款进信息列)
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_A2",
        status="CANCELLED",
        line_ext="TEST_LINE_A2",
        qty="2",
        unit_price="526600",
        paid=True,
    )
    line2 = _fetch_spu_line_id(sess, "TEST_ORDER_A2")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_A1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_A1", "1", "526600")],
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o2,
        ext_case="TEST_CASE_A2",
        case_type="CANCELLATION",
        status="CANCELLATION_REQUEST_COMPLETE",
        lines=[
            (line2, "TEST_CLINE_A2", "1", "526600"),
            (line2, "TEST_CLINE_A3", "1", None),
        ],
    )
    return spu_pk


def _seed_extra_order_line(
    sess, *, order_id: str, spu_pk: int, line_ext: str, qty: str, unit_price: str
) -> None:
    """给已存在订单补插一行(跨 SPU 订单用:同一 order_id 多行)。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "SELECT id, :ext, :spu, CAST(:qty AS numeric), "
            "CAST(:price AS numeric), 'VND' FROM commerce.sales_orders "
            "WHERE order_id = :oid"
        ),
        {
            "ext": line_ext,
            "spu": spu_pk,
            "qty": qty,
            "price": unit_price,
            "oid": order_id,
        },
    )


def _seed_cross_spu_orders(sess) -> tuple[int, int]:
    """跨 SPU 去重场景(review MINOR 补测):两个 SPU X/Y 共享同一张订单。

    - TEST_ROI_SPU_X / TEST_ROI_SPU_Y(同店铺,均 ACTIVATE)
    - 有效订单 O1(DELIVERED,已付):两行,分别挂 X(1×$10)与 Y(1×$10)
      → 每 SPU 行 order_count=1,但全局 distinct 有效单只有 1
    - 已付被取消订单 O2(CANCELLED,已付):两行 X/Y(各 1×$5)
      → 全局 distinct 取消单 = 1;取消原额 10 计入 GMV

    期望:行加总(∑order_count=2)≠ totals.order_count=1;GMV = 20+10。
    """
    seller = "TEST_SELLER_XY"
    shop_pk = _seed_shop(sess, seller)
    x = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_X")
    y = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_Y")
    # O1: 有效单(首行挂 X,补一行挂 Y → 同一张单跨两 SPU)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_XY1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_XY1",
        qty="1",
        unit_price="263300",
        paid=True,  # $10
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_XY1",
        spu_pk=y,
        line_ext="TEST_LINE_XY2",
        qty="1",
        unit_price="263300",  # $10
    )
    # O2: 已付被取消,同样跨两 SPU(各 $5)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_XY2",
        status="CANCELLED",
        line_ext="TEST_LINE_XY3",
        qty="1",
        unit_price="131650",
        paid=True,  # $5
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_XY2",
        spu_pk=y,
        line_ext="TEST_LINE_XY4",
        qty="1",
        unit_price="131650",  # $5
    )
    return x, y


def _seed_projection_scenario(sess) -> int:
    """One settled sample plus unresolved and partly resolved unsettled orders."""

    seller = "TEST_SELLER_PROJECTION"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_PROJECTION")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_PROJECTION",
        campaign_id="TEST_CAMP_PROJECTION",
        spend="10",
        orders="3",
        gmv="100",
    )

    settled_order = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_PROJECTION_SETTLED",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_PROJECTION_SETTLED",
        qty="10",
        unit_price="100000",
        paid=True,
    )
    settled_line = _fetch_spu_line_id(sess, "TEST_ORDER_PROJECTION_SETTLED")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=settled_order,
        ext_case="TEST_CASE_PROJECTION_SETTLED",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(settled_line, "TEST_CLINE_PROJECTION_SETTLED", "2", "200000")],
    )
    _seed_settlement(
        sess,
        order_pk=settled_order,
        external_id="TEST_TXN_PROJECTION_SETTLED",
        amount_vnd="600000",
        customer_refund_vnd="-200000",
    )

    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_PROJECTION_UNRESOLVED",
        status="IN_TRANSIT",
        line_ext="TEST_LINE_PROJECTION_UNRESOLVED",
        qty="4",
        unit_price="100000",
        paid=True,
    )
    partial_order = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_PROJECTION_PARTIAL",
        status="IN_TRANSIT",
        line_ext="TEST_LINE_PROJECTION_PARTIAL",
        qty="2",
        unit_price="100000",
        paid=True,
    )
    partial_line = _fetch_spu_line_id(sess, "TEST_ORDER_PROJECTION_PARTIAL")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=partial_order,
        ext_case="TEST_CASE_PROJECTION_PARTIAL",
        case_type="REFUND_ONLY",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(partial_line, "TEST_CLINE_PROJECTION_PARTIAL", "1", "100000")],
    )
    return spu_pk


def _seed_terminal_delivery_risk_scenario(sess) -> int:
    """Completed cross-border outcomes plus one undelivered live target."""

    seller = "TEST_SELLER_TERMINAL_DELIVERY_RISK"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK")

    delivered_order = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_TERMINAL_DELIVERED",
        status="DELIVERED",
        line_ext="TEST_LINE_TERMINAL_DELIVERED",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    _seed_settlement(
        sess,
        order_pk=delivered_order,
        external_id="TEST_TXN_TERMINAL_DELIVERED",
        amount_vnd="100000",
    )

    loss_order = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_TERMINAL_RETURNED",
        status="CANCELLED",
        line_ext="TEST_LINE_TERMINAL_RETURNED",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    shipment_id = sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " order_pk, external_package_id, status"
            ") VALUES (:order_pk, 'TEST_PKG_TERMINAL_RETURNED',"
            " 'RETURNED_TO_SELLER') RETURNING id"
        ),
        {"order_pk": loss_order},
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO fulfillment.tracking_events ("
            " shipment_id, external_event_key, action_code, event_at, description"
            ") VALUES (:shipment_id, 'TEST_EVENT_TERMINAL_RETURNED', 80101,"
            " now(), 'Your package was returned to seller by the shipping provider.')"
        ),
        {"shipment_id": shipment_id},
    )

    cancelled_with_delivery_evidence = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_CANCELLED_WITH_DELIVERY_EVIDENCE",
        status="CANCELLED",
        line_ext="TEST_LINE_CANCELLED_WITH_DELIVERY_EVIDENCE",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " order_pk, external_package_id, status, delivered_at"
            ") VALUES (:order_pk, 'TEST_PKG_CANCELLED_DELIVERED',"
            " 'DELIVERED', now())"
        ),
        {"order_pk": cancelled_with_delivery_evidence},
    )

    delivered_full_refund = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_DELIVERED_FULL_REFUND",
        status="DELIVERED",
        line_ext="TEST_LINE_DELIVERED_FULL_REFUND",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    _seed_settlement(
        sess,
        order_pk=delivered_full_refund,
        external_id="TEST_TXN_DELIVERED_FULL_REFUND",
        amount_vnd="0",
        customer_refund_vnd="-100000",
    )

    domestic_cancel_full_refund = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_DOMESTIC_CANCEL_FULL_REFUND",
        status="CANCELLED",
        line_ext="TEST_LINE_DOMESTIC_CANCEL_FULL_REFUND",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    _seed_settlement(
        sess,
        order_pk=domestic_cancel_full_refund,
        external_id="TEST_TXN_DOMESTIC_CANCEL_FULL_REFUND",
        amount_vnd="0",
        customer_refund_vnd="-100000",
    )

    overseas_cancel_full_refund = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_OVERSEAS_CANCEL_FULL_REFUND",
        status="CANCELLED",
        line_ext="TEST_LINE_OVERSEAS_CANCEL_FULL_REFUND",
        qty="1",
        unit_price="100000",
        paid=True,
    )
    overseas_cancel_line = _fetch_spu_line_id(
        sess, "TEST_ORDER_OVERSEAS_CANCEL_FULL_REFUND"
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=overseas_cancel_full_refund,
        ext_case="TEST_CASE_OVERSEAS_CANCEL_FULL_REFUND",
        case_type="CANCELLATION",
        status="CANCELLATION_REQUEST_COMPLETE",
        lines=[
            (
                overseas_cancel_line,
                "TEST_CLINE_OVERSEAS_CANCEL_FULL_REFUND",
                "1",
                "100000",
            )
        ],
    )
    overseas_shipment_id = sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " order_pk, external_package_id, status"
            ") VALUES (:order_pk, 'TEST_PKG_OVERSEAS_CANCEL_FULL_REFUND',"
            " 'IN_TRANSIT') RETURNING id"
        ),
        {"order_pk": overseas_cancel_full_refund},
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO fulfillment.tracking_events ("
            " shipment_id, external_event_key, action_code, event_at, description"
            ") VALUES (:shipment_id, 'TEST_EVENT_OVERSEAS_CANCEL_FULL_REFUND',"
            " 38301, now(), 'Arrived in destination country/region')"
        ),
        {"shipment_id": overseas_shipment_id},
    )

    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_TERMINAL_TARGET",
        status="IN_TRANSIT",
        line_ext="TEST_LINE_TERMINAL_TARGET",
        qty="2",
        unit_price="100000",
        paid=True,
    )
    return spu_pk


def _seed_delivery_aware_projection_scenario(sess) -> int:
    """Add two unsettled orders that have already reached delivery."""

    spu_pk = _seed_projection_scenario(sess)
    shop_pk = sess.execute(
        text(
            "SELECT shop_pk FROM commerce.sales_orders "
            "WHERE order_id = 'TEST_ORDER_PROJECTION_UNRESOLVED'"
        )
    ).scalar_one()
    unresolved_order = sess.execute(
        text(
            "SELECT id FROM commerce.sales_orders "
            "WHERE order_id = 'TEST_ORDER_PROJECTION_UNRESOLVED'"
        )
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " order_pk, external_package_id, status, delivered_at"
            ") VALUES (:order_pk, 'TEST_PKG_PROJECTION_DELIVERED',"
            " 'DELIVERED', now())"
        ),
        {"order_pk": unresolved_order},
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_PROJECTION_STATUS_DELIVERED",
        status="DELIVERED",
        line_ext="TEST_LINE_PROJECTION_STATUS_DELIVERED",
        qty="4",
        unit_price="100000",
        paid=True,
    )
    return spu_pk


def _seed_spu_b(sess) -> int:
    """SPU B:spend=50、销售 $90(3×$30)、无退款 → roi_real=1.80。"""
    seller = "TEST_SELLER_B"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_B")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_B",
        campaign_id="TEST_CAMP_B",
        spend="50.00",
        orders="2",
        gmv="100.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_B1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_B1",
        qty="3",
        unit_price="789900",  # $30/件 → sales=$90
        paid=True,
    )
    return spu_pk


def _seed_spu_c(sess) -> int:
    """SPU C:spend=10、销售 $30(3×$10)、无退款 → roi_real=3.00。"""
    seller = "TEST_SELLER_C"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_C")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_C",
        campaign_id="TEST_CAMP_C",
        spend="10.00",
        orders="2",
        gmv="20.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_C1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_C1",
        qty="3",
        unit_price="263300",  # $10/件 → sales=$30
        paid=True,
    )
    return spu_pk


def _seed_inactive_spu(sess) -> int:
    """无任何活动的 SPU(仅目录行)。"""
    shop_pk = _seed_shop(sess, "TEST_SELLER_INACTIVE")
    return _seed_spu(sess, shop_pk, "TEST_ROI_SPU_INACTIVE")


def _seed_spu_manual(sess) -> int:
    """命中人工成本的 SPU(unit_cost=25 CNY → MANUAL)。"""
    seller = "TEST_SELLER_MANUAL"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_MANUAL")
    sess.execute(
        text(
            "INSERT INTO procurement.manual_product_costs "
            "(spu_pk, unit_cost, currency, valid_from, valid_to, note, created_by) "
            "VALUES (:spu, 25.0000, 'CNY', now(), NULL, 'TEST 成本', 'test')"
        ),
        {"spu": spu_pk},
    )
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_MANUAL",
        campaign_id="TEST_CAMP_M",
        spend="10.00",
        orders="5",
        gmv="50.00",
    )
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_M1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_M1",
        qty="5",
        unit_price="526600",
        paid=True,
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_M1")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_M1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_M1", "1", "526600")],
    )
    return spu_pk


def _seed_unpaid_refund_spu(sess) -> int:
    """异常订单退款场景(§4.2 rule 0):有效销售单 + UNPAID 订单已完结退款。

    - TEST_ORDER_AB1:有效销售(DELIVERED,已付)5 件×$20 → sales $100
    - TEST_ORDER_AB2:UNPAID(白名单外且非 CANCELLED)订单,1 行已完结
      RETURN_AND_REFUND case,退 1 件 526,600 VND(=$20),有 spu_pk

    期望:该退款不进 refund_net/refund_cancelled 桶、不进净额与行内金额
    (net_profit 视同无此退款),只在 meta.unattributed_refund_lines 显式
    +1 计数(防御性未归属),不静默丢。
    """
    seller = "TEST_SELLER_AB"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_AB")
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_AB1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_AB1",
        qty="5",
        unit_price="526600",  # $20/件 → sales $100
        paid=True,
    )
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_AB2",
        status="UNPAID",
        line_ext="TEST_LINE_AB2",
        qty="1",
        unit_price="526600",
        paid=False,
    )
    line2 = _fetch_spu_line_id(sess, "TEST_ORDER_AB2")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o2,
        ext_case="TEST_CASE_AB1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line2, "TEST_CLINE_AB1", "1", "526600")],
    )
    return spu_pk


def _seed_window_spu(sess) -> int:
    """退款跟随原订单下单日期的窗口裁剪场景（仅销售+退款，无广告）。

    - 订单 1：2026-09-01 下单（paid_at 故意错开为 09-10，钉死按下单日归属），
      2026-09-10 完结退款 1 件 $20；归入 09-01。
    - 订单 2：2026-08-01 下单（paid_at 故意错开为 09-02——旧 COALESCE(paid_at,
      order_time) 口径会把它误归 9 月），2026-09-12 完结退款 1 件 $20；归入 08-01。

    默认全历史：units=5、sales=$100、refund_return_qty=2；查询 9 月只
    保留订单 1 及其退款，不能因订单 2 的退款/收款发生在 9 月而把它算进来。
    """
    seller = "TEST_SELLER_WIN"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_WIN")
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_W1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_W1",
        qty="3",
        unit_price="526600",  # $20/件 → $60
        paid=True,
        order_iso="2026-09-01T08:00:00+00:00",
        paid_iso="2026-09-10T08:00:00+00:00",
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_W1")
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_W2",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_W2",
        qty="2",
        unit_price="526600",  # $20/件 → $40
        paid=True,
        order_iso="2026-08-01T08:00:00+00:00",
        paid_iso="2026-09-02T08:00:00+00:00",
    )
    line2 = _fetch_spu_line_id(sess, "TEST_ORDER_W2")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_W1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_W1", "1", "526600")],
        updated_iso="2026-09-10T00:00:00+00:00",
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o2,
        ext_case="TEST_CASE_W2",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line2, "TEST_CLINE_W2", "1", "526600")],
        updated_iso="2026-09-12T00:00:00+00:00",
    )
    statement_pk = sess.execute(
        text(
            "INSERT INTO finance.settlement_statements "
            "(external_statement_id, statement_time, currency) "
            "VALUES ('TEST_STATEMENT_WINDOW', '2026-09-15T00:00:00+00:00', 'VND') "
            "RETURNING id"
        )
    ).scalar_one()
    for suffix, order_pk, amount in (
        ("W1", o1, "1579800"),
        ("W2", o2, "1053200"),
    ):
        transaction_pk = sess.execute(
            text(
                "INSERT INTO finance.settlement_transactions "
                "(settlement_statement_id, external_transaction_id, order_pk, "
                "transaction_time) VALUES (:statement_pk, :external_id, :order_pk, "
                "'2026-09-15T00:00:00+00:00') RETURNING id"
            ),
            {
                "statement_pk": statement_pk,
                "external_id": f"TEST_TXN_{suffix}",
                "order_pk": order_pk,
            },
        ).scalar_one()
        sess.execute(
            text(
                "INSERT INTO finance.settlement_components "
                "(transaction_id, component_code, amount, currency) "
                "VALUES (:transaction_pk, 'SETTLEMENT', :amount, 'VND')"
            ),
            {"transaction_pk": transaction_pk, "amount": amount},
        )
    return spu_pk


def _seed_status_mix(sess) -> None:
    """include_all 行范围(§5.1-7):ACTIVATE vs DEACTIVATE 目录状态。

    - TEST_ROI_SPU_ACTIVE_ON / DEACTIVE_ON 各有有效销售单(有活动)
    - TEST_ROI_SPU_ACTIVE_IDLE / DEACTIVE_IDLE 仅目录行(无活动)
    """
    for sid, status in (
        ("TEST_ROI_SPU_ACTIVE_ON", "ACTIVATE"),
        ("TEST_ROI_SPU_DEACTIVE_ON", "DEACTIVATE"),
    ):
        shop = _seed_shop(sess, f"TEST_SELLER_{sid}")
        spu = _seed_spu(sess, shop, sid, status=status)
        _seed_order_line(
            sess,
            shop_pk=shop,
            spu_pk=spu,
            order_id=f"TEST_ORDER_{sid}",
            status=PAID_ORDER_STATUS,
            line_ext=f"TEST_LINE_{sid}",
            qty="1",
            unit_price="263300",  # $10
            paid=True,
        )
    for sid, status in (
        ("TEST_ROI_SPU_ACTIVE_IDLE", "ACTIVATE"),
        ("TEST_ROI_SPU_DEACTIVE_IDLE", "DEACTIVATE"),
    ):
        shop = _seed_shop(sess, f"TEST_SELLER_{sid}")
        _seed_spu(sess, shop, sid, status=status)


def _commit_all(sess) -> None:
    sess.commit()


_SeedResult = TypeVar("_SeedResult")


def _seed(sess: Session, fn: Callable[[Session], _SeedResult]) -> _SeedResult:
    """在真 commit 的 session 里执行一个 seed 函数。"""
    result = fn(sess)
    sess.commit()
    return result


# ─── auth ─────────────────────────────────────────────────────────────


def test_spu_roi_requires_auth(api_client):
    assert api_client.get("/v2/analytics/spu-roi").status_code == 401


def test_spu_roi_rejects_invalid_projection_lookback(api_client, readonly_key):
    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"projection_lookback_days": 60},
    )
    assert response.status_code == 422, response.text


def test_spu_roi_readonly_and_admin_ok(api_client, readonly_key, admin_key):
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q, "limit": 1},
    )
    assert r.status_code == 200, r.text
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {admin_key}"},
        params={"q": Q, "limit": 1},
    )
    assert r.status_code == 200, r.text


# ─── 业务口径(§4.2 公式)──────────────────────────────────────────────


def test_spu_roi_math_single_spu_default_k1(api_client, readonly_key, db_engine):
    """单 SPU(1 有效单 + 1 已完结退货退款 + 1 已付被取消单)v7 公式断言。

    原生广告金额为 USD、销售/退款为 VND、采购成本为 CNY；公式入口按
    同一汇率快照统一换算为 CNY 后再计算。下列旧 USD 推导值仅用于说明比例：
      net_revenue = 55.36 USD ÷ cny_usd
      cogs_all = 5件 × 40 CNY = 200 CNY
      net_profit = 15.812 USD ÷ cny_usd
      v9 全损：完结退货(不论物流)也算全损 → full_loss_qty=1(退货桶)
      return_loss = 1 × 40 CNY
      roi_real 与 roi_breakeven 为无量纲比例，换币前后保持不变
      platform_fee = 30.80 USD ÷ cny_usd
      full_loss_rate = 1/(5+0) = 0.20
      cancel_rate = 1/(1+1) = 0.50（CANCELLED 单无 38301 → 国内取消）
    """
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_scenario_a)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    assert item["spu_pk"] == spu_pk
    assert item["spu_id"] == "TEST_ROI_SPU_A"
    assert item["title"] == "TEST_ROI_SPU_A 标题"
    assert item["status"] == "ACTIVATE"
    assert item["shop_name"] == "TEST_SELLER_A 店铺"

    # 广告侧原生 USD；API 金额统一输出 CNY。
    assert item["ad_count"] == 1
    assert item["ad_orders"] == 5
    assert item["spend"] == cny4_from_usd("10")
    assert item["gmv_ad"] == cny4_from_usd("50")
    assert item["roi_l0"] == "5.00"
    assert item["ad_system_actual_roi"] == "5.00"
    assert item["ad_system_breakeven_roi"] == "1.94"
    assert item["ad_system_max_ad_spend"] == m4(scenario_a_max_ad_spend_cny())
    assert item["ad_system_remaining_ad_spend_capacity"] == m4(
        scenario_a_net_profit_cny()
    )
    assert item["ad_system_breakeven_roi_status"] == "estimated_known_costs"
    assert item["ad_first_day"] == DAY
    assert item["ad_last_day"] == DAY

    # 销售侧：保留有效销售订单原始值，同时暴露与大盘同口径的净有效指标
    assert item["order_count"] == 1
    assert item["cancelled_order_count"] == 1
    assert item["total_orders"] == 2
    assert item["effective_order_count"] == 0
    assert item["units_sold"] == 5
    assert item["sales"] == cny4_from_usd("100")
    assert item["effective_sales"] == cny4_from_usd("80")

    # v7 分层字段（场景无 SETTLEMENT → settled=0, unsettled=100）
    assert item["settled_order_count"] == 0
    assert item["has_unsettled_orders"] is True
    assert item["uses_default_unit_cost"] is True
    assert item["refund_rate_alert"] is True
    assert item["profit_status"] == "profit"
    assert item["roi_status"] == "non_negative"
    assert item["net_revenue"] == cny4_from_usd("55.3600")
    assert item["settled_sales"] == "0.0000"
    assert item["unsettled_sales"] == cny4_from_usd("100")
    assert item["settled_net"] == "0.0000"

    # 退款桶不变
    assert item["refund_only_qty"] == 0
    assert item["refund_only_amount"] == "0.0000"
    assert item["refund_return_qty"] == 1
    assert item["refund_return_amount"] == cny4_from_usd("20")
    assert item["refund_net_qty"] == 1
    assert item["refund_net_amount"] == cny4_from_usd("20")
    assert item["refund_order_count"] == 1
    assert item["refund_rate"] == "0.50"
    assert item["refund_amount_rate"] == "0.20"

    # 已付被取消订单退款（信息列）
    assert item["refund_cancelled_qty"] == 2
    assert item["refund_cancelled_amount"] == cny4_from_usd("20")
    assert item["refund_cancelled_missing_lines"] == 1

    # v9 全损：完结退货不论物流直接计全损（场景无 38301 仍计 1 件退货）
    assert item["full_loss_qty"] == 1
    assert item["full_loss_cancelled_qty"] == 0
    assert item["full_loss_order_count"] == 1
    assert item["full_loss_rate"] == "0.50"
    assert item["full_loss_qty_rate"] == "0.20"
    # v10 取消率：国内取消 ÷ 全部订单 = 1/2
    assert item["cancel_rate"] == "0.50"
    assert item["domestic_cancelled_order_count"] == 1
    assert item["overseas_cancelled_order_count"] == 0

    # 成本与全部利润金额统一使用 CNY。
    assert item["cost_source"] == "DEFAULT_K1"
    assert Decimal(item["unit_cost_used"]) == K1_CNY

    # v9 利润域（return_loss = 完结退货 1 件 × 40 CNY）
    assert item["return_loss"] == "40.0000"
    assert item["platform_fee"] == cny4_from_usd("30.8000")
    assert item["net_profit"] == m4(scenario_a_net_profit_cny())
    assert item["roi_real"] == "4.95"
    assert item["roi_breakeven"] == "1.92"
    assert item["cpa"] == cny4_from_usd("2")

    # 物流终态样本独立于结算：一笔成功送达、没有拒收全损，风险率为 0。
    assert item["projection_status"] == "no_unsettled_orders"
    assert item["projection_terminal_basis_order_count"] == 1
    assert item["projection_terminal_full_loss_order_count"] == 0
    assert item["projection_terminal_full_loss_qty"] == 0
    assert item["projection_completed_basis_order_count"] == 2
    assert item["projection_completed_full_loss_order_count"] == 0
    assert item["completed_full_loss_rate"] == "0.0000"
    assert item["projection_basis_full_loss_order_count"] == 1
    assert item["projection_refund_amount_rate"] == "0.0000"
    assert item["pre_delivery_full_loss_rate"] == "0.0000"
    # Compatibility metrics preserve the pre-existing delivered-refund cohort.
    assert item["delivered_full_loss_rate"] == "1.0000"
    assert item["settled_full_loss_rate"] == "1.0000"
    assert item["projection_full_loss_qty_rate"] == "1.0000"
    assert Decimal(item["projected_future_full_loss_qty"]) == Decimal(0)
    assert item["projected_net_profit"] == item["net_profit"]
    assert item["projected_roi_real"] == item["roi_real"]

    # meta v10：阈值与公式说明也由后端返回，前端只渲染。
    assert body["meta"]["rubric_version"] == RUBRIC_VERSION
    presentation = body["meta"]["presentation"]
    assert presentation["rubric_label"] == f"盈利 {RUBRIC_VERSION}"
    assert presentation["refund_rate_alert_threshold"] == "0.3000"
    assert "fee_rate_used" in presentation["pnl_hints"]["unsettled"]
    assert "0.308" not in presentation["pnl_hints"]["unsettled"]
    assert body["totals"]["profit_status"] in {"loss", "profit", "break_even"}
    assert body["totals"]["roi_status"] in {"negative", "non_negative", "unavailable"}
    assert body["meta"]["currency"]["display"] == "CNY"
    assert Decimal(body["meta"]["fx"]["usd_cny"]) == USD_CNY
    assert body["meta"]["fx"]["cny_vnd"] == format(USD_VND / USD_CNY, "f")
    assert body["meta"]["fx"]["vnd_cny"] == format(USD_CNY / USD_VND, "f")
    reconstructed_spend = Decimal("10") * Decimal(body["meta"]["fx"]["usd_cny"])
    reconstructed_sales = Decimal(2_633_000) / Decimal(
        body["meta"]["fx"]["cny_vnd"]
    )
    assert item["spend"] == m4(reconstructed_spend)
    assert item["sales"] == m4(reconstructed_sales)
    assert "SETTLEMENT" in body["meta"]["fee"]["note"]
    assert "人工标注" in body["meta"]["cost_assumption"]
    assert "同步货源价不参与计算" in body["meta"]["cost_assumption"]
    assert "40 CNY" in body["meta"]["cost_assumption"]
    assert "缺成本" in body["meta"]["cost_assumption"]

    # totals
    assert body["totals"]["row_count"] == 1
    assert body["totals"]["spend"] == cny4_from_usd("10")
    assert body["totals"]["sales"] == cny4_from_usd("100")
    assert body["totals"]["gmv"] == cny4_from_usd("140")
    assert body["totals"]["order_count"] == 1
    assert body["totals"]["cancelled_order_count"] == 1
    assert body["totals"]["total_orders"] == 2
    assert body["totals"]["refund_net_amount"] == cny4_from_usd("20")
    assert body["totals"]["net_profit"] == m4(scenario_a_net_profit_cny())
    assert body["totals"]["ad_system_actual_roi"] == "5.00"
    assert body["totals"]["ad_system_breakeven_roi"] == "1.94"
    assert body["totals"]["ad_system_max_ad_spend"] == m4(
        scenario_a_max_ad_spend_cny()
    )
    assert body["totals"]["ad_system_remaining_ad_spend_capacity"] == m4(
        scenario_a_net_profit_cny()
    )
    assert body["totals"]["ad_system_breakeven_roi_status"] == "estimated_known_costs"


def test_spu_roi_projects_undelivered_loss_from_terminal_delivery_outcomes(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed(sess, _seed_terminal_delivery_risk_scenario)

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]

    vnd_cny = USD_CNY / USD_VND
    one_minus_fee = Decimal(1) - FEE_BASELINE
    completed_full_loss_rate = Decimal(3) / Decimal(6)
    risk_sales_after_fee = Decimal(200_000) * one_minus_fee * vnd_cny
    expected_future_refund = risk_sales_after_fee * completed_full_loss_rate
    expected_unsettled_net = risk_sales_after_fee - expected_future_refund

    assert item["projection_basis_order_count"] == 3
    assert item["projection_terminal_basis_order_count"] == 4
    assert item["projection_terminal_basis_sales"] == m4(
        Decimal(400_000) * vnd_cny
    )
    assert item["projection_terminal_full_loss_sales"] == m4(
        Decimal(100_000) * vnd_cny
    )
    assert item["projection_terminal_full_loss_order_count"] == 1
    assert item["projection_terminal_full_loss_qty"] == 1
    assert item["projection_completed_basis_order_count"] == 6
    assert item["projection_completed_full_loss_order_count"] == 3
    assert item["completed_full_loss_rate"] == "0.5000"
    assert item["projection_full_loss_basis_order_count"] == 3
    assert item["projection_basis_full_loss_order_count"] == 0
    assert item["projection_basis_full_loss_qty"] == 0
    assert item["pre_delivery_full_loss_rate"] == "0.2500"
    assert item["delivered_full_loss_rate"] == "0.0000"
    assert item["settled_full_loss_rate"] == "0.0000"
    assert item["projection_refund_amount_rate"] == "0.2500"
    assert Decimal(item["projected_future_full_loss_order_count"]) == Decimal("0.5")
    assert Decimal(item["projected_future_full_loss_qty"]) == Decimal(1)
    assert Decimal(item["projected_future_refund_amount"]) == (
        expected_future_refund.quantize(_Q4, rounding=ROUND_HALF_UP)
    )
    assert item["projected_unsettled_net"] == m4(expected_unsettled_net)
    expected_projected_profit = (
        Decimal(100_000) * vnd_cny + expected_unsettled_net - Decimal(200)
    )
    assert item["projected_net_profit"] == m4(expected_projected_profit)


def test_spu_roi_projects_unsettled_orders_from_independent_samples(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed(sess, _seed_projection_scenario)

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_PROJECTION"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]

    vnd_cny = USD_CNY / USD_VND
    one_minus_fee = Decimal(1) - FEE_BASELINE
    projected_terminal_refund = Decimal(100_000) * one_minus_fee * vnd_cny
    confirmed_unsettled_refund = Decimal(100_000) * one_minus_fee * vnd_cny
    projected_unsettled_net = Decimal(500_000) * one_minus_fee * vnd_cny
    projected_net_revenue = Decimal(600_000) * vnd_cny + projected_unsettled_net
    projected_net_profit = (
        projected_net_revenue - Decimal(640) - Decimal(10) * USD_CNY
    )
    projected_future_full_loss_qty = Decimal(0)
    projected_terminal_full_loss_qty = Decimal(3)
    projected_full_loss_cost = Decimal(120)
    projected_nc_prime = projected_net_revenue - projected_full_loss_cost
    projected_cogs_kept = Decimal(520)
    projected_roi = projected_nc_prime / (Decimal(10) * USD_CNY)
    projected_ad_gmv = Decimal(100) * USD_CNY

    assert item["projection_status"] == "available"
    assert item["projection_basis_order_count"] == 1
    assert item["projection_basis_qty"] == 10
    assert item["projection_basis_sales"] == m4(Decimal(1_000_000) * vnd_cny)
    assert item["projection_basis_refund_amount"] == m4(
        Decimal(200_000) * vnd_cny
    )
    assert item["projection_terminal_basis_order_count"] == 1
    assert item["projection_terminal_basis_sales"] == m4(
        Decimal(1_000_000) * vnd_cny
    )
    assert item["projection_terminal_full_loss_sales"] == "0.0000"
    assert item["projection_terminal_full_loss_order_count"] == 0
    assert item["projection_terminal_full_loss_qty"] == 0
    assert item["projection_completed_basis_order_count"] == 1
    assert item["projection_completed_full_loss_order_count"] == 0
    assert item["completed_full_loss_rate"] == "0.0000"
    assert item["projection_full_loss_basis_order_count"] == 1
    assert item["projection_basis_full_loss_order_count"] == 1
    assert item["projection_basis_full_loss_qty"] == 2
    assert item["projection_refund_amount_rate"] == "0.0000"
    assert item["pre_delivery_full_loss_rate"] == "0.0000"
    assert item["delivered_full_loss_rate"] == "1.0000"
    assert item["settled_full_loss_rate"] == "1.0000"
    assert item["projection_full_loss_qty_rate"] == "1.0000"
    assert item["full_loss_exposure_unsettled_order_count"] == 2
    # The in-transit partial refund is known revenue loss, not confirmed full loss.
    assert item["confirmed_full_loss_exposure_order_count"] == 0
    assert item["confirmed_full_loss_exposure_qty"] == 0
    assert item["unresolved_unsettled_order_count"] == 2
    assert item["unresolved_full_loss_exposure_order_count"] == 2
    assert Decimal(item["unresolved_unsettled_qty"]) == Decimal(5)
    assert Decimal(item["unresolved_full_loss_exposure_qty"]) == Decimal(6)
    assert item["unresolved_unsettled_sales"] == m4(Decimal(500_000) * vnd_cny)
    assert item["full_loss_exposure_unsettled_sales"] == m4(
        Decimal(600_000) * vnd_cny
    )
    assert item["confirmed_unsettled_refund_amount"] == m4(
        Decimal(100_000) * vnd_cny
    )
    assert item["confirmed_full_loss_exposure_refund_amount"] == m4(
        Decimal(100_000) * vnd_cny
    )
    assert item["confirmed_unsettled_full_loss_order_count"] == 0
    assert Decimal(item["projected_future_refund_amount"]) == (
        projected_terminal_refund - confirmed_unsettled_refund
    ).quantize(_Q4, rounding=ROUND_HALF_UP)
    assert item["projected_terminal_refund_amount"] == m4(
        projected_terminal_refund
    )
    assert Decimal(item["projected_future_full_loss_order_count"]) == Decimal(0)
    assert Decimal(item["projected_future_full_loss_qty"]) == (
        projected_future_full_loss_qty
    )
    assert Decimal(item["projected_terminal_full_loss_qty"]) == (
        projected_terminal_full_loss_qty
    )
    assert item["projected_full_loss_cost"] == m4(projected_full_loss_cost)
    assert item["projected_unsettled_net"] == m4(projected_unsettled_net)
    assert item["projected_net_revenue"] == m4(projected_net_revenue)
    assert item["projected_net_profit"] == m4(projected_net_profit)
    assert item["projected_nc_prime"] == m4(projected_nc_prime)
    assert item["projected_cogs_kept"] == m4(projected_cogs_kept)
    assert item["projected_roi_real"] == m2(projected_roi)
    assert item["projected_roi_breakeven"] is None
    assert item["projected_ad_gmv"] == m4(projected_ad_gmv)
    assert item["projected_ad_system_actual_roi"] == "10.00"
    assert item["projected_ad_system_breakeven_roi"] is None
    assert body["meta"]["projection"]["date_attribution"] == (
        "COALESCE(order_time, paid_at)"
    )
    assert "尚未送达" in body["meta"]["projection"]["target"]
    assert "到达海外/已送达后最终全额退款" in body["meta"]["projection"][
        "full_loss_sample"
    ]
    assert body["meta"]["projection"]["full_loss_rate_source"] == (
        "已完结全损订单数 ÷ 全部已完结订单数"
    )
    assert "不参与当前预测" in body["meta"]["projection"][
        "refund_amount_rate_source"
    ]
    # 已完结样本没有全损，因此不新增预测退款；风险池内已经确认的
    # 100,000 VND 退款只扣一次。
    assert item["projected_future_refund_amount"] == "0.0000"
    assert item["projected_unsettled_net"] == m4(
        (Decimal(600_000) - Decimal(100_000)) * one_minus_fee * vnd_cny
    )
    # 预计净利润 = 当前净利润 + 未结算净收入调整。
    assert Decimal(item["projected_net_profit"]) == (
        Decimal(item["net_profit"])
        + Decimal(item["projected_unsettled_net"])
        - Decimal(item["unsettled_net"])
    ).quantize(_Q4, rounding=ROUND_HALF_UP)
    # Existing COGS already contains all 16 paid units. Predicted loss is not
    # subtracted again from terminal profit.
    assert Decimal(item["projected_net_profit"]) == (
        Decimal(item["projected_net_revenue"])
        - Decimal(item["cogs_total"])
        - Decimal(item["spend"])
    ).quantize(_Q4, rounding=ROUND_HALF_UP)

    for field in (
        "projection_status",
        "projection_basis_order_count",
        "projection_basis_qty",
        "projection_basis_sales",
        "projection_basis_refund_amount",
        "projection_terminal_basis_order_count",
        "projection_terminal_basis_sales",
        "projection_terminal_full_loss_sales",
        "projection_terminal_full_loss_order_count",
        "projection_terminal_full_loss_qty",
        "projection_completed_basis_order_count",
        "projection_completed_full_loss_order_count",
        "projection_full_loss_basis_order_count",
        "projection_basis_full_loss_order_count",
        "projection_basis_full_loss_qty",
        "projection_refund_amount_rate",
        "pre_delivery_full_loss_rate",
        "completed_full_loss_rate",
        "delivered_full_loss_rate",
        "settled_full_loss_rate",
        "projection_full_loss_qty_rate",
        "delivered_unsettled_order_count",
        "full_loss_exposure_unsettled_order_count",
        "full_loss_exposure_unsettled_sales",
        "confirmed_full_loss_exposure_order_count",
        "confirmed_full_loss_exposure_refund_amount",
        "confirmed_full_loss_exposure_qty",
        "unresolved_unsettled_order_count",
        "unresolved_full_loss_exposure_order_count",
        "unresolved_unsettled_qty",
        "unresolved_full_loss_exposure_qty",
        "unresolved_unsettled_sales",
        "confirmed_unsettled_full_loss_order_count",
        "projected_future_refund_amount",
        "projected_terminal_refund_amount",
        "projected_future_full_loss_order_count",
        "projected_future_full_loss_qty",
        "projected_terminal_full_loss_qty",
        "projected_full_loss_cost",
        "projected_unsettled_net",
        "projected_net_revenue",
        "projected_net_profit",
        "projected_roi_real",
        "projected_roi_breakeven",
        "projected_nc_prime",
        "projected_cogs_kept",
        "projected_ad_gmv",
        "projected_ad_system_actual_roi",
        "projected_ad_system_max_ad_spend",
        "projected_ad_system_breakeven_roi",
    ):
        assert body["totals"][field] == item[field], field


def test_spu_roi_excludes_delivered_unsettled_orders_from_full_loss_exposure(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed(sess, _seed_delivery_aware_projection_scenario)

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_PROJECTION"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]

    # Three orders await settlement, but only the partly refunded IN_TRANSIT
    # order remains exposed. The other two are delivery-terminal by independent
    # evidence: one by sales_orders.status and one by shipment facts.
    assert item["unsettled_order_count"] == 3
    assert item["delivered_unsettled_order_count"] == 2
    assert item["full_loss_exposure_unsettled_order_count"] == 1
    assert item["unresolved_unsettled_order_count"] == 3
    assert Decimal(item["projected_future_full_loss_order_count"]) == Decimal(0)
    # Delivered sample: one two-piece refund among three delivered orders.
    # The exposed order already has one confirmed lost piece, so no future
    # quantity remains after subtracting the confirmed result.
    assert Decimal(item["projected_future_full_loss_qty"]) == Decimal(0)
    assert "已送达" in response.json()["meta"]["projection"]["full_loss_target"]


def test_spu_roi_projection_with_no_unsettled_orders_matches_current_result(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed_projection_scenario(sess)
        unresolved_pk = sess.execute(
            text(
                "SELECT id FROM commerce.sales_orders "
                "WHERE order_id = 'TEST_ORDER_PROJECTION_UNRESOLVED'"
            )
        ).scalar_one()
        partial_pk = sess.execute(
            text(
                "SELECT id FROM commerce.sales_orders "
                "WHERE order_id = 'TEST_ORDER_PROJECTION_PARTIAL'"
            )
        ).scalar_one()
        _seed_settlement(
            sess,
            order_pk=unresolved_pk,
            external_id="TEST_TXN_PROJECTION_UNRESOLVED",
            amount_vnd="400000",
        )
        _seed_settlement(
            sess,
            order_pk=partial_pk,
            external_id="TEST_TXN_PROJECTION_PARTIAL",
            amount_vnd="100000",
        )
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_PROJECTION"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    item = body["items"][0]

    assert item["projection_status"] == "no_unsettled_orders"
    assert item["unsettled_order_count"] == 0
    assert item["projected_future_full_loss_qty"] == "0"
    assert item["projected_unsettled_net"] == "0.0000"
    assert item["projected_net_revenue"] == item["net_revenue"]
    assert item["projected_net_profit"] == item["net_profit"]
    assert item["projected_roi_real"] == item["roi_real"]
    assert item["projected_roi_breakeven"] == item["roi_breakeven"]
    assert item["projected_ad_system_actual_roi"] == item["ad_system_actual_roi"]
    assert item["projected_ad_system_breakeven_roi"] == (
        item["ad_system_breakeven_roi"]
    )
    assert body["totals"]["projection_status"] == "no_unsettled_orders"
    assert body["totals"]["projected_net_profit"] == body["totals"]["net_profit"]


def test_projection_sample_is_independent_but_target_follows_reporting_window(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed_projection_scenario(sess)
        sess.execute(
            text(
                "UPDATE commerce.sales_orders SET order_time = "
                "'2026-08-01T08:00:00+00:00' "
                "WHERE order_id IN ("
                "'TEST_ORDER_PROJECTION_SETTLED', "
                "'TEST_ORDER_PROJECTION_PARTIAL'"
                ")"
            )
        )
        sess.commit()

    headers = {"Authorization": f"Bearer {readonly_key}"}
    default_response = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={"q": "TEST_ROI_SPU_PROJECTION"},
    )
    assert default_response.status_code == 200, default_response.text
    assert default_response.json()["meta"]["projection"]["lookback_days"] == 30

    all_time = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={
            "q": "TEST_ROI_SPU_PROJECTION",
            "projection_lookback_days": 90,
        },
    ).json()
    september = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={
            "q": "TEST_ROI_SPU_PROJECTION",
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
            "projection_lookback_days": 90,
        },
    ).json()

    all_time_item = all_time["items"][0]
    september_item = september["items"][0]
    assert all_time_item["projection_status"] == "available"
    assert september_item["projection_status"] == "available"

    # The mature 90-day sample remains independent from the selected reporting
    # dates, but only unsettled orders in the selected dates are forecast.
    for key in (
        "sample_start",
        "sample_end",
        "lookback_days",
        "maturity_lag_days",
        "as_of",
        "basis_order_count",
        "basis_full_loss_order_count",
        "completed_full_loss_rate",
    ):
        assert all_time["meta"]["projection"][key] == september["meta"][
            "projection"
        ][key]
    assert all_time_item["projection_basis_order_count"] == 1
    assert september_item["projection_basis_order_count"] == 1
    assert all_time_item["unsettled_order_count"] == 2
    assert september_item["unsettled_order_count"] == 1
    assert all_time_item["unresolved_unsettled_order_count"] == 2
    assert september_item["unresolved_unsettled_order_count"] == 1

    one_minus_fee = Decimal(1) - FEE_BASELINE
    vnd_cny = USD_CNY / USD_VND
    assert all_time_item["unsettled_net"] == m4(
        Decimal(500_000) * one_minus_fee * vnd_cny
    )
    assert september_item["unsettled_net"] == m4(
        Decimal(400_000) * one_minus_fee * vnd_cny
    )
    assert all_time["totals"]["projected_net_profit"] != september["totals"][
        "projected_net_profit"
    ]
    assert september["totals"]["projected_net_profit"] == september["totals"][
        "net_profit"
    ]
    assert "projection_low_sample" in all_time["meta"]["projection"]["warnings"]


def test_projection_target_is_empty_when_reporting_window_has_no_activity(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed_projection_scenario(sess)
        sess.commit()

    headers = {"Authorization": f"Bearer {readonly_key}"}
    params = {"q": "TEST_ROI_SPU_PROJECTION", "projection_lookback_days": 90}
    unrestricted = api_client.get(
        "/v2/analytics/spu-roi", headers=headers, params=params
    ).json()
    excluded = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={
            **params,
            "w_start": "2027-01-01",
            "w_end": "2027-01-31",
            "include_all": "true",
        },
    ).json()

    assert excluded["items"]
    # The fixed mature sample remains available, but a reporting window with no
    # orders has no current forecast target or future adjustment.
    for key in (
        "sample_start",
        "sample_end",
        "lookback_days",
        "maturity_lag_days",
        "as_of",
        "basis_order_count",
        "basis_full_loss_order_count",
        "completed_full_loss_rate",
    ):
        assert unrestricted["meta"]["projection"][key] == excluded["meta"][
            "projection"
        ][key]
    assert excluded["totals"]["projection_basis_order_count"] == unrestricted[
        "totals"
    ]["projection_basis_order_count"]
    assert excluded["totals"]["unsettled_order_count"] == 0
    assert excluded["totals"]["full_loss_exposure_unsettled_order_count"] == 0
    assert excluded["totals"]["projection_status"] == "no_unsettled_orders"
    assert excluded["totals"]["projected_net_profit"] == excluded["totals"][
        "net_profit"
    ]


def test_paid_cancelled_returned_order_is_strict_full_loss_sample(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed_terminal_delivery_risk_scenario(sess)
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["projection_terminal_full_loss_order_count"] == 1
    assert item["projection_completed_full_loss_order_count"] >= 1


def test_projection_excludes_unpaid_cancelled_returned_order_from_strict_sample(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        spu_pk = _seed_terminal_delivery_risk_scenario(sess)
        shop_pk = sess.execute(
            text(
                "SELECT shop_pk FROM commerce.products_spu WHERE id = :spu_pk"
            ),
            {"spu_pk": spu_pk},
        ).scalar_one()
        sess.commit()
        baseline = api_client.get(
            "/v2/analytics/spu-roi",
            headers={"Authorization": f"Bearer {readonly_key}"},
            params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
        ).json()["items"][0]
        order_pk = _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_UNPAID_CANCELLED_RETURNED",
            status="CANCELLED",
            line_ext="TEST_LINE_UNPAID_CANCELLED_RETURNED",
            qty="1",
            unit_price="100000",
            paid=False,
        )
        shipment_id = sess.execute(
            text(
                "INSERT INTO fulfillment.shipments ("
                " order_pk, external_package_id, status"
                ") VALUES (:order_pk, 'TEST_PKG_UNPAID_CANCELLED_RETURNED',"
                " 'RETURNED_TO_SELLER') RETURNING id"
            ),
            {"order_pk": order_pk},
        ).scalar_one()
        sess.execute(
            text(
                "INSERT INTO fulfillment.tracking_events ("
                " shipment_id, external_event_key, action_code, event_at, description"
                ") VALUES (:shipment_id, 'TEST_EVENT_UNPAID_CANCELLED_RETURNED',"
                " 80101, now(), 'returned to seller')"
            ),
            {"shipment_id": shipment_id},
        )
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["projection_completed_full_loss_order_count"] == baseline[
        "projection_completed_full_loss_order_count"
    ]
    assert item["projection_terminal_full_loss_order_count"] == baseline[
        "projection_terminal_full_loss_order_count"
    ]


def test_paid_non_cancelled_returned_order_is_completed_full_loss_not_risk(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        spu_pk = _seed_terminal_delivery_risk_scenario(sess)
        shop_pk = sess.execute(
            text(
                "SELECT shop_pk FROM commerce.products_spu WHERE id = :spu_pk"
            ),
            {"spu_pk": spu_pk},
        ).scalar_one()
        sess.commit()
        baseline = api_client.get(
            "/v2/analytics/spu-roi",
            headers={"Authorization": f"Bearer {readonly_key}"},
            params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
        ).json()["items"][0]
        order_pk = _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_PAID_RETURNED_NON_CANCELLED",
            status="IN_TRANSIT",
            line_ext="TEST_LINE_PAID_RETURNED_NON_CANCELLED",
            qty="1",
            unit_price="100000",
            paid=True,
        )
        shipment_id = sess.execute(
            text(
                "INSERT INTO fulfillment.shipments ("
                " order_pk, external_package_id, status"
                ") VALUES (:order_pk, 'TEST_PKG_PAID_RETURNED_NON_CANCELLED',"
                " 'RETURNED_TO_SELLER') RETURNING id"
            ),
            {"order_pk": order_pk},
        ).scalar_one()
        sess.execute(
            text(
                "INSERT INTO fulfillment.tracking_events ("
                " shipment_id, external_event_key, action_code, event_at, description"
                ") VALUES (:shipment_id, 'TEST_EVENT_PAID_RETURNED_NON_CANCELLED',"
                " 80101, now(), 'returned to seller')"
            ),
            {"shipment_id": shipment_id},
        )
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_TERMINAL_DELIVERY_RISK"},
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["projection_completed_full_loss_order_count"] > baseline[
        "projection_completed_full_loss_order_count"
    ]
    assert item["full_loss_exposure_unsettled_order_count"] == baseline[
        "full_loss_exposure_unsettled_order_count"
    ]


def test_projection_sample_keeps_selected_spus_when_one_has_no_window_activity(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        first_spu = _seed_projection_scenario(sess)
        shop_pk = sess.execute(
            text("SELECT shop_pk FROM commerce.products_spu WHERE id = :spu_pk"),
            {"spu_pk": first_spu},
        ).scalar_one()
        second_spu = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_PROJECTION_SECOND")
        second_order = _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=second_spu,
            order_id="TEST_ORDER_PROJECTION_SECOND",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_PROJECTION_SECOND",
            qty="1",
            unit_price="100000",
            paid=True,
        )
        sess.execute(
            text(
                "UPDATE commerce.sales_orders SET order_time = "
                "'2026-08-01T08:00:00+00:00' WHERE id = :order_pk"
            ),
            {"order_pk": second_order},
        )
        _seed_settlement(
            sess,
            order_pk=second_order,
            external_id="TEST_TXN_PROJECTION_SECOND",
            amount_vnd="100000",
        )
        sess.commit()

    headers = {"Authorization": f"Bearer {readonly_key}"}
    base_params = {
        "shop_pk": shop_pk,
        "projection_lookback_days": 90,
    }
    query_count = 0
    auth_query_count = 0

    def count_query(_conn, _cursor, statement, *_args) -> None:
        nonlocal query_count, auth_query_count
        query_count += 1
        if (
            statement.lstrip().startswith("SELECT 1 FROM commerce.shops s WHERE s.id = ")
            and "shop_credential" in statement
            and "shop_app" in statement
        ):
            auth_query_count += 1

    event.listen(db_engine, "before_cursor_execute", count_query)
    try:
        all_dates = api_client.get(
            "/v2/analytics/spu-roi", headers=headers, params=base_params
        ).json()
    finally:
        event.remove(db_engine, "before_cursor_execute", count_query)
    # b8a64dc5 在时区和目录读取各加一条固定授权 SELECT，独立限制以拦住授权 N+1。
    # 其他所有 SQL 保留原 31 条预算（30 条既有语句 + 一条价格观察表 capability 探测）。
    assert auth_query_count <= 2, (query_count, auth_query_count)
    assert query_count - auth_query_count <= 31, (query_count, auth_query_count)
    narrowed = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={
            **base_params,
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
        },
    ).json()

    # The second SPU still contributes to the selected-scope mature sample, but
    # its August current facts do not enter September projected final results.
    for key in (
        "sample_start",
        "sample_end",
        "lookback_days",
        "basis_order_count",
        "basis_full_loss_order_count",
        "completed_full_loss_rate",
    ):
        assert all_dates["meta"]["projection"][key] == narrowed["meta"][
            "projection"
        ][key]
    assert narrowed["totals"]["projection_basis_order_count"] == all_dates[
        "totals"
    ]["projection_basis_order_count"]
    assert narrowed["totals"]["unsettled_order_count"] == all_dates["totals"][
        "unsettled_order_count"
    ]
    assert narrowed["totals"]["projected_net_profit"] != all_dates["totals"][
        "projected_net_profit"
    ]


def test_profitability_public_interface_returns_typed_consistent_result(
    db_engine,
):
    with Session(db_engine) as seed_session:
        spu_pk = _seed(seed_session, _seed_scenario_a)
        shop_pk = seed_session.execute(
            text("SELECT shop_pk FROM commerce.products_spu WHERE id = :pk"),
            {"pk": spu_pk},
        ).scalar_one()

    with Session(db_engine) as session:
        result = read_overview(
            session,
            scope=ProfitScope(shop_pk=shop_pk),
            view=RowView(search="TEST_ROI_SPU_A"),
        )
        assert session.execute(text("SHOW transaction_isolation")).scalar_one() == (
            "repeatable read"
        )
        assert session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"

    assert result.total == 1
    assert isinstance(result.items[0].net_profit, Decimal)
    assert result.items[0].net_profit.quantize(_Q4, rounding=ROUND_HALF_UP) == Decimal(
        m4(scenario_a_net_profit_cny())
    )
    assert isinstance(result.totals.net_profit, Decimal)
    assert result.items[0].ad_system_actual_roi == Decimal(5)
    assert result.items[0].ad_system_max_ad_spend.quantize(
        _Q4, rounding=ROUND_HALF_UP
    ) == Decimal(m4(scenario_a_max_ad_spend_cny()))
    assert result.totals.ad_system_breakeven_roi is not None
    assert result.totals.ad_system_remaining_ad_spend_capacity.quantize(
        _Q4, rounding=ROUND_HALF_UP
    ) == Decimal(m4(scenario_a_net_profit_cny()))
    assert result.basis.rubric_version == "v10"
    assert result.basis.display_currency == "CNY"
    assert result.basis.fx.snapshot_id > 0
    assert result.basis.calculated_at.tzinfo is not None

    with Session(db_engine) as session:
        explanation = explain_spu(
            session,
            scope=ProfitScope(shop_pk=shop_pk),
            spu_pk=spu_pk,
            evidence=EvidenceRequest(frozenset({EvidenceKind.ORDERS})),
        )
    assert explanation.result.spu_pk == spu_pk
    assert explanation.basis.calculated_at.tzinfo is not None
    assert EvidenceKind.ORDERS in explanation.evidence.rows
    order_evidence = explanation.evidence.rows[EvidenceKind.ORDERS][0]
    assert isinstance(order_evidence["line_gmv"], Decimal)
    assert isinstance(order_evidence["paid_at"], datetime)


def test_profitability_focused_selection_matches_exact_scope(db_engine) -> None:
    with Session(db_engine) as seed_session:
        spu_pk = _seed(seed_session, _seed_scenario_a)
        row = seed_session.execute(
            text("SELECT shop_pk, spu_id FROM commerce.products_spu WHERE id = :pk"),
            {"pk": spu_pk},
        ).one()
        shop_pk, spu_id = int(row.shop_pk), str(row.spu_id)
        seed_session.execute(
            text(
                "INSERT INTO reporting.focused_spus (shop_pk, spu_id) "
                "VALUES (:shop_pk, :spu_id)"
            ),
            {"shop_pk": shop_pk, "spu_id": spu_id},
        )
        seed_session.commit()

    with Session(db_engine) as session:
        exact = read_overview(
            session,
            scope=ProfitScope(
                shop_pk=shop_pk,
                selection=ExactIdsSelection((spu_id,)),
            ),
            view=RowView(),
        )
    with Session(db_engine) as session:
        focused = read_overview(
            session,
            scope=ProfitScope(shop_pk=shop_pk, selection=FocusedSelection()),
            view=RowView(),
        )

    assert focused.items == exact.items
    assert focused.totals == exact.totals
    assert focused.basis.rubric_version == exact.basis.rubric_version


def test_profitability_empty_focused_selection_never_falls_back(db_engine) -> None:
    with Session(db_engine) as seed_session:
        spu_pk = _seed(seed_session, _seed_scenario_a)
        shop_pk = int(
            seed_session.execute(
                text("SELECT shop_pk FROM commerce.products_spu WHERE id = :pk"),
                {"pk": spu_pk},
            ).scalar_one()
        )

    with Session(db_engine) as session:
        result = read_overview(
            session,
            scope=ProfitScope(shop_pk=shop_pk, selection=FocusedSelection()),
            view=RowView(),
        )

    assert result.items == ()
    assert result.total == 0
    assert result.totals.row_count == 0


def test_spu_roi_focused_scope_validation(api_client, readonly_key) -> None:
    headers = {"Authorization": f"Bearer {readonly_key}"}
    missing_shop = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={"scope": "focused"},
    )
    assert missing_shop.status_code == 422
    conflict = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={
            "scope": "focused",
            "shop_pk": 1,
            "spu_ids": "TEST_ROI_SPU_A",
        },
    )
    assert conflict.status_code == 422
    unknown = api_client.get(
        "/v2/analytics/spu-roi",
        headers=headers,
        params={"scope": "unknown", "shop_pk": 1},
    )
    assert unknown.status_code == 422


def test_profitability_public_include_inactive_semantics(db_engine) -> None:
    with Session(db_engine) as session:
        shop_pk = _seed_shop(session, "TEST_SELLER_PUBLIC_SCOPE")
        active_pk = _seed_spu(
            session, shop_pk, "TEST_ROI_PUBLIC_ACTIVE", status="ACTIVATE"
        )
        inactive_pk = _seed_spu(
            session, shop_pk, "TEST_ROI_PUBLIC_INACTIVE", status="DEACTIVATE"
        )
        for spu_pk, suffix in ((active_pk, "A"), (inactive_pk, "I")):
            _seed_order_line(
                session,
                shop_pk=shop_pk,
                spu_pk=spu_pk,
                order_id=f"TEST_ORDER_PUBLIC_{suffix}",
                status=PAID_ORDER_STATUS,
                line_ext=f"TEST_LINE_PUBLIC_{suffix}",
                qty="1",
                unit_price="263300",
                paid=True,
            )
        session.commit()

    with Session(db_engine) as session:
        active_only = read_overview(
            session,
            scope=ProfitScope(shop_pk=shop_pk, include_inactive=False),
            view=RowView(),
        )
    assert {row.spu_pk for row in active_only.items} == {active_pk}

    with Session(db_engine) as session:
        including_inactive = read_overview(
            session,
            scope=ProfitScope(shop_pk=shop_pk, include_inactive=True),
            view=RowView(),
        )
    assert {row.spu_pk for row in including_inactive.items} == {
        active_pk,
        inactive_pk,
    }


def test_spu_roi_totals_cross_spu_dedup_and_gmv_split(
    api_client, readonly_key, db_engine
):
    """review MINOR:一张跨 SPU 的订单(两 SPU 各一行)在 totals 里只计一次。

    - 行加总 ∑order_count = 2(X/Y 各 1)≠ totals.order_count = 1(全局去重)
    - 已付被取消同理:totals.cancelled_order_count = 1
    - GMV 拆分:X/Y 各分摊有效 10 USD + 取消原额 5 USD；API 统一换算 CNY
    """
    with Session(db_engine) as sess:
        x, _y = _seed(sess, _seed_cross_spu_orders)
        shop_pk = sess.execute(
            text("SELECT shop_pk FROM commerce.products_spu WHERE id = :spu_pk"),
            {"spu_pk": x},
        ).scalar_one()
        _seed_spu(
            sess,
            shop_pk,
            "TEST_ROI_SPU_X_EXTRA",
            title="TEST selector title needle",
        )
        other_shop_pk = _seed_shop(sess, "TEST_SELLER_XY_OTHER")
        _seed_spu(sess, other_shop_pk, "TEST_ROI_SPU_X", title="other shop")
        sess.commit()

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2, body["items"]
    row_sum_orders = sum(i["order_count"] for i in body["items"])
    assert row_sum_orders == 2  # X、Y 行各计 1
    t = body["totals"]
    assert t["row_count"] == 2
    assert t["order_count"] == 1, "跨 SPU 订单在 totals 应全局去重"
    assert t["cancelled_order_count"] == 1
    assert t["total_orders"] == 2
    assert t["sales"] == cny4_from_usd("20")  # 10+10(行级各自归属)
    assert t["gmv"] == cny4_from_usd("30")  # 有效 20 + 取消原额 10
    # 单行归属校验:每 SPU 只带自己那行金额
    by_id = {i["spu_id"]: i for i in body["items"]}
    assert by_id["TEST_ROI_SPU_X"]["sales"] == cny4_from_usd("10")
    assert by_id["TEST_ROI_SPU_Y"]["sales"] == cny4_from_usd("10")
    assert by_id["TEST_ROI_SPU_X"]["order_count"] == 1
    assert by_id["TEST_ROI_SPU_Y"]["order_count"] == 1
    # 行和大盘公式相同，但多 SPU 订单数量必须在大盘范围重新去重。
    assert sum(i["total_orders"] for i in body["items"]) == 4
    assert t["total_orders"] == 2
    assert sum(i["effective_order_count"] for i in body["items"]) == 2
    assert t["effective_order_count"] == 1
    assert {i["cancel_rate"] for i in body["items"]} == {"0.50"}
    assert t["cancel_rate"] == "0.5000"
    # Projection order counts are also order-dimension facts: the same shared
    # unsettled order appears in both SPU rows but once in dashboard totals.
    assert sum(i["unsettled_order_count"] for i in body["items"]) == 2
    assert t["unsettled_order_count"] == 1
    assert sum(i["unresolved_unsettled_order_count"] for i in body["items"]) == 2
    assert t["unresolved_unsettled_order_count"] == 1

    filtered = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"shop_pk": shop_pk, "q": "TEST_ROI_SPU_X"},
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["spu_id"] == "TEST_ROI_SPU_X"
    assert filtered["totals"]["row_count"] == 2
    assert filtered["totals"]["order_count"] == 1

    # 精确 SPU scope 必须同时约束 items 与大盘；中文/英文逗号、空格、重复值
    # 等价，且跨 SPU 共享订单仍由大盘做 distinct。
    selected = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "shop_pk": shop_pk,
            "spu_ids": " TEST_ROI_SPU_X，TEST_ROI_SPU_Y,TEST_ROI_SPU_X ",
        },
    )
    assert selected.status_code == 200, selected.text
    selected_body = selected.json()
    assert {item["spu_id"] for item in selected_body["items"]} == {
        "TEST_ROI_SPU_X",
        "TEST_ROI_SPU_Y",
    }
    assert selected_body["total"] == 2
    assert selected_body["totals"]["row_count"] == 2
    assert selected_body["totals"]["order_count"] == 1
    assert selected_body["totals"]["cancelled_order_count"] == 1

    only_x = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"shop_pk": shop_pk, "spu_ids": "TEST_ROI_SPU_X"},
    )
    assert only_x.status_code == 200, only_x.text
    only_x_body = only_x.json()
    assert [item["spu_id"] for item in only_x_body["items"]] == [
        "TEST_ROI_SPU_X"
    ]
    assert only_x_body["total"] == 1
    assert only_x_body["totals"]["row_count"] == 1
    assert only_x_body["totals"]["order_count"] == 1
    assert only_x_body["totals"]["cancelled_order_count"] == 1
    assert only_x_body["totals"]["gmv"] == cny4_from_usd("15")

    options = api_client.get(
        "/v2/commerce/channel-product-options",
        headers=h,
        params={
            "shop_pk": shop_pk,
            "spu_ids": "TEST_ROI_SPU_X，TEST_ROI_SPU_Y",
        },
    )
    assert options.status_code == 200, options.text
    assert [option["spu_id"] for option in options.json()] == [
        "TEST_ROI_SPU_X",
        "TEST_ROI_SPU_Y",
    ]
    searched_options = api_client.get(
        "/v2/commerce/channel-product-options",
        headers=h,
        params={"shop_pk": shop_pk, "q": "title needle"},
    )
    assert searched_options.status_code == 200, searched_options.text
    assert [option["spu_id"] for option in searched_options.json()] == [
        "TEST_ROI_SPU_X_EXTRA"
    ]

    # The same cross-SPU order also counts once when it becomes a settled
    # projection sample, even though each SPU row reports one sample order.
    with Session(db_engine) as sess:
        shared_order_pk = sess.execute(
            text(
                "SELECT id FROM commerce.sales_orders "
                "WHERE order_id = 'TEST_ORDER_XY1'"
            )
        ).scalar_one()
        _seed_settlement(
            sess,
            order_pk=shared_order_pk,
            external_id="TEST_TXN_XY1",
            amount_vnd="526600",
        )
        sess.commit()
    settled_scope = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "shop_pk": shop_pk,
            "spu_ids": "TEST_ROI_SPU_X,TEST_ROI_SPU_Y",
        },
    ).json()
    assert sum(
        item["projection_basis_order_count"] for item in settled_scope["items"]
    ) == 2
    assert settled_scope["totals"]["projection_basis_order_count"] == 1
    assert settled_scope["totals"]["unsettled_order_count"] == 0


def _seed_refund_on_shared_order_y_line(sess) -> tuple[int, int]:
    """跨 SPU 订单退款归属(P2-1 回归):O1 含 X/Y 两行,仅 Y 行有退款 case。

    行级退款订单数必须按【行】归属:Y 的 refund_order_count=1/refund_rate_qty=1.00,
    X 保持 0.00(不能因同订单另一 SPU 行退款而虚增)。
    """
    seller = "TEST_SELLER_RY"
    shop_pk = _seed_shop(sess, seller)
    x = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_RY_X")
    y = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_RY_Y")
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_RY1",
        status="DELIVERED",
        line_ext="TEST_LINE_RY1",
        qty="1",
        unit_price="263300",
        paid=True,  # $10
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_RY1",
        spu_pk=y,
        line_ext="TEST_LINE_RY2",
        qty="1",
        unit_price="263300",  # $10
    )
    y_line = sess.execute(
        text(
            "SELECT id FROM commerce.sales_order_lines "
            "WHERE order_pk = (SELECT id FROM commerce.sales_orders "
            "WHERE order_id = 'TEST_ORDER_RY1' LIMIT 1) "
            "AND external_line_id = 'TEST_LINE_RY2'"
        )
    ).scalar_one()
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_RY1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(y_line, "TEST_CLINE_RY1", "1", "263300")],
    )
    return x, y


def test_spu_roi_refund_order_count_attributed_to_own_line(
    api_client, readonly_key, db_engine
):
    """P2-1:退款订单数按行归属——共享订单仅 Y 行退款时,X 的退货率不虚增。"""
    with Session(db_engine) as sess:
        _seed(sess, _seed_refund_on_shared_order_y_line)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_RY"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {i["spu_id"]: i for i in body["items"]}
    assert set(by_id) == {"TEST_ROI_SPU_RY_X", "TEST_ROI_SPU_RY_Y"}
    assert by_id["TEST_ROI_SPU_RY_X"]["refund_rate_qty"] == "0.00"
    assert by_id["TEST_ROI_SPU_RY_X"]["refund_net_amount"] == "0.0000"
    assert (
        by_id["TEST_ROI_SPU_RY_Y"]["refund_rate_qty"] == "1.00"
    )  # 1 退货单 / 1 有效单
    assert by_id["TEST_ROI_SPU_RY_Y"]["refund_net_amount"] == cny4_from_usd("10")


def test_profitability_order_evidence_attributes_refund_to_own_spu_line(
    db_engine,
) -> None:
    with Session(db_engine) as session:
        x_pk, y_pk = _seed(session, _seed_refund_on_shared_order_y_line)
        shop_pk = session.execute(
            text("SELECT shop_pk FROM commerce.products_spu WHERE id = :pk"),
            {"pk": x_pk},
        ).scalar_one()

    explanations = {}
    for spu_pk in (x_pk, y_pk):
        with Session(db_engine) as session:
            explanations[spu_pk] = explain_spu(
                session,
                scope=ProfitScope(shop_pk=shop_pk),
                spu_pk=spu_pk,
                evidence=EvidenceRequest(frozenset({EvidenceKind.ORDERS})),
            )

    x_order = explanations[x_pk].evidence.rows[EvidenceKind.ORDERS][0]
    y_order = explanations[y_pk].evidence.rows[EvidenceKind.ORDERS][0]
    assert explanations[x_pk].result.full_loss_qty == 0
    assert x_order["full_loss"] is False
    assert explanations[y_pk].result.full_loss_qty == 1
    assert y_order["full_loss"] is True


def test_profitability_settlement_evidence_uses_order_window(
    api_client, readonly_key, db_engine
) -> None:
    with Session(db_engine) as session:
        spu_pk = _seed_scenario_a(session)
        order_pk, shop_pk = session.execute(
            text(
                "SELECT so.id, so.shop_pk FROM commerce.sales_orders so "
                "JOIN commerce.sales_order_lines sl ON sl.order_pk = so.id "
                "WHERE sl.spu_pk = :spu_pk AND so.order_id = 'TEST_ORDER_A1'"
            ),
            {"spu_pk": spu_pk},
        ).one()
        statement_pk = session.execute(
            text(
                "INSERT INTO finance.settlement_statements "
                "(external_statement_id, statement_time, currency) "
                "VALUES ('TEST_STATEMENT_LATE', '2026-10-01T00:00:00+00:00', 'VND') "
                "RETURNING id"
            )
        ).scalar_one()
        transaction_pk = session.execute(
            text(
                "INSERT INTO finance.settlement_transactions "
                "(settlement_statement_id, external_transaction_id, order_pk, "
                "transaction_time) VALUES (:statement_pk, 'TEST_TXN_LATE', :order_pk, "
                "'2026-10-01T00:00:00+00:00') RETURNING id"
            ),
            {"statement_pk": statement_pk, "order_pk": order_pk},
        ).scalar_one()
        session.execute(
            text(
                "INSERT INTO finance.settlement_components "
                "(transaction_id, component_code, amount, currency) "
                "VALUES (:transaction_pk, 'SETTLEMENT', 1.0000, 'VND')"
            ),
            {"transaction_pk": transaction_pk},
        )
        session.commit()

    with Session(db_engine) as session:
        explanation = explain_spu(
            session,
            scope=ProfitScope(
                shop_pk=shop_pk,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 1),
            ),
            spu_pk=spu_pk,
            evidence=EvidenceRequest(frozenset({EvidenceKind.SETTLEMENTS})),
        )

    assert explanation.result.settled_order_count == 1
    settlement = explanation.evidence.rows[EvidenceKind.SETTLEMENTS][0]
    assert isinstance(settlement["statement_time"], datetime)
    components = settlement["components"]
    assert isinstance(components, tuple)
    component = components[0]
    assert isinstance(component, Mapping)
    assert component["amount_vnd"] == Decimal("1.0000")
    assert component["amount"] == Decimal("1.0000") / (USD_VND / USD_CNY)

    response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/settlements",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"w_start": "2026-09-01", "w_end": "2026-09-01"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["currency"]["display"] == "CNY"
    wire_component = payload["settlements"][0]["components"][0]
    assert wire_component["amount_vnd"] == "1.0000"
    assert wire_component["amount"] == m4(Decimal(1) / (USD_VND / USD_CNY))


def test_profitability_settlement_share_includes_unattributed_order_lines(
    api_client, readonly_key, db_engine
) -> None:
    with Session(db_engine) as session:
        spu_pk = _seed_scenario_a(session)
        order_pk, shop_pk = session.execute(
            text(
                "SELECT so.id, so.shop_pk FROM commerce.sales_orders so "
                "WHERE so.order_id = 'TEST_ORDER_A1'"
            )
        ).one()
        session.execute(
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
                "VALUES (:order_pk, 'TEST_LINE_UNATTRIBUTED', NULL, 5, 526600, 'VND')"
            ),
            {"order_pk": order_pk},
        )
        statement_pk = session.execute(
            text(
                "INSERT INTO finance.settlement_statements "
                "(external_statement_id, statement_time, currency) "
                "VALUES ('TEST_STATEMENT_SHARE', '2026-09-02T00:00:00+00:00', 'VND') "
                "RETURNING id"
            )
        ).scalar_one()
        transaction_pk = session.execute(
            text(
                "INSERT INTO finance.settlement_transactions "
                "(settlement_statement_id, external_transaction_id, order_pk, "
                "transaction_time) VALUES (:statement_pk, 'TEST_TXN_SHARE', :order_pk, "
                "'2026-09-02T00:00:00+00:00') RETURNING id"
            ),
            {"statement_pk": statement_pk, "order_pk": order_pk},
        ).scalar_one()
        session.execute(
            text(
                "INSERT INTO finance.settlement_components "
                "(transaction_id, component_code, amount, currency) "
                "VALUES (:transaction_pk, 'SETTLEMENT', 2633000, 'VND')"
            ),
            {"transaction_pk": transaction_pk},
        )
        session.commit()

    with Session(db_engine) as session:
        explanation = explain_spu(
            session,
            scope=ProfitScope(shop_pk=shop_pk),
            spu_pk=spu_pk,
            evidence=EvidenceRequest(frozenset({EvidenceKind.SETTLEMENTS})),
        )

    assert explanation.result.settled_net == Decimal(1316500) / (USD_VND / USD_CNY)
    settlement = explanation.evidence.rows[EvidenceKind.SETTLEMENTS][0]
    assert settlement["share_ratio"] == Decimal("0.5")

    response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/orders",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["currency"]["display"] == "CNY"
    order = next(row for row in payload["orders"] if row["order_id"] == "TEST_ORDER_A1")
    assert order["line_gmv"] == cny4_from_usd("100")
    assert order["settled_net_share"] == cny4_from_usd("50")


def _seed_cod_and_unpaid_cancelled(sess) -> int:
    """全链状态口径回归场景(2026-09-06):COD 在途单 + 未收款取消单。

    - TEST_ROI_SPU_COD: 有效单(DELIVERED,paid,3×$10=$30)
    - COD 在途单(IN_TRANSIT,paid_at=NULL,行 2×$10=$20) → 算"有效订单"
    - 未收款取消单(CANCELLED,paid_at=NULL,行 1×$10=$10) → 算"取消单"

    2026-09-06 全链状态口径:行级与 totals 都不再卡 paid_at ——
    order_count=2(有效已付+COD在途)、cancelled=1、total=3;
    sales=50(含在途COD 20,不含取消)、gmv=60(含取消原额);
    派生 net_profit/units_sold 等也随行级 sales 走状态口径。
    """
    seller = "TEST_SELLER_COD"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_COD")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_COD",
        campaign_id="TEST_CAMP_COD",
        spend="10.00",
        orders="3",
        gmv="30.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD1",
        status="DELIVERED",
        line_ext="TEST_LINE_COD1",
        qty="3",
        unit_price="263300",
        paid=True,  # $30
    )
    # COD 在途:状态白名单但钱未收(paid_at 不落)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD2",
        status="IN_TRANSIT",
        line_ext="TEST_LINE_COD2",
        qty="2",
        unit_price="263300",
        paid=False,  # $20 未收款
    )
    # 未收款取消:CANCELLED 且 paid_at 无
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD3",
        status="CANCELLED",
        line_ext="TEST_LINE_COD3",
        qty="1",
        unit_price="263300",
        paid=False,  # $10 未收款取消
    )
    return spu_pk


def test_spu_roi_totals_order_status_scope_cod_shop(
    api_client, readonly_key, db_engine
):
    """2026-09-06:全链状态口径(COD 店下单即算单)。

    行级与 totals 一致:销售/件数/单量不看 paid_at(COD 在途计入有效、
    未收款取消计入取消),GMV=全部原始行金额。金额主指标(净利润/ROI)
    跟随行级 sales 同步为状态口径。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_cod_and_unpaid_cancelled)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_COD"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    # 行级也按状态口径(2026-09-06 全链):COD 在途算有效订单/件数/销售
    assert item["order_count"] == 2, "行级有效订单数含 COD 在途单"
    assert item["units_sold"] == 5  # 3(已收) + 2(在途)
    assert item["sales"] == cny4_from_usd("50")  # 30 + 20,含在途 COD
    assert item["refund_net_amount"] == "0.0000"
    # 派生:净现金 50 − COGS_all(5×4.4322=22.161) − spend 10 − fee(50×0.1156=5.78)
    assert item["platform_fee"] == cny4_from_usd(Decimal(50) * FEE_BASELINE)
    expected_net_profit = (
        Decimal(50) * USD_CNY * (Decimal(1) - FEE_BASELINE)
        - Decimal(5) * K1_CNY
        - Decimal(10) * USD_CNY
    )
    assert item["net_profit"] == m4(expected_net_profit)
    # 行内新列(2026-09-06 列集):销售=GMV全单(50+10 取消原额)、取消单量、取消率、退货率(单量)
    assert item["gmv_sales"] == cny4_from_usd("60"), "行内销售 = 有效销售 + 取消原额"
    assert item["cancelled_order_count"] == 1
    assert item["cancel_rate"] == m2(Decimal(1) / Decimal(3))  # 1/(2+1)=0.33
    assert item["refund_rate_qty"] == "0.00"  # 0 退货订单 / 2 有效单
    t = body["totals"]
    assert t["order_count"] == 2
    assert t["cancelled_order_count"] == 1, "未收款取消单应计入取消单(状态口径)"
    assert t["total_orders"] == 3
    assert t["sales"] == cny4_from_usd("50")  # 与行级一致 = 状态口径
    assert t["gmv"] == cny4_from_usd("60"), (
        "GMV=全部订单原始行金额(含在途COD与取消原额)"
    )


def test_spu_roi_manual_cost_source(api_client, readonly_key, db_engine):
    """命中 manual_product_costs 有效行 → cost_source=MANUAL,unit_cost_used 用真值。

    v10 公式（D1 MANUAL + 完结退货直接全损）统一输出 CNY:
      cost=25 CNY；完结退货 1 件 → full_loss_qty=1
      net_revenue、广告消耗从原生币种换算为 CNY
      net_profit 等于旧 USD 结果 26.8925 ÷ cny_usd
      return_loss = 1 × 25 CNY = 3.6935
    """
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_spu_manual)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_MANUAL"},
    )
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["spu_pk"] == spu_pk
    assert item["cost_source"] == "MANUAL"
    assert Decimal(item["unit_cost_used"]) == Decimal(25)
    # v9: return_loss = 完结退货件数 × 单位成本（不论是否有物流轨迹）
    assert item["return_loss"] == "25.0000"
    # v10 net_profit（D1 MANUAL 25 CNY 成本 + D5 未结算折算）
    assert item["net_profit"] == cny4_from_usd("26.8925")


def test_spu_roi_unpaid_order_refund_defensive_unattributed(
    api_client, readonly_key, db_engine
):
    """§4.2 rule 0:UNPAID 等异常订单的已完结退款防御性进未归属。

    该退款行有 spu_pk 但订单状态不在白名单也不是 CANCELLED → 不进
    refund_net/refund_cancelled 桶、不进净额与行内金额(net_profit 视同
    无此退款),只在 meta.unattributed_refund_lines 显式 +1,不静默丢。
    """
    h = {"Authorization": f"Bearer {readonly_key}"}
    params = {"q": "TEST_ROI_SPU_AB"}
    # 造数据前的基线 meta(未归属计数是全局口径,delta 断言不受其它残留行影响)
    r0 = api_client.get("/v2/analytics/spu-roi", headers=h, params=params)
    assert r0.status_code == 200, r0.text
    base = r0.json()["meta"]["unattributed_refund_lines"]

    with Session(db_engine) as sess:
        _seed(sess, _seed_unpaid_refund_spu)

    r = api_client.get("/v2/analytics/spu-roi", headers=h, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    # 销售侧不受影响:UNPAID 单不进有效销售(DELIVERED 单 5 件×20 USD)
    assert item["order_count"] == 1
    assert item["units_sold"] == 5
    assert item["sales"] == cny4_from_usd("100")

    # 异常订单退款不进 refund_net 桶(REFUND_ONLY/RETURN 桶都保持 0)
    assert item["refund_return_qty"] == 0
    assert item["refund_return_amount"] == "0.0000"
    assert item["refund_net_qty"] == 0
    assert item["refund_net_amount"] == "0.0000"
    assert item["refund_rate"] == "0.00"
    # 也不进取消桶 / 货损
    assert item["refund_cancelled_qty"] == 0
    assert item["refund_cancelled_amount"] == "0.0000"
    assert item["refund_cancelled_missing_lines"] == 0
    assert item["return_loss"] == "0.0000"
    # 行内金额视同无此退款:v7 net_profit = 100×(1−0.308)×1.0 − 5×5.9096 − 0
    # = 69.2 − 29.5480 = 39.6520
    assert item["platform_fee"] == cny4_from_usd("30.8000")
    assert item["net_profit"] == cny4_from_usd("39.6520")

    # 防御性进未归属:meta 计数 +1(不静默)
    assert body["meta"]["unattributed_refund_lines"] == base + 1, body["meta"]


# ─── 行范围(include_all)──────────────────────────────────────────────


def test_spu_roi_excludes_inactive_by_default_includes_with_flag(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        inactive_pk = _seed(sess, _seed_inactive_spu)

    # 默认:无活动 SPU 不在行范围
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q},
    )
    body = r.json()
    assert body["total"] == 1
    assert [i["spu_id"] for i in body["items"]] == ["TEST_ROI_SPU_A"]

    # include_all=true:全量目录行,无活动行金额全 0、ROI null
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q, "include_all": "true"},
    )
    body2 = r2.json()
    assert body2["total"] == 2
    by_id = {i["spu_id"]: i for i in body2["items"]}
    assert inactive_pk == by_id["TEST_ROI_SPU_INACTIVE"]["spu_pk"]
    zero = by_id["TEST_ROI_SPU_INACTIVE"]
    assert zero["ad_count"] == 0
    assert zero["spend"] == "0.0000"
    assert zero["sales"] == "0.0000"
    assert zero["units_sold"] == 0
    assert zero["refund_net_amount"] == "0.0000"
    assert zero["net_profit"] == "0.0000"
    assert zero["roi_real"] is None
    assert zero["roi_breakeven"] is None
    assert zero["roi_l0"] is None
    assert zero["refund_rate"] is None
    assert zero["cpa"] is None


def test_spu_roi_include_all_filters_non_active_status(
    api_client, readonly_key, db_engine
):
    """§5.1-7 include_all = 全部 ACTIVE SPU:DEACTIVATE 状态不进行范围。

    默认(不含 include_all)仍按"有活动"返回(不按状态裁剪),include_all
    目录查询加 status ILIKE 'activate' 后 DEACTIVATE 目录行被排除。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_status_mix)

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 默认:有活动的 ACTIVATE + DEACTIVATE 都在(状态不参与默认行范围)
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    body = r.json()
    assert body["total"] == 2
    by_id = {i["spu_id"] for i in body["items"]}
    assert by_id == {"TEST_ROI_SPU_ACTIVE_ON", "TEST_ROI_SPU_DEACTIVE_ON"}

    # include_all=true:只含 ACTIVE 状态(含无活动),DEACTIVATE 一律排除
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "include_all": "true"},
    )
    body2 = r2.json()
    assert body2["total"] == 2
    by_id2 = {i["spu_id"] for i in body2["items"]}
    assert by_id2 == {"TEST_ROI_SPU_ACTIVE_ON", "TEST_ROI_SPU_ACTIVE_IDLE"}
    assert "TEST_ROI_SPU_DEACTIVE_ON" not in by_id2
    assert "TEST_ROI_SPU_DEACTIVE_IDLE" not in by_id2


def test_spu_roi_window_params_clip_sales_and_refunds_by_order_time(
    api_client, readonly_key, db_engine
):
    """退款金额、件数、订单数和钻取明细统一跟随原订单时间归属。"""
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_window_spu)
        win_shop_pk = sess.execute(
            text("SELECT id FROM commerce.shops WHERE shop_id = 'TEST_SELLER_WIN'")
        ).scalar_one()

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 默认(无窗口参数):全历史累计 → 两单两退款都在(带 shop_pk:coverage 只算本店,
    # 断言确定性,不受共享库其它店铺真实数据影响)
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": "TEST_ROI_SPU_WIN", "shop_pk": win_shop_pk},
    )
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["order_count"] == 2
    assert item["units_sold"] == 5
    assert item["sales"] == cny4_from_usd("100")
    assert item["refund_order_count"] == 2
    assert item["refund_return_qty"] == 2
    assert item["refund_return_amount"] == cny4_from_usd("40")
    assert body["totals"]["refund_order_count"] == 2
    assert "ad=视图全窗口累计" not in body["meta"]["window"]["note"]
    assert "未裁剪" in body["meta"]["window"]["note"]
    # 可裁剪数据覆盖范围也按订单时间，而不是售后发生时间。
    w = body["meta"]["window"]
    assert w["coverage_first_day"] == "2026-08-01"
    assert w["coverage_last_day"] == "2026-09-01"

    # 9 月窗口只纳入 9 月订单 W1；W2 虽在 9 月退款，仍归属 8 月而被排除。
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "q": "TEST_ROI_SPU_WIN",
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
        },
    )
    body2 = r2.json()
    assert body2["total"] == 1
    item2 = body2["items"][0]
    assert item2["order_count"] == 1, item2
    assert item2["units_sold"] == 3
    assert item2["sales"] == cny4_from_usd("60")
    assert item2["refund_order_count"] == 1
    assert item2["refund_return_qty"] == 1
    assert item2["refund_return_amount"] == cny4_from_usd("20")
    assert item2["full_loss_qty"] == 1
    assert body2["totals"]["refund_order_count"] == 1
    assert body2["totals"]["full_loss_order_count"] == 1
    assert "跟随原订单" in body2["meta"]["window"]["note"]
    assert body2["totals"]["sales"] == cny4_from_usd("60")
    assert body2["totals"]["gmv"] == cny4_from_usd("60")
    assert body2["totals"]["order_count"] == 1
    assert body2["totals"]["cancelled_order_count"] == 0
    assert body2["totals"]["total_orders"] == 1

    # 精确到 9 月 1 日：9 月 10 日才退款的 W1 仍完整归入订单日。
    order_day = {"w_start": "2026-09-01", "w_end": "2026-09-01"}
    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": "TEST_ROI_SPU_WIN", **order_day},
    )
    item3 = r3.json()["items"][0]
    assert item3["refund_order_count"] == 1
    assert item3["refund_return_qty"] == 1
    assert item3["refund_return_amount"] == cny4_from_usd("20")
    assert item3["full_loss_qty"] == 1

    orders_response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/orders",
        headers=h,
        params=order_day,
    )
    assert orders_response.status_code == 200, orders_response.text
    orders = orders_response.json()["orders"]
    assert [order["order_id"] for order in orders] == ["TEST_ORDER_W1"]
    assert orders[0]["paid_at"].startswith("2026-09-10")

    settlements_response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/settlements",
        headers=h,
        params=order_day,
    )
    assert settlements_response.status_code == 200, settlements_response.text
    settlements = settlements_response.json()["settlements"]
    assert [settlement["order_id"] for settlement in settlements] == [
        "TEST_ORDER_W1"
    ]

    cases = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/cases",
        headers=h,
        params=order_day,
    ).json()["cases"]
    assert [case["case_id"] for case in cases] == ["TEST_CASE_W1"]
    assert cases[0]["updated_at"].startswith("2026-09-10")

    # 退款发生日 9 月 10 日没有订单，不能单独把退款计入该日。
    refund_day = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "q": "TEST_ROI_SPU_WIN",
            "w_start": "2026-09-10",
            "w_end": "2026-09-10",
        },
    ).json()
    assert refund_day["total"] == 0
    assert refund_day["totals"]["refund_order_count"] == 0

    # 不传窗口 = 全历史:两单都在(与上面 item 断言同源)
    assert body["totals"]["sales"] == cny4_from_usd("100")
    assert body["totals"]["gmv"] == cny4_from_usd("100")
    assert body["totals"]["order_count"] == 2
    assert body["totals"]["total_orders"] == 2


def test_spu_roi_ad_query_pushes_shop_scope_and_index_contract():
    """SPU ROI 广告事实查询显式按店铺 seller_id 下推，并有配套索引。"""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0051_spu_roi_query_indexes.py"
    ).read_text(encoding="utf-8")

    ad_sql = profitability_impl._SQL_ROI_AD.text
    assert "d.seller_id = ANY(CAST(:selected_seller_ids AS text[]))" in ad_sql
    assert "cp.id = ANY(CAST(:selected_pks AS bigint[]))" in ad_sql
    assert "ix_ad_daily_roi_seller_product_day" in migration
    assert "ON plugin.ad_daily (seller_id, product_id, day)" in migration
    assert "WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'" in migration
    assert "ix_sales_order_lines_spu_order" in migration
    assert "ix_tracking_events_shipment_action" in migration
    assert "CONCURRENTLY IF NOT EXISTS" in migration


def test_spu_roi_date_window_uses_shop_local_midnights(api_client, readonly_key, db_engine):
    """VN shop date filters include complete Ho Chi Minh calendar days."""
    local_day = date(2026, 9, 1)
    start, end = profitability_impl._window_dates(
        local_day, local_day, ZoneInfo("Asia/Ho_Chi_Minh")
    )
    assert start == datetime(2026, 8, 31, 17, tzinfo=UTC)
    assert end == datetime(2026, 9, 1, 17, tzinfo=UTC)

    spring_forward = date(2026, 3, 29)
    gb_start, gb_end = profitability_impl._window_dates(
        spring_forward, spring_forward, ZoneInfo("Europe/London")
    )
    assert gb_start is not None and gb_end is not None
    assert gb_end - gb_start == timedelta(hours=23)

    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_LOCAL_DAY")
        spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_LOCAL_DAY")
        for order_id, order_iso in (
            ("BEFORE", "2026-08-31T16:59:59+00:00"),
            ("AT_START", "2026-08-31T17:00:00+00:00"),
            ("BEFORE_END", "2026-09-01T16:59:59+00:00"),
            ("AT_END", "2026-09-01T17:00:00+00:00"),
        ):
            _seed_order_line(
                sess,
                shop_pk=shop_pk,
                spu_pk=spu_pk,
                order_id=f"TEST_ORDER_LOCAL_DAY_{order_id}",
                status=PAID_ORDER_STATUS,
                line_ext=f"TEST_LINE_LOCAL_DAY_{order_id}",
                qty="1",
                unit_price="100000",
                paid=True,
                order_iso=order_iso,
            )
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={
            "shop_pk": shop_pk,
            "q": "TEST_ROI_SPU_LOCAL_DAY",
            "w_start": local_day.isoformat(),
            "w_end": local_day.isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["totals"]["total_orders"] == 2

    detail = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/orders",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"w_start": local_day.isoformat(), "w_end": local_day.isoformat()},
    )
    assert detail.status_code == 200, detail.text
    assert {row["order_id"] for row in detail.json()["orders"]} == {
        "TEST_ORDER_LOCAL_DAY_AT_START",
        "TEST_ORDER_LOCAL_DAY_BEFORE_END",
    }


def test_spu_roi_rejects_date_window_for_ambiguous_shop_region(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_UNKNOWN_REGION", region="US")
        _seed_spu(sess, shop_pk, "TEST_ROI_SPU_UNKNOWN_REGION")
        sess.commit()

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={
            "shop_pk": shop_pk,
            "w_start": "2026-09-01",
            "w_end": "2026-09-01",
        },
    )
    assert response.status_code == 422
    assert "region=US" in response.json()["detail"]


def test_spu_roi_date_window_clips_ad(api_client, readonly_key, db_engine):
    """v8 (2026-09-15 fix/spu-roi-ad-window-clip)：起始/截止日同时裁剪广告。

    v8 主动从 _SQL_ROI_AD / _SQL_DETAIL_ADS 删 ad_daily ∪ ad_today 的“全窗
    累计”逻辑，改成 ad_daily.day 按请求日期范围裁剪 — 选日期范围时
    spend / gmv_ad / ad_count 全部随窗口变化，与销售/退款同语义。

    场景：VN 本地窗口 09-01~09-30；窗外 ad (08-31 spend=25) + 窗内 ad
    (09-10 spend=15)。若把本地午夜错当 UTC，08-31 会被误纳入。
    """
    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_WAD")
        spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_WAD")
        # 窗外 ad：v7 会计入 spend=25，v8 被裁掉
        _seed_ad_dump(
            sess,
            seller="TEST_SELLER_WAD",
            product_id="TEST_ROI_SPU_WAD",
            campaign_id="CAMP_OUTSIDE_WINDOW",
            spend="25",
            orders="0",
            gmv="30",
            day="2026-08-31",
        )
        # 窗内 ad：保留 spend=15、orders=2
        _seed_ad_dump(
            sess,
            seller="TEST_SELLER_WAD",
            product_id="TEST_ROI_SPU_WAD",
            campaign_id="CAMP_INSIDE_WINDOW",
            spend="15",
            orders="2",
            gmv="20",
            day="2026-09-10",
        )
        # 两笔销售：窗内(09-10)与窗外(08-10)各 $20
        _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_WAD1",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_WAD1",
            qty="1",
            unit_price="526600",  # $20
            paid=True,
            order_iso="2026-09-10T08:00:00+00:00",
            paid_iso="2026-09-10T08:00:00+00:00",
        )
        _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_WAD2",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_WAD2",
            qty="1",
            unit_price="526600",
            paid=True,
            order_iso="2026-08-10T08:00:00+00:00",
            paid_iso="2026-08-10T08:00:00+00:00",
        )
        sess.commit()  # handler 用独立连接读,必须真提交(SQLAlchemy 2 上下文不自动 commit)
    h = {"Authorization": f"Bearer {readonly_key}"}
    # 不限窗口:ad spend=25+15=40 (两条都在),sales=40,2 单
    r_all = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_WAD"}
    )
    item_all = r_all.json()["items"][0]
    assert item_all["ad_count"] == 2
    assert item_all["spend"] == cny4_from_usd("40")
    assert item_all["sales"] == cny4_from_usd("40")
    assert item_all["order_count"] == 2
    # 裁剪到 09-01~09-30：销售只 1 单 $20；ad 只留窗内 spend=15 / ad_count=1
    r_crop = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "q": "TEST_ROI_SPU_WAD",
            "shop_pk": shop_pk,
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
        },
    )
    item_crop = r_crop.json()["items"][0]
    assert item_crop["ad_count"] == 1, item_crop  # v8：窗外 ad 被裁
    assert item_crop["spend"] == cny4_from_usd("15")
    assert item_crop["sales"] == cny4_from_usd("20")
    assert item_crop["order_count"] == 1
    assert "已裁剪" in r_crop.json()["meta"]["window"]["note"]
    assert "ad 同窗口裁剪" in r_crop.json()["meta"]["window"]["note"]


def test_spu_roi_ad_window_single_side_only(api_client, readonly_key, db_engine):
    """v8 §6.4 覆盖：仅传 :ws 或 :we 时 NULL 短路另一侧。

    _SQL_ROI_AD (tts_erp_v2/analytics/spu_roi.py:101) 使用：
      AND (CAST(:ws AS timestamptz) IS NULL OR t.day >= :ws::date)
      AND (CAST(:we AS timestamptz) IS NULL OR t.day <  :we::date)
    仅传 w_start → 仅下界过滤，上界短路 = 不卡上界；仅传 w_end 同理。

    场景：4 条 ad 跨 06-01 / 09-10 / 09-25 / 10-05 四个日期；验证：
    - 仅 w_start=09-01 → 留 09-10+09-25+10-05（10-05 无上界束缚）
    - 仅 w_end=09-30 → 留 06-01+09-10+09-25（10-05 被上界裁）
    - 双边界 w_start=09-01 & w_end=09-30 → 仅 09-10+09-25（边界全开）
    """
    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_WADSS")  # noqa: F841 — 传入 _seed_spu
        _seed_spu(sess, shop_pk, "TEST_ROI_SPU_WADSS")
        # 4 天 ad：过远过去 / 窗内早 / 窗内晚 / 过远未来
        for day, camp, spend in (
            ("2026-06-01", "CAMP_D1", "10"),
            ("2026-09-10", "CAMP_D2", "20"),
            ("2026-09-25", "CAMP_D3", "30"),
            ("2026-10-05", "CAMP_D4", "40"),
        ):
            _seed_ad_dump(
                sess,
                seller="TEST_SELLER_WADSS",
                product_id="TEST_ROI_SPU_WADSS",
                campaign_id=camp,
                spend=spend,
                orders="0",
                gmv="0",
                day=day,
            )
        sess.commit()
    h = {"Authorization": f"Bearer {readonly_key}"}
    base = {"q": "TEST_ROI_SPU_WADSS"}

    # 仅 w_start=09-01：无上界束缚，09-10/09-25/10-05 留下
    r_ws = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={**base, "w_start": "2026-09-01"}
    )
    item_ws = r_ws.json()["items"][0]
    assert item_ws["ad_count"] == 3, item_ws  # 10+30+40 = 90
    assert item_ws["spend"] == cny4_from_usd("90")

    # 仅 w_end=09-30：无下界束缚，06-01/09-10/09-25 留下（10-05 被上界裁）
    r_we = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={**base, "w_end": "2026-09-30"}
    )
    item_we = r_we.json()["items"][0]
    assert item_we["ad_count"] == 3, item_we  # 10+20+30 = 60
    assert item_we["spend"] == cny4_from_usd("60")

    # 双边界：09-01~09-30，仅 09-10/09-25
    r_both = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={**base, "w_start": "2026-09-01", "w_end": "2026-09-30"},
    )
    item_both = r_both.json()["items"][0]
    assert item_both["ad_count"] == 2, item_both  # 20+30 = 50
    assert item_both["spend"] == cny4_from_usd("50")



def _react_profitability_source(name: str):
    from pathlib import Path
    return (Path(__file__).resolve().parents[2] / "frontend/src/pages/spu-profitability" / name).read_text(encoding="utf-8")

def _react_profitability_metrics(name: str):
    source = _react_profitability_source("Summary.tsx")
    match = re.search(rf"\bconst\s+{re.escape(name)}\b[^=]*=\s*\[(.*?)\n\];", source, re.DOTALL)
    assert match is not None, f"Missing shared metric declaration: {name}"
    return re.findall(r"\['([^']+)',\s*'([^']+)',\s*'([^']+)'", match[1])

def _spu_react_document(api_client, readonly_key):
    from tests.api.test_frontend_pages import _bootstrap
    response = api_client.get("/v2/pages/spu-roi", headers={"Authorization": f"Bearer {readonly_key}"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "private, no-store"
    bootstrap = _bootstrap(response.text)
    assert bootstrap["pageId"] == "spu-roi"
    assert bootstrap["roleCapabilities"] == {"readwrite": False, "admin": False}
    assert 'id="frontend-root"' in response.text and "/static/frontend/" in response.text
    return response.text, bootstrap


def test_spu_roi_sort_binding_is_declarative_and_delegated():
    """Tabulator's declared metric fields drive the backend remote-sort callback."""
    table = _react_profitability_source("ProfitabilityTable.tsx")
    query = _react_profitability_source("query.ts")
    page = _react_profitability_source("index.tsx")
    assert "export const columns = [" in table and "columns.map(([title, field])" in table
    assert "headerSort: true" in table and "sortMode: 'remote'" in table
    assert "instance.on('dataSorting'" in table and "state.current.onSort(field, direction)" in table
    assert "onSort={(sort, order) => update({ sort, order })}" in page
    assert "sort: q.sort, order: q.order" in query
    assert "priceStats." in table and "priceGroups.map" in table


def test_spu_roi_totals_roi_real_native_reconciliation(
    api_client, readonly_key, db_engine
):
    """totals.roi_real 对账：(Σnet_revenue − Σreturn_loss)/Σspend，CNY 口径。

    net_revenue 已含汇率换算，Σ跨 SPU 累加后除 Σspend；比例换币前后不变。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)  # spend 10
        _seed(sess, _seed_spu_b)  # spend 50
        _seed(sess, _seed_spu_c)  # spend 10

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    body = r.json()
    assert body["total"] == 3

    items = {it["spu_id"]: it for it in body["items"]}
    # 场景 B/C 走默认 DEFAULT_K1 = 40 CNY
    # v7：net_revenue = sales × (1−0.308) × (1−refund_rate_spu)
    # return_loss = full_loss_qty × unit_cost（v9：完结退货不论物流也计）
    # 三 SPU 默认成本 × 5件（unit_cost = 40 × 0.14774 = 5.9096）
    # A：sales=$100, refund=$20, units=5；net=100×0.692×0.80=55.36
    # B：sales=?, refund=?, units=? （按 _seed_spu_b）
    # C：sales=?, refund=?, units=? （按 _seed_spu_c）
    # 统一验证：totals.roi_real == (Σnet_revenue − Σreturn_loss) / Σspend
    total_net_revenue_cny = sum(Decimal(it["net_revenue"]) for it in items.values())
    total_return_loss_cny = sum(Decimal(it["return_loss"]) for it in items.values())
    total_spend_cny = sum(Decimal(it["spend"]) for it in items.values())
    expected = (total_net_revenue_cny - total_return_loss_cny) / total_spend_cny
    assert body["totals"]["roi_real"] == m2(Decimal(expected))


def test_spu_roi_totals_roi_real_single_row_matches_item(
    api_client, readonly_key, db_engine
):
    """单行场景:totals.roi_real == 该行 roi_real(同源同公式)。

    v7 单 SPU：净收入按已结算/未结算分层，D1 DEFAULT=40 CNY 后 net_profit
    同步变动；此处仅断言 totals == items[0].roi_real。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A"},
    )
    body2 = r2.json()
    assert body2["total"] == 1
    assert body2["totals"]["roi_real"] == body2["items"][0]["roi_real"]


def test_spu_roi_single_spu_row_metrics_align_with_dashboard_totals(
    api_client, readonly_key, db_engine
):
    """单 SPU 范围内，行级明细和盈利大盘必须使用完全相同的 v10 口径。

    多 SPU 大盘仍需按订单全局去重，不能简单累加行；单 SPU 是排除跨 SPU
    重复后的最小对齐反馈环。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    row = body["items"][0]
    totals = body["totals"]

    for field in (
        "total_orders",
        "effective_order_count",
        "refund_order_count",
        "full_loss_order_count",
        "domestic_cancelled_order_count",
        "overseas_cancelled_order_count",
    ):
        assert row[field] == totals[field], field

    assert Decimal(row["effective_sales"]) == Decimal(totals["effective_sales"])
    for field in ("refund_rate", "full_loss_rate", "cancel_rate"):
        assert Decimal(row[field]) == Decimal(totals[field]), field


# ─── 分页 / 搜索 / 排序 ───────────────────────────────────────────────


def test_spu_roi_default_sort_roi_asc_pagination_and_totals(
    api_client, readonly_key, db_engine
):
    """3 个活动 SPU:v7 默认 ROI 升序(D8 保留端点默认 sort=roi_real 不动);
    分页 limit/offset;totals 跨分页加总。

    v7 ROI 值变动（成本默认 40 CNY + 未结算 ×(1−r̂)×(1−rate)）。校验 ROI
    排序顺序与具体数值一同产出（不写死数字，从 net_revenue/return_loss/
    spend 反推）。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        _seed(sess, _seed_spu_b)
        _seed(sess, _seed_spu_c)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": Q, "sort": "roi_real"}
    )
    body = r.json()
    assert body["total"] == 3

    # 从实际响应推导期望顺序与值
    items = body["items"]
    spu_roi = {it["spu_id"]: Decimal(it["roi_real"]) for it in items}
    # A spend=10, B=50, C=10（已知常数）
    expected_order = sorted(spu_roi, key=lambda k: spu_roi[k])
    assert [i["spu_id"] for i in items] == expected_order
    for spu_id, _ in spu_roi.items():
        # 不再写死：从行 net_revenue/return_loss/spend 反推（舍入到 2dp）
        row = next(it for it in items if it["spu_id"] == spu_id)
        nc = Decimal(row["net_revenue"])
        rl = Decimal(row["return_loss"])
        sp = Decimal(row["spend"])
        if sp != 0:
            expected = (nc - rl) / sp
            # 服务端 quantize 2dp（HALF_UP）
            assert Decimal(row["roi_real"]) == expected.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

    totals = body["totals"]
    assert totals["row_count"] == 3
    assert totals["order_count"] == 3
    assert totals["cancelled_order_count"] == 1
    assert totals["total_orders"] == 4
    assert totals["spend"] == m4(
        sum((Decimal(v) * USD_CNY for v in (10, 50, 10)), Decimal(0))
    )
    assert totals["sales"] == cny4_from_usd("220")
    assert totals["gmv"] == cny4_from_usd("260")
    assert totals["refund_net_amount"] == cny4_from_usd("20")

    # totals = 行加总（money 4 位）
    row_sum = {
        "spend": sum((Decimal(i["spend"]) for i in items), Decimal(0)),
        "sales": sum((Decimal(i["sales"]) for i in items), Decimal(0)),
        "refund_net_amount": sum(
            (Decimal(i["refund_net_amount"]) for i in items), Decimal(0)
        ),
        "net_profit": sum((Decimal(i["net_profit"]) for i in items), Decimal(0)),
    }
    # totals 按服务端未量化 Decimal 加总；逐行 wire 值已量化 4 位，允许 1 个最小单位尾差。
    for field, value in row_sum.items():
        assert abs(value - Decimal(totals[field])) <= _Q4

    # 分页：limit=2 → B,C;offset=2 → A
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 0},
    )
    b2 = r2.json()
    assert [i["spu_id"] for i in b2["items"]] == expected_order[:2]
    assert b2["total"] == 3
    assert b2["totals"]["row_count"] == 3

    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 2},
    )
    b3 = r3.json()
    assert [i["spu_id"] for i in b3["items"]] == expected_order[2:]


def test_spu_roi_sort_order_desc_and_search(api_client, readonly_key, db_engine):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        _seed(sess, _seed_spu_b)
        _seed(sess, _seed_spu_c)

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 搜索 spu_id 子串
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": "TEST_ROI_SPU_C"},
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["spu_id"] == "TEST_ROI_SPU_C"

    # sort=net_profit + order=desc
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "net_profit", "order": "desc"},
    )
    body2 = r2.json()
    profits = [Decimal(i["net_profit"]) for i in body2["items"]]
    assert profits == sorted(profits, reverse=True)
    # A 17.0390(新基线 30.8%)… 排序断言见下(以实际为准)
    assert body2["items"][0]["spu_id"] == "TEST_ROI_SPU_A"

    # sort=spend desc 与 sort=sales asc
    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "spend", "order": "desc"},
    )
    b3 = r3.json()
    assert [i["spu_id"] for i in b3["items"]] == [
        "TEST_ROI_SPU_B",  # spend 50
        "TEST_ROI_SPU_A",
        "TEST_ROI_SPU_C",
    ]


def test_spu_roi_rejects_bad_params(api_client, readonly_key, db_engine):
    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_BAD_PARAMS")
        sess.commit()

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 未知 sort → 422;limit 超上限 → 422;非法 fee_rate → 422
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"sort": "bogus"}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"limit": 99999}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "abc"}
        ).status_code
        == 422
    )
    # 非有限 Decimal(NaN/Infinity)比较不报错但会穿透到 quantize → 一律 422,不能 500
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "NaN"}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "Infinity"}
        ).status_code
        == 422
    )
    # 有限但指数量级巨大(1e9999999)会穿透到乘法/_fmt_money quantize → 量级上限 422,不能 500
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "1e9999999"}
        ).status_code
        == 422
    )
    # 精确 SPU scope 必须有有效值、有界，且不能和展示层 q 混用。
    bad_spu_params = [
        {"spu_ids": ",，，"},
        {"spu_ids": "TEST_ROI_SPU_A"},  # 精确 scope 必须绑定内部 shop_pk
        {"spu_ids": "X" * 129},
        {"spu_ids": ",".join(f"TEST_{index}" for index in range(101))},
        {"q": "TEST", "spu_ids": "TEST_ROI_SPU_A"},
    ]
    for params in bad_spu_params:
        response = api_client.get(
            "/v2/analytics/spu-roi",
            headers=h,
            params=params,
        )
        assert response.status_code == 422, (params, response.text)

    invalid_options = api_client.get(
        "/v2/commerce/channel-product-options",
        headers=h,
        params={"shop_pk": shop_pk, "spu_ids": "，，"},
    )
    assert invalid_options.status_code == 422


def test_spu_roi_empty_result_and_meta(api_client, readonly_key):
    """q 不命中 → items 空 + envelope 结构 + meta 字段齐全。"""
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["totals"] == {
        "row_count": 0,
        "order_count": 0,
        "cancelled_order_count": 0,
        "total_orders": 0,
        "spend": "0.0000",
        "sales": "0.0000",
        "gmv": "0.0000",
        "refund_net_amount": "0.0000",
        "return_loss": "0.0000",
        "net_profit": "0.0000",
        "roi_real": None,  # Σspend=0 → null(页面显示 —)
        "refund_order_count": 0,
        "full_loss_qty": 0,
        "full_loss_cancelled_qty": 0,
        "domestic_cancelled_order_count": 0,
        "overseas_cancelled_order_count": 0,
        "roi_breakeven": None,
        "effective_sales": "0.0000",
        "effective_order_count": 0,
        "full_loss_order_count": 0,
        "refund_rate": None,
        "full_loss_rate": None,
        "cancel_rate": None,
        "ad_system_actual_roi": None,
        "ad_system_breakeven_roi": None,
        "ad_system_max_ad_spend": "0.0000",
        "ad_system_remaining_ad_spend_capacity": "0.0000",
        "ad_system_breakeven_roi_status": "estimated_known_costs",
        "projection_status": "no_unsettled_orders",
        "projection_basis_order_count": 0,
        "projection_basis_qty": 0,
        "projection_basis_sales": "0.0000",
        "projection_basis_refund_amount": "0.0000",
        "projection_terminal_basis_order_count": 0,
        "projection_terminal_basis_sales": "0.0000",
        "projection_terminal_full_loss_sales": "0.0000",
        "projection_terminal_full_loss_order_count": 0,
        "projection_terminal_full_loss_qty": 0,
        "projection_completed_basis_order_count": 0,
        "projection_completed_full_loss_order_count": 0,
        "projection_full_loss_basis_order_count": 0,
        "projection_basis_full_loss_order_count": 0,
        "projection_basis_full_loss_qty": 0,
        "projection_refund_amount_rate": None,
        "pre_delivery_full_loss_rate": None,
        "completed_full_loss_rate": None,
        "delivered_full_loss_rate": None,
        "settled_full_loss_rate": None,
        "projection_full_loss_qty_rate": None,
        "unsettled_order_count": 0,
        "delivered_unsettled_order_count": 0,
        "full_loss_exposure_unsettled_order_count": 0,
        "full_loss_exposure_unsettled_sales": "0.0000",
        "confirmed_full_loss_exposure_order_count": 0,
        "confirmed_full_loss_exposure_qty": 0,
        "confirmed_full_loss_exposure_refund_amount": "0.0000",
        "unresolved_unsettled_order_count": 0,
        "unresolved_full_loss_exposure_order_count": 0,
        "unresolved_unsettled_qty": 0,
        "unresolved_full_loss_exposure_qty": 0,
        "unresolved_unsettled_sales": "0.0000",
        "confirmed_unsettled_refund_amount": "0.0000",
        "confirmed_unsettled_full_loss_order_count": 0,
        "confirmed_unsettled_full_loss_qty": 0,
        "projected_future_refund_amount": "0.0000",
        "projected_terminal_refund_amount": "0.0000",
        "projected_future_full_loss_order_count": "0",
        "projected_future_full_loss_qty": "0",
        "projected_terminal_full_loss_qty": "0",
        "projected_full_loss_cost": "0.0000",
        "projected_unsettled_net": "0.0000",
        "projected_net_revenue": "0.0000",
        "projected_net_profit": "0.0000",
        "projected_roi_real": None,
        "projected_roi_breakeven": None,
        "projected_nc_prime": "0.0000",
        "projected_cogs_kept": "0.0000",
        "projected_ad_gmv": "0.0000",
        "projected_ad_system_actual_roi": None,
        "projected_ad_system_max_ad_spend": "0.0000",
        "projected_ad_system_breakeven_roi": None,
        "profit_status": "break_even",
        "roi_status": "unavailable",
    }
    meta = body["meta"]
    assert meta["fx"]["usd_vnd"] == "26330.0000"
    assert meta["fx"]["cny_usd"] == "0.1477"
    assert Decimal(meta["fx"]["usd_cny"]) == USD_CNY
    assert meta["fx"]["cny_vnd"] == format(USD_VND / USD_CNY, "f")
    assert meta["fx"]["vnd_cny"] == format(USD_CNY / USD_VND, "f")
    assert meta["fx"]["as_of"] == "2099-09-06"
    assert meta["fx"]["source"] == "fx-cache"
    assert isinstance(meta["fx"]["snapshot_id"], int)
    # ``mode`` 是 stable API 的兼容别名；``source`` 提供细化后的逐店来源。
    assert meta["fee"]["mode"] == "baseline"
    assert meta["fee"]["source"] == "baseline"
    assert meta["fee"]["rate"] == "0.3080"
    assert meta["fee"]["override"] is None
    assert meta["cost_assumption"]
    assert "first_day" in meta["window"]
    assert "last_day" in meta["window"]
    # meta.window (v8)：销售/退款默认不裁剪，但 ad 不再是“视图全窗口”
    assert "ad=视图全窗口累计" not in meta["window"]["note"]
    assert "w_start/w_end" in meta["window"]["note"]
    assert meta["unattributed_refund_lines"] >= 0
    assert meta["computed_at"]
    assert meta["currency"] == {
        "display": "CNY",
        "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
    }
    assert meta["ad_system_roi"]["actual_formula"] == "广告归因GMV ÷ 广告实际消耗"
    assert meta["ad_system_roi"]["additional_costs_status"] == "not_modeled"
    assert meta["ad_system_roi"]["advertising_credit_status"] == (
        "not_available_separately"
    )
    assert "不混入广告赠金" in meta["ad_system_roi"]["spend_basis"]
    assert "已知成本下限估算" in meta["ad_system_roi"]["warning"]
    assert "ad_system_other_necessary_costs_not_modeled" in meta["warnings"]
    # 金额列可 JSON 序列化(Decimal 已转字符串)
    json.dumps(body)


def test_spu_roi_fee_rate_override_meta(api_client, readonly_key, db_engine):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A", "fee_rate": "0.20"},
    )
    body = r.json()
    assert body["total"] == 1
    meta = body["meta"]
    assert meta["fee"]["source"] == "user_override"
    assert meta["fee"]["rate"] == "0.2000"
    assert meta["fee"]["override"] == "0.2000"
    # fee_rate=0.20 + 无结算 + refund_rate=0.20；金额统一输出 CNY。
    item = body["items"][0]
    assert item["platform_fee"] == cny4_from_usd("20")
    expected_net_profit = (
        Decimal(2633000) / (USD_VND / USD_CNY) * Decimal("0.8") * Decimal("0.8")
        - Decimal(5) * K1_CNY
        - Decimal(10) * USD_CNY
    )
    assert item["net_profit"] == m4(expected_net_profit)


# ─── 页面契约 ─────────────────────────────────────────────────────────


def test_spu_roi_page_returns_html(api_client, readonly_key):
    _spu_react_document(api_client, readonly_key)


def test_spu_roi_page_requires_auth(api_client):
    assert api_client.get("/v2/pages/spu-roi").status_code == 401


def test_spu_roi_page_toolbar_shop_and_date_filters(api_client, readonly_key):
    """React owns shop/date inputs and one adjacent SPU apply/clear group."""
    body, _ = _spu_react_document(api_client, readonly_key)
    page = _react_profitability_source("index.tsx")
    selection = _react_profitability_source("Selection.tsx")
    for hook in ("shop-switcher", "shop-modal-list", "filter-w-start", "filter-w-end", "filter-projection-lookback-days", "filter-include-all", "btn-toolbar-more"):
        assert f'id="{hook}"' in page, hook
    assert "/v2/commerce/channel-accounts?platform=tiktok" in page
    assert "Boolean(shops.data && !shop)" in page and "请选择店铺" in page
    assert "onPress={() => changeShop(row.id)}" in page
    assert "全部店铺" not in page
    for retired in ("ops-toast", "toast-countdown", "REDIRECT_COUNTDOWN_SEC"):
        assert retired not in page
    assert 'type="date"' in page and 'aria-label="快捷日期范围"' in page
    assert "起始日期不能晚于截止日期" in page
    assert page.index('id="filter-w-start"') < page.index("<Selection ")
    for hook in ("filter-spu-ids", "spu-search-input", "btn-spu-apply", "btn-spu-clear"):
        assert hook in selection
    assert "/v2/commerce/channel-product-options" in selection
    assert "onPaste=" in selection and "onApply(draft)" in selection and "onApply([])" in selection
    assert page.count('id="filter-limit"') == 1
    assert page.index('id="filter-limit"') > page.index("<ProfitabilityTable ")
    assert '<OffsetPagination label="SPU ROI 分页"' in page
    assert "tom-select" not in body and "bootstrap.min" not in body
    for forbidden in ("onchange=", "onclick=", "onsubmit="):
        assert forbidden not in body


def test_spu_roi_page_remembers_filters_and_enhances_date_range():
    """URL dates precede local preferences; native date controls use the shop day."""
    query = _react_profitability_source("query.ts")
    page = _react_profitability_source("index.tsx")
    assert "tts-erp:" in query and ":preferences:v1" in query
    assert "localStorage.getItem(preferenceKey(profile))" in query
    assert "localStorage.setItem(preferenceKey(profile)" in query
    assert "params.has('shop_pk')" in query and "params.has('w_start')" in query
    assert "explicitDates ?" in query and "saved.datesTouched === true && datesValid" in query
    assert "text(saved.wStart) <= text(saved.wEnd)" in query
    for field in ("shopPk", "wStart", "wEnd", "datesTouched", "includeAll", "limit", "sort", "order"):
        assert field in query.split("export function persistQuery", 1)[1]
    for region, zone in {"VN": "Asia/Ho_Chi_Minh", "TH": "Asia/Bangkok", "SG": "Asia/Singapore", "MY": "Asia/Kuala_Lumpur", "PH": "Asia/Manila", "CN": "Asia/Shanghai", "JP": "Asia/Tokyo", "KR": "Asia/Seoul", "GB": "Europe/London"}.items():
        assert f"{region}: '{zone}'" in query
    assert "new Intl.DateTimeFormat('en-CA', { timeZone" in query
    assert "shop.region" in page and "shiftDay(today, -1)" in page
    assert "query.datesTouched" in page and "wStart: yesterday, wEnd: yesterday" in page
    assert 'type="date"' in page and "data-date-preset={id}" in page
    assert "datePresets(today)" in page
    assert "option.start !== month.start || option.end !== month.end" in query
    assert "销售/退款起始日期（空 = 不限）" in page and "销售/退款截止日期（空 = 不限）" in page
    assert "无法确定报表时区" in page and "!unknownZone" in page
    assert "flatpickr" not in page + query and "bootstrap-datepicker" not in page + query


def test_spu_roi_page_shell_contract(api_client, readonly_key):
    """Manifest documents delegate navigation and session actions to shared AppShell."""
    from pathlib import Path
    body, bootstrap = _spu_react_document(api_client, readonly_key)
    root = Path(__file__).resolve().parents[2]
    styles = (root / "frontend/src/styles.css").read_text(encoding="utf-8")
    main = (root / "frontend/src/main.tsx").read_text(encoding="utf-8")
    page = _react_profitability_source("index.tsx")
    active = [item for item in bootstrap["navigation"] if item["id"] == "spu-roi"]
    assert len(active) == 1 and active[0]["href"] == bootstrap["rootPath"] + "/v2/pages/spu-roi"
    assert "static/css/tokens.css" in styles and "static/css/navigation.css" in styles
    assert main.count("<AppShell ") == 1 and "<SessionActions " in main
    assert "<AppShell" not in page and "SessionActions" not in page
    assert "spu-profitability-page.js" not in body and "spu-roi.js" not in body
    for external in ("fonts.googleapis.com", "fonts.gstatic.com"):
        assert external not in body
    for handler in ("onclick=", "onsubmit=", "onchange="):
        assert handler not in body


@pytest.mark.requires_frontend_build
def test_spu_roi_page_cache_busts_css_and_js_from_their_own_content(api_client, readonly_key):
    """Selected release and Vite manifest own script and stylesheet identities."""
    from tts_erp_v2.api.v2 import frontend_pages
    body, bootstrap = _spu_react_document(api_client, readonly_key)
    release_id = bootstrap["releaseId"]
    assert release_id != "dev", "Canonical acceptance uses the compiled release"
    directory = frontend_pages._FRONTEND_DIR / release_id
    manifest = json.loads((directory / ".vite/manifest.json").read_text(encoding="utf-8"))
    prefix = f'{bootstrap["rootPath"]}/static/frontend/{release_id}/'
    entry = manifest["src/main.tsx"]
    assert f'src="{prefix}{entry["file"]}"' in body
    pending, seen, css = ["src/main.tsx"], set(), set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        record = manifest[key]
        css.update(record.get("css", []))
        pending.extend(record.get("imports", []))
    assert css, "The production entry must include its shared stylesheet"
    for filename in css:
        assert (directory / filename).is_file()
        assert f'href="{prefix}{filename}"' in body
    assert "?v=" not in body


def test_spu_roi_page_uses_bootstrap_responsive_layout(api_client, readonly_key):
    """HeroUI/shared workspace own responsive layout; business columns do not vanish."""
    body, _ = _spu_react_document(api_client, readonly_key)
    page = _react_profitability_source("index.tsx")
    css = _react_profitability_source("profitability.css")
    details = _react_profitability_source("DrillDetails.tsx")
    table = _react_profitability_source("ProfitabilityTable.tsx")
    assert "erp-page" in page and "erp-panel" in page
    assert "flex flex-wrap" in page and "@media (max-width: 767px)" in css
    assert "minmax(0, 1fr)" in css and 'className="erp-table-scroll"' in details
    assert "frozen: true" in table and "variableHeight: true" in table
    assert "nth-child(" not in css
    assert "bootstrap.min" not in body and "row-cols-" not in page + details
    assert "column.hide()" in table and "key?.startsWith('priceStats.')" in table


def test_spu_roi_hidden_state_overrides_bootstrap_display_utilities(api_client, readonly_key):
    """HeroUI owns modal visibility; hidden price/fee panels have no display override."""
    body, _ = _spu_react_document(api_client, readonly_key)
    page = _react_profitability_source("index.tsx")
    summary = _react_profitability_source("Summary.tsx")
    css = _react_profitability_source("profitability.css")
    assert "<Modal.Backdrop isOpen={Boolean(shops.data && !shop)}" in page
    assert "<Modal.Backdrop isOpen={Boolean(image)}" in page
    assert "hidden={supported !== true}" in summary
    assert "hidden={dismissed || !fee.source}" in summary
    assert "d-flex" not in body + page
    for selector in ("#price-summary", "#fee-card"):
        assert not re.search(re.escape(selector) + r"\s*\{[^}]*display:", css, re.DOTALL)


def test_spu_roi_mobile_sticky_product_cells_use_opaque_backgrounds():
    css = _react_profitability_source("profitability.css")
    table = _react_profitability_source("ProfitabilityTable.tsx")
    for selector in (".erp-profit-grid .tabulator-row .tabulator-cell.tabulator-frozen", ".erp-profit-grid .tabulator-row.erp-profit-loss .tabulator-cell.tabulator-frozen"):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match is not None, selector
        assert "background: var(--surface)" in match[1]
        assert "transparent" not in match[1] and "rgba(" not in match[1]
    assert "inset 3px 0 var(--danger)" in css
    assert "frozen: true" in table and "ResizeObserver" in table
    assert ".erp-profit-detail-trigger" in css and "overflow-wrap: anywhere" in css


def test_spu_roi_dashboard_metrics_are_never_truncated():
    css = _react_profitability_source("profitability.css")
    for selector in (".erp-profit-metrics dt", ".erp-profit-metrics dd"):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match is not None, selector
        assert "overflow: hidden" not in match[1]
        assert "text-overflow: ellipsis" not in match[1]
        assert "white-space: nowrap" not in match[1]
    values = re.search(r"\.erp-profit-metrics dd\s*\{([^}]*)\}", css)
    assert values is not None and "overflow-wrap: anywhere" in values[1]
    assert "minmax(0, 1fr)" in css


def test_spu_roi_drill_summary_matches_actual_dashboard_metrics():
    """Totals and row details share current-metric definitions and explanations."""
    expected = {"总单量": "total_orders", "广告消耗": "spend", "有效单量": "effective_order_count", "有效销售": "effective_sales", "退款数": "refund_order_count", "退款率": "refund_rate", "全损量": "full_loss_order_count", "全损率": "full_loss_rate", "国内取消量": "domestic_cancelled_order_count", "国内取消率": "cancel_rate", "净利润": "net_profit", "实际ROI": "roi_real", "实际保本ROI": "roi_breakeven", "广告系统实际ROI": "ad_system_actual_roi", "广告系统保本ROI": "ad_system_breakeven_roi"}
    assert {label: field for label, field, _ in _react_profitability_metrics("currentMetrics")} == expected
    summary = _react_profitability_source("Summary.tsx")
    details = _react_profitability_source("DrillDetails.tsx")
    assert "metrics = currentMetrics" in summary
    assert "<Metrics values={data.totals}" in summary and "<Metrics values={row} meta={meta}" in details
    assert "metrics={projectedMetrics}" not in details
    assert "presentation.summary_hints" in summary and "metricHints[field]" in summary
    assert "查看" in summary and "口径" in summary and "aria-label=" in summary


def test_spu_roi_pnl_cogs_rows_show_qty_times_unit_cost():
    details = _react_profitability_source("DrillDetails.tsx")
    assert "货本 · 售出件" in details and "货本 · 全损取消件" in details
    assert "件 × " in details
    assert "displayValue(row.units_sold)" in details and "displayValue(row.full_loss_cancelled_qty)" in details
    assert "displayValue(row.unit_cost_used)" in details
    assert "row.units_sold != null && row.unit_cost_used != null" in details
    assert "row.full_loss_cancelled_qty != null && row.unit_cost_used != null" in details
    assert "displayValue(row[field])" in details
    assert "* row.unit_cost_used" not in details


def test_spu_roi_settlements_tab_uses_chinese_headers_and_status():
    details = _react_profitability_source("DrillDetails.tsx")
    for label in ("结算金额 CNY", "对账单时间", "订单状态", "本 SPU 占单比"):
        assert label in details
    assert "本 SPU 行金额 ÷ 整单金额" in details and "与给平台分摊多少钱无关" in details
    assert "分摊比" not in details
    assert "tr('order_status', item.status)" in details
    assert "已取消 / 冲销" in details and "无入账" in details
    assert "displayPercent(item.share_ratio)" in details


def test_spu_roi_zero_net_settlement_with_refund_stays_settled(
    api_client, readonly_key, db_engine
):
    """0 元结算 + 退款组件 = 已结算且全额退款，必须留在已结算桶（口径钉死）。

    2026-10-03 只读核查：生产 0 元 SETTLEMENT 流水全部落在取消单上（不进白名单），
    有效单流水 SETTLEMENT 均非 0；「SETTLEMENT=0 + CUSTOMER_REFUND≠0」是已结算
    全额退款单的正常形态（_seed_terminal_delivery_risk_scenario 有意钉死）。若把
    这类单改判为未结算，会把已退款订单重新估成未结算收入、虚增净收入
    （finding f-71e76e93-71b 复核结论：维持现状）。
    """
    from decimal import Decimal

    from sqlalchemy.orm import Session

    def scenario(sess) -> int:
        shop_pk = _seed_shop(sess, "TEST_ZERO_NET_SETTLE")
        spu_pk = _seed_spu(sess, shop_pk, "TEST_SPU_ZERO_NET_SETTLE")
        order_pk = _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_ZERO_NET_SETTLE",
            status="DELIVERED",
            line_ext="TEST_LINE_ZERO_NET_SETTLE",
            qty="1",
            unit_price="100000",
            paid=True,
        )
        _seed_settlement(
            sess,
            order_pk=order_pk,
            external_id="TEST_TXN_ZERO_NET_SETTLE",
            amount_vnd="0",
            customer_refund_vnd="-100000",
        )
        return spu_pk

    with Session(db_engine) as sess:
        spu_pk = _seed(sess, scenario)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_SPU_ZERO_NET_SETTLE"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    # 已结算全额退款：结算净额 0，且不得再进未结算估算（否则虚增收入）
    assert Decimal(item["settled_net"]) == 0
    assert Decimal(item["unsettled_net"]) == 0
    assert item["settled_order_count"] == 1
    # 已结算样本必须包含它（projection_basis 口径）
    assert item["projection_basis_order_count"] == 1

    # 钻取 /orders 同口径：该单显示已结算
    r2 = api_client.get(f"/v2/analytics/spu-roi/{spu_pk}/orders", headers=h)
    assert r2.status_code == 200, r2.text
    rows = r2.json()["orders"]
    assert len(rows) == 1
    assert rows[0]["is_settled"] is True


def test_spu_roi_cost_and_refund_warnings_use_distinct_badges():
    table = _react_profitability_source("ProfitabilityTable.tsx")
    assert "row.uses_default_unit_cost === true, '缺成本'" in table
    assert "row.refund_rate_alert === true, '高退款'" in table
    assert "default_cost_alert_message" in table and "refund_rate_alert_message" in table
    assert "span.textContent = textValue" in table
    assert "'⚠'" not in table and '"⚠"' not in table


def test_spu_roi_js_targets_dashboard_hooks():
    page = _react_profitability_source("index.tsx")
    selection = _react_profitability_source("Selection.tsx")
    query = _react_profitability_source("query.ts")
    read = _react_profitability_source("useRead.ts")
    summary = _react_profitability_source("Summary.tsx")
    assert "/v2/analytics/spu-roi?" in page and "useRead(root, path" in page
    assert "requestJson<unknown>(root, path" in read and "parseIdentityJson" in read
    assert "decodeOverview" in page and "DEFAULT_K1" not in page + summary
    assert "/v2/commerce/channel-product-options" in selection
    assert "params.set('spu_ids', q.spuIds.join(','))" in query
    assert "replaceAll('，', ',')" in query
    assert "resolvePaste" in selection and "scope?.signal.aborted" in selection
    assert "controller.abort()" in selection and "controller.abort()" in read
    assert "onApply([])" in selection and "onApply(draft)" in selection
    assert "url.searchParams.set('spu_ids'" in page and "url.searchParams.delete('spu_ids')" in page
    assert "params.get('spu_ids')" in query
    assert "'delivered_unsettled_order_count', 'sum-delivered-unsettled-orders'" in summary
    for removed in ("meta.cost_assumption", '"¥"', "TomSelect"):
        assert removed not in page + summary + selection


def test_spu_roi_projection_render_tolerates_stale_html_shell():
    """React creates projection nodes; absent values no longer depend on legacy hooks."""
    summary = _react_profitability_source("Summary.tsx")
    contracts = _react_profitability_source("contracts.ts")
    # Summary.test.tsx exercises null/empty/absent values in the rendered component.
    # This boundary guards projection ownership, without pinning formatter internals.
    assert "optionalRecord(data.meta.projection)" in summary
    assert "status[String(data.totals.projection_status)] ?? text(data.totals.projection_status)" in summary
    assert "values[field]" in summary and "id={prefix + id}" in summary
    assert "document.querySelector" not in summary and "getElementById" not in summary
    assert "value === null || value === undefined || value === '' ? '—'" in contracts


def test_spu_roi_filter_actions_keep_a_stable_mobile_layout():
    selection = _react_profitability_source("Selection.tsx")
    css = _react_profitability_source("profitability.css")
    assert 'className="erp-spu-selection-search"' in selection
    assert ".erp-spu-selection-search" in css and ".erp-spu-selection-field" in css
    assert "flex-wrap: wrap" in css
    actions = selection.split('id="btn-spu-apply"', 1)[1].split("</div>", 1)[0]
    assert 'id="btn-spu-clear"' in actions and "onApply([])" in actions
    assert "onApply(draft)" in actions
    assert 'id="spu-search-input"' in selection
    assert "min-w-0" in selection and "break-all" in selection
    assert "按 Ctrl" not in selection


def test_spu_roi_pagination_uses_shared_heroui_navigation():
    """SPU applies offsets and page size through the shared HeroUI pager."""
    page = _react_profitability_source("index.tsx")
    assert '<OffsetPagination label="SPU ROI 分页"' in page
    assert "offset={query.offset} pageSize={query.limit} total={data?.total}" in page
    assert "onPage={(offset) => update({ offset })}" in page
    assert page.count('id="filter-limit"') == 1
    assert 'aria-label="跳转页"' not in page


def test_spu_roi_page_header_summary_extended_band(api_client, readonly_key):
    """All current/projected bindings and metric explanations survive visual folding."""
    _spu_react_document(api_client, readonly_key)
    current = _react_profitability_metrics("currentMetrics")
    projected = _react_profitability_metrics("projectedMetrics")
    assert [hook for _, _, hook in current] == [
        "sum-total-orders", "sum-spend", "sum-orders", "sum-sales",
        "sum-refund-count", "sum-refund-rate", "sum-loss-qty", "sum-loss-rate",
        "sum-cancel-count", "sum-cancel-rate", "sum-net-profit", "sum-roi",
        "sum-roi-breakeven", "sum-roi-ad-actual", "sum-roi-ad",
    ]
    assert [hook for _, _, hook in projected] == [
        "sum-projection-completed-basis-orders", "sum-projection-completed-loss-orders",
        "sum-projection-full-loss-rate", "sum-unresolved-orders",
        "sum-delivered-unsettled-orders", "sum-full-loss-exposure-unsettled-orders",
        "sum-projected-future-loss-qty", "sum-projected-net-revenue",
        "sum-projected-net-profit", "sum-projected-roi", "sum-projected-breakeven-roi",
        "sum-projected-ad-roi", "sum-projected-ad-breakeven-roi",
    ]
    summary = _react_profitability_source("Summary.tsx")
    hints = summary.split("export const metricHints", 1)[1].split("export function Metrics", 1)[0]
    hint_fields = set(re.findall(r"\b([a-z_]+):\s*'", hints))
    assert {field for _, field, _ in current + projected} | {"projection_status"} <= hint_fields
    assert "查看" in summary and "口径" in summary and "aria-label=" in summary
    assert "metricHints[field]" in summary and "presentation.summary_hints" in summary
    assert "<Modal.Backdrop isOpen={Boolean(hint)}" in summary
    assert "广告系统实际ROI = 广告归因GMV ÷ 广告实际消耗" in summary
    assert "广告系统保本ROI = 广告归因GMV ÷ 最大可承受广告费" in summary
    assert 'id="sum-projection-status"' in summary
    assert "metrics={projectedMetrics.filter(" in summary and "<Disclosure" in summary
    assert "ad_system_breakeven_roi_status === 'estimated_known_costs'" in summary
    for retired in ("sum-n", "sum-projected-terminal-loss-qty", "预计拒收金额率", "预计财务ROI"):
        assert f"'{retired}'" not in summary and f'id="{retired}"' not in summary


def test_spu_roi_drill_summary_excludes_projection_metrics():
    current = _react_profitability_metrics("currentMetrics")
    projected = _react_profitability_metrics("projectedMetrics")
    current_fields = {field for _, field, _ in current}
    projected_fields = {field for _, field, _ in projected}
    assert current_fields.isdisjoint(projected_fields)
    assert not any(field.startswith("projection") or field.startswith("projected") for field in current_fields)
    details = _react_profitability_source("DrillDetails.tsx")
    assert "<Metrics values={row}" in details
    assert "metrics={projectedMetrics}" not in details and "projection_status" not in details
    summary = _react_profitability_source("Summary.tsx")
    assert 'id="sum-projection-status"' in summary
    assert "'sum-projected-net-profit'" in summary and "metrics={projectedMetrics.filter(" in summary


def test_spu_roi_page_d8_no_column_toggles(api_client, readonly_key):
    body, _ = _spu_react_document(api_client, readonly_key)
    page = _react_profitability_source("index.tsx")
    table = _react_profitability_source("ProfitabilityTable.tsx")
    for retired in ("col-toggle-", "data-colgroup=", "data-cg="):
        assert retired not in body + page + table
    block = table.split("export const columns = [", 1)[1].split("] as const", 1)[0]
    fields = re.findall(r"\['[^']+', '([^']+)'\]", block)
    assert fields == ["spend", "ad_system_actual_roi", "ad_system_breakeven_roi", "effective_sales", "total_orders", "effective_order_count", "cancel_rate", "full_loss_rate", "net_profit"]
    assert "columns.map(([title, field])" in table


def test_spu_roi_page_sortable_headers_within_endpoint_whitelist(
    api_client, readonly_key, db_engine
):
    """页面可点列头 ⊆ 端点 sort 白名单:每个 data-sort 请求 200(不再 422)。"""
    import re

    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200
    # Current columns have one React adapter owner; keep real endpoint numeric checks.
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend/src/pages/spu-profitability/ProfitabilityTable.tsx"
    ).read_text(encoding="utf-8")
    definitions = source.split("export const columns = [", 1)[1].split("] as const", 1)[0]
    fields = re.findall(r"\['[^']+', '([a-z0-9_]+)'\]", definitions)
    assert set(fields) == {
        "spend", "ad_system_actual_roi", "ad_system_breakeven_roi", "effective_sales",
        "total_orders", "effective_order_count", "cancel_rate", "full_loss_rate", "net_profit",
    }
    assert fields
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        _seed(sess, _seed_spu_b)
        _seed(sess, _seed_spu_c)
    h = {"Authorization": f"Bearer {readonly_key}"}
    for field in fields:
        for order in ("asc", "desc"):
            response = api_client.get(
                "/v2/analytics/spu-roi",
                headers=h,
                params={"q": Q, "sort": field, "order": order},
            )
            assert response.status_code == 200, (
                f"sort={field}&order={order} 应 200,得 {response.status_code}"
            )
            values = [item[field] for item in response.json()["items"]]
            non_null = [Decimal(str(value)) for value in values if value is not None]
            assert non_null == sorted(non_null, reverse=order == "desc"), (
                field,
                order,
                values,
            )
            if None in values:
                first_null = values.index(None)
                assert all(value is None for value in values[first_null:]), (
                    field,
                    order,
                    values,
                )


def test_spu_roi_js_review_fixes_present():
    page = _react_profitability_source("index.tsx")
    table = _react_profitability_source("ProfitabilityTable.tsx")
    details = _react_profitability_source("DrillDetails.tsx")
    summary = _react_profitability_source("Summary.tsx")
    for foreign in ("loadMe", "ops-identity", "btn-logout", "ROI_HARD_LOSS", "PASS_LINE", "REFUND_RATE_ALERT"):
        assert foreign not in page + table + details + summary
    assert "row.refund_rate_alert === true" in table
    assert "row.has_unsettled_orders === true" in table and "row.profit_status === 'loss'" in table
    assert "instance.on('rowClick'" in table and "createPortal(<DrillDetails" in page
    assert "if (tab === 'pnl') return" in details
    assert "cache.get(key)" in details and "requestJson<unknown>(root, path" in details
    assert "values[field]" in summary and "'roi_real', 'sum-roi'" in summary
    assert "coverage_first_day" in page and "query.datesTouched" in page


def test_spu_roi_frontend_only_displays_backend_profitability():
    page = _react_profitability_source("index.tsx")
    details = _react_profitability_source("DrillDetails.tsx")
    summary = _react_profitability_source("Summary.tsx")
    read = _react_profitability_source("useRead.ts")
    contracts = _react_profitability_source("contracts.ts")
    source = page + details + summary
    assert "totals: record(r.totals), meta: record(r.meta)" in contracts
    assert "values[field]" in summary and "displayValue(row[field])" in details
    assert "error.code === 'FX_RATE_UNAVAILABLE'" in read
    assert "汇率数据缺失，无法计算结果" in read
    assert "setState({ key, error: message, loading: false" in read
    assert "data={data ?? emptyOverview}" in page
    assert "totals: {}, meta: {}" in page
    assert "ad_system_breakeven_roi_status === 'estimated_known_costs'" in summary
    assert "optionalRecord(meta.fx).as_of_at" in page
    assert "shop_estimate: '实测'" in summary
    assert "presentation.pnl_hints" in details
    for financial_formula in ("netRevenue - settledNet", "* unitCost", "* row.unit_cost_used", "ad_system_max_ad_spend", "0.308", "DEFAULT_K1", "ROI_HARD_LOSS", "settledCount > 0"):
        assert financial_formula not in source


def test_spu_roi_js_shop_switch_listener_before_early_return():
    """Both picker surfaces work while no valid shop has been selected."""
    page = _react_profitability_source("index.tsx")
    assert "value={`choice:${shop?.id ?? ''}`}" in page
    assert "typeof key === 'string'" in page and "changeShop(key.slice(7))" in page
    assert "Boolean(shops.data && !shop)" in page and "onPress={() => changeShop(row.id)}" in page
    assert "url.searchParams.set('shop_pk', id)" in page and "url.searchParams.delete('spu_ids')" in page
    assert "shopPk: id, spuIds: []" in page and "query.datesTouched" in page
    assert "'/v2/config/enum-map'" in page
    assert "shopRevision" in page and "重试店铺列表" in page
    assert "shops.loading || shops.blocked" in page
    for obsolete in ("REDIRECT_COUNTDOWN_SEC", "秒后自动返回首页", "showToast"):
        assert obsolete not in page


# ─── 在线汇率接入(D1 落地 2026-09-06)─────────────────────────────────


def _fx_meta_of_empty_query(api_client, readonly_key) -> dict:
    """q 不命中 → items 空,meta.fx 仍按当前汇率解析给出(在线或回退)。"""
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert r.status_code == 200, r.text
    return r.json()["meta"]["fx"]


def test_spu_roi_meta_uses_live_fx_rates(api_client, readonly_key, monkeypatch):
    """换算汇率来自在线 fx 快照:monkeypatch 一张非 D9 值的 USD 快照,
    验证实现真正走 fx-cache —— 值/来源/日期都取自已注入的快照(不是常量)。
    派生数学:CNY→USD = 1/rates[CNY] 量化 8dp；同时暴露 USD→CNY
    与 VND/CNY 供 CNY 金额对账。
    """
    from datetime import UTC, datetime

    from tts_erp_v2.fx.rates import RateMap

    rm = RateMap(
        snapshot_id=999_000_001,
        base_code="USD",
        upstream_last_update=datetime(2026, 9, 6, 0, 0, 1, tzinfo=UTC),
        next_update_at=datetime(2026, 9, 7, 0, 0, 1, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 6, 6, 0, 0, tzinfo=UTC),
        rates={
            "USD": Decimal(1),
            "VND": Decimal(26000),
            "CNY": Decimal("6.9"),
        },
    )
    monkeypatch.setattr(
        profitability_impl, "load_rate_map", lambda sess, base_code="USD": rm
    )
    fx = _fx_meta_of_empty_query(api_client, readonly_key)
    usd_cny = Decimal("6.9")
    assert fx == {
        "usd_vnd": "26000.0000",
        "cny_usd": "0.1449",  # 1/6.9 = 0.14492753… → .4f
        "usd_cny": format(usd_cny, "f"),
        "cny_vnd": format(Decimal(26000) / usd_cny, "f"),
        "vnd_cny": format(usd_cny / Decimal(26000), "f"),
        "as_of": "2026-09-06",
        "as_of_at": "2026-09-06T00:00:01+00:00",
        "snapshot_id": 999_000_001,
        "source": "fx-cache",
    }


def test_spu_roi_fails_closed_when_no_fx_snapshot(
    api_client, readonly_key, monkeypatch
):
    """无完整数据库 FX 快照时整页不可计算，禁止固定汇率兜底。"""
    monkeypatch.setattr(
        profitability_impl, "load_rate_map", lambda sess, base_code="USD": None
    )
    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "FX_RATE_UNAVAILABLE"
    assert body["message"] == "汇率数据缺失，无法计算结果"
    assert body["retryable"] is True
    assert body["requestId"]
    assert "data" not in body


@pytest.mark.parametrize(
    ("cny_rate", "vnd_rate"),
    [
        (Decimal(0), Decimal(26330)),
        (Decimal("6.8"), Decimal(-1)),
        (Decimal("NaN"), Decimal(26330)),
    ],
)
def test_spu_roi_rejects_malformed_fx_snapshot_as_unavailable(
    api_client,
    readonly_key,
    monkeypatch,
    cny_rate,
    vnd_rate,
):
    from datetime import UTC

    from tts_erp_v2.fx.rates import RateMap

    now = datetime(2026, 9, 6, tzinfo=UTC)
    rate_map = RateMap(
        snapshot_id=999_000_002,
        base_code="USD",
        upstream_last_update=now,
        next_update_at=now,
        fetched_at=now,
        rates={"USD": Decimal(1), "CNY": cny_rate, "VND": vnd_rate},
    )
    monkeypatch.setattr(
        profitability_impl,
        "load_rate_map",
        lambda sess, base_code="USD": rate_map,
    )
    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "FX_RATE_UNAVAILABLE"


# ─── D7/D6 钻取面板(行内 accordion + tab 懒加载)──────────────────────────────


def test_spu_roi_page_drilldown_template_present(api_client, readonly_key):
    """React owns one inline detail component and five lazy evidence panels."""
    _spu_react_document(api_client, readonly_key)
    page = _react_profitability_source("index.tsx")
    details = _react_profitability_source("DrillDetails.tsx")
    table = _react_profitability_source("ProfitabilityTable.tsx")
    assert "createPortal(<DrillDetails" in page and "row.getElement().after(detail)" in table
    for tab, label in (("pnl", "利润构成"), ("orders", "订单·物流"), ("settlements", "结算"), ("cases", "售后"), ("ads", "广告")):
        assert f"['{tab}', '{label}']" in details
    assert "<Tabs.List " in details and "<Tabs.Panel " in details
    assert "has_unsettled_orders === true" in details
    assert "presentation.unsettled_alert_message" in details and "onPress={onClose}" in details


def test_spu_roi_page_no_old_columns(api_client, readonly_key):
    _spu_react_document(api_client, readonly_key)
    table = _react_profitability_source("ProfitabilityTable.tsx")
    block = table.split("export const columns = [", 1)[1].split("] as const", 1)[0]
    labels = re.findall(r"\['([^']+)', '[^']+'\]", block)
    assert labels == ["广告消耗", "广告系统实际ROI", "广告系统保本ROI", "有效销售", "总单量", "有效单量", "取消率%", "全损率%", "净利润"]
    assert "title: '商品', field: 'spu_id'" in table
    for retired in ("实际ROI", "保本ROI", "销售$", "有效销售$", "退货$", "取消单量", "退货率%", "全损退款$", "关联广告数"):
        assert retired not in labels
    assert "col-hidden" not in table


# ═════════════════════════════════════════════════════════════════════
# §3.5 D2 零值落库 + D4 全损 38301 + D5/COGS_kept 防御钳位 + 成本层穿透
# + §6.3 钻取端点契约（review 后续补测）
# ═════════════════════════════════════════════════════════════════════


def _seed_synced_source_price(sess, *, spu_id: str, cost: str) -> None:
    """插入一条应被 SPU ROI 忽略的妙手/1688 同步货源价。"""
    account_id = sess.execute(
        text(
            "INSERT INTO procurement.procurement_accounts ("
            " provider, external_account_id, account_name, status"
            ") VALUES ('miaoshou', 'TEST_ROI_SOURCE_PRICE',"
            " 'TEST ROI source price', 'active') "
            "ON CONFLICT (provider, external_account_id) DO UPDATE SET "
            " account_name = EXCLUDED.account_name "
            "RETURNING id"
        )
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO procurement.procurement_products ("
            " procurement_account_id, external_product_id, product_type, title,"
            " source_platform, source_unit_cost, synced_at"
            ") VALUES (:account_id, :ext, 'SPU', 'TEST 货源价', '1688',"
            " CAST(:cost AS numeric), now())"
        ),
        {"account_id": account_id, "ext": spu_id, "cost": cost},
    )


def _seed_tracking_event_overseas(
    sess, *, order_pk: int, action_code: int = 38301
) -> int:
    """插入包裹 + 38301 海外到达事件，助全损口径测试（v9）。

    注意：fulfillment.shipments 无 shop_pk 列（2026-09-13 修），按 order_pk 挂。
    """
    ship_pk = sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " order_pk, external_package_id, tracking_number,"
            " provider_name, status, shipped_at"
            ") SELECT :op, 'TEST_PKG', 'TN_001', 'TEST',"
            " 'in_transit', coalesce(so.paid_at, so.order_time)"
            " FROM commerce.sales_orders so"
            " WHERE so.id = :op"
            " RETURNING id"
        ),
        {"op": order_pk},
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO fulfillment.tracking_events ("
            " shipment_id, external_event_key, action_code, event_at, description"
            ") VALUES (:sp, :eek, :ac, now(), 'TEST 到达海外')"
        ),
        {"sp": ship_pk, "ac": action_code, "eek": f"TEST_EV_{ship_pk}_{action_code}"},
    )
    return ship_pk


def test_spu_roi_ignores_synced_source_price(api_client, readonly_key, db_engine):
    """妙手/1688 同步货源价不参与 SPU ROI 成本解析。

    即使 procurement_products 有 source_unit_cost，只要没有人工成本，页面也必须
    使用 DEFAULT_K1，避免同步报价被当成实际采购成本。
    """
    # 清理前次运行残留（unique constraint 防重复）
    with Session(db_engine) as sess:
        sess.execute(
            text(
                "DELETE FROM procurement.procurement_products "
                "WHERE external_product_id = :ext"
            ),
            {"ext": "TEST_ROI_SPU_PRICE_DIRECT"},
        )
        sess.commit()

    with Session(db_engine) as sess:
        spu_id = "TEST_ROI_SPU_PRICE_DIRECT"
        shop_pk = _seed_shop(sess, "TEST_SELLER_PRICE")
        spu_pk = _seed_spu(sess, shop_pk, spu_id)
        _seed_synced_source_price(sess, spu_id=spu_id, cost="35.0000")
        _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_PRICE",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_PRICE",
            qty="2",
            unit_price="526600",
            paid=True,  # $20
        )
        sess.commit()

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": spu_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["cost_source"] == "DEFAULT_K1", item
    assert Decimal(item["unit_cost_used"]) == K1_CNY


def test_spu_roi_drilldown_requires_auth(api_client):
    """钻取端点 readonly 鉴权（与主表一致：401 无 key）。"""
    for tab in ("orders", "settlements", "cases", "ads"):
        assert api_client.get(f"/v2/analytics/spu-roi/1/{tab}").status_code == 401


def test_spu_roi_case_and_ad_drill_money_use_cny(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_scenario_a)

    headers = {"Authorization": f"Bearer {readonly_key}"}
    cases_response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/cases",
        headers=headers,
    )
    assert cases_response.status_code == 200, cases_response.text
    cases_payload = cases_response.json()
    assert cases_payload["meta"]["currency"]["display"] == "CNY"
    refund_case = next(
        row for row in cases_payload["cases"] if row["case_id"] == "TEST_CASE_A1"
    )
    assert refund_case["refund_amount"] == cny4_from_usd("20")

    ads_response = api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/ads",
        headers=headers,
    )
    assert ads_response.status_code == 200, ads_response.text
    ads_payload = ads_response.json()
    assert ads_payload["meta"]["currency"]["display"] == "CNY"
    assert ads_payload["ads"][0]["spend"] == cny4_from_usd("10")


# ═════════════════════════════════════════════════════════════════════
# v9 全损口径（rubric v9：全损 = 完结退货(不论物流) + 海外取消(38301)；
# 国内取消 ≠ 全损；取消率只计国内取消，与全损退款率不重叠）
# ═════════════════════════════════════════════════════════════════════


def _seed_refund_only_breakeven(sess) -> int:
    seller = "TEST_SELLER_REFUND_ONLY_BE"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_REFUND_ONLY_BE")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_REFUND_ONLY_BE",
        campaign_id="TEST_CAMP_REFUND_ONLY_BE",
        spend="10.00",
        orders="1",
        gmv="100.00",
    )
    order_pk = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_REFUND_ONLY_BE",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_REFUND_ONLY_BE",
        qty="10",
        unit_price="263300",
        paid=True,
    )
    line_pk = _fetch_spu_line_id(sess, "TEST_ORDER_REFUND_ONLY_BE")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=order_pk,
        ext_case="TEST_CASE_REFUND_ONLY_BE",
        case_type="REFUND_ONLY",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line_pk, "TEST_CLINE_REFUND_ONLY_BE", "1", "263300")],
    )
    return spu_pk


def test_refund_only_reduces_row_and_global_breakeven_cogs(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as session:
        _seed(session, _seed_refund_only_breakeven)

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_REFUND_ONLY_BE"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    item = body["items"][0]
    totals = body["totals"]
    assert item["refund_only_qty"] == 1
    assert item["roi_breakeven"] == "17.70"
    assert totals["roi_breakeven"] == item["roi_breakeven"]
    assert Decimal(item["net_profit"]) < 0
    assert Decimal(item["roi_real"]) < Decimal(item["roi_breakeven"])


def _seed_v9_bucket_split(sess) -> int:
    """v9 三桶分离场景：完结退货 vs 海外取消 vs 国内取消。

    - 有效单 TEST_ORDER_V9_1：DELIVERED 已付 4 件 × 263,300 VND ($10) = $40
      - 完结退货 RETURN_AND_REFUND 1 件（无 38301 物流，v9 仍计全损）
      - 完结仅退款 REFUND_ONLY 1 件（v9 也入全损退货桶）
    - 海外取消单 TEST_ORDER_V9_2：CANCELLED 已付 2 件 × $10 + 38301 → 全损取消桶
    - 国内取消单 TEST_ORDER_V9_3：CANCELLED 已付 1 件 × $10，无物流 → 非全损

    期望：full_loss_qty = 1+1+2 = 4、full_loss_cancelled_qty = 2、
      full_loss_rate = 4/(4+2) = 0.67、return_loss = 4×5.9096 = 23.6384、
      cancelled_order_count = 2（信息列=全部取消单）、domestic=1/overseas=1、
      cancel_rate = 1/(1+1) = 0.50（只计国内取消；若含海外取消则错为 0.67）。
    """
    seller = "TEST_SELLER_V9"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_V9")
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_V9_1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_V9_1",
        qty="4",
        unit_price="263300",
        paid=True,
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_V9_1")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_V9_1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_V9_1", "1", "263300")],
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_V9_2",
        case_type="REFUND_ONLY",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_V9_2", "1", "263300")],
    )
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_V9_2",
        status="CANCELLED",
        line_ext="TEST_LINE_V9_2",
        qty="2",
        unit_price="263300",
        paid=True,
    )
    _seed_tracking_event_overseas(sess, order_pk=o2)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_V9_3",
        status="CANCELLED",
        line_ext="TEST_LINE_V9_3",
        qty="1",
        unit_price="263300",
        paid=True,
    )
    return spu_pk


def test_spu_roi_v9_full_loss_two_buckets_and_disjoint_cancel_rate(
    api_client, readonly_key, db_engine
):
    """v9 口径主断言：全损两桶 + 取消率/全损退款率不重叠。

    - 完结退货（RETURN_AND_REFUND + REFUND_ONLY，无 38301）也计全损件数
    - 海外取消（CANCELLED∧38301）计全损取消件
    - 国内取消（CANCELLED 无 38301）不计全损，只进取消率
    - cancel_rate 分子不含海外取消（与全损退款率不重叠）
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_v9_bucket_split)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_V9"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    # 全损 = 完结退货 2 件（不论物流）+ 海外取消 2 件
    assert item["full_loss_qty"] == 4
    assert item["full_loss_cancelled_qty"] == 2
    assert item["full_loss_order_count"] == 2  # 1 退款订单 + 1 海外取消订单
    assert item["full_loss_rate"] == "0.67"  # 2 / 3 全部订单
    assert item["full_loss_qty_rate"] == "0.67"  # 旧件数解释口径 4/(4+2)
    assert item["return_loss"] == "160.0000"  # 4 × 40 CNY

    # 取消：信息列仍是全部取消单；分子仅国内取消，分母是全部 3 单。
    assert item["cancelled_order_count"] == 2
    assert item["total_orders"] == 3
    assert item["domestic_cancelled_order_count"] == 1
    assert item["overseas_cancelled_order_count"] == 1
    assert item["cancel_rate"] == "0.33"

    # 退款桶口径不变（退货+仅退款都在 refund_net）
    assert item["refund_return_qty"] == 1
    assert item["refund_only_qty"] == 1
    assert item["refund_net_amount"] == cny4_from_usd("20")


def test_spu_roi_v9_drill_orders_full_loss_flag(api_client, readonly_key, db_engine):
    """钻取 orders 的 full_loss 旗标同 v9 口径：
    完结退货(不论物流)=⚠、海外取消=⚠、国内取消≠全损。"""
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_v9_bucket_split)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(f"/v2/analytics/spu-roi/{spu_pk}/orders", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["rubric_version"] == "v10"
    by_id = {o["order_id"]: o for o in body["orders"]}
    assert by_id["TEST_ORDER_V9_1"]["full_loss"] is True  # 完结退货，无 38301 也全损
    assert by_id["TEST_ORDER_V9_2"]["full_loss"] is True  # 海外取消
    assert by_id["TEST_ORDER_V9_3"]["full_loss"] is False  # 国内取消 ≠ 全损
    assert by_id["TEST_ORDER_V9_2"]["arrived_overseas"] is True
    assert by_id["TEST_ORDER_V9_3"]["arrived_overseas"] is False


def test_spu_roi_all_date_preset_clears_both_bounds():
    page = _react_profitability_source("index.tsx")
    preset = page.split("function preset(", 1)[1].split("const closeDrill", 1)[0]
    assert "setDateStart(start); setDateEnd(end); commitDates(start, end)" in preset
    query = _react_profitability_source("query.ts")
    assert "id: 'all', label: '不限', start: '', end: ''" in query
    assert "onPress={() => preset(start, end)}" in page
    assert "if (q.wStart) params.set('w_start', q.wStart)" in query
    assert "if (q.wEnd) params.set('w_end', q.wEnd)" in query


def test_spu_roi_tabulator_row_bad_class_is_toggled_not_only_added():
    table = _react_profitability_source("ProfitabilityTable.tsx")
    formatter = table.split("rowFormatter:", 1)[1].split("}); table.current", 1)[0]
    assert "classList.toggle('erp-profit-loss', row.getData().profit_status === 'loss')" in formatter
    assert "classList.add('erp-profit-loss')" not in formatter


def test_spu_roi_tabulator_fills_container_width():
    table = _react_profitability_source("ProfitabilityTable.tsx")
    options = table.split("new Tabulator(element, {", 1)[1].split("}); table.current", 1)[0]
    assert "layout: 'fitColumns'" in options and "layout: 'fitData'" not in options
    assert "frozen: true" in options and "widthShrink: 0" in options
    assert "ResizeObserver" in table and "updateColumnDefinition('spu_id'" in table


def test_spu_roi_table_wrap_content_insets_match_toolbar():
    """Scope, selection, metrics and table share the same application content owner."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    page = _react_profitability_source("index.tsx")
    css = _react_profitability_source("profitability.css")
    workspace = (root / "frontend/src/app/workspace.css").read_text(encoding="utf-8")
    assert 'className="erp-page"' in page
    assert 'className="erp-panel erp-profit-controls"' in page
    assert 'className="erp-panel min-w-0 space-y-4"' in page
    assert "<Selection " in page and "<Summary " in page
    assert ".erp-main" in workspace and ".erp-panel" in workspace
    assert "padding: 24px" in workspace and "background: var(--surface)" in workspace
    assert "margin-inline" not in css and "margin-left" not in css
    assert ".erp-profit-grid" in css and "max-width: 100%" in css
    assert "max-width: 100%" in (root / "frontend/src/shared/tables.css").read_text(encoding="utf-8")
