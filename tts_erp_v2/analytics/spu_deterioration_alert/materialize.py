"""Controlled materialization seam for alert snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import AlertConfig
from tts_erp_v2.analytics.spu_deterioration_alert.policy import AlertDecision
from tts_erp_v2.db.models import SpuDeteriorationAlert


def payload_hash(payload: dict) -> str:
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def replace_anchor(
    session: Session,
    *,
    rows: Iterable[SpuDeteriorationAlert],
    anchor_date: date,
) -> int:
    """Atomically replace one anchor; callers commit or rollback as one unit.

    No destructive-script guard: this is a job-owned derived snapshot, the
    DELETE is scoped to a single ``anchor_date``, and the next scheduled run
    recomputes the same rows from source facts (AGENTS.md section 3).
    """
    materialized = list(rows)
    session.execute(
        delete(SpuDeteriorationAlert).where(
            SpuDeteriorationAlert.anchor_date == anchor_date
        )
    )
    session.add_all(materialized)
    session.flush()
    return len(materialized)


def stale(anchor_date: date, *, today: date | None = None) -> bool:
    # Freshness budget, deliberately one day looser than the T-1 anchor offset in
    # read.build_materialized_rows. With anchor = T-1, accepting T-2 here keeps a
    # snapshot fresh for the remainder of its run day plus one full day of slack
    # instead of flipping to stale the moment the UTC date rolls over.
    return anchor_date < (today or datetime.now(UTC).date()) - timedelta(days=2)


def decision_row(
    *,
    decision: AlertDecision,
    shop_pk: int,
    spu_pk: int,
    anchor_date: date,
    window_days: int,
    layer: str,
    config: AlertConfig,
    config_hash: str,
    calculated_at: datetime,
) -> SpuDeteriorationAlert:
    return SpuDeteriorationAlert(
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        anchor_date=anchor_date,
        window_days=window_days,
        layer=layer,
        severity=decision.severity.value,
        state=decision.state.value,
        sample_status=decision.sample_status.value,
        prior_roi=decision.previous.roi_real,
        current_roi=decision.current.roi_real,
        roi_decline=decision.roi_decline,
        prior_net_profit_cny=decision.previous.net_profit_cny,
        current_net_profit_cny=decision.current.net_profit_cny,
        net_profit_decline=decision.net_profit_decline,
        previous_spend_cny=decision.previous.spend_cny,
        current_spend_cny=decision.current.spend_cny,
        previous_order_count=decision.previous.order_count,
        current_order_count=decision.current.order_count,
        previous_ad_orders=decision.previous.ad_orders,
        current_ad_orders=decision.current.ad_orders,
        effective_config_source=config.source,
        effective_config_version=config.version,
        effective_config_updated_at=config.updated_at,
        effective_config_updated_by=config.updated_by,
        config_payload_hash=config_hash,
        basis_calculated_at=calculated_at,
    )
