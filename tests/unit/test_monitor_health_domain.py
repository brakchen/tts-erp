from __future__ import annotations

import pytest

from tts_erp_v2.plugin.monitor_health.domain import acknowledge, plan, receive

pytestmark = pytest.mark.layer_unit


def event(state=None, step="heartbeat", status="running", now=0):
    return receive(state, step, status, now)


def notices(state, now):
    return plan(state, now)[1]


def test_heartbeat_150_and_business_179_180_boundaries():
    state = event()
    assert "heartbeat:fault" not in notices(state, 149)
    assert "heartbeat:fault" in notices(state, 150)
    state = event(state, now=179)
    assert notices(state, 179) == []
    assert notices(state, 180) == ["scan:fault"]


def test_repeated_started_and_heartbeat_never_renew_progress():
    state = event()
    state = event(state, "scan", "started", 10)
    state = event(state, "scan", "started", 170)
    state = event(state, now=179)
    assert notices(state, 180) == ["scan:fault"]


def test_upstream_waits_and_real_batch_work():
    state = event()
    state = event(state, "scan", "progress", 170)
    state = event(state, now=179)
    assert notices(state, 180) == []
    state = event(state, "scan", "completed", 181)
    state = event(state, "evaluate", "completed", 182)
    for time in (300, 450, 600):
        state = event(state, "close", "progress", time)
        state = event(state, now=time)
        assert notices(state, time) == []
    state = event(state, "close", "completed", 601)
    state = event(state, now=780)
    assert notices(state, 780) == []
    assert notices(state, 781) == ["scan:fault"]


def test_failed_close_not_success_and_empty_close_can_complete():
    state = event()
    state = event(state, "scan", "completed", 1)
    state = event(state, "evaluate", "completed", 2)
    state = event(state, "close", "failed", 3)
    assert notices(state, 3) == []
    assert state["expected"] == "close"
    assert state["stages"]["close"] == "failed"
    state = event(state, "close", "completed", 4)
    assert notices(state, 4) == []


def test_stop_is_not_recovery_resume_fresh_observation():
    state, pending = plan(event(event(), now=180), 180)
    state = acknowledge(state, pending)
    state = event(state, status="stopped", now=181)
    assert notices(state, 182) == []
    assert notices(state, 331) == ["heartbeat:fault"]
    state = event(state, now=500)
    assert notices(state, 500) == []
    assert notices(state, 679) == ["heartbeat:fault"]
    state = event(state, "scan", "progress", 680)
    state = event(state, now=680)
    assert notices(state, 680) == ["scan:recovery"]


def test_success_once_failure_pending_and_distinct_recoveries():
    state, pending = plan(event(), 180)
    assert set(pending) == {"heartbeat:fault", "scan:fault"}
    assert plan(state, 181)[1] == pending  # failed send is not acknowledged
    state = acknowledge(state, pending)
    assert notices(state, 182) == []
    state = event(state, now=183)
    assert notices(state, 183) == ["heartbeat:recovery"]
    state = acknowledge(state, ["heartbeat:recovery"])
    assert notices(state, 184) == []  # heartbeat didn't recover business
    state = event(state, "scan", "completed", 185)
    assert notices(state, 185) == ["scan:recovery"]


def test_other_stage_cannot_mask_current_stall_and_input_not_mutated():
    state = event()
    changed = event(state, "close", "progress", 179)
    assert changed == state
    assert notices(event(changed, now=179), 180) == ["scan:fault"]
    assert state["heartbeat_at"] == 0


def test_failed_retry_started_does_not_cancel_pending_failure():
    state = event()
    state = event(state, "scan", "failed", 1)
    state = event(state, "scan", "started", 2)
    assert notices(state, 2) == []
    state = event(state, now=179)
    assert notices(state, 180) == ["scan:fault"]


@pytest.mark.parametrize("stage", ["scan", "evaluate", "close"])
def test_each_expected_stage_179_180_and_unstarted_downstream_wait(stage):
    state = event()
    if stage in {"evaluate", "close"}:
        state = event(state, "scan", "completed", 1)
    if stage == "close":
        state = event(state, "evaluate", "completed", 2)
    start = state["observed_at"]
    state = event(state, now=start + 179)
    assert notices(state, start + 179) == []
    assert notices(state, start + 180) == [f"{stage}:fault"]


def test_resume_does_not_send_stale_unnotified_failure_or_fake_recovery():
    state = event(event(), "scan", "failed", 1)
    state, _ = plan(state, 2)
    state = event(state, status="stopped", now=3)
    assert notices(state, 100) == []
    state = event(state, now=500)
    assert notices(state, 500) == []
    state = event(state, now=679)
    assert notices(state, 679) == []
    assert notices(state, 680) == ["scan:fault"]


