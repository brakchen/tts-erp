"""merge 0036_config_schema + 0036_channel_accounts_service_id

Revision ID: 0037_merge_0036_heads
Revises: 0036_config_schema, 0036_channel_accounts_service_id
Create Date: 2026-09-28

两个 0036 migration 同时从 0035 分叉，本 migration 合并它们。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]  # noqa: F401

revision: str = "0037_merge_0036_heads"
down_revision: tuple[str, ...] | str | None = (
    "0036_config_schema",
    "0036_channel_accounts_service_id",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op merge — both branches already applied their DDL."""


def downgrade() -> None:
    """No-op."""
