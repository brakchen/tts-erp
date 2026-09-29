"""plugin.campaign_opt_logs 补建（2026-09-14 lane feature/plugin-shop-analytics）

背景：该表只存在于 schema_tts_erp.sql + prod 手工建表，从未有过 migration，
导致 test 库（tts_erp_v3_test）缺表，tests/plugin/ads 全部 ERROR
（UndefinedTable）。本 migration 用 CREATE TABLE IF NOT EXISTS 幂等补建，
prod 已有表则 no-op。

触发 lane：修复 ads/repository.py upsert_campaign_opt_logs 的 now() 顶替问题时
需要测试覆盖，发现 test 库无表。
"""

from alembic import op

revision: str = "0031_plugin_campaign_opt_logs"
down_revision: str | None = "0030_plugin_after_sales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin.campaign_opt_logs (
            id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            seller_id        TEXT NOT NULL,
            advertiser_id    TEXT NOT NULL,
            log_id           TEXT NOT NULL UNIQUE,
            campaign_id      TEXT NOT NULL,
            "user"           TEXT,
            opt_time         TIMESTAMPTZ NOT NULL,
            object_type      TEXT,
            object_raw_type  TEXT,
            activity_details JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_campaign_opt_logs_seller_time "
        "ON plugin.campaign_opt_logs (seller_id, opt_time)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_campaign_opt_logs_campaign "
        "ON plugin.campaign_opt_logs (campaign_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plugin.campaign_opt_logs")
