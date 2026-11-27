"""Append manual cost history; the caller owns the transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ManualProductCost
from tts_erp_v2.reporting.manual_cost_lock import lock_manual_cost_spu


def append_manual_cost(
    session: Session,
    *,
    spu_pk: int,
    unit_cost: Decimal,
    currency: str,
    note: str | None,
    created_by: str,
    valid_from: datetime | None = None,
) -> ManualProductCost:
    """Close the previous version and append under the shared per-SPU lock."""
    lock_manual_cost_spu(session, spu_pk=spu_pk)
    now = datetime.now(UTC)
    session.execute(
        update(ManualProductCost)
        .where(ManualProductCost.spu_pk == spu_pk, ManualProductCost.valid_to.is_(None))
        .values(valid_to=now)
    )
    # RETURNING keeps the response identical to the persisted numeric(20,4),
    # including callers that submit more than four decimal places.
    return session.scalars(
        insert(ManualProductCost).values(
            spu_pk=spu_pk, unit_cost=unit_cost, currency=currency,
            valid_from=valid_from or now, note=note, created_by=created_by, created_at=now,
        ).returning(ManualProductCost)
    ).one()
