"""Allow settlement_statements.payout_id to be NULL.

V3 §14.3（2026-09-28 修复）：
TikTok 部分历史 statement payload 不携带 payment_id 字段（审计 16/45；
当前 35/88 unresolved），无法挂载到 payouts（FK NOT NULL 强制 skip）。
生产证据（2026-09-28 实测）：35 个被跳过的 statement 的 raw payload
里 distinct transaction 385 条，覆盖 383 个订单；其中 352 个订单已通
过其他 statement 拿到结算，真正因此缺结算的订单仅 31 个（占 1196 的
2.6%，对 ROI 大盘数字影响有限）。但仍是真实数据损失：这些订单
settled_net 从实到账降级为估算公式。

修复：
1. payout_id FK 改 nullable（payment_id 缺失的 statement 以 NULL 入库）
2. 把原 UniqueConstraint（payout_id, external_statement_id）替换为
   UNIQUE（external_statement_id）单一索引——上游 external_statement_id
   本应全局唯一，按它去重既涵盖 NULL payout_id 也涵盖非 NULL，且
   ON CONFLICT DO UPDATE 简单（SQLAlchemy `index_elements=["external_statement_id"]`
   直接生成 ON CONFLICT (external_statement_id)）。
3. finance job 的 _upsert_statement ON CONFLICT 键同步改为
   external_statement_id。

下游：
- 9 月 35 个被跳过的 statement 下次 fetch 时正常入库；
- 它们的 transactions 不再因为 statement FK 缺失而被跳；
- 31 个真实缺结算的订单获得实到账；
- sync_issues 仍记 STATEMENT_PAYMENT_ID_MISSING（人工可追）。
"""

from __future__ import annotations

from alembic import op

revision: str = "0038_statement_payout_nullable"
down_revision: str | None = "0037_merge_0036_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) DROP 原全表 UNIQUE：行级 UniqueConstraint(payout_id, external_statement_id)
    op.execute(
        "ALTER TABLE finance.settlement_statements "
        "DROP CONSTRAINT IF EXISTS uq_settlement_statements_payout_ext"
    )
    # 2) 单 unique index on external_statement_id（上游全局唯一，去重够用，
    #    涵盖 NULL/非 NULL payout_id 两种情况）。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_settlement_statements_ext "
        "ON finance.settlement_statements (external_statement_id)"
    )
    # 3) payout_id 改 nullable（V3 §14.3）
    op.execute(
        "ALTER TABLE finance.settlement_statements "
        "ALTER COLUMN payout_id DROP NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE finance.settlement_statements "
        "ALTER COLUMN payout_id SET NOT NULL"
    )
    op.execute(
        "DROP INDEX IF EXISTS finance.uq_settlement_statements_ext"
    )
    op.execute(
        "ALTER TABLE finance.settlement_statements "
        "ADD CONSTRAINT uq_settlement_statements_payout_ext "
        "UNIQUE (payout_id, external_statement_id)"
    )