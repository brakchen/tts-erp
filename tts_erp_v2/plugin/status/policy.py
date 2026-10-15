"""Pure recovery policy for managed plugin instances (design §11).

The policy is a function of verified facts plus an explicit clock. It holds no
state, reads no database and never consults ``datetime.now``: the runtime layer
persists whatever it returns together with the report that produced it.

Precedence, highest first (§11):

1. an unreachable instance is never handed a new command;
2. an operator latch on the *current* episode outranks every automatic rule;
3. a user-disabled instance is never remotely re-enabled — reported either as the
   execution reason, the report reason or a false monitor switch, so an
   inconsistent execution block cannot buy a recovery (§11);
4. a full report queue escalates to a person — §12 outranks every state shortcut
   below, so a release, an in-flight recovery and a verified candidate all stop;
5. a scope conflict escalates to a person;
6. an already-running ready instance gets nothing — its permission window is an
   action-start deadline, not a runtime lease (§7.2);
7. an episode still unresolved after ten minutes escalates to a person, whether
   or not it ever spent a recovery attempt and whether or not readiness evidence
   arrived late;
8. otherwise fresh ready evidence resumes monitoring;
9. otherwise the reason code decides, and every ``recover_binding`` passes the
   same two gates: the observation must still be actionable (§6.2: server-side
   age within -5..120 s) and the episode budget must allow another attempt.
   A verified same-target candidate inside this episode is recovery evidence in
   its own right (§11), except while the page is loading or on the first
   transport failure, where the plugin is expected to keep observing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from tts_erp_v2.plugin.status.contracts import (
    RECOVER_CAPTURE_TIMEOUT_SECONDS,
    BindingPhase,
    Connectivity,
    DecisionAction,
    ExecutionPhase,
    ExecutionReasonCode,
    ReasonCode,
)

POLICY_VERSION = "binding-recovery-v1"
MAX_RECOVER_ATTEMPTS_PER_EPISODE = 3
MIN_RECOVER_INTERVAL_SECONDS = 60
EPISODE_ESCALATION_SECONDS = 600
MAX_FUTURE_SKEW_SECONDS = 5
MAX_OBSERVATION_AGE_SECONDS = 120
MAX_CLOSE_SNAPSHOT_AGE_SECONDS = 60
MIN_TRANSPORT_FAILURES_FOR_RECOVER = 2
HEARTBEAT_STALE_SECONDS = 180

_RECOVERABLE_PAGE_REASONS = frozenset(
    {ReasonCode.TAB_CLOSED, ReasonCode.ORIGIN_LEFT, ReasonCode.TAB_DISCARDED}
)
_TRANSPORT_REASONS = frozenset(
    {ReasonCode.TRANSPORT_TIMEOUT, ReasonCode.REQUEST_FAILED}
)
_USER_ACTION_REASONS = frozenset(
    {
        ReasonCode.LOGIN_REQUIRED,
        ReasonCode.IDENTITY_MISMATCH,
        ReasonCode.AMBIGUOUS_CANDIDATES,
    }
)
_LATCHABLE_ACTIONS = frozenset({DecisionAction.HOLD, DecisionAction.REQUIRE_USER})


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RecoveryHistory:
    """Issued-recovery facts for the current episode, read from the projection.

    ``recover_decisions_issued`` counts *issued* commands, including ones whose
    delivery was lost, so a lost response cannot buy extra attempts (§11).
    """

    recover_decisions_issued: int = 0
    last_recover_issued_at: datetime | None = None
    consecutive_transport_failures: int = 0


@dataclass(frozen=True, slots=True)
class ManualLatch:
    """Operator hold/require_user latched to one episode (§9.5).

    A plain heartbeat cannot clear it, and it stops applying as soon as the
    instance reports a different (or no) episode.
    """

    incident_id: UUID
    action: DecisionAction
    set_at: datetime

    def __post_init__(self) -> None:
        if self.action not in _LATCHABLE_ACTIONS:
            raise ValueError("only hold and require_user latch an episode")
        _require_aware(self.set_at, "set_at")


@dataclass(frozen=True, slots=True)
class RecoveryAttempt:
    """Which authorised attempt this recovery is, and its capture deadline."""

    attempt: int
    capture_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class StatusPolicyDecision:
    action: DecisionAction
    parameters: RecoveryAttempt | None = None
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True, slots=True)
class StatusPolicyInput:
    """Every fact the policy is allowed to see.

    ``observed_at`` is plugin evidence; ``connectivity`` is derived from what
    the server has actually received. Received connectivity never substitutes
    for observed readiness, so both are separate inputs.
    """

    now: datetime
    observed_at: datetime
    connectivity: Connectivity
    incident_id: UUID | None
    episode_started_at: datetime | None
    reason_code: ReasonCode
    binding_phase: BindingPhase | None
    identity_verified: bool
    candidate_count: int
    execution_phase: ExecutionPhase
    execution_reason_code: ExecutionReasonCode
    close_snapshot_complete: bool
    close_snapshot_observed_at: datetime | None
    monitor_enabled: bool | None
    scope_consistent: bool
    recovery_history: RecoveryHistory = field(default_factory=RecoveryHistory)
    manual_latch: ManualLatch | None = None


def observation_age_seconds(observed_at: datetime, now: datetime) -> float:
    """Server-side age of a client observation, positive when in the past."""
    _require_aware(observed_at, "observed_at")
    _require_aware(now, "now")
    return (now - observed_at).total_seconds()


def observation_is_actionable(observed_at: datetime, now: datetime) -> bool:
    """Whether an observation may authorise recovery or release (§6).

    The window tolerates 5 seconds of clock skew in each direction; a client
    clock far in the future must not extend freshness.
    """
    age = observation_age_seconds(observed_at, now)
    return -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_OBSERVATION_AGE_SECONDS


def derive_connectivity(
    last_received_at: datetime | None, now: datetime
) -> Connectivity:
    """Delivery state from receipt history alone (§4.2, §11).

    A stale instance says only "no recent message" — never that a page closed,
    that a shop logged out or that nobody is watching.
    """
    if last_received_at is None:
        return Connectivity.UNKNOWN
    _require_aware(last_received_at, "last_received_at")
    _require_aware(now, "now")
    if (now - last_received_at).total_seconds() > HEARTBEAT_STALE_SECONDS:
        return Connectivity.STALE
    return Connectivity.ONLINE


def evaluate_status_policy(
    inp: StatusPolicyInput,
) -> StatusPolicyDecision | None:
    """Return the decision to send now, or ``None`` to send nothing.

    ``None`` means "keep whatever is already outstanding": it is the answer for
    a healthy instance, for an in-flight recovery, for the recovery interval and
    for an instance the server cannot reach. It must never be confused with a
    ``hold`` that would suspend healthy work.
    """
    _require_aware(inp.now, "now")
    _require_aware(inp.observed_at, "observed_at")

    if inp.connectivity is not Connectivity.ONLINE:
        return None

    latch = inp.manual_latch
    if (
        latch is not None
        and inp.incident_id is not None
        and latch.incident_id == inp.incident_id
    ):
        return StatusPolicyDecision(action=latch.action)

    if (
        inp.execution_phase is ExecutionPhase.DISABLED
        or inp.execution_reason_code is ExecutionReasonCode.USER_DISABLED
        or inp.reason_code is ReasonCode.USER_DISABLED
        or inp.monitor_enabled is False
    ):
        # The report-level reason is included so a momentarily inconsistent
        # execution block cannot smuggle a recovery past the switch (§11).
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    if inp.reason_code is ReasonCode.REPORT_QUEUE_FULL:
        # §12: a full queue has already lost the ability to record lifecycle
        # facts, so recovery is paused and a person is asked. Decided here, above
        # every state shortcut, because a release, an in-flight recovery or a
        # candidate would all produce more facts the queue cannot store.
        return StatusPolicyDecision(action=DecisionAction.REQUIRE_USER)

    if not inp.scope_consistent:
        return StatusPolicyDecision(action=DecisionAction.REQUIRE_USER)

    already_running = (
        inp.binding_phase is BindingPhase.READY
        and inp.execution_phase is ExecutionPhase.RUNNING
    )
    if already_running:
        return None

    if _episode_has_timed_out(inp):
        return StatusPolicyDecision(action=DecisionAction.REQUIRE_USER)

    if inp.binding_phase is BindingPhase.READY:
        if _release_evidence_is_fresh(inp):
            return StatusPolicyDecision(action=DecisionAction.RESUME_MONITORING)
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    if inp.execution_reason_code is ExecutionReasonCode.RECOVERING:
        return None

    return _decision_for_reason(inp)


def _episode_has_timed_out(inp: StatusPolicyInput) -> bool:
    """Whether an open episode has had long enough without reaching readiness.

    Applies to every unresolved, not-running episode and is decided *before* any
    release: fresh readiness evidence cannot buy an open episode more than ten
    minutes, and a stale or unavailable snapshot cannot hold one forever (§11).
    Healthy running work returns earlier, and disabled/latched instances are
    decided before this point, so none of them reach it.
    """
    if inp.incident_id is None or inp.episode_started_at is None:
        return False
    return (
        inp.now - inp.episode_started_at
    ).total_seconds() >= EPISODE_ESCALATION_SECONDS


def _decision_for_reason(inp: StatusPolicyInput) -> StatusPolicyDecision | None:
    """Decide from the reason code. ``REPORT_QUEUE_FULL`` never reaches here.

    The caller resolves it first because it outranks every state shortcut; this
    keeps a single owner for that fatal rule.
    """
    reason = inp.reason_code

    if reason in _USER_ACTION_REASONS:
        if _same_target_candidate_is_verified(inp):
            # Passive verification cleared the problem; the full ready/snapshot
            # gate still has to run before monitoring resumes.
            return _recover_binding_or_escalate(inp)
        return StatusPolicyDecision(action=DecisionAction.REQUIRE_USER)

    if reason in _TRANSPORT_REASONS:
        # Network trouble is never a login verdict, and it spends budget only
        # after repeated failures with a candidate in hand.
        if (
            inp.recovery_history.consecutive_transport_failures
            >= MIN_TRANSPORT_FAILURES_FOR_RECOVER
            and inp.candidate_count > 0
        ):
            return _recover_binding_or_escalate(inp)
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    if reason is ReasonCode.PAGE_LOADING:
        # Waiting for the page or its shared lease; never reload on top of it.
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    if _same_target_candidate_is_verified(inp):
        # §11: a verified same-target candidate inside this episode is recovery
        # evidence in its own right, whichever reason code led here. The latch,
        # disabled priority and the recovery budget above/below still apply.
        return _recover_binding_or_escalate(inp)

    if reason in _RECOVERABLE_PAGE_REASONS:
        if inp.candidate_count > 0:
            return _recover_binding_or_escalate(inp)
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    return StatusPolicyDecision(action=DecisionAction.HOLD)


def _same_target_candidate_is_verified(inp: StatusPolicyInput) -> bool:
    return (
        inp.candidate_count > 0
        and inp.identity_verified
        and inp.binding_phase in {BindingPhase.DISCOVERING, BindingPhase.VERIFYING}
    )


def _release_evidence_is_fresh(inp: StatusPolicyInput) -> bool:
    """Everything §6/§11 require before monitoring may be released.

    The close-window snapshot gate belongs to ``monitor.resume.v1``, the only
    release capability registered in this phase (§4.1, §14 D5). A ``ready``
    observation that cannot satisfy it stays held instead of being released, so
    adding a plugin never silently loosens this gate.
    """
    if not inp.identity_verified:
        return False
    if not observation_is_actionable(inp.observed_at, inp.now):
        return False
    if not inp.close_snapshot_complete or inp.close_snapshot_observed_at is None:
        return False
    snapshot_age = (inp.now - inp.close_snapshot_observed_at).total_seconds()
    return -MAX_FUTURE_SKEW_SECONDS <= snapshot_age <= MAX_CLOSE_SNAPSHOT_AGE_SECONDS


def _recover_binding_or_escalate(
    inp: StatusPolicyInput,
) -> StatusPolicyDecision | None:
    """Gate every ``recover_binding`` on fresh evidence and the episode budget.

    Returns ``None`` when a correct recovery is already outstanding (in flight,
    or issued less than a minute ago); the caller must keep the pending
    decision rather than replace it with a hold that would abort the attempt.
    """
    history = inp.recovery_history

    if not observation_is_actionable(inp.observed_at, inp.now):
        # §6.2/§10: only an observation whose server-side age is within
        # -5..120 s may authorise an action. The episode timeout is decided
        # before this point, so a long-dead episode still escalates to a person
        # instead of waiting for evidence that is too old to act on.
        return StatusPolicyDecision(action=DecisionAction.HOLD)

    if history.recover_decisions_issued >= MAX_RECOVER_ATTEMPTS_PER_EPISODE:
        return StatusPolicyDecision(action=DecisionAction.REQUIRE_USER)

    if inp.execution_reason_code is ExecutionReasonCode.RECOVERING:
        return None

    if (
        history.last_recover_issued_at is not None
        and (inp.now - history.last_recover_issued_at).total_seconds()
        < MIN_RECOVER_INTERVAL_SECONDS
    ):
        return None

    return StatusPolicyDecision(
        action=DecisionAction.RECOVER_BINDING,
        parameters=RecoveryAttempt(
            attempt=history.recover_decisions_issued + 1,
            capture_timeout_seconds=RECOVER_CAPTURE_TIMEOUT_SECONDS,
        ),
    )
