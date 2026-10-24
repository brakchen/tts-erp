"""15s system-job component; bounded UTF-8 chunks under one total dispatch deadline.

The caller must commit immediately after run returns (the system JobSpec runner
already does). Locks fence intake and acknowledgements until that commit. A
crash after remote acceptance but before DB commit remains inherently ambiguous;
Feishu bots offer no idempotency key. Ordinary concurrent scans are serialized.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from tts_erp_v2.db.models.monitor_health import MonitorHealth
from tts_erp_v2.plugin.monitor_health.domain import (
    OFFLINE_SECONDS,
    SCAN_SECONDS,
    acknowledge,
    plan,
)
from tts_erp_v2.plugin.monitor_health.notifications import (
    DIGEST_BYTES,
    NOTIFICATION_BUDGET_SECONDS,
    DeliveryError,
    webhook,
)
from tts_erp_v2.plugin.monitor_health.notifications import (
    send_many as send,
)
from tts_erp_v2.plugin.monitor_health.repository import locked_current


def _line(row: MonitorHealth, notice: str) -> str:
    # Keep display compact, single-line, and UTF-8-byte-accounted below.
    name = " ".join(row.shop_name.split())[:40]
    label = f"{name} ({row.shop_id})" if name else row.shop_id
    reason = (
        "失联"
        if notice == "heartbeat:fault"
        else "恢复"
        if notice.endswith(":recovery")
        else "连续180秒无实际进展"
    )
    return f"{label} {notice} {reason}"


def run(session: Session, *, now: datetime | None = None) -> dict:
    timestamp = (now or datetime.now(UTC)).timestamp()
    pending: list[tuple[float, str, str, MonitorHealth]] = []
    for row in locked_current(session):
        row.state, notices = plan(row.state, timestamp)
        for notice in notices:
            step = notice.split(":")[0]
            pending.append(
                (
                    (
                        row.state["faults"][step]["recovery_since"]
                        if notice.endswith(":recovery")
                        else row.state["faults"][step]["since"]
                    ),
                    row.shop_id,
                    notice,
                    row,
                )
            )
    pending.sort(key=lambda entry: entry[:3])
    batches: list[list[str]] = []
    members: list[list[tuple[MonitorHealth, str]]] = []
    size = 0
    for _, _, notice, row in pending:
        line = _line(row, notice)
        cost = len(line.encode("utf-8"))
        if not batches or size + 1 + cost > DIGEST_BYTES:
            batches.append([])
            members.append([])
            size = 0
        batches[-1].append(line)
        members[-1].append((row, notice))
        size += cost + (1 if len(batches[-1]) > 1 else 0)
    accepted = 0
    delivery_error = None
    if batches:
        try:
            accepted = send(webhook(session), batches)
        except DeliveryError as error:
            if error.accepted_batches == 0:
                raise
            accepted = error.accepted_batches
            delivery_error = "monitor health Feishu notification failed"
        if not 0 <= accepted <= len(batches):
            raise RuntimeError("monitor health invalid accepted batch count")
        for batch in members[:accepted]:
            for row, notice in batch:
                row.state = acknowledge(row.state, [notice])
    delivered = sum(len(batch) for batch in members[:accepted])
    deadline_at_risk = any(
        notice == "heartbeat:fault"
        and timestamp
        + (SCAN_SECONDS if index >= delivered else 0)
        + NOTIFICATION_BUDGET_SECONDS
        > since + (180 - OFFLINE_SECONDS)
        for index, (since, _, notice, _) in enumerate(pending)
    )
    if delivery_error:
        logging.getLogger(__name__).error(
            "monitor health dispatch degraded: accepted_batches=%d pending_notices=%d",
            accepted,
            len(pending) - delivered,
        )
    session.flush()
    return {
        "sent": delivered,
        "pending": len(pending) - delivered,
        "degraded": delivery_error is not None or delivered < len(pending),
        "deadlineAtRisk": deadline_at_risk,
        "digestBytes": sum(
            len("\n".join(lines).encode("utf-8")) for lines in batches[:accepted]
        ),
        "deliveryError": delivery_error,
    }
