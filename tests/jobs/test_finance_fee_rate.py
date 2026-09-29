"""Tests for jobs.finance_fee_rate — 店铺级平台抽成费率日快照（24h 任务）。

Contract under test (feature/shop-fee-rate)
-------------------------------------------
* 费率 = 交易级聚合后的 ``Σ|FEE| / Σ GROSS_SALES``（GMV 加权），店铺归因走
  ``settlement_transactions.order_pk → sales_orders.shop_pk``。
* **分子用 ``FEE``（交易级平台总扣除），不是 ``PLATFORM_COMMISSION``。**
* 窗口按 ``coalesce(transaction_time, synced_at)`` 裁剪；窗口外不计入。
* 门槛：``eligible_order_count >= MIN_ELIGIBLE_ORDER_COUNT`` 且
  ``coverage_ratio >= MIN_COVERAGE_RATIO``；不达标**不写行**（读取侧回退基线）。
  覆盖率分母 = 窗口内全部已结算 GROSS_SALES，分子 = 有 ``FEE`` 且币种一致的
  那部分 —— 用来暴露「只有 SETTLEMENT 没有 FEE 分项」的历史数据缺口。
* 幂等 + 日快照：同一天重跑 upsert 同一行；不同日新增一行。
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


def _make_settled_txn(
    session,
    *,
    shop: ChannelAccount,
    gross: str,
    txn_time: datetime,
    fee: str | None = None,
    gross_currency: str = "VND",
    fee_currency: str | None = None,
) -> None:
    """造一笔已结算交易。

    ``fee=None`` 模拟历史数据缺口 —— 该交易只有 ``GROSS_SALES`` 没有 ``FEE``
    分项（v3 时期只落 settlement_amount 的遗留），应计入覆盖率分母但不计入
    分子/费率样本。
    """
    _SEQ[0] += 1
    seq = _SEQ[0]
    order = SalesOrder(
        shop_pk=shop.id,
        order_id=f"TEST_FEERATE_O{seq}",
        status="COMPLETED",
        currency=gross_currency,
    )
    session.add(order)
    session.flush()
    stmt = SettlementStatement(external_statement_id=f"TEST_FEERATE_S{seq}")
    session.add(stmt)
    session.flush()
    txn = SettlementTransaction(
        settlement_statement_id=stmt.id,
        external_transaction_id=f"TEST_FEERATE_T{seq}",
        order_pk=order.id,
        transaction_time=txn_time,
    )
    session.add(txn)
    session.flush()
    session.add(
        SettlementComponent(
            transaction_id=txn.id,
            component_code="GROSS_SALES",
            amount=Decimal(gross),
            currency=gross_currency,
        )
    )
    if fee is not None:
        session.add(
            SettlementComponent(
                transaction_id=txn.id,
                component_code="FEE",
                amount=Decimal(fee),
                currency=fee_currency or gross_currency,
            )
        )
    session.flush()


def _bulk(
    session,
    shop: ChannelAccount,
    *,
    count: int,
    gross: str,
    fee: str | None,
    txn_time: datetime,
) -> None:
    for _ in range(count):
        _make_settled_txn(
            session, shop=shop, gross=gross, fee=fee, txn_time=txn_time
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
    # A: 49 单 1000/-250 + 1 单 1000/-1000 → (49*250+1000)/(50*1000) = 0.265
    # （验证 GMV 加权而非简单平均：简单平均会是 0.625）
    _bulk(db_session, shop_a, count=49, gross="1000", fee="-250", txn_time=recent)
    _bulk(db_session, shop_a, count=1, gross="1000", fee="-1000", txn_time=recent)
    # B: 50 单 1000/-380 → 0.380
    _bulk(db_session, shop_b, count=50, gross="1000", fee="-380", txn_time=recent)

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert result["calculated_on"] == _NOW.date().isoformat()
    assert result["lookback_days"] == LOOKBACK_DAYS
    rates = _rates(db_session)
    assert rates[shop_a.id].fee_rate == Decimal("0.265")
    assert rates[shop_a.id].eligible_order_count == 50
    assert rates[shop_a.id].gross_sales_covered == Decimal("50000")
    assert rates[shop_a.id].gross_sales_total == Decimal("50000")
    assert rates[shop_a.id].coverage_ratio == Decimal("1")
    assert rates[shop_a.id].total_fee == Decimal("13250")
    assert rates[shop_a.id].currency == "VND"
    assert rates[shop_b.id].fee_rate == Decimal("0.380")


def test_fee_component_is_used_not_platform_commission(db_session) -> None:
    """分子必须取 ``FEE``（总扣除），不是 ``PLATFORM_COMMISSION``（抽佣分项）。

    真实数据里 ``PLATFORM_COMMISSION`` 只有 ``FEE`` 的一部分（联盟/运费等
    在 FEE 内），若误用会把费率显著低估。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_FEEONLY")
    recent = _NOW - timedelta(days=3)
    _bulk(db_session, shop, count=50, gross="1000", fee="-300", txn_time=recent)
    # 额外挂一个抽佣分项（值更小），确认它不参与分子。
    first = db_session.execute(
        select(SettlementTransaction)
        .join(SalesOrder, SalesOrder.id == SettlementTransaction.order_pk)
        .where(SalesOrder.shop_pk == shop.id)
        .limit(1)
    ).scalar_one()
    db_session.add(
        SettlementComponent(
            transaction_id=first.id,
            component_code="PLATFORM_COMMISSION",
            amount=Decimal("-150"),
            currency="VND",
        )
    )
    db_session.flush()

    compute_shop_fee_rates(db_session, now=_NOW)

    # 仍是 300/1000 = 0.30，而不是 (300+150)/1000
    assert _rates(db_session)[shop.id].fee_rate == Decimal("0.300")


