"""analytics: 新建 ad_today / ad_daily / ad_monthly / ad_raw_log 四表

技术方案：tech-doc/analytics/daily-sync-with-coverage.md §1
- ad_today：今天实时表（30s ON CONFLICT DO UPDATE 刷新）
- ad_daily：天级结构化表（历史数据 ON CONFLICT DO NOTHING，不可变）
- ad_monthly：月级结构化表（独立同步，不依赖 daily）
- ad_raw_log：原始请求日志（kind CHECK: daily/today/monthly）

所有表统一 created_at + updated_at（updated_at 由 fn_touch_updated_at() trigger 自动维护）。

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── ad_today ─────────────────────────────────────────────────────────
_UP_AD_TODAY = """
CREATE TABLE analytics.ad_today (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    day              DATE NOT NULL,
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),
    metrics_extra    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ad_today UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, day)
);

CREATE INDEX idx_ad_today_coverage
    ON analytics.ad_today (seller_id, advertiser_id, endpoint, campaign_id, day);
"""

_DOWN_AD_TODAY = """
DROP INDEX IF EXISTS analytics.idx_ad_today_coverage;
DROP TABLE IF EXISTS analytics.ad_today;
"""

# ── ad_daily ─────────────────────────────────────────────────────────
_UP_AD_DAILY = """
CREATE TABLE analytics.ad_daily (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    day              DATE NOT NULL,
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),
    metrics_extra    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ad_daily UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, day)
);

CREATE INDEX idx_ad_daily_coverage
    ON analytics.ad_daily (seller_id, advertiser_id, endpoint, campaign_id, day);
CREATE INDEX idx_ad_daily_product_day
    ON analytics.ad_daily (product_id, day);
"""

_DOWN_AD_DAILY = """
DROP INDEX IF EXISTS analytics.idx_ad_daily_product_day;
DROP INDEX IF EXISTS analytics.idx_ad_daily_coverage;
DROP TABLE IF EXISTS analytics.ad_daily;
"""

# ── ad_monthly ───────────────────────────────────────────────────────
_UP_AD_MONTHLY = """
CREATE TABLE analytics.ad_monthly (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    year_month       TEXT NOT NULL,
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),
    metrics_extra    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ad_monthly UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, year_month)
);

CREATE INDEX idx_ad_monthly_coverage
    ON analytics.ad_monthly (seller_id, advertiser_id, endpoint, campaign_id, year_month);
"""

_DOWN_AD_MONTHLY = """
DROP INDEX IF EXISTS analytics.idx_ad_monthly_coverage;
DROP TABLE IF EXISTS analytics.ad_monthly;
"""

# ── ad_raw_log ───────────────────────────────────────────────────────
_UP_AD_RAW_LOG = """
CREATE TABLE analytics.ad_raw_log (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    campaign_id      TEXT,
    product_id       TEXT,
    kind             TEXT NOT NULL CHECK (kind IN ('daily', 'today', 'monthly')),
    day              DATE,
    year_month       TEXT,
    request_url      TEXT NOT NULL,
    request_method   TEXT NOT NULL,
    request_body     JSONB,
    response_status  INT,
    response_body    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id       TEXT,
    source           TEXT DEFAULT 'tiktok-shop-data-sync'
);

CREATE INDEX idx_ad_raw_log_day ON analytics.ad_raw_log (day);
CREATE INDEX idx_ad_raw_log_request_id ON analytics.ad_raw_log (request_id);
"""

_DOWN_AD_RAW_LOG = """
DROP INDEX IF EXISTS analytics.idx_ad_raw_log_request_id;
DROP INDEX IF EXISTS analytics.idx_ad_raw_log_day;
DROP TABLE IF EXISTS analytics.ad_raw_log;
"""


def _touch_trigger_sql(schema_table: str, trigger_name: str) -> str:
    """Return CREATE TRIGGER DDL for fn_touch_updated_at()."""
    return (
        f"CREATE TRIGGER {trigger_name}\n"
        f"    BEFORE UPDATE ON {schema_table}\n"
        f"    FOR EACH ROW\n"
        f"    EXECUTE FUNCTION fn_touch_updated_at();"
    )


def upgrade() -> None:
    # ── Create tables ────────────────────────────────────────────────
    op.execute(text(_UP_AD_TODAY))
    op.execute(text(_UP_AD_DAILY))
    op.execute(text(_UP_AD_MONTHLY))
    op.execute(text(_UP_AD_RAW_LOG))

    # ── Create triggers (fn_touch_updated_at) ───────────────────────
    # Naming: trg_{schema}_{table}_touch (consistent with existing ad_raw trigger)
    for schema_table, trigger_name in [
        ("analytics.ad_today", "trg_analytics_ad_today_touch"),
        ("analytics.ad_daily", "trg_analytics_ad_daily_touch"),
        ("analytics.ad_monthly", "trg_analytics_ad_monthly_touch"),
        ("analytics.ad_raw_log", "trg_analytics_ad_raw_log_touch"),
    ]:
        op.execute(text(_touch_trigger_sql(schema_table, trigger_name)))


def downgrade() -> None:
    # ── Drop triggers first ─────────────────────────────────────────
    for tbl, trigger_name in [
        ("analytics.ad_raw_log", "trg_analytics_ad_raw_log_touch"),
        ("analytics.ad_monthly", "trg_analytics_ad_monthly_touch"),
        ("analytics.ad_daily", "trg_analytics_ad_daily_touch"),
        ("analytics.ad_today", "trg_analytics_ad_today_touch"),
    ]:
        op.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON {tbl}"))

    # ── Drop tables (indexes dropped with them) ─────────────────────
    op.execute(text(_DOWN_AD_RAW_LOG))
    op.execute(text(_DOWN_AD_MONTHLY))
    op.execute(text(_DOWN_AD_DAILY))
    op.execute(text(_DOWN_AD_TODAY))
