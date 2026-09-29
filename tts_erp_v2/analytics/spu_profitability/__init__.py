"""Deep module for v10 SPU profitability.

Public interface:

* :func:`read_overview` returns SPU rows and globally deduplicated overview totals.
* :func:`explain_spu` recomputes one SPU and its evidence in one new consistent
  snapshot.

PostgreSQL queries, normalization, v10 formulas, and fallback disclosure remain
behind this seam.  HTTP adapters must not reach into private implementation files.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import _implementation
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.analytics.spu_profitability._types import (
    ActivitySelection,
    EvidenceKind,
    EvidenceRequest,
    ExactIdsSelection,
    FocusedSelection,
    FormulaStatus,
    FxBasis,
    FxRateUnavailable,
    ProfitabilityBasis,
    ProfitabilityError,
    ProfitabilityEvidence,
    ProfitabilityOverview,
    ProfitabilityTotals,
    ProfitScope,
    RowView,
    SnapshotIsolationUnavailable,
    SortDirection,
    SortField,
    SpuNotFound,
    SpuProfitability,
    SpuProfitExplanation,
    SpuSelection,
)

__all__ = [
    "ActivitySelection",
    "EvidenceKind",
    "EvidenceRequest",
    "ExactIdsSelection",
    "FocusedSelection",
    "FormulaStatus",
    "FxBasis",
    "FxRateUnavailable",
    "ProfitScope",
    "ProfitabilityBasis",
    "ProfitabilityError",
    "ProfitabilityEvidence",
    "ProfitabilityOverview",
    "ProfitabilityTotals",
    "RowView",
    "SnapshotIsolationUnavailable",
    "SortDirection",
    "SortField",
    "SpuNotFound",
    "SpuProfitExplanation",
    "SpuProfitability",
    "SpuSelection",
    "explain_spu",
    "read_overview",
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
    )


def read_overview(
    session: Session,
    *,
    scope: ProfitScope,
    view: RowView,
) -> ProfitabilityOverview:
    """Read one typed v10 profitability overview from a consistent snapshot."""

    with consistent_read_snapshot(session) as calculated_at:
        return _read_overview_in_snapshot(
            session,
            scope=scope,
            view=view,
            calculated_at=calculated_at,
        )


def _typed_evidence_rows(kind: EvidenceKind, payload: dict[str, Any]) -> tuple[dict, ...]:
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
