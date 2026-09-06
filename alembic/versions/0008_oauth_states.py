"""integration.oauth_states — 一次性 CSRF state 表（新店授权流程 Lane E）

Revision ID: 0008_oauth_states
Revises: 0007_analytics_reorg
Create Date: 2026-09-06

新店 TikTok seller 授权（proxy/tiktik_oauth.py + api/v2/oauth.py）需要
一次性、短 TTL 的 state token（Authorization overview 的 CSRF 建议）：

- 只存 sha256（raw token 只出现在 authorize-link URL 与进程内存）
- pop 用原子 UPDATE ... WHERE consumed_at IS NULL AND expires_at > now()
  RETURNING 保证 single-use（两个并发 callback 只有一个能赢）
- 行是短命的（45 分钟 TTL），无 FK；孤儿行由过期语义自然失效

与 0007 同风格：纯 op.* API（无 op.execute / text），防止自动化 fixer
改写破坏函数体。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0008_oauth_states"
down_revision: str | None = "0007_analytics_reorg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("state_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("state_hash", name="uq_oauth_states_hash"),
        schema="integration",
    )
    op.create_index(
        "ix_oauth_states_created_at",
        "oauth_states",
        ["created_at"],
        schema="integration",
    )
    # ADR-0001 双时间字段约定：BEFORE UPDATE trigger 自动维护 updated_at
    # （与 integration.* 其它表同一命名 trg_<schema>_<table>_touch）。
    op.execute(
        """
        CREATE TRIGGER trg_integration_oauth_states_touch
        BEFORE UPDATE ON integration.oauth_states
        FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oauth_states_created_at", table_name="oauth_states", schema="integration"
    )
    op.drop_table("oauth_states", schema="integration")
