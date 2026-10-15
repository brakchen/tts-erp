"""Strict wire contracts for ``POST /v2/plugin/status/report``.

Source of truth: ``docs/plugins/plugin-status-report.md`` §4 (instances and
orthogonal state), §6 (request), §7 (receipt, decision whitelist, execution
confirmation) and §9 (admin inputs). Everything here is pure validation and
serialisation: no database, no HTTP, no clock reads.

Rules that are wire-level rather than business-level live here; instance
registration, persistence and the decision policy belong to
:mod:`tts_erp_v2.plugin.status.policy` and the runtime modules.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StrictBool,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION = 1
INT64_MAX = 9_223_372_036_854_775_807
MAX_CAPABILITIES = 16
MAX_DECISION_RESULTS = 8
MAX_DECISION_WINDOW_SECONDS = 120
MIN_REPORT_AFTER_SECONDS = 30
MAX_REPORT_AFTER_SECONDS = 300
RECOVER_CAPTURE_TIMEOUT_SECONDS = 90
_ASCII_DIGITS = "0123456789"

# Wire timestamp text, mirroring the plugin's reader so both ends accept exactly
# the same set: an ISO-8601 UTC instant, string only, microsecond precision.
_TIMESTAMP_TEXT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|\+00:00)"
)


class PluginId(StrEnum):
    """Registered plugin identities (§4.1). Registration, not discovery."""

    ADS_MONITOR = "ads-monitor"
    ADS_DATA_SYNC = "ads-data-sync"
    ORDER_DATA_SYNC = "order-data-sync"
    MIAOSHOU_ORDER_CAPTURE = "miaoshou-order-capture"
    TTS_REQUEST_INTERCEPT = "tts-request-intercept"


class Capability(StrEnum):
    """Registered capability ids. Unknown values are rejected, not extended."""

    BINDING_RECOVER_V1 = "binding.recover.v1"
    MONITOR_RESUME_V1 = "monitor.resume.v1"


class ScopeKind(StrEnum):
    SHOP = "shop"
    INSTALLATION = "installation"


class BindingPhase(StrEnum):
    UNBOUND = "unbound"
    DISCOVERING = "discovering"
    VERIFYING = "verifying"
    READY = "ready"
    INVALID = "invalid"
    LOGIN_REQUIRED = "login_required"
    CONFLICT = "conflict"


class ExecutionPhase(StrEnum):
    DISABLED = "disabled"
    SUSPENDED = "suspended"
    RUNNING = "running"


class ExecutionReasonCode(StrEnum):
    USER_DISABLED = "USER_DISABLED"
    INITIALIZING = "INITIALIZING"
    PAGE_INVALID = "PAGE_INVALID"
    AWAITING_SERVER = "AWAITING_SERVER"
    RECOVERING = "RECOVERING"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    NONE = "NONE"


class EventType(StrEnum):
    INSTANCE_STARTED = "instance_started"
    HEARTBEAT = "heartbeat"
    BINDING_INVALIDATED = "binding_invalidated"
    BINDING_READY = "binding_ready"
    EXECUTION_CHANGED = "execution_changed"
    DECISION_RESULT = "decision_result"


class ReasonCode(StrEnum):
    TAB_CLOSED = "TAB_CLOSED"
    ORIGIN_LEFT = "ORIGIN_LEFT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TAB_DISCARDED = "TAB_DISCARDED"
    PAGE_LOADING = "PAGE_LOADING"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    REQUEST_FAILED = "REQUEST_FAILED"
    NO_CANDIDATE = "NO_CANDIDATE"
    AMBIGUOUS_CANDIDATES = "AMBIGUOUS_CANDIDATES"
    WORKER_CONTEXT_UNKNOWN = "WORKER_CONTEXT_UNKNOWN"
    USER_DISABLED = "USER_DISABLED"
    REPORT_QUEUE_FULL = "REPORT_QUEUE_FULL"
    NONE = "NONE"


class DecisionOutcome(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DecisionResultReasonCode(StrEnum):
    NONE = "NONE"
    LOCAL_DISABLED = "LOCAL_DISABLED"
    STALE_CONTEXT = "STALE_CONTEXT"
    EXPIRED = "EXPIRED"
    UNSUPPORTED = "UNSUPPORTED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    INTERRUPTED = "INTERRUPTED"


class DecisionAction(StrEnum):
    """The only actions the server may ever send (§7.2)."""

    HOLD = "hold"
    RECOVER_BINDING = "recover_binding"
    RESUME_MONITORING = "resume_monitoring"
    REQUIRE_USER = "require_user"


class ManualDecisionAction(StrEnum):
    """Admin-actionable subset: an operator may never grant ``resume_monitoring``."""

    HOLD = "hold"
    RECOVER_BINDING = "recover_binding"
    REQUIRE_USER = "require_user"


class Connectivity(StrEnum):
    """Server-derived delivery state (§4.2). Never a page or login verdict."""

    ONLINE = "online"
    STALE = "stale"
    UNKNOWN = "unknown"


# ── Scalar wire rules ────────────────────────────────────────────────


class _WireValueError(TypeError, ValueError):
    """Wrong-typed wire value.

    Pydantic only converts ``ValueError``/``AssertionError`` raised inside a
    validator into a ``ValidationError``; a plain ``TypeError`` would escape as a
    server error instead of a 422, so the two bases are combined deliberately.
    """


def _is_canonical_uuid_text(value: str) -> bool:
    """Canonical lowercase dashed UUID text; round-trip rejects the aliases."""
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _canonical_uuid(value: Any) -> Any:
    """Accept only canonical lowercase dashed UUID text (§6).

    Round-tripping through ``UUID`` rejects the alternate spellings pydantic
    would otherwise accept (uppercase, ``urn:uuid:``, undashed).
    """
    if isinstance(value, str) and not _is_canonical_uuid_text(value):
        raise ValueError("uuid must be canonical lowercase dashed text")
    return value


def _positive_int64_decimal(value: Any) -> Any:
    """``reportSeq``/``revision`` are canonical positive decimal strings.

    A JSON number is rejected on purpose: it would have travelled through a
    JavaScript ``Number`` and lost its int64 precision (§6).
    """
    if not isinstance(value, str):
        raise _WireValueError("expected a decimal string, not a JSON number")
    if not value or not all(char in _ASCII_DIGITS for char in value):
        raise ValueError("expected a canonical positive decimal string")
    if len(value) > 1 and value[0] == "0":
        raise ValueError("expected a canonical positive decimal string")
    if int(value) < 1 or int(value) > INT64_MAX:
        raise ValueError("value outside the int64 range")
    return value


def _aware(value: datetime) -> datetime:
    """Reject naive timestamps; aware offsets are compared as instants (§6)."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _utc(value: datetime) -> datetime:
    """§6.2: wire timestamps are aware UTC text, never a local offset."""
    _aware(value)
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC (Z or +00:00)")
    return value


