"""Shared transaction lock for writers of ``manual_product_costs``."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_purchase_price_spu(session: Session, *, spu_pk: int) -> None:
    """Serialize close-old + insert for one SPU across API/jobs/processes."""
    lock_purchase_price_spus(session, spu_pks=[spu_pk])


def lock_purchase_price_spus(session: Session, *, spu_pks: Sequence[int]) -> None:
    """Acquire the same per-SPU locks in ascending order with one DB round trip."""
    if not spu_pks:
        return
    # pi-lens-ignore: python-sql-injection
    session.execute(
        text("SELECT pg_advisory_xact_lock(spu_pk) "
             "FROM unnest(CAST(:spu_pks AS bigint[])) AS requested(spu_pk) ORDER BY spu_pk"),
        {"spu_pks": sorted(set(spu_pks))},
    ).all()


__all__ = ["lock_purchase_price_spu", "lock_purchase_price_spus"]
