"""Four-field monitor intake; intentionally not mounted until parent integration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_cookie_csrf, require_role_at_least
from tts_erp_v2.plugin.monitor_health.repository import ingest

router = APIRouter(prefix="/v2/plugin/monitor", tags=["monitor-health"])
MAX_BODY_BYTES = 4096


class HealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    shop_id: str = Field(alias="shopId", min_length=1, max_length=128)
    shop_name: str = Field(alias="shopName", max_length=200)
    step: Literal["heartbeat", "scan", "evaluate", "close"]
    status: Literal["running", "stopped", "started", "progress", "completed", "failed"]

    @model_validator(mode="after")
    def valid_combination(self) -> Self:
        if not self.shop_id.strip():
            raise ValueError("shopId must not be blank")
        valid = (
            self.status in {"running", "stopped"}
            if self.step == "heartbeat"
            else self.status in {"started", "completed", "failed"}
            or (self.status == "progress" and self.step in {"scan", "close"})
        )
        if not valid:
            raise ValueError("invalid step/status combination")
        return self


async def _report(request: Request) -> tuple[HealthReport, datetime]:
    arrived = datetime.now(UTC)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "monitor health body exceeds 4KiB")
        raw.extend(chunk)
    try:
        return HealthReport.model_validate_json(raw), arrived
    except ValidationError:
        raise HTTPException(422, "invalid monitor health report") from None


@router.post("/health", status_code=204)
def post_health(
    request: Request,
    report: Annotated[tuple[HealthReport, datetime], Depends(_report)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)
    body, arrived = report
    scopes = request.scope.get("api_key_scopes", ())
    # Here shopId is Seller Center's oec_seller_id / seller_id from tab-binding,
    # not an advertiser ID, commerce PK, or assumed OpenAPI shop_id mapping.
    if scopes and "*" not in scopes:
        known = all(value.startswith(("seller:", "advertiser:")) for value in scopes)
        if not known or f"seller:{body.shop_id}" not in scopes:
            raise HTTPException(403, "shop scope denied")
    try:
        ingest(
            session, body.shop_id, body.shop_name, body.step, body.status, now=arrived
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "monitor health persistence failed") from None
    return Response(status_code=204)
