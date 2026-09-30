"""Remove the unused linkage schema.

Revision ID: 0044_drop_linkage_schema
Revises: 0043_focused_spus
Create Date: 2026-09-30

The schema contained six tables (account_links, product_links, variant_links,
link_evidence, link_overrides, link_issues) and the effective_product_links
view. The ingestion pipeline only populated link_evidence; no production path
converted that evidence into effective links.

A production archive was created before this migration was authored:

* path: /home/schan/backups/tts_erp_manual/linkage_schema_20260930T042224Z.dump
* sha256: 5e167d6cd41f0a7270140ce6b40db97a1a42ba2216a199163fc56f93fe3957d3
* rows: link_evidence=716; every other linkage table=0

Production application remains human-operated. Deploy and restart the
linkage-free API and sync worker before applying this guarded migration.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0044_drop_linkage_schema"
down_revision: str | None = "0043_focused_spus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS linkage CASCADE")


def downgrade() -> None:
    raise RuntimeError(
        "linkage data cannot be recreated by Alembic; restore the archived "
        "custom-format dump and then run `alembic stamp 0043_focused_spus`"
    )
