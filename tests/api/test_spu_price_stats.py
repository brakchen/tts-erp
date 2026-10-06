"""GET /v2/analytics/spu-roi 价格统计（priceStats / priceCoverage）契约测试。

口径唯一真相 = docs/business/spu-profitability.md（已付款白名单、经营窗口、
退款不擦除历史成交价）+ docs/design/spu-price-statistics.md（六指标 wire 契约）。
数学内核 = tts_erp_v2/analytics/spu_profitability/_price_math.py（件数加权）。

本文件锁定（全部经真实隔离测试库 + 真实 FastAPI 路由）：
1. 六指标 purchase/originalSale/paid × mean/median 都是件数加权，不是行均值；
2. 范围 = 店铺 + 已应用 SPU 范围 + 经营窗口内已付款商品件（排除未付款/挂起/
   取消/赠品/未知赠品/非法数量/无观察），并为每个排除原因出零计数；
3. q / sort / limit / offset 只影响 items，不动 totals 与全 scope 重聚合；
4. 无样本 → null（绝不 0），显式 0 是有效观察；wire 金额四位小数字符串；
5. 同一汇率快照换算 CNY；无法验证的币种 → 503 PRICE_FX_UNAVAILABLE；
6. 后续退款/全损不擦除历史 paid 观察；payment.total_amount 与妙手价格不参与；
7. 只有最新的 canonical 观察生效，旧观察只留在审计历史；
8. spu-roi 与 scope=focused / spu_ids 口径一致。

数据隔离：共享 api conftest 只自动 wipe shops/products_spu/manual_costs/
api_keys；本模块自清其余 TEST_ 行（observations → lines → orders → raw →
miaoshou → focused），module autouse 前后各 wipe 一次。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import _price_stats
from tts_erp_v2.analytics.spu_profitability._price_stats import (
    PriceCoverageStatus,
    PriceStatsBasis,
    PriceStatsRequest,
    read_price_stats,
)
from tts_erp_v2.analytics.spu_profitability._types import FxBasis
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    Credentials,
    FocusedSpu,
    ManualProductCost,
    MiaoshouPurchasePrice,
    RawRecord,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.db.models.commerce import SalesOrderLinePriceObservation

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# ─── 常量 ────────────────────────────────────────────────────────────

DAY = "2026-09-01"
NEXT_DAY = "2026-09-10"
ORDER_TS = f"{DAY}T08:00:00+00:00"
NEXT_ORDER_TS = f"{NEXT_DAY}T08:00:00+00:00"
# 2099 时间戳保证这是最新快照；VND→CNY = usd_cny/usd_vnd = 6.5/26000 = 0.00025
FX_SEED_TS = "2099-12-31T00:00:00+00:00"
USD_CNY = Decimal("6.5")
USD_VND = Decimal(26000)
_Q4 = Decimal("0.0001")

PAID_STATUS = "DELIVERED"


def m4(value: Decimal) -> str:
    return format(value.quantize(_Q4, rounding=ROUND_HALF_UP), "f")


# ─── 隔离与 FX 夹具 ──────────────────────────────────────────────────


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.case_lines WHERE case_id IN ("
                "  SELECT c.id FROM after_sales.cases c WHERE c.shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  ))"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.cases WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%')"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_order_line_price_observations "
                "WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%')"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_order_lines WHERE order_pk IN ("
                "  SELECT id FROM commerce.sales_orders WHERE shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'))"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_orders WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%')"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text("DELETE FROM miaoshou.purchase_prices WHERE spu_id LIKE 'TEST_%'")
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM integration.raw_records WHERE external_id LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text("DELETE FROM reporting.focused_spus WHERE spu_id LIKE 'TEST_%'")
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM procurement.manual_product_costs WHERE spu_pk IN ("
                "  SELECT id FROM commerce.products_spu WHERE spu_id LIKE 'TEST_%')"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM integration.credentials "
                "WHERE external_account_id LIKE 'TEST_PRICE_CRED_%'"
            )
        )


@pytest.fixture(autouse=True)
def _wipe_price_rows(db_engine, _isolate_state):
    """Setup + teardown 都清本模块的 TEST_ 行（先子表后父表）。"""
    _wipe(db_engine)
    yield
    _wipe(db_engine)


def _seed_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        snapshot_id = conn.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, "
                " rates_count) "
                "VALUES ('USD', :ts, :ts2, now(), 3) RETURNING id"
            ),
            {"ts": FX_SEED_TS, "ts2": "2100-01-01T00:00:00+00:00"},
        ).scalar()
        for code, rate in (("USD", "1"), ("CNY", str(USD_CNY)), ("VND", str(USD_VND))):
            conn.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:sid, 'USD', :c, :r)"
                ),
                {"sid": snapshot_id, "c": code, "r": rate},
            )


def _del_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM fx.exchange_rate_snapshots "
                "WHERE base_code = 'USD' AND upstream_last_update = :ts"
            ),
            {"ts": FX_SEED_TS},
        )


@pytest.fixture(autouse=True)
def _fx_snapshot(db_engine, _isolate_state):
    """注入本模块自用的确定性汇率快照，teardown 移除。"""
    _seed_fx(db_engine)
    yield
    _del_fx(db_engine)


# ─── 造数 helpers（handler 用独立 session，必须真 commit）────────────


def _seed_shop(sess: Session, seller: str, *, region: str = "VN") -> ChannelAccount:
    account = ChannelAccount(
        platform="tiktok",
        shop_id=seller,
        account_name=f"{seller} 店铺",
        region=region,
        status="active",
    )
    sess.add(account)
    sess.flush()
    return account


def _seed_spu(
    sess: Session, shop: ChannelAccount, spu_id: str, *, status: str = "ACTIVATE"
) -> ChannelProduct:
    spu = ChannelProduct(
        shop_pk=shop.id,
        spu_id=spu_id,
        title=f"{spu_id} 标题",
        status=status,
        main_image_url="https://img.test/x.png",
    )
    sess.add(spu)
    sess.flush()
    return spu


def _seed_manual_cost(sess: Session, spu: ChannelProduct, unit_cost: str) -> None:
    sess.add(
        ManualProductCost(
            spu_pk=spu.id,
            unit_cost=Decimal(unit_cost),
            currency="CNY",
            note="TEST 成本",
            created_by="test",
        )
    )
    sess.flush()


def _seed_miaoshou_price(
    sess: Session, spu: ChannelProduct, *, unit_cost: str, suffix: str
) -> None:
    credential = Credentials(
        provider="miaoshou",
        external_account_id=f"TEST_PRICE_CRED_{suffix}",
        ciphertext=b"\x00" * 32,
    )
    sess.add(credential)
    sess.flush()
    sess.add(
        MiaoshouPurchasePrice(
            credential_id=credential.id,
            miaoshou_shop_id=f"TEST_PRICE_MS_SHOP_{suffix}",
            shop_pk=spu.shop_pk,
            spu_id=spu.spu_id,
            unit_cost=Decimal(unit_cost),
            source_purchase_order_sn=f"TEST_PRICE_PO_{suffix}",
            source_item_id=f"TEST_PRICE_ITEM_{suffix}",
            resolution_status="resolved",
            evidence={"source": "TEST_PRICE_MIAOSHOU"},
            source_purchase_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
    )
    sess.flush()


def _seed_order(
    sess: Session,
    shop: ChannelAccount,
    *,
    order_id: str,
    status: str,
    order_iso: str = ORDER_TS,
    paid_iso: str | None = None,
    payment_amount: str = "0",
    total_amount: str = "0",
) -> SalesOrder:
    order = SalesOrder(
        shop_pk=shop.id,
        order_id=order_id,
        status=status,
        currency="CNY",
        paid_at=datetime.fromisoformat(paid_iso or order_iso),
        order_time=datetime.fromisoformat(order_iso),
        payment_amount=Decimal(payment_amount),
        total_amount=Decimal(total_amount),
    )
    sess.add(order)
    sess.flush()
    return order


def _seed_line(
    sess: Session,
    order: SalesOrder,
    spu: ChannelProduct | None,
    *,
    line_ext: str,
    qty: str = "1",
    unit_price: str = "0",
    currency: str = "CNY",
) -> SalesOrderLine:
    line = SalesOrderLine(
        order_pk=order.id,
        external_line_id=line_ext,
        spu_pk=spu.id if spu is not None else None,
        quantity=Decimal(qty),
        unit_price=Decimal(unit_price),
        currency=currency,
    )
    sess.add(line)
    sess.flush()
    return line


def _seed_raw_record(sess: Session, *, external_id: str) -> RawRecord:
    record = RawRecord(
        endpoint="/order/202309/orders/search",
        external_id=external_id,
        payload={"order_id": external_id},
        payload_hash=hashlib.sha256(external_id.encode()).hexdigest(),
    )
    sess.add(record)
    sess.flush()
    return record


def _seed_observation(
    sess: Session,
    *,
    shop: ChannelAccount,
    order: SalesOrder,
    line_ext: str,
    spu: ChannelProduct | None = None,
    qty: str = "1",
    quantity_status: str = "OBSERVED",
    effective_qty: str | None = None,
    original: str | None = "10",
    paid: str | None = "8",
    currency: str | None = "CNY",
    original_status: str = "OBSERVED",
    paid_status: str = "OBSERVED",
    gift_status: str = "NOT_GIFT",
    parent_payment_status: str = PAID_STATUS,
    version: str = "2026-09-01T00:00:00+00:00",
    captured: str = "2026-09-01T01:00:00+00:00",
    semantic: str | None = None,
) -> SalesOrderLinePriceObservation:
    """直接落一条 canonical 观察（生产由 order_prices producer 写入）。"""
    raw = _seed_raw_record(sess, external_id=f"TEST_PRICE_RAW_{line_ext}_{version}")
    effective: Decimal | None
    if effective_qty is not None:
        effective = Decimal(effective_qty)
    elif quantity_status in ("OBSERVED", "DEFAULT_ONE_PER_LINE"):
        effective = Decimal(qty)
    else:
        effective = None
    observation = SalesOrderLinePriceObservation(
        shop_pk=shop.id,
        order_pk=order.id,
        external_line_id=line_ext,
        raw_record_id=raw.id,
        source_endpoint="ORDER_SEARCH",
        source_payload_hash=f"TEST_PRICE_PAYLOAD_{line_ext}",
        semantic_observation_hash=semantic or f"TEST_PRICE_SEM_{line_ext}_{version}",
        source_order_version_at=datetime.fromisoformat(version),
        source_captured_at=datetime.fromisoformat(captured),
        spu_pk=spu.id if spu is not None else None,
        raw_quantity=Decimal(qty) if quantity_status != "MISSING" else None,
        effective_quantity=effective,
        quantity_status=quantity_status,
        line_status_raw=parent_payment_status,
        parent_payment_status=parent_payment_status,
        gift_status=gift_status,
        original_price_native=None if original is None else Decimal(original),
        paid_price_native=None if paid is None else Decimal(paid),
        currency=currency,
        original_price_status=original_status,
        paid_price_status=paid_status,
    )
    sess.add(observation)
    sess.flush()
    return observation


def _seed_case(
    sess: Session,
    *,
    shop_pk: int,
    order_pk: int,
    ext_case: str,
    case_type: str,
    status: str,
    line_pk: int,
    qty: str = "1",
    refund_amount: str = "500",
) -> None:
    # pi-lens-ignore: python-sql-injection
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
        {
            "shop": shop_pk,
            "o": order_pk,
            "ec": ext_case,
            "ct": case_type,
            "st": status,
        },
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO after_sales.case_lines "
            "(case_id, sales_order_line_id, external_case_line_id, quantity, "
            " refund_amount, currency) "
            "VALUES (:c, :sl, :ext, CAST(:qty AS numeric), "
            " CAST(:amt AS numeric), 'VND')"
        ),
        {
            "c": case_pk,
            "sl": line_pk,
            "ext": f"{ext_case}_L1",
            "qty": qty,
            "amt": refund_amount,
        },
    )
    sess.flush()


def _commit(sess: Session) -> None:
    sess.commit()


# ─── 请求 helpers ────────────────────────────────────────────────────


def _get(client, key: str, **params: Any) -> dict:
    response = client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _items(body: dict) -> dict[str, dict]:
    return {item["spu_id"]: item for item in body["items"]}


def _metric(item: dict, metric: str) -> dict:
    return item["priceStats"][metric]


# ─── 1. 件数加权口径（不是行均值）────────────────────────────────────


def test_price_stats_weighted_mean_and_median_not_row_average(
    api_client, readonly_key, db_engine
):
    """§7.1 oracle：A 成本 10×1 + B 成本 40×9 → 总量均值 37、加权中位数 40。

    行均值/简单行中位数都会得到 25，所以这组 fixture 能区分加权与非加权。
    """
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_WEIGHTED")
        spu_a = _seed_spu(sess, shop, "TEST_PRICE_SPU_A")
        spu_b = _seed_spu(sess, shop, "TEST_PRICE_SPU_B")
        _seed_manual_cost(sess, spu_a, "10")
        # B 没有人工成本 → DEFAULT_K1 = 40 CNY/件（estimated）
        order_a = _seed_order(
            sess, shop, order_id="TEST_PRICE_ORDER_A", status=PAID_STATUS
        )
        line_a = _seed_line(sess, order_a, spu_a, line_ext="TEST_PRICE_LINE_A", qty="1")
        _seed_observation(
            sess,
            shop=shop,
            order=order_a,
            line_ext=line_a.external_line_id,
            spu=spu_a,
            qty="1",
            original="20",
            paid="18",
        )
        order_b = _seed_order(
            sess, shop, order_id="TEST_PRICE_ORDER_B", status=PAID_STATUS
        )
        line_b = _seed_line(sess, order_b, spu_b, line_ext="TEST_PRICE_LINE_B", qty="9")
        _seed_observation(
            sess,
            shop=shop,
            order=order_b,
            line_ext=line_b.external_line_id,
            spu=spu_b,
            qty="9",
            original="50",
            paid="45",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(
        api_client,
        readonly_key,
        shop_pk=shop_pk,
        w_start=DAY,
        w_end=DAY,
        sort="paidPriceMedian",
        order="asc",
    )
    items = _items(body)
    assert set(items) == {"TEST_PRICE_SPU_A", "TEST_PRICE_SPU_B"}

    purchase_a = _metric(items["TEST_PRICE_SPU_A"], "purchase")
    assert purchase_a == {
        "mean": "10.0000",
        "median": "10.0000",
        "eligibleQuantity": 1,
        "observedQuantity": 1,
        "missingQuantity": 0,
        "invalidQuantity": 0,
        "observedLineCount": 1,
        "missingLineCount": 0,
        "invalidLineCount": 0,
        "coverageRatio": "1.0000",
        "status": "complete",
        "source": "roi_unit_cost",
        "estimated": False,
    }
    purchase_b = _metric(items["TEST_PRICE_SPU_B"], "purchase")
    assert purchase_b["mean"] == "40.0000"
    assert purchase_b["median"] == "40.0000"
    assert purchase_b["eligibleQuantity"] == 9
    assert purchase_b["source"] == "roi_unit_cost"
    assert purchase_b["estimated"] is True
    assert _metric(items["TEST_PRICE_SPU_B"], "originalSale")["mean"] == "50.0000"
    assert _metric(items["TEST_PRICE_SPU_B"], "paid")["median"] == "45.0000"

    totals = body["totals"]["priceStats"]
    # Σ(价×件)/Σ件 = (10×1 + 40×9)/10 = 37；加权中位数 = 第 5/6 件 = 40
    assert totals["purchase"]["mean"] == "37.0000"
    assert totals["purchase"]["median"] == "40.0000"
    assert totals["purchase"]["eligibleQuantity"] == 10
    assert totals["purchase"]["observedQuantity"] == 10
    assert totals["purchase"]["coverageRatio"] == "1.0000"
    assert totals["purchase"]["status"] == "complete"
    assert totals["purchase"]["source"] == "roi_unit_cost"
    assert totals["purchase"]["estimated"] is True
    # (20×1 + 50×9)/10 = 47；单位序列 [20,50×9] 中位数 50
    assert totals["originalSale"]["mean"] == "47.0000"
    assert totals["originalSale"]["median"] == "50.0000"
    # (18×1 + 45×9)/10 = 42.3；中位数 45
    assert totals["paid"]["mean"] == "42.3000"
    assert totals["paid"]["median"] == "45.0000"
    assert totals["paid"]["eligibleQuantity"] == 10
    assert totals["paid"]["observedQuantity"] == 10
    assert totals["paid"]["source"] == "tiktok_line_item_sale_price"
    assert totals["paid"]["estimated"] is False

    coverage = body["totals"]["priceCoverage"]
    assert coverage["eligibleLineCount"] == 2
    assert coverage["eligibleQuantity"] == 10
    assert coverage["excludedValidQuantity"] == 0
    assert coverage["missingObservationLineCount"] == 0
    assert coverage["missingCurrencyQuantity"] == 0
    assert coverage["fxUnavailableQuantity"] == 0


def test_price_stats_odd_even_median_zero_and_missing(api_client, readonly_key, db_engine):
    """奇/偶中位数、单观察、显式 0 有效、缺价 → null（绝不 0）。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_MEDIAN")
        odd = _seed_spu(sess, shop, "TEST_PRICE_SPU_ODD")
        even = _seed_spu(sess, shop, "TEST_PRICE_SPU_EVEN")
        zero = _seed_spu(sess, shop, "TEST_PRICE_SPU_ZERO")
        missing = _seed_spu(sess, shop, "TEST_PRICE_SPU_MISSING")
        for spu in (odd, even, zero, missing):
            _seed_manual_cost(sess, spu, "10")

        # 奇数件：20×1 + 50×2 + 80×2 = 5 件 → 中位数 rank 3 = 50
        for index, (original, paid, qty) in enumerate(
            (("20", "18", "1"), ("50", "45", "2"), ("80", "72", "2")), start=1
        ):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_ORDER_ODD_{index}",
                status=PAID_STATUS,
            )
            line = _seed_line(
                sess,
                order,
                odd,
                line_ext=f"TEST_PRICE_LINE_ODD_{index}",
                qty=qty,
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=odd,
                qty=qty,
                original=original,
                paid=paid,
            )
        # 偶数件：10×2 + 40×2 = 4 件 → 中位数 (10+40)/2 = 25
        for index, (original, paid) in enumerate((("10", "9"), ("40", "36")), start=1):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_ORDER_EVEN_{index}",
                status=PAID_STATUS,
            )
            line = _seed_line(
                sess,
                order,
                even,
                line_ext=f"TEST_PRICE_LINE_EVEN_{index}",
                qty="2",
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=even,
                qty="2",
                original=original,
                paid=paid,
            )
        # 显式 0：有效观察，进分母且均值/中位数为 0.0000
        order_zero = _seed_order(
            sess, shop, order_id="TEST_PRICE_ORDER_ZERO", status=PAID_STATUS
        )
        line_zero = _seed_line(
            sess, order_zero, zero, line_ext="TEST_PRICE_LINE_ZERO", qty="1"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order_zero,
            line_ext=line_zero.external_line_id,
            spu=zero,
            qty="1",
            original="0",
            paid="0",
        )
        # 缺原价：paid 有效、original MISSING → original 全 null + missing 计数
        order_missing = _seed_order(
            sess, shop, order_id="TEST_PRICE_ORDER_MISSING", status=PAID_STATUS
        )
        line_missing = _seed_line(
            sess, order_missing, missing, line_ext="TEST_PRICE_LINE_MISSING", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order_missing,
            line_ext=line_missing.external_line_id,
            spu=missing,
            qty="2",
            original=None,
            original_status="MISSING",
            paid="5",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    items = _items(body)

    odd_original = _metric(items["TEST_PRICE_SPU_ODD"], "originalSale")
    assert odd_original["mean"] == m4((Decimal(20) + Decimal(100) + Decimal(160)) / 5)
    assert odd_original["median"] == "50.0000"
    assert odd_original["eligibleQuantity"] == 5
    odd_paid = _metric(items["TEST_PRICE_SPU_ODD"], "paid")
    assert odd_paid["mean"] == m4(Decimal(252) / 5)
    assert odd_paid["median"] == "45.0000"

    even_original = _metric(items["TEST_PRICE_SPU_EVEN"], "originalSale")
    assert even_original["mean"] == "25.0000"
    assert even_original["median"] == "25.0000"
    assert even_original["eligibleQuantity"] == 4

    zero_paid = _metric(items["TEST_PRICE_SPU_ZERO"], "paid")
    assert zero_paid["mean"] == "0.0000"
    assert zero_paid["median"] == "0.0000"
    assert zero_paid["observedQuantity"] == 1
    assert zero_paid["status"] == "complete"

    missing_original = _metric(items["TEST_PRICE_SPU_MISSING"], "originalSale")
    assert missing_original["mean"] is None
    assert missing_original["median"] is None
    assert missing_original["observedQuantity"] == 0
    assert missing_original["missingQuantity"] == 2
    assert missing_original["missingLineCount"] == 1
    assert missing_original["coverageRatio"] == "0.0000"
    assert missing_original["status"] == "partial"
    assert missing_original["source"] == "tiktok_line_item_original_price"
    missing_paid = _metric(items["TEST_PRICE_SPU_MISSING"], "paid")
    assert missing_paid["mean"] == "5.0000"
    assert missing_paid["status"] == "complete"


# ─── 2. population 范围与排除原因 ────────────────────────────────────


def test_price_stats_exclude_unpaid_on_hold_cancelled_gift_and_unknown(
    api_client, readonly_key, db_engine
):
    """统计范围 = 经营窗口内已付款商品件；每种排除原因互斥且各自出计数。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_POPULATION")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_POPULATION")
        _seed_manual_cost(sess, spu, "7")
        cases = (
            ("TEST_PRICE_POP_ELIGIBLE", PAID_STATUS, "2", "NOT_GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_UNPAID", "UNPAID", "3", "NOT_GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_ONHOLD", "ON_HOLD", "1", "NOT_GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_CANCELLED", "CANCELLED", "4", "NOT_GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_GIFT", PAID_STATUS, "5", "GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_UNKNOWNGIFT", PAID_STATUS, "6", "UNKNOWN", "OBSERVED"),
            ("TEST_PRICE_POP_UNKNOWNSTATUS", "SOMETHING_ELSE", "7", "NOT_GIFT", "OBSERVED"),
            ("TEST_PRICE_POP_BADQTY", PAID_STATUS, "8", "NOT_GIFT", "INVALID_ZERO"),
        )
        for order_id, status, qty, gift, quantity_status in cases:
            order = _seed_order(sess, shop, order_id=order_id, status=status)
            line = _seed_line(
                sess, order, spu, line_ext=f"{order_id}_LINE", qty=qty
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=spu,
                qty=qty,
                quantity_status=quantity_status,
                original="30",
                paid="25",
                gift_status=gift,
                parent_payment_status=status,
            )
        # 无观察的已付款行 → missingObservationLineCount
        order_no_obs = _seed_order(
            sess, shop, order_id="TEST_PRICE_POP_NOOBS", status=PAID_STATUS
        )
        _seed_line(sess, order_no_obs, spu, line_ext="TEST_PRICE_POP_NOOBS_LINE", qty="9")
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(body)["TEST_PRICE_SPU_POPULATION"]
    coverage = item["priceCoverage"]
    assert coverage["eligibleLineCount"] == 1
    assert coverage["eligibleQuantity"] == 2
    assert coverage["excludedUnpaidQuantity"] == 3
    assert coverage["excludedOnHoldQuantity"] == 1
    assert coverage["excludedCancelledQuantity"] == 4
    assert coverage["excludedGiftQuantity"] == 5
    assert coverage["unknownGiftQuantity"] == 6
    assert coverage["unknownStatusQuantity"] == 7
    assert coverage["invalidQuantityLineCount"] == 1
    assert coverage["missingObservationLineCount"] == 1
    # 已知有效件数被互斥排除的合计 = 3+1+4+5+6+7
    assert coverage["excludedValidQuantity"] == 26

    paid = _metric(item, "paid")
    assert paid["eligibleQuantity"] == 2
    assert paid["observedQuantity"] == 2
    assert paid["mean"] == "25.0000"
    assert paid["median"] == "25.0000"
    assert paid["status"] == "complete"
    assert _metric(item, "purchase")["mean"] == "7.0000"


def test_price_stats_coverage_isolation_is_per_spu(api_client, readonly_key, db_engine):
    """§5.3 coverage 夹具：A 不继承 B 的取消/缺币种计数；C 只剩排除行也保留 item。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_ISOLATION")
        spu_a = _seed_spu(sess, shop, "TEST_PRICE_SPU_ISO_A")
        spu_b = _seed_spu(sess, shop, "TEST_PRICE_SPU_ISO_B")
        spu_c = _seed_spu(sess, shop, "TEST_PRICE_SPU_ISO_C")
        for spu in (spu_a, spu_b, spu_c):
            _seed_manual_cost(sess, spu, "10")

        order_a = _seed_order(
            sess, shop, order_id="TEST_PRICE_ISO_ORDER_A", status=PAID_STATUS
        )
        line_a = _seed_line(sess, order_a, spu_a, line_ext="TEST_PRICE_ISO_LINE_A")
        _seed_observation(
            sess,
            shop=shop,
            order=order_a,
            line_ext=line_a.external_line_id,
            spu=spu_a,
            original="20",
            paid="18",
        )

        order_b_cancelled = _seed_order(
            sess, shop, order_id="TEST_PRICE_ISO_ORDER_B1", status="CANCELLED"
        )
        line_b_cancelled = _seed_line(
            sess, order_b_cancelled, spu_b, line_ext="TEST_PRICE_ISO_LINE_B1", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order_b_cancelled,
            line_ext=line_b_cancelled.external_line_id,
            spu=spu_b,
            qty="2",
            original="20",
            paid="18",
            parent_payment_status="CANCELLED",
        )
        order_b_blank = _seed_order(
            sess, shop, order_id="TEST_PRICE_ISO_ORDER_B2", status=PAID_STATUS
        )
        line_b_blank = _seed_line(
            sess, order_b_blank, spu_b, line_ext="TEST_PRICE_ISO_LINE_B2", qty="4"
        )
        # currency 为空白（显式空串）：既不回退父订单币种，也不冒充 FX 不可用
        _seed_observation(
            sess,
            shop=shop,
            order=order_b_blank,
            line_ext=line_b_blank.external_line_id,
            spu=spu_b,
            qty="4",
            original="20",
            paid="18",
            currency=None,
        )

        order_c = _seed_order(
            sess, shop, order_id="TEST_PRICE_ISO_ORDER_C", status="CANCELLED"
        )
        line_c = _seed_line(
            sess, order_c, spu_c, line_ext="TEST_PRICE_ISO_LINE_C", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order_c,
            line_ext=line_c.external_line_id,
            spu=spu_c,
            qty="2",
            original="20",
            paid="18",
            parent_payment_status="CANCELLED",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(
        api_client,
        readonly_key,
        shop_pk=shop_pk,
        w_start=DAY,
        w_end=DAY,
        include_all="true",
    )
    items = _items(body)
    assert set(items) == {
        "TEST_PRICE_SPU_ISO_A",
        "TEST_PRICE_SPU_ISO_B",
        "TEST_PRICE_SPU_ISO_C",
    }

    coverage_a = items["TEST_PRICE_SPU_ISO_A"]["priceCoverage"]
    assert coverage_a == {
        "eligibleLineCount": 1,
        "eligibleQuantity": 1,
        "excludedUnpaidQuantity": 0,
        "excludedOnHoldQuantity": 0,
        "excludedCancelledQuantity": 0,
        "excludedGiftQuantity": 0,
        "unknownGiftQuantity": 0,
        "unknownStatusQuantity": 0,
        "invalidQuantityLineCount": 0,
        "excludedValidQuantity": 0,
        "missingCurrencyQuantity": 0,
        "fxUnavailableQuantity": 0,
        "missingObservationLineCount": 0,
    }

    coverage_b = items["TEST_PRICE_SPU_ISO_B"]["priceCoverage"]
    assert coverage_b["eligibleLineCount"] == 1
    assert coverage_b["eligibleQuantity"] == 4
    assert coverage_b["excludedCancelledQuantity"] == 2
    assert coverage_b["excludedValidQuantity"] == 2
    assert coverage_b["missingCurrencyQuantity"] == 4
    assert coverage_b["fxUnavailableQuantity"] == 0
    # 观察值 status=OBSERVED 但币种空白 → 该 metric 记 invalid（不是 missing）
    paid_b = _metric(items["TEST_PRICE_SPU_ISO_B"], "paid")
    assert paid_b["observedQuantity"] == 0
    assert paid_b["invalidQuantity"] == 4
    assert paid_b["missingQuantity"] == 0
    assert paid_b["mean"] is None
    assert paid_b["status"] == "partial"

    coverage_c = items["TEST_PRICE_SPU_ISO_C"]["priceCoverage"]
    assert coverage_c["eligibleLineCount"] == 0
    assert coverage_c["eligibleQuantity"] == 0
    assert coverage_c["excludedCancelledQuantity"] == 2
    assert coverage_c["excludedValidQuantity"] == 2
    paid_c = _metric(items["TEST_PRICE_SPU_ISO_C"], "paid")
    assert paid_c["mean"] is None
    assert paid_c["median"] is None
    assert paid_c["eligibleQuantity"] == 0
    assert paid_c["status"] == "no_samples"
    assert paid_c["source"] == "none"
    assert paid_c["coverageRatio"] is None

    totals_coverage = body["totals"]["priceCoverage"]
    assert totals_coverage["eligibleLineCount"] == 2
    assert totals_coverage["eligibleQuantity"] == 5
    assert totals_coverage["excludedCancelledQuantity"] == 4
    assert totals_coverage["excludedValidQuantity"] == 4
    assert totals_coverage["missingCurrencyQuantity"] == 4
    assert totals_coverage["fxUnavailableQuantity"] == 0
    assert totals_coverage["unknownGiftQuantity"] == 0


def test_price_stats_empty_scope_returns_null_not_zero(api_client, readonly_key, db_engine):
    """空选择（focused 无成员）→ 完整 envelope + 六指标 no_samples/null。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_EMPTY")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_EMPTY")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_EMPTY_ORDER", status=PAID_STATUS
        )
        line = _seed_line(sess, order, spu, line_ext="TEST_PRICE_EMPTY_LINE")
        _seed_observation(
            sess, shop=shop, order=order, line_ext=line.external_line_id, spu=spu
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(
        api_client, readonly_key, shop_pk=shop_pk, scope="focused", w_start=DAY, w_end=DAY
    )
    assert body["items"] == []
    assert body["total"] == 0
    for metric in ("purchase", "originalSale", "paid"):
        stats = body["totals"]["priceStats"][metric]
        assert stats["mean"] is None
        assert stats["median"] is None
        assert stats["eligibleQuantity"] == 0
        assert stats["observedQuantity"] == 0
        assert stats["coverageRatio"] is None
        assert stats["status"] == "no_samples"
        assert stats["source"] == "none"
        assert stats["estimated"] is False
    coverage = body["totals"]["priceCoverage"]
    assert coverage["eligibleLineCount"] == 0
    assert coverage["eligibleQuantity"] == 0
    assert list(coverage.values()) == [0] * len(coverage)
    assert body["meta"]["priceSort"] is None
    assert body["meta"]["priceCurrency"] == "CNY"


def test_price_stats_no_samples_for_missing_observation(
    api_client, readonly_key, db_engine
):
    """有已付款件但没有任何观察 → no_samples + missingObservationLineCount，不进比率。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_NOOBS")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_NOOBS")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_NOOBS_ORDER", status=PAID_STATUS
        )
        _seed_line(sess, order, spu, line_ext="TEST_PRICE_NOOBS_LINE", qty="3")
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(body)["TEST_PRICE_SPU_NOOBS"]
    coverage = item["priceCoverage"]
    assert coverage["eligibleLineCount"] == 0
    assert coverage["eligibleQuantity"] == 0
    assert coverage["missingObservationLineCount"] == 1
    assert _metric(item, "paid")["status"] == "no_samples"
    assert _metric(item, "paid")["mean"] is None


