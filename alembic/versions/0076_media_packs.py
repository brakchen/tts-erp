"""Add named video packs to the shared media library."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0076_media_packs"
down_revision = "0075_media_card_feeds"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "media_packs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("creation_token", postgresql.UUID(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("creation_token", name="uq_media_packs_creation_token"),
        schema="media",
    )
    op.create_table(
        "media_pack_videos",
        sa.Column(
            "pack_id",
            sa.BigInteger(),
            sa.ForeignKey("media.media_packs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("media.media_assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        *_timestamps(),
        schema="media",
    )
    op.create_index(
        "ix_media_pack_videos_asset", "media_pack_videos", ["asset_id"], schema="media"
    )
    op.create_table(
        "digital_human_packs",
        sa.Column(
            "digital_human_id",
            sa.BigInteger(),
            sa.ForeignKey("media.digital_humans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "pack_id",
            sa.BigInteger(),
            sa.ForeignKey("media.media_packs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        *_timestamps(),
        schema="media",
    )
    op.create_index(
        "ix_digital_human_packs_pack",
        "digital_human_packs",
        ["pack_id"],
        schema="media",
    )
    for table in ("media_packs", "media_pack_videos", "digital_human_packs"):
        # pi-lens-ignore: python-sql-injection
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_touch BEFORE UPDATE ON media.{table} "
                "FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
            )
        )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM media.media_packs)")):
        raise RuntimeError("refusing downgrade while media packs exist")
    op.drop_table("digital_human_packs", schema="media")
    op.drop_table("media_pack_videos", schema="media")
    op.drop_table("media_packs", schema="media")
