"""after_sales.cases — add case-level refund_amount + currency

Revision ID: 0002_cases_refund_amount
Revises: 0001_init_nine_schemas
Create Date: 2026-09-01

Lane 3 (P1-1 / P1-5) follow-up. The TikTok cancellation payload puts
``refund_amount`` at the *case* level (payload top-level key) but the
initial schema only captured refund at the line level. This adds two
nullable columns to ``after_sales.cases`` so the case-level refund
total has a home; IF NOT EXISTS guards make the migration safe to
re-apply.

Why NUMERIC(20,4) + TEXT (mirrors :class:`after_sales.case_lines`)
-------------------------------------------------------------------
- refund amount needs the same precision/scale as the line-level
  ``refund_amount`` so joins / SUM don't have to coerce.
- currency is plain TEXT (case_lines also stores it as TEXT; we do not
  enforce an ISO-4217 FK — TikTok occasionally emits non-standard
  tokens like ``VND`` that are still well-formed).

Notes
-----
- Downgrade is a symmetric DROP COLUMN. The IF EXISTS guard keeps
  downgrade idempotent in case the migration was partially applied.
- No data backfill: existing rows stay with NULL refund_amount/currency.
  The :mod:`tts_erp_v2.jobs.tiktok.after_sales` job re-runs every 15
  minutes and will populate them on the next tick.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_cases_refund_amount"
down_revision: str | None = "0001_init_nine_schemas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS makes the migration re-runnable. Column types match
    # the line-level refund_amount in after_sales.case_lines so SUMs do
    # not have to coerce.
    op.execute(
        "ALTER TABLE after_sales.cases "
        "ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(20, 4)"
    )
    op.execute(
        "ALTER TABLE after_sales.cases "
        "ADD COLUMN IF NOT EXISTS currency TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE after_sales.cases DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE after_sales.cases DROP COLUMN IF EXISTS refund_amount")
