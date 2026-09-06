"""fx schema — cached exchange rates (migration 0010)

Revision ID: 0010_fx_exchange_rates
Revises: 0009_add_mirror_object_key
Create Date: 2026-09-06

汇率接入（exchangerate-api.com，Free plan 1500 req/月，超量收费）：
存储层 = 第 11 个 schema ``fx``，两张表：

- ``fx.exchange_rate_snapshots`` — 每次上游 Standard endpoint 拉取
  （``GET /v6/{key}/latest/{base}``）的元数据：``base_code`` +
  ``upstream_last_update``（上游 time_last_update_utc，快照天然幂等键，
  同值重拉不重复插行）+ ``next_update_at``（上游 time_next_update_utc，
  即**缓存失效时刻**——sync job 在 now() < next_update_at 时直接 skip，
  不碰上游 → 上游日更一次 ≈ 每天 0~1 次请求，月用量 ~30/1500）。
- ``fx.exchange_rates`` — 汇率换算表：每 (snapshot, target_code) 一行，
  ``rate`` = 1 单位 base_code 兑 target_code（Numeric(20,8)）。任意币对
  换算走本地（以快照 base 为桥做除法），不调 pair endpoint。

设计/配额预算/运维见 tech-doc/fx-exchange-rates.md。API key 只在
sync-worker 环境（.env ``EXCHANGERATE_API_KEY``），无任何 HTTP 面。

updated_at 触发器命名沿用 ``trg_<schema>_<table>_touch`` 约定
（public.fn_touch_updated_at()）。

downgrade 删表删 schema（汇率数据为可重拉缓存，丢失无业务损失——
与 0007 同注释约定：down 只保证 schema 可回滚，不保证数据）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0010_fx_exchange_rates"
down_revision: str | None = "0009_add_mirror_object_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── CREATE SCHEMA（先于表，与 0001 init 同约定）──────────────────
    op.execute('CREATE SCHEMA IF NOT EXISTS "fx"')

    op.create_table(
        "exchange_rate_snapshots",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("base_code", sa.Text, nullable=False),
        sa.Column("upstream_last_update", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("next_update_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "rates_count",
            sa.Integer,
            server_default=sa.text("0"),
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
        sa.UniqueConstraint(
            "base_code",
            "upstream_last_update",
            name="uq_fx_snapshots_base_update",
        ),
        schema="fx",
    )
    op.create_index(
        "ix_fx_snapshots_base_id",
        "exchange_rate_snapshots",
        ["base_code", "id"],
        schema="fx",
    )

    op.create_table(
        "exchange_rates",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.BigInteger, nullable=False),
        sa.Column("base_code", sa.Text, nullable=False),
        sa.Column("target_code", sa.Text, nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
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
            ["snapshot_id"],
            ["fx.exchange_rate_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "target_code",
            name="uq_fx_rates_snapshot_target",
        ),
        schema="fx",
    )

    # updated_at 触发器 —— 命名与 41 个既有触发器同一约定
    # (trg_<schema>_<table>_touch → public.fn_touch_updated_at())。
    op.execute(
        "CREATE OR REPLACE TRIGGER trg_fx_exchange_rate_snapshots_touch "
        "BEFORE UPDATE ON fx.exchange_rate_snapshots FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER trg_fx_exchange_rates_touch "
        "BEFORE UPDATE ON fx.exchange_rates FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )


def downgrade() -> None:
    """Drop the two fx tables + the fx schema (cache data, no loss)."""
    op.drop_table("exchange_rates", schema="fx")
    op.drop_table("exchange_rate_snapshots", schema="fx")
    op.execute('DROP SCHEMA IF EXISTS "fx"')