def test_insufficient_sample_is_skipped(db_session) -> None:
    shop = _make_shop(db_session, "TEST_FEERATE_SMALL")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT - 1,
        gross="1000",
        fee="-300",
        txn_time=_NOW - timedelta(days=1),
    )

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("insufficient_sample", 0) >= 1


def test_insufficient_coverage_is_skipped(db_session) -> None:
    """历史数据缺口：只有少数交易带 ``FEE`` 分项 → 覆盖率不达标 → 不写行。

    若把缺 ``FEE`` 的交易当 ``fee=0`` 计入分母，会得出被人为拉低的费率，
    所以这里必须跳过而不是产出一个偏低的估算。
    """
    shop = _make_shop(db_session, "TEST_FEERATE_GAP")
    recent = _NOW - timedelta(days=2)
    # 50 单有 FEE（满足样本量门槛）+ 20 单没有 → 覆盖率 50/70 ≈ 0.714 < 0.80
    _bulk(db_session, shop, count=50, gross="1000", fee="-300", txn_time=recent)
    _bulk(db_session, shop, count=20, gross="1000", fee=None, txn_time=recent)

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("insufficient_coverage", 0) >= 1


def test_coverage_just_at_threshold_passes(db_session) -> None:
    """覆盖率恰好达到门槛时应当通过（边界取 ``>=``）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_EDGE")
    recent = _NOW - timedelta(days=2)
    # 40 有 FEE + 10 无 = 覆盖率 40/50 = 0.80，但 eligible 只有 40 < 50 → 样本不足。
    # 所以造 50 有 FEE + 12 无 = 50/62 ≈ 0.806（>= 0.80）且样本达标。
    _bulk(db_session, shop, count=50, gross="1000", fee="-300", txn_time=recent)
    _bulk(db_session, shop, count=12, gross="1000", fee=None, txn_time=recent)

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    assert row.eligible_order_count == 50
    assert row.gross_sales_total == Decimal("62000")
    assert row.gross_sales_covered == Decimal("50000")
    assert row.coverage_ratio >= MIN_COVERAGE_RATIO
    assert row.fee_rate == Decimal("0.300")


def test_currency_mismatch_excluded_from_eligible(db_session) -> None:
    """``FEE`` 与 ``GROSS_SALES`` 币种不一致的交易不参与费率（防跨币种混加）。"""
    shop = _make_shop(db_session, "TEST_FEERATE_FX")
    recent = _NOW - timedelta(days=2)
    # 100 单币种一致 + 20 单 FEE 为 USD → 覆盖率 100/120 ≈ 0.833 ≥ 0.80（可通过）
    _bulk(db_session, shop, count=100, gross="1000", fee="-300", txn_time=recent)
    for _ in range(20):
        _make_settled_txn(
            db_session,
            shop=shop,
            gross="1000",
            fee="-999",
            txn_time=recent,
            gross_currency="VND",
            fee_currency="USD",
        )

    compute_shop_fee_rates(db_session, now=_NOW)

    row = _rates(db_session)[shop.id]
    # 币种不一致的 20 单进分母但不进分子/样本
    assert row.eligible_order_count == 100
    assert row.gross_sales_total == Decimal("120000")
    assert row.gross_sales_covered == Decimal("100000")
    assert row.total_fee == Decimal("30000")
    assert row.fee_rate == Decimal("0.300")


def test_zero_gross_is_skipped(db_session) -> None:
    shop = _make_shop(db_session, "TEST_FEERATE_ZERO")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        gross="0",
        fee="0",
        txn_time=_NOW - timedelta(days=1),
    )

    result = compute_shop_fee_rates(db_session, now=_NOW)

    assert shop.id not in _rates(db_session)
    assert result["skipped_reasons"].get("no_eligible_transactions", 0) >= 1


def test_out_of_window_transactions_ignored(db_session) -> None:
    shop = _make_shop(db_session, "TEST_FEERATE_OLD")
    _bulk(
        db_session,
        shop,
        count=MIN_ELIGIBLE_ORDER_COUNT,
        gross="1000",
        fee="-300",
        txn_time=_NOW - timedelta(days=LOOKBACK_DAYS + 1),
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
        gross="1000",
        fee="-310",
        txn_time=recent,
    )

    compute_shop_fee_rates(db_session, now=_NOW)
    compute_shop_fee_rates(db_session, now=_NOW + timedelta(hours=6))
    same_day = db_session.execute(
        select(ShopFeeRateEstimate).where(ShopFeeRateEstimate.shop_pk == shop.id)
    ).scalars().all()
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
