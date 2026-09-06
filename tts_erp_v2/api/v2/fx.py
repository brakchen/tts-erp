"""/v2/fx/* — cached exchange rates + local currency conversion (readonly).

Serves the ``fx.*`` cache tables populated by the ``fx.sync`` sync-worker
job (ExchangeRate-API, Free plan = 1500 requests / month, billed
overage). **These handlers never dial the upstream** — all reads hit the
local Postgres cache and all currency-pair math runs locally through the
snapshot base as a bridge (see tts_erp_v2/fx/rates.py). The stale flag
tells callers whether the cache is past the upstream refresh horizon.

Auth classification (middleware/auth.py): ``/v2/fx/`` → readonly.
Response models are defined here (not in api/schemas.py) because the
latter currently carries an uncommitted concurrent-lane change — the
field naming still follows DB columns exactly, and Decimal serializes
to a JSON string via the app encoder.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.fx import (
    DEFAULT_BASE_CODE,
    UnknownCurrencyError,
    convert_or_none,
    load_rate_map,
)

router = APIRouter(prefix="/v2/fx", tags=["fx"])


class FxLatestOut(BaseModel):
    """Latest cached snapshot for a base code (rates as Decimal → JSON strings)."""

    base_code: str
    upstream_last_update: datetime
    next_update_at: datetime | None
    fetched_at: datetime
    rate_count: int
    #: True when the cache is past the upstream refresh horizon (may be
    #: up to one horizon old until fx.sync refetches).
    stale: bool
    rates: dict[str, Decimal]


class FxConvertOut(BaseModel):
    """One local currency conversion (never an upstream pair call)."""

    base_code: str
    upstream_last_update: datetime
    next_update_at: datetime | None
    stale: bool
    amount: Decimal
    from_code: str
    to_code: str
    rate: Decimal
    converted: Decimal


def _not_found(base_code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"no cached fx snapshot for base {base_code!r} — the fx.sync "
            "job has not fetched it yet"
        ),
    )


@router.get("/latest", response_model=FxLatestOut)
def latest_fx(
    sess: Session = Depends(get_session),
    base_code: str = Query(
        default=DEFAULT_BASE_CODE, min_length=3, max_length=12
    ),
) -> FxLatestOut:
    """Full latest rate map (1 unit of base_code in every target code).

    Rates are JSON strings (Decimal, 8 dp). ``stale`` is true when the
    upstream has refreshed past the stored horizon but fx.sync has not
    refetched yet — clients should treat stale data as approximate.
    """
    rm = load_rate_map(sess, base_code=base_code)
    if rm is None:
        raise _not_found(base_code)
    return FxLatestOut(
        base_code=rm.base_code,
        upstream_last_update=rm.upstream_last_update,
        next_update_at=rm.next_update_at,
        fetched_at=rm.fetched_at,
        rate_count=rm.rate_count,
        stale=rm.is_stale(),
        rates=rm.rates,
    )


@router.get("/convert", response_model=FxConvertOut)
def convert_fx(
    sess: Session = Depends(get_session),
    amount: Decimal = Query(
        ..., description="Amount to convert (JSON number or string)"
    ),
    from_code: str = Query(..., min_length=3, max_length=12),
    to_code: str = Query(..., min_length=3, max_length=12),
    base_code: str = Query(
        default=DEFAULT_BASE_CODE, min_length=3, max_length=12
    ),
) -> FxConvertOut:
    """Convert ``amount`` from ``from_code`` to ``to_code`` using the
    latest cached snapshot for ``base_code`` — pure local math, zero
    upstream requests.

    404 when fx.sync has not fetched a snapshot for ``base_code`` yet;
    400 when either currency code has no cached rate.
    """
    try:
        conv = convert_or_none(
            sess,
            amount=amount,
            from_code=from_code,
            to_code=to_code,
            base_code=base_code,
        )
    except UnknownCurrencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{exc} (fx cache has {exc.codes} unlisted)",
        ) from exc
    if conv is None:
        raise _not_found(base_code)
    return FxConvertOut(
        base_code=conv.base_code,
        upstream_last_update=conv.upstream_last_update,
        next_update_at=conv.next_update_at,
        stale=conv.stale,
        amount=conv.amount,
        from_code=conv.from_code,
        to_code=conv.to_code,
        rate=conv.rate,
        converted=conv.converted,
    )
