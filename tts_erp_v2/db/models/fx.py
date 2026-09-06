"""fx.* — cached exchange rates (third-party upstream, quota-bounded).

2 tables:
* ``exchange_rate_snapshots`` — one row per upstream fetch of the
  ExchangeRate-API Standard endpoint (v6.exchangerate-api.com, Free
  plan = 1500 requests / month). Stores the upstream-published update
  window verbatim: ``upstream_last_update`` (when the rates were last
  refreshed upstream) and ``next_update_at`` (when the upstream says the
  rates will next change — this is the **cache horizon** that gates the
  sync job, so a fetch happens ~once per day, never per request).
* ``exchange_rates`` — the per-(snapshot, currency) conversion table
  ("汇率换算表"): rate of one unit of ``base_code`` in ``target_code``.
  Arbitrary currency pairs are derived locally through the snapshot's
  base as a bridge; the read API never dials the upstream.

Design constraints (see tech-doc/fx-exchange-rates.md):
* Upstream key lives in env (``EXCHANGERATE_API_KEY``) and is only ever
  used by the sync worker — there is NO HTTP surface for it.
* A snapshot is unique per (base_code, upstream_last_update): a re-fetch
  that observes the same upstream timestamp updates the existing
  snapshot (fresh ``next_update_at`` / ``fetched_at``) instead of
  duplicating rows. Idempotent by construction.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class ExchangeRateSnapshot(Base):
    """Metadata of one successful Standard-endpoint fetch (per base code).

    ``next_update_at`` mirrors the upstream ``time_next_update_utc``:
    while ``now() < next_update_at`` the stored rates are authoritative
    and the sync job must NOT hit the network (quota budget: the Free
    plan updates rates ~daily, so a horizon-gated job costs ~1 request
    per day ≈ 30/1500 per month).

    History is retained (a fresh upstream timestamp inserts a new
    snapshot); volume is ~160 rate rows/day — negligible.
    """

    __tablename__ = "exchange_rate_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "base_code",
            "upstream_last_update",
            name="uq_fx_snapshots_base_update",
        ),
        Index("ix_fx_snapshots_base_id", "base_code", "id"),
        {"schema": "fx"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    base_code: Mapped[str] = mapped_column(Text, nullable=False)
    # Upstream "time_last_update_utc" — the timestamp the rates refer to.
    upstream_last_update: Mapped[datetime] = mapped_column(nullable=False)
    # Upstream "time_next_update_utc" — cache horizon; the sync job
    # skips while now < next_update_at. NULL when the upstream response
    # omitted it (defensive fallback in the job: treat as 12 h).
    next_update_at: Mapped[datetime | None]
    # Local wall-clock of this fetch (falls back to now() at insert).
    fetched_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    rates_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class ExchangeRate(Base):
    """Conversion-table rows: 1 unit of ``base_code`` = ``rate`` of ``target_code``.

    All rows of one snapshot share the same ``base_code`` (the snapshot
    is per fetch *per base*). Cross-pair conversion between two
    non-base currencies goes through the base as a bridge — computed in
    :mod:`tts_erp_v2.fx.rates`, never by an extra upstream call.
    """

    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "target_code",
            name="uq_fx_rates_snapshot_target",
        ),
        {"schema": "fx"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fx.exchange_rate_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    base_code: Mapped[str] = mapped_column(Text, nullable=False)
    target_code: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = ["ExchangeRate", "ExchangeRateSnapshot"]