# ─── 3. 窗口 / 不受搜索排序分页影响 ─────────────────────────────────


def test_price_stats_follow_operating_window_by_order_time(
    api_client, readonly_key, db_engine
):
    """经营窗口按 COALESCE(order_time, paid_at) 裁剪；窗口外不进入统计。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_WINDOW")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_WINDOW")
        _seed_manual_cost(sess, spu, "10")
        for suffix, order_iso, original, paid in (
            ("D1", ORDER_TS, "10", "8"),
            ("D10", NEXT_ORDER_TS, "50", "45"),
        ):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_WINDOW_ORDER_{suffix}",
                status=PAID_STATUS,
                order_iso=order_iso,
            )
            line = _seed_line(
                sess,
                order,
                spu,
                line_ext=f"TEST_PRICE_WINDOW_LINE_{suffix}",
                qty="2",
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=spu,
                qty="2",
                original=original,
                paid=paid,
            )
        _commit(sess)
        shop_pk = shop.id

    first = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(first)["TEST_PRICE_SPU_WINDOW"]
    assert item["priceCoverage"]["eligibleQuantity"] == 2
    assert _metric(item, "paid")["mean"] == "8.0000"
    assert first["totals"]["priceStats"]["paid"]["mean"] == "8.0000"

    second = _get(
        api_client, readonly_key, shop_pk=shop_pk, w_start=NEXT_DAY, w_end=NEXT_DAY
    )
    item = _items(second)["TEST_PRICE_SPU_WINDOW"]
    assert item["priceCoverage"]["eligibleQuantity"] == 2
    assert _metric(item, "paid")["mean"] == "45.0000"

    both = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=NEXT_DAY)
    assert _items(both)["TEST_PRICE_SPU_WINDOW"]["priceCoverage"][
        "eligibleQuantity"
    ] == 4
    assert both["totals"]["priceStats"]["paid"]["mean"] == "26.5000"

    # 窗口无事实 → 目录行仍在（include_all），但价格 no_samples
    empty = _get(
        api_client,
        readonly_key,
        shop_pk=shop_pk,
        w_start="2026-11-01",
        w_end="2026-11-02",
        include_all="true",
    )
    assert _metric(_items(empty)["TEST_PRICE_SPU_WINDOW"], "paid")["mean"] is None


def test_price_stats_invariant_under_search_sort_and_pagination(
    api_client, readonly_key, db_engine
):
    """q / sort / limit / offset 只影响 items；totals 从全 scope 原始观察重聚合。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_INVARIANT")
        spu_x = _seed_spu(sess, shop, "TEST_PRICE_SPU_INV_X")
        spu_y = _seed_spu(sess, shop, "TEST_PRICE_SPU_INV_Y")
        _seed_manual_cost(sess, spu_x, "10")
        for spu, qty, original, paid in (
            (spu_x, "1", "20", "18"),
            (spu_y, "9", "50", "45"),
        ):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_INV_ORDER_{spu.spu_id}",
                status=PAID_STATUS,
            )
            line = _seed_line(
                sess, order, spu, line_ext=f"TEST_PRICE_INV_LINE_{spu.spu_id}", qty=qty
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=spu,
                qty=qty,
                original=original,
                paid=paid,
            )
        _commit(sess)
        shop_pk = shop.id

    baseline = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    variants = (
        {"q": "TEST_PRICE_SPU_INV_X"},
        {"sort": "paidPriceMedian", "order": "desc"},
        {"sort": "purchasePriceMean", "order": "asc", "limit": 1},
        {"offset": 1, "limit": 1},
        {"sort": "originalSalePriceMedian", "order": "desc", "q": "INV_Y"},
    )
    for variant in variants:
        body = _get(
            api_client,
            readonly_key,
            shop_pk=shop_pk,
            w_start=DAY,
            w_end=DAY,
            **variant,
        )
        assert body["totals"] == baseline["totals"], variant
        assert body["meta"]["priceFx"] == baseline["meta"]["priceFx"], variant
        # basisFingerprint/asOfAt 是 response-local（含 calculatedAt），跨请求可变；
        # 成本 map 本身必须等价。
        assert (
            body["meta"]["priceCost"]["defaultK1Cny"],
            body["meta"]["priceCost"]["estimated"],
        ) == (
            baseline["meta"]["priceCost"]["defaultK1Cny"],
            baseline["meta"]["priceCost"]["estimated"],
        ), variant

    # 分页确实改变 items（否则上面的不变性断言没有意义）
    paged = _get(
        api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY, limit=1
    )
    assert len(paged["items"]) == 1
    assert paged["total"] == 2
    assert _items(baseline)["TEST_PRICE_SPU_INV_X"]["priceStats"] == _items(paged)[
        next(iter(_items(paged)))
    ]["priceStats"]


