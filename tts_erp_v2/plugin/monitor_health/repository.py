"""Serialized intake and scanning on the same current-state row."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.monitor_health import MonitorHealth
from tts_erp_v2.plugin.monitor_health.domain import receive


def ingest(
    session: Session,
    shop_id: str,
    shop_name: str,
    step: str,
    status: str,
    *,
    now: datetime | None = None,
) -> None:
    # Capture arrival before waiting for a scan's lock/network request. A late
    # lock acquisition must not overwrite a more recently received run intent.
    arrived = (now or datetime.now(UTC)).timestamp()
    initial = receive(None, step, status, arrived)
    initial["received_at"] = arrived
    session.execute(
        insert(MonitorHealth)
        .values(shop_id=shop_id, shop_name=shop_name, state=initial)
        .on_conflict_do_nothing(index_elements=[MonitorHealth.shop_id])
    )
    row = session.scalars(
        select(MonitorHealth)
        .where(MonitorHealth.shop_id == shop_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if arrived < row.state["received_at"]:
        return
    state = receive(row.state, step, status, arrived)
    state["received_at"] = arrived
    row.shop_name, row.state = shop_name, state
    session.flush()


def locked_current(session: Session) -> list[MonitorHealth]:
    # Locks stay held through the single bounded digest and the caller's commit.
    # Concurrent scanners skip them; intake waits then reads the latest row.
    return list(
        session.scalars(
            select(MonitorHealth)
            .order_by(MonitorHealth.shop_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
