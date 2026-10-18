"""Lifecycle sweep and outbox delivery for the plugin status domain (§11).

Runs from the existing sync-worker on a fixed interval — there is no in-API
timer and no per-request background task (§11). The tick has three phases with
deliberate transaction boundaries:

1. reclaim notifications whose claimer's lease expired;
2. mark newly-stale instances and queue their one notification per outage;
3. claim due notifications, send them *outside* a transaction, then record the
   outcome.

Phases 1–2 and each phase-3 result commit on their own so a crash mid-delivery
never loses the claim (the lease makes it retryable) and never holds a database
lock while the webhook is being called.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from tts_erp_v2.plugin.status import notifications, repository, service

log = logging.getLogger("tts_erp_v2.plugin.status.scheduler")

__all__ = [
    "JOB_INTERVAL_SECONDS",
    "JOB_MODULE_PATH",
    "JOB_NAME",
    "run_scheduled",
    "sweep_no_candidate",
    "sweep_stale_instances",
]

#: Job name the sync-worker registry should register (§11, 60 s cadence). The
#: registry entry itself lives in ``tts_erp_v2.sync_worker.scheduler``, which is
#: a shared integration seam, so it is added there rather than duplicated here.
JOB_NAME = "plugin.status.lifecycle"
JOB_MODULE_PATH = "tts_erp_v2.plugin.status.scheduler"
JOB_INTERVAL_SECONDS = 60

DEFAULT_BATCH_SIZE = 50
DEFAULT_LEASE_SECONDS = 60


def sweep_no_candidate(session: Session, *, now: datetime) -> int:
    """Queue the §11 notice for instances stuck without any candidate.

    Eligibility is re-checked under the instance lock: the id query is a
    snapshot, so an operator could have disabled the instance between the read
    and here — and the report itself may say the *local* monitor switch is off.
    Both go through the one predicate the report path used to update the
    condition, so a monitor the user switched off is never told it lost its
    page (§11 disabled priority).
    """
    queued = 0
    for instance_id in service.no_candidate_instance_ids(session, now=now):
        instance = repository.lock_instance(session, instance_id)
        if instance is None:  # deleted between the id read and the lock
            continue
        if service.instance_is_locally_disabled(instance):
            continue
        if service.enqueue_no_candidate_notification(
            session, instance=instance, now=now
        ):
            queued += 1
    return queued


def sweep_stale_instances(session: Session, *, now: datetime) -> int:
    """Queue one notification per instance that just went stale.

    Returns how many instances *newly* crossed into stale, not how many are
    stale now: the operator-facing number is "new outages", and a sweep that
    reported the total would look like a growing failure count (§11).
    """
    newly_stale = 0
    for instance_id in service.stale_instance_ids(session, now=now):
        instance = repository.lock_instance(session, instance_id)
        if instance is None:  # deleted between the id read and the lock
            continue
        if instance.stale_since is not None:
            # Already inside this outage; the dedupe key keeps it to one message.
            continue
        service.enqueue_stale_notification(session, instance=instance, now=now)
        newly_stale += 1
    return newly_stale


def _resolve_sender(
    session: Session, sender: notifications.Sender | None
) -> notifications.Sender:
    """Build the default sender with **no** transaction left open.

    Resolving the webhook URL reads ``config.runtime_config_secrets``, which
    would otherwise leave a transaction open across the HTTP call (§11:
    delivery happens outside the transaction). The read is committed/closed here
    so the send starts clean.
    """
    if sender is not None:
        return sender
    url = notifications.resolve_webhook_url(session)
    session.rollback()
    return notifications.HttpWebhookSender(url=url)


def run_scheduled(
    session: Session,
    *,
    now: datetime | None = None,
    sender: notifications.Sender | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    """One sweep tick. Callable as a sync-worker system job with ``(session)``."""
    tick_now = now or datetime.now(UTC)

    reclaimed = repository.reclaim_expired_leases(session, now=tick_now)
    newly_stale = sweep_stale_instances(session, now=tick_now)
    no_candidate = sweep_no_candidate(session, now=tick_now)
    session.commit()

    claimed = repository.claim_due_notifications(
        session, now=tick_now, limit=limit, lease_seconds=lease_seconds
    )
    session.commit()
    if not claimed:
        return {
            "reclaimedLeases": reclaimed,
            "staleInstances": newly_stale,
            "noCandidateNotices": no_candidate,
            "attempted": 0,
            "delivered": 0,
            "retrying": 0,
            "pendingManual": 0,
        }

    deliver = _resolve_sender(session, sender)
    # Materialise every message before the first send, so building them cannot
    # hold a transaction open either; the rows are already leased and committed.
    messages = [
        notifications.render_message(
            kind=row.notification_kind,
            instance_id=str(row.instance_id),
            payload=dict(row.payload),
            attempt=int(row.attempt_count) + 1,
        )
        for row in claimed
    ]
    session.rollback()
    delivered = retrying = pending_manual = 0

    for row, message in zip(claimed, messages, strict=True):
        try:
            outcome = deliver(message)
        except Exception:
            log.exception(
                "[plugin-status] notification sender raised kind=%s",
                row.notification_kind,
            )
            outcome = notifications.RETRY

        if outcome == notifications.DELIVERED:
            repository.mark_delivered(session, row, now=tick_now)
            delivered += 1
        elif outcome == notifications.PENDING_MANUAL:
            repository.mark_pending_manual(session, row, error_code=outcome)
            pending_manual += 1
        else:
            delay = notifications.retry_delay_seconds(int(row.attempt_count) + 1)
            repository.mark_retry(
                session,
                row,
                now=tick_now,
                next_attempt_at=tick_now + timedelta(seconds=delay),
                error_code=outcome,
            )
            retrying += 1
        # One commit per message: the outcome is durable before the next
        # network call, so a crash cannot replay a notification already sent.
        session.commit()

    log.info(
        "[plugin-status] tick stale=%d attempted=%d delivered=%d retry=%d manual=%d",
        newly_stale,
        len(claimed),
        delivered,
        retrying,
        pending_manual,
    )
    return {
        "reclaimedLeases": reclaimed,
        "staleInstances": newly_stale,
        "noCandidateNotices": no_candidate,
        "attempted": len(claimed),
        "delivered": delivered,
        "retrying": retrying,
        "pendingManual": pending_manual,
    }
