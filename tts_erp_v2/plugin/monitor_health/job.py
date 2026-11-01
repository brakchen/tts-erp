"""15s system-job component; bounded UTF-8 chunks under one total dispatch deadline.

The caller must commit immediately after run returns (the system JobSpec runner
already does). Locks fence intake and acknowledgements until that commit. A
crash after remote acceptance but before DB commit remains inherently ambiguous;
Feishu bots offer no idempotency key. Ordinary concurrent scans are serialized.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from tts_erp_v2.db.models.monitor_health import MonitorHealth
from tts_erp_v2.jobs.runner import run_job
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
    with run_job(session, job_name="plugin.monitor_health") as job:
        result = _run(session, now=now)
        job.rows_total = result["sent"] + result["pending"]
        job.rows_inserted = result["sent"]
        job.extra = result
        return result


def _run(session: Session, *, now: datetime | None = None) -> dict:
    timestamp = (now or datetime.now(UTC)).timestamp()
    owner = str(uuid4())
    pending: list[tuple[float, str, str, MonitorHealth]] = []
    with session.begin():
        for row in locked_current(session):
            if row.state.get("_dispatch", {}).get("expires_at", 0) > timestamp:
                continue
            state, notices = plan(row.state, timestamp)
            identities = {}
            for notice in notices:
                step = notice.split(":")[0]
                since = state["faults"][step][
                    "recovery_since" if notice.endswith(":recovery") else "since"
                ]
                identities[notice] = since
                pending.append((since, row.shop_id, notice, row))
            if notices:
                state["_dispatch"] = {
                    "owner": owner,
                    "expires_at": timestamp
                    + NOTIFICATION_BUDGET_SECONDS
                    + SCAN_SECONDS,
                    "notices": identities,
                    "recovered": {},
                }
            else:
                state.pop("_dispatch", None)
            row.state = state
        # The webhook is a plaintext value snapshot; no DB connection survives
        # into the notification sender.
        target = webhook(session) if pending else ""
        session.flush()
        for _, _, _, row in pending:
            if row in session:
                session.expunge(row)
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
            accepted = send(target, batches)
        except DeliveryError as error:
            if error.accepted_batches == 0:
                _complete(session, owner=owner, pending=pending, delivered=set())
                raise
            accepted = error.accepted_batches
            delivery_error = "monitor health Feishu notification failed"
        if not 0 <= accepted <= len(batches):
            raise RuntimeError("monitor health invalid accepted batch count")
    _complete(
        session,
        owner=owner,
        pending=pending,
        delivered={
            (row.shop_id, notice)
            for batch in members[:accepted]
            for row, notice in batch
        },
    )
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


def _complete(
    session: Session,
    *,
    owner: str,
    pending: list[tuple[float, str, str, MonitorHealth]],
    delivered: set[tuple[str, str]],
) -> None:
    by_shop: dict[str, list[tuple[float, str]]] = {}
    for since, shop_id, notice, _ in pending:
        by_shop.setdefault(shop_id, []).append((since, notice))
    for shop_id, notices in by_shop.items():
        with session.begin():
            row = session.get(
                MonitorHealth, shop_id, with_for_update=True, populate_existing=True
            )
            if row is None:
                continue
            state = deepcopy(row.state)
            dispatch = state.get("_dispatch", {})
            if dispatch.get("owner") != owner:
                continue
            for since, notice in notices:
                if (shop_id, notice) not in delivered:
                    continue
                step, kind = notice.split(":")
                fault = state["faults"].get(step)
                if kind == "recovery":
                    if fault and fault.get("recovery_since") == since:
                        state = acknowledge(state, [notice])
                    continue
                recovered_at = dispatch.get("recovered", {}).get(step)
                if recovered_at is not None:
                    if fault is None:
                        fault = {"since": since, "active": False, "notified": True}
                        state["faults"][step] = fault
                    fault.update(recovery_pending=True, recovery_since=recovered_at)
                if fault and fault["since"] == since:
                    fault["notified"] = True
            state.pop("_dispatch", None)
            row.state = state
