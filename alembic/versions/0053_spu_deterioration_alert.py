"""Add materialized SPU profit deterioration alerts."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0053_spu_deterioration_alert"
down_revision: str | None = "0052_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_CONFIG_KEY = "analytics.spu_profit_deterioration_alert.v1"
_SEED_WARNING = {
    "roiAbsDelta": "0.20",
    "roiRelativeDecline": "0.20",
    "netProfitDecline": "0.25",
    "minSpendCny": "100",
    "minOrders": 3,
    "minAdOrders": 0,
}
_SEED_CRITICAL = {
    "roiAbsDelta": "0.40",
    "roiRelativeDecline": "0.40",
    "netProfitDecline": "0.40",
    "minSpendCny": "300",
    "minOrders": 5,
    "minAdOrders": 0,
}
_SEED_PAYLOAD = {
    "enabled": True,
    "maturityDays": 7,
    "fast": {
        str(days): {"warning": _SEED_WARNING, "critical": _SEED_CRITICAL}
        for days in (1, 3, 7)
    },
    "confirmation": {
        str(days): {
            "warning": {
                **_SEED_WARNING,
                "roiAbsDelta": "0.15",
                "roiRelativeDecline": "0.15",
                "netProfitDecline": "0.20",
            },
            "critical": {
                **_SEED_CRITICAL,
                "roiAbsDelta": "0.30",
                "roiRelativeDecline": "0.30",
                "netProfitDecline": "0.35",
            },
        }
        for days in (1, 3, 7)
    },
}


def _decimal_schema() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _threshold_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "roiAbsDelta",
            "roiRelativeDecline",
            "netProfitDecline",
            "minSpendCny",
            "minOrders",
            "minAdOrders",
        ],
        "properties": {
            "roiAbsDelta": _decimal_schema(),
            "roiRelativeDecline": _decimal_schema(),
            "netProfitDecline": _decimal_schema(),
            "minSpendCny": _decimal_schema(),
            "minOrders": {"type": "integer", "minimum": 0},
            "minAdOrders": {"type": "integer", "minimum": 0},
        },
    }


def _window_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["warning", "critical"],
        "properties": {
            "warning": _threshold_schema(),
            "critical": _threshold_schema(),
        },
    }


def _layer_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["1", "3", "7"],
        "properties": {str(days): _window_schema() for days in (1, 3, 7)},
    }


def _alert_schema() -> dict[str, object]:
    layer = _layer_schema()
    return {
        "title": "SPU利润劣化预警（回测暂定）",
        "description": "SPU利润劣化预警（回测暂定）运行配置。",
        "type": "object",
        "additionalProperties": False,
        "required": ["enabled", "maturityDays", "fast", "confirmation"],
        "properties": {
            "enabled": {"type": "boolean"},
            "maturityDays": {"type": "integer", "minimum": 0},
            "fast": layer,
            "confirmation": _layer_schema(),
        },
    }


def _seed_runtime_config(connection) -> None:
    # pi-lens-ignore: python-sql-injection
    connection.execute(
        text(
            """
            INSERT INTO config.runtime_config_items
                (config_key, display_name, json_schema, draft_payload,
                 draft_rollout, draft_version, published_version)
            VALUES
                (:config_key, :display_name,
                 CAST(:json_schema AS jsonb), CAST(:draft_payload AS jsonb),
                 '[]'::jsonb, 1, NULL)
            ON CONFLICT (config_key) DO NOTHING
            """
        ),
        {
            "config_key": _ALERT_CONFIG_KEY,
            "display_name": "SPU利润劣化预警（回测暂定）",
            "json_schema": json.dumps(_alert_schema(), ensure_ascii=False),
            "draft_payload": json.dumps(_SEED_PAYLOAD, ensure_ascii=False),
        },
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analytics.spu_deterioration_alerts (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            shop_pk BIGINT NOT NULL,
            spu_pk BIGINT NOT NULL,
            anchor_date DATE NOT NULL,
            window_days SMALLINT NOT NULL CHECK (window_days IN (1, 3, 7)),
            layer TEXT NOT NULL CHECK (layer IN ('fast', 'confirmation')),
            severity TEXT NOT NULL CHECK (severity IN ('none', 'warning', 'critical')),
            state TEXT NOT NULL,
            sample_status TEXT NOT NULL CHECK (sample_status IN ('sufficient', 'sample_insufficient', 'unavailable')),
            prior_roi NUMERIC(24,12), current_roi NUMERIC(24,12), roi_decline NUMERIC(24,12),
            prior_net_profit_cny NUMERIC(24,6), current_net_profit_cny NUMERIC(24,6), net_profit_decline NUMERIC(24,12),
            previous_spend_cny NUMERIC(24,6), current_spend_cny NUMERIC(24,6),
            previous_order_count INTEGER, current_order_count INTEGER,
            previous_ad_orders INTEGER, current_ad_orders INTEGER,
            effective_config_source TEXT NOT NULL, effective_config_version INTEGER,
            effective_config_updated_at TIMESTAMPTZ, effective_config_updated_by TEXT,
            config_payload_hash TEXT NOT NULL,
            basis_calculated_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_spu_deterioration_alert_anchor UNIQUE (shop_pk, spu_pk, anchor_date, window_days, layer),
            CONSTRAINT fk_spu_deterioration_alert_shop FOREIGN KEY (shop_pk) REFERENCES commerce.shops (id) ON DELETE RESTRICT,
            CONSTRAINT fk_spu_deterioration_alert_spu FOREIGN KEY (spu_pk) REFERENCES commerce.products_spu (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_spu_deterioration_alert_latest ON analytics.spu_deterioration_alerts (anchor_date, severity)"
    )
    op.execute(
        "CREATE INDEX ix_spu_deterioration_alert_shop ON analytics.spu_deterioration_alerts (shop_pk, anchor_date)"
    )
    op.execute(
        "CREATE INDEX ix_spu_deterioration_alert_severity ON analytics.spu_deterioration_alerts (severity, window_days)"
    )
    _seed_runtime_config(op.get_bind())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics.spu_deterioration_alerts")
