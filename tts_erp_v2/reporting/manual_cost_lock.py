"""Shared transaction lock for writers of ``manual_product_costs``."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_manual_cost_spu(session: Session, *, spu_pk: int) -> None:
    """Serialize close-old + insert for one SPU across API/jobs/processes."""
    # pi-lens-ignore: python-sql-injection
    session.execute(
        text("SELECT pg_advisory_xact_lock(:spu_pk)"),
        {"spu_pk": spu_pk},
    )


__all__ = ["lock_manual_cost_spu"]
