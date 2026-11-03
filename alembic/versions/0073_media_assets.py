"""Global media asset library and digital humans.

Revision ID: 0073_media_assets
Revises: 0072_shop_app_credentials

New schema ``media`` with three tables:

- ``media.media_assets`` — one uploaded image or video.
- ``media.digital_humans`` — one persona record.
- ``media.digital_human_assets`` — the many-to-many persona ↔ asset link.

The library is global: assets carry no shop partition and may be unattached
or shared by several personas.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0073_media_assets"
down_revision: str | None = "0072_shop_app_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(text("CREATE SCHEMA IF NOT EXISTS media"))
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE media.media_assets (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          kind TEXT NOT NULL,
          filename TEXT NOT NULL,
          content_type TEXT NOT NULL,
          size_bytes BIGINT NOT NULL,
          bucket TEXT NOT NULL,
          object_key TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'awaiting_upload',
          failure_reason TEXT,
          description TEXT,
          tags TEXT[] NOT NULL DEFAULT '{}',
          uploaded_by_key_id TEXT,
          uploaded_by_prefix TEXT,
          uploaded_at TIMESTAMPTZ,
          deleted_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT media_asset_kind_check CHECK (kind IN ('image','video')),
          CONSTRAINT media_asset_status_check CHECK (status IN ('awaiting_upload','ready','failed')),
          CONSTRAINT media_asset_size_check CHECK (size_bytes > 0)
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE media.digital_humans (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          created_by TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at TIMESTAMPTZ,
          CONSTRAINT digital_human_status_check CHECK (status IN ('active','archived'))
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE media.digital_human_assets (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          digital_human_id BIGINT NOT NULL
            REFERENCES media.digital_humans(id) ON DELETE CASCADE,
          asset_id BIGINT NOT NULL
            REFERENCES media.media_assets(id) ON DELETE CASCADE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_digital_human_assets_pair UNIQUE (digital_human_id, asset_id)
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_media_assets_status ON media.media_assets (status)"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_media_assets_created ON media.media_assets (created_at DESC, id DESC)"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("CREATE INDEX ix_digital_humans_status ON media.digital_humans (status)")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("CREATE INDEX ix_digital_humans_name ON media.digital_humans (name)")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_digital_human_assets_asset ON media.digital_human_assets (asset_id)"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_media_assets_touch BEFORE UPDATE ON media.media_assets FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_digital_humans_touch BEFORE UPDATE ON media.digital_humans FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_digital_human_assets_touch BEFORE UPDATE ON media.digital_human_assets FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    # Page permission points. The media library is an operator tool: admin and
    # operator receive both pages; the read-only analyst role does not.
    for code, name in (
        ("page:media-assets", "资产管理"),
        ("page:digital-humans", "数字人"),
    ):
        # pi-lens-ignore: python-sql-injection
        op.execute(
            text(
                "INSERT INTO security.permissions (code, kind, name) "
                f"VALUES ('{code}','page','{name}') ON CONFLICT (code) DO NOTHING"
            )
        )
        for role_code in ("admin", "operator"):
            # pi-lens-ignore: python-sql-injection
            op.execute(
                text(
                    "INSERT INTO security.role_permissions (role_code, permission_code) "
                    f"SELECT '{role_code}','{code}' "
                    "WHERE EXISTS (SELECT 1 FROM security.roles WHERE code = "
                    f"'{role_code}') "
                    "ON CONFLICT DO NOTHING"
                )
            )


def downgrade() -> None:
    # Assets are user-uploaded media; dropping the schema destroys them.
    bind = op.get_bind()
    if bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM media.media_assets) "
            "OR EXISTS (SELECT 1 FROM media.digital_humans)"
        )
    ).scalar():
        raise RuntimeError("refusing downgrade while media library data exists")
    # pi-lens-ignore: python-sql-injection
    op.execute(text("DROP SCHEMA media CASCADE"))
    for code in ("page:media-assets", "page:digital-humans"):
        # pi-lens-ignore: python-sql-injection
        op.execute(
            text(f"DELETE FROM security.permissions WHERE code='{code}'")
        )