def test_price_stats_ignore_payment_total_and_miaoshou_prices(
    api_client, readonly_key, db_engine
):
    """paid 只来自 line_items.sale_price；订单级 total/payment 与妙手价格不参与。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_AUTHORITY")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_AUTHORITY")
        # 故意制造与行价不一致的订单级金额；妙手价也故意不同
        _seed_miaoshou_price(sess, spu, unit_cost="7.7777", suffix="AUTH")
        order = _seed_order(
            sess,
            shop,
            order_id="TEST_PRICE_AUTH_ORDER",
            status=PAID_STATUS,
            payment_amount="999999",
            total_amount="888888",
        )
        line = _seed_line(sess, order, spu, line_ext="TEST_PRICE_AUTH_LINE", qty="2")
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="12",
            paid="8",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(body)["TEST_PRICE_SPU_AUTHORITY"]
    paid = _metric(item, "paid")
    assert paid["mean"] == "8.0000"
    assert paid["median"] == "8.0000"
    # 妙手 CNY 采购价 7.7777 不出现：无人工成本 → DEFAULT_K1 40
    purchase = _metric(item, "purchase")
    assert purchase["mean"] == "40.0000"
    assert purchase["source"] == "roi_unit_cost"
    assert purchase["estimated"] is True
    assert "7.7777" not in body["items"][0]["priceStats"]["purchase"]["mean"]


# ─── 4. FX 换算 ─────────────────────────────────────────────────────


def test_price_stats_fx_conversion_uses_snapshot_rates(api_client, readonly_key, db_engine):
    """逐观察值按同一汇率快照转 CNY 后再聚合：USD→6.5，VND→0.00025。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_FX")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_FX")
        _seed_manual_cost(sess, spu, "10")
        usd_order = _seed_order(
            sess, shop, order_id="TEST_PRICE_FX_ORDER_USD", status=PAID_STATUS
        )
        usd_line = _seed_line(
            sess, usd_order, spu, line_ext="TEST_PRICE_FX_LINE_USD", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=usd_order,
            line_ext=usd_line.external_line_id,
            spu=spu,
            qty="2",
            original="4",
            paid="3",
            currency="USD",
        )
        vnd_order = _seed_order(
            sess, shop, order_id="TEST_PRICE_FX_ORDER_VND", status=PAID_STATUS
        )
        vnd_line = _seed_line(
            sess, vnd_order, spu, line_ext="TEST_PRICE_FX_LINE_VND", qty="1"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=vnd_order,
            line_ext=vnd_line.external_line_id,
            spu=spu,
            qty="1",
            original="40000",
            paid="40000",
            currency="VND",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    paid = _metric(_items(body)["TEST_PRICE_SPU_FX"], "paid")
    # 单位序列 [19.5, 19.5, 10] → 均值 16.3333…、中位数 19.5
    assert paid["mean"] == m4((Decimal("19.5") * 2 + Decimal(10)) / 3)
    assert paid["median"] == "19.5000"
    original = _metric(_items(body)["TEST_PRICE_SPU_FX"], "originalSale")
    assert original["mean"] == m4((Decimal(26) * 2 + Decimal(10)) / 3)
    assert original["median"] == "26.0000"

    meta = body["meta"]
    assert meta["priceCurrency"] == "CNY"
    assert meta["priceFx"]["conversionPolicy"] == (
        "native_line_currency_to_cny_before_aggregation"
    )
    assert isinstance(meta["priceFx"]["snapshotId"], int)
    assert meta["priceFx"]["asOfAt"].startswith("2099-12-31")
    assert meta["priceCost"]["defaultK1Cny"] == "40.0000"
    assert meta["priceCost"]["basisFingerprint"].startswith("sha256:")
    assert meta["calculatedAt"] is not None

    # 同一批事实的两次请求：FX 快照与成本 map 语义一致；
    # basisFingerprint/asOfAt 是 response-local，含该次请求的 calculatedAt。
    again = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    assert again["meta"]["priceFx"] == meta["priceFx"]
    assert again["meta"]["priceCost"]["defaultK1Cny"] == "40.0000"
    assert again["meta"]["priceCost"]["estimated"] == meta["priceCost"]["estimated"]
    assert again["totals"]["priceStats"] == body["totals"]["priceStats"]


def test_price_stats_unverifiable_currency_returns_503(api_client, readonly_key, db_engine):
    """已知但不支持的币种 → 503 PRICE_FX_UNAVAILABLE，绝不静默按 1 或 0 换算。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_THB")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_THB")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_THB_ORDER", status=PAID_STATUS
        )
        line = _seed_line(sess, order, spu, line_ext="TEST_PRICE_THB_LINE", qty="1")
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="1",
            original="100",
            paid="90",
            currency="THB",
        )
        _commit(sess)
        shop_pk = shop.id

    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"shop_pk": shop_pk, "w_start": DAY, "w_end": DAY},
    )
    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["code"] == "PRICE_FX_UNAVAILABLE"
    assert payload["retryable"] is True
    assert payload["requestId"]


# ─── 5. 退款不擦除历史成交价 ─────────────────────────────────────────


def test_later_refund_keeps_paid_price_observation(api_client, readonly_key, db_engine):
    """后续售后退款不改变历史 paid 价格观察（价格统计不 join 退款事实）。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_REFUND")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_REFUND")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_REFUND_ORDER", status=PAID_STATUS
        )
        line = _seed_line(
            sess, order, spu, line_ext="TEST_PRICE_REFUND_LINE", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="12",
            paid="8",
        )
        _commit(sess)
        shop_pk = shop.id
        line_pk = line.id
        order_pk = order.id

    before = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    paid_before = _metric(_items(before)["TEST_PRICE_SPU_REFUND"], "paid")

    with Session(db_engine) as sess:
        _seed_case(
            sess,
            shop_pk=shop_pk,
            order_pk=order_pk,
            ext_case="TEST_PRICE_REFUND_CASE",
            case_type="RETURN_AND_REFUND",
            status="RETURN_OR_REFUND_REQUEST_COMPLETE",
            line_pk=line_pk,
            qty="1",
        )
        sess.commit()

    after = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(after)["TEST_PRICE_SPU_REFUND"]
    assert _metric(item, "paid") == paid_before
    assert item["priceCoverage"] == _items(before)["TEST_PRICE_SPU_REFUND"]["priceCoverage"]
    # 退款事实确实生效（否则上面的不变性断言是空断言）
    assert item["refund_return_qty"] == 1


