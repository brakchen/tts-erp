"""fx read service — latest snapshot lookup + local currency conversion.

Pure DB reads + Decimal math over the ``fx.*`` cache tables. Two hard
rules:

* **No upstream calls.** The sync job (``tts_erp_v2.jobs.exchangerate``)
  owns the network; API handlers and this module only ever read.
* **No pair-endpoint calls.** ``exchange_rates`` stores rates per
  (snapshot, target) relative to the snapshot's base. Any pair
  (F → T) is derived through the base as a bridge:

      rate(F → T) = rate(base → T) / rate(base → F)

  Division is quantized to 8 dp (the storage precision,
  Numeric(20, 8)) so repeated lookups return stable values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ExchangeRate, ExchangeRateSnapshot

#: Storage precision of fx.exchange_rates.rate (Numeric(20, 8)).
RATE_QUANTUM = Decimal("0.00000001")

DEFAULT_BASE_CODE = "USD"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def quantize_rate(value: Decimal) -> Decimal:
    """Round a rate to the 8-dp storage / response precision."""
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


class UnknownCurrencyError(ValueError):
    """A requested currency code has no cached rate in the latest snapshot."""

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes
        super().__init__(f"unsupported currency code(s): {', '.join(sorted(codes))}")


@dataclass(frozen=True)
class RateMap:
    """The latest cached snapshot for one base code + its rate rows."""

    snapshot_id: int
    base_code: str
    upstream_last_update: datetime
    next_update_at: datetime | None
    fetched_at: datetime
    rates: dict[str, Decimal]  # target_code → rate (base self-rate included)

    @property
    def rate_count(self) -> int:
        return len(self.rates)

    def is_stale(self, now: datetime | None = None) -> bool:
        """True when the cache is past the upstream refresh horizon.

        ``next_update_at is None`` (defensive rows / pre-horizon data)
        is treated as stale: we cannot prove freshness, so callers are
        told the data may be old rather than silently trusted.
        """
        n = now or _now_utc()
        return self.next_update_at is None or n >= self.next_update_at


def load_rate_map(
    session: Session, base_code: str = DEFAULT_BASE_CODE
) -> RateMap | None:
    """Return the latest snapshot + rates for ``base_code``, or None if the
    sync job has never fetched that base yet."""
    code = (base_code or "").strip().upper() or DEFAULT_BASE_CODE
    snap = session.execute(
        select(ExchangeRateSnapshot)
        .where(ExchangeRateSnapshot.base_code == code)
        .order_by(ExchangeRateSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap is None:
        return None
    rows = session.execute(
        select(ExchangeRate.target_code, ExchangeRate.rate).where(
            ExchangeRate.snapshot_id == snap.id
        )
    ).all()
    return RateMap(
        snapshot_id=snap.id,
        base_code=snap.base_code,
        upstream_last_update=snap.upstream_last_update,
        next_update_at=snap.next_update_at,
        fetched_at=snap.fetched_at,
        rates={row.target_code: row.rate for row in rows},
    )


@dataclass(frozen=True)
class Conversion:
    """Result of a local currency conversion (always 8-dp quantized)."""

    base_code: str
    upstream_last_update: datetime
    next_update_at: datetime | None
    stale: bool
    amount: Decimal
    from_code: str
    to_code: str
    rate: Decimal
    converted: Decimal


def _bridge_rate(rm: RateMap, from_code: str, to_code: str) -> Decimal:
    """rate(F → T) derived through rm.base_code — one row fetch, no math
    when either leg is the base itself."""
    base = rm.base_code
    if from_code == to_code:
        return Decimal(1)
    if from_code == base:
        return rm.rates[to_code]
    if to_code == base:
        # 1 base = rate(from) of from → 1 from = 1/rate(from) base.
        return Decimal(1) / rm.rates[from_code]
    return rm.rates[to_code] / rm.rates[from_code]


def convert_or_none(
    session: Session,
    *,
    amount: Decimal,
    from_code: str,
    to_code: str,
    base_code: str = DEFAULT_BASE_CODE,
    now: datetime | None = None,
) -> Conversion | None:
    """Convert ``amount`` between two cached currencies.

    Returns None when no snapshot exists for ``base_code`` yet (the API
    maps that to 404). Raises :class:`UnknownCurrencyError` when either
    code has no cached rate. Never performs network I/O.
    """
    rm = load_rate_map(session, base_code=base_code)
    if rm is None:
        return None
    frm = (from_code or "").strip().upper()
    to = (to_code or "").strip().upper()
    if not frm or not to:
        raise UnknownCurrencyError([c for c in (frm, to) if not c])
    missing = sorted({c for c in (frm, to) if c not in rm.rates})
    if missing:
        raise UnknownCurrencyError(missing)
    rate = quantize_rate(_bridge_rate(rm, frm, to))
    converted = (amount * rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    return Conversion(
        base_code=rm.base_code,
        upstream_last_update=rm.upstream_last_update,
        next_update_at=rm.next_update_at,
        stale=rm.is_stale(now),
        amount=amount,
        from_code=frm,
        to_code=to,
        rate=rate,
        converted=converted,
    )


def convert(
    session: Session,
    *,
    amount: Decimal,
    from_code: str,
    to_code: str,
    base_code: str = DEFAULT_BASE_CODE,
) -> Conversion:
    """Like :func:`convert_or_none` but raises ``LookupError`` when no
    snapshot exists (keeps callers that already checked None simple)."""
    result = convert_or_none(
        session,
        amount=amount,
        from_code=from_code,
        to_code=to_code,
        base_code=base_code,
    )
    if result is None:
        raise LookupError(f"no cached fx snapshot for base {base_code!r}")
    return result


__all__ = [
    "DEFAULT_BASE_CODE",
    "RATE_QUANTUM",
    "Conversion",
    "RateMap",
    "UnknownCurrencyError",
    "convert",
    "convert_or_none",
    "load_rate_map",
    "quantize_rate",
]
