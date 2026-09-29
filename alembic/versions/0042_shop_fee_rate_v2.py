"""Invalidate ambiguous fee-v1 snapshots and make kept-only fee-v2 explicit.

Revision ID: 0042_shop_fee_rate_v2
Revises: 0041_shop_fee_rate_kept_only
Create Date: 2026-09-29

``0040`` calculated rates across refunded and kept orders. ``0041`` changed the
job to kept-only semantics but only renamed columns, so an existing ``fee-v1``
row could be read as if it had already been recomputed. Both algorithms also
used the same version string.

This migration preserves the derived rows for forensics but relabels every
ambiguous ``fee-v1`` row as ``fee-v1-legacy``. Readers accept only ``fee-v2``;
until the required post-migration job run writes fresh rows, they safely fall
back to the 0.308 baseline instead of serving a known-wrong estimate.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0042_shop_fee_rate_v2"
down_revision: str | None = "0041_shop_fee_rate_kept_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Preserve rather than delete derived history, but ensure current readers
    # cannot mistake rows produced by either pre-v2 algorithm for fee-v2.
    op.execute(
        "UPDATE reporting.shop_fee_rate_estimates "
        "SET calculation_version = 'fee-v1-legacy' "
        "WHERE calculation_version = 'fee-v1'"
    )
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "ALTER COLUMN calculation_version SET DEFAULT 'fee-v2'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.calculation_version IS "
        "'fee-v2 = kept-only rate; fee-v1-legacy = ambiguous pre-v2 snapshot, "
        "retained for forensics but ignored by readers'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE reporting.shop_fee_rate_estimates "
        "ALTER COLUMN calculation_version SET DEFAULT 'fee-v1'"
    )
    op.execute(
        "UPDATE reporting.shop_fee_rate_estimates "
        "SET calculation_version = 'fee-v1' "
        "WHERE calculation_version = 'fee-v1-legacy'"
    )
    op.execute(
        "COMMENT ON COLUMN reporting.shop_fee_rate_estimates.calculation_version IS "
        "'fee-v1 = pre-v2 or 0041 kept-only snapshot; version did not distinguish them'"
    )
