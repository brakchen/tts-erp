"""plugin.status_* — the managed plugin status domain (design §10).

Four tables, one owner:

* ``status_instances``  — provisioned instances, their dedicated credential,
  the authorised target scope and the *latest state projection*.
* ``status_reports``    — one immutable row per accepted plugin report, with
  the canonical payload hash and the receipt/decision frozen at first receipt.
* ``status_decisions``  — every decision ever *issued* (never executed here),
  the unit the per-episode recovery budget counts.
* ``status_notification_outbox`` — lifecycle notifications awaiting delivery.

``status_reports`` and ``status_decisions`` are audit facts: nothing in this
module deletes them. Only the outbox's delivery columns are mutable.

Deliberately a separate module rather than more tables in
``db/models/plugin.py``: that file already owns the ad/order business facts and
the status domain is neither ads nor orders. Table registration in
``db/models/__init__.py`` and the Alembic revision are integration-step work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base

__all__ = [
    "StatusDecision",
    "StatusInstance",
    "StatusNotificationOutbox",
    "StatusReport",
]


class StatusInstance(Base):
    """One provisioned plugin installation and its current state projection.

    ``scope`` is the *authorised target*, not an observed page identity, and is
    written only through the strict scope contract; ``shop_pk`` is the server's
    own mapping and is never accepted from a report. The advertiser may be
    registered as null once and is then locked under the row lock.
    """

    __tablename__ = "status_instances"
    __table_args__ = (
        Index("ix_status_instances_plugin", "plugin_id"),
        Index("ix_status_instances_shop", "shop_pk"),
        Index("ix_status_instances_last_seen", "last_seen_at"),
        {"schema": "plugin"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    shop_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("commerce.shops.id", ondelete="RESTRICT")
    )
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status_api_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("security.api_keys.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # ── latest-state projection (only a greater report_seq writes these) ──
    latest_report_seq: Mapped[int | None] = mapped_column(BigInteger)
    latest_event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    latest_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latest_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Network reachability only. Any accepted request advances it, including a
    #: late archival report — that proves delivery, never readiness (§10).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when the sweep first observes delivery loss; cleared by the next
    #: fresh report, so one outage produces one notification, not one per tick.
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the *current* observation first reported NO_CANDIDATE. §11 requires
    #: one notification once that has persisted for 180 seconds; anchoring to the
    #: first such report keeps it to one per episode rather than one per tick.
    no_candidate_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ── current episode ──
    current_incident_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_incident_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: The episode most recently closed by a confirmed applied resume. Kept so a
    #: late report that still carries that id updates history without
    #: resurrecting the episode (§4.1).
    resolved_incident_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_decision_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    #: Operator hold/require_user latched to the current episode (§9.5).
    manual_latch_action: Mapped[str | None] = mapped_column(Text)
    manual_latch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_transport_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class StatusReport(Base):
    """One accepted report: an immutable audit fact plus its frozen response.

    The unique constraints are the last line of defence behind the service's
    explicit conflict checks, and they are what makes concurrent duplicate
    delivery safe. ``receipt``/``response_decision`` freeze the *business* part
    of the response; ``requestId`` and ``serverTime`` are rebuilt per attempt.
    """

    __tablename__ = "status_reports"
    __table_args__ = (
        UniqueConstraint("instance_id", "event_id", name="uq_status_report_event"),
        UniqueConstraint("instance_id", "report_seq", name="uq_status_report_seq"),
        Index("ix_status_reports_instance_received", "instance_id", "received_at"),
        {"schema": "plugin"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    report_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    incident_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    binding_epoch: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    validated_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: ``None`` for an archived out-of-order report: it produced no command.
    response_decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class StatusDecision(Base):
    """Every decision ever issued. Recording is issuance, not execution.

    The per-episode recovery budget counts rows here, so a command whose
    response was lost still costs an attempt (§11). ``revision`` increases only
    when the policy or the operator changes the current instruction.
    """

    __tablename__ = "status_decisions"
    __table_args__ = (
        UniqueConstraint("instance_id", "revision", name="uq_status_decision_rev"),
        UniqueConstraint(
            "instance_id", "admin_request_id", name="uq_status_decision_admin_req"
        ),
        Index("ix_status_decisions_incident", "instance_id", "incident_id"),
        {"schema": "plugin"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incident_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    expected_binding_epoch: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_report_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin.status_reports.id", ondelete="SET NULL"),
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    #: Operator idempotency key; NULL for automatic decisions (Postgres treats
    #: NULLs as distinct, so the unique constraint only binds manual rows).
    admin_request_id: Mapped[str | None] = mapped_column(Text)
    delivery_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'issued'")
    )
    outcome: Mapped[str | None] = mapped_column(Text)
    outcome_reason_code: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class StatusNotificationOutbox(Base):
    """Instance/binding lifecycle notifications awaiting delivery.

    Only non-sensitive template parameters and controlled references live here;
    the webhook secret stays with its own secret owner (§11). ``dedupe_key`` is
    unique, so a repeated sweep cannot enqueue a second copy of the same fact.
    """

    __tablename__ = "status_notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_status_outbox_dedupe"),
        Index("ix_status_outbox_state", "state", "next_attempt_at"),
        {"schema": "plugin"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_key: Mapped[str] = mapped_column(Text, nullable=False)
    notification_kind: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
