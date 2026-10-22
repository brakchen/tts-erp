"""Persistence primitives for the plugin status domain (design §10).

Plain functions over one SQLAlchemy ``Session`` — the repository is not a
separate object because there is exactly one caller shape: a request handler or
the sync-worker tick that already owns a session and the instance row lock.

Everything that mutates the *current decision* happens here or in
:mod:`tts_erp_v2.plugin.status.service` behind the same instance lock, so
reports, operator commands and the sweep can never each keep their own notion
of "the" current decision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import and_, false, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.plugin_status import (
    StatusDecision,
    StatusInstance,
    StatusNotificationOutbox,
    StatusReport,
)
from tts_erp_v2.plugin.status.contracts import (
    Connectivity,
)
from tts_erp_v2.plugin.status.contracts import (
    StatusDecision as StatusDecisionContract,
)
from tts_erp_v2.plugin.status.policy import HEARTBEAT_STALE_SECONDS, RecoveryHistory

#: The one validator that owns the decision wire shape, reused here so a stored
#: row is re-read through exactly the checks a fresh decision passes.
_DECISION_ADAPTER: TypeAdapter[StatusDecisionContract] = TypeAdapter(
    StatusDecisionContract
)

__all__ = [
    "claim_due_notifications",
    "current_decision",
    "decision_by_admin_request",
    "decision_wire",
    "enqueue_notification",
    "episode_recovery_history",
    "find_report_by_event",
    "find_report_by_seq",
    "insert_decision",
    "insert_report",
    "lock_instance",
    "mark_delivered",
    "mark_pending_manual",
    "mark_retry",
    "read_instances",
    "reclaim_expired_leases",
    "wire_timestamp",
]


def wire_timestamp(value: datetime) -> str:
    """Render an instant in the one timestamp spelling the plugin accepts.

    The contract's ``UtcTimestamp`` validator only accepts ISO-8601 *text*, so
    every value read back out of the database has to be re-rendered before it
    can re-enter the wire model. It lives here, the lowest layer, because both
    the repository and the service need exactly this one definition.
    """
    # Keep the exact persisted action boundary; whole-second inputs stay unchanged.
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ── Instance locking ──────────────────────────────────────────────────


def lock_instance(session: Session, instance_id: UUID) -> StatusInstance | None:
    """Take the instance row lock every writer shares (§10 step 1).

    ``FOR UPDATE`` rather than an optimistic read: the report path, the
    operator command and the sweep all mutate the same projection, and a lost
    update there would silently resurrect a resolved episode or double-issue a
    recovery.

    ``populate_existing`` is essential, not an optimisation.
    ``authenticate_report`` already loads this instance into the session's
    identity map and the caller keeps that object; a plain re-``SELECT`` of the
    same primary key acquires the database lock but hands back the *already
    loaded* object, so its attributes stay as they were at authentication time.
    A concurrent operator HOLD or the one-time advertiser binding committed in
    between would then be invisible: the write path would reuse a stale revision
    (colliding with the one already committed) and re-run a compare-and-set that
    had already happened. Forcing a refresh makes taking the lock and reading the
    authoritative row a single act.
    """
    return session.execute(
        select(StatusInstance)
        .where(StatusInstance.id == instance_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def read_instances(
    session: Session,
    *,
    shop_pk: int | None = None,
    plugin_id: str | None = None,
    connectivity: Connectivity | None = None,
    now: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[StatusInstance], int]:
    """Return a filtered page of instances plus the unpaginated total.

    ``connectivity`` is applied **in SQL, to both the page and the count**, and
    before ``limit``/``offset``. It is a derived label rather than a stored
    column, so it becomes a ``last_seen_at`` predicate; filtering after the page
    was taken would drop matching rows from the page while still reporting the
    unfiltered total (§9.4).
    """
    filters = []
    if shop_pk is not None:
        filters.append(StatusInstance.shop_pk == shop_pk)
    if plugin_id is not None:
        filters.append(StatusInstance.plugin_id == plugin_id)
    if connectivity is not None:
        filters.append(_connectivity_filter(connectivity, now=now))
    total = session.execute(
        select(StatusInstance.id).where(*filters).order_by(StatusInstance.id)
    ).all()
    rows = (
        session.execute(
            select(StatusInstance)
            .where(*filters)
            .order_by(StatusInstance.created_at.desc(), StatusInstance.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), len(total)


def _connectivity_filter(connectivity: Connectivity, *, now: datetime | None) -> Any:
    """SQL predicate equivalent to ``derive_connectivity`` (§4.2)."""
    if now is None:
        return false()
    cutoff = now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    seen = StatusInstance.last_seen_at
    if connectivity is Connectivity.UNKNOWN:
        return seen.is_(None)
    if connectivity is Connectivity.ONLINE:
        return and_(seen.is_not(None), seen >= cutoff)
    return and_(seen.is_not(None), seen < cutoff)


# ── Reports ───────────────────────────────────────────────────────────


def find_report_by_event(
    session: Session, instance_id: UUID, event_id: UUID
) -> StatusReport | None:
    return session.execute(
        select(StatusReport).where(
            StatusReport.instance_id == instance_id,
            StatusReport.event_id == event_id,
        )
    ).scalar_one_or_none()


def find_report_by_seq(
    session: Session, instance_id: UUID, report_seq: int
) -> StatusReport | None:
    return session.execute(
        select(StatusReport).where(
            StatusReport.instance_id == instance_id,
            StatusReport.report_seq == report_seq,
        )
    ).scalar_one_or_none()


def insert_report(
    session: Session,
    *,
    instance_id: UUID,
    event_id: UUID,
    report_seq: int,
    payload_hash: str,
    observed_at: datetime,
    received_at: datetime,
    event_type: str,
    reason_code: str,
    incident_id: UUID | None,
    binding_epoch: UUID | None,
    validated_payload: dict[str, Any],
    receipt: dict[str, Any],
    response_decision: dict[str, Any] | None,
) -> StatusReport:
    row = StatusReport(
        id=uuid.uuid4(),
        instance_id=instance_id,
        event_id=event_id,
        report_seq=report_seq,
        payload_hash=payload_hash,
        observed_at=observed_at,
        received_at=received_at,
        event_type=event_type,
        reason_code=reason_code,
        incident_id=incident_id,
        binding_epoch=binding_epoch,
        validated_payload=validated_payload,
        receipt=receipt,
        response_decision=response_decision,
    )
    session.add(row)
    session.flush()
    return row


# ── Decisions ─────────────────────────────────────────────────────────


def current_decision(session: Session, instance_id: UUID) -> StatusDecision | None:
    """The highest issued revision, automatic or manual."""
    return session.execute(
        select(StatusDecision)
        .where(StatusDecision.instance_id == instance_id)
        .order_by(StatusDecision.revision.desc())
        .limit(1)
    ).scalar_one_or_none()


def decision_by_admin_request(
    session: Session, instance_id: UUID, admin_request_id: str
) -> StatusDecision | None:
    return session.execute(
        select(StatusDecision).where(
            StatusDecision.instance_id == instance_id,
            StatusDecision.admin_request_id == admin_request_id,
        )
    ).scalar_one_or_none()


def episode_recovery_history(
    session: Session, instance_id: UUID, incident_id: UUID | None
) -> RecoveryHistory:
    """Issued-recovery facts for one episode, counted from the decision log.

    Counting *issued* rows rather than confirmed executions is deliberate: a
    command whose response was lost still consumed budget, so a lost reply can
    never buy extra attempts (§11).
    """
    if incident_id is None:
        return RecoveryHistory()
    rows = (
        session.execute(
            select(StatusDecision)
            .where(
                StatusDecision.instance_id == instance_id,
                StatusDecision.incident_id == incident_id,
                StatusDecision.action == "recover_binding",
            )
            .order_by(StatusDecision.issued_at)
        )
        .scalars()
        .all()
    )
    if not rows:
        return RecoveryHistory()
    return RecoveryHistory(
        recover_decisions_issued=len(rows),
        last_recover_issued_at=rows[-1].issued_at,
    )


def insert_decision(
    session: Session,
    *,
    instance_id: UUID,
    revision: int,
    incident_id: UUID,
    expected_binding_epoch: UUID,
    action: str,
    parameters: dict[str, Any],
    issued_at: datetime,
    not_before: datetime,
    expires_at: datetime,
    policy_version: str,
    source: str,
    source_report_id: UUID | None = None,
    admin_request_id: str | None = None,
) -> StatusDecision:
    row = StatusDecision(
        id=uuid.uuid4(),
        instance_id=instance_id,
        revision=revision,
        incident_id=incident_id,
        expected_binding_epoch=expected_binding_epoch,
        action=action,
        parameters=parameters,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        policy_version=policy_version,
        source=source,
        source_report_id=source_report_id,
        admin_request_id=admin_request_id,
        delivery_state="issued",
    )
    session.add(row)
    session.flush()
    return row


def decision_wire(row: StatusDecision) -> StatusDecisionContract:
    """Rebuild the frozen wire decision from its stored row.

    The stored row — not the live projection — is the source of what the plugin
    was told, so a replay can never hand out a silently different instruction.
    """
    return _DECISION_ADAPTER.validate_python(
        {
            "decisionId": str(row.id),
            "revision": str(row.revision),
            "incidentId": str(row.incident_id),
            "expectedBindingEpoch": str(row.expected_binding_epoch),
            "notBefore": wire_timestamp(row.not_before),
            "expiresAt": wire_timestamp(row.expires_at),
            "action": row.action,
            "parameters": dict(row.parameters or {}),
        }
    )


# ── Notification outbox ───────────────────────────────────────────────


def enqueue_notification(
    session: Session,
    *,
    instance_id: UUID,
    incident_key: str,
    notification_kind: str,
    payload: dict[str, Any],
    now: datetime,
) -> bool:
    """Add one notification unless its dedupe key already exists.

    Returns whether a row was inserted, so the caller can tell "this report is
    the first of its kind" from "we already said so" without re-querying.
    """
    dedupe_key = f"{instance_id}:{incident_key}:{notification_kind}"
    existing = session.execute(
        select(StatusNotificationOutbox.id).where(
            StatusNotificationOutbox.dedupe_key == dedupe_key
        )
    ).first()
    if existing is not None:
        return False
    session.execute(
        pg_insert(StatusNotificationOutbox)
        .values(
            id=uuid.uuid4(),
            instance_id=instance_id,
            incident_key=incident_key,
            notification_kind=notification_kind,
            dedupe_key=dedupe_key,
            payload=payload,
            state="pending",
            attempt_count=0,
            next_attempt_at=now,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    return True


@dataclass(frozen=True, slots=True)
class NotificationClaim:
    """Owned lease snapshot, immune to ORM expiry and another worker's refresh."""

    id: UUID
    instance_id: UUID
    notification_kind: str
    payload: dict[str, Any]
    attempt_count: int
    lease_until: datetime


