"""analytics.shop_fee_rate — 店铺级平台抽成费率重算（每 24h，系统级 job）。

背景
----
spu-roi 估算未结算订单净额用费率 r̂
（``unsettled_sales × (1−r̂) × (1−退款率)``，见
``analytics/spu_profitability/_formula_v10.py``）。r̂ 原本是全局硬编码
基线 0.308（dashboard D10 口径），但平台抽成与店铺强相关，本任务按店铺
实测并写入 ``reporting.shop_fee_rate_estimates``（每日一份快照）。

口径
----
按**订单**聚合（一笔订单可能有多笔结算交易，按交易求和会把 line_gmv
重复计）。::

    fee_rate       = Σ|FEE| / Σ line_gmv          （带 FEE 的已结算订单）
    coverage_ratio = Σ line_gmv(带 FEE) / Σ line_gmv(窗口内全部已结算订单)

* ``line_gmv`` = ``sales_order_lines.quantity × unit_price`` = **客户实付
  （折扣后）**。**分母不能用 ``GROSS_SALES``** —— 那是折扣前挂牌价，
  实测是 line_gmv 的 169%（= AFTER_SELLER_DISCOUNTS_SUBTOTAL +
  SELLER_DISCOUNT），用错会得出 12.5% 而不是正确的 21.2%。
  生产库实测：Σ line_gmv 与结算单 ``CUSTOMER_PAYMENT`` 只差 0.24%。
* 分子 ``FEE`` = 交易级平台总扣除（``fee_amount``）。逐单恒等式已验证
  （1204 笔，中位残差 0.000%，91.3% 在 ±5% 内）::

      SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND

  即 FEE 已含运费类，**不可再叠加运费分项**（会重复扣）；也不是
  ``PLATFORM_COMMISSION``（只是抽佣分项，约占 FEE 的一半）。
* ``ABS`` 防御符号方向差异（上游扣款行为负值）。
* 币种：line_gmv 用店铺本币；若某店混用多种 FEE 币种则跳过（防跨币种相加）。

为何记录 ``coverage_ratio`` 而不是直接算（2026-09-29 方案评审）
-------------------------------------------------------------
历史结算数据存在**只有 ``SETTLEMENT`` 没有 ``FEE`` 分项**的行（v3 时期
只落 settlement_amount 的历史遗留，见 ``db/models/finance.py`` 的
audit 注记）。这类交易若被当作 ``fee = 0`` 计入分母，会把费率系统性拉低。
因此本任务把「有 FEE 的 GMV」与「窗口内全部已结算 GMV」分开统计，覆盖率
不达 ``MIN_COVERAGE_RATIO`` 的店铺直接跳过（不写快照，读取侧回退基线）。

门槛与守卫
----------
* 窗口：近 ``LOOKBACK_DAYS`` 天（默认 180），按
  ``coalesce(transaction_time, synced_at)`` 裁剪。
* 最小样本 ``MIN_ELIGIBLE_ORDER_COUNT`` = 50 单；单量太少时加权平均对
  个别大单过度敏感。
* 最低覆盖率 ``MIN_COVERAGE_RATIO`` = 0.80。
* 费率越界 ``[0, 0.95]`` 跳过（DB 层另有 CHECK 兜底）。
* 不满足门槛的店铺**不写行** → 保留其历史快照，读取侧按过期逻辑回退基线。
* 幂等：``ON CONFLICT (shop_pk, calculated_on) DO UPDATE``，同日可安全重跑。

调度：``sync_worker/scheduler.py`` JOBS 注册，``interval_seconds=86400``，
``is_tiktok=False``，entrypoint = :func:`run_scheduled`。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.jobs.runner import run_job

log = logging.getLogger("tts_erp_v2.jobs.finance_fee_rate")

JOB_NAME = "analytics.shop_fee_rate"

#: 口径版本；改动聚合方式 / 组件集合时递增，便于对新旧快照分群对比。
CALCULATION_VERSION = "fee-v1"

#: 统计窗口：近 N 天已结算订单。
LOOKBACK_DAYS = 180

#: 最小有效订单数（单量太少时加权平均被个别大单主导）。
MIN_ELIGIBLE_ORDER_COUNT = 50

#: 最低 GMV 覆盖率；低于此值说明历史交易缺 FEE 分项，费率不可信。
MIN_COVERAGE_RATIO = Decimal("0.80")

#: 费率合法区间；越界样本跳过（DB CHECK 约束同步兜底）。
RATE_MIN = Decimal("0")
RATE_MAX = Decimal("0.95")

_RATE_Q = Decimal("0.000001")
_AMOUNT_Q = Decimal("0.0001")

# 订单级聚合。一笔订单可能对应多笔结算交易，所以 line_gmv 必须按订单
# 去重后再汇总（按交易求和会重复计分母）。
_SQL_SHOP_FEE_RATE = text(
    """
    WITH settled_orders AS (
        SELECT DISTINCT st.order_pk AS order_pk
        FROM finance.settlement_transactions st
        JOIN commerce.sales_orders so ON so.id = st.order_pk
        WHERE st.order_pk IS NOT NULL
          AND COALESCE(st.transaction_time, st.synced_at) >= :window_start
          AND COALESCE(st.transaction_time, st.synced_at) <  :window_end
    ),
    order_fee AS (
        SELECT st.order_pk AS order_pk,
               SUM(ABS(sc.amount)) AS total_fee,
               MAX(sc.currency) AS fee_currency
        FROM finance.settlement_transactions st
        JOIN finance.settlement_components sc ON sc.transaction_id = st.id
        JOIN settled_orders so ON so.order_pk = st.order_pk
        WHERE sc.component_code = 'FEE'
        GROUP BY st.order_pk
    ),
    order_gmv AS (
        SELECT sl.order_pk AS order_pk,
               MAX(so.shop_pk) AS shop_pk,
               SUM(sl.quantity * sl.unit_price) AS line_gmv
        FROM commerce.sales_order_lines sl
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        JOIN settled_orders so2 ON so2.order_pk = sl.order_pk
        GROUP BY sl.order_pk
    )
    SELECT g.shop_pk,
           COALESCE(SUM(g.line_gmv), 0) AS line_gmv_total,
           COALESCE(
               SUM(g.line_gmv) FILTER (WHERE f.order_pk IS NOT NULL), 0
           ) AS line_gmv_covered,
           COALESCE(
               SUM(f.total_fee) FILTER (WHERE f.order_pk IS NOT NULL), 0
           ) AS total_fee,
           COUNT(*) FILTER (WHERE f.order_pk IS NOT NULL)
               AS eligible_order_count,
           COUNT(DISTINCT f.fee_currency) FILTER (
               WHERE f.order_pk IS NOT NULL
           ) AS fee_currency_count,
           MAX(f.fee_currency) FILTER (WHERE f.order_pk IS NOT NULL)
               AS currency
    FROM order_gmv g
    LEFT JOIN order_fee f ON f.order_pk = g.order_pk
    WHERE g.line_gmv > 0
    GROUP BY g.shop_pk
    """
)

_SQL_UPSERT = text(
    """
    INSERT INTO reporting.shop_fee_rate_estimates
        (shop_pk, calculated_on, lookback_days, fee_rate, eligible_order_count,
         line_gmv_covered, line_gmv_total, coverage_ratio, total_fee,
         currency, calculation_version, calculated_at)
    VALUES
        (:shop_pk, :calculated_on, :lookback_days, :fee_rate,
         :eligible_order_count, :line_gmv_covered, :line_gmv_total,
         :coverage_ratio, :total_fee, :currency, :calculation_version, now())
    ON CONFLICT (shop_pk, calculated_on) DO UPDATE SET
        lookback_days        = EXCLUDED.lookback_days,
        fee_rate             = EXCLUDED.fee_rate,
        eligible_order_count = EXCLUDED.eligible_order_count,
        line_gmv_covered     = EXCLUDED.line_gmv_covered,
        line_gmv_total       = EXCLUDED.line_gmv_total,
        coverage_ratio       = EXCLUDED.coverage_ratio,
        total_fee            = EXCLUDED.total_fee,
        currency             = EXCLUDED.currency,
        calculation_version  = EXCLUDED.calculation_version,
        calculated_at        = EXCLUDED.calculated_at
    """
)


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def compute_shop_fee_rates(
    session: Session,
    *,
    now: datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, Any]:
    """重算全部店铺的费率快照并 upsert。返回计数摘要（供测试与日志）。

    不满足样本量 / 覆盖率 / 费率区间门槛的店铺**不写行**，其跳过原因计入
    ``skipped_reasons``（按店计数）以便运维定位是数据缺口还是样本不足。
    """
    now = now or datetime.now(UTC)
    window_end = now
    window_start = now - timedelta(days=lookback_days)
    calculated_on: date = now.date()

    rows = (
        session.execute(
            _SQL_SHOP_FEE_RATE,
            {"window_start": window_start, "window_end": window_end},
        )
        .mappings()
        .all()
    )

    upserted = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    def _skip(shop_pk: int, reason: str, **fields: Any) -> None:
        nonlocal skipped
        skipped += 1
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        log.warning(
            "[%s] shop_pk=%s skipped (%s) %s", JOB_NAME, shop_pk, reason, fields
        )

    for row in rows:
        shop_pk = int(row["shop_pk"])
        eligible_count = int(row["eligible_order_count"] or 0)
        covered = Decimal(row["line_gmv_covered"] or 0)
        gmv_total = Decimal(row["line_gmv_total"] or 0)
        fee_total = Decimal(row["total_fee"] or 0)
        currency = row["currency"]
        currency_count = int(row["fee_currency_count"] or 0)

        if currency is None or covered <= 0:
            _skip(shop_pk, "no_eligible_orders")
            continue
        if currency_count > 1:
            # line_gmv 是本币，但 FEE 混用多币种 → 不可相加（防跨币种）。
            _skip(shop_pk, "mixed_fee_currency", currency_count=currency_count)
            continue
        if eligible_count < MIN_ELIGIBLE_ORDER_COUNT:
            _skip(
                shop_pk,
                "insufficient_sample",
                eligible_order_count=eligible_count,
                required=MIN_ELIGIBLE_ORDER_COUNT,
            )
            continue

        coverage_ratio = (
            _q(covered / gmv_total, _RATE_Q) if gmv_total > 0 else Decimal("0")
        )
        if coverage_ratio < MIN_COVERAGE_RATIO:
            _skip(
                shop_pk,
                "insufficient_coverage",
                coverage_ratio=str(coverage_ratio),
                required=str(MIN_COVERAGE_RATIO),
            )
            continue

        rate = _q(fee_total / covered, _RATE_Q)
        if not (RATE_MIN <= rate <= RATE_MAX):
            _skip(
                shop_pk,
                "rate_out_of_range",
                rate=str(rate),
                bounds=f"[{RATE_MIN}, {RATE_MAX}]",
            )
            continue

        session.execute(
            _SQL_UPSERT,
            {
                "shop_pk": shop_pk,
                "calculated_on": calculated_on,
                "lookback_days": lookback_days,
                "fee_rate": rate,
                "eligible_order_count": eligible_count,
                "line_gmv_covered": _q(covered, _AMOUNT_Q),
                "line_gmv_total": _q(gmv_total, _AMOUNT_Q),
                "coverage_ratio": coverage_ratio,
                "total_fee": _q(fee_total, _AMOUNT_Q),
                "currency": currency,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        upserted += 1

    return {
        "shops_seen": len(rows),
        "shops_upserted": upserted,
        "shops_skipped": skipped,
        "skipped_reasons": skipped_reasons,
        "calculated_on": calculated_on.isoformat(),
        "lookback_days": lookback_days,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def run_scheduled(session: Session) -> dict[str, Any]:
    """Scheduler entrypoint（system job，is_tiktok=False）。"""
    with run_job(session, job_name=JOB_NAME) as job:
        result = compute_shop_fee_rates(session)
        job.rows_total = result["shops_seen"]
        job.rows_inserted = result["shops_upserted"]
        job.extra = result
        return result
