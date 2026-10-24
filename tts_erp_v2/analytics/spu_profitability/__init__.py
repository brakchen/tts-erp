"""Deep module for v10 SPU profitability.

Public interface:

* :func:`read_overview` returns SPU rows and globally deduplicated overview totals.
* :func:`explain_spu` recomputes one SPU and its evidence in one new consistent
  snapshot.

PostgreSQL queries, normalization, v10 formulas, and fallback disclosure remain
behind this seam.  HTTP adapters must not reach into private implementation files.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import _implementation
from tts_erp_v2.analytics.spu_profitability._price_stats import (
    PriceCoverage,
    PriceCoverageStatus,
    PriceMetric,
    PriceSource,
    PriceStatsOverview,
    SpuPriceStats,
    is_price_sort_field,
)
from tts_erp_v2.analytics.spu_profitability._projection import ProjectionPolicy
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.analytics.spu_profitability._types import (
    REFUND_RATE_ALERT_THRESHOLD,
    ActivitySelection,
    EvidenceKind,
    EvidenceRequest,
    ExactIdsSelection,
    FocusedSelection,
    FormulaStatus,
    FxBasis,
    FxRateUnavailable,
    PriceFxUnavailable,
    ProfitabilityBasis,
    ProfitabilityError,
    ProfitabilityEvidence,
    ProfitabilityOverview,
    ProfitabilityTotals,
    ProfitScope,
    ProjectionBasis,
    ReportingTimezoneUnavailable,
    RowView,
    SnapshotIsolationUnavailable,
    SortDirection,
    SortField,
    SpuNotFound,
    SpuProfitability,
    SpuProfitExplanation,
    SpuSelection,
)
from tts_erp_v2.db.models import (
    Case,
    CaseLine,
    SalesOrder,
    SalesOrderLine,
    SettlementComponent,
    SettlementTransaction,
)

__all__ = [
    "REFUND_RATE_ALERT_THRESHOLD",
    "ActivitySelection",
    "EvidenceKind",
    "EvidenceRequest",
    "ExactIdsSelection",
    "FocusedSelection",
    "FormulaStatus",
    "FxBasis",
    "FxRateUnavailable",
    "PriceCoverage",
    "PriceCoverageStatus",
    "PriceFxUnavailable",
    "PriceMetric",
    "PriceSource",
    "PriceStatsOverview",
    "ProfitScope",
    "ProfitabilityBasis",
    "ProfitabilityError",
    "ProfitabilityEvidence",
    "ProfitabilityOverview",
    "ProfitabilityTotals",
    "ProjectionBasis",
    "ProjectionPolicy",
    "ReportingTimezoneUnavailable",
    "RowView",
    "SnapshotIsolationUnavailable",
    "SortDirection",
    "SortField",
    "SpuNotFound",
    "SpuPriceStats",
    "SpuProfitExplanation",
    "SpuProfitability",
    "SpuSelection",
    "consistent_read_snapshot",
    "count_orders_in_snapshot",
    "explain_spu",
    "is_price_sort_field",
    "missing_completed_refund_amounts_in_snapshot",
    "read_overview",
    "read_overview_in_snapshot",
    "reporting_timezone",
]


def _read_overview_in_snapshot(
    session: Session,
    *,
    scope: ProfitScope,
    view: RowView,
    calculated_at,
    only_spu_pk: int | None = None,
    fee_rate: Decimal | None = None,
    legacy_include_all: bool | None = None,
    projection_lookback_days: int = 30,
) -> ProfitabilityOverview:
    return _implementation._query_spu_roi(
        session,
        q=view.search or None,
        shop_pk=scope.shop_pk,
        selection=scope.effective_selection,
        active_only=(
            legacy_include_all
            if legacy_include_all is not None
            else not scope.include_inactive
        ),
        include_without_activity=(
            legacy_include_all
            if legacy_include_all is not None
            else scope.include_inactive
        ),
        sort_field=view.sort.value,
        ascending=view.direction is SortDirection.ASC,
        limit=view.limit,
        offset=view.offset,
        fee_rate=fee_rate,
        calculated_at=calculated_at,
        only_spu_pk=only_spu_pk,
        w_start=scope.start_date,
        w_end=scope.end_date,
        projection_lookback_days=projection_lookback_days,
    )


def reporting_timezone(session: Session, *, shop_pk: int) -> tzinfo:
    """Resolve the canonical shop-local reporting calendar."""
    return _implementation._shop_reporting_timezone(session, shop_pk=shop_pk)


def read_overview_in_snapshot(
    session: Session,
    *,
    scope: ProfitScope,
    view: RowView,
    calculated_at: datetime,
) -> ProfitabilityOverview:
    """Compose reads inside ``consistent_read_snapshot`` with its shared timestamp."""
    return _read_overview_in_snapshot(
        session, scope=scope, view=view, calculated_at=calculated_at
    )


def count_orders_in_snapshot(
    session: Session,
    *,
    shop_pk: int,
    spu_pks: tuple[int, ...],
    start_date: date,
    end_date: date,
) -> int:
    """Canonical paid-order DISTINCT over the full relation, never batch totals."""
    start, end = _implementation._window_dates(
        start_date, end_date, reporting_timezone(session, shop_pk=shop_pk)
    )
    row = (
        session.execute(
            _implementation._SQL_ROI_ORDER_SCOPE,
            {
                "pks": list(spu_pks),
                "paid_statuses": list(_implementation.PAID_SALES_ORDER_STATUSES),
                "ac": _implementation._TRACK_ACTION_CODE_OVERSEAS,
                "ws": start,
                "we": end,
            },
        )
        .mappings()
        .one()
    )
    return int(row["order_count"])


def missing_completed_refund_amounts_in_snapshot(
    session: Session,
    *,
    shop_pk: int,
    spu_pks: tuple[int, ...],
    start_date: date,
    end_date: date,
) -> set[int]:
    """Find SPUs whose current ROI/profit need missing unsettled refund money.

    Match ``_SQL_ROI_REFUND_SCOPE`` case/order/window criteria and
    ``_SQL_ROI_SALES``'s unsettled-refund cohort. Settled current metrics use
    actual net receipts and known refund quantity, not case refund amounts;
    amount-dependent projection classifications are not current metrics.
    """
    if not spu_pks:
        return set()
    start, end = _implementation._window_dates(
        start_date, end_date, reporting_timezone(session, shop_pk=shop_pk)
    )
    event_time = func.coalesce(SalesOrder.order_time, SalesOrder.paid_at)
    # Component.amount is NOT NULL: presence is exactly SUM(amount) IS NOT NULL
    # in _SQL_ROI_SALES, even for explicit zero or offsetting settlements.
    has_settlement = (
        select(SettlementTransaction.id)
        .join(
            SettlementComponent,
            SettlementComponent.transaction_id == SettlementTransaction.id,
        )
        .where(
            SettlementTransaction.order_pk == SalesOrder.id,
            SettlementComponent.component_code == "SETTLEMENT",
        )
        .exists()
    )
    return {
        pk
        for pk in session.scalars(
            select(SalesOrderLine.spu_pk)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_pk)
            .join(CaseLine, CaseLine.sales_order_line_id == SalesOrderLine.id)
            .join(Case, Case.id == CaseLine.case_id)
            .where(
                SalesOrderLine.spu_pk.in_(spu_pks),
                SalesOrder.shop_pk == shop_pk,
                SalesOrder.status.in_(_implementation.PAID_SALES_ORDER_STATUSES),
                Case.status.in_(_implementation._CASE_COMPLETED_STATUSES),
                Case.case_type.in_(("REFUND_ONLY", "RETURN_AND_REFUND")),
                CaseLine.refund_amount.is_(None),
                ~has_settlement,
                event_time >= start,
                event_time < end,
            )
            .distinct()
        ).all()
        if pk is not None
    }


def read_overview(
    session: Session,
    *,
    scope: ProfitScope,
    view: RowView,
    projection_lookback_days: int = 30,
) -> ProfitabilityOverview:
    """Read one typed v10 profitability overview from a consistent snapshot."""

    with consistent_read_snapshot(session) as calculated_at:
        return _read_overview_in_snapshot(
            session,
            scope=scope,
            view=view,
            calculated_at=calculated_at,
            projection_lookback_days=projection_lookback_days,
        )


def _typed_evidence_rows(
    kind: EvidenceKind, payload: dict[str, Any]
) -> tuple[dict, ...]:
    """Keep native PostgreSQL/domain values; the HTTP adapter formats them."""
    return tuple(dict(row) for row in payload.get(kind.value, []))


def explain_spu(
    session: Session,
    *,
    scope: ProfitScope,
    spu_pk: int,
    evidence: EvidenceRequest,
) -> SpuProfitExplanation:
    """Recompute one SPU and requested evidence in one consistent snapshot."""

    if spu_pk < 1:
        raise ValueError("spu_pk must be >= 1")
    with consistent_read_snapshot(session) as calculated_at:
        overview = _read_overview_in_snapshot(
            session,
            scope=scope,
            view=RowView(limit=1),
            calculated_at=calculated_at,
            only_spu_pk=spu_pk,
        )
        if not overview.items:
            raise SpuNotFound(f"spu_pk {spu_pk} not found in profitability scope")

        readers = {
            EvidenceKind.ORDERS: _implementation._detail_orders,
            EvidenceKind.SETTLEMENTS: _implementation._detail_settlements,
            EvidenceKind.CASES: _implementation._detail_cases,
            EvidenceKind.ADS: _implementation._detail_ads,
        }
        rows = {}
        for kind in evidence.kinds:
            payload = readers[kind](
                session,
                spu_pk,
                scope.start_date,
                scope.end_date,
            )
            rows[kind] = _typed_evidence_rows(kind, payload)
        return SpuProfitExplanation(
            result=overview.items[0],
            evidence=ProfitabilityEvidence.from_rows(rows),
            basis=overview.basis,
        )
