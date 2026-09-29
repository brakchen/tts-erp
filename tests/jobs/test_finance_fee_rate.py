"""Tests for jobs.finance_fee_rate — 店铺级平台抽成费率日快照（24h 任务）。

Contract under test (feature/shop-fee-rate)
-------------------------------------------
* 费率 = ``Σ|FEE| / Σ line_gmv``，按**订单**聚合后按店铺汇总。
  ``line_gmv`` = ``sales_order_lines.quantity × unit_price`` = 客户实付（折扣后）。
* **分母绝不能用 ``GROSS_SALES``** —— 那是折扣前挂牌价（实测是 line_gmv 的
  169%），用错会得出 ~12.5% 而不是正确的 ~21%。本文件用
  ``test_gross_sales_component_does_not_affect_rate`` 钉死这一点。
* 分子用 ``FEE``（平台总扣除），不是 ``PLATFORM_COMMISSION``（抽佣分项）。
* 窗口按 ``coalesce(transaction_time, synced_at)`` 裁剪；窗口外不计入。
* 门槛：``eligible_order_count >= MIN_ELIGIBLE_ORDER_COUNT`` 且
  ``coverage_ratio >= MIN_COVERAGE_RATIO``；不达标**不写行**（读取侧回退基线）。
* 一笔订单多笔结算交易时，line_gmv 只计一次（不得按交易重复计分母）。
* 幂等 + 日快照：同日重跑 upsert 同一行；不同日新增一行。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models import (
    ChannelAccount,
    Credentials,
    SalesOrder,
    SalesOrderLine,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
    ShopFeeRateEstimate,
)
from tts_erp_v2.jobs.finance_fee_rate import (
    LOOKBACK_DAYS,
    MIN_COVERAGE_RATIO,
    MIN_ELIGIBLE_ORDER_COUNT,
    compute_shop_fee_rates,
)

pytestmark = [pytest.mark.domain_finance, pytest.mark.layer_integration]

_NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
_SEQ = [0]


def _make_shop(session, shop_id: str) -> ChannelAccount:
    cred = Credentials(
        provider="tiktok",
        external_account_id=shop_id,
        ciphertext=b"\x00" * 32,
    )
    session.add(cred)
    session.flush()
    acct = ChannelAccount(
        platform="tiktok",
        shop_id=shop_id,
        credential_id=cred.id,
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _make_settled_order(
    session,
    *,
    shop: ChannelAccount,
    txn_time: datetime,
    quantity: int = 1,
    unit_price: str = "1000",
    fee: str | None = None,
    fee_currency: str = "VND",
    gross_sales: str | None = None,
    extra_txns: int = 0,
) -> int:
    """造一笔已结算订单，返回 order_pk。

    ``line_gmv`` = ``quantity × unit_price``，即费率的**分母基准**。
    ``fee=None`` 模拟历史数据缺口（订单缺 FEE 分项）→ 进分母不进分子。
    ``gross_sales`` 仅用于「GROSS_SALES 不影响费率」的反向断言。
    ``extra_txns`` >0 时给同一订单再加几笔结算交易（验证 line_gmv 不重复计）。
    """
    _SEQ[0] += 1
    seq = _SEQ[0]
    order = SalesOrder(
        shop_pk=shop.id,
        order_id=f"TEST_FEERATE_O{seq}",
        status="COMPLETED",
        currency="VND",
    )
    session.add(order)
    session.flush()
    session.add(
        SalesOrderLine(
            order_pk=order.id,
            external_line_id=f"TEST_FEERATE_L{seq}",
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
            currency="VND",
        )
    )
    session.flush()

    for n in range(1 + extra_txns):
        stmt = SettlementStatement(external_statement_id=f"TEST_FEERATE_S{seq}_{n}")
        session.add(stmt)
        session.flush()
        txn = SettlementTransaction(
            settlement_statement_id=stmt.id,
            external_transaction_id=f"TEST_FEERATE_T{seq}_{n}",
            order_pk=order.id,
            transaction_time=txn_time,
        )
        session.add(txn)
        session.flush()
        if gross_sales is not None:
            session.add(
                SettlementComponent(
                    transaction_id=txn.id,
                    component_code="GROSS_SALES",
                    amount=Decimal(gross_sales),
                    currency="VND",
                )
            )
        # fee 只挂在第一笔交易上（真实场景中 FEE 是订单级的汇总扣款）
        if fee is not None and n == 0:
            session.add(
                SettlementComponent(
                    transaction_id=txn.id,
                    component_code="FEE",
                    amount=Decimal(fee),
                    currency=fee_currency,
                )
            )
    session.flush()
    return order.id


def _bulk(
    session,
    shop: ChannelAccount,
    *,
    count: int,
    txn_time: datetime,
    quantity: int = 1,
    unit_price: str = "1000",
    fee: str | None = None,
) -> None:
    for _ in range(count):
        _make_settled_order(
            session,
            shop=shop,
            txn_time=txn_time,
            quantity=quantity,
            unit_price=unit_price,
            fee=fee,
        )


def _rates(session) -> dict[int, ShopFeeRateEstimate]:
    """只取本测试造的店铺行。

    ``compute_shop_fee_rates`` 是全库聚合（共享测试库里有真实店铺的真实结算
    数据），断言必须收窄到自己的样本。
    """
    rows = (
        session.execute(
            select(ShopFeeRateEstimate)
            .join(
                ChannelAccount,
                ChannelAccount.id == ShopFeeRateEstimate.shop_pk,
            )
            .where(ChannelAccount.shop_id.like("TEST_FEERATE%"))
        )
        .scalars()
        .all()
    )
    return {r.shop_pk: r for r in rows}


def test_gmv_weighted_rate_per_shop(db_session) -> None:
    shop_a = _make_shop(db_session, "TEST_FEERATE_A")
    shop_b = _make_shop(db_session, "TEST_FEERATE_B")
    recent = _NOW - timedelta(days=5)
    # A: 49 单 line_gmv=1000/fee=-250 + 1 单 line_gmv=1000/fee=-1000
    #    → (49*250+1000)/(50*1000) = 0.265（验证 GMV 加权，简单平均会是 0.625）
    _bulk(db_session, shop_a, count=49, txn_time=recent, fee="-250")
    _bulk(db_session, shop_a, count=1, txn_time=recent, fee="-1000")
    # B: 50 单 line_gmv=1000/fee=-380 → 0.380
    _bulk(db_session, shop_b, count=50, txn_time=recent, fee="-380")

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert result["calculated_on"] == _NOW.date().isoformat()
    assert result["lookback_days"] == LOOKBACK_DAYS
    rates = _rates(db_session)
    assert rates[shop_a.id].fee_rate == Decimal("0.265")
    assert rates[shop_a.id].eligible_order_count == 50
    assert rates[shop_a.id].line_gmv_covered == Decimal(50000)
    assert rates[shop_a.id].line_gmv_total == Decimal(50000)
    assert rates[shop_a.id].coverage_ratio == Decimal(1)
    assert rates[shop_a.id].total_fee == Decimal(13250)
    assert rates[shop_a.id].currency == "VND"
    assert rates[shop_b.id].fee_rate == Decimal("0.380")


def test_gross_sales_component_does_not_affect_rate(db_session) -> None:
    """分母必须是 line_gmv，不是结算单的 GROSS_SALES。

    GROSS_SALES 是折扣前挂牌价（真实数据实测是 line_gmv 的 169%），
    若误用它当分母会系统性低估费率。这里给同一批订单挂上 2 倍于 line_gmv
    的 GROSS_SALES，费率必须仍是 0.30 而不是 0.15。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_GS")
    recent = _NOW - timedelta(days=3)
    for _ in range(MIN_ELIGIBLE_ORDER_COUNT):
        _make_settled_order(
            db_session,
            shop=shop,
            txn_time=recent,
            unit_price="1000",
            fee="-300",
            gross_sales="2000",  # 2× line_gmv
        )

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.line_gmv_total == Decimal(50000)
    assert row.fee_rate == Decimal("0.300")  # 15000/50000，不是 15000/100000