def _timestamp_text(value: Any) -> Any:
    """Reject a JSON number and any UTC spelling the plugin would not send."""
    if not isinstance(value, str):
        raise _WireValueError("timestamp must be an ISO-8601 string")
    if _TIMESTAMP_TEXT.fullmatch(value) is None:
        raise ValueError("timestamp must be an ISO-8601 UTC instant")
    return value


def _not_bool(value: Any) -> Any:
    if isinstance(value, bool):
        raise _WireValueError("boolean is not an integer field value")
    return value


CanonicalUuid = Annotated[UUID, BeforeValidator(_canonical_uuid)]
Int64DecimalString = Annotated[str, BeforeValidator(_positive_int64_decimal)]
UtcTimestamp = Annotated[
    datetime, BeforeValidator(_timestamp_text), AfterValidator(_utc)
]
StrictInt = Annotated[int, Strict(), BeforeValidator(_not_bool)]


class _WireModel(BaseModel):
    """Wire JSON is camelCase only; python field names are never accepted."""

    model_config = ConfigDict(extra="forbid")


# ── Scope (§4.1) ─────────────────────────────────────────────────────


class ShopScope(_WireModel):
    kind: Literal[ScopeKind.SHOP]
    seller_id: str = Field(alias="sellerId", min_length=1, max_length=128)
    advertiser_id: str | None = Field(
        default=None, alias="advertiserId", min_length=1, max_length=128
    )


class InstallationScope(_WireModel):
    kind: Literal[ScopeKind.INSTALLATION]


StatusScope = Annotated[ShopScope | InstallationScope, Field(discriminator="kind")]


# ── Reported state (§4.2) ────────────────────────────────────────────


class BindingState(_WireModel):
    epoch: CanonicalUuid
    phase: BindingPhase
    candidate_count: StrictInt = Field(alias="candidateCount", ge=0, le=1000)
    identity_verified: StrictBool = Field(alias="identityVerified")


class ExecutionState(_WireModel):
    phase: ExecutionPhase
    reason_code: ExecutionReasonCode = Field(alias="reasonCode")