def claim_due_notifications(
    session: Session,
    *,
    now: datetime,
    limit: int,
    lease_seconds: int,
) -> list[NotificationClaim]:
    """Take a short lease on deliverable notifications.

    A row is claimable when it is ``pending`` and due, or when a previous
    claimer's lease expired — the lease, not the attempt counter, is what makes
    a crashed sender's work retryable without a sweeper of its own.
    """

    if lease_seconds <= 0:
        raise ValueError("notification lease must be positive")
    lease_until = now + timedelta(seconds=lease_seconds)
    rows = (
        session.execute(
            select(StatusNotificationOutbox)
            .where(
                StatusNotificationOutbox.state == "pending",
                StatusNotificationOutbox.next_attempt_at <= now,
            )
            .order_by(StatusNotificationOutbox.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.state = "claimed"
        row.lease_until = lease_until
    session.flush()
    return [
        NotificationClaim(
            id=row.id,
            instance_id=row.instance_id,
            notification_kind=row.notification_kind,
            payload=dict(row.payload),
            attempt_count=row.attempt_count,
            lease_until=lease_until,
        )
        for row in rows
    ]


def reclaim_expired_leases(session: Session, *, now: datetime) -> int:
    """Return rows whose claimer died back to ``pending``."""
    rows = (
        session.execute(
            select(StatusNotificationOutbox)
            .where(
                StatusNotificationOutbox.state == "claimed",
                StatusNotificationOutbox.lease_until.is_not(None),
                StatusNotificationOutbox.lease_until <= now,
            )
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.state = "pending"
        row.lease_until = None
    session.flush()
    return len(rows)


def _settle_notification(
    session: Session,
    claim: NotificationClaim,
    *,
    now: datetime,
    values: dict[str, Any],
) -> bool:
    # SQLAlchemy 2 conditional UPDATE/RETURNING owns the CAS; no ORM row can
    # refresh itself into a successor's lease. Reclaim requires now >= old lease,
    # so positive lease intervals make the successor's lease strictly greater.
    result = session.execute(
        update(StatusNotificationOutbox)
        .where(
            StatusNotificationOutbox.id == claim.id,
            StatusNotificationOutbox.state == "claimed",
            StatusNotificationOutbox.lease_until == claim.lease_until,
            StatusNotificationOutbox.lease_until > now,
        )
        .values(**values, lease_until=None)
        .returning(StatusNotificationOutbox.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


def mark_delivered(session: Session, row: NotificationClaim, *, now: datetime) -> bool:
    return _settle_notification(
        session,
        row,
        now=now,
        values={"state": "delivered", "delivered_at": now, "last_error_code": None},
    )


def mark_retry(
    session: Session,
    row: NotificationClaim,
    *,
    now: datetime,
    next_attempt_at: datetime,
    error_code: str,
) -> bool:
    return _settle_notification(
        session,
        row,
        now=now,
        values={
            "state": "pending",
            "attempt_count": StatusNotificationOutbox.attempt_count + 1,
            "next_attempt_at": next_attempt_at,
            "last_error_code": error_code,
        },
    )


def mark_pending_manual(
    session: Session,
    row: NotificationClaim,
    *,
    error_code: str,
    now: datetime | None = None,
) -> bool:
    """A configuration/signing failure is not retryable by waiting."""
    return _settle_notification(
        session,
        row,
        now=now if now is not None else datetime.now(UTC),
        values={"state": "pending_manual", "last_error_code": error_code},
    )