def test_newest_canonical_observation_wins_over_older_valid_price(
    api_client, readonly_key, db_engine
):
    """同一行的最新观察才生效；较新的 MISSING 直接让该 metric 为 null，不回退旧价。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_CANONICAL")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_CANONICAL")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_CANONICAL_ORDER", status=PAID_STATUS
        )
        line = _seed_line(
            sess, order, spu, line_ext="TEST_PRICE_CANONICAL_LINE", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="12",
            paid="8",
            version="2026-09-01T00:00:00+00:00",
            semantic="TEST_PRICE_SEM_CANONICAL_OLD",
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="12",
            paid=None,
            paid_status="MISSING",
            version="2026-09-05T00:00:00+00:00",
            captured="2026-09-05T01:00:00+00:00",
            semantic="TEST_PRICE_SEM_CANONICAL_NEW",
        )
        _commit(sess)
        shop_pk = shop.id
        order_pk = order.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    paid = _metric(_items(body)["TEST_PRICE_SPU_CANONICAL"], "paid")
    assert paid["observedQuantity"] == 0
    assert paid["missingQuantity"] == 2
    assert paid["missingLineCount"] == 1
    assert paid["mean"] is None
    assert paid["status"] == "partial"
    # 旧观察仍作为审计行保留（不是被覆盖/删除）
    with Session(db_engine) as sess:
        rows = (
            sess.execute(
                select(SalesOrderLinePriceObservation)
                .where(SalesOrderLinePriceObservation.order_pk == order_pk)
                .order_by(SalesOrderLinePriceObservation.source_order_version_at)
            )
            .scalars()
            .all()
        )
    assert [row.paid_price_native for row in rows] == [Decimal(8), None]


# ─── 6. wire 形状、类型、枚举与排序 ─────────────────────────────────


PRICE_METRIC_KEYS = {
    "mean",
    "median",
    "eligibleQuantity",
    "observedQuantity",
    "missingQuantity",
    "invalidQuantity",
    "observedLineCount",
    "missingLineCount",
    "invalidLineCount",
    "coverageRatio",
    "status",
    "source",
    "estimated",
}
PRICE_COVERAGE_KEYS = {
    "eligibleLineCount",
    "eligibleQuantity",
    "excludedUnpaidQuantity",
    "excludedOnHoldQuantity",
    "excludedCancelledQuantity",
    "excludedGiftQuantity",
    "unknownGiftQuantity",
    "unknownStatusQuantity",
    "invalidQuantityLineCount",
    "excludedValidQuantity",
    "missingCurrencyQuantity",
    "fxUnavailableQuantity",
    "missingObservationLineCount",
}
PRICE_STATUSES = {"complete", "partial", "no_samples", "unavailable"}
PRICE_SOURCES = {
    "roi_unit_cost",
    "tiktok_line_item_original_price",
    "tiktok_line_item_sale_price",
    "none",
}
PRICE_SORTS = (
    "purchasePriceMean",
    "purchasePriceMedian",
    "originalSalePriceMean",
    "originalSalePriceMedian",
    "paidPriceMean",
    "paidPriceMedian",
)


def test_price_stats_wire_shape_types_and_enums(api_client, readonly_key, db_engine):
    """字段存在性/类型/四位小数/枚举值精确匹配契约；响应里没有第二个计算口径。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_WIRE")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_WIRE")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_WIRE_ORDER", status=PAID_STATUS
        )
        line = _seed_line(sess, order, spu, line_ext="TEST_PRICE_WIRE_LINE", qty="3")
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="3",
            original="12.5",
            paid="8.25",
        )
        _commit(sess)
        shop_pk = shop.id

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    item = _items(body)["TEST_PRICE_SPU_WIRE"]
    assert set(item["priceStats"]) == {"purchase", "originalSale", "paid"}
    assert set(item["priceCoverage"]) == PRICE_COVERAGE_KEYS
    for scope in (item, body["totals"]):
        for metric in ("purchase", "originalSale", "paid"):
            stats = scope["priceStats"][metric]
            assert set(stats) == PRICE_METRIC_KEYS
            assert stats["status"] in PRICE_STATUSES
            assert stats["source"] in PRICE_SOURCES
            assert isinstance(stats["estimated"], bool)
            for key in (
                "eligibleQuantity",
                "observedQuantity",
                "missingQuantity",
                "invalidQuantity",
                "observedLineCount",
                "missingLineCount",
                "invalidLineCount",
            ):
                assert isinstance(stats[key], int) and not isinstance(stats[key], bool)
            for key in ("mean", "median", "coverageRatio"):
                value = stats[key]
                assert value is None or (
                    isinstance(value, str)
                    and value == m4(Decimal(value))
                    and len(value.split(".")[1]) == 4
                )
        for value in scope["priceCoverage"].values():
            assert isinstance(value, int) and not isinstance(value, bool)
    # 四位小数：12.5 → 12.5000；8.25 → 8.2500
    assert _metric(item, "originalSale")["mean"] == "12.5000"
    assert _metric(item, "paid")["mean"] == "8.2500"
    assert body["meta"]["priceSort"] is None