class MonitorCapabilityState(_WireModel):
    """``ads-monitor``'s registered capability block; never in public execution."""

    monitor_enabled: StrictBool = Field(alias="monitorEnabled")
    auto_close_enabled: StrictBool = Field(alias="autoCloseEnabled")
    close_snapshot_complete: StrictBool = Field(alias="closeSnapshotComplete")
    close_snapshot_observed_at: UtcTimestamp | None = Field(
        default=None, alias="closeSnapshotObservedAt"
    )

    @model_validator(mode="after")
    def _snapshot_evidence_is_consistent(self) -> MonitorCapabilityState:
        if self.close_snapshot_complete and self.close_snapshot_observed_at is None:
            raise ValueError(
                "closeSnapshotComplete=true requires closeSnapshotObservedAt"
            )
        if not self.close_snapshot_complete and self.close_snapshot_observed_at:
            raise ValueError(
                "closeSnapshotObservedAt must be null when the snapshot is incomplete"
            )
        return self


class CapabilityState(_WireModel):
    monitor: MonitorCapabilityState | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_monitor(self, handler: Any) -> dict[str, Any]:
        """A plugin without the registered block sends ``{}``, not ``null`` (§6).

        Every other null in the envelope is meaningful and kept; only this
        absent capability block is dropped so both ends see the same shape.
        """
        data = handler(self)
        if data.get("monitor") is None:
            data.pop("monitor", None)
        return data


class DecisionResult(_WireModel):
    decision_id: CanonicalUuid = Field(alias="decisionId")
    revision: Int64DecimalString = Field(alias="revision")
    outcome: DecisionOutcome
    reason_code: DecisionResultReasonCode = Field(alias="reasonCode")
    completed_at: UtcTimestamp = Field(alias="completedAt")


class StatusReport(_WireModel):
    """One plugin status report. ``(instanceId, eventId)`` is the business key."""

    schema_version: StrictInt = Field(
        alias="schemaVersion", ge=SCHEMA_VERSION, le=SCHEMA_VERSION
    )
    plugin_id: PluginId = Field(alias="pluginId")
    plugin_version: str = Field(alias="pluginVersion", min_length=1, max_length=64)
    instance_id: CanonicalUuid = Field(alias="instanceId")
    event_id: CanonicalUuid = Field(alias="eventId")
    report_seq: Int64DecimalString = Field(alias="reportSeq")
    observed_at: UtcTimestamp = Field(alias="observedAt")
    event_type: EventType = Field(alias="eventType")
    reason_code: ReasonCode = Field(alias="reasonCode")
    scope: StatusScope
    incident_id: CanonicalUuid | None = Field(default=None, alias="incidentId")
    binding: BindingState | None = None
    execution: ExecutionState
    capabilities: list[Capability] = Field(max_length=MAX_CAPABILITIES)
    capability_state: CapabilityState = Field(alias="capabilityState")
    decision_results: list[DecisionResult] = Field(
        default_factory=list, alias="decisionResults", max_length=MAX_DECISION_RESULTS
    )

    @model_validator(mode="after")
    def _report_is_self_consistent(self) -> StatusReport:
        if _incident_is_required(self) and self.incident_id is None:
            raise ValueError("this report describes an episode and needs an incidentId")

        _check_scope_capabilities(
            plugin_id=self.plugin_id,
            scope=self.scope,
            capabilities=self.capabilities,
            binding_present=self.binding is not None,
        )

        if self.plugin_id is PluginId.ADS_MONITOR:
            if self.capability_state.monitor is None:
                raise ValueError("ads-monitor must report its monitor capability state")
            if self.binding is None:
                raise ValueError(
                    "ads-monitor is a page-bound plugin and needs a binding"
                )
        elif self.capability_state.monitor is not None:
            raise ValueError(
                "the monitor capability block is not registered for this plugin"
            )

        monitor = self.capability_state.monitor
        if (
            monitor is not None
            and monitor.close_snapshot_observed_at is not None
            and monitor.close_snapshot_observed_at > self.observed_at
        ):
            raise ValueError("closeSnapshotObservedAt must not be after observedAt")

        ready_monitor = (
            self.binding is not None
            and self.binding.phase is BindingPhase.READY
            and self.plugin_id is PluginId.ADS_MONITOR
        )
        if (
            ready_monitor
            and isinstance(self.scope, ShopScope)
            and (self.scope.advertiser_id is None)
        ):
            raise ValueError("a ready monitor report must carry advertiserId")
        return self


