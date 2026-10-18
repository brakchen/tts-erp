"""Managed plugin status facts and canonical dedicated credential purpose.

Revision ID: 0071_plugin_status
Revises: 0070_monitor_config_delivery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0071_plugin_status"
down_revision: str | None = "0070_monitor_config_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_api_keys_purpose_shop_binding", "api_keys", schema="security"
    )
    op.create_check_constraint(
        "ck_api_keys_purpose_shop_binding",
        "api_keys",
        "purpose IN ('general', 'monitor_config', 'plugin_status') AND ("
        "(purpose = 'monitor_config' AND shop_pk IS NOT NULL) OR "
        "(purpose IN ('general', 'plugin_status') AND shop_pk IS NULL))",
        schema="security",
    )
    op.create_table(
        "status_instances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plugin_id", sa.Text(), nullable=False),
        sa.Column(
            "shop_pk",
            sa.BigInteger(),
            sa.ForeignKey("commerce.shops.id", ondelete="RESTRICT"),
        ),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("capabilities", JSONB(), nullable=False),
        sa.Column(
            "status_api_key_id",
            sa.BigInteger(),
            sa.ForeignKey("security.api_keys.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("latest_report_seq", sa.BigInteger()),
        sa.Column("latest_event_id", UUID(as_uuid=True)),
        sa.Column("latest_state", JSONB()),
        sa.Column("latest_observed_at", sa.DateTime(timezone=True)),
        sa.Column("latest_received_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("stale_since", sa.DateTime(timezone=True)),
        sa.Column("no_candidate_since", sa.DateTime(timezone=True)),
        sa.Column("current_incident_id", UUID(as_uuid=True)),
        sa.Column("current_incident_started_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_incident_id", UUID(as_uuid=True)),
        sa.Column(
            "current_decision_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("manual_latch_action", sa.Text()),
        sa.Column("manual_latch_at", sa.DateTime(timezone=True)),
        sa.Column(
            "consecutive_transport_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="plugin",
    )
    op.create_index(
        "ix_status_instances_plugin", "status_instances", ["plugin_id"], schema="plugin"
    )
    op.create_index(
        "ix_status_instances_shop", "status_instances", ["shop_pk"], schema="plugin"
    )
    op.create_index(
        "ix_status_instances_last_seen",
        "status_instances",
        ["last_seen_at"],
        schema="plugin",
    )
    op.create_table(
        "status_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instance_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("report_seq", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("incident_id", UUID(as_uuid=True)),
        sa.Column("binding_epoch", UUID(as_uuid=True)),
        sa.Column("validated_payload", JSONB(), nullable=False),
        sa.Column("receipt", JSONB(), nullable=False),
        sa.Column("response_decision", JSONB()),
        sa.UniqueConstraint("instance_id", "event_id", name="uq_status_report_event"),
        sa.UniqueConstraint("instance_id", "report_seq", name="uq_status_report_seq"),
        schema="plugin",
    )
    op.create_index(
        "ix_status_reports_instance_received",
        "status_reports",
        ["instance_id", "received_at"],
        schema="plugin",
    )
    op.create_table(
        "status_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instance_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expected_binding_epoch", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("parameters", JSONB(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column(
            "source_report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plugin.status_reports.id", ondelete="SET NULL"),
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("admin_request_id", sa.Text()),
        sa.Column(
            "delivery_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'issued'"),
        ),
        sa.Column("outcome", sa.Text()),
        sa.Column("outcome_reason_code", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("instance_id", "revision", name="uq_status_decision_rev"),
        sa.UniqueConstraint(
            "instance_id", "admin_request_id", name="uq_status_decision_admin_req"
        ),
        schema="plugin",
    )
    op.create_index(
        "ix_status_decisions_incident",
        "status_decisions",
        ["instance_id", "incident_id"],
        schema="plugin",
    )
    op.create_table(
        "status_notification_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instance_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plugin.status_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("incident_key", sa.Text(), nullable=False),
        sa.Column("notification_kind", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "state", sa.Text(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_status_outbox_dedupe"),
        schema="plugin",
    )
    op.create_index(
        "ix_status_outbox_state",
        "status_notification_outbox",
        ["state", "next_attempt_at"],
        schema="plugin",
    )


def downgrade() -> None:
    from tts_erp_v2.api.deps import require_destructive_script_guard

    require_destructive_script_guard(
        script_name="0071_plugin_status downgrade", confirmation=True
    )
    # Never erase audit facts or rewrite status credentials to general keys.
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM plugin.status_instances)
                OR EXISTS (SELECT 1 FROM plugin.status_reports)
                OR EXISTS (SELECT 1 FROM plugin.status_decisions)
                OR EXISTS (SELECT 1 FROM plugin.status_notification_outbox)
                OR EXISTS (SELECT 1 FROM security.api_keys WHERE purpose = 'plugin_status')
            THEN RAISE EXCEPTION '0071 downgrade refused: status facts or credentials exist';
            END IF;
        END $$
    """)
    )
    op.drop_table("status_notification_outbox", schema="plugin")
    op.drop_table("status_decisions", schema="plugin")
    op.drop_table("status_reports", schema="plugin")
    op.drop_table("status_instances", schema="plugin")
    op.drop_constraint(
        "ck_api_keys_purpose_shop_binding", "api_keys", schema="security"
    )
    op.create_check_constraint(
        "ck_api_keys_purpose_shop_binding",
        "api_keys",
        "purpose IN ('general', 'monitor_config') AND ("
        "(purpose = 'monitor_config' AND shop_pk IS NOT NULL) OR "
        "(purpose = 'general' AND shop_pk IS NULL))",
        schema="security",
    )
