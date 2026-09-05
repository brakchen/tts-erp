"""TDD 契约测试:GET /v2/analytics/spu-roi(SPU 实际 ROI 看板只读端点)+ /v2/pages/spu-roi。

口径唯一真相 = tech-doc/analytics/spu-real-roi-dashboard.md §4/§5(固定汇率
USD→VND=26330、CNY→USD=0.14774、K1=30 CNY/件、平台佣金基线 0.1156)。
本文件锁定:
1. auth:无 key 401 / readonly 200 / admin 200(端点挂 _READONLY_EXACT)
2. 分页 / spu_id 子串搜索 / 默认排序实际 ROI 升序 / sort+order
3. 业务口径:单 SPU 场景(1 有效订单 + 1 已完结退货退款 + 1 已付被取消订单
   的取消退款 case)按 §4.2 公式精确断言 sales/refund_return/net_profit/
   roi_real/roi_breakeven/platform_fee/return_loss/cpa/unit_cost_used;
   取消桶只进信息列不进净额(DEFAULT_K1 + MANUAL 两分支)
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
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# ─── 口径常量(与实现对齐,期望值推导用)──────────────────────────────
USD_VND = Decimal("26330")
CNY_USD = Decimal("0.14774")
K1_CNY = Decimal("30")
FEE_BASELINE = Decimal("0.1156")
_Q4 = Decimal("0.0001")
_Q2 = Decimal("0.01")

Q = "TEST_ROI_SPU"  # 搜索范围:只命中本模块 TEST SPU
PAID_ORDER_STATUS = "DELIVERED"
DAY = "2026-09-01"


def m4(v: Decimal) -> str:
    return format(v.quantize(_Q4, rounding=ROUND_HALF_UP), ".4f")


def m2(v: Decimal) -> str:
    return format(v.quantize(_Q2, rounding=ROUND_HALF_UP), ".2f")


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


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text("DELETE FROM analytics.ad_raw WHERE seller_id LIKE 'TEST_%'")
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
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
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM after_sales.cases c "
                "WHERE c.shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
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
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.sales_orders "
                "WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )


# ─── 造数 helpers(handler 用独立 session,必须真 commit)────────────────


def _seed_shop(sess, seller: str) -> int:
    # pi-lens-ignore: python-sql-injection — literal SQL, bound-param dict
    return sess.execute(
        text(
            "INSERT INTO commerce.shops (platform, shop_id, account_name, status) "
            "VALUES ('tiktok', :sid, :name, 'active') RETURNING id"
        ),
        {"sid": seller, "name": f"{seller} 店铺"},
    ).scalar_one()


def _seed_spu(sess, shop_pk: int, spu_id: str, *, title: str | None = None) -> int:
    # pi-lens-ignore: python-sql-injection — literal SQL, bound-param dict
    return sess.execute(
        text(
            "INSERT INTO commerce.products_spu "
            "(shop_pk, spu_id, title, status, main_image_url) "
            "VALUES (:shop, :sid, :title, 'ACTIVATE', 'https://img.test/x.png') "
            "RETURNING id"
        ),
        {"shop": shop_pk, "sid": spu_id, "title": title or f"{spu_id} 标题"},
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
) -> None:
    """post_product_list 一条 ad_raw(1 campaign×SPU×1 day)。"""
    # pi-lens-ignore: python-sql-injection — literal SQL, bound-param dict
    sess.execute(
        text(
            """
            INSERT INTO analytics.ad_raw (
                idempotency_key, seller_id, advertiser_id, endpoint, method,
                day, campaign_id, request, response, captured_at, source,
                protocol_version, schema_version
            ) VALUES (
                :idem, :seller, :advertiser, :endpoint, 'POST',
                CAST(:day AS date), :campaign,
                CAST(:request AS JSONB), CAST(:response AS JSONB),
                now(), 'TEST', 2, 1
            )
            """
        ),
        {
            "idem": f"TEST_IDEM_{seller}_{product_id}_{campaign_id}",
            "seller": seller,
            "advertiser": "TEST_ADV",
            "endpoint": "/oec_ads/shopping/v1/oec/stat/post_product_list",
            "day": DAY,
            "campaign": campaign_id,
            "request": json.dumps({"url": "http://tiktok.test/", "body": {}}),
            "response": json.dumps(
                {
                    "status": 200,
                    "body": {
                        "code": 0,
                        "data": {
                            "table": [
                                {
                                    "product_id": product_id,
                                    "product_name": "TEST 商品",
                                    "product_status": "available",
                                    "mixed_real_cost": spend,
                                    "onsite_roi2_shopping_sku": orders,
                                    "onsite_roi2_shopping_value": gmv,
                                    "gmv_max_bid_type": "1",
                                }
                            ]
                        },
                    },
                }
            ),
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
) -> int:
    """插入一单(可带多行);返回 order id。"""
    paid_at = None
    if paid:
        paid_at = "2026-09-01T08:00:00+00:00"
    # pi-lens-ignore: python-sql-injection — literal SQL, bound-param dict
    order_pk = sess.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, paid_at) "
            "VALUES (:shop, :oid, :status, 'VND', CAST(:paid AS timestamptz)) "
            "RETURNING id"
        ),
        {"shop": shop_pk, "oid": order_id, "status": status, "paid": paid_at},
    ).scalar_one()
    # pi-lens-ignore: python-sql-injection — literal SQL, bound-param dict
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
    lines: list[tuple[int, str, str, str | None]],  # (sales_order_line_id, ext, qty, refund_amt)
) -> None:
    case_pk = sess.execute(
        text(
            "INSERT INTO after_sales.cases "
            "(shop_pk, order_pk, external_case_id, case_type, status, "
            " created_at_source, updated_at_source, currency) "
            "VALUES (:shop, :o, :ec, :ct, :st, "
            " CAST('2026-09-02T00:00:00+00:00' AS timestamptz), "
            " CAST('2026-09-03T00:00:00+00:00' AS timestamptz), 'VND') "
            "RETURNING id"
        ),
        {"shop": shop_pk, "o": order_pk, "ec": ext_case, "ct": case_type, "st": status},
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
    net_profit=36.2790 / roi_real=7.56 / roi_breakeven=1.63 / cpa=2.0000。
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


def _commit_all(sess) -> None:
    sess.commit()


def _seed(sess, fn) -> int:
    """在真 commit 的 session 里执行一个 seed 函数。"""
    result = fn(sess)
    sess.commit()
    return result


# ─── auth ─────────────────────────────────────────────────────────────


def test_spu_roi_requires_auth(api_client):
    assert api_client.get("/v2/analytics/spu-roi").status_code == 401


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
    """单 SPU(1 有效单 + 1 已完结退货退款 + 1 已付被取消单)按 spec 公式断言。"""
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

    # 广告侧(USD 原生)
    assert item["ad_count"] == 1
    assert item["ad_orders"] == 5
    assert item["spend"] == "10.0000"
    assert item["gmv_ad"] == "50.0000"
    assert item["roi_l0"] == "5.00"
    assert item["ad_first_day"] == DAY
    assert item["ad_last_day"] == DAY

    # 销售侧(有效单,VND→USD)
    assert item["order_count"] == 1  # CANCELLED 单不进销售
    assert item["units_sold"] == 5
    assert item["sales"] == "100.0000"

    # 退款净额桶(仅有效订单的 REFUND_ONLY + RETURN_AND_REFUND)
    assert item["refund_only_qty"] == 0
    assert item["refund_only_amount"] == "0.0000"
    assert item["refund_return_qty"] == 1
    assert item["refund_return_amount"] == "20.0000"
    assert item["refund_net_qty"] == 1
    assert item["refund_net_amount"] == "20.0000"
    assert item["refund_rate"] == "0.20"

    # 已付被取消订单退款(信息列,不入净额)
    assert item["refund_cancelled_qty"] == 2
    assert item["refund_cancelled_amount"] == "20.0000"  # 已知金额小计
    assert item["refund_cancelled_missing_lines"] == 1

    # 成本解析:DEFAULT_K1 → ⚠(页面标题旁)
    assert item["cost_source"] == "DEFAULT_K1"
    assert item["unit_cost_used"] == m4(K1_CNY * CNY_USD)  # "4.4322"

    # 净/ROI(M13b/M14/M15/M17/M18/M19)
    assert item["return_loss"] == "4.4322"  # 1 件 × 30 CNY × 0.14774
    assert item["platform_fee"] == "11.5600"  # sales 100 × 0.1156
    assert item["net_profit"] == "36.2790"
    assert item["roi_real"] == "7.56"
    assert item["roi_breakeven"] == "1.63"
    assert item["cpa"] == "2.0000"  # spend 10 / ad_orders 5

    # totals 单行 = 该行
    assert body["totals"]["row_count"] == 1
    assert body["totals"]["spend"] == "10.0000"
    assert body["totals"]["sales"] == "100.0000"
    assert body["totals"]["refund_net_amount"] == "20.0000"
    assert body["totals"]["net_profit"] == "36.2790"


def test_spu_roi_manual_cost_source(api_client, readonly_key, db_engine):
    """命中 manual_product_costs 有效行 → cost_source=MANUAL,unit_cost_used 用真值。"""
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
    assert item["unit_cost_used"] == m4(Decimal("25") * CNY_USD)  # "3.6935"
    # 货损/净利按 25 CNY/件 重算:return_loss = 1×3.6935
    assert item["return_loss"] == "3.6935"
    assert item["net_profit"] == "39.9725"


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


# ─── 分页 / 搜索 / 排序 ───────────────────────────────────────────────


def test_spu_roi_default_sort_roi_asc_pagination_and_totals(
    api_client, readonly_key, db_engine
):
    """3 个活动 SPU:默认实际 ROI 升序;分页 limit/offset;totals 跨分页加总。"""
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)  # roi 7.56
        _seed(sess, _seed_spu_b)  # roi 1.80
        _seed(sess, _seed_spu_c)  # roi 3.00

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 全量
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": Q, "sort": "roi_real"}
    )
    body = r.json()
    assert body["total"] == 3
    assert [i["spu_id"] for i in body["items"]] == [
        "TEST_ROI_SPU_B",  # 1.80
        "TEST_ROI_SPU_C",  # 3.00
        "TEST_ROI_SPU_A",  # 7.56
    ]
    assert [i["roi_real"] for i in body["items"]] == ["1.80", "3.00", "7.56"]

    # totals 跨分页、当前筛选加总
    totals = body["totals"]
    assert totals["row_count"] == 3
    assert totals["spend"] == "70.0000"  # 10+50+10
    assert totals["sales"] == "220.0000"  # 100+90+30
    assert totals["refund_net_amount"] == "20.0000"
    assert totals["net_profit"] == "55.8138"
    # 行加总 == totals(每行已是 4 位小数字符串)
    row_sum = {
        "spend": sum((Decimal(i["spend"]) for i in body["items"]), Decimal("0")),
        "sales": sum((Decimal(i["sales"]) for i in body["items"]), Decimal("0")),
        "refund_net_amount": sum(
            (Decimal(i["refund_net_amount"]) for i in body["items"]), Decimal("0")
        ),
        "net_profit": sum((Decimal(i["net_profit"]) for i in body["items"]), Decimal("0")),
    }
    assert m4(row_sum["spend"]) == totals["spend"]
    assert m4(row_sum["sales"]) == totals["sales"]
    assert m4(row_sum["refund_net_amount"]) == totals["refund_net_amount"]
    assert m4(row_sum["net_profit"]) == totals["net_profit"]

    # 分页:limit=2 → B,C;offset=2 → A
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 0},
    )
    b2 = r2.json()
    assert [i["spu_id"] for i in b2["items"]] == ["TEST_ROI_SPU_B", "TEST_ROI_SPU_C"]
    assert b2["total"] == 3  # total 不随分页
    assert b2["totals"]["row_count"] == 3

    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 2},
    )
    b3 = r3.json()
    assert [i["spu_id"] for i in b3["items"]] == ["TEST_ROI_SPU_A"]


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
    # A 36.2790 > C 3.2354 > B 16.2994? B=16.2994 → desc: A,B,C
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


def test_spu_roi_rejects_bad_params(api_client, readonly_key):
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
        "spend": "0.0000",
        "sales": "0.0000",
        "refund_net_amount": "0.0000",
        "return_loss": "0.0000",
        "net_profit": "0.0000",
    }
    meta = body["meta"]
    assert meta["fx"] == {
        "usd_vnd": "26330.0000",
        "cny_usd": "0.1477",
        "as_of": "2026-09-05",
        "source": "fixed-const",
    }
    assert meta["fee"]["mode"] == "baseline"
    assert meta["fee"]["rate"] == "0.1156"
    assert meta["fee"]["override"] is None
    assert meta["cost_assumption"]
    assert "first_day" in meta["window"]
    assert "last_day" in meta["window"]
    assert meta["unattributed_refund_lines"] >= 0
    assert meta["computed_at"]
    assert meta["currency"] == {
        "display": "USD",
        "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
    }
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
    assert meta["fee"]["mode"] == "override"
    assert meta["fee"]["rate"] == "0.20"
    assert meta["fee"]["override"] == "0.20"
    # platform_fee = 100 × 0.20 = 20 → net_profit = 80−22.161−10−20 = 27.839
    item = body["items"][0]
    assert item["platform_fee"] == "20.0000"
    assert item["net_profit"] == "27.8390"


# ─── 页面契约 ─────────────────────────────────────────────────────────


def test_spu_roi_page_returns_html(api_client, readonly_key):
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers


def test_spu_roi_page_requires_auth(api_client):
    assert api_client.get("/v2/pages/spu-roi").status_code == 401


def test_spu_roi_page_shell_contract(api_client, readonly_key):
    """页面 HTML shell:标题、静态资源相对路径、warm-paper token。"""
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "SPU 实际 ROI" in body, "missing page title"
    assert "../../static/vendor/bootstrap.min.css" in body, "missing bootstrap link"
    assert "../../static/js/spu-roi.js" in body, "missing spu-roi.js link"
    # 前缀安全(2026-08-31 回归):无根绝对路径资源引用
    assert 'href="/' not in body
    assert 'src="/' not in body
    # warm-paper 工业操作台 token
    assert "--paper:" in body
    assert "--accent:" in body
    assert "--mono:" in body
    assert "border-radius: 0" in body
    # 无外链字体
    assert "fonts.googleapis.com" not in body
    assert "fonts.gstatic.com" not in body
    # 无内联事件处理器
    for forbidden in ("onclick=", "onsubmit=", "onchange="):
        assert forbidden not in body, f"inline handler found: {forbidden}"


def test_spu_roi_js_targets_dashboard_hooks():
    """spu-roi.js 必须存在且渲染表格与结余带。"""
    from pathlib import Path

    js_path = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-roi.js"
    )
    src = js_path.read_text(encoding="utf-8")
    # 必须消费 /v2/analytics/spu-roi(带 PREFIX 推导)
    assert "/v2/analytics/spu-roi" in src
    assert "PREFIX" in src
    assert "unwrap" in src
    assert "401" in src  # 401 → login 跳转
    assert "roi_breakeven" in src  # 红绿判据字段
    assert "cost_source" in src
    assert "DEFAULT_K1" in src  # ⚠ 判断
