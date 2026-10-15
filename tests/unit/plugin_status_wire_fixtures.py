"""Shared synthetic wire-parity corpus for the plugin status protocol.

Both contract owners assert the same accept/reject set from the same literals:

- ERP producer side: ``tests/unit/test_plugin_status_contracts.py``
- browser consumer side: the plugin's ``plugin-status-contract`` Vitest suite

Add a case here and to the sibling suite in the same change, otherwise the two
validators drift and a report the server accepts can still be rejected on arrival
(or worse, the reverse). Every entry is synthetic: ``TEST_`` ids, no real
instance, key, shop or URL.

Sections: §4 state model, §6 request, §7 receipt/decision, §9 admin inputs.
"""

from __future__ import annotations

from typing import Any

INSTANCE_ID = "9439ce3d-66ca-447c-84c2-408c32cb8dd3"
EVENT_ID = "9e4436ba-d223-440e-968f-152454a89144"
INCIDENT_ID = "944aa39e-0865-4296-873d-5be7d99dcefb"
BINDING_EPOCH = "8f755b12-dbc8-4542-bf54-049f72d230fc"
DECISION_ID = "b0422ae4-ff2c-473d-9c41-a717f7636a1b"
REQUEST_ID = "620be3ac-9727-4d5c-88e4-3204e21cb201"
UPPER_INSTANCE_ID = INSTANCE_ID.upper()

_MONITOR_OFF: dict[str, Any] = {
    "monitorEnabled": True,
    "autoCloseEnabled": True,
    "closeSnapshotComplete": False,
    "closeSnapshotObservedAt": None,
}

# ── §6.2 request, verbatim from the design ───────────────────────────

GOLDEN_REPORT: dict[str, Any] = {
    "schemaVersion": 1,
    "pluginId": "ads-monitor",
    "pluginVersion": "0.4.26",
    "instanceId": INSTANCE_ID,
    "eventId": EVENT_ID,
    "reportSeq": "104",
    "observedAt": "2026-10-15T17:00:00Z",
    "eventType": "binding_invalidated",
    "reasonCode": "TAB_CLOSED",
    "scope": {
        "kind": "shop",
        "sellerId": "TEST_SELLER_A",
        "advertiserId": "TEST_ADVERTISER_A",
    },
    "incidentId": INCIDENT_ID,
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "invalid",
        "candidateCount": 1,
        "identityVerified": False,
    },
    "execution": {"phase": "suspended", "reasonCode": "PAGE_INVALID"},
    "capabilities": ["binding.recover.v1", "monitor.resume.v1"],
    "capabilityState": {"monitor": dict(_MONITOR_OFF)},
    "decisionResults": [],
}

READY_REPORT: dict[str, Any] = {
    **GOLDEN_REPORT,
    "eventType": "binding_ready",
    "reasonCode": "NONE",
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "ready",
        "candidateCount": 1,
        "identityVerified": True,
    },
    "execution": {"phase": "suspended", "reasonCode": "INITIALIZING"},
    "capabilityState": {
        "monitor": {
            "monitorEnabled": True,
            "autoCloseEnabled": True,
            "closeSnapshotComplete": True,
            "closeSnapshotObservedAt": "2026-10-15T16:59:59Z",
        }
    },
}

HEALTHY_REPORT: dict[str, Any] = {
    **GOLDEN_REPORT,
    "eventType": "heartbeat",
    "reasonCode": "NONE",
    "incidentId": None,
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "ready",
        "candidateCount": 1,
        "identityVerified": True,
    },
    "execution": {"phase": "running", "reasonCode": "NONE"},
}

USER_DISABLED_REPORT: dict[str, Any] = {
    **GOLDEN_REPORT,
    "eventType": "execution_changed",
    "reasonCode": "USER_DISABLED",
    "incidentId": None,
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "ready",
        "candidateCount": 1,
        "identityVerified": True,
    },
    "execution": {"phase": "disabled", "reasonCode": "USER_DISABLED"},
    "capabilityState": {
        "monitor": {
            "monitorEnabled": False,
            "autoCloseEnabled": False,
            "closeSnapshotComplete": False,
            "closeSnapshotObservedAt": None,
        }
    },
}

