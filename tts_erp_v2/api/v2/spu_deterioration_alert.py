"""Read-only materialized SPU profit deterioration alerts API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import ALERT_CONFIG_KEY
from tts_erp_v2.analytics.spu_deterioration_alert.read import read_alerts
from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.db.models import SpuDeteriorationAlert

router = APIRouter(prefix="/v2/analytics", tags=["analytics"])


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _row(row: SpuDeteriorationAlert) -> dict[str, Any]:
    warning_code = {
        "profit_to_loss": "PROFIT_TO_LOSS",
        "loss_expanding": "LOSS_EXPANDING",
        "sample_insufficient": "SAMPLE_INSUFFICIENT",
        "unavailable": "DATA_STALE",
    }.get(row.state, "ROI_AND_NET_PROFIT_DETERIORATED")
    return {
        "shopPk": row.shop_pk,
        "spuPk": row.spu_pk,
        "windowDays": row.window_days,
        "layer": row.layer,
        "severity": row.severity,
        "state": row.state,
        "sampleStatus": row.sample_status,
        "previousRoi": _decimal(row.prior_roi),
        "currentRoi": _decimal(row.current_roi),
        "roiDecline": _decimal(row.roi_decline),
        "previousNetProfitCny": _decimal(row.prior_net_profit_cny),
        "currentNetProfitCny": _decimal(row.current_net_profit_cny),
        "netProfitDecline": _decimal(row.net_profit_decline),
        "previousSpendCny": _decimal(row.previous_spend_cny),
        "currentSpendCny": _decimal(row.current_spend_cny),
        "previousOrderCount": row.previous_order_count,
        "currentOrderCount": row.current_order_count,
        "previousAdOrderCount": row.previous_ad_orders,
        "currentAdOrderCount": row.current_ad_orders,
        "anchorDate": row.anchor_date.isoformat(),
        "basisCalculatedAt": row.basis_calculated_at.isoformat(),
        "configSource": row.effective_config_source,
        "configVersion": row.effective_config_version,
        "provisionalLabel": "回测暂定"
        if row.effective_config_source == "seed_fallback"
        else None,
        "warningCode": warning_code,
        "warningText": "实际 ROI 与净利润比较恶化" if row.severity != "none" else None,
        "drilldown": {
            "profitabilityUrl": f"/v2/analytics/spu-roi?shop_pk={row.shop_pk}&spu_pk={row.spu_pk}",
            "pageUrl": "/v2/pages/spu-roi",
        },
    }


@router.get("/spu-profit-deterioration")
def list_spu_profit_deterioration(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    shop_pk: Annotated[int, Query(ge=1)],
    spu_ids: Annotated[list[int] | None, Query(alias="spu_ids")] = None,
    window_days: Annotated[list[int] | None, Query(alias="window_days")] = None,
    layer: Annotated[str, Query()] = "all",
    severity: Annotated[str, Query()] = "all",
    state: Annotated[list[str] | None, Query(alias="state")] = None,
    sample: str = Query(default="all"),
    anchor_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    require_role_at_least(request, "readonly")
    if window_days is not None and (
        not window_days or any(value not in (1, 3, 7) for value in window_days)
    ):
        raise HTTPException(
            status_code=422, detail="window_days must be one of 1, 3, 7"
        )
    if layer not in ("all", "fast", "confirmation"):
        raise HTTPException(
            status_code=422, detail="layer must be all, fast or confirmation"
        )
    if severity not in ("all", "none", "warning", "critical"):
        raise HTTPException(status_code=422, detail="invalid severity")
    if state is not None and any(
        value
        not in {
            "profit_to_loss",
            "loss_expanding",
            "loss_to_profit",
            "roi_deterioration",
            "net_profit_deterioration",
            "roi_recovery",
            "recovery",
            "stable",
            "sample_insufficient",
            "unavailable",
        }
        for value in state
    ):
        raise HTTPException(status_code=422, detail="invalid state")
    if sample not in ("all", "sufficient", "sample_insufficient", "unavailable"):
        raise HTTPException(status_code=422, detail="invalid sample")
    parsed_spu_ids = spu_ids or []
    if len(parsed_spu_ids) > 100 or any(value < 1 for value in parsed_spu_ids):
        raise HTTPException(
            status_code=422, detail="spu_ids must contain 1..100 positive ids"
        )
    try:
        rows, total, all_rows, config_basis = read_alerts(
            session,
            shop_pk=shop_pk,
            spu_pks=parsed_spu_ids,
            window_days=window_days,
            layer=None if layer == "all" else layer,
            severity=None if severity == "all" else severity,
            states=state,
            sample=sample,
            anchor_date=anchor_date,
            limit=limit,
            offset=offset,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    payload, source, version, payload_hash, updated_at, updated_by = config_basis
    latest = all_rows[0] if all_rows else None
    totals = {
        "warningCount": sum(row.severity == "warning" for row in all_rows),
        "criticalCount": sum(row.severity == "critical" for row in all_rows),
        "insufficientSampleCount": sum(
            row.sample_status != "sufficient" for row in all_rows
        ),
        "shopSpuCount": len({row.spu_pk for row in all_rows}),
    }
    request_id = request.headers.get("x-request-id") or ""
    return {
        "items": [_row(row) for row in rows],
        "total": total,
        "totals": totals,
        "meta": {
            "requestId": request_id,
            "enabled": payload["enabled"],
            "anchorDate": latest.anchor_date.isoformat() if latest else None,
            "batchThrough": latest.anchor_date.isoformat() if latest else None,
            "maturityDays": payload["maturityDays"],
            "calculatedAt": latest.basis_calculated_at.isoformat() if latest else None,
            "stale": False,
            "coverage": {"materialized": True, "stale": False, "missingWindowCount": 0},
            "config": {
                "key": ALERT_CONFIG_KEY,
                "source": source,
                "version": version,
                "updatedAt": updated_at.isoformat() if updated_at else None,
                "updatedBy": updated_by,
                "validation": "passed",
            },
            "effectiveConfig": {
                "source": source,
                "version": version,
                "updatedAt": updated_at.isoformat() if updated_at else None,
                "updatedBy": updated_by,
                "validation": "passed",
                "enabled": payload["enabled"],
                "maturityDays": payload["maturityDays"],
                "thresholds": payload,
                "payloadHash": payload_hash,
                "provisionalLabel": "回测暂定" if source == "seed_fallback" else None,
                "drawer": {
                    "mode": "published_effective_readonly_safe",
                    "canEdit": False,
                    "draftIncluded": False,
                    "rolloutIncluded": False,
                    "secretsIncluded": False,
                },
            },
        },
    }