def test_fee_component_is_used_not_platform_commission(db_session) -> None:
    """分子取 ``FEE``（总扣除），不是 ``PLATFORM_COMMISSION``（抽佣分项）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_FEEONLY")
    recent = _NOW - timedelta(days=3)
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        txn_time=recent,
        fee="-300",
    )
    # 额外挂一个抽佣分项（值更小），确认它不参与分子。
    first_txn = db_session.execute(
        select(SettlementTransaction)
        .join(SalesOrder, SalesOrder.id == SettlementTransaction.order_pk)
        .where(SalesOrder.shop_pk == shop.id)
        .limit(1)
    ).scalar_one()
    db_session.add(
        SettlementComponent(
            transaction_id=first_txn.id,
            component_code="PLATFORM_COMMISSION",
            amount=Decimal(-150),
            currency="VND",
        )
    )
    db_session.flush()

    compute_shop_fee_rates(db_session, now=_NOW)

    assert _rates(db_session)[shop.id].fee_rate == Decimal("0.300")


def test_multi_transaction_order_counts_line_gmv_once(db_session) -> None:
    """一笔订单多笔结算交易时，line_gmv 只能计一次（否则分母被放大、费率被低估）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_MULTITXN")
    recent = _NOW - timedelta(days=1)
    for _ in range(MIN_ELIGIBLE_ORDER_COUNT):
        _make_settled_order(
            db_session,
            shop=shop,
            txn_time=recent,
            unit_price="1000",
            fee="-300",
            extra_txns=2,  # 每单共 3 笔结算交易
        )

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.eligible_order_count == MIN_ELIGIBLE_ORDER_COUNT
    assert row.line_gmv_total == Decimal(50000)  # 不是 150000
    assert row.fee_rate == Decimal("0.300")


