"""config schema + enum_map 表（枚举中文化映射）。

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-28

新建 config schema，存放可配置的枚举映射/元数据。
当前仅 config.enum_map：SPU ROI 钻取面板枚举值中文化映射。

详见 docs/design/spu-roi-enum-translation-plan.md。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0036_config_schema"
down_revision: str | None = "0035_order_detail_history_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ── seed 数据 ──────────────────────────────────────────────────────
_SEED: list[tuple[str, str, str, int]] = [
    # (enum_type, enum_value, label_zh, sort_order)
    # order_status
    ("order_status", "AWAITING_SHIPMENT", "待发货", 1),
    ("order_status", "PARTIAL_SHIPPING", "部分发货", 2),
    ("order_status", "AWAITING_COLLECTION", "待揽收", 3),
    ("order_status", "IN_TRANSIT", "运输中", 4),
    ("order_status", "DELIVERED", "已送达", 5),
    ("order_status", "COMPLETED", "已完成", 6),
    ("order_status", "CANCELLED", "已取消", 7),
    ("order_status", "UNPAID", "未付款", 8),
    ("order_status", "ON_HOLD", "挂起", 9),
    # case_type
    ("case_type", "RETURN_AND_REFUND", "退货退款", 1),
    ("case_type", "REFUND_ONLY", "仅退款", 2),
    ("case_type", "CANCELLATION", "取消", 3),
    ("case_type", "CANCEL", "取消", 4),
    # case_status
    ("case_status", "CANCELLATION_REQUEST_COMPLETE", "取消完成", 1),
    ("case_status", "RETURN_OR_REFUND_REQUEST_COMPLETE", "退货退款完成", 2),
    ("case_status", "REFUND_REQUEST_COMPLETE", "仅退款完成", 3),
    # settle_component
    ("settle_component", "SETTLEMENT", "结算净额", 1),
    ("settle_component", "COMMISSION", "平台佣金", 2),
    ("settle_component", "SHIPPING_FEE", "运费", 3),
    ("settle_component", "AFFILIATE_COMMISSION", "联盟佣金", 4),
    # cost_source
    ("cost_source", "MANUAL", "人工标注", 1),
    ("cost_source", "SOURCE_PRICE", "货源价", 2),
    ("cost_source", "DEFAULT_K1", "默认40元/件", 3),
    # shipment_status
    ("shipment_status", "PENDING", "待处理", 1),
    ("shipment_status", "SHIPPED", "已发货", 2),
    ("shipment_status", "IN_TRANSIT", "运输中", 3),
    ("shipment_status", "DELIVERED", "已送达", 4),
    # column_header（钻取面板表头翻译）
    ("column_header", "is_settled", "已结算", 1),
    ("column_header", "38301", "到海外", 2),
    ("column_header", "SETTLEMENT", "结算净额", 3),
    ("column_header", "statement", "结算时间", 4),
    ("column_header", "case", "Case ID", 5),
    ("column_header", "campaign_id", "广告计划", 6),
    ("column_header", "spend", "消耗", 7),
    ("column_header", "orders", "广告订单", 8),
]


def upgrade() -> None:
    # ── schema ──────────────────────────────────────────────────────
    op.execute(text("CREATE SCHEMA IF NOT EXISTS config"))

    # ── enum_map 表 ────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE config.enum_map (
                id          SERIAL PRIMARY KEY,
                enum_type   VARCHAR(64)  NOT NULL,
                enum_value  VARCHAR(128) NOT NULL,
                label_zh    VARCHAR(256) NOT NULL,
                sort_order  INT          NOT NULL DEFAULT 0,
                created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
                CONSTRAINT uq_enum_map_type_value UNIQUE (enum_type, enum_value)
            )
            """
        )
    )
    op.execute(text("CREATE INDEX ix_enum_map_type ON config.enum_map (enum_type)"))
    op.execute(
        text("COMMENT ON TABLE config.enum_map IS 'SPU ROI 钻取面板枚举值中文化映射表'")
    )
    op.execute(
        text(
            "COMMENT ON COLUMN config.enum_map.enum_type IS "
            "'枚举分类：order_status/case_type/case_status/settle_component/"
            "cost_source/shipment_status/column_header'"
        )
    )
    op.execute(text("COMMENT ON COLUMN config.enum_map.enum_value IS '原始英文枚举值'"))
    op.execute(text("COMMENT ON COLUMN config.enum_map.label_zh IS '中文显示标签'"))

    # updated_at 触发器（复用 public.fn_touch_updated_at）
    op.execute(
        text(
            """
            CREATE TRIGGER trg_enum_map_updated_at
            BEFORE UPDATE ON config.enum_map
            FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
            """
        )
    )

    # ── seed 初始翻译数据 ──────────────────────────────────────────
    for enum_type, enum_value, label_zh, sort_order in _SEED:
        op.execute(
            text(
                "INSERT INTO config.enum_map "
                "(enum_type, enum_value, label_zh, sort_order) "
                "VALUES (:t, :v, :l, :s)"
            ).bindparams(t=enum_type, v=enum_value, l=label_zh, s=sort_order)
        )


def downgrade() -> None:
    op.execute(text("DROP SCHEMA IF EXISTS config CASCADE"))
