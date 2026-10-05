"""Add materialized SPU profit deterioration alerts."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053_spu_deterioration_alert"
down_revision: str | None = "0052_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics.spu_deterioration_alerts")
