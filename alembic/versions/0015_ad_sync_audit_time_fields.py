"""ad_sync_audit: add created_at + updated_at + touch trigger (ADR-0001)

Revision ID: 0015_ad_sync_audit_time_fields
Revises: 0014_ad_product_links_range
Create Date: 2026-09-08

The original migration 0013 created ad_sync_audit without the
standard v2 time-field convention. This migration adds:
- ``created_at`` (backfilled from ``occurred_at``)
- ``updated_at`` (default ``now()``)
- ``BEFORE UPDATE`` trigger calling ``fn_touch_updated_at()``
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0015_ad_sync_audit_time_fields"
down_revision: str | None = "0014_ad_product_links_range"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. created_at — backfill from occurred_at (they share semantics)
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_sync_audit
                ADD COLUMN created_at timestamp with time zone
                    DEFAULT now() NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            UPDATE analytics.ad_sync_audit
               SET created_at = occurred_at
             WHERE created_at = (SELECT now())
            """
        )
    )

    # 2. updated_at — default now(), updated by trigger
    op.execute(
        text(
            """
            ALTER TABLE analytics.ad_sync_audit
                ADD COLUMN updated_at timestamp with time zone
                    DEFAULT now() NOT NULL
            """
        )
    )
    op.execute(
        text(
            """
            UPDATE analytics.ad_sync_audit
               SET updated_at = occurred_at
            """
        )
    )

    # 3. BEFORE UPDATE trigger — same as every other v2 table
    op.execute(
        text(
            """
            CREATE TRIGGER trg_ad_sync_audit_touch
                BEFORE UPDATE ON analytics.ad_sync_audit
                FOR EACH ROW
                EXECUTE FUNCTION fn_touch_updated_at()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_ad_sync_audit_touch ON analytics.ad_sync_audit"
        )
    )
    op.execute(
        text("ALTER TABLE analytics.ad_sync_audit DROP COLUMN IF EXISTS updated_at")
    )
    op.execute(
        text("ALTER TABLE analytics.ad_sync_audit DROP COLUMN IF EXISTS created_at")
    )
