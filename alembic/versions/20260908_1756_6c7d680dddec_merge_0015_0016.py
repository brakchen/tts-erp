"""merge 0015 + 0016

Revision ID: 6c7d680dddec
Revises: 0015_ad_sync_audit_time_fields, 0016_chrome_sync_schema
Create Date: 2026-09-08 17:56:56.366632+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '6c7d680dddec'
down_revision: tuple[str, ...] | str | None = ('0015_ad_sync_audit_time_fields', '0016_chrome_sync_schema')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
