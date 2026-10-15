"""Wire-contract behaviour for ``plugin/status/report`` (design §4, §6–§9).

Pure validation: no DB, no fixtures, no HTTP. Each test states one observable
wire rule so a missing rule fails as an assertion, not as an import error.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from tests.unit.plugin_status_wire_fixtures import (
    BINDING_EPOCH,
    GOLDEN_REPORT,
    GOLDEN_RESPONSE,
    INCIDENT_ID,
    INSTANCE_ID,
    NEGATIVE_REPORTS,
    NEGATIVE_RESPONSES,
    POSITIVE_REPORTS,
    POSITIVE_RESPONSES,
)
from tts_erp_v2.plugin.status import contracts

pytestmark = pytest.mark.layer_unit

DECISION_ID = "b0422ae4-ff2c-473d-9c41-a717f7636a1b"
REQUEST_ID = "620be3ac-9727-4d5c-88e4-3204e21cb201"


def _report(**overrides: Any) -> dict[str, Any]:
    payload = json.loads(json.dumps(GOLDEN_REPORT))
    payload.update(overrides)
    return payload


def _monitor_report(**overrides: Any) -> dict[str, Any]:
    """A ready ads-monitor report (the resume/release evidence shape)."""
    payload = _report(
        eventType="binding_ready",
        reasonCode="NONE",
        binding={
            "epoch": BINDING_EPOCH,
            "phase": "ready",
            "candidateCount": 1,
            "identityVerified": True,
        },
        execution={"phase": "suspended", "reasonCode": "INITIALIZING"},
    )
    payload["capabilityState"]["monitor"].update(
        {
            "closeSnapshotComplete": True,
            "closeSnapshotObservedAt": "2026-10-15T16:59:59Z",
        }
    )
    payload.update(overrides)
    return payload


def _parse(payload: Any) -> contracts.StatusReport:
    return contracts.status_report_from_wire(payload)


# ── Golden examples ──────────────────────────────────────────────────


def test_design_example_report_parses_into_typed_facts() -> None:
    report = _parse(GOLDEN_REPORT)

    assert report.plugin_id is contracts.PluginId.ADS_MONITOR
    assert report.event_type is contracts.EventType.BINDING_INVALIDATED
    assert report.reason_code is contracts.ReasonCode.TAB_CLOSED
    assert str(report.instance_id) == INSTANCE_ID
    assert report.report_seq == "104"
    assert report.binding is not None
    assert report.binding.phase is contracts.BindingPhase.INVALID
    assert report.binding.candidate_count == 1
    assert isinstance(report.scope, contracts.ShopScope)
    assert report.scope.seller_id == "TEST_SELLER_A"
    assert report.capability_state.monitor is not None
    assert report.capability_state.monitor.close_snapshot_complete is False


def test_design_example_response_parses_into_typed_decision() -> None:
    response = contracts.StatusResponse.model_validate(GOLDEN_RESPONSE)

    assert response.receipt.projection_updated is True
    assert response.report_after_seconds == 60
    assert response.decision is not None
    assert isinstance(response.decision, contracts.RecoverBindingDecision)
    assert response.decision.action is contracts.DecisionAction.RECOVER_BINDING
    assert response.decision.revision == "7"
    assert response.decision.parameters.attempt == 1
    assert response.decision.parameters.capture_timeout_seconds == 90


def test_wire_round_trip_preserves_camel_case_keys() -> None:
    wire = contracts.status_response_to_wire(
        contracts.StatusResponse.model_validate(GOLDEN_RESPONSE)
    )

    assert set(wire) == set(GOLDEN_RESPONSE)
    assert wire["receipt"]["projectionUpdated"] is True
    assert wire["decision"]["expectedBindingEpoch"] == BINDING_EPOCH
    assert wire["decision"]["parameters"] == {
        "attempt": 1,
        "captureTimeoutSeconds": 90,
    }
    assert json.loads(json.dumps(wire)) == GOLDEN_RESPONSE


# ── Unknown fields ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        ("binding", "tabId"),
        ("execution", "tabId"),
        ("scope", "shopPk"),
        ("capabilityState", "monitor", "sellerName"),
        ("capabilityState", "extensions"),
    ],
)
def test_unknown_nested_fields_are_rejected(path: tuple[str, ...]) -> None:
    payload = _report()
    cursor: Any = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = "TEST_EXTRA"

    with pytest.raises(ValueError):
        _parse(payload)


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(tabId="TEST_TAB"))


def test_python_cased_field_name_is_rejected() -> None:
    payload = _report()
    payload["plugin_id"] = payload.pop("pluginId")

    with pytest.raises(ValueError):
        _parse(payload)


# ── Decimal int64 strings ────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        104,
        0,
        "0",
        "007",
        "-1",
        "+1",
        " 104",
        "104 ",
        "104.0",
        "1e3",
        "9223372036854775808",
    ],
)
def test_report_seq_rejects_non_canonical_int64_strings(value: Any) -> None:
    with pytest.raises(ValueError):
        _parse(_report(reportSeq=value))


def test_report_seq_accepts_int64_upper_bound() -> None:
    report = _parse(_report(reportSeq="9223372036854775807"))

    assert report.report_seq == "9223372036854775807"


def test_report_seq_rejects_non_ascii_digits() -> None:
    with pytest.raises(ValueError):
        _parse(_report(reportSeq="١٠٤"))


@pytest.mark.parametrize("value", [7, "0", "007", "-7", "9.3e7"])
def test_response_revision_rejects_non_canonical_int64_strings(value: Any) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["revision"] = value

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


# ── Integer / boolean confusion and bounds ───────────────────────────


@pytest.mark.parametrize("value", [True, False])
def test_candidate_count_rejects_booleans(value: bool) -> None:
    payload = _report()
    payload["binding"]["candidateCount"] = value

    with pytest.raises(ValueError):
        _parse(payload)


@pytest.mark.parametrize("value", [-1, 1001])
def test_candidate_count_is_bounded(value: int) -> None:
    payload = _report()
    payload["binding"]["candidateCount"] = value

    with pytest.raises(ValueError):
        _parse(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identityVerified", 1),
        ("identityVerified", "true"),
        ("identityVerified", None),
    ],
)
def test_boolean_binding_flag_rejects_integer_aliases(field: str, value: Any) -> None:
    payload = _report()
    payload["binding"][field] = value

    with pytest.raises(ValueError):
        _parse(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("capabilityState", "monitor", "monitorEnabled"), 1),
        (("capabilityState", "monitor", "autoCloseEnabled"), 0),
    ],
)
def test_boolean_monitor_flags_reject_integer_aliases(
    path: tuple[str, ...], value: Any
) -> None:
    payload = _report()
    payload[path[0]][path[1]][path[2]] = value

    with pytest.raises(ValueError):
        _parse(payload)


def test_projection_updated_flag_rejects_integer_alias() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["receipt"]["projectionUpdated"] = 1

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


# ── Identifiers and timestamps ───────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        INSTANCE_ID.upper(),
        f"urn:uuid:{INSTANCE_ID}",
        INSTANCE_ID.replace("-", ""),
        f" {INSTANCE_ID}",
        "not-a-uuid",
        7,
    ],
)
def test_instance_id_must_be_canonical_uuid(value: Any) -> None:
    with pytest.raises(ValueError):
        _parse(_report(instanceId=value))


def test_binding_epoch_must_be_canonical_uuid() -> None:
    payload = _report()
    payload["binding"]["epoch"] = BINDING_EPOCH.upper()

    with pytest.raises(ValueError):
        _parse(payload)


def test_naive_observed_at_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(observedAt="2026-10-15T17:00:00"))


def test_naive_receipt_timestamp_is_rejected() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["receipt"]["receivedAt"] = "2026-10-15T17:00:01"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_non_utc_offset_timestamp_is_rejected() -> None:
    """§6.2: wire timestamps are aware UTC text; an offset is not accepted."""
    with pytest.raises(ValueError):
        _parse(_report(observedAt="2026-10-15T17:00:00+07:00"))


def test_numeric_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(observedAt=1_762_000_000))


def test_schema_version_rejects_boolean_and_text() -> None:
    with pytest.raises(ValueError):
        _parse(_report(schemaVersion=True))

    with pytest.raises(ValueError):
        _parse(_report(schemaVersion="1"))


def test_report_after_seconds_rejects_text() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["reportAfterSeconds"] = "60"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


# ── Scope discrimination ─────────────────────────────────────────────


def test_installation_scope_rejects_seller_fields() -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={
                    "kind": "installation",
                    "sellerId": "TEST_SELLER_A",
                },
                capabilities=[],
                capabilityState={},
                binding=None,
            )
        )


def test_installation_scope_rejects_advertiser_only() -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={
                    "kind": "installation",
                    "advertiserId": "TEST_ADVERTISER_A",
                },
                capabilities=[],
                capabilityState={},
                binding=None,
            )
        )


def test_installation_scope_plugin_may_report_without_binding() -> None:
    report = _parse(
        _report(
            pluginId="order-data-sync",
            pluginVersion="1.0.0",
            scope={"kind": "installation"},
            capabilities=[],
            capabilityState={},
            binding=None,
            incidentId=None,
            eventType="heartbeat",
            execution={"phase": "disabled", "reasonCode": "NONE"},
        )
    )

    assert report.binding is None
    assert isinstance(report.scope, contracts.InstallationScope)


def test_shop_scope_requires_seller_id() -> None:
    with pytest.raises(ValueError):
        _parse(_report(scope={"kind": "shop", "advertiserId": "TEST_ADVERTISER_A"}))


def test_ads_monitor_must_use_shop_scope() -> None:
    with pytest.raises(ValueError):
        _parse(_report(scope={"kind": "installation"}))


# ── Capability registration ──────────────────────────────────────────


def test_unregistered_capability_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(capabilities=["binding.recover.v1", "monitor.resume.v2"]))


def test_capability_count_is_bounded() -> None:
    ok = _report(capabilities=["binding.recover.v1"] * 16)
    assert _parse(ok).capabilities == [contracts.Capability.BINDING_RECOVER_V1] * 16

    with pytest.raises(ValueError):
        _parse(_report(capabilities=["binding.recover.v1"] * 17))


def test_ads_monitor_capability_block_is_required() -> None:
    with pytest.raises(ValueError):
        _parse(_report(capabilityState={}))


def test_unregistered_plugin_may_not_send_monitor_block() -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={"kind": "installation"},
                capabilities=[],
                binding=None,
            )
        )


def test_non_monitor_plugin_accepts_empty_capability_block() -> None:
    report = _parse(
        _report(
            pluginId="order-data-sync",
            scope={"kind": "shop", "sellerId": "TEST_SELLER_A"},
            capabilities=["binding.recover.v1"],
            capabilityState={},
        )
    )

    assert report.capability_state.monitor is None


def test_binding_recover_capability_requires_a_binding() -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={"kind": "installation"},
                capabilities=["binding.recover.v1"],
                capabilityState={},
                binding=None,
            )
        )


def test_monitor_resume_capability_is_reserved_for_ads_monitor() -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={"kind": "shop", "sellerId": "TEST_SELLER_A"},
                capabilities=["monitor.resume.v1"],
                capabilityState={},
            )
        )


# ── Monitor close snapshot evidence ──────────────────────────────────


def test_monitor_ready_report_requires_advertiser_id() -> None:
    payload = _monitor_report()
    payload["scope"] = {"kind": "shop", "sellerId": "TEST_SELLER_A"}

    with pytest.raises(ValueError):
        _parse(payload)


def test_incomplete_snapshot_must_not_carry_a_timestamp() -> None:
    payload = _report()
    payload["capabilityState"]["monitor"].update(
        {
            "closeSnapshotComplete": False,
            "closeSnapshotObservedAt": "2026-10-15T16:59:59Z",
        }
    )

    with pytest.raises(ValueError):
        _parse(payload)


def test_complete_snapshot_requires_a_timestamp() -> None:
    payload = _report()
    payload["capabilityState"]["monitor"].update(
        {"closeSnapshotComplete": True, "closeSnapshotObservedAt": None}
    )

    with pytest.raises(ValueError):
        _parse(payload)


def test_snapshot_timestamp_cannot_be_after_observed_at() -> None:
    payload = _monitor_report()
    payload["capabilityState"]["monitor"]["closeSnapshotObservedAt"] = (
        "2026-10-15T17:00:00.500Z"
    )

    with pytest.raises(ValueError):
        _parse(payload)


def test_snapshot_timestamp_must_be_aware() -> None:
    payload = _monitor_report()
    payload["capabilityState"]["monitor"]["closeSnapshotObservedAt"] = (
        "2026-10-15T16:59:59"
    )

    with pytest.raises(ValueError):
        _parse(payload)


# ── Incident and decision results ────────────────────────────────────


@pytest.mark.parametrize(
    "event_type",
    ["instance_started", "binding_invalidated", "binding_ready", "decision_result"],
)
def test_incident_id_is_required_for_lifecycle_events(event_type: str) -> None:
    with pytest.raises(ValueError):
        _parse(_report(eventType=event_type, incidentId=None))


@pytest.mark.parametrize(
    ("binding_phase", "reason"),
    [
        ("invalid", "PAGE_INVALID"),
        ("login_required", "AWAITING_SERVER"),
        ("conflict", "IDENTITY_MISMATCH"),
    ],
)
def test_episode_binding_state_requires_an_incident_id_on_any_event(
    binding_phase: str, reason: str
) -> None:
    """§4.1: the episode id rides along with the state, not only with the event type."""
    with pytest.raises(ValueError):
        _parse(
            _report(
                eventType="heartbeat",
                incidentId=None,
                reasonCode=reason,
                binding={
                    "epoch": BINDING_EPOCH,
                    "phase": binding_phase,
                    "candidateCount": 0,
                    "identityVerified": False,
                },
                execution={"phase": "suspended", "reasonCode": reason},
            )
        )


@pytest.mark.parametrize(
    "execution_reason",
    [
        "INITIALIZING",
        "PAGE_INVALID",
        "AWAITING_SERVER",
        "RECOVERING",
        "SNAPSHOT_UNAVAILABLE",
    ],
)
def test_suspended_episode_execution_requires_an_incident_id(
    execution_reason: str,
) -> None:
    with pytest.raises(ValueError):
        _parse(
            _report(
                eventType="heartbeat",
                incidentId=None,
                reasonCode="NONE",
                binding={
                    "epoch": BINDING_EPOCH,
                    "phase": "verifying",
                    "candidateCount": 1,
                    "identityVerified": False,
                },
                execution={"phase": "suspended", "reasonCode": execution_reason},
            )
        )


def test_suspended_execution_requires_an_incident_even_without_a_reason() -> None:
    """§4.1: ``suspended`` is itself an episode state, whatever the reason says."""
    with pytest.raises(ValueError):
        _parse(
            _report(
                eventType="heartbeat",
                incidentId=None,
                reasonCode="NONE",
                binding={
                    "epoch": BINDING_EPOCH,
                    "phase": "ready",
                    "candidateCount": 1,
                    "identityVerified": True,
                },
                execution={"phase": "suspended", "reasonCode": "NONE"},
            )
        )


@pytest.mark.parametrize("execution_phase", ["running", "disabled"])
def test_episode_execution_reason_requires_an_incident_on_any_phase(
    execution_phase: str,
) -> None:
    """An episode reason is an episode statement whatever the phase claims."""
    with pytest.raises(ValueError):
        _parse(
            _report(
                eventType="heartbeat",
                incidentId=None,
                reasonCode="NONE",
                binding={
                    "epoch": BINDING_EPOCH,
                    "phase": "ready",
                    "candidateCount": 1,
                    "identityVerified": True,
                },
                execution={"phase": execution_phase, "reasonCode": "INITIALIZING"},
            )
        )


@pytest.mark.parametrize(
    ("binding_phase", "execution_reason", "reason"),
    [
        ("ready", "USER_DISABLED", "USER_DISABLED"),
        ("unbound", "USER_DISABLED", "USER_DISABLED"),
        ("discovering", "NONE", "NONE"),
        ("ready", "NONE", "NONE"),
    ],
)
def test_nonincident_user_disabled_state_may_omit_incident_id(
    binding_phase: str, execution_reason: str, reason: str
) -> None:
    """§6.2: healthy and switched-off-by-the-user states carry no episode."""
    report = _parse(
        _report(
            eventType="heartbeat",
            incidentId=None,
            reasonCode=reason,
            binding={
                "epoch": BINDING_EPOCH,
                "phase": binding_phase,
                "candidateCount": 0,
                "identityVerified": False,
            },
            execution={"phase": "disabled", "reasonCode": execution_reason},
        )
    )

    assert report.incident_id is None


def test_response_request_id_must_be_a_canonical_uuid() -> None:
    """The plugin matches this id against the UUID it sent; ``req-*`` cannot work."""
    for value in ("req-11111111-1111-4111-8111-111111111111", "not-a-uuid", ""):
        payload = copy.deepcopy(GOLDEN_RESPONSE)
        payload["requestId"] = value

        with pytest.raises(ValueError):
            contracts.StatusResponse.model_validate(payload)


def test_status_request_id_echoes_a_canonical_uuid() -> None:
    sent = "620be3ac-9727-4d5c-88e4-3204e21cb201"
    fallback = UUID("11111111-2222-4333-8444-555555555555")

    assert contracts.status_request_id(sent, fallback=fallback) == sent


@pytest.mark.parametrize(
    "header", [None, "", "req-11111111-1111-4111-8111-111111111111", "not-a-uuid"]
)
def test_status_request_id_replaces_a_non_uuid_request_id(header: str | None) -> None:
    fallback = UUID("11111111-2222-4333-8444-555555555555")

    resolved = contracts.status_request_id(header, fallback=fallback)

    assert resolved == str(fallback)
    assert (
        contracts.StatusResponse.model_validate(
            {**GOLDEN_RESPONSE, "requestId": resolved}
        ).request_id
        == fallback
    )


def test_status_request_id_generates_a_uuid_when_no_fallback_is_given() -> None:
    resolved = contracts.status_request_id(None)

    assert UUID(resolved).version == 4


def test_healthy_heartbeat_may_omit_incident_id() -> None:
    report = _parse(
        _report(
            eventType="heartbeat",
            incidentId=None,
            reasonCode="NONE",
            binding={
                "epoch": BINDING_EPOCH,
                "phase": "ready",
                "candidateCount": 1,
                "identityVerified": True,
            },
            execution={"phase": "running", "reasonCode": "NONE"},
        )
    )

    assert report.incident_id is None


def test_decision_results_are_bounded_at_eight() -> None:
    def _result(index: int) -> dict[str, Any]:
        return {
            "decisionId": "b0422ae4-ff2c-473d-9c41-a717f7636a1b",
            "revision": str(index),
            "outcome": "applied",
            "reasonCode": "NONE",
            "completedAt": "2026-10-15T17:00:00Z",
        }

    payload = _report(eventType="decision_result")
    payload["decisionResults"] = [_result(i) for i in range(1, 9)]
    assert len(_parse(payload).decision_results) == 8

    payload["decisionResults"] = [_result(i) for i in range(1, 10)]
    with pytest.raises(ValueError):
        _parse(payload)


def test_decision_result_requires_completion_timestamp() -> None:
    payload = _report(eventType="decision_result")
    payload["decisionResults"] = [
        {
            "decisionId": "b0422ae4-ff2c-473d-9c41-a717f7636a1b",
            "revision": "7",
            "outcome": "applied",
            "reasonCode": "NONE",
        }
    ]

    with pytest.raises(ValueError):
        _parse(payload)


@pytest.mark.parametrize("value", ["done", "APPLIED", "succeeded"])
def test_decision_result_outcome_is_a_registered_enum(value: str) -> None:
    payload = _report(eventType="decision_result")
    payload["decisionResults"] = [
        {
            "decisionId": "b0422ae4-ff2c-473d-9c41-a717f7636a1b",
            "revision": "7",
            "outcome": value,
            "reasonCode": "NONE",
            "completedAt": "2026-10-15T17:00:00Z",
        }
    ]

    with pytest.raises(ValueError):
        _parse(payload)


# ── Enumerations and length limits ───────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eventType", "binding_recovered"),
        ("reasonCode", "SESSION_EXPIRED"),
    ],
)
def test_unregistered_report_enum_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _parse(_report(**{field: value}))


def test_unregistered_execution_reason_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(execution={"phase": "suspended", "reasonCode": "RELOADING"}))


def test_unregistered_plugin_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(pluginId="ads-monitor-legacy"))


@pytest.mark.parametrize("version", ["x" * 65, ""])
def test_plugin_version_length_is_bounded(version: str) -> None:
    with pytest.raises(ValueError):
        _parse(_report(pluginVersion=version))


def test_seller_id_length_is_bounded() -> None:
    with pytest.raises(ValueError):
        _parse(_report(scope={"kind": "shop", "sellerId": "S" * 129}))


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(_report(schemaVersion=2))


# ── Response envelope ────────────────────────────────────────────────


@pytest.mark.parametrize("value", [29, 301])
def test_report_after_seconds_is_bounded(value: int) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["reportAfterSeconds"] = value

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_response_rejects_unknown_field() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["rowsWritten"] = 12

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_response_rejects_unregistered_decision_action() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["action"] = "close_campaign"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


@pytest.mark.parametrize("attempt", [0, 4, True])
def test_recover_attempt_is_bounded(attempt: Any) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["parameters"]["attempt"] = attempt

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_recover_parameters_reject_unknown_key() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["parameters"]["url"] = "https://seller.example/ads"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


@pytest.mark.parametrize("action", ["hold", "resume_monitoring", "require_user"])
def test_non_recover_actions_reject_parameters(action: str) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["action"] = action
    payload["decision"]["parameters"] = {"attempt": 1, "captureTimeoutSeconds": 90}

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_recover_action_requires_parameters() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["parameters"] = {}

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


@pytest.mark.parametrize("action", ["hold", "resume_monitoring", "require_user"])
def test_parameterless_actions_require_an_empty_parameters_object(action: str) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["action"] = action
    payload["decision"]["parameters"] = {}

    decision = contracts.StatusResponse.model_validate(payload).decision

    assert isinstance(
        decision,
        contracts.HoldDecision
        | contracts.ResumeMonitoringDecision
        | contracts.RequireUserDecision,
    )
    assert decision.parameters == contracts.EmptyDecisionParameters()


@pytest.mark.parametrize("action", ["hold", "resume_monitoring", "require_user"])
@pytest.mark.parametrize("parameters", [None, "{}", {"attempt": 1}])
def test_parameterless_actions_reject_missing_or_populated_parameters(
    action: str, parameters: Any
) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["action"] = action
    if parameters == "MISSING":
        payload["decision"].pop("parameters")
    else:
        payload["decision"]["parameters"] = parameters

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


@pytest.mark.parametrize("action", ["hold", "resume_monitoring", "require_user"])
def test_parameterless_actions_serialize_an_empty_object(action: str) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["action"] = action
    payload["decision"]["parameters"] = {}

    wire = contracts.status_response_to_wire(
        contracts.StatusResponse.model_validate(payload)
    )

    assert wire["decision"]["parameters"] == {}


@pytest.mark.parametrize("timeout", [1, 89, 91, 120])
def test_recover_capture_timeout_must_be_the_specified_value(timeout: int) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["parameters"]["captureTimeoutSeconds"] = timeout

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


@pytest.mark.parametrize("timeout", ["90", True, None])
def test_recover_capture_timeout_rejects_non_integer_values(timeout: Any) -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["parameters"]["captureTimeoutSeconds"] = timeout

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_decision_window_must_be_ordered() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["notBefore"] = "2026-10-15T17:02:01Z"
    payload["decision"]["expiresAt"] = "2026-10-15T17:00:01Z"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_decision_window_is_bounded_at_two_minutes() -> None:
    payload = copy.deepcopy(GOLDEN_RESPONSE)
    payload["decision"]["expiresAt"] = "2026-10-15T17:02:02Z"

    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)


def test_decision_is_open_only_inside_its_window() -> None:
    from datetime import UTC, datetime

    decision = contracts.StatusResponse.model_validate(GOLDEN_RESPONSE).decision
    assert decision is not None

    assert contracts.decision_is_open(
        decision, datetime(2026, 10, 15, 17, 1, tzinfo=UTC)
    )
    assert not contracts.decision_is_open(
        decision, datetime(2026, 10, 15, 17, 0, 0, 59, tzinfo=UTC)
    )
    assert not contracts.decision_is_open(
        decision, datetime(2026, 10, 15, 17, 2, 2, tzinfo=UTC)
    )


# ── Canonical payload hashing (§8 idempotency) ───────────────────────


def test_canonical_payload_hash_is_stable_across_retries() -> None:
    first = _parse(GOLDEN_REPORT)
    second = _parse(copy.deepcopy(GOLDEN_REPORT))

    assert contracts.report_payload_hash(first) == contracts.report_payload_hash(second)
    assert len(contracts.report_payload_hash(first)) == 64
    assert json.loads(contracts.canonical_report_json(first).decode()) == GOLDEN_REPORT


def test_canonical_payload_hash_changes_with_business_content() -> None:
    baseline = _parse(GOLDEN_REPORT)
    mutated = _parse(_report(reasonCode="ORIGIN_LEFT"))

    assert contracts.report_payload_hash(baseline) != contracts.report_payload_hash(
        mutated
    )


# ── Admin inputs (§9.4, §9.5) ───────────────────────────────────────


def test_registration_request_accepts_shop_scope_with_capabilities() -> None:
    request = contracts.InstanceRegistrationRequest.model_validate(
        {
            "pluginId": "ads-monitor",
            "scope": {
                "kind": "shop",
                "sellerId": "TEST_SELLER_A",
                "advertiserId": "TEST_ADVERTISER_A",
            },
            "capabilities": ["binding.recover.v1", "monitor.resume.v1"],
        }
    )

    assert request.plugin_id is contracts.PluginId.ADS_MONITOR


def test_registration_request_rejects_unregistered_capability() -> None:
    with pytest.raises(ValueError):
        contracts.InstanceRegistrationRequest.model_validate(
            {
                "pluginId": "ads-monitor",
                "scope": {"kind": "shop", "sellerId": "TEST_SELLER_A"},
                "capabilities": ["binding.recover.v1", "monitor.unlock.v1"],
            }
        )


def test_registration_request_rejects_installation_scope_monitor_capability() -> None:
    with pytest.raises(ValueError):
        contracts.InstanceRegistrationRequest.model_validate(
            {
                "pluginId": "ads-monitor",
                "scope": {"kind": "installation"},
                "capabilities": ["monitor.resume.v1"],
            }
        )


@pytest.mark.parametrize(
    "capabilities",
    [["binding.recover.v1"], ["binding.recover.v1", "monitor.resume.v1"]],
)
def test_registration_request_rejects_installation_binding_capability(
    capabilities: list[str],
) -> None:
    """§6.2: an installation instance may not obtain binding/monitor commands."""
    with pytest.raises(ValueError):
        contracts.InstanceRegistrationRequest.model_validate(
            {
                "pluginId": "order-data-sync",
                "scope": {"kind": "installation"},
                "capabilities": capabilities,
            }
        )


def test_registration_request_allows_installation_without_page_capabilities() -> None:
    request = contracts.InstanceRegistrationRequest.model_validate(
        {
            "pluginId": "order-data-sync",
            "scope": {"kind": "installation"},
            "capabilities": [],
        }
    )

    assert isinstance(request.scope, contracts.InstallationScope)


def test_report_rejects_installation_scope_with_binding_recover_capability() -> None:
    """A fabricated binding does not let an installation instance claim recovery."""
    with pytest.raises(ValueError):
        _parse(
            _report(
                pluginId="order-data-sync",
                scope={"kind": "installation"},
                capabilities=["binding.recover.v1"],
                capabilityState={},
                eventType="heartbeat",
                incidentId=None,
                reasonCode="NONE",
                execution={"phase": "disabled", "reasonCode": "NONE"},
                binding={
                    "epoch": BINDING_EPOCH,
                    "phase": "ready",
                    "candidateCount": 1,
                    "identityVerified": True,
                },
            )
        )


def test_report_allows_installation_scope_without_binding_capability() -> None:
    """The generic envelope stays valid: no capability, no binding."""
    report = _parse(
        _report(
            pluginId="order-data-sync",
            scope={"kind": "installation"},
            capabilities=[],
            capabilityState={},
            binding=None,
            incidentId=None,
            eventType="heartbeat",
            execution={"phase": "disabled", "reasonCode": "NONE"},
        )
    )

    assert report.binding is None


def test_registration_request_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        contracts.InstanceRegistrationRequest.model_validate(
            {
                "pluginId": "ads-monitor",
                "scope": {"kind": "shop", "sellerId": "TEST_SELLER_A"},
                "capabilities": [],
                "shopPk": 7,
            }
        )


def test_registration_request_rejects_more_than_sixteen_capabilities() -> None:
    with pytest.raises(ValueError):
        contracts.InstanceRegistrationRequest.model_validate(
            {
                "pluginId": "ads-monitor",
                "scope": {"kind": "shop", "sellerId": "TEST_SELLER_A"},
                "capabilities": ["binding.recover.v1"] * 17,
            }
        )


@pytest.mark.parametrize("action", ["hold", "recover_binding", "require_user"])
def test_admin_decision_accepts_the_manual_action_whitelist(action: str) -> None:
    request = contracts.AdminDecisionRequest.model_validate(
        {
            "requestId": "620be3ac-9727-4d5c-88e4-3204e21cb201",
            "expectedRevision": "7",
            "action": action,
            "incidentId": INCIDENT_ID,
            "expectedBindingEpoch": BINDING_EPOCH,
        }
    )

    assert request.action is contracts.ManualDecisionAction(action)


@pytest.mark.parametrize("missing", ["incidentId", "expectedBindingEpoch"])
def test_admin_decision_requires_current_context_preconditions(missing: str) -> None:
    """§9.5: the command must carry the episode and epoch it believes is current."""
    body: dict[str, Any] = {
        "requestId": "620be3ac-9727-4d5c-88e4-3204e21cb201",
        "expectedRevision": "7",
        "action": "recover_binding",
        "incidentId": INCIDENT_ID,
        "expectedBindingEpoch": BINDING_EPOCH,
    }
    body.pop(missing)

    with pytest.raises(ValueError):
        contracts.AdminDecisionRequest.model_validate(body)


@pytest.mark.parametrize("field", ["incidentId", "expectedBindingEpoch"])
@pytest.mark.parametrize("value", [None, "not-a-uuid", 7])
def test_admin_decision_preconditions_must_be_canonical_uuids(
    field: str, value: Any
) -> None:
    body: dict[str, Any] = {
        "requestId": "620be3ac-9727-4d5c-88e4-3204e21cb201",
        "expectedRevision": "7",
        "action": "hold",
        "incidentId": INCIDENT_ID,
        "expectedBindingEpoch": BINDING_EPOCH,
        field: value,
    }

    with pytest.raises(ValueError):
        contracts.AdminDecisionRequest.model_validate(body)


@pytest.mark.parametrize(
    "payload",
    [
        {"expectedRevision": "7", "action": "resume_monitoring"},
        {"expectedRevision": "7", "action": "close_campaign"},
        {"expectedRevision": "07", "action": "hold"},
        {"expectedRevision": 7, "action": "hold"},
        {"expectedRevision": "7", "action": "hold", "force": True},
    ],
)
def test_admin_decision_rejects_unsupported_or_unbounded_input(
    payload: dict[str, Any],
) -> None:
    body = {"requestId": "620be3ac-9727-4d5c-88e4-3204e21cb201", **payload}

    with pytest.raises(ValueError):
        contracts.AdminDecisionRequest.model_validate(body)


# ── Cross-language parity corpus (plan T1/T2) ────────────────────────


@pytest.mark.parametrize(("name", "payload"), POSITIVE_REPORTS, ids=lambda v: str(v))
def test_parity_positive_reports_are_accepted(
    name: str, payload: dict[str, Any]
) -> None:
    report = contracts.status_report_from_wire(payload)

    assert report.schema_version == contracts.SCHEMA_VERSION
    assert report.instance_id is not None
    assert name  # the corpus is keyed by a stable, shared case name


@pytest.mark.parametrize(("name", "payload"), NEGATIVE_REPORTS, ids=lambda v: str(v))
def test_parity_negative_reports_are_rejected(
    name: str, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        contracts.status_report_from_wire(payload)

    assert name


@pytest.mark.parametrize(("name", "payload"), POSITIVE_RESPONSES, ids=lambda v: str(v))
def test_parity_positive_responses_are_accepted(
    name: str, payload: dict[str, Any]
) -> None:
    response = contracts.StatusResponse.model_validate(payload)

    offset = response.server_time.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    assert name


@pytest.mark.parametrize(("name", "payload"), NEGATIVE_RESPONSES, ids=lambda v: str(v))
def test_parity_negative_responses_are_rejected(
    name: str, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        contracts.StatusResponse.model_validate(payload)

    assert name


@pytest.mark.parametrize(("name", "payload"), POSITIVE_RESPONSES, ids=lambda v: str(v))
def test_parity_positive_responses_are_exactly_what_python_serialises(
    name: str, payload: dict[str, Any]
) -> None:
    """The shared response literals are Python output, not hand-written guesses."""
    assert (
        contracts.status_response_to_wire(
            contracts.StatusResponse.model_validate(payload)
        )
        == payload
    )
    assert name


@pytest.mark.parametrize(("name", "payload"), POSITIVE_REPORTS, ids=lambda v: str(v))
def test_parity_positive_reports_round_trip_to_their_wire_form(
    name: str, payload: dict[str, Any]
) -> None:
    wire = contracts.status_report_from_wire(payload).model_dump(
        mode="json", by_alias=True
    )

    assert wire == payload
    assert name
