"""Pure current-state transitions; server UTC epoch seconds, never client time.

Only the expected stage advances, except that a terminal downstream failure
allows the next scheduled scan. Failed-stage clocks survive that restart;
other stages and repeated ``started`` signals cannot renew them. Waiting for
another scan is itself observed, so a silently abandoned cycle is detectable.
"""

from __future__ import annotations

from copy import deepcopy

HEARTBEAT_SECONDS = 30
SCAN_SECONDS = 15
OFFLINE_SECONDS = 150
NO_PROGRESS_SECONDS = 180


def _failed_observations(state: dict) -> dict[str, float]:
    failed = state.setdefault("failed_observed_at", {})
    expected = state["expected"]
    # Old rows can only retain a failed expected stage; seed its known clock
    # before a later scan changes the single current-stage observation.
    if state["stages"].get(expected) == "failed":
        failed.setdefault(expected, state["observed_at"])
    return failed


def _recover(state: dict, step: str, now: float) -> None:
    fault = state["faults"].get(step)
    if fault and fault["active"]:
        if fault["notified"]:
            # One pending confirmed recovery is independent of the next current
            # fault. A recurrence must not erase it or inherit its delivered bit.
            fault.update(recovery_pending=True, recovery_since=now, notified=False)
        fault["active"] = False
        if not fault.get("recovery_pending"):
            del state["faults"][step]


def receive(state: dict | None, step: str, status: str, now: float) -> dict:
    result = (
        deepcopy(state)
        if state
        else {
            "heartbeat_at": now,
            "enabled": True,
            "expected": "scan",
            "observed_at": now,
            "waiting_scan": False,
            "stages": {},
            "faults": {},
        }
    )
    failed_observed = _failed_observations(result)
    if step == "heartbeat":
        result["heartbeat_at"] = now
        _recover(result, "heartbeat", now)
        enabled = status == "running"
        if enabled and not result["enabled"]:
            result.update(
                expected="scan",
                observed_at=now,
                waiting_scan=False,
                stages={},
                failed_observed_at={},
            )
        result["enabled"] = enabled
        return result
    if not result["enabled"]:
        return result
    if (
        step == "scan"
        and result["expected"] != "scan"
        and result["stages"].get(result["expected"]) == "failed"
    ):
        # The producer coalesces reports: a new scan may first arrive as
        # progress/completed/failed rather than started. No fake completion of
        # the previous failed stage is required or inferred.
        result.update(expected="scan", observed_at=now, waiting_scan=False)
    if step != result["expected"]:
        return result
    if status == "failed":
        failed_observed.setdefault(step, result["observed_at"])
    # A retry merely starting is not evidence that a failed attempt recovered.
    if status != "started" or result["stages"].get(step) != "failed":
        result["stages"][step] = status
    if status in {"progress", "completed"}:
        failed_observed.pop(step, None)
        result["observed_at"] = now
        _recover(result, step, now)
        if status == "completed":
            result["expected"] = {
                "scan": "evaluate",
                "evaluate": "close",
                "close": "scan",
            }[step]
            result["waiting_scan"] = step == "close"
        else:
            result["waiting_scan"] = False
    return result


def plan(state: dict, now: float) -> tuple[dict, list[str]]:
    result = deepcopy(state)
    overdue = []
    observed = dict(_failed_observations(result))
    observed.setdefault(result["expected"], result["observed_at"])
    if now - result["heartbeat_at"] >= OFFLINE_SECONDS:
        overdue.append("heartbeat")
    if result["enabled"]:
        overdue.extend(
            step
            for step, started in observed.items()
            if now - started >= NO_PROGRESS_SECONDS
        )
    for step in overdue:
        since = (
            result["heartbeat_at"] + OFFLINE_SECONDS
            if step == "heartbeat"
            else observed[step] + NO_PROGRESS_SECONDS
        )
        fault = result["faults"].setdefault(
            step, {"active": True, "notified": False, "since": since}
        )
        if not fault["active"]:
            fault["since"] = since
        fault["active"] = True
    notices = []
    for step, fault in result["faults"].items():
        if step != "heartbeat" and not result["enabled"]:
            continue  # A stop is neither a fault nor proof of recovery.
        if fault.get("recovery_pending"):
            notices.append(f"{step}:recovery")
        if fault["active"] and not fault["notified"] and step in overdue:
            notices.append(f"{step}:fault")
    return result, notices


def acknowledge(state: dict, notices: list[str]) -> dict:
    result = deepcopy(state)
    for notice in notices:
        step, kind = notice.split(":")
        if kind == "fault":
            result["faults"][step]["notified"] = True
        else:
            fault = result["faults"][step]
            fault["recovery_pending"] = False
            if not fault["active"]:
                del result["faults"][step]
    return result
