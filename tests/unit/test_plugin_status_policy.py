"""Pure recovery-policy behaviour for the status domain (design §11, §4.2).

The policy receives only verified facts plus an explicit clock. It never reads
the database, the network or a global clock, so every budget and freshness rule
is asserted at its exact boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tts_erp_v2.plugin.status import policy
from tts_erp_v2.plugin.status.contracts import (
    BindingPhase,
    Connectivity,
    DecisionAction,
    ExecutionPhase,
    ExecutionReasonCode,
    ReasonCode,
)

pytestmark = pytest.mark.layer_unit

NOW = datetime(2026, 10, 15, 17, 0, 0, tzinfo=UTC)
INCIDENT_ID = UUID("944aa39e-0865-4296-873d-5be7d99dcefb")
OTHER_INCIDENT_ID = UUID("11111111-2222-4333-8444-555555555555")


def _input(**overrides: object) -> policy.StatusPolicyInput:
    """A fresh, verified, ready-but-suspended observation (resume candidate)."""
    base: dict[str, object] = {
        "now": NOW,
        "observed_at": NOW - timedelta(seconds=5),
        "connectivity": Connectivity.ONLINE,
        "incident_id": INCIDENT_ID,
        "episode_started_at": NOW - timedelta(seconds=30),
        "reason_code": ReasonCode.NONE,
        "binding_phase": BindingPhase.READY,
        "identity_verified": True,
        "candidate_count": 1,
        "execution_phase": ExecutionPhase.SUSPENDED,
        "execution_reason_code": ExecutionReasonCode.INITIALIZING,
        "close_snapshot_complete": True,
        "close_snapshot_observed_at": NOW - timedelta(seconds=10),
        "monitor_enabled": True,
        "scope_consistent": True,
    }
    base.update(overrides)
    return policy.StatusPolicyInput(**base)  # type: ignore[arg-type]


def _action(decision: policy.StatusPolicyDecision | None) -> DecisionAction | None:
    return None if decision is None else decision.action


def _invalidated(**overrides: object) -> policy.StatusPolicyInput:
    """An invalidated binding with one same-target candidate still known."""
    facts: dict[str, object] = {
        "reason_code": ReasonCode.TAB_CLOSED,
        "binding_phase": BindingPhase.INVALID,
        "identity_verified": False,
        "execution_reason_code": ExecutionReasonCode.PAGE_INVALID,
        "close_snapshot_complete": False,
        "close_snapshot_observed_at": None,
    }
    facts.update(overrides)
    return _input(**facts)


# ── Healthy state ────────────────────────────────────────────────────


def test_healthy_running_heartbeat_without_incident_returns_no_command() -> None:
    decision = policy.evaluate_status_policy(
        _input(
            incident_id=None,
            episode_started_at=None,
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
        )
    )

    assert decision is None


def test_running_recovery_awaiting_server_confirmation_returns_no_command() -> None:
    decision = policy.evaluate_status_policy(
        _input(
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
        )
    )

    assert decision is None


def test_running_instance_is_never_interrupted_by_a_stale_snapshot() -> None:
    """Healthy work is not suspended because a snapshot aged (§2.8)."""
    decision = policy.evaluate_status_policy(
        _input(
            incident_id=None,
            episode_started_at=None,
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
            close_snapshot_complete=False,
            close_snapshot_observed_at=None,
            observed_at=NOW - timedelta(seconds=600),
        )
    )

    assert decision is None


def test_permission_expiry_does_not_reissue_a_command_to_a_healthy_instance() -> None:
    """expiresAt bounds action start, it is not a runtime lease (§7.2)."""
    decision = policy.evaluate_status_policy(
        _input(
            incident_id=None,
            episode_started_at=None,
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
            now=NOW + timedelta(minutes=30),
        )
    )

    assert decision is None


# ── Ready / release evidence ─────────────────────────────────────────


def test_fresh_ready_observation_resumes_monitoring() -> None:
    decision = policy.evaluate_status_policy(_input())

    assert _action(decision) is DecisionAction.RESUME_MONITORING
    assert decision is not None
    assert decision.parameters is None
    assert decision.policy_version == "binding-recovery-v1"


def test_ready_without_verified_identity_holds_instead_of_resuming() -> None:
    decision = policy.evaluate_status_policy(_input(identity_verified=False))

    assert _action(decision) is DecisionAction.HOLD


def test_ready_without_complete_snapshot_holds() -> None:
    decision = policy.evaluate_status_policy(
        _input(close_snapshot_complete=False, close_snapshot_observed_at=None)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_ready_with_snapshot_older_than_sixty_seconds_holds() -> None:
    decision = policy.evaluate_status_policy(
        _input(close_snapshot_observed_at=NOW - timedelta(seconds=61))
    )

    assert _action(decision) is DecisionAction.HOLD


def test_ready_with_stale_observation_holds() -> None:
    decision = policy.evaluate_status_policy(
        _input(observed_at=NOW - timedelta(seconds=121))
    )

    assert _action(decision) is DecisionAction.HOLD


def test_stale_receipt_does_not_substitute_for_stale_observation() -> None:
    """Received connectivity proves delivery only; readiness needs observed facts."""
    decision = policy.evaluate_status_policy(
        _input(observed_at=NOW - timedelta(seconds=300))
    )

    assert _action(decision) is DecisionAction.HOLD


def test_scope_conflict_requires_user() -> None:
    decision = policy.evaluate_status_policy(_input(scope_consistent=False))

    assert _action(decision) is DecisionAction.REQUIRE_USER


# ── Disabled priority ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("execution_phase", "execution_reason"),
    [
        (ExecutionPhase.DISABLED, ExecutionReasonCode.USER_DISABLED),
        (ExecutionPhase.DISABLED, ExecutionReasonCode.NONE),
        (ExecutionPhase.SUSPENDED, ExecutionReasonCode.USER_DISABLED),
    ],
)
def test_user_disabled_wins_over_ready_evidence(
    execution_phase: ExecutionPhase, execution_reason: ExecutionReasonCode
) -> None:
    decision = policy.evaluate_status_policy(
        _input(execution_phase=execution_phase, execution_reason_code=execution_reason)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_monitor_disabled_wins_over_ready_evidence() -> None:
    decision = policy.evaluate_status_policy(_input(monitor_enabled=False))

    assert _action(decision) is DecisionAction.HOLD


def test_disabled_priority_also_suppresses_recovery() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.USER_DISABLED,
            execution_reason_code=ExecutionReasonCode.USER_DISABLED,
            execution_phase=ExecutionPhase.DISABLED,
        )
    )

    assert _action(decision) is DecisionAction.HOLD


# ── Manual latch ─────────────────────────────────────────────────────


@pytest.mark.parametrize("latched", [DecisionAction.HOLD, DecisionAction.REQUIRE_USER])
def test_manual_latch_survives_automatic_heartbeats(latched: DecisionAction) -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            manual_latch=policy.ManualLatch(
                incident_id=INCIDENT_ID,
                action=latched,
                set_at=NOW - timedelta(seconds=5),
            )
        )
    )

    assert _action(decision) is latched


def test_manual_latch_loses_to_a_fresh_ready_episode() -> None:
    decision = policy.evaluate_status_policy(
        _input(
            manual_latch=policy.ManualLatch(
                incident_id=OTHER_INCIDENT_ID,
                action=DecisionAction.HOLD,
                set_at=NOW - timedelta(seconds=5),
            )
        )
    )

    assert _action(decision) is DecisionAction.RESUME_MONITORING


def test_manual_latch_is_dropped_with_its_episode() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            incident_id=None,
            manual_latch=policy.ManualLatch(
                incident_id=INCIDENT_ID,
                action=DecisionAction.REQUIRE_USER,
                set_at=NOW - timedelta(seconds=5),
            ),
        )
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING


# ── Recovery from a same-target candidate ────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [ReasonCode.TAB_CLOSED, ReasonCode.ORIGIN_LEFT, ReasonCode.TAB_DISCARDED],
)
def test_page_invalidated_with_known_candidate_recovers_binding(
    reason: ReasonCode,
) -> None:
    decision = policy.evaluate_status_policy(_invalidated(reason_code=reason))

    assert _action(decision) is DecisionAction.RECOVER_BINDING
    assert decision is not None and decision.parameters is not None
    assert decision.parameters.attempt == 1
    assert decision.parameters.capture_timeout_seconds == 90


def test_no_candidate_holds_instead_of_reloading() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(reason_code=ReasonCode.NO_CANDIDATE, candidate_count=0)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_page_loading_holds_without_creating_an_episode_command() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(reason_code=ReasonCode.PAGE_LOADING, candidate_count=2)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_worker_context_unknown_holds_for_new_evidence() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(reason_code=ReasonCode.WORKER_CONTEXT_UNKNOWN)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_report_queue_full_requires_user() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(reason_code=ReasonCode.REPORT_QUEUE_FULL)
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_in_flight_recovery_is_not_interrupted_by_a_second_command() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(execution_reason_code=ExecutionReasonCode.RECOVERING)
    )

    assert decision is None


# ── Login / identity ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.LOGIN_REQUIRED,
        ReasonCode.IDENTITY_MISMATCH,
        ReasonCode.AMBIGUOUS_CANDIDATES,
    ],
)
def test_login_and_identity_reasons_require_user(reason: ReasonCode) -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(reason_code=reason, candidate_count=0, binding_phase=None)
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_login_problem_passively_cleared_recover_binding_not_resume() -> None:
    """Clearing a login problem still walks the full ready/snapshot gate (§11)."""
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.LOGIN_REQUIRED,
            binding_phase=BindingPhase.VERIFYING,
            identity_verified=True,
            candidate_count=1,
            close_snapshot_complete=False,
            close_snapshot_observed_at=None,
        )
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING


def test_manual_latch_overrides_passively_cleared_login_problem() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.LOGIN_REQUIRED,
            binding_phase=BindingPhase.VERIFYING,
            identity_verified=True,
            manual_latch=policy.ManualLatch(
                incident_id=INCIDENT_ID,
                action=DecisionAction.REQUIRE_USER,
                set_at=NOW - timedelta(seconds=5),
            ),
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


# ── Transport failures are never a login verdict ─────────────────────


def test_first_transport_failure_holds() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.TRANSPORT_TIMEOUT,
            recovery_history=policy.RecoveryHistory(consecutive_transport_failures=1),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_repeated_transport_failure_with_candidate_recovers_binding() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.REQUEST_FAILED,
            recovery_history=policy.RecoveryHistory(consecutive_transport_failures=2),
        )
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING


def test_repeated_transport_failure_without_candidate_holds() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.TRANSPORT_TIMEOUT,
            candidate_count=0,
            recovery_history=policy.RecoveryHistory(consecutive_transport_failures=4),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


# ── Recovery budget ──────────────────────────────────────────────────


def test_third_recovery_is_the_last_allowed_attempt() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            recovery_history=policy.RecoveryHistory(recover_decisions_issued=2)
        )
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING
    assert decision is not None and decision.parameters is not None
    assert decision.parameters.attempt == 3


def test_recovery_budget_of_three_escalates_to_a_person() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            recovery_history=policy.RecoveryHistory(
                recover_decisions_issued=3,
                last_recover_issued_at=NOW - timedelta(seconds=600),
                consecutive_transport_failures=1,
            )
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_second_recovery_inside_sixty_seconds_is_not_issued() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            recovery_history=policy.RecoveryHistory(
                recover_decisions_issued=1,
                last_recover_issued_at=NOW - timedelta(seconds=59),
            )
        )
    )

    assert decision is None


def test_second_recovery_after_sixty_seconds_carries_the_next_attempt_number() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            recovery_history=policy.RecoveryHistory(
                recover_decisions_issued=1,
                last_recover_issued_at=NOW - timedelta(seconds=60),
            )
        )
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING
    assert decision is not None and decision.parameters is not None
    assert decision.parameters.attempt == 2


def test_episode_without_progress_after_ten_minutes_requires_user() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(episode_started_at=NOW - timedelta(minutes=10))
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_episode_at_nine_minutes_still_recovers() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(episode_started_at=NOW - timedelta(minutes=9, seconds=59))
    )

    assert _action(decision) is DecisionAction.RECOVER_BINDING


# ── Connectivity is not readiness ────────────────────────────────────


def test_stale_instance_never_receives_a_new_command() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(connectivity=Connectivity.STALE)
    )

    assert decision is None


def test_unknown_instance_never_receives_a_new_command() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(connectivity=Connectivity.UNKNOWN)
    )

    assert decision is None


@pytest.mark.parametrize(
    ("last_received_at", "expected"),
    [
        (NOW - timedelta(seconds=1), Connectivity.ONLINE),
        (NOW - timedelta(seconds=180), Connectivity.ONLINE),
        (NOW - timedelta(seconds=181), Connectivity.STALE),
        (NOW - timedelta(hours=3), Connectivity.STALE),
        (None, Connectivity.UNKNOWN),
    ],
)
def test_connectivity_is_derived_from_received_reports_only(
    last_received_at: datetime | None, expected: Connectivity
) -> None:
    assert policy.derive_connectivity(last_received_at, NOW) is expected


@pytest.mark.parametrize("age_seconds", [-5, 0, 120])
def test_observation_within_the_actionable_window(age_seconds: int) -> None:
    """``age_seconds`` is seconds-since-observed; negative means a fast clock."""
    observed_at = NOW - timedelta(seconds=age_seconds)

    assert policy.observation_is_actionable(observed_at, NOW) is True
    assert policy.observation_age_seconds(observed_at, NOW) == age_seconds


@pytest.mark.parametrize("age_seconds", [121, -6, -3600])
def test_observation_outside_the_actionable_window(age_seconds: int) -> None:
    observed_at = NOW - timedelta(seconds=age_seconds)

    assert policy.observation_is_actionable(observed_at, NOW) is False


def test_client_clock_five_minutes_ahead_is_never_actionable() -> None:
    decision = policy.evaluate_status_policy(
        _input(observed_at=NOW + timedelta(minutes=5, seconds=1))
    )

    assert _action(decision) is DecisionAction.HOLD


def _verified_candidate(**overrides: object) -> policy.StatusPolicyInput:
    """Passive verification found one same-target candidate in this episode."""
    facts: dict[str, object] = {
        "reason_code": ReasonCode.NONE,
        "binding_phase": BindingPhase.VERIFYING,
        "identity_verified": True,
        "candidate_count": 1,
        "execution_reason_code": ExecutionReasonCode.PAGE_INVALID,
        "close_snapshot_complete": False,
        "close_snapshot_observed_at": None,
    }
    facts.update(overrides)
    return _input(**facts)


# ── Recovery freshness (§6.2, §10) ──────────────────────────────────


@pytest.mark.parametrize(
    "observed_at", [NOW - timedelta(seconds=121), NOW + timedelta(seconds=6)]
)
def test_recovery_requires_an_actionable_observation(observed_at: datetime) -> None:
    """A stale or clock-skewed report must not authorise a recovery."""
    decision = policy.evaluate_status_policy(_invalidated(observed_at=observed_at))

    assert _action(decision) is DecisionAction.HOLD


@pytest.mark.parametrize(
    "observed_at", [NOW - timedelta(seconds=120), NOW + timedelta(seconds=5)]
)
def test_recovery_is_allowed_inside_the_observation_window(
    observed_at: datetime,
) -> None:
    decision = policy.evaluate_status_policy(_invalidated(observed_at=observed_at))

    assert _action(decision) is DecisionAction.RECOVER_BINDING


def test_recovery_history_does_not_bypass_observation_freshness() -> None:
    """The budget cannot be spent with evidence that is too old to act on."""
    decision = policy.evaluate_status_policy(
        _invalidated(
            observed_at=NOW - timedelta(seconds=300),
            recovery_history=policy.RecoveryHistory(consecutive_transport_failures=5),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


# ── Same-episode candidate rule (§11) ────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.NONE,
        ReasonCode.NO_CANDIDATE,
        ReasonCode.WORKER_CONTEXT_UNKNOWN,
        ReasonCode.TAB_CLOSED,
    ],
)
def test_verified_same_target_candidate_authorises_recovery(reason: ReasonCode) -> None:
    decision = policy.evaluate_status_policy(_verified_candidate(reason_code=reason))

    assert _action(decision) is DecisionAction.RECOVER_BINDING


def test_page_loading_keeps_waiting_even_with_a_verified_candidate() -> None:
    decision = policy.evaluate_status_policy(
        _verified_candidate(reason_code=ReasonCode.PAGE_LOADING)
    )

    assert _action(decision) is DecisionAction.HOLD


def test_unverified_candidate_never_recovers_on_its_own() -> None:
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            identity_verified=False, binding_phase=BindingPhase.DISCOVERING
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_verified_candidate_does_not_bypass_the_manual_latch() -> None:
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            manual_latch=policy.ManualLatch(
                incident_id=INCIDENT_ID,
                action=DecisionAction.HOLD,
                set_at=NOW - timedelta(seconds=5),
            )
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_verified_candidate_does_not_bypass_disabled_priority() -> None:
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            execution_phase=ExecutionPhase.DISABLED,
            execution_reason_code=ExecutionReasonCode.USER_DISABLED,
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_verified_candidate_still_observes_the_recovery_budget() -> None:
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            recovery_history=policy.RecoveryHistory(recover_decisions_issued=3)
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_transport_failures_need_two_observations_even_with_a_candidate() -> None:
    """The candidate rule does not spend budget on the first failure."""
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            reason_code=ReasonCode.TRANSPORT_TIMEOUT,
            recovery_history=policy.RecoveryHistory(consecutive_transport_failures=1),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


# ── Episode timeout escalation (§11) ────────────────────────────────


@pytest.mark.parametrize(
    "reason", [ReasonCode.NO_CANDIDATE, ReasonCode.PAGE_LOADING, ReasonCode.NONE]
)
def test_unresolved_episode_escalates_after_ten_minutes(reason: ReasonCode) -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=reason,
            candidate_count=0,
            episode_started_at=NOW - timedelta(minutes=10),
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_unresolved_episode_holds_just_before_the_timeout() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.NO_CANDIDATE,
            candidate_count=0,
            episode_started_at=NOW - timedelta(minutes=9, seconds=59),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


@pytest.mark.parametrize(
    ("episode_age", "snapshot_at", "expected"),
    [
        (599, NOW - timedelta(seconds=10), DecisionAction.RESUME_MONITORING),
        (599, None, DecisionAction.HOLD),
        (600, NOW - timedelta(seconds=10), DecisionAction.REQUIRE_USER),
        (600, None, DecisionAction.REQUIRE_USER),
    ],
)
def test_episode_timeout_outranks_ready_release(
    episode_age: int,
    snapshot_at: datetime | None,
    expected: DecisionAction,
) -> None:
    """§11: readiness cannot buy an open episode more than ten minutes."""
    decision = policy.evaluate_status_policy(
        _input(
            episode_started_at=NOW - timedelta(seconds=episode_age),
            close_snapshot_complete=snapshot_at is not None,
            close_snapshot_observed_at=snapshot_at,
        )
    )

    assert _action(decision) is expected


def test_ready_release_without_an_episode_is_not_time_limited() -> None:
    decision = policy.evaluate_status_policy(
        _input(incident_id=None, episode_started_at=None)
    )

    assert _action(decision) is DecisionAction.RESUME_MONITORING


def test_healthy_running_wins_over_the_episode_timeout() -> None:
    """Recovered work is not escalated while it is still confirming."""
    decision = policy.evaluate_status_policy(
        _input(
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
            episode_started_at=NOW - timedelta(minutes=30),
        )
    )

    assert decision is None


def test_in_flight_recovery_escalates_after_the_episode_timeout() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            execution_reason_code=ExecutionReasonCode.RECOVERING,
            episode_started_at=NOW - timedelta(minutes=10),
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


def test_episode_timeout_does_not_override_disabled_priority() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.USER_DISABLED,
            execution_phase=ExecutionPhase.DISABLED,
            execution_reason_code=ExecutionReasonCode.USER_DISABLED,
            episode_started_at=NOW - timedelta(minutes=10),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_episode_timeout_does_not_override_a_manual_latch() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.NO_CANDIDATE,
            candidate_count=0,
            episode_started_at=NOW - timedelta(minutes=10),
            manual_latch=policy.ManualLatch(
                incident_id=INCIDENT_ID,
                action=DecisionAction.HOLD,
                set_at=NOW - timedelta(seconds=5),
            ),
        )
    )

    assert _action(decision) is DecisionAction.HOLD


def test_resolved_episode_is_not_escalated_by_elapsed_time() -> None:
    """A running instance with a pending confirmation is not an open failure."""
    decision = policy.evaluate_status_policy(
        _input(
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
            episode_started_at=NOW - timedelta(minutes=30),
        )
    )

    assert decision is None


def test_episode_timeout_requires_an_incident() -> None:
    decision = policy.evaluate_status_policy(
        _invalidated(
            reason_code=ReasonCode.NO_CANDIDATE,
            candidate_count=0,
            incident_id=None,
            episode_started_at=None,
        )
    )

    assert _action(decision) is DecisionAction.HOLD


@pytest.mark.parametrize(
    "binding_phase", [BindingPhase.DISCOVERING, BindingPhase.VERIFYING]
)
def test_report_queue_full_is_fatal_even_with_a_verified_candidate(
    binding_phase: BindingPhase,
) -> None:
    """§12: a full queue pauses recovery and asks for a person, whatever is seen."""
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            reason_code=ReasonCode.REPORT_QUEUE_FULL, binding_phase=binding_phase
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


@pytest.mark.parametrize(
    ("execution_phase", "execution_reason", "monitor_enabled"),
    [
        (ExecutionPhase.SUSPENDED, ExecutionReasonCode.PAGE_INVALID, True),
        (ExecutionPhase.SUSPENDED, ExecutionReasonCode.NONE, True),
        (ExecutionPhase.RUNNING, ExecutionReasonCode.NONE, True),
    ],
)
def test_user_disabled_report_reason_holds_despite_inconsistent_execution(
    execution_phase: ExecutionPhase,
    execution_reason: ExecutionReasonCode,
    monitor_enabled: bool,
) -> None:
    """§11: a report that says the user disabled work never buys a recovery."""
    decision = policy.evaluate_status_policy(
        _verified_candidate(
            reason_code=ReasonCode.USER_DISABLED,
            execution_phase=execution_phase,
            execution_reason_code=execution_reason,
            monitor_enabled=monitor_enabled,
        )
    )

    assert _action(decision) is DecisionAction.HOLD


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(
            {
                "binding_phase": BindingPhase.READY,
                "execution_phase": ExecutionPhase.SUSPENDED,
                "execution_reason_code": ExecutionReasonCode.NONE,
            },
            id="ready_suspended_fresh_snapshot",
        ),
        pytest.param(
            {
                "binding_phase": BindingPhase.READY,
                "execution_phase": ExecutionPhase.RUNNING,
                "execution_reason_code": ExecutionReasonCode.NONE,
            },
            id="ready_running",
        ),
        pytest.param(
            {
                "binding_phase": BindingPhase.INVALID,
                "execution_phase": ExecutionPhase.SUSPENDED,
                "execution_reason_code": ExecutionReasonCode.RECOVERING,
            },
            id="recovering",
        ),
        pytest.param(
            {
                "binding_phase": BindingPhase.VERIFYING,
                "execution_phase": ExecutionPhase.SUSPENDED,
                "execution_reason_code": ExecutionReasonCode.PAGE_INVALID,
            },
            id="verified_candidate",
        ),
    ],
)
def test_report_queue_full_outranks_every_state_shortcut(
    state: dict[str, object],
) -> None:
    """§12: a full queue is fatal in every active shape, not only the reason path."""
    decision = policy.evaluate_status_policy(
        _input(
            reason_code=ReasonCode.REPORT_QUEUE_FULL,
            identity_verified=state["binding_phase"]
            in {BindingPhase.READY, BindingPhase.VERIFYING},
            **state,
        )
    )

    assert _action(decision) is DecisionAction.REQUIRE_USER


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param(
            {
                "execution_phase": ExecutionPhase.DISABLED,
                "execution_reason_code": ExecutionReasonCode.USER_DISABLED,
            },
            DecisionAction.HOLD,
            id="disabled_execution",
        ),
        pytest.param(
            {"monitor_enabled": False}, DecisionAction.HOLD, id="monitor_switch_off"
        ),
        pytest.param(
            {
                "manual_latch": policy.ManualLatch(
                    incident_id=INCIDENT_ID,
                    action=DecisionAction.HOLD,
                    set_at=NOW - timedelta(seconds=5),
                )
            },
            DecisionAction.HOLD,
            id="manual_hold_latch",
        ),
        pytest.param(
            {
                "manual_latch": policy.ManualLatch(
                    incident_id=INCIDENT_ID,
                    action=DecisionAction.REQUIRE_USER,
                    set_at=NOW - timedelta(seconds=5),
                )
            },
            DecisionAction.REQUIRE_USER,
            id="manual_require_user_latch",
        ),
    ],
)
def test_report_queue_full_keeps_manual_and_disabled_priorities(
    state: dict[str, object], expected: DecisionAction
) -> None:
    """§11/§12: a full queue never overrides a switch or an operator latch."""
    decision = policy.evaluate_status_policy(
        _input(reason_code=ReasonCode.REPORT_QUEUE_FULL, **state)
    )

    assert _action(decision) is expected


def test_report_queue_full_does_not_revive_a_healthy_instance_without_a_report() -> (
    None
):
    """Without the queue-full fact the healthy path is unchanged."""
    decision = policy.evaluate_status_policy(
        _input(
            incident_id=None,
            episode_started_at=None,
            execution_phase=ExecutionPhase.RUNNING,
            execution_reason_code=ExecutionReasonCode.NONE,
        )
    )

    assert decision is None


def test_naive_clock_input_is_rejected() -> None:
    naive_now = NOW.replace(tzinfo=None)

    with pytest.raises(ValueError):
        policy.evaluate_status_policy(_input(now=naive_now))
