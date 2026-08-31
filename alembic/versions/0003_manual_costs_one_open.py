"""manual_costs_one_open — partial unique index on procurement.manual_product_costs

Revision ID: 0003_manual_costs_one_open
Revises: 0001_init_nine_schemas
Create Date: 2026-09-01

Audit P1-6 (2026-09-01 fleet review): the manual-costs POST handler used
to write a new row in transaction #1 (commit) and then close the prior
effective row in transaction #2 wrapped in ``try/except: rollback()``. A
failure between the two commits left two rows with ``valid_to IS NULL``
for the same channel_product_id — i.e. two "effective" manual costs at
once. The handler now wraps close-old + insert in a single transaction
(see ``tts_erp_v2/api/v2/reporting.py::submit_manual_cost``), and this
migration adds the DB-side belt-and-suspenders:

    CREATE UNIQUE INDEX uq_manual_costs_one_open
        ON procurement.manual_product_costs (channel_product_id)
        WHERE valid_to IS NULL;

PG partial indexes only enforce uniqueness for rows that match the
predicate (``valid_to IS NULL`` here). Historical / closed rows are
free to accumulate. This is the same pattern used by other
"single-effective-row" tables (e.g. ``procurement.procurement_products``
history-by-valid_to).

If you ever see ``duplicate key value violates unique constraint
"uq_manual_costs_one_open"`` after this migration ships, it means
production had pre-existing duplicate ``valid_to IS NULL`` rows that
need to be reconciled by hand before the index can be created (the
``upgrade`` body checks for that and aborts with a clear error).

Down revision: the previous head revision. The main agent re-stitches
the chain on merge; we hard-code 0001 here so the migration is
self-contained for review.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0003_manual_costs_one_open"
down_revision: str | None = "0002_cases_refund_amount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── 1. Safety check: refuse to create the index if the table
    # already has duplicate valid_to IS NULL rows for any
    # channel_product_id. Without this, CREATE UNIQUE INDEX would fail
    # mid-transaction and leave alembic in a half-state.
    bind = op.get_bind()
    # pi-lens-ignore opengrep.sqlalchemy.sql-injection: literal SQL, no user input
    duplicates = bind.execute(
        text(
            "SELECT channel_product_id, COUNT(*) AS n "
            "FROM procurement.manual_product_costs "
            "WHERE valid_to IS NULL "
            "GROUP BY channel_product_id "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        rows = ", ".join(
            f"channel_product_id={r.channel_product_id} count={r.n}" for r in duplicates
        )
        raise RuntimeError(
            "procurement.manual_product_costs already has multiple "
            "valid_to IS NULL rows per channel_product_id — cannot add "
            "uq_manual_costs_one_open without manual reconciliation. "
            f"Violators: {rows}"
        )

    # ─── 2. Partial unique index. PG evaluates the predicate at write
    # time, so historical rows (valid_to set) are not part of the
    # uniqueness constraint. This is exactly the "single effective
    # row per parent" pattern used elsewhere.
    op.execute(
        "CREATE UNIQUE INDEX uq_manual_costs_one_open "
        "ON procurement.manual_product_costs (channel_product_id) "
        "WHERE valid_to IS NULL"
    )


def downgrade() -> None:
    # Index is rebuildable; no data loss on drop.
    op.execute("DROP INDEX IF EXISTS procurement.uq_manual_costs_one_open")
