"""reporting.shop_fee_rate_estimates — 店铺级平台抽成费率日快照。

Revision ID: 0040_shop_fee_rate_estimates
Revises: 0039_tiktok_app_credentials
Create Date: 2026-09-29

背景
----
spu-roi 估算未结算订单抽成用的费率 r̂ 此前是全局硬编码基线 0.308
（``tts_erp_v2/analytics/spu_profitability/_implementation.py``
``FEE_RATE_BASELINE``）。实际抽成率与店铺强相关：本表承载每店铺按近 N 天
已结算订单实测的 GMV 加权费率，由 ``analytics.shop_fee_rate`` 任务
（24h）重算。

口径（与 dashboard D10 基线同定义，作用域收窄到单店）::

    fee_rate       = Σ|FEE| / Σ GROSS_SALES     （已结算交易，币种一致）
    coverage_ratio = Σ GROSS_SALES(有 FEE 的交易)
                   / Σ GROSS_SALES(窗口内全部已结算交易)

``FEE`` 是交易级平台总扣除（``fee_amount``，含交易抽佣 + 联盟 + 运费等），
**不是** ``PLATFORM_COMMISSION``（后者只是抽佣分项，会显著低估）。
覆盖率用于暴露历史交易缺 ``FEE`` 分项的数据缺口 —— 覆盖不足时该店不产出
可用快照。

每日一份快照（``uq_shop_fee_rate_est_shop_day``），保留费率变化历史以便
解释某周净利润波动；读取侧取每店最新一行，超过
``MAX_ESTIMATE_AGE_DAYS`` 视为过期并回退全局基线。无快照 = 回退基线。

详见 ``tech-doc/analytics/spu-real-roi-dashboard.md``（费率段）与
``tech-doc/external-api.md``（``meta.fee`` 契约）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0040_shop_fee_rate_estimates"
down_revision: str | None = "0039_tiktok_app_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shop_fee_rate_estimates",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("shop_pk", sa.BigInteger, nullable=False),
        sa.Column("calculated_on", sa.Date, nullable=False),
        sa.Column("lookback_days", sa.Integer, nullable=False),
        sa.Column("fee_rate", sa.Numeric(8, 6), nullable=False),
        sa.Column("eligible_order_count", sa.Integer, nullable=False),
        sa.Column("gross_sales_covered", sa.Numeric(20, 4), nullable=False),
        sa.Column("gross_sales_total", sa.Numeric(20, 4), nullable=False),
        sa.Column("coverage_ratio", sa.Numeric(8, 6), nullable=False),
        sa.Column("total_fee", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column(
            "calculation_version",
            sa.Text,
            server_default=sa.text("'fee-v1'"),
            nullable=False,
        ),
        sa.Column(
            "calculated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["shop_pk"],
            ["commerce.shops.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "shop_pk",
            "calculated_on",
            name="uq_shop_fee_rate_est_shop_day",
        ),
        sa.CheckConstraint(
            "fee_rate >= 0 AND fee_rate <= 1",
            name="ck_shop_fee_rate_est_rate",
        ),
        sa.CheckConstraint(
            "coverage_ratio >= 0 AND coverage_ratio <= 1",
            name="ck_shop_fee_rate_est_coverage",
        ),
        schema="reporting",
    )
    # 读取侧固定取「每店最新」一行：该复合索引同时服务 ORDER BY calculated_at DESC。
    op.create_index(
        "ix_shop_fee_rate_est_shop_calc_at",
        "shop_fee_rate_estimates",
        ["shop_pk", "calculated_at"],
        schema="reporting",
    )

    # updated_at 触发器 —— 命名与既有触发器同一约定
    # (trg_<schema>_<table>_touch → public.fn_touch_updated_at())。
    op.execute(
        "CREATE OR REPLACE TRIGGER trg_reporting_shop_fee_rate_estimates_touch "
        "BEFORE UPDATE ON reporting.shop_fee_rate_estimates FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )

    op.execute(
        "COMMENT ON TABLE reporting.shop_fee_rate_estimates IS "
        "'店铺级平台抽成费率日快照（analytics.shop_fee_rate 任务每 24h 按近 N 天"
        "已结算订单 Σ|FEE|/ΣGROSS_SALES 重算）；无快照或快照过期 = 回退全局基线 0.308'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.fee_rate IS "
        "'GMV 加权平均抽成率 = Σ|FEE| / ΣGROSS_SALES（仅含 FEE 与 GROSS_SALES "
        "币种一致的已结算交易）'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.coverage_ratio IS "
        "'覆盖率 = 有 FEE 分项交易的 GROSS_SALES / 窗口内全部已结算 GROSS_SALES；"
        "偏低说明历史交易缺 FEE 分项，该行不应作为费率依据'"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_reporting_shop_fee_rate_estimates_touch "
        "ON reporting.shop_fee_rate_estimates"
    )
    op.drop_index(
        "ix_shop_fee_rate_est_shop_calc_at",
        table_name="shop_fee_rate_estimates",
        schema="reporting",
    )
    op.drop_table("shop_fee_rate_estimates", schema="reporting")