INSTALLATION_REPORT: dict[str, Any] = {
    "schemaVersion": 1,
    "pluginId": "order-data-sync",
    "pluginVersion": "1.4.0",
    "instanceId": INSTANCE_ID,
    "eventId": EVENT_ID,
    "reportSeq": "7",
    "observedAt": "2026-10-15T17:00:00Z",
    "eventType": "heartbeat",
    "reasonCode": "NONE",
    "scope": {"kind": "installation"},
    "incidentId": None,
    "binding": None,
    "execution": {"phase": "disabled", "reasonCode": "NONE"},
    "capabilities": [],
    "capabilityState": {},
    "decisionResults": [],
}

INITIALIZING_REPORT: dict[str, Any] = {
    **GOLDEN_REPORT,
    "eventType": "instance_started",
    "reasonCode": "NO_CANDIDATE",
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "discovering",
        "candidateCount": 0,
        "identityVerified": False,
    },
    "execution": {"phase": "suspended", "reasonCode": "INITIALIZING"},
}

DECISION_RESULT_REPORT: dict[str, Any] = {
    **GOLDEN_REPORT,
    "eventType": "decision_result",
    "reasonCode": "NONE",
    "binding": {
        "epoch": BINDING_EPOCH,
        "phase": "ready",
        "candidateCount": 1,
        "identityVerified": True,
    },
    "execution": {"phase": "running", "reasonCode": "NONE"},
    "decisionResults": [
        {
            "decisionId": DECISION_ID,
            "revision": "7",
            "outcome": "applied",
            "reasonCode": "NONE",
            "completedAt": "2026-10-15T17:00:00Z",
        }
    ],
}

POSITIVE_REPORTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("invalidated_report", GOLDEN_REPORT),
    ("ready_report", READY_REPORT),
    ("healthy_heartbeat", HEALTHY_REPORT),
    ("user_disabled_report", USER_DISABLED_REPORT),
    ("installation_report_without_binding", INSTALLATION_REPORT),
    ("initializing_report", INITIALIZING_REPORT),
    ("decision_result_report", DECISION_RESULT_REPORT),
)


def _report(**overrides: Any) -> dict[str, Any]:
    payload = {**GOLDEN_REPORT, **overrides}
    return payload


