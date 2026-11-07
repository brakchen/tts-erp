"""Add digital human portraits and indexes for recent-first media feeds."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0075_media_card_feeds"
down_revision = "0074_runninghub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column(
            "is_portrait", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        schema="media",
    )
    op.add_column(
        "digital_humans",
        sa.Column("portrait_asset_id", sa.BigInteger()),
        schema="media",
    )
    op.create_foreign_key(
        "fk_digital_humans_portrait_asset",
        "digital_humans",
        "media_assets",
        ["portrait_asset_id"],
        ["id"],
        source_schema="media",
        referent_schema="media",
    )
    op.create_index(
        "ix_digital_humans_portrait_asset",
        "digital_humans",
        ["portrait_asset_id"],
        schema="media",
        postgresql_where=sa.text("portrait_asset_id IS NOT NULL"),
    )
    for table, condition in (
        ("media_assets", "deleted_at IS NULL AND is_portrait IS FALSE"),
        ("digital_humans", "deleted_at IS NULL"),
    ):
        op.create_index(
            f"ix_{table}_live_recent",
            table,
            [sa.text("updated_at DESC"), sa.text("id DESC")],
            schema="media",
            postgresql_where=sa.text(condition),
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM media.media_assets WHERE is_portrait) THEN
                RAISE EXCEPTION 'Portrait assets must be handled before removing their visibility flag';
            END IF;
        END;
        $$
        """
    )
    op.drop_index(
        "ix_media_assets_live_recent", table_name="media_assets", schema="media"
    )
    op.drop_index(
        "ix_digital_humans_live_recent", table_name="digital_humans", schema="media"
    )
    op.drop_index(
        "ix_digital_humans_portrait_asset", table_name="digital_humans", schema="media"
    )
    op.drop_constraint(
        "fk_digital_humans_portrait_asset",
        "digital_humans",
        schema="media",
        type_="foreignkey",
    )
    op.drop_column("digital_humans", "portrait_asset_id", schema="media")
    op.drop_column("media_assets", "is_portrait", schema="media")
