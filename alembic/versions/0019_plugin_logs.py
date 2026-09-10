"""analytics: 新建 plugin_logs 表

插件端日志上传表，用于 Chrome 扩展（tk-adv-cost-monitor）上传运行时日志。
- seller_id / advertiser_id 定义 scope
- level CHECK: info/warn/error
- context JSONB 可选结构化上下文
- occurred_at 插件端事件时间；received_at 服务端接收时间

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UP_PLUGIN_LOGS = """
CREATE TABLE analytics.plugin_logs (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    plugin_version   TEXT NOT NULL,
    level            TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
    message          TEXT NOT NULL,
    context          JSONB,
    occurred_at      TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_plugin_logs_seller_time
    ON analytics.plugin_logs (seller_id, occurred_at DESC);

CREATE INDEX idx_plugin_logs_level
    ON analytics.plugin_logs (level, occurred_at DESC);
"""

_DOWN_PLUGIN_LOGS = """
DROP INDEX IF EXISTS analytics.idx_plugin_logs_level;
DROP INDEX IF EXISTS analytics.idx_plugin_logs_seller_time;
DROP TABLE IF EXISTS analytics.plugin_logs;
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
    # pi-lens-ignore: python-sql-injection — all identifiers are compile-time constants
    op.execute(text(_UP_PLUGIN_LOGS))
    op.execute(
        text(
            _touch_trigger_sql(
                "analytics.plugin_logs",
                "trg_analytics_plugin_logs_touch",
            )
        )
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection — all identifiers are compile-time constants
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_analytics_plugin_logs_touch ON analytics.plugin_logs"
        )
    )
    op.execute(text(_DOWN_PLUGIN_LOGS))