def _with(path: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    """Return a copy of ``path`` with one nested wire field replaced.

    ``dotted`` walks wire keys; a numeric segment indexes a list, e.g.
    ``"decisionResults.0.outcome"``.
    """
    import copy

    payload = copy.deepcopy(path)
    keys = dotted.split(".")
    cursor: Any = payload
    for key in keys[:-1]:
        cursor = cursor[int(key)] if isinstance(cursor, list) else cursor[key]
    last = keys[-1]
    if isinstance(cursor, list):
        cursor[int(last)] = value
    else:
        cursor[last] = value
    return payload


def _order_shop_report() -> dict[str, Any]:
    """A non-monitor plugin with a shop binding (its permitted capability)."""
    return {
        **GOLDEN_REPORT,
        "pluginId": "order-data-sync",
        "pluginVersion": "1.4.0",
        "scope": {"kind": "shop", "sellerId": "TEST_SELLER_A"},
        "capabilities": ["binding.recover.v1"],
        "capabilityState": {},
        "incidentId": None,
        "eventType": "heartbeat",
        "reasonCode": "NONE",
        "execution": {"phase": "running", "reasonCode": "NONE"},
        "binding": {
            "epoch": BINDING_EPOCH,
            "phase": "ready",
            "candidateCount": 1,
            "identityVerified": True,
        },
    }


def _nine_decision_results() -> list[dict[str, Any]]:
    return [
        {
            "decisionId": DECISION_ID,
            "revision": str(index),
            "outcome": "applied",
            "reasonCode": "NONE",
            "completedAt": "2026-10-15T17:00:00Z",
        }
        for index in range(1, 10)
    ]


# Every entry below must be rejected by both validators.
NEGATIVE_REPORTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("unknown_top_level_field", _report(tabId="TEST_TAB")),
    ("python_cased_field_name", _report(plugin_id="ads-monitor")),
    ("unknown_binding_field", _with(GOLDEN_REPORT, "binding.tabId", "TEST_TAB")),
    (
        "unknown_execution_field",
        _with(GOLDEN_REPORT, "execution.reason_code", "PAGE_INVALID"),
    ),
    ("report_seq_as_number", _report(reportSeq=104)),
    ("report_seq_zero", _report(reportSeq="0")),
    ("report_seq_leading_zero", _report(reportSeq="0104")),
    ("report_seq_negative", _report(reportSeq="-1")),
    ("report_seq_int64_overflow", _report(reportSeq="9223372036854775808")),
    ("report_seq_non_ascii_digits", _report(reportSeq="١٠٤")),
    ("report_seq_float_text", _report(reportSeq="104.0")),
    ("uppercase_instance_id", _report(instanceId=UPPER_INSTANCE_ID)),
    ("urn_instance_id", _report(instanceId=f"urn:uuid:{INSTANCE_ID}")),
    ("undashed_event_id", _report(eventId=EVENT_ID.replace("-", ""))),
    ("naive_observed_at", _report(observedAt="2026-10-15T17:00:00")),
    ("non_utc_observed_at", _report(observedAt="2026-10-15T17:00:00+07:00")),
    ("numeric_observed_at", _report(observedAt=1_762_000_000)),
    ("schema_version_boolean", _report(schemaVersion=True)),
    ("schema_version_text", _report(schemaVersion="1")),
    ("schema_version_future", _report(schemaVersion=2)),
    ("candidate_count_boolean", _with(GOLDEN_REPORT, "binding.candidateCount", True)),
    ("candidate_count_over_max", _with(GOLDEN_REPORT, "binding.candidateCount", 1001)),
    ("identity_verified_integer", _with(GOLDEN_REPORT, "binding.identityVerified", 1)),
    (
        "monitor_enabled_integer",
        _with(GOLDEN_REPORT, "capabilityState.monitor.monitorEnabled", 1),
    ),
    ("unregistered_capability", _report(capabilities=["monitor.resume.v2"])),
    ("unregistered_event_type", _report(eventType="binding_recovered")),
    ("unregistered_reason_code", _report(reasonCode="SESSION_EXPIRED")),
    (
        "unregistered_execution_reason",
        _with(GOLDEN_REPORT, "execution.reasonCode", "RELOADING"),
    ),
    ("unregistered_plugin_id", _report(pluginId="ads-monitor-legacy")),
    ("plugin_version_too_long", _report(pluginVersion="x" * 65)),
    ("seller_id_too_long", _with(GOLDEN_REPORT, "scope.sellerId", "S" * 129)),
    ("shop_scope_without_seller", _with(GOLDEN_REPORT, "scope", {"kind": "shop"})),
    (
        "ready_monitor_without_advertiser",
        _with(READY_REPORT, "scope", {"kind": "shop", "sellerId": "TEST_SELLER_A"}),
    ),
    (
        "snapshot_complete_without_timestamp",
        _with(GOLDEN_REPORT, "capabilityState.monitor.closeSnapshotComplete", True),
    ),
    (
        "timestamp_without_complete_snapshot",
        _with(
            GOLDEN_REPORT,
            "capabilityState.monitor.closeSnapshotObservedAt",
            "2026-10-15T16:59:59Z",
        ),
    ),
    (
        "snapshot_after_observed_at",
        _with(
            READY_REPORT,
            "capabilityState.monitor.closeSnapshotObservedAt",
            "2026-10-15T17:00:00.500Z",
        ),
    ),
    ("ads_monitor_installation_scope", _report(scope={"kind": "installation"})),
    (
        "installation_scope_with_seller",
        {
            **INSTALLATION_REPORT,
            "scope": {"kind": "installation", "sellerId": "TEST_SELLER_A"},
        },
    ),
    ("ads_monitor_without_monitor_block", _report(capabilityState={})),
    (
        "monitor_block_for_unregistered_plugin",
        {**_order_shop_report(), "capabilityState": GOLDEN_REPORT["capabilityState"]},
    ),
    (
        "binding_recover_capability_without_binding",
        {**INSTALLATION_REPORT, "capabilities": ["binding.recover.v1"]},
    ),
    (
        "installation_scope_with_binding_recover_capability",
        {
            **INSTALLATION_REPORT,
            "capabilities": ["binding.recover.v1"],
            "binding": {
                "epoch": BINDING_EPOCH,
                "phase": "ready",
                "candidateCount": 1,
                "identityVerified": True,
            },
        },
    ),
    (
        "installation_scope_with_monitor_resume_capability",
        {**INSTALLATION_REPORT, "capabilities": ["monitor.resume.v1"]},
    ),
    (
        "monitor_resume_for_other_plugin",
        {**_order_shop_report(), "capabilities": ["monitor.resume.v1"]},
    ),
    ("seventeen_capabilities", _report(capabilities=["binding.recover.v1"] * 17)),
    ("nine_decision_results", _report(decisionResults=_nine_decision_results())),
    (
        "heartbeat_with_invalid_binding_has_no_incident",
        {
            **HEALTHY_REPORT,
            "incidentId": None,
            "reasonCode": "PAGE_INVALID",
            "binding": {
                "epoch": BINDING_EPOCH,
                "phase": "invalid",
                "candidateCount": 0,
                "identityVerified": False,
            },
            "execution": {"phase": "suspended", "reasonCode": "PAGE_INVALID"},
        },
    ),
    (
        "heartbeat_recovery_without_incident",
        {
            **HEALTHY_REPORT,
            "incidentId": None,
            "binding": {
                "epoch": BINDING_EPOCH,
                "phase": "verifying",
                "candidateCount": 1,
                "identityVerified": False,
            },
            "execution": {"phase": "suspended", "reasonCode": "RECOVERING"},
        },
    ),
    (
        "heartbeat_awaiting_server_without_incident",
        {
            **HEALTHY_REPORT,
            "incidentId": None,
            "binding": {
                "epoch": BINDING_EPOCH,
                "phase": "login_required",
                "candidateCount": 0,
                "identityVerified": False,
            },
            "execution": {"phase": "suspended", "reasonCode": "AWAITING_SERVER"},
        },
    ),
    (
        "heartbeat_suspended_none_reason_has_no_incident",
        {
            **HEALTHY_REPORT,
            "incidentId": None,
            "execution": {"phase": "suspended", "reasonCode": "NONE"},
        },
    ),
    (
        "heartbeat_episode_reason_on_running_phase_has_no_incident",
        {
            **HEALTHY_REPORT,
            "incidentId": None,
            "execution": {"phase": "running", "reasonCode": "INITIALIZING"},
        },
    ),
    (
        "instance_started_without_incident",
        _report(eventType="instance_started", incidentId=None),
    ),
    (
        "decision_result_without_completed_at",
        _with(
            DECISION_RESULT_REPORT,
            "decisionResults.0",
            {
                "decisionId": DECISION_ID,
                "revision": "7",
                "outcome": "applied",
                "reasonCode": "NONE",
            },
        ),
    ),
    (
        "decision_result_unregistered_outcome",
        _with(DECISION_RESULT_REPORT, "decisionResults.0.outcome", "done"),
    ),
)