def test_new_offline_onset_after_unnotified_fault_uses_fresh_deadline():
    state, _ = plan(event(), 150)
    state = event(state, now=151)
    state, pending = plan(state, 301)
    assert "heartbeat:fault" in pending
    assert state["faults"]["heartbeat"]["since"] == 301


def test_recovery_survives_recurrence_before_dispatch_and_failed_delivery():
    state, pending = plan(event(event(), now=179), 180)
    state = acknowledge(state, pending)
    state = event(state, "scan", "progress", 181)
    state = event(state, "scan", "failed", 182)
    state = event(state, now=183)
    assert notices(state, 183) == ["scan:recovery"]
    # Failed recovery delivery leaves recovery pending, even after a new timeout.
    state = event(state, now=360)
    state, pending = plan(state, 361)
    assert pending == ["scan:recovery", "scan:fault"]
    assert notices(state, 362) == pending
    state = acknowledge(state, ["scan:recovery"])
    assert notices(state, 362) == ["scan:fault"]
    state = acknowledge(state, ["scan:fault"])
    assert notices(state, 363) == []
    state = event(state, "scan", "progress", 364)
    assert notices(state, 364) == ["scan:recovery"]
    state = acknowledge(state, ["scan:recovery"])
    assert notices(state, 365) == []


def test_failed_and_retried_started_wait_for_180_seconds_without_progress():
    state = event()
    for time, status in (
        (1, "failed"),
        (14, "started"),
        (100, "failed"),
        (178, "started"),
    ):
        state = event(state, "scan", status, time)
        state = event(state, now=time)
        assert notices(state, time) == []
    assert notices(state, 179) == []
    assert notices(state, 180) == ["scan:fault"]


def test_offline_recurrence_keeps_pending_recovery_and_rearms_notice():
    state, pending = plan(event(status="stopped"), 150)
    state = acknowledge(state, pending)
    state = event(state, status="stopped", now=151)
    state, pending = plan(state, 301)
    assert pending == ["heartbeat:recovery", "heartbeat:fault"]
    state = acknowledge(state, pending)
    assert notices(state, 302) == []
    state = event(state, status="stopped", now=303)
    assert notices(state, 303) == ["heartbeat:recovery"]


@pytest.mark.parametrize("failed_step", ["evaluate", "close"])
@pytest.mark.parametrize(
    ("first_scan_status", "expected"),
    [
        ("started", "scan"),
        ("progress", "scan"),
        ("completed", "evaluate"),
        ("failed", "scan"),
    ],
)
def test_next_scan_is_observed_after_failed_downstream(
    failed_step: str, first_scan_status: str, expected: str
) -> None:
    state = event()
    state = event(state, "scan", "completed", 1)
    if failed_step == "close":
        state = event(state, "evaluate", "completed", 2)
    state = event(state, failed_step, "failed", 3)
    changed = event(state, "scan", first_scan_status, 10)
    assert changed["expected"] == expected
    assert changed["stages"]["scan"] == first_scan_status
    assert changed["observed_at"] == 10
    assert changed["stages"][failed_step] == "failed"
    assert state["expected"] == failed_step


def test_next_scan_progress_does_not_renew_failed_close_deadline() -> None:
    state = event()
    state = event(state, "scan", "completed", 1)
    state = event(state, "evaluate", "completed", 2)
    state = event(state, "close", "failed", 3)
    state = event(state, "scan", "started", 10)
    state = event(state, "scan", "progress", 180)
    state = event(state, now=181)
    assert "close:fault" not in notices(state, 181)
    assert notices(state, 182) == ["close:fault"]


def test_legacy_failed_state_accepts_next_scan_without_masking_old_failure() -> None:
    state = {
        "heartbeat_at": 179,
        "enabled": True,
        "expected": "close",
        "observed_at": 2,
        "waiting_scan": False,
        "stages": {"scan": "completed", "evaluate": "completed", "close": "failed"},
        "faults": {},
    }
    changed = event(state, "scan", "progress", 180)
    assert changed["expected"] == "scan"
    assert changed["observed_at"] == 180
    assert notices(changed, 182) == ["close:fault"]
    assert state["observed_at"] == 2


