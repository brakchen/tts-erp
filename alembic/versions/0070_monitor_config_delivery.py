"""monitor_config restricted api keys and the shop delivery projection.

Adds ``security.api_keys.purpose`` / ``.shop_pk`` (restricted-use scope for the
monitor plugin token) and the two ``config.monitor_*`` tables that hold frozen
per-shop delivery snapshots. See docs/monitor-config-sync.md §4.2 and §5.2.

Revision ID: 0070_monitor_config_delivery
Revises: 0069_video_publish_confirmation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0070_monitor_config_delivery"
down_revision: str | None = "0069_video_publish_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── security.api_keys：受限用途与店铺归属 ──────────────────────
    # 既有 key 一律落在 purpose='general' 且无店铺绑定，因此本次改动不扩大
    # 任何既有凭证的权限。
    op.add_column(
        "api_keys",
        sa.Column(
            "purpose",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'general'"),
        ),
        schema="security",
    )
    op.add_column(
        "api_keys",
        sa.Column("shop_pk", sa.BigInteger(), nullable=True),
        schema="security",
    )
    op.create_foreign_key(
        "fk_api_keys_shop_pk",
        "api_keys",
        "shops",
        ["shop_pk"],
        ["id"],
        source_schema="security",
        referent_schema="commerce",
        ondelete="RESTRICT",
    )
    op.create_index("ix_api_keys_shop_pk", "api_keys", ["shop_pk"], schema="security")
    # monitor 用途必须绑定店铺；general 用途不得携带绑定，否则会暗示一个它
    # 并不具备的店铺作用域。
    op.create_check_constraint(
        "ck_api_keys_purpose_shop_binding",
        "api_keys",
        "(purpose = 'monitor_config' AND shop_pk IS NOT NULL) OR "
        "(purpose <> 'monitor_config' AND shop_pk IS NULL)",
        schema="security",
    )

    # ── config.monitor_shop_state：版本指针与发布行锁锚点 ──────────
    op.create_table(
        "monitor_shop_state",
        sa.Column("shop_pk", sa.BigInteger(), nullable=False),
        sa.Column("current_delivery_version", sa.BigInteger(), nullable=True),
        sa.Column("last_force_version", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shop_pk"],
            ["commerce.shops.id"],
            name="fk_monitor_shop_state_shop_pk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("shop_pk"),
        schema="config",
    )

    # ── config.monitor_config_deliveries：不可变的完整店铺配置快照 ──
    op.create_table(
        "monitor_config_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shop_pk", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("source_global_version", sa.BigInteger(), nullable=True),
        sa.Column("source_shop_version", sa.BigInteger(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "is_force",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_digest", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["shop_pk"],
            ["commerce.shops.id"],
            name="fk_monitor_config_deliveries_shop_pk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_pk", "version", name="uq_monitor_config_deliveries_shop_version"
        ),
        sa.UniqueConstraint(
            "shop_pk",
            "request_id",
            name="uq_monitor_config_deliveries_shop_request_id",
        ),
        schema="config",
    )
    op.create_index(
        "ix_monitor_config_deliveries_shop_force",
        "monitor_config_deliveries",
        ["shop_pk", "is_force"],
        schema="config",
    )


def downgrade() -> None:
    # Delivery rows are frozen published configuration and shop state records
    # which shops are onboarded; neither can be rebuilt by re-running an
    # upgrade. Refuse instead of dropping source-of-truth facts.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM config.monitor_config_deliveries LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    '0070 downgrade refused: monitor delivery rows exist';
            END IF;
            IF EXISTS (
                SELECT 1 FROM config.monitor_shop_state LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    '0070 downgrade refused: onboarded monitor shops exist';
            END IF;
            IF EXISTS (
                SELECT 1 FROM security.api_keys
                WHERE purpose <> 'general'
            ) THEN
                RAISE EXCEPTION
                    '0070 downgrade refused: restricted api keys exist';
            END IF;
        END $$
        """)
    )
    op.drop_index(
        "ix_monitor_config_deliveries_shop_force",
        table_name="monitor_config_deliveries",
        schema="config",
    )
    op.drop_table("monitor_config_deliveries", schema="config")
    op.drop_table("monitor_shop_state", schema="config")
    op.drop_constraint(
        "ck_api_keys_purpose_shop_binding", "api_keys", schema="security"
    )
    op.drop_index("ix_api_keys_shop_pk", table_name="api_keys", schema="security")
    op.drop_constraint("fk_api_keys_shop_pk", "api_keys", schema="security")
    op.drop_column("api_keys", "shop_pk", schema="security")
    op.drop_column("api_keys", "purpose", schema="security")
