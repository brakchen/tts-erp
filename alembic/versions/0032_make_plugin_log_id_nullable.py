"""plugin.*.log_id 列 nullable 化（2026-09-15 lane chore/deprecate-plugin-raw-log Phase 1）

背景（chore/deprecate-plugin-raw-log 三阶段下线计划）：

- plugin.raw_log 是 chrome-ext dumps 流水，38,037 行（实测 2026-09-15 14:00 UTC），
  ~2257 行/h 持续写入，2 个店铺，1315 个 distinct endpoint
- 8 张业务表（orders / order_lines / shipments / tracking_events / settlements /
  settlement_details / after_sales / after_sale_items）log_id 列强 FK →
  plugin.raw_log(id)，且 NOT NULL
- prod 实测 6 张业务表（shipments / tracking_events / settlements /
  settlement_details / after_sales / after_sale_items）**完全空**，FK
  引用实际只服务 orders + order_lines 两张
- 用户 2026-09-15 拍板：方案 B（保守），三阶段下线：
  - **Phase 1（本 migration + 同 lane 代码 commit）**：
    log_id 列改 nullable + write_raw_log no-op + 业务表 INSERT 不再写 log_id；
    进入 1 天观察期
  - Phase 2：1 天观察期（验证 chrome 端 dumps 仍正常 + 业务表是否还能
    upsert——如果业务表在 raw_log 停写后 24h 仍正常增长，证明 raw_log 真的只是审计层）
  - Phase 3：alembic 0033 DROP CONSTRAINT FK + DROP COLUMN log_id + DROP
    TABLE plugin.raw_log + DROP SEQUENCE raw_log_id_d
- 本 migration 是 Phase 1 的 schema 配套：让 log_id 可空，让写 None 不
  违反 NOT NULL 约束；FK 约束保留（Phase 3 才一并 DROP）

变更（**非破坏性**——只放宽约束，不丢任何 prod 数据；
prod-shape dbname 需 ``ALLOW_PROD_DESTRUCTIVE=1`` 由 ``alembic/env.py`` 中央
guard 拦截，agent 不自动跑 alembic upgrade；prod 由用户手动触发）：

- 8 张 plugin.* 表的 log_id 列 ``ALTER COLUMN ... DROP NOT NULL``
- 无新索引 / 无新 FK / 无新列

downgrade（理论上的逆向）：``ALTER COLUMN ... SET NOT NULL``——但**会失败**如果
观察期已有业务表写入 log_id=NULL 的行；本 migration 不承诺可逆，
downgrade 留 no-op 注释提醒手动处理。
"""

from alembic import op

revision: str = "0032_make_plugin_log_id_nullable"
down_revision: str | None = "0031_plugin_campaign_opt_logs"
branch_labels = None
depends_on = None


_TABLES = (
    "plugin.orders",
    "plugin.order_lines",
    "plugin.shipments",
    "plugin.tracking_events",
    "plugin.settlements",
    "plugin.settlement_details",
    "plugin.after_sales",
    "plugin.after_sale_items",
)


def upgrade() -> None:
    for tbl in _TABLES:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN log_id DROP NOT NULL")


def downgrade() -> None:
    # 不可逆（观察期业务表已写 log_id=NULL）。手动处理：
    #   1) 先 UPDATE plugin.<tbl> SET log_id = NULL WHERE log_id IS NULL  (no-op)
    #   2) 再决定是否回填历史 raw_log.id 或保持 nullable 终态
    # 这里保留语句以便 test 库可逆（test 库观察期后清空，可逆）：
    for tbl in _TABLES:
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN log_id SET NOT NULL")