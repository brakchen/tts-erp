"""Append purchase-price history; the caller owns the transaction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ManualProductCost
from tts_erp_v2.reporting.purchase_price_lock import lock_purchase_price_spus


@dataclass(frozen=True)
class PurchasePriceEntry:
    spu_pk: int
    unit_cost: Decimal
    currency: str
    note: str | None
    valid_from: datetime | None = None


def append_purchase_price(
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
    return append_purchase_prices(
        session, prices=[PurchasePriceEntry(spu_pk, unit_cost, currency, note, valid_from)],
        created_by=created_by,
    )[0]


def append_purchase_prices(
    session: Session, *, prices: Sequence[PurchasePriceEntry], created_by: str,
) -> list[ManualProductCost]:
    """One lock query, one history-close query and a bulk INSERT with RETURNING.

    Reuse SQLAlchemy 2 ORM bulk insert (MIT), without per-product network IO:
    https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-bulk-insert-statements
    """
    if not prices:
        return []
    spu_pks = [price.spu_pk for price in prices]
    if len(set(spu_pks)) != len(spu_pks):
        raise ValueError("采购价批次包含重复商品")
    lock_purchase_price_spus(session, spu_pks=spu_pks)
    now = datetime.now(UTC)
    session.execute(
        update(ManualProductCost)
        .where(ManualProductCost.spu_pk.in_(spu_pks), ManualProductCost.valid_to.is_(None))
        .values(valid_to=now)
    )
    # RETURNING keeps the response identical to the persisted numeric(20,4),
    # including callers that submit more than four decimal places.
    return list(session.scalars(
        insert(ManualProductCost).returning(ManualProductCost),
        [{"spu_pk": price.spu_pk, "unit_cost": price.unit_cost, "currency": price.currency,
          "valid_from": price.valid_from or now, "note": price.note,
          "created_by": created_by, "created_at": now} for price in prices],
    ).all())