def test_price_sort_identifiers_nulls_last_and_meta(api_client, readonly_key, db_engine):
    """六个价格排序标识都可用、null 双向沉底、meta.priceSort 回传标识。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_SORT")
        cheep = _seed_spu(sess, shop, "TEST_PRICE_SPU_SORT_CHEAP")
        pricey = _seed_spu(sess, shop, "TEST_PRICE_SPU_SORT_PRICEY")
        blank = _seed_spu(sess, shop, "TEST_PRICE_SPU_SORT_NULL")
        for spu in (cheep, pricey, blank):
            _seed_manual_cost(sess, spu, "10")
        for spu, qty, original, paid, paid_status, original_status in (
            (cheep, "1", "20", "18", "OBSERVED", "OBSERVED"),
            (pricey, "9", "50", "45", "OBSERVED", "OBSERVED"),
            (blank, "4", None, None, "MISSING", "MISSING"),
        ):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_SORT_ORDER_{spu.spu_id}",
                status=PAID_STATUS,
            )
            line = _seed_line(
                sess, order, spu, line_ext=f"TEST_PRICE_SORT_LINE_{spu.spu_id}", qty=qty
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=spu,
                qty=qty,
                original=original,
                paid=paid,
                original_status=original_status,
                paid_status=paid_status,
            )
        _commit(sess)
        shop_pk = shop.id

    for identifier in PRICE_SORTS:
        asc = _get(
            api_client,
            readonly_key,
            shop_pk=shop_pk,
            w_start=DAY,
            w_end=DAY,
            sort=identifier,
            order="asc",
        )
        desc = _get(
            api_client,
            readonly_key,
            shop_pk=shop_pk,
            w_start=DAY,
            w_end=DAY,
            sort=identifier,
            order="desc",
        )
        assert asc["meta"]["priceSort"] == identifier
        assert desc["meta"]["priceSort"] == identifier
        # null 双向沉底：无样本 SPU 在两种方向都在最后
        assert asc["items"][-1]["spu_id"] == "TEST_PRICE_SPU_SORT_NULL"
        assert desc["items"][-1]["spu_id"] == "TEST_PRICE_SPU_SORT_NULL"
        assert asc["totals"] == desc["totals"]

    paid_asc = [
        item["spu_id"]
        for item in _get(
            api_client,
            readonly_key,
            shop_pk=shop_pk,
            w_start=DAY,
            w_end=DAY,
            sort="paidPriceMean",
            order="asc",
        )["items"]
    ]
    assert paid_asc == [
        "TEST_PRICE_SPU_SORT_CHEAP",
        "TEST_PRICE_SPU_SORT_PRICEY",
        "TEST_PRICE_SPU_SORT_NULL",
    ]
    paid_desc = [
        item["spu_id"]
        for item in _get(
            api_client,
            readonly_key,
            shop_pk=shop_pk,
            w_start=DAY,
            w_end=DAY,
            sort="paidPriceMedian",
            order="desc",
        )["items"]
    ]
    assert paid_desc == [
        "TEST_PRICE_SPU_SORT_PRICEY",
        "TEST_PRICE_SPU_SORT_CHEAP",
        "TEST_PRICE_SPU_SORT_NULL",
    ]


def test_price_stats_match_focused_and_exact_spu_scope(
    api_client, readonly_key, db_engine
):
    """同一 scope kernel：scope=focused、spu_ids 与整店读取的 priceStats 逐字段一致。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_SCOPE")
        spu_a = _seed_spu(sess, shop, "TEST_PRICE_SPU_SCOPE_A")
        spu_b = _seed_spu(sess, shop, "TEST_PRICE_SPU_SCOPE_B")
        _seed_manual_cost(sess, spu_a, "10")
        for spu, qty, original, paid in (
            (spu_a, "1", "20", "18"),
            (spu_b, "9", "50", "45"),
        ):
            order = _seed_order(
                sess,
                shop,
                order_id=f"TEST_PRICE_SCOPE_ORDER_{spu.spu_id}",
                status=PAID_STATUS,
            )
            line = _seed_line(
                sess, order, spu, line_ext=f"TEST_PRICE_SCOPE_LINE_{spu.spu_id}", qty=qty
            )
            _seed_observation(
                sess,
                shop=shop,
                order=order,
                line_ext=line.external_line_id,
                spu=spu,
                qty=qty,
                original=original,
                paid=paid,
            )
        sess.add(FocusedSpu(shop_pk=shop.id, spu_id=spu_a.spu_id, active=True))
        _commit(sess)
        shop_pk = shop.id

    whole = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)
    focused = _get(
        api_client,
        readonly_key,
        shop_pk=shop_pk,
        scope="focused",
        w_start=DAY,
        w_end=DAY,
    )
    exact = _get(
        api_client,
        readonly_key,
        shop_pk=shop_pk,
        spu_ids="TEST_PRICE_SPU_SCOPE_A",
        w_start=DAY,
        w_end=DAY,
    )

    assert {item["spu_id"] for item in focused["items"]} == {"TEST_PRICE_SPU_SCOPE_A"}
    assert {item["spu_id"] for item in exact["items"]} == {"TEST_PRICE_SPU_SCOPE_A"}
    whole_a = _items(whole)["TEST_PRICE_SPU_SCOPE_A"]
    assert focused["items"][0]["priceStats"] == whole_a["priceStats"]
    assert exact["items"][0]["priceStats"] == whole_a["priceStats"]
    assert focused["totals"]["priceStats"] == exact["totals"]["priceStats"]
    # focused totals 只聚合已应用 SPU 范围，不复制整店 totals
    assert focused["totals"]["priceStats"]["paid"]["eligibleQuantity"] == 1
    assert whole["totals"]["priceStats"]["paid"]["eligibleQuantity"] == 10


