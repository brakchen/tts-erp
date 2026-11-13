"""Evidence-only reads share the profitability snapshot and visibility rules."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import _implementation
from tts_erp_v2.analytics.spu_profitability._selection import resolve_selected_spus
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.analytics.spu_profitability._types import (
    EvidenceKind,
    EvidenceRequest,
    ProfitabilityEvidence,
    ProfitScope,
    SpuEvidenceRead,
    SpuNotFound,
)


def read_evidence(
    session: Session,
    *,
    spu_pk: int,
    evidence: EvidenceRequest,
    start_date: date | None = None,
    end_date: date | None = None,
) -> SpuEvidenceRead:
    """Read one authorized SPU's evidence without computing profitability.

    Inactive products and products without activity remain visible. Date-only
    bounds and failure precedence match the full explanation's legacy HTTP use.
    """
    if spu_pk < 1:
        raise ValueError("spu_pk must be >= 1")
    scope = ProfitScope(
        shop_pk=None,
        include_inactive=True,
        start_date=start_date,
        end_date=end_date,
    )
    with consistent_read_snapshot(session) as calculated_at:
        # FX failure historically precedes missing/unauthorized SPU and timezone
        # failures, including evidence sources whose current rows are empty.
        fx = _implementation._resolve_fx_basis(session)
        if start_date is not None or end_date is not None:
            _implementation._shop_reporting_timezone(
                session, shop_pk=None, only_spu_pk=spu_pk
            )
        catalog = resolve_selected_spus(
            session,
            selection=scope.effective_selection,
            shop_pk=None,
            catalog_search=None,
            active_only=False,
            only_spu_pk=spu_pk,
        )
        if not catalog:
            raise SpuNotFound(f"spu_pk {spu_pk} not found in profitability scope")

        readers = {
            EvidenceKind.ORDERS: _implementation._detail_orders,
            EvidenceKind.SETTLEMENTS: _implementation._detail_settlements,
            EvidenceKind.CASES: _implementation._detail_cases,
            EvidenceKind.ADS: _implementation._detail_ads,
        }
        rows = {}
        for kind in evidence.kinds:
            payload = readers[kind](session, spu_pk, start_date, end_date)
            rows[kind] = tuple(dict(row) for row in payload.get(kind.value, []))
        return SpuEvidenceRead(
            spu_pk=spu_pk,
            spu_id=catalog[0]["spu_id"],
            evidence=ProfitabilityEvidence.from_rows(rows),
            calculated_at=calculated_at,
            fx=fx,
        )
