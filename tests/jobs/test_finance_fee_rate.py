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
* **无样本量门槛、无覆盖率门槛**（用户拍板 2026-09-29：只要有一单已结算就
  要算）。``kept_share`` 只记录并对外暴露，**不阻断**产出。
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
    MIN_ELIGIBLE_ORDER_COUNT,
    compute_shop_fee_rates,
)

pytestmark = [pytest.mark.domain_finance, pytest.mark.layer_integration]

_NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
_SEQ = [0]
#: 本测试文件内部用的批量样本量。与生产门槛无关 —— 生产已按用户拍板取消
#: 样本量门槛（``MIN_ELIGIBLE_ORDER_COUNT = 1``，有一单就算），但测试需要有
#: 足够多的订单才能验证 GMV 加权、line_gmv 去重等行为。
_BULK = 50


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
    refund: str | None = None,
    extra_txns: int = 0,
) -> int:
    """造一笔已结算订单，返回 order_pk。

    ``line_gmv`` = ``quantity × unit_price``，即费率的**分母基准**。
    ``fee=None`` 模拟历史数据缺口（订单缺 FEE 分项）→ 进分母不进分子。
    ``gross_sales`` 仅用于「GROSS_SALES 不影响费率」的反向断言。
    ``refund`` 挂 ``CUSTOMER_REFUND``（上游为负值）—— 带退款的订单必须被
    排除在费率样本之外（否则退款效应被 (1−退款率) 与 r̂ 算两遍）。
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
        if refund is not None and n == 0:
            session.add(
                SettlementComponent(
                    transaction_id=txn.id,
                    component_code="CUSTOMER_REFUND",
                    amount=Decimal(refund),
                    currency="VND",
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
    assert rates[shop_a.id].kept_order_count == 50
    assert rates[shop_a.id].kept_line_gmv == Decimal(50000)
    assert rates[shop_a.id].window_line_gmv == Decimal(50000)
    assert rates[shop_a.id].kept_share == Decimal(1)
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
    for _ in range(_BULK):
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
    assert row.window_line_gmv == Decimal(50000)
    assert row.fee_rate == Decimal("0.300")  # 15000/50000，不是 15000/100000


def test_fee_component_is_used_not_platform_commission(db_session) -> None:
    """分子取 ``FEE``（总扣除），不是 ``PLATFORM_COMMISSION``（抽佣分项）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_FEEONLY")
    recent = _NOW - timedelta(days=3)
    _bulk(
        db_session,
        shop,
        count=_BULK,
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
    for _ in range(_BULK):
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
    assert row.kept_order_count == _BULK
    assert row.window_line_gmv == Decimal(50000)  # 不是 150000
    assert row.fee_rate == Decimal("0.300")


def test_single_settled_order_is_enough(db_session) -> None:
    """用户拍板 2026-09-29：只要有一单已结算就要算 —— 不设样本量门槛。"""
    assert MIN_ELIGIBLE_ORDER_COUNT == 1
    shop = _make_shop(db_session, "TEST_FEERATE_ONE")
    _bulk(
        db_session,
        shop,
        count=1,
        txn_time=_NOW - timedelta(days=1),
        fee="-300",
    )

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.kept_order_count == 1
    assert row.fee_rate == Decimal("0.300")


def test_low_coverage_still_produces_row(db_session) -> None:
    """覆盖率不达任何门槛时**仍然产出**（门槛已被用户拍板取消）。

    覆盖率只作观测/审计字段（前端费率卡会显示），不阻断产出。
    历史数据缺 FEE 分项时，缺的那批订单既不进分子也不进分母。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_GAP")
    recent = _NOW - timedelta(days=2)
    # 10 单有 FEE + 90 单没有 → 覆盖率 10/100 = 0.10（远低于任何旧门槛）
    _bulk(db_session, shop, count=10, txn_time=recent, fee="-300")
    _bulk(db_session, shop, count=90, txn_time=recent, fee=None)

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert "insufficient_coverage" not in result["skipped_reasons"]
    row = _rates(db_session)[shop.id]
    assert row.kept_share == Decimal("0.1")
    assert row.kept_order_count == 10
    assert row.fee_rate == Decimal("0.300")


def test_kept_share_is_recorded(db_session) -> None:
    """覆盖率如实记录：分子=有 FEE 订单的 line_gmv，分母=全部已结算 line_gmv。"""
    shop = _make_shop(db_session, "TEST_FEERATE_EDGE")
    recent = _NOW - timedelta(days=2)
    _bulk(db_session, shop, count=50, txn_time=recent, fee="-300")
    _bulk(db_session, shop, count=12, txn_time=recent, fee=None)

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.kept_order_count == 50
    assert row.window_line_gmv == Decimal(62000)
    assert row.kept_line_gmv == Decimal(50000)
    assert row.kept_share == Decimal("0.806452")
    assert row.fee_rate == Decimal("0.300")


def test_refunded_orders_are_excluded_from_rate(db_session) -> None:
    """全额退款订单**不进**费率分子/分母。

    页面公式 ``line_gmv × (1−r̂) × (1−退款率)`` 已另有 (1−退款率) 扣一次
    退款；若 r̂ 的样本里再混入全额退款订单（其费率仅 ~3%），退款效应会被
    算两遍。生产反证：kept 口径预测误差 ±1%，混合口径高估 ~16%。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_REFUND")
    recent = _NOW - timedelta(days=2)
    # 50 单未退款：line_gmv=1000, fee=-300 → 费率 0.30
    _bulk(db_session, shop, count=50, txn_time=recent, fee="-300")
    # 50 单全额退款：line_gmv=1000, fee=-30（退款单费率仅 3%）
    for _ in range(50):
        _make_settled_order(
            db_session,
            shop=shop,
            txn_time=recent,
            unit_price="1000",
            fee="-30",
            refund="-1000",
        )

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    # 只算未退款的 50 单 → 0.30；若混入退款单会是 16500/100000 = 0.165
    assert row.kept_order_count == 50
    assert row.kept_line_gmv == Decimal(50000)
    assert row.window_line_gmv == Decimal(100000)
    assert row.kept_share == Decimal("0.5")
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
        count=_BULK,
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
        count=_BULK,
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
        count=_BULK,
        txn_time=recent,
        fee="-310",
    )

    compute_shop_fee_rates(db_session, now=_NOW)
    compute_shop_fee_rates(db_session, now=_NOW + timedelta(hours=6))
    same_day = (
        db_session.execute(
            select(ShopFeeRateEstimate).where(ShopFeeRateEstimate.shop_pk == shop.id)
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
