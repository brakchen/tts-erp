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

    fee_rate       = Σ|FEE| / Σ line_gmv      （已结算并带 FEE 分项的订单）
    coverage_ratio = Σ line_gmv(有 FEE 的订单) / Σ line_gmv(窗口内全部已结算订单)

**分母必须是 `line_gmv`（订单行 `quantity × unit_price`，= 客户实付），
不是 `GROSS_SALES`**。2026-09-29 在生产库实测确认：

* `sales_order_lines.unit_price` 是**折扣后实付价**（Σ line_gmv 与结算单的
  `CUSTOMER_PAYMENT` 只差 0.24%），而 `GROSS_SALES` 是**折扣前挂牌价**
  （= `AFTER_SELLER_DISCOUNTS_SUBTOTAL` + `SELLER_DISCOUNT`，1 230M vs 727M，差 69%）。
* 逐单恒等式（1204 笔已结算订单，中位残差 **0.000%**，91.3% 在 ±5% 内）::

      SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND

  即 `FEE` 已含全部从卖家结算款扣掉的项目（含运费类）；把分母换成
  `GROSS_SALES` 会得出 12.5%，而公式里真正使用的变量是 `line_gmv`，
  对应 21.2%。
* `FEE` 是交易级平台总扣除（``fee_amount``），**不是**
  ``PLATFORM_COMMISSION``（后者只是抽佣分项，实测仅占一半）。
  另：`FEE + 运费类` 会**重复扣**（运费已在 FEE 内），勿相加。

``coverage_ratio`` 暴露历史交易缺 ``FEE`` 分项的数据缺口（生产库实测
约 62%~92%）—— **只记录、不阻断**（用户拍板 2026-09-29：只要有一单已结算
就要算）。缺 ``FEE`` 的订单既不进分子也不进分母，前端费率卡会显示该值供人工
判断数据完整度。

每日一份快照（``uq_shop_fee_rate_est_shop_day``），保留费率变化历史以便
解释某周净利润波动；读取侧取每店最新一行，超过
``MAX_ESTIMATE_AGE_DAYS`` 视为过期并回退全局基线。无快照 = 回退基线。

详见 ``docs/archive/spu-real-roi-dashboard.md``（费率段）与
``docs/api/external-api.md``（``meta.fee`` 契约）。
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
        sa.Column("line_gmv_covered", sa.Numeric(20, 4), nullable=False),
        sa.Column("line_gmv_total", sa.Numeric(20, 4), nullable=False),
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
        "已结算订单 Σ|FEE|/Σline_gmv 重算）；无快照或快照过期 = 回退全局基线 0.308'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.fee_rate IS "
        "'GMV 加权平均抽成率 = Σ|FEE| / Σline_gmv（line_gmv = 订单行 quantity×unit_price"
        "= 客户实付；不是折扣前的 GROSS_SALES）'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.line_gmv_covered IS "
        "'带 FEE 分项的已结算订单的 line_gmv 合计，即费率的分子分母基准'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.line_gmv_total IS "
        "'窗口内全部已结算订单的 line_gmv 合计；与 covered 之比即覆盖率，"
        "偏低说明历史订单缺 FEE 分项'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.coverage_ratio IS "
        "'覆盖率 = 有 FEE 订单的 line_gmv / 窗口内全部已结算订单的 line_gmv；"
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