@pytest.mark.parametrize("status", ["started", "progress", "completed", "failed"])
def test_current_close_after_scheduled_join_needs_no_replayed_evaluation(
    status: str,
) -> None:
    # Capture already evaluated before the scheduled run joined its active drain.
    # Only newly observed close facts are sent; the first may be coalesced.
    state = event(event(), "scan", "completed", 1)
    changed = event(state, "close", status, 10)
    assert changed["expected"] == ("scan" if status == "completed" else "close")
    assert changed["stages"]["close"] == status
    assert changed["observed_at"] == 10
    assert "evaluate" not in changed["stages"]
    assert state["expected"] == "evaluate"
    assert "close" not in state["stages"]


def test_late_shared_close_genuine_progress_does_not_raise_missing_evaluation_fault() -> (
    None
):
    state = event(event(), "scan", "completed", 1)
    state = event(state, "close", "started", 10)
    for time in (100, 250, 400):
        state = event(state, "close", "progress", time)
        state = event(state, now=time)
        assert notices(state, time) == []
    assert "evaluate" not in state["stages"]
    state = event(state, "close", "completed", 401)
    assert state["expected"] == "scan"
    assert state["waiting_scan"] is True


def test_late_close_repeated_started_cannot_renew_its_observation() -> None:
    state = event(event(), "scan", "completed", 1)
    for time in (10, 100, 179):
        state = event(state, "close", "started", time)
    state = event(state, now=189)
    assert notices(state, 189) == []
    assert notices(state, 190) == ["close:fault"]


def test_late_close_does_not_mask_or_recover_known_failed_evaluation() -> None:
    state = event(event(), "scan", "completed", 1)
    state = event(state, "evaluate", "failed", 2)
    state = event(state, now=180)
    state, pending = plan(state, 181)
    assert pending == ["evaluate:fault"]
    state = acknowledge(state, pending)
    changed = event(state, "close", "progress", 182)
    assert changed["expected"] == "close"
    assert changed["failed_observed_at"]["evaluate"] == 1
    assert changed["stages"]["evaluate"] == "failed"
    assert changed["faults"]["evaluate"] == state["faults"]["evaluate"]
    assert notices(changed, 182) == []
    changed = event(changed, "close", "completed", 183)
    changed = event(changed, "scan", "completed", 184)
    changed = event(changed, "evaluate", "completed", 185)
    assert notices(changed, 185) == ["evaluate:recovery"]
    assert state["expected"] == "evaluate"


@pytest.mark.parametrize("status", ["started", "failed"])
def test_current_close_admission_keeps_previous_close_failure_deadline(
    status: str,
) -> None:
    state = event(event(), "scan", "completed", 1)
    state = event(state, "evaluate", "completed", 2)
    state = event(state, "close", "failed", 3)
    state = event(state, "scan", "completed", 10)
    state = event(state, "close", status, 180)
    state = event(state, now=181)
    assert state["expected"] == "close"
    assert state["failed_observed_at"]["close"] == 2
    assert state["stages"]["close"] == "failed"
    assert notices(state, 181) == []
    assert notices(state, 182) == ["close:fault"]


def test_stopped_intent_must_be_reenabled_before_current_close_can_be_observed() -> (
    None
):
    state = event(status="stopped")
    for step, status in (("scan", "completed"), ("close", "failed")):
        assert event(state, step, status, 1) == state
    state = event(state, now=2)
    state = event(state, "scan", "completed", 3)
    state = event(state, "close", "failed", 4)
    assert state["enabled"] is True
    assert state["expected"] == "close"
    assert state["stages"]["close"] == "failed"
    assert "evaluate" not in state["stages"]


@pytest.mark.parametrize("status", ["started", "progress", "completed", "failed"])
def test_close_cannot_bypass_unfinished_scan(status: str) -> None:
    state = event()
    assert event(state, "close", status, 179) == state


def test_restarted_scan_started_does_not_renew_either_observation() -> None:
    state = event()
    state = event(state, "scan", "completed", 1)
    state = event(state, "evaluate", "completed", 2)
    state = event(state, "close", "failed", 3)
    for time in (10, 100, 179):
        state = event(state, "scan", "started", time)
    state = event(state, now=189)
    assert set(notices(state, 190)) == {"close:fault", "scan:fault"}


def test_stop_resume_discards_old_failed_stage_deadlines_without_fake_recovery() -> (
    None
):
    state = event()
    state = event(state, "scan", "completed", 1)
    state = event(state, "evaluate", "completed", 2)
    state = event(state, "close", "failed", 3)
    state = event(state, now=181)
    state, pending = plan(state, 182)
    assert pending == ["close:fault"]
    state = acknowledge(state, pending)
    state = event(state, status="stopped", now=183)
    state = event(state, now=500)
    assert notices(state, 500) == []
    state = event(state, now=679)
    assert notices(state, 679) == []
    assert notices(state, 680) == ["scan:fault"]
