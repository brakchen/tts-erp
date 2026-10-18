"""Domain service for managed plugin status (design §9–§11).

One transaction, one instance row lock, one owner of "the current decision".
The report path, the operator command and the lifecycle sweep all come through
here, so a report can never race a manual hold into a second current decision.

Ordering inside :func:`ingest_report` is the design's (§10):

1. authenticate the dedicated credential and its bound instance, lock the row;
2. reject content/event conflicts, then archive the validated report;
3. advance the projection only for a greater ``reportSeq`` — a late report is
   history, never current state;
4. match any reported decision results, then run the pure policy on the
   current facts and select or create one decision;
5. freeze the receipt/decision onto the report row and queue any notification.

Steps 2–5 share the caller's transaction: the handler commits after this
returns, so a failure anywhere in here is a 5xx and leaves no partial fact
(§8 — never "200 with a lost write").
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.access import hash_key
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.plugin_status import (
    StatusDecision,
    StatusInstance,
    StatusReport,
)
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.plugin.status import repository
from tts_erp_v2.plugin.status.contracts import (
    MAX_DECISION_WINDOW_SECONDS,
    RECOVER_CAPTURE_TIMEOUT_SECONDS,
    SCHEMA_VERSION,
    AdminDecisionRequest,
    BindingPhase,
    Capability,
    Connectivity,
    DecisionAction,
    EventType,
    ExecutionPhase,
    ExecutionReasonCode,
    InstallationScope,
    InstanceRegistrationRequest,
    ManualDecisionAction,
    ReasonCode,
    ShopScope,
    StatusResponse,
)
from tts_erp_v2.plugin.status.contracts import (
    StatusReport as StatusReportContract,
)
from tts_erp_v2.plugin.status.policy import (
    EPISODE_ESCALATION_SECONDS,
    HEARTBEAT_STALE_SECONDS,
    MAX_RECOVER_ATTEMPTS_PER_EPISODE,
    MIN_RECOVER_INTERVAL_SECONDS,
    POLICY_VERSION,
    ManualLatch,
    RecoveryHistory,
    StatusPolicyDecision,
    StatusPolicyInput,
    derive_connectivity,
    evaluate_status_policy,
)
from tts_erp_v2.plugin.status.repository import wire_timestamp

__all__ = [
    "MAX_FUTURE_OBSERVATION_SECONDS",
    "NOTIFY_EPISODE_NO_CANDIDATE",
    "NOTIFY_EPISODE_RECOVERED",
    "NOTIFY_EPISODE_REQUIRES_USER",
    "NOTIFY_INSTANCE_STALE",
    "NO_CANDIDATE_NOTIFY_SECONDS",
    "PLUGIN_STATUS_PURPOSE",
    "REPORT_AFTER_SECONDS",
    "STATUS_KEY_ROLE",
    "AuthenticatedInstance",
    "StatusServiceError",
    "authenticate_report",
    "disable_instance",
    "enable_instance",
    "enqueue_no_candidate_notification",
    "enqueue_stale_notification",
    "ingest_report",
    "instance_detail",
    "instance_is_locally_disabled",
    "list_instances",
    "manual_decision",
    "no_candidate_instance_ids",
    "purpose_allows_status",
    "register_instance",
    "set_instance_enabled",
    "stale_instance_ids",
    "wire_timestamp",
]

#: §3/§7.1: the next-heartbeat suggestion. It never delays an invalidation or a
#: readiness event, and it does not drive the ad scan cadence.
REPORT_AFTER_SECONDS = 60

STATUS_KEY_PREFIX = "ttserp_st_"

#: §9.1 role floor for this protocol, and the only thing this module asserts
#: about a credential's authorisation. Defined once so the minting path and the
#: checking path cannot drift apart. It is *not* the canonical ``purpose`` label:
#: that belongs to the ``access``/``security`` owner.
STATUS_KEY_ROLE = "readwrite"

#: Canonical ``security.api_keys.purpose`` value for this protocol. The column
#: itself belongs to the access/security owner (monitor-config lane, migration
#: 0070) and is *not* in this tree yet, so nothing here creates a purpose store
#: or registry — this is the one literal the integration agrees on.
PLUGIN_STATUS_PURPOSE = "plugin_status"


def purpose_allows_status(purpose: str | None) -> bool:
    """Whether a key's canonical purpose permits status reporting.

    Takes the raw column value so it is testable without the column. It is
    **fail-closed**: an absent/unreadable purpose means the canonical contract
    (access/security owner, migration 0070) is not available, and unavailable is
    "no" — never "assume general, the instance binding will carry it". A
    ``general`` or ``monitor_config`` key is refused even when it is bound to an
    instance, because binding proves *which* instance, never *what the key may
    be used for* (§9.1).
    """
    return str(purpose) == PLUGIN_STATUS_PURPOSE


def _read_purpose(session: Session, api_key_id: int) -> str | None:
    """Read ``security.api_keys.purpose`` for one key, or ``None`` if unreadable.

    Deliberately a direct column read rather than an ORM attribute: the column
    belongs to the access/security owner and may not exist in this tree yet, and
    this seam must not add it. A missing column (or an unknown key) yields
    ``None``, which :func:`purpose_allows_status` refuses, so the boundary is
    closed until the canonical contract is applied.
    """
    try:
        return session.execute(
            text("SELECT purpose FROM security.api_keys WHERE id = :key_id"),
            {"key_id": api_key_id},
        ).scalar_one_or_none()
    except SQLAlchemyError:
        # The transaction is now aborted by the failed statement; the caller
        # turns this refusal into a rollback, so it is not recovered here.
        return None


def _set_purpose(session: Session, api_key_id: int, purpose: str) -> bool:
    """Write the canonical purpose. False means the column is unavailable.

    The statement either applies or raises; there is no partial success to
    inspect, so no rowcount is read. On failure the transaction is already
    aborted and the caller rolls the whole provisioning back, which is the
    point: a half-minted unrestricted key must never survive.
    """
    try:
        session.execute(
            text("UPDATE security.api_keys SET purpose = :purpose WHERE id = :key_id"),
            {"purpose": purpose, "key_id": api_key_id},
        )
    except SQLAlchemyError:
        return False
    return True


#: §11 notification kinds this domain owns. It is not a general messaging
#: platform: only instance/binding lifecycle facts belong here.
NOTIFY_EPISODE_REQUIRES_USER = "episode_requires_user"
NOTIFY_EPISODE_RECOVERED = "episode_recovered"
NOTIFY_INSTANCE_STALE = "instance_stale"
NOTIFY_EPISODE_NO_CANDIDATE = "episode_no_candidate"

#: §11: an online instance that keeps reporting NO_CANDIDATE gets one
#: notification once that has persisted this long. Defined here rather than in
#: the pure policy module because it is a *notification* threshold, not a
#: decision input — no decision depends on it.
NO_CANDIDATE_NOTIFY_SECONDS = 180

#: §6.2: an observation more than five minutes in the future is rejected. This
#: is deliberately looser than ``MAX_FUTURE_SKEW_SECONDS`` (5 s): small clock
#: skew is tolerated when deciding whether evidence is *actionable*, while a
#: report that claims to have been observed far ahead of the server is refused
#: outright rather than stored.
MAX_FUTURE_OBSERVATION_SECONDS = 300

#: Only these operator actions latch an episode (§9.5). ``recover_binding``
#: releases the latch; ``resume_monitoring`` is not an operator action at all.
_LATCHING_ADMIN_ACTIONS = frozenset(
    {ManualDecisionAction.HOLD, ManualDecisionAction.REQUIRE_USER}
)

#: §9.1: an instance only ever receives the page commands its registration
#: granted. Mapping is by action because that is what has to be gated, not the
#: capability name the plugin happens to advertise in its report.
_CAPABILITY_FOR_ACTION: dict[DecisionAction, Capability] = {
    DecisionAction.RECOVER_BINDING: Capability.BINDING_RECOVER_V1,
    DecisionAction.RESUME_MONITORING: Capability.MONITOR_RESUME_V1,
}

_TRANSPORT_REASONS = frozenset(
    {ReasonCode.TRANSPORT_TIMEOUT, ReasonCode.REQUEST_FAILED}
)
#: Reasons that may legitimately persist across reports. ``NONE`` is silence,
#: ``NO_CANDIDATE`` and the transport reasons are conditions that persist until
#: something contradicts them. Any other reason is an explicit, *different*
#: current observation and therefore supersedes the previous condition.
_PERSISTING_REASONS = frozenset({ReasonCode.NONE, ReasonCode.NO_CANDIDATE}) | (
    _TRANSPORT_REASONS
)
_TERMINAL_OUTCOMES = frozenset({"applied", "rejected", "failed", "unknown"})


class StatusServiceError(Exception):
    """A refusal that maps to one HTTP status and one protocol error code.

    Messages are safe by construction: they name the rule that refused, never
    the payload, the credential or another shop's identity (§9.8).
    """

    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AuthenticatedInstance:
    instance: StatusInstance
    api_key: ApiKey


# ── Authentication (§9.1/§9.2) ────────────────────────────────────────


def authenticate_report(
    session: Session, *, bearer_token: str | None
) -> AuthenticatedInstance:
    """Resolve the status credential to the one instance it was provisioned for.

    **This is not a purpose owner.** It creates no credential store and no
    purpose registry: it reads the existing ``security.api_keys`` row through the
    shared ``access.hash_key``, and applies two deny-only gates on top. The
    canonical ``purpose`` contract stays with the ``access``/``security`` owner
    (monitor-config lane); nothing here grants what that contract would refuse.

    The two gates are deliberately distinct concepts:

    * *instance binding* — §4.1 ties a status key to exactly one instance, so a
      credential that is not that instance's key is refused here and nowhere
      else. This is this module's own fact, not a purpose judgement.
    * *role floor* — §9.1 requires ``role=readwrite`` for this protocol. This is
      a floor copied from the approved contract, not a substitute for the
      canonical purpose label.

    Independent of ``TTS_ERP_AUTH_MODE`` on purpose. The middleware hands out a
    bypass grant in ``off``/``shadow``, so a boundary that trusted it would hand
    recovery permission to an anonymous caller exactly when the deployment
    posture is weakest (§9.2).
    """
    if not bearer_token:
        raise StatusServiceError(401, "INVALID_CREDENTIAL", "missing status credential")
    digest = hash_key(bearer_token)
    api_key = session.execute(
        select(ApiKey).where(ApiKey.key_hash == digest)
    ).scalar_one_or_none()
    # 401 is only for "there is no usable credential here": unknown hash or a
    # revoked/disabled one. A valid key that is not this instance's key (or not
    # at the §9.1 role floor) is a *refusal*, and §8 keeps the two apart so the
    # plugin stops retrying and asks for re-provisioning instead.
    if api_key is None or api_key.status != "active":
        raise StatusServiceError(
            401,
            "INVALID_CREDENTIAL",
            "invalid, disabled or expired status credential",
        )
    instance = session.execute(
        select(StatusInstance).where(StatusInstance.status_api_key_id == api_key.id)
    ).scalar_one_or_none()
    if (
        instance is None
        or api_key.role != STATUS_KEY_ROLE
        or not purpose_allows_status(_read_purpose(session, api_key.id))
    ):
        raise StatusServiceError(
            403,
            "PURPOSE_DENIED",
            "this credential is not provisioned for plugin status reporting",
        )
    if not instance.enabled:
        raise StatusServiceError(403, "INSTANCE_DENIED", "instance is disabled")
    return AuthenticatedInstance(instance=instance, api_key=api_key)


# ── Provisioning and reads (§9.4) ─────────────────────────────────────


def _resolve_shop_pk(
    session: Session, scope: ShopScope | InstallationScope
) -> int | None:
    """Map a seller id to the server's own shop row, or refuse.

    A client may never choose ``shopPk``, and an unmapped or ambiguous seller is
    refused rather than auto-registered — a typo must not create a shop.
    """
    if not isinstance(scope, ShopScope):
        return None
    rows = session.execute(
        select(ChannelAccount.id).where(
            ChannelAccount.platform == "tiktok",
            ChannelAccount.shop_id == scope.seller_id,
        )
    ).all()
    if not rows:
        raise StatusServiceError(
            403, "SHOP_NOT_MAPPED", "no commerce shop is registered for this seller id"
        )
    if len(rows) > 1:
        raise StatusServiceError(
            403, "SHOP_AMBIGUOUS", "more than one shop matches this seller id"
        )
    return int(rows[0][0])


def _mint_status_key(session: Session, *, plugin_id: str) -> tuple[ApiKey, str]:
    """Create the dedicated readwrite key and return it with its plaintext.

    Reuses ``security.api_keys``' random-key + SHA-256 + 16-char-prefix shape
    rather than introducing a second credential store (§9.1). The plaintext is
    handed straight back to the provisioning caller and never stored.

    The key is minted **already purpose-scoped**: ``purpose`` is written before
    this call returns, so there is never a moment where an ordinary readwrite key
    exists for a managed instance (§9.1). If the canonical column is unavailable
    the whole insert is rolled back and provisioning is refused — a half-minted
    unrestricted key is worse than no key.
    """
    plaintext = f"{STATUS_KEY_PREFIX}{secrets.token_urlsafe(24)}"
    row = ApiKey(
        key_hash=hash_key(plaintext),
        key_prefix=plaintext[:16],
        name=f"plugin_status:{plugin_id}",
        role=STATUS_KEY_ROLE,
        status="active",
    )
    session.add(row)
    session.flush()
    if not _set_purpose(session, row.id, PLUGIN_STATUS_PURPOSE):
        raise StatusServiceError(
            503,
            "PURPOSE_UNAVAILABLE",
            "api key purpose is unavailable; provisioning is refused until it exists",
            retryable=True,
        )
    return row, plaintext


def _key_summary(
    api_key: ApiKey | None, *, plaintext: str | None = None
) -> dict[str, Any] | None:
    if api_key is None:
        return None
    summary: dict[str, Any] = {
        "keyId": api_key.id,
        "keyPrefix": api_key.key_prefix,
        "role": api_key.role,
        "status": api_key.status,
    }
    # Present exactly once, in the provisioning response only (§9.8).
    if plaintext is not None:
        summary["value"] = plaintext
    return summary


def register_instance(
    session: Session,
    *,
    command: InstanceRegistrationRequest,
    now: datetime,
) -> dict[str, Any]:
    """Provision one instance and mint its dedicated status key."""
    shop_pk = _resolve_shop_pk(session, command.scope)
    api_key, plaintext = _mint_status_key(session, plugin_id=command.plugin_id.value)
    scope_wire = command.scope.model_dump(mode="json", by_alias=True)
    instance = StatusInstance(
        id=uuid.uuid4(),
        plugin_id=command.plugin_id.value,
        shop_pk=shop_pk,
        scope=scope_wire,
        capabilities=[cap.value for cap in command.capabilities],
        status_api_key_id=api_key.id,
        enabled=True,
        current_decision_revision=0,
        consecutive_transport_failures=0,
    )
    session.add(instance)
    session.flush()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "instanceId": str(instance.id),
        "pluginId": command.plugin_id.value,
        "scope": scope_wire,
        "shopPk": shop_pk,
        "capabilities": list(instance.capabilities),
        **_instance_summary(instance, now=now),
        "statusApiKey": _key_summary(api_key, plaintext=plaintext),
    }


def _instance_summary(instance: StatusInstance, *, now: datetime) -> dict[str, Any]:
    connectivity = derive_connectivity(instance.last_seen_at, now)
    return {
        "instanceId": str(instance.id),
        "pluginId": instance.plugin_id,
        "shopPk": instance.shop_pk,
        "scope": instance.scope,
        "capabilities": list(instance.capabilities or []),
        "enabled": instance.enabled,
        "connectivity": connectivity.value,
        "lastSeenAt": (
            wire_timestamp(instance.last_seen_at) if instance.last_seen_at else None
        ),
        "latestReportSeq": (
            str(instance.latest_report_seq) if instance.latest_report_seq else None
        ),
        "latestObservedAt": (
            wire_timestamp(instance.latest_observed_at)
            if instance.latest_observed_at
            else None
        ),
        "currentIncidentId": (
            str(instance.current_incident_id) if instance.current_incident_id else None
        ),
        "currentDecisionRevision": str(instance.current_decision_revision),
        "manualLatch": (
            instance.manual_latch_action
            if instance.current_incident_id and instance.manual_latch_action
            else None
        ),
        "createdAt": wire_timestamp(instance.created_at),
    }


def list_instances(
    session: Session,
    *,
    now: datetime,
    limit: int = 50,
    offset: int = 0,
    shop_pk: int | None = None,
    plugin_id: str | None = None,
    connectivity: Connectivity | None = None,
) -> dict[str, Any]:
    """Filtered, paginated instance list.

    The derived ``connectivity`` label is pushed into the query so the page and
    the ``total`` describe the *same* filtered set; a post-page filter would both
    drop matching rows and report an unfiltered count (§9.4).
    """
    rows, total = repository.read_instances(
        session,
        shop_pk=shop_pk,
        plugin_id=plugin_id,
        connectivity=connectivity,
        now=now,
        limit=limit,
        offset=offset,
    )
    items = [_instance_summary(row, now=now) for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def instance_detail(
    session: Session, *, instance_id: UUID, now: datetime
) -> dict[str, Any]:
    """Instance plus its recent reports and the current decision."""
    instance = session.get(StatusInstance, instance_id)
    if instance is None:
        raise StatusServiceError(404, "INSTANCE_NOT_FOUND", "unknown instance")
    reports = (
        session.execute(
            select(StatusReport)
            .where(StatusReport.instance_id == instance_id)
            .order_by(StatusReport.received_at.desc(), StatusReport.report_seq.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    decision = repository.current_decision(session, instance_id)
    return {
        **_instance_summary(instance, now=now),
        "latestState": instance.latest_state,
        "statusApiKey": _key_summary(session.get(ApiKey, instance.status_api_key_id)),
        "currentDecision": (
            repository.decision_wire(decision).model_dump(mode="json", by_alias=True)
            if decision is not None
            else None
        ),
        "recentReports": [
            {
                "eventId": str(row.event_id),
                "reportSeq": str(row.report_seq),
                "eventType": row.event_type,
                "reasonCode": row.reason_code,
                "observedAt": wire_timestamp(row.observed_at),
                "receivedAt": wire_timestamp(row.received_at),
                "incidentId": str(row.incident_id) if row.incident_id else None,
            }
            for row in reports
        ],
    }


def set_instance_enabled(
    session: Session, *, instance_id: UUID, enabled: bool, now: datetime
) -> dict[str, Any]:
    instance = repository.lock_instance(session, instance_id)
    if instance is None:
        raise StatusServiceError(404, "INSTANCE_NOT_FOUND", "unknown instance")
    instance.enabled = enabled
    session.flush()
    return _instance_summary(instance, now=now)


def disable_instance(
    session: Session, *, instance_id: UUID, now: datetime
) -> dict[str, Any]:
    return set_instance_enabled(
        session, instance_id=instance_id, enabled=False, now=now
    )


def enable_instance(
    session: Session, *, instance_id: UUID, now: datetime
) -> dict[str, Any]:
    return set_instance_enabled(session, instance_id=instance_id, enabled=True, now=now)


# ── Report ingest (§6, §8, §10) ───────────────────────────────────────


def _authorize_scope(instance: StatusInstance, report: StatusReportContract) -> bool:
    """Check the report against the registered target.

    Returns whether the advertiser must be locked now. The seller is never
    negotiable. A registered-null advertiser may be filled exactly once, and
    only by a *verified ready* report — an unverified identity must not pin or
    claim a target (§4.1).
    """
    if report.plugin_id.value != instance.plugin_id:
        raise StatusServiceError(
            403, "SCOPE_DENIED", "plugin id does not match the provisioned instance"
        )
    scope = instance.scope or {}
    if scope.get("kind") != report.scope.kind:
        raise StatusServiceError(
            403, "SCOPE_DENIED", "scope kind does not match the provisioned target"
        )
    if not isinstance(report.scope, ShopScope):
        return False
    if scope.get("sellerId") != report.scope.seller_id:
        raise StatusServiceError(
            403, "SCOPE_DENIED", "seller id does not match the provisioned target"
        )
    registered = scope.get("advertiserId")
    if registered is not None:
        if report.scope.advertiser_id != registered:
            raise StatusServiceError(
                403,
                "SCOPE_DENIED",
                "advertiser id does not match the provisioned target",
            )
        return False
    if report.scope.advertiser_id is None:
        return False
    verified_ready = (
        report.binding is not None
        and report.binding.phase is BindingPhase.READY
        and report.binding.identity_verified
    )
    if not verified_ready:
        raise StatusServiceError(
            403,
            "SCOPE_DENIED",
            "an unverified identity may not set the target advertiser",
        )
    return True


def _advance_episode(
    instance: StatusInstance, report: StatusReportContract, *, now: datetime
) -> None:
    """Advance current-episode and transport state from a *current* report.

    Only ever called for a report that advances the projection (§10 step 3). An
    archival report is history: letting it replace ``current_incident_id`` would
    let a delayed low-sequence report from an old episode silently drop an
    operator latch and reset the recovery budget, while the response still says
    ``projectionUpdated: false``.

    A *different* incident id is a new episode and drops any latch that belonged
    to the old one — that is how an operator hold stops applying (§9.5). The
    exception is the episode this instance just resolved: a report that still
    carries its id is late history, and reopening on it would resurrect an
    incident the plugin has already been told is over (§4.1).
    """
    reported = report.incident_id
    if (
        reported is not None
        and reported != instance.current_incident_id
        and reported != instance.resolved_incident_id
    ):
        instance.current_incident_id = reported
        instance.current_incident_started_at = now
        instance.manual_latch_action = None
        instance.manual_latch_at = None
    if (
        _contradicts_failure(report)
        or _supersedes_failure(report)
        or _report_is_disabled(instance, report)
    ):
        # Contradictory evidence (verified ready / running), a different
        # explicit reason (USER_DISABLED, LOGIN_REQUIRED, TAB_CLOSED …), or the
        # user having switched the monitor off: all end the previous unresolved
        # condition. A monitor that is off by choice must not be reported as
        # having lost its page, nor as repeatedly failing business requests.
        instance.consecutive_transport_failures = 0
        instance.no_candidate_since = None
    elif report.reason_code in _TRANSPORT_REASONS:
        if not _repeats_sticky_reason(instance, report):
            instance.consecutive_transport_failures = (
                int(instance.consecutive_transport_failures or 0) + 1
            )
    elif report.reason_code is ReasonCode.NO_CANDIDATE:
        if instance.no_candidate_since is None:
            instance.no_candidate_since = now
    # Otherwise the report is silence (NONE) or a repeat of the same sticky
    # reason: both unresolved facts are preserved untouched.


def _is_disabled_state(
    *,
    instance_enabled: bool,
    monitor_enabled: bool | None,
    execution_phase: str | None,
    execution_reason: str | None,
) -> bool:
    """One predicate for "the user turned this monitor off".

    Four *independent* signals mean the same thing, and §11's disabled priority
    has to agree on all of them:

    * the instance was disabled server-side;
    * the report says the **local monitor switch** is off;
    * the report's execution is ``disabled``;
    * the report's execution reason is ``USER_DISABLED``.

    Judging only the reported ``reasonCode`` let a locally-disabled instance
    both start and keep a NO_CANDIDATE condition — and then receive a fault
    notice about a page it was never going to look for. ``monitor_enabled=None``
    means the plugin has no monitor capability block at all (an
    ``installation``-scope plugin), which is not evidence of being disabled.
    """
    return (
        not instance_enabled
        or monitor_enabled is False
        or execution_phase == ExecutionPhase.DISABLED
        or execution_reason == ExecutionReasonCode.USER_DISABLED
    )


def _report_is_disabled(instance: StatusInstance, report: StatusReportContract) -> bool:
    monitor = report.capability_state.monitor
    return _is_disabled_state(
        instance_enabled=bool(instance.enabled),
        monitor_enabled=monitor.monitor_enabled if monitor is not None else None,
        execution_phase=report.execution.phase,
        execution_reason=report.execution.reason_code,
    )


def instance_is_locally_disabled(instance: StatusInstance) -> bool:
    """The same predicate, evaluated against the stored latest state.

    Used by the sweep so notification eligibility reuses exactly the rule the
    report path used to *update* the condition — otherwise the two can drift and
    a monitor that was already disabled still gets told about a missing page.
    """
    state = instance.latest_state or {}
    execution = state.get("execution") or {}
    monitor = (state.get("capabilityState") or {}).get("monitor") or {}
    return _is_disabled_state(
        instance_enabled=bool(instance.enabled),
        monitor_enabled=monitor.get("monitorEnabled"),
        execution_phase=execution.get("phase"),
        execution_reason=execution.get("reasonCode"),
    )


def _repeats_sticky_reason(
    instance: StatusInstance, report: StatusReportContract
) -> bool:
    """True when this report only re-sends the previous observation's reason.

    The controller keeps its sticky reason across heartbeats. Counting every
    transport-failure report would let *one* business failure plus its own
    heartbeat satisfy §11's two-failure recovery threshold, so only a new
    event counts as a new business failure observation. ``latest_state`` is
    still the previous report here — the projection is written after this call.
    """
    if report.event_type is not EventType.HEARTBEAT:
        return False
    previous = instance.latest_state or {}
    return previous.get("reasonCode") == report.reason_code.value


def _supersedes_failure(report: StatusReportContract) -> bool:
    """An explicit reason other than the persisting set ends the old one."""
    return report.reason_code not in _PERSISTING_REASONS


def _contradicts_failure(report: StatusReportContract) -> bool:
    """Evidence that an unresolved transport / no-candidate failure is over.

    A *new different* reason code supersedes too (handled by the caller), but
    ``NONE`` alone is silence, not resolution.
    """
    if report.execution.phase is ExecutionPhase.RUNNING:
        return True
    binding = report.binding
    return (
        binding is not None
        and binding.phase is BindingPhase.READY
        and binding.identity_verified
    )


def _open_initialization_episode(instance: StatusInstance, *, now: datetime) -> None:
    """Give a managed-but-episode-less instance one server-owned incident id.

    §4.1 requires an initialisation episode on first onboarding. A disabled or
    awaiting-server instance reports no incident of its own, yet any decision
    has to name one, so the server owns the id until the plugin reports a
    healthy running heartbeat.
    """
    instance.current_incident_id = uuid.uuid4()
    instance.current_incident_started_at = now
    instance.manual_latch_action = None
    instance.manual_latch_at = None


def _apply_decision_results(
    session: Session, instance: StatusInstance, report: StatusReportContract
) -> list[UUID]:
    """Record matched decision results idempotently.

    A result naming an unknown decision is simply unmatched — it is not forged
    away, and it never invents authority. A late result updates the audit row
    while it is still open but never reopens a resolved episode.
    """
    matched: list[UUID] = []
    for result in report.decision_results:
        row = session.execute(
            select(StatusDecision).where(
                StatusDecision.instance_id == instance.id,
                StatusDecision.id == result.decision_id,
                StatusDecision.revision == int(result.revision),
            )
        ).scalar_one_or_none()
        if row is None:
            continue
        matched.append(row.id)
        if row.outcome in _TERMINAL_OUTCOMES:
            continue
        row.outcome = result.outcome.value
        row.outcome_reason_code = result.reason_code.value
        row.completed_at = result.completed_at
        row.delivery_state = result.outcome.value
    session.flush()
    return matched


def _episode_is_resolved(
    session: Session,
    instance: StatusInstance,
    report: StatusReportContract,
    matched: list[UUID],
) -> UUID | None:
    """Return the incident this report closes, or ``None``.

    §4.1: an episode closes only on a confirmed ``resume_monitoring`` *applied*
    result plus an actual running execution. The confirmation must also be
    *current*: matching on decision id alone let a replayed applied resume from
    an already-closed episode resolve a newer one, because the id still matched
    while the incident, epoch and revision had all moved on. A result from
    another incident, another binding epoch, or a superseded revision is history
    and may update the audit row but never closes anything.
    """
    incident = instance.current_incident_id
    if incident is None:
        return None
    if report.incident_id != incident:
        return None
    if report.execution.phase is not ExecutionPhase.RUNNING:
        return None
    report_epoch = report.binding.epoch if report.binding is not None else None
    for decision_id in matched:
        row = session.get(StatusDecision, decision_id)
        if row is None:
            continue
        if row.action != DecisionAction.RESUME_MONITORING.value:
            continue
        if row.outcome != "applied":
            continue
        if row.incident_id != incident:
            continue
        if row.revision != int(instance.current_decision_revision):
            continue
        if report_epoch is not None and row.expected_binding_epoch != report_epoch:
            continue
        # Remember what was closed so a late result for it is history, and hand
        # the id back so the notification keys on the real episode rather than
        # the now-empty current slot.
        instance.resolved_incident_id = incident
        instance.current_incident_id = None
        instance.current_incident_started_at = None
        instance.manual_latch_action = None
        instance.manual_latch_at = None
        instance.consecutive_transport_failures = 0
        return incident
    return None


def _policy_input(
    session: Session,
    instance: StatusInstance,
    report: StatusReportContract,
    *,
    now: datetime,
) -> StatusPolicyInput:
    monitor = report.capability_state.monitor
    binding = report.binding
    latch = None
    if instance.manual_latch_action and instance.current_incident_id:
        latch = ManualLatch(
            incident_id=instance.current_incident_id,
            action=DecisionAction(instance.manual_latch_action),
            set_at=instance.manual_latch_at or now,
        )
    history = replace(
        repository.episode_recovery_history(
            session, instance.id, instance.current_incident_id
        ),
        consecutive_transport_failures=int(
            instance.consecutive_transport_failures or 0
        ),
    )
    return StatusPolicyInput(
        now=now,
        observed_at=report.observed_at,
        # Receiving this report *is* the proof of reachability, so connectivity
        # is online here even if the previous heartbeat was stale.
        connectivity=derive_connectivity(now, now),
        incident_id=instance.current_incident_id,
        episode_started_at=instance.current_incident_started_at,
        reason_code=report.reason_code,
        binding_phase=binding.phase if binding is not None else None,
        identity_verified=bool(binding.identity_verified)
        if binding is not None
        else False,
        candidate_count=binding.candidate_count if binding is not None else 0,
        execution_phase=report.execution.phase,
        execution_reason_code=report.execution.reason_code,
        close_snapshot_complete=(
            bool(monitor.close_snapshot_complete) if monitor is not None else False
        ),
        close_snapshot_observed_at=(
            monitor.close_snapshot_observed_at if monitor is not None else None
        ),
        monitor_enabled=monitor.monitor_enabled if monitor is not None else None,
        # A scope mismatch was already refused with 403 before the policy ran.
        scope_consistent=True,
        recovery_history=history,
        manual_latch=latch,
    )


def _is_healthy_running(report: StatusReportContract) -> bool:
    return (
        report.execution.phase is ExecutionPhase.RUNNING
        and report.execution.reason_code is ExecutionReasonCode.NONE
        and report.binding is not None
        and report.binding.phase is BindingPhase.READY
    )


def _decision_window_open(row: StatusDecision, *, now: datetime) -> bool:
    return row.not_before <= now <= row.expires_at


def _same_attempt(row: StatusDecision, policy: StatusPolicyDecision) -> bool:
    if row.action != DecisionAction.RECOVER_BINDING.value:
        return True
    wanted = policy.parameters.attempt if policy.parameters else None
    return wanted is not None and int(row.parameters.get("attempt", -1)) == wanted


def _parameters_for(policy: StatusPolicyDecision) -> dict[str, Any]:
    if policy.action is DecisionAction.RECOVER_BINDING and policy.parameters:
        return {
            "attempt": policy.parameters.attempt,
            "captureTimeoutSeconds": policy.parameters.capture_timeout_seconds,
        }
    return {}


def _current_binding_epoch(
    instance: StatusInstance, report: StatusReportContract
) -> UUID:
    """The epoch the plugin must still be on for this command to be legal."""
    binding = (instance.latest_state or {}).get("binding") or {}
    epoch = binding.get("epoch")
    if epoch:
        return UUID(epoch)
    if report.binding is not None:
        return report.binding.epoch
    raise StatusServiceError(
        409, "CONTEXT_MISMATCH", "no current binding context to address a decision to"
    )


def _current_binding_epoch_or_none(
    instance: StatusInstance, report: StatusReportContract
) -> UUID | None:
    """The binding context a command would address, or ``None`` if there is none."""
    binding = (instance.latest_state or {}).get("binding") or {}
    epoch = binding.get("epoch")
    if epoch:
        return UUID(epoch)
    return report.binding.epoch if report.binding is not None else None


def _grants(instance: StatusInstance, action: DecisionAction) -> bool:
    """Whether this instance was registered to receive ``action``."""
    needed = _CAPABILITY_FOR_ACTION.get(action)
    if needed is None:
        return True
    return needed.value in (instance.capabilities or [])


def _capability_gate(
    instance: StatusInstance, policy: StatusPolicyDecision | None
) -> StatusPolicyDecision | None:
    """Downgrade an ungranted page command to a request for a person.

    §9.1 makes the registered capability list authoritative, and §D5 refuses to
    hand out ``monitor.resume`` by default. A page command nobody granted would
    otherwise be issued on the strength of the report alone — the report is an
    observation, not an authorisation. ``require_user`` is the safe downgrade:
    it stops unsafe work and routes the grant decision to an operator.
    """
    if policy is None or _grants(instance, policy.action):
        return policy
    return StatusPolicyDecision(
        action=DecisionAction.REQUIRE_USER, policy_version=policy.policy_version
    )


def _is_reusable(
    row: StatusDecision,
    *,
    incident: UUID | None,
    epoch: UUID | None,
    now: datetime,
) -> bool:
    """Whether ``row`` is still an outstanding, applicable instruction.

    Reuse must not resurrect an instruction the plugin has already finished with,
    and must not address an epoch the plugin has since moved off (§7.2): a
    terminal outcome would let the ledger execute it twice, and a stale epoch
    would address a binding that no longer exists.
    """
    if row.outcome in _TERMINAL_OUTCOMES:
        return False
    if row.incident_id != incident:
        return False
    if not _decision_window_open(row, now=now):
        return False
    return epoch is None or row.expected_binding_epoch == epoch


def _decide(
    session: Session,
    instance: StatusInstance,
    report: StatusReportContract,
    *,
    now: datetime,
) -> tuple[Any, StatusDecision | None]:
    """Run the pure policy and select or create the one current decision."""
    if report.binding is None:
        # A plugin with no page context (an ``installation`` instance, §6.2) can
        # only send telemetry. There is no binding epoch to address a command to,
        # so accepting it means giving no command at all — previously this fell
        # through to hold issuance and then failed on the missing epoch.
        return None, None

    if (
        instance.current_incident_id is None
        and instance.resolved_incident_id is not None
        and report.incident_id == instance.resolved_incident_id
    ):
        # A late result for an episode this instance already closed. §4.1 lets
        # it supplement the audit history, but it must not reopen the episode and
        # must not buy a new command — the page evidence it carries belongs to a
        # binding context that has since been superseded. ``resolved_incident_id
        # is not None`` keeps a never-managed instance from matching on
        # ``None == None`` and having its first decision swallowed.
        return None, None

    if instance.current_incident_id is None and not _is_healthy_running(report):
        _open_initialization_episode(instance, now=now)

    policy = _capability_gate(
        instance,
        evaluate_status_policy(_policy_input(session, instance, report, now=now)),
    )
    existing = repository.current_decision(session, instance.id)
    epoch = _current_binding_epoch_or_none(instance, report)

    if policy is None:
        # Keep an outstanding instruction rather than replacing it with a hold
        # that would abort an attempt in flight (§11) — but only while it is
        # still applicable and not already finished by the plugin.
        if existing is not None and _is_reusable(
            existing,
            incident=instance.current_incident_id,
            epoch=epoch,
            now=now,
        ):
            return repository.decision_wire(existing), existing
        return None, None

    # Only a decision row needs an episode to point at. A healthy running
    # instance legitimately has none, and demanding one here would turn its
    # silence into a refusal instead of the ``decision: null`` it must get.
    incident = instance.current_incident_id
    if incident is None:  # pragma: no cover — the branch above guarantees one
        raise StatusServiceError(
            409, "CONTEXT_MISMATCH", "no open episode to address a decision to"
        )

    if (
        existing is not None
        and existing.action == policy.action.value
        and _is_reusable(existing, incident=incident, epoch=epoch, now=now)
        and _same_attempt(existing, policy)
    ):
        return repository.decision_wire(existing), existing

    if epoch is None:
        raise StatusServiceError(
            409,
            "CONTEXT_MISMATCH",
            "no current binding context to address a decision to",
        )

    revision = int(instance.current_decision_revision) + 1
    row = repository.insert_decision(
        session,
        instance_id=instance.id,
        revision=revision,
        incident_id=incident,
        expected_binding_epoch=epoch,
        action=policy.action.value,
        parameters=_parameters_for(policy),
        issued_at=now,
        not_before=now,
        expires_at=now + timedelta(seconds=MAX_DECISION_WINDOW_SECONDS),
        policy_version=policy.policy_version,
        source="automatic",
    )
    instance.current_decision_revision = revision
    return repository.decision_wire(row), row


def _freeze_response(
    row: StatusReport, *, receipt: dict[str, Any], decision_wire: dict[str, Any] | None
) -> None:
    """Attach the frozen business part of the response to the report row.

    Split out because it is the last mutation before commit: a failure here must
    roll the whole ingest back rather than leave a report whose response the
    server never actually committed (§8).
    """
    row.receipt = receipt
    row.response_decision = decision_wire


def _build_response(
    *,
    receipt_wire: dict[str, Any],
    decision_wire: dict[str, Any] | None,
    request_id: str,
    now: datetime,
) -> StatusResponse:
    return StatusResponse.model_validate(
        {
            "schemaVersion": SCHEMA_VERSION,
            "requestId": request_id,
            "receipt": receipt_wire,
            "serverTime": wire_timestamp(now),
            "reportAfterSeconds": REPORT_AFTER_SECONDS,
            "decision": decision_wire,
        }
    )


def _replay(row: StatusReport, *, request_id: str, now: datetime) -> StatusResponse:
    """Answer a duplicate with the original frozen receipt and decision.

    Only ``requestId`` and ``serverTime`` are rebuilt: they belong to this
    transport attempt, and reusing a stale ``serverTime`` would let a client add
    it to ``expiresAt`` and extend a decision's life (§7.3).
    """
    return _build_response(
        receipt_wire=dict(row.receipt),
        decision_wire=(
            dict(row.response_decision) if row.response_decision is not None else None
        ),
        request_id=request_id,
        now=now,
    )


def _notification_payload(
    instance: StatusInstance, report: StatusReportContract, *, kind: str
) -> dict[str, Any]:
    """Non-sensitive template parameters only — no credential, no seller id."""
    return {
        "kind": kind,
        "pluginId": instance.plugin_id,
        "instanceId": str(instance.id),
        "incidentId": (
            str(instance.current_incident_id) if instance.current_incident_id else None
        ),
        "eventType": report.event_type.value,
        "reasonCode": report.reason_code.value,
        "bindingPhase": (
            report.binding.phase.value if report.binding is not None else None
        ),
        "executionPhase": report.execution.phase.value,
        "observedAt": wire_timestamp(report.observed_at),
    }


def _queue_notifications(
    session: Session,
    instance: StatusInstance,
    report: StatusReportContract,
    *,
    decision_row: StatusDecision | None,
    resolved_incident: UUID | None,
    now: datetime,
) -> None:
    """Queue this report's lifecycle notification, if it produced one.

    ``resolved_incident`` is the id of the episode that just closed. The current
    slot is already empty by the time this runs, so deriving the dedupe key from
    ``current_incident_id`` collapsed every recovery onto the literal
    ``no-episode`` and let the first one suppress all later ones.
    """
    if resolved_incident is not None:
        repository.enqueue_notification(
            session,
            instance_id=instance.id,
            incident_key=str(resolved_incident),
            notification_kind=NOTIFY_EPISODE_RECOVERED,
            payload={
                **_notification_payload(
                    instance, report, kind=NOTIFY_EPISODE_RECOVERED
                ),
                "incidentId": str(resolved_incident),
            },
            now=now,
        )
        return
    if decision_row is None or decision_row.action != DecisionAction.REQUIRE_USER.value:
        return
    repository.enqueue_notification(
        session,
        instance_id=instance.id,
        incident_key=str(instance.current_incident_id or "no-episode"),
        notification_kind=NOTIFY_EPISODE_REQUIRES_USER,
        payload=_notification_payload(
            instance, report, kind=NOTIFY_EPISODE_REQUIRES_USER
        ),
        now=now,
    )


def ingest_report(
    session: Session,
    *,
    instance_id: UUID,
    report: StatusReportContract,
    payload_hash: str,
    request_id: str,
    now: datetime,
) -> StatusResponse:
    """Accept one already-validated report and return its receipt + decision."""
    instance = repository.lock_instance(session, instance_id)
    if instance is None:
        raise StatusServiceError(404, "INSTANCE_NOT_FOUND", "unknown instance")
    if not instance.enabled:
        raise StatusServiceError(403, "INSTANCE_DENIED", "instance is disabled")
    if report.instance_id != instance.id:
        raise StatusServiceError(
            403, "INSTANCE_DENIED", "report does not belong to this credential"
        )
    if report.observed_at > now + timedelta(seconds=MAX_FUTURE_OBSERVATION_SECONDS):
        # §6.2 refuses an observation more than five minutes ahead of the server.
        # Stored as-is it would both persist an impossible fact and advance the
        # projection, and an obviously-future clock must not be able to stretch
        # the freshness window an action is judged against.
        raise StatusServiceError(
            400,
            "SCHEMA_INVALID",
            "observedAt is more than five minutes in the future",
        )

    prior_event = repository.find_report_by_event(session, instance.id, report.event_id)
    if prior_event is not None:
        if prior_event.payload_hash != payload_hash:
            raise StatusServiceError(
                409, "EVENT_CONFLICT", "event id already stored with other content"
            )
        return _replay(prior_event, request_id=request_id, now=now)

    report_seq = int(report.report_seq)
    prior_seq = repository.find_report_by_seq(session, instance.id, report_seq)
    if prior_seq is not None:
        # §8: same sequence, different event — a cloned profile or local
        # corruption. Never overwrite, never silently renumber.
        raise StatusServiceError(
            409, "SEQUENCE_CONFLICT", "report sequence already used by another event"
        )

    if _authorize_scope(instance, report):
        scope = report.scope
        if not isinstance(scope, ShopScope):  # pragma: no cover — guarded above
            raise StatusServiceError(
                403, "SCOPE_DENIED", "installation scope cannot carry an advertiser"
            )
        instance.scope = {**instance.scope, "advertiserId": scope.advertiser_id}

    projection_updated = instance.latest_report_seq is None or report_seq > int(
        instance.latest_report_seq
    )
    if projection_updated:
        # Only a report that becomes the new current state may advance the
        # episode, the latch and the transport counter (§10 step 3). An archival
        # report still proves reachability and still archives below.
        _advance_episode(instance, report, now=now)
        instance.latest_report_seq = report_seq
        instance.latest_event_id = report.event_id
        instance.latest_state = report.model_dump(mode="json", by_alias=True)
        instance.latest_observed_at = report.observed_at
        instance.latest_received_at = now
    # Any accepted request proves reachability — never readiness (§10 step 3).
    instance.last_seen_at = now
    instance.stale_since = None
    session.flush()

    # Decision results are matched regardless of ordering: an archival report is
    # still the only place a late result can arrive (§7.3).
    matched = _apply_decision_results(session, instance, report)
    # ...but only a *current* report may close an episode, or a delayed low
    # sequence carrying a stale applied resume could resolve a live incident.
    resolved = (
        _episode_is_resolved(session, instance, report, matched)
        if projection_updated
        else None
    )

    receipt_wire = {
        "eventId": str(report.event_id),
        "reportSeq": report.report_seq,
        "receivedAt": wire_timestamp(now),
        "projectionUpdated": projection_updated,
    }

    row = repository.insert_report(
        session,
        instance_id=instance.id,
        event_id=report.event_id,
        report_seq=report_seq,
        payload_hash=payload_hash,
        observed_at=report.observed_at,
        received_at=now,
        event_type=report.event_type.value,
        reason_code=report.reason_code.value,
        incident_id=report.incident_id,
        binding_epoch=report.binding.epoch if report.binding is not None else None,
        validated_payload=report.model_dump(mode="json", by_alias=True),
        receipt=receipt_wire,
        response_decision=None,
    )

    decision_contract = None
    decision_row = None
    if projection_updated:
        decision_contract, decision_row = _decide(session, instance, report, now=now)

    decision_wire_payload = (
        decision_contract.model_dump(mode="json", by_alias=True)
        if decision_contract is not None
        else None
    )
    _freeze_response(row, receipt=receipt_wire, decision_wire=decision_wire_payload)
    _queue_notifications(
        session,
        instance,
        report,
        decision_row=decision_row,
        resolved_incident=resolved,
        now=now,
    )
    session.flush()

    return _build_response(
        receipt_wire=receipt_wire,
        decision_wire=decision_wire_payload,
        request_id=request_id,
        now=now,
    )


# ── Operator decisions (§9.5) ──────────────────────────────────────────


def _current_epoch_from_state(instance: StatusInstance) -> UUID | None:
    binding = (instance.latest_state or {}).get("binding") or {}
    epoch = binding.get("epoch")
    return UUID(epoch) if epoch else None


def _enforce_recovery_budget(
    instance: StatusInstance,
    history: RecoveryHistory,
    *,
    now: datetime,
) -> None:
    """Apply §11's episode budget to a *manual* recovery as well.

    An operator command is still a ``recover_binding`` issuance, so it spends the
    same budget as an automatic one: without this the fourth request built
    ``attempt=4``, which cannot satisfy the decision wire contract at all and
    surfaced as an error instead of a bounded refusal. Releasing a latch must not
    refund anything either — the count comes from the decision log for the
    episode, which no latch operation touches.
    """
    if history.recover_decisions_issued >= MAX_RECOVER_ATTEMPTS_PER_EPISODE:
        raise StatusServiceError(
            409,
            "RECOVERY_BUDGET_EXHAUSTED",
            "this episode already used its recovery attempts; a person must decide",
        )
    if (
        history.last_recover_issued_at is not None
        and (now - history.last_recover_issued_at).total_seconds()
        < MIN_RECOVER_INTERVAL_SECONDS
    ):
        raise StatusServiceError(
            409,
            "RECOVERY_INTERVAL_NOT_ELAPSED",
            "wait for the minimum interval before another recovery attempt",
        )
    if (
        instance.current_incident_started_at is not None
        and (now - instance.current_incident_started_at).total_seconds()
        >= EPISODE_ESCALATION_SECONDS
    ):
        raise StatusServiceError(
            409,
            "RECOVERY_EPISODE_TIMED_OUT",
            "this episode exceeded its recovery window; escalate to require_user",
        )


def manual_decision(
    session: Session,
    *,
    instance_id: UUID,
    command: AdminDecisionRequest,
    now: datetime,
) -> dict[str, Any]:
    """Apply an operator hold/recover/require_user to the current episode.

    Three conditions must all hold under the instance row lock: the caller's
    ``expectedRevision`` is current, the named ``incidentId`` is the open one,
    and the named ``expectedBindingEpoch`` is the current binding context. None
    is ever inferred, so an operator command cannot become a cross-identity
    release, and there is no manual ``resume_monitoring`` at all.
    """
    instance = repository.lock_instance(session, instance_id)
    if instance is None:
        raise StatusServiceError(404, "INSTANCE_NOT_FOUND", "unknown instance")

    replayed = repository.decision_by_admin_request(
        session, instance_id, command.request_id
    )
    if replayed is not None:
        return {
            "instanceId": str(instance.id),
            "decision": repository.decision_wire(replayed).model_dump(
                mode="json", by_alias=True
            ),
            "replayed": True,
        }

    if command.action is ManualDecisionAction.RECOVER_BINDING and not _grants(
        instance, DecisionAction.RECOVER_BINDING
    ):
        # §9.1: the registered capability list is authoritative for an operator
        # exactly as it is for the automatic path — a manual recovery is still
        # a recovery issuance. Refused *before* the revision, epoch and budget
        # checks mutate anything, and after the replay lookup above so a retried
        # command that was already issued still returns its original decision.
        raise StatusServiceError(
            403,
            "CAPABILITY_DENIED",
            "this instance was not registered for binding recovery",
        )

    if command.action is ManualDecisionAction.RECOVER_BINDING and not instance.enabled:
        # §9.2 disabled priority, same branch ``ingest_report`` already takes.
        # A disabled instance must not gain a fresh permission, lose its latch
        # or spend recovery budget even from an admin holding a matching tuple
        # and the registered capability. Replay is handled above and stays
        # available: re-reading a frozen historical decision grants nothing.
        raise StatusServiceError(403, "INSTANCE_DENIED", "instance is disabled")

    if int(command.expected_revision) != int(instance.current_decision_revision):
        raise StatusServiceError(
            409, "REVISION_CONFLICT", "expectedRevision is not the current revision"
        )
    if (
        instance.current_incident_id is None
        or instance.current_incident_id != command.incident_id
    ):
        raise StatusServiceError(
            409, "CONTEXT_MISMATCH", "there is no open episode with this incident id"
        )
    current_epoch = _current_epoch_from_state(instance)
    if current_epoch is None or current_epoch != command.expected_binding_epoch:
        raise StatusServiceError(
            409, "CONTEXT_MISMATCH", "expectedBindingEpoch is not the current binding"
        )

    history = repository.episode_recovery_history(
        session, instance.id, instance.current_incident_id
    )
    action = command.action
    parameters: dict[str, Any] = {}
    if action is ManualDecisionAction.RECOVER_BINDING:
        _enforce_recovery_budget(instance, history, now=now)
        parameters = {
            "attempt": history.recover_decisions_issued + 1,
            "captureTimeoutSeconds": RECOVER_CAPTURE_TIMEOUT_SECONDS,
        }

    revision = int(instance.current_decision_revision) + 1
    row = repository.insert_decision(
        session,
        instance_id=instance.id,
        revision=revision,
        incident_id=instance.current_incident_id,
        expected_binding_epoch=current_epoch,
        action=action.value,
        parameters=parameters,
        issued_at=now,
        not_before=now,
        expires_at=now + timedelta(seconds=MAX_DECISION_WINDOW_SECONDS),
        policy_version=POLICY_VERSION,
        source="admin",
        admin_request_id=command.request_id,
    )
    instance.current_decision_revision = revision
    if action in _LATCHING_ADMIN_ACTIONS:
        instance.manual_latch_action = action.value
        instance.manual_latch_at = now
    else:
        # An explicit operator recover is the only thing that releases a latch.
        instance.manual_latch_action = None
        instance.manual_latch_at = None
    session.flush()
    return {
        "instanceId": str(instance.id),
        "decision": repository.decision_wire(row).model_dump(
            mode="json", by_alias=True
        ),
        "replayed": False,
    }


# ── Sweep support (§11) ───────────────────────────────────────────────


def stale_instance_ids(session: Session, *, now: datetime) -> list[UUID]:
    """Instances whose *delivery* has lapsed.

    Only instances that have reported at least once are candidates: a freshly
    provisioned instance is ``unknown``, never "stale" (§11).
    """
    rows = session.execute(
        select(StatusInstance.id).where(
            StatusInstance.last_seen_at.is_not(None),
            StatusInstance.last_seen_at
            < now - timedelta(seconds=HEARTBEAT_STALE_SECONDS),
        )
    ).all()
    return [row[0] for row in rows]


def no_candidate_instance_ids(session: Session, *, now: datetime) -> list[UUID]:
    """Online instances that have reported NO_CANDIDATE for long enough (§11).

    §11 requires one notification when an instance that is still *reachable*
    keeps reporting no candidate for 180 seconds — a page that is simply gone is
    silent in a different way, and that case is the stale notification instead.
    """
    rows = session.execute(
        select(StatusInstance.id).where(
            StatusInstance.enabled.is_(True),
            StatusInstance.no_candidate_since.is_not(None),
            StatusInstance.no_candidate_since
            < now - timedelta(seconds=NO_CANDIDATE_NOTIFY_SECONDS),
            StatusInstance.last_seen_at.is_not(None),
            StatusInstance.last_seen_at
            >= now - timedelta(seconds=HEARTBEAT_STALE_SECONDS),
        )
    ).all()
    return [row[0] for row in rows]


def enqueue_no_candidate_notification(
    session: Session, *, instance: StatusInstance, now: datetime
) -> bool:
    """One notice per episode of continuously missing candidates.

    Keyed on the episode, so a per-minute sweep repeats nothing and a fresh
    episode notifies again.
    """
    if instance.no_candidate_since is None:  # pragma: no cover — filtered by the query
        return False
    return repository.enqueue_notification(
        session,
        instance_id=instance.id,
        incident_key=str(instance.current_incident_id or "no-episode"),
        notification_kind=NOTIFY_EPISODE_NO_CANDIDATE,
        payload={
            "kind": NOTIFY_EPISODE_NO_CANDIDATE,
            "pluginId": instance.plugin_id,
            "instanceId": str(instance.id),
            "incidentId": (
                str(instance.current_incident_id)
                if instance.current_incident_id
                else None
            ),
            "noCandidateSince": wire_timestamp(instance.no_candidate_since),
            "noCandidateSeconds": NO_CANDIDATE_NOTIFY_SECONDS,
        },
        now=now,
    )


def enqueue_stale_notification(
    session: Session, *, instance: StatusInstance, now: datetime
) -> bool:
    """One notification per outage, keyed by when the outage was first seen.

    §11: the stale episode starts on the online→stale transition and ends on the
    next fresh report, so a per-minute sweep cannot manufacture a new id — and
    the message never claims a page closed or a shop logged out.
    """
    if instance.stale_since is None:
        instance.stale_since = now
        session.flush()
    return repository.enqueue_notification(
        session,
        instance_id=instance.id,
        incident_key=wire_timestamp(instance.stale_since),
        notification_kind=NOTIFY_INSTANCE_STALE,
        payload={
            "kind": NOTIFY_INSTANCE_STALE,
            "pluginId": instance.plugin_id,
            "instanceId": str(instance.id),
            "lastSeenAt": (
                wire_timestamp(instance.last_seen_at) if instance.last_seen_at else None
            ),
            "staleSince": wire_timestamp(instance.stale_since),
            "heartbeatStaleSeconds": HEARTBEAT_STALE_SECONDS,
        },
        now=now,
    )