# ── §7.1 response, verbatim from the design ──────────────────────────

GOLDEN_RESPONSE: dict[str, Any] = {
    "schemaVersion": 1,
    "requestId": REQUEST_ID,
    "receipt": {
        "eventId": EVENT_ID,
        "reportSeq": "104",
        "receivedAt": "2026-10-15T17:00:01Z",
        "projectionUpdated": True,
    },
    "serverTime": "2026-10-15T17:00:01Z",
    "reportAfterSeconds": 60,
    "decision": {
        "decisionId": DECISION_ID,
        "revision": "7",
        "incidentId": INCIDENT_ID,
        "expectedBindingEpoch": BINDING_EPOCH,
        "action": "recover_binding",
        "notBefore": "2026-10-15T17:00:01Z",
        "expiresAt": "2026-10-15T17:02:01Z",
        "parameters": {"attempt": 1, "captureTimeoutSeconds": 90},
    },
}

_HOLD_RESPONSE: dict[str, Any] = {
    **GOLDEN_RESPONSE,
    "decision": {
        "decisionId": DECISION_ID,
        "revision": "8",
        "incidentId": INCIDENT_ID,
        "expectedBindingEpoch": BINDING_EPOCH,
        "action": "hold",
        "notBefore": "2026-10-15T17:00:01Z",
        "expiresAt": "2026-10-15T17:02:01Z",
        "parameters": {},
    },
}

_RESUME_RESPONSE: dict[str, Any] = {
    **_HOLD_RESPONSE,
    "decision": {**_HOLD_RESPONSE["decision"], "action": "resume_monitoring"},  # type: ignore[index]
}

_REQUIRE_USER_RESPONSE: dict[str, Any] = {
    **_HOLD_RESPONSE,
    "decision": {**_HOLD_RESPONSE["decision"], "action": "require_user"},  # type: ignore[index]
}

_NO_DECISION_RESPONSE: dict[str, Any] = {
    **GOLDEN_RESPONSE,
    "decision": None,
    "receipt": {**GOLDEN_RESPONSE["receipt"], "projectionUpdated": False},  # type: ignore[index]
}

POSITIVE_RESPONSES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("recover_binding_response", GOLDEN_RESPONSE),
    ("hold_response", _HOLD_RESPONSE),
    ("resume_monitoring_response", _RESUME_RESPONSE),
    ("require_user_response", _REQUIRE_USER_RESPONSE),
    ("response_without_decision", _NO_DECISION_RESPONSE),
)


