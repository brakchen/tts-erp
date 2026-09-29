"""HTTP adapter for the v10 SPU profitability deep module.

The historical module name and ``/v2/analytics/spu-roi`` URLs are stable wire
contracts.  PostgreSQL queries and profitability behavior live behind
``tts_erp_v2.analytics.spu_profitability``; this adapter only validates query
parameters, serializes exact domain values, and maps domain failures to HTTP.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import (
    EvidenceKind,
    EvidenceRequest,
    FxRateUnavailable,
    ProfitScope,
    RowView,
    SortDirection,
    SortField,
    SpuNotFound,
    explain_spu,
)
from tts_erp_v2.analytics.spu_profitability._compat import read_legacy_overview
from tts_erp_v2.api.deps import get_session
from tts_erp_v2.api.query_params import parse_spu_ids
from tts_erp_v2.api.v2._common import error_response, request_id

_MONEY_Q = Decimal("0.0001")
_RATIO_Q = Decimal("0.01")
_RATE_Q = Decimal("0.0001")

_MONEY_FIELDS = {
    "spend",
    "gmv_ad",
    "sales",
    "gmv_sales",
    "refund_only_amount",
    "refund_return_amount",
    "refund_net_amount",
    "refund_cancelled_amount",
    "return_loss",
    "net_profit",
    "platform_fee",
    "cpa",
    "unit_cost_used",
    "net_revenue",
    "settled_net",
    "unsettled_net",
    "settled_sales",
    "unsettled_sales",
    "cogs_sold",
    "cogs_full_loss_cancelled",
    "cogs_total",
    "gmv",
    "effective_sales",
    "ad_system_max_ad_spend",
    "ad_system_remaining_ad_spend_capacity",
    "line_gmv",
    "settled_net_share",
    "refund_amount",
    "amount",
    "amount_vnd",
}
_RATIO_FIELDS = {
    "roi_l0",
    "cancel_rate",
    "refund_rate_qty",
    "refund_rate",
    "refund_amount_rate",
    "roi_real",
    "roi_breakeven",
    "full_loss_rate",
    "full_loss_qty_rate",
    "share_ratio",
    "ad_system_actual_roi",
    "ad_system_breakeven_roi",
}
_TOTAL_RATE_FIELDS = {"refund_rate", "full_loss_rate", "cancel_rate"}
# fee_rate_used 与 DB 的 NUMERIC(8,6) 同量级，用 4 位小数与 meta.fee.rate 对齐。
_FOUR_DECIMAL_RATIO_FIELDS = {"share_ratio", "fee_rate_used"}

_COST_ASSUMPTION = (
    "按 SPU 解析：人工标注采购成交价(MANUAL)优先，其次 1688 货源价"
    "(SOURCE_PRICE)；均未命中 → 默认 40 CNY/件；DEFAULT_K1 行页面 ⚠ 可补录"
)
_FEE_NOTE = (
    "平台佣金=平台从销售额直接扣除的全部费用；已结算=SETTLEMENT 实到账；"
    "未结算=sales×r̂×(1−SPU退款率)；信息列不重复计入净利。"
    "r̂ 优先级：页面覆写 > 店铺实测（近180天已结算单 Σ|FEE|/Σ行GMV，行GMV=客户实付；"
    "每24h重算且需达标样本量与覆盖率） > 全局基线 0.308"
)


def _fmt(value: Decimal | None, quantum: Decimal) -> str | None:
    if value is None:
        return None
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _fmt_rate(value: Decimal) -> str:
    """费率统一 4 位小数（与行级 ``fee_rate_used`` 同精度，避免 DB
    NUMERIC(8,6) 尾零直接外泄成 "0.359000" 这类前后端不一致的写法）。"""
    return format(value.quantize(_RATE_Q, rounding=ROUND_HALF_UP), "f")


def _wire_value(key: str, value: Any, *, totals: bool = False) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if key in _MONEY_FIELDS:
            return _fmt(value, _MONEY_Q)
        if totals and key in _TOTAL_RATE_FIELDS:
            return _fmt(value, _RATE_Q)
        if key in _FOUR_DECIMAL_RATIO_FIELDS:
            return _fmt(value, _RATE_Q)
        if key in _RATIO_FIELDS:
            return _fmt(value, _RATIO_Q)
        return str(value)
    if is_dataclass(value):
        return {
            field.name: _wire_value(field.name, getattr(value, field.name), totals=totals)
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _wire_value(str(k), v, totals=totals) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire_value(key, item, totals=totals) for item in value]
    return value


def _row_payload(row) -> dict[str, Any]:
    return {
        field.name: _wire_value(field.name, getattr(row, field.name))
        for field in fields(row)
    }


def _totals_payload(totals) -> dict[str, Any]:
    return {
        field.name: _wire_value(field.name, getattr(totals, field.name), totals=True)
        for field in fields(totals)
    }


def _estimate_payload(estimate) -> dict[str, Any] | None:
    """序列化一个店铺费率实测快照（无快照 → None）。"""
    if estimate is None:
        return None
    return {
        "calculated_on": estimate.calculated_on.isoformat(),
        "calculated_at": estimate.calculated_at.isoformat(),
        "lookback_days": estimate.lookback_days,
        "eligible_order_count": estimate.eligible_order_count,
        "line_gmv_covered": str(estimate.line_gmv_covered),
        "line_gmv_total": str(estimate.line_gmv_total),
        "coverage_ratio": _fmt_rate(estimate.coverage_ratio),
        "total_fee": str(estimate.total_fee),
        "currency": estimate.currency,
    }


def _meta_payload(result, scope: ProfitScope, fee_rate: Decimal | None) -> dict[str, Any]:
    basis = result.basis
    if scope.start_date is None and scope.end_date is None:
        window_note = (
            "销售/退款=全历史(未裁剪,可传 w_start/w_end)；"
            "概览单量/GMV 按下单状态全量累计"
        )
    else:
        window_note = (
            "销售/退款已裁剪至日期窗口；ad 同窗口裁剪；退款跟随原订单，统一按 "
            "COALESCE(paid_at, order_time) 归属"
        )
    return {
        "fx": {
            "usd_vnd": _fmt(basis.fx.usd_vnd, _MONEY_Q),
            "cny_usd": _fmt(basis.fx.cny_usd, _MONEY_Q),
            "usd_cny": format(basis.fx.usd_cny, "f"),
            "cny_vnd": format(basis.fx.usd_vnd / basis.fx.usd_cny, "f"),
            "vnd_cny": format(basis.fx.usd_cny / basis.fx.usd_vnd, "f"),
            "as_of": basis.fx.as_of.date().isoformat(),
            "snapshot_id": basis.fx.snapshot_id,
            "source": "fx-cache",
        },
        "cost_assumption": _COST_ASSUMPTION,
        "fee": {
            "source": basis.fee_source,
            "rate": _fmt_rate(basis.fee_rate),
            "override": _fmt_rate(fee_rate) if fee_rate is not None else None,
            "per_shop": [
                {
                    "shop_pk": entry.shop_pk,
                    "shop_name": entry.shop_name,
                    "rate": _fmt_rate(entry.fee_rate),
                    "source": entry.source,
                    "fallback_reason": entry.fallback_reason,
                    "estimate": _estimate_payload(entry.estimate),
                }
                for entry in basis.fee_per_shop
            ],
            "note": _FEE_NOTE,
        },
        "window": {
            "first_day": basis.ad_first_day.isoformat() if basis.ad_first_day else None,
            "last_day": basis.ad_last_day.isoformat() if basis.ad_last_day else None,
            "coverage_first_day": (
                basis.coverage_first_day.isoformat()
                if basis.coverage_first_day
                else None
            ),
            "coverage_last_day": (
                basis.coverage_last_day.isoformat()
                if basis.coverage_last_day
                else None
            ),
            "note": window_note,
        },
        "unattributed_refund_lines": basis.unattributed_refund_lines,
        "warnings": list(basis.warnings),
        "computed_at": basis.calculated_at.isoformat(),
        "rubric_version": basis.rubric_version,
        "currency": {
            "display": basis.display_currency,
            "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
        },
        "ad_system_roi": {
            "actual_formula": "广告归因GMV ÷ 广告实际消耗",
            "max_ad_spend_formula": "预计净结算收入 − 同范围采购成本 − 结算外必要成本",
            "breakeven_formula": "广告归因GMV ÷ 最大可承受广告费",
            "settlement_basis": "已结算实际到账 + 未结算净额估算",
            "cost_scope": "与收入同日期范围的售出货本 + 海外取消全损货本",
            "spend_basis": "mixed_real_cost 广告实际消耗；不混入广告赠金",
            "advertising_credit_status": "not_available_separately",
            "additional_costs_status": "not_modeled",
            "warning": (
                "当前未结构化录入退货运费、提现费、汇兑损失、包装耗材等"
                "结算外必要成本；广告系统保本ROI仅为已知成本下限估算"
            ),
        },
    }


def _overview_payload(result, scope: ProfitScope, fee_rate: Decimal | None) -> dict:
    return {
        "items": [_row_payload(row) for row in result.items],
        "total": result.total,
        "totals": _totals_payload(result.totals),
        "meta": _meta_payload(result, scope, fee_rate),
    }


def _parse_fee_rate(raw: str | None) -> Decimal | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except Exception as exc:
        raise HTTPException(status_code=422, detail="fee_rate must be a decimal") from exc
    if not value.is_finite():
        raise HTTPException(status_code=422, detail="fee_rate must be a finite decimal")
    if value.copy_abs() > Decimal("1e6"):
        raise HTTPException(
            status_code=422,
            detail="fee_rate out of reasonable range (|fee_rate| <= 1e6)",
        )
    if value < 0:
        raise HTTPException(status_code=422, detail="fee_rate must be >= 0")
    return value


def _scope(
    shop_pk,
    include_all,
    w_start,
    w_end,
    spu_ids: tuple[str, ...] | None = None,
) -> ProfitScope:
    try:
        return ProfitScope(
            shop_pk=shop_pk,
            start_date=w_start,
            end_date=w_end,
            include_inactive=include_all,
            spu_ids=spu_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _fx_error(request: Request) -> JSONResponse:
    return error_response(
        status=503,
        code="FX_RATE_UNAVAILABLE",
        message="汇率数据缺失，无法计算结果",
        request_id=request_id(request),
        retryable=True,
    )


roi_router = APIRouter(prefix="/v2/analytics", tags=["analytics"])


@roi_router.get("/spu-roi")
def list_spu_roi(
    request: Request,
    sess: Session = Depends(get_session),  # noqa: B008
    q: str | None = Query(default=None, max_length=200),
    spu_ids: str | None = Query(default=None, max_length=4096),
    sort: str = Query(default="roi_real"),
    order: str = Query(default="asc"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_all: bool = Query(default=False),
    shop_pk: int | None = Query(default=None, ge=1),
    fee_rate: str | None = Query(default=None, max_length=20),
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> Any:
    try:
        sort_field = SortField(sort)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"sort must be one of {tuple(field.value for field in SortField)}",
        ) from exc
    fee_value = _parse_fee_rate(fee_rate)
    try:
        parsed_spu_ids = parse_spu_ids(spu_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if parsed_spu_ids is not None and shop_pk is None:
        raise HTTPException(status_code=422, detail="shop_pk is required with spu_ids")
    if parsed_spu_ids is not None and q is not None and q.strip():
        raise HTTPException(status_code=422, detail="q and spu_ids cannot be combined")
    scope = _scope(shop_pk, include_all, w_start, w_end, parsed_spu_ids)
    view = RowView(
        search=q or None,
        sort=sort_field,
        direction=SortDirection.DESC if order == "desc" else SortDirection.ASC,
        limit=limit,
        offset=offset,
    )
    try:
        result = read_legacy_overview(
            sess,
            scope=scope,
            view=view,
            fee_rate=fee_value,
        )
    except FxRateUnavailable:
        return _fx_error(request)
    return _overview_payload(result, scope, fee_value)


drilldown_router = APIRouter(prefix="/v2/analytics/spu-roi", tags=["analytics"])


def _evidence_payload(explanation, kind: EvidenceKind, scope: ProfitScope) -> dict:
    rows = explanation.evidence.rows.get(kind, ())
    payload = {
        "spu_pk": explanation.result.spu_pk,
        kind.value: [_wire_value(kind.value, dict(row)) for row in rows],
        "meta": {
            "rubric_version": explanation.basis.rubric_version,
            "computed_at": explanation.basis.calculated_at.isoformat(),
            "currency": {
                "display": explanation.basis.display_currency,
                "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
            },
        },
    }
    if kind is EvidenceKind.ORDERS:
        payload.update(
            {
                "spu_id": explanation.result.spu_id,
                "window": {
                    "w_start": scope.start_date.isoformat() if scope.start_date else None,
                    "w_end": scope.end_date.isoformat() if scope.end_date else None,
                },
            }
        )
        payload["meta"]["orders_truncated"] = len(rows) >= 500
    if kind is EvidenceKind.ADS:
        payload["meta"]["note"] = "广告域与日期窗口同语义裁剪"
    return payload


def _read_evidence(
    request: Request,
    sess: Session,
    spu_pk: int,
    kind: EvidenceKind,
    w_start: date | None,
    w_end: date | None,
) -> dict | JSONResponse:
    scope = _scope(None, True, w_start, w_end)
    try:
        explanation = explain_spu(
            sess,
            scope=scope,
            spu_pk=spu_pk,
            evidence=EvidenceRequest(frozenset({kind})),
        )
    except FxRateUnavailable:
        return _fx_error(request)
    except SpuNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _evidence_payload(explanation, kind, scope)


@drilldown_router.get("/{spu_pk:int}/orders")
def list_orders(
    request: Request,
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> Any:
    return _read_evidence(request, sess, spu_pk, EvidenceKind.ORDERS, w_start, w_end)


@drilldown_router.get("/{spu_pk:int}/settlements")
def list_settlements(
    request: Request,
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> Any:
    return _read_evidence(
        request, sess, spu_pk, EvidenceKind.SETTLEMENTS, w_start, w_end
    )


@drilldown_router.get("/{spu_pk:int}/cases")
def list_cases(
    request: Request,
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> Any:
    return _read_evidence(request, sess, spu_pk, EvidenceKind.CASES, w_start, w_end)


@drilldown_router.get("/{spu_pk:int}/ads")
def list_ads(
    request: Request,
    spu_pk: int,
    sess: Session = Depends(get_session),  # noqa: B008
    w_start: date | None = Query(default=None),  # noqa: B008
    w_end: date | None = Query(default=None),  # noqa: B008
) -> Any:
    return _read_evidence(request, sess, spu_pk, EvidenceKind.ADS, w_start, w_end)
