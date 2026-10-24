"""Read-only PRD v3 SPU profitability comparisons at the established URL."""

from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.availability import (
    ComparisonUnavailable,
)
from tts_erp_v2.analytics.spu_deterioration_alert.comparison import (
    ComparisonInputError,
    read_comparison,
)
from tts_erp_v2.analytics.spu_profitability import ProfitabilityError
from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.v2._common import request_id

router = APIRouter(prefix="/v2/analytics", tags=["analytics"])
log = logging.getLogger(__name__)


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


@router.get("/spu-profit-deterioration")
def list_spu_profit_deterioration(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    shop_pk: Annotated[int, Query(ge=1)],
    spu_ids: Annotated[list[int] | None, Query()] = None,
    window_days: Annotated[int, Query()] = 7,
    activity: Annotated[str, Query()] = "recent",
    anchor_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    require_role_at_least(request, "readonly")
    rid = request_id(request)

    def invalid(message: str) -> HTTPException:
        return HTTPException(
            status_code=422, detail={"message": message, "requestId": rid}
        )

    allowed = {
        "shop_pk",
        "spu_ids",
        "window_days",
        "activity",
        "anchor_date",
        "limit",
        "offset",
    }
    if set(request.query_params) - allowed:
        raise invalid(
            "Unsupported/deprecated filters; use shop_pk, spu_ids, window_days=3/7 and activity."
        )
    if any(len(request.query_params.getlist(key)) > 1 for key in allowed - {"spu_ids"}):
        raise invalid("Only spu_ids may be repeated; choose one window_days (3 or 7).")
    try:
        result = read_comparison(
            session,
            shop_pk=shop_pk,
            spu_pks=spu_ids,
            window_days=window_days,
            activity=activity,
            anchor_date=anchor_date,
            limit=limit,
            offset=offset,
        )
    except ComparisonInputError as exc:
        detail = {"message": str(exc), "requestId": rid}
        if exc.latest_date is not None:
            detail["latestAvailableDate"] = exc.latest_date.isoformat()
        raise HTTPException(status_code=422, detail=detail) from exc
    except ComparisonUnavailable as exc:
        raise HTTPException(
            status_code=503, detail={"message": str(exc), "requestId": rid}
        ) from exc
    except (ProfitabilityError, SQLAlchemyError) as exc:
        # Never expose SQL, identifiers from exception strings or credentials.
        log.warning(
            "SPU comparison unavailable request_id=%s error_type=%s",
            rid,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={"message": "盈利事实读取不可用，请重试。", "requestId": rid},
        ) from exc
    result["meta"]["requestId"] = rid
    return _wire(result)