def test_insufficient_sample_is_skipped(db_session) -> None:
    shop = _make_shop(db_session, "TEST_FEERATE_SMALL")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT - 1,
        txn_time=_NOW - timedelta(days=1),
        fee="-300",
    )

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("insufficient_sample", 0) >= 1


def test_insufficient_coverage_is_skipped(db_session) -> None:
    """订单缺 ``FEE`` 分项 → 覆盖率不达标 → 不写行。

    若把缺 FEE 的订单当 ``fee=0`` 计入分母，会得出被人为拉低的费率，
    所以必须跳过而不是产出一个偏低的估算。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_GAP")
    recent = _NOW - timedelta(days=2)
    # 50 单有 FEE（满足样本量门槛）+ 20 单没有 → 覆盖率 50/70 ≈ 0.714 < 0.80
    _bulk(
        db_session, shop, count=50, txn_time=recent, fee="-300"
    )
    _bulk(db_session, shop, count=20, txn_time=recent, fee=None)

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("insufficient_coverage", 0) >= 1


def test_coverage_just_at_threshold_passes(db_session) -> None:
    """覆盖率恰好达到门槛时应当通过（边界取 ``>=``）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_EDGE")
    recent = _NOW - timedelta(days=2)
    # 50 有 FEE + 12 无 = 50/62 ≈ 0.806 ≥ 0.80 且样本达标
    _bulk(db_session, shop, count=50, txn_time=recent, fee="-300")
    _bulk(db_session, shop, count=12, txn_time=recent, fee=None)

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.eligible_order_count == 50
    assert row.line_gmv_total == Decimal(62000)
    assert row.line_gmv_covered == Decimal(50000)
    assert row.coverage_ratio >= MIN_COVERAGE_RATIO
    assert row.fee_rate == Decimal("0.300")


def test_mixed_fee_currency_is_skipped(db_session) -> None:
    """同一店铺混用多种 FEE 币种 → 不可相加，跳过。"""
    shop = _make_shop(db_session, "TEST_FEERATE_FX")
    recent = _NOW - timedelta(days=2)
    _bulk(db_session, shop, count=50, txn_time=recent, fee="-300")
    _bulk(
        db_session,
        shop,
        count=1,
        txn_time=recent,
        fee="-300",
    )
    # 把一单的 FEE 币种改成 USD，制造混币种
    txn = db_session.execute(
        select(SettlementTransaction)
        .join(SalesOrder, SalesOrder.id == SettlementTransaction.order_pk)
        .where(SalesOrder.shop_pk == shop.id)
        .limit(1)
    ).scalar_one()
    comp = db_session.execute(
        select(SettlementComponent).where(
            SettlementComponent.transaction_id == txn.id,
            SettlementComponent.component_code == "FEE",
        )
    ).scalar_one()
    comp.currency = "USD"
    db_session.flush()

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("mixed_fee_currency", 0) >= 1


def test_zero_line_gmv_is_skipped(db_session) -> None:
    """line_gmv = 0 的订单进不了聚合（SQL 侧 ``line_gmv > 0``），店铺直接不出行。"""
    shop = _make_shop(db_session, "TEST_FEERATE_ZERO")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        txn_time=_NOW - timedelta(days=1),
        unit_price="0",
        fee="0",
    )

    result = compute_shop_fee_rates(db_session, now=_NOW)

    # line_gmv = 0 的订单被 SQL 侧 ``WHERE g.line_gmv > 0`` 滤掉，该店连
    # 聚合结果行都不存在（既不是 upsert 也不是 skip）。
    assert shop.id not in _rates(db_session)
    assert result["shops_upserted"] >= 0  # 全库聚合，只断言本店无行


def test_out_of_window_orders_ignored(db_session) -> None:
    shop = _make_shop(db_session, "TEST_FEERATE_OLD")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        txn_time=_NOW - timedelta(days=LOOKBACK_DAYS + 1),
        fee="-300",
    )

    compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)


def test_upsert_idempotent_same_day_and_new_row_next_day(db_session) -> None:
    """同日重跑 upsert 同一行；跨日新增一份快照（保留费率变化历史）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_IDEM")
    recent = _NOW - timedelta(days=2)
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        txn_time=recent,
        fee="-310",
    )

    compute_shop_fee_rates(db_session, now=_NOW)
    compute_shop_fee_rates(db_session, now=_NOW + timedelta(hours=6))
    same_day = (
        db_session.execute(
            select(ShopFeeRateEstimate).where(
                ShopFeeRateEstimate.shop_pk == shop.id
            )
        )
        .scalars()
        .all()
    )
    assert len(same_day) == 1
    assert same_day[0].fee_rate == Decimal("0.310")

    next_day = _NOW + timedelta(days=1)
    compute_shop_fee_rates(db_session, now=next_day)
    rows = (
        db_session.execute(
            select(ShopFeeRateEstimate)
            .where(ShopFeeRateEstimate.shop_pk == shop.id)
            .order_by(ShopFeeRateEstimate.calculated_on)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert [r.calculated_on for r in rows] == [
        date(2026, 9, 29),
        date(2026, 9, 30),
    ]