def _decision(**overrides: Any) -> dict[str, Any]:
    return {**GOLDEN_RESPONSE, "decision": {**GOLDEN_RESPONSE["decision"], **overrides}}


# Every entry below must be rejected by both validators.
NEGATIVE_RESPONSES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("hold_with_null_parameters", _decision(action="hold", parameters=None)),
    (
        "hold_with_recover_parameters",
        _decision(
            action="hold", parameters={"attempt": 1, "captureTimeoutSeconds": 90}
        ),
    ),
    (
        "resume_with_recover_parameters",
        _decision(
            action="resume_monitoring",
            parameters={"attempt": 1, "captureTimeoutSeconds": 90},
        ),
    ),
    (
        "require_user_with_recover_parameters",
        _decision(
            action="require_user",
            parameters={"attempt": 1, "captureTimeoutSeconds": 90},
        ),
    ),
    ("recover_with_empty_parameters", _decision(parameters={})),
    (
        "recover_with_missing_parameters",
        _with(
            GOLDEN_RESPONSE,
            "decision",
            {k: v for k, v in GOLDEN_RESPONSE["decision"].items() if k != "parameters"},
        ),
    ),  # type: ignore[index]
    (
        "recover_capture_timeout_below_90",
        _with(GOLDEN_RESPONSE, "decision.parameters.captureTimeoutSeconds", 89),
    ),
    (
        "recover_capture_timeout_above_90",
        _with(GOLDEN_RESPONSE, "decision.parameters.captureTimeoutSeconds", 91),
    ),
    (
        "recover_capture_timeout_text",
        _with(GOLDEN_RESPONSE, "decision.parameters.captureTimeoutSeconds", "90"),
    ),
    (
        "recover_capture_timeout_boolean",
        _with(GOLDEN_RESPONSE, "decision.parameters.captureTimeoutSeconds", True),
    ),
    ("recover_attempt_zero", _with(GOLDEN_RESPONSE, "decision.parameters.attempt", 0)),
    ("recover_attempt_four", _with(GOLDEN_RESPONSE, "decision.parameters.attempt", 4)),
    (
        "recover_parameters_unknown_key",
        _with(GOLDEN_RESPONSE, "decision.parameters.url", "https://seller.example/ads"),
    ),
    ("unregistered_action", _decision(action="close_campaign")),
    ("revision_as_number", _with(GOLDEN_RESPONSE, "decision.revision", 7)),
    ("revision_leading_zero", _with(GOLDEN_RESPONSE, "decision.revision", "07")),
    (
        "uppercase_decision_id",
        _with(GOLDEN_RESPONSE, "decision.decisionId", DECISION_ID.upper()),
    ),
    (
        "window_reversed",
        _decision(notBefore="2026-10-15T17:02:01Z", expiresAt="2026-10-15T17:00:01Z"),
    ),
    ("window_over_two_minutes", _decision(expiresAt="2026-10-15T17:02:02Z")),
    (
        "window_without_order",
        _decision(notBefore="2026-10-15T17:00:01Z", expiresAt="2026-10-15T17:00:01Z"),
    ),
    (
        "report_after_seconds_below_range",
        _with(GOLDEN_RESPONSE, "reportAfterSeconds", 29),
    ),
    (
        "report_after_seconds_above_range",
        _with(GOLDEN_RESPONSE, "reportAfterSeconds", 301),
    ),
    ("report_after_seconds_text", _with(GOLDEN_RESPONSE, "reportAfterSeconds", "60")),
    (
        "server_time_non_utc",
        _with(GOLDEN_RESPONSE, "serverTime", "2026-10-15T17:00:01+07:00"),
    ),
    (
        "receipt_time_naive",
        _with(GOLDEN_RESPONSE, "receipt.receivedAt", "2026-10-15T17:00:01"),
    ),
    (
        "projection_updated_integer",
        _with(GOLDEN_RESPONSE, "receipt.projectionUpdated", 1),
    ),
    ("parallel_success_channel", {**GOLDEN_RESPONSE, "rowsWritten": 12}),
    (
        "request_id_not_a_uuid",
        {**GOLDEN_RESPONSE, "requestId": "req-11111111-1111-4111-8111-111111111111"},
    ),
    (
        "request_id_uppercase_uuid",
        {**GOLDEN_RESPONSE, "requestId": REQUEST_ID.upper()},
    ),
    ("schema_version_boolean", {**GOLDEN_RESPONSE, "schemaVersion": True}),
)