_INCIDENT_REQUIRED_EVENT_TYPES = frozenset(
    {
        EventType.INSTANCE_STARTED,
        EventType.BINDING_INVALIDATED,
        EventType.BINDING_READY,
        EventType.DECISION_RESULT,
    }
)
_EPISODE_BINDING_PHASES = frozenset(
    {BindingPhase.INVALID, BindingPhase.LOGIN_REQUIRED, BindingPhase.CONFLICT}
)
_EPISODE_EXECUTION_REASONS = frozenset(
    {
        ExecutionReasonCode.INITIALIZING,
        ExecutionReasonCode.PAGE_INVALID,
        ExecutionReasonCode.AWAITING_SERVER,
        ExecutionReasonCode.RECOVERING,
        ExecutionReasonCode.SNAPSHOT_UNAVAILABLE,
    }
)


def _incident_is_required(report: StatusReport) -> bool:
    """Whether the report describes an episode and must carry its id (§4.1).

    The predicate is a disjunction — a required lifecycle event, an invalid
    binding, a suspended execution or an episode execution reason — because any
    one of them states that an episode is open. A plain ``heartbeat`` that
    reports ``suspended`` with no reason, or that reports ``INITIALIZING`` on a
    ``running`` phase, would otherwise orphan its recovery: the episode id has to
    be part of the report. Healthy running work and a user-disabled instance
    carry no episode and keep ``incidentId`` null.
    """
    if report.event_type in _INCIDENT_REQUIRED_EVENT_TYPES:
        return True
    if report.binding is not None and report.binding.phase in _EPISODE_BINDING_PHASES:
        return True
    if report.execution.phase is ExecutionPhase.SUSPENDED:
        return True
    return report.execution.reason_code in _EPISODE_EXECUTION_REASONS


_PAGE_BOUND_CAPABILITIES = (Capability.BINDING_RECOVER_V1, Capability.MONITOR_RESUME_V1)


def _check_scope_capabilities(
    *,
    plugin_id: PluginId,
    scope: ShopScope | InstallationScope,
    capabilities: list[Capability],
    binding_present: bool | None,
) -> None:
    """Scope is an authorisation target, not a guessed page identity (§4.1).

    An installation has no page and therefore no shop target, so it may never
    obtain binding or monitor commands — neither when the instance is
    registered nor when a report claims a binding that the scope cannot
    support (§6.2).
    """
    if not isinstance(scope, ShopScope) and any(
        capability in capabilities for capability in _PAGE_BOUND_CAPABILITIES
    ):
        raise ValueError(
            "an installation scope cannot obtain binding or monitor commands"
        )
    if plugin_id is PluginId.ADS_MONITOR and not isinstance(scope, ShopScope):
        raise ValueError("ads-monitor must use shop scope")
    if (
        Capability.MONITOR_RESUME_V1 in capabilities
        and plugin_id is not PluginId.ADS_MONITOR
    ):
        raise ValueError("monitor.resume.v1 is reserved for ads-monitor")
    if Capability.BINDING_RECOVER_V1 in capabilities and binding_present is False:
        raise ValueError("binding.recover.v1 requires a binding context")


# ── Response (§7.1, §7.2) ────────────────────────────────────────────


class Receipt(_WireModel):
    event_id: CanonicalUuid = Field(alias="eventId")
    report_seq: Int64DecimalString = Field(alias="reportSeq")
    received_at: UtcTimestamp = Field(alias="receivedAt")
    projection_updated: StrictBool = Field(alias="projectionUpdated")


class RecoverBindingParameters(_WireModel):
    attempt: StrictInt = Field(alias="attempt", ge=1, le=3)
    capture_timeout_seconds: StrictInt = Field(
        alias="captureTimeoutSeconds",
        ge=RECOVER_CAPTURE_TIMEOUT_SECONDS,
        le=RECOVER_CAPTURE_TIMEOUT_SECONDS,
    )


class EmptyDecisionParameters(_WireModel):
    """``{}`` — the parameter shape of the three parameterless actions."""


class _DecisionBase(_WireModel):
    decision_id: CanonicalUuid = Field(alias="decisionId")
    revision: Int64DecimalString = Field(alias="revision")
    incident_id: CanonicalUuid = Field(alias="incidentId")
    expected_binding_epoch: CanonicalUuid = Field(alias="expectedBindingEpoch")
    not_before: UtcTimestamp = Field(alias="notBefore")
    expires_at: UtcTimestamp = Field(alias="expiresAt")

    @model_validator(mode="after")
    def _window_is_bounded(self) -> _DecisionBase:
        if self.not_before >= self.expires_at:
            raise ValueError("notBefore must be earlier than expiresAt")
        window = (self.expires_at - self.not_before).total_seconds()
        if window > MAX_DECISION_WINDOW_SECONDS:
            raise ValueError("the decision validity window is at most 120 seconds")
        return self


class HoldDecision(_DecisionBase):
    action: Literal[DecisionAction.HOLD]
    parameters: EmptyDecisionParameters


