"""Compatibility hooks for the historical ``spu-roi`` HTTP contract.

New callers must use the package-level ``read_overview``/``explain_spu`` seam.
This file exists only while the legacy ``fee_rate`` query parameter remains on the
stable endpoint.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability import _read_overview_in_snapshot
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.analytics.spu_profitability._types import (
    ProfitabilityOverview,
    ProfitScope,
    RowView,
)


def read_legacy_overview(
    session: Session,
    *,
    scope: ProfitScope,
    view: RowView,
    fee_rate: Decimal | None,
) -> ProfitabilityOverview:
    with consistent_read_snapshot(session) as calculated_at:
        return _read_overview_in_snapshot(
            session,
            scope=scope,
            view=view,
            calculated_at=calculated_at,
            fee_rate=fee_rate,
            legacy_include_all=scope.include_inactive,
        )