# ─── 7. 加固：capability 探测与 NULL 价格 ─────────────────────────


def _observed_null_price_fixture(db_engine) -> tuple[int, int]:
    """已付款行：original 有价、paid 状态 OBSERVED 但单价 NULL。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_NULLPRICE")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_NULLPRICE")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_NULLPRICE_ORDER", status=PAID_STATUS
        )
        line = _seed_line(
            sess, order, spu, line_ext="TEST_PRICE_NULLPRICE_LINE", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="10",
            paid=None,
            original_status="OBSERVED",
            paid_status="OBSERVED",
        )
        _commit(sess)
        return shop.id, spu.id


def test_read_price_stats_null_native_price_is_invalid_not_observed(db_engine):
    """domain 层：OBSERVED + NULL native price 记 invalid，不抛 ValueError。"""
    shop_pk, spu_pk = _observed_null_price_fixture(db_engine)
    with Session(db_engine) as sess:
        overview = read_price_stats(
            sess,
            basis=PriceStatsBasis(
                calculated_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
                fx=FxBasis(
                    snapshot_id=901,
                    usd_cny=USD_CNY,
                    cny_usd=Decimal(1) / USD_CNY,
                    usd_vnd=USD_VND,
                    as_of=datetime(2026, 10, 5, 11, 59, tzinfo=UTC),
                ),
                unit_costs_cny={},
                default_k1_cny=Decimal(40),
            ),
            request=PriceStatsRequest(
                shop_pk=shop_pk,
                selected_spu_pks=(spu_pk,),
                window_start_utc=None,
                window_end_exclusive_utc=None,
            ),
        )

    assert overview is not None
    stats = overview.by_spu[spu_pk]
    assert stats.paid.observed_quantity == 0
    assert stats.paid.invalid_quantity == 2
    assert stats.paid.invalid_line_count == 1
    assert stats.paid.mean_cny is None
    assert stats.paid.median_cny is None
    assert stats.paid.status is PriceCoverageStatus.PARTIAL
    # 同一 eligible 行的 original 字段仍是观察值：分类是 per-field，不是 per-line
    assert stats.original_sale.observed_quantity == 2
    assert stats.original_sale.mean_cny == Decimal(10)


def test_price_stats_observed_null_native_price_returns_200(
    api_client, readonly_key, db_engine
):
    """接口层：OBSERVED + NULL 单价不得抛未捕获 ValueError（整页 500）。"""
    shop_pk = _observed_null_price_fixture(db_engine)[0]

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)

    item = _items(body)["TEST_PRICE_SPU_NULLPRICE"]
    paid = _metric(item, "paid")
    assert paid["mean"] is None
    assert paid["median"] is None
    assert paid["observedQuantity"] == 0
    assert paid["invalidQuantity"] == 2
    assert paid["invalidLineCount"] == 1
    assert paid["eligibleQuantity"] == 2
    assert paid["status"] == "partial"
    assert _metric(item, "originalSale")["mean"] == "10.0000"
    assert item["priceCoverage"]["eligibleQuantity"] == 2


def test_price_stats_fields_omitted_when_observation_table_missing(
    api_client, readonly_key, db_engine, monkeypatch
):
    """0054 未应用（capability 探测为假）→ 200 且省略价格字段，不是整页 500。"""
    with Session(db_engine) as sess:
        shop = _seed_shop(sess, "TEST_PRICE_SHOP_NOTABLE")
        spu = _seed_spu(sess, shop, "TEST_PRICE_SPU_NOTABLE")
        _seed_manual_cost(sess, spu, "10")
        order = _seed_order(
            sess, shop, order_id="TEST_PRICE_NOTABLE_ORDER", status=PAID_STATUS
        )
        line = _seed_line(
            sess, order, spu, line_ext="TEST_PRICE_NOTABLE_LINE", qty="2"
        )
        _seed_observation(
            sess,
            shop=shop,
            order=order,
            line_ext=line.external_line_id,
            spu=spu,
            qty="2",
            original="20",
            paid="18",
        )
        _commit(sess)
        shop_pk = shop.id

    # 注入「观察表不存在」：探测语句走 read_price_stats 的同一分支。
    monkeypatch.setattr(
        _price_stats,
        "_SQL_PRICE_OBSERVATION_TABLE_EXISTS",
        text(
            "SELECT to_regclass("
            "'commerce.sales_order_line_price_observations_absent')"
            " IS NOT NULL"
        ),
    )

    body = _get(api_client, readonly_key, shop_pk=shop_pk, w_start=DAY, w_end=DAY)

    # 利润部分照常返回（价格模块缺失不得影响同一页面的利润字段）
    item = _items(body)["TEST_PRICE_SPU_NOTABLE"]
    assert "priceStats" not in item
    assert "priceCoverage" not in item
    assert "net_profit" in item
    assert "priceStats" not in body["totals"]
    assert "priceCoverage" not in body["totals"]
    # 没有价格快照时不得声明一个不存在的 CNY 基准/排序回传
    assert "priceFx" not in body["meta"]
    assert "priceCost" not in body["meta"]
    assert "priceSort" not in body["meta"]
