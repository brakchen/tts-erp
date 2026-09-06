"""integration.oauth_states — 一次性 CSRF state 表（新店授权流程 Lane E）

Revision ID: 0011_oauth_states
Revises: 0010_fx_exchange_rates
Create Date: 2026-09-06

部署注记：live DB 的 integration.oauth_states 表已于 2026-09-05 以
out-of-band 方式先行创建（与本文 upgrade() 内容同构），本次合入时以
``alembic stamp 0011_oauth_states`` 记录，upgrade() 仅供全新库按序创建。

新店 TikTok seller 授权（proxy/tiktik_oauth.py + api/v2/oauth.py）需要
一次性、短 TTL 的 state token（Authorization overview 的 CSRF 建议）：

- 只存 sha256（raw token 只出现在 authorize-link URL 与进程内存）
- pop 用原子 UPDATE ... WHERE consumed_at IS NULL AND expires_at > now()
  RETURNING 保证 single-use（两个并发 callback 只有一个能赢）
- 行是短命的（45 分钟 TTL），无 FK；孤儿行由过期语义自然失效

与 0010 同风格：表/索引用 op API；updated_at trigger 无法用 op API 表达，
以裸 op.execute 创建（同 0010_fx_exchange_rates 的先例）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0011_oauth_states"
down_revision: str | None = "0010_fx_exchange_rates"
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
        "CREATE OR REPLACE TRIGGER trg_integration_oauth_states_touch "
        "BEFORE UPDATE ON integration.oauth_states FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oauth_states_created_at", table_name="oauth_states", schema="integration"
    )
    op.drop_table("oauth_states", schema="integration")