class RecoverBindingDecision(_DecisionBase):
    action: Literal[DecisionAction.RECOVER_BINDING]
    parameters: RecoverBindingParameters


class ResumeMonitoringDecision(_DecisionBase):
    action: Literal[DecisionAction.RESUME_MONITORING]
    parameters: EmptyDecisionParameters


class RequireUserDecision(_DecisionBase):
    action: Literal[DecisionAction.REQUIRE_USER]
    parameters: EmptyDecisionParameters


StatusDecision = Annotated[
    HoldDecision
    | RecoverBindingDecision
    | ResumeMonitoringDecision
    | RequireUserDecision,
    Field(discriminator="action"),
]


class StatusResponse(_WireModel):
    """One receipt plus at most one current decision, or ``null``."""

    schema_version: StrictInt = Field(
        alias="schemaVersion", ge=SCHEMA_VERSION, le=SCHEMA_VERSION
    )
    request_id: CanonicalUuid = Field(alias="requestId")
    receipt: Receipt
    server_time: UtcTimestamp = Field(alias="serverTime")
    report_after_seconds: StrictInt = Field(
        alias="reportAfterSeconds",
        ge=MIN_REPORT_AFTER_SECONDS,
        le=MAX_REPORT_AFTER_SECONDS,
    )
    decision: StatusDecision | None = None


# ── Admin inputs (§9.4, §9.5) ────────────────────────────────────────


class InstanceRegistrationRequest(_WireModel):
    """Provision one managed instance and mint its dedicated status key."""

    plugin_id: PluginId = Field(alias="pluginId")
    scope: StatusScope
    capabilities: list[Capability] = Field(max_length=MAX_CAPABILITIES)

    @model_validator(mode="after")
    def _registration_is_authorisable(self) -> InstanceRegistrationRequest:
        _check_scope_capabilities(
            plugin_id=self.plugin_id,
            scope=self.scope,
            capabilities=self.capabilities,
            binding_present=None,
        )
        return self


class AdminDecisionRequest(_WireModel):
    """Manual hold/recover/require_user with optimistic locking (§9.5).

    ``incident_id`` and ``expected_binding_epoch`` are mandatory: the command
    states which episode and binding context the operator acted on, and the
    runtime must compare them with the current values under the instance lock
    rather than infer them. No field can grant a cross-identity release.
    """

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    expected_revision: Int64DecimalString = Field(alias="expectedRevision")
    action: ManualDecisionAction
    incident_id: CanonicalUuid = Field(alias="incidentId")
    expected_binding_epoch: CanonicalUuid = Field(alias="expectedBindingEpoch")


# ── Pure helpers ─────────────────────────────────────────────────────


def status_report_from_wire(payload: Any) -> StatusReport:
    """Validate one wire body. Unknown fields and unregistered values raise."""
    return StatusReport.model_validate(payload)


def status_request_id(
    client_request_id: str | None, *, fallback: UUID | None = None
) -> str:
    """Resolve the request id carried by the status envelope (§7.1).

    The browser reader treats ``requestId`` as a canonical UUID and matches it
    against the id it sent, while the shared
    ``tts_erp_v2.api.v2._common.request_id`` echoes any header text and returns
    ``req-<uuid4>`` when the header is absent. Rather than change that
    project-wide contract, the status boundary echoes the client's id when it is
    already canonical and mints a UUID otherwise, so the envelope can never
    carry a value its only consumer rejects.

    ``fallback`` exists for deterministic callers and tests; production leaves it
    unset and gets a fresh UUID.
    """
    if client_request_id and _is_canonical_uuid_text(client_request_id):
        return client_request_id
    return str(fallback if fallback is not None else uuid4())


def status_response_to_wire(response: StatusResponse) -> dict[str, Any]:
    """Serialise a response with camelCase aliases, keeping explicit nulls."""
    return response.model_dump(mode="json", by_alias=True)


def canonical_report_json(report: StatusReport) -> bytes:
    """Canonical JSON of the validated facts, used for the §8 payload hash.

    Derived from the parsed model rather than the raw body so that a retry of
    the same business content hashes identically regardless of key order or
    whitespace, and so a same-``eventId`` mutation is detectable.
    """
    return json.dumps(
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def report_payload_hash(report: StatusReport) -> str:
    return hashlib.sha256(canonical_report_json(report)).hexdigest()


def decision_is_open(decision: StatusDecision, at: datetime) -> bool:
    """Whether a decision's start window is still open at ``at``.

    The window limits when an action may *start*; it is not a runtime lease and
    never re-suspends work that already resumed (§7.2).
    """
    _aware(at)
    return decision.not_before <= at <= decision.expires_at
