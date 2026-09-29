"""口径修正：r̂ 只统计「未退款(kept)」订单 —— 列名同步改清晰。

Revision ID: 0041
Revises: 0040_shop_fee_rate_estimates
Create Date: 2026-09-29

背景（2026-09-29 生产库实测反证）
---------------------------------
0040 的 ``fee_rate`` 分母是「窗口内全部已结算订单的 line_gmv」，
把**全额退款**订单（其 ``FEE`` 仅 ~3% of line_gmv）也算了进去，
把费率从真实的 ~32% 拉低到 ~21%。

但页面估算未结算订单用的公式是::

    unsettled_net = unsettled_sales × (1 − r̂) × (1 − 退款率)

其中 ``(1 − 退款率)`` **已经**单独扣过一次退款。若 r̂ 的样本里再混入退款
订单，就把退款效应**算了两遍**，导致未结算净收入被系统性高估。

生产库反证（把已结算订单当作未结算来预测，与真实 ΣSETTLEMENT 比）::

    shop 314   真实 234,747,738  kept 口径 233,401,405 (-0.6%)  全部口径 271,139,041 (+15.5%)
    shop 68234 真实  76,295,977  kept 口径  77,046,204 (+1.0%)  全部口径  88,999,139 (+16.6%)

即 kept 口径预测误差 ±1%，全部口径高估 ~16%。同时 kept 口径实测
32.30% / 31.48% 与文档里长期记载的基线 **30.8%** 一致 —— 说明 30.8% 一直
是对的，是 0040 的口径错了。

改动
----
仅重命名列以匹配新语义（数据不动；PG 元数据操作，瞬时完成）::

    eligible_order_count -> kept_order_count   有 FEE 且无客户退款的订单数
    line_gmv_covered     -> kept_line_gmv      费率分母：这批订单的 line_gmv
    line_gmv_total       -> window_line_gmv    窗口内全部已结算订单的 line_gmv
    coverage_ratio       -> kept_share         kept / window

注：``kept_share`` 的余量 = 退款订单 + 缺 FEE 分项的订单（两者都不可观测或
不该计入费率），仅供观测，不再作为任何门槛（用户拍板：有一单已结算就算）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0041_shop_fee_rate_kept_only"
down_revision: str | None = "0040_shop_fee_rate_estimates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "reporting.shop_fee_rate_estimates"


def upgrade() -> None:
    # 全部写成字面量 ALTER（不变量、不拼字符串），彻底避开动态 SQL 审计告警。
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN eligible_order_count TO kept_order_count"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN line_gmv_covered TO kept_line_gmv"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN line_gmv_total TO window_line_gmv"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN coverage_ratio TO kept_share"
    )
    # 模型里 CHECK 约束已改名为 ck_shop_fee_rate_est_kept_share，
    # 0040 建的旧名要同步改，否则 autogenerate/文档对不上。
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME CONSTRAINT ck_shop_fee_rate_est_coverage "
        "TO ck_shop_fee_rate_est_kept_share"
    )

    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.fee_rate IS "
        "'未退款(kept)订单的 GMV 加权平台抽成率 = sum(|FEE|) / sum(line_gmv)；"
        "line_gmv = 订单行 quantity*unit_price = 客户实付。"
        "只统计无客户退款的订单 —— 退款效应由页面公式的 (1-退款率) 单独扣，"
        "此处重复计入会高估未结算净收入'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.kept_order_count IS "
        "'参与费率计算的订单数（有 FEE 分项且无客户退款）'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.kept_line_gmv IS "
        "'费率分母：未退款且可观测(有 FEE)订单的 line_gmv 合计'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.window_line_gmv IS "
        "'窗口内全部已结算订单的 line_gmv 合计（仅作上下文对比，不参与费率）'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.kept_share IS "
        "'kept_line_gmv / window_line_gmv。余量 = 退款订单 + 缺 FEE 分项的订单；"
        "仅供观测，不作门槛'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME CONSTRAINT ck_shop_fee_rate_est_kept_share "
        "TO ck_shop_fee_rate_est_coverage"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN kept_order_count TO eligible_order_count"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN kept_line_gmv TO line_gmv_covered"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN window_line_gmv TO line_gmv_total"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "RENAME COLUMN kept_share TO coverage_ratio"
    )
