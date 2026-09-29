"""drop plugin.raw_log + 8 张业务表 log_id 列 + FK（2026-09-17 lane chore/deprecate-plugin-raw-log Phase 3）

背景（Phase 3 of chore/deprecate-plugin-raw-log 三阶段下线，2026-09-17）：

- Phase 1（b77cf83，2026-09-15）：alembic 0032 + write_raw_log no-op + 业务表 INSERT 不再
  带 log_id；prod 跑 0032 migration 已在 2026-09-17 09:25 UTC 完成（alembic_version
  = 0032），8 张业务表 log_id 列 nullable
- Phase 2：用户拍板跳过观察期，直接 Phase 3
  - chrome-plugins 端已在 2026-09-16 06:23 UTC 停摆（52h+ 无 dump 上传），
    与 Phase 1 部署无关
  - Phase 1 代码 review + test 库 62 passed → 代码层无 bug 信号
  - 用户决定承担"无真实流量验证"风险，直接进 Phase 3
- Phase 3（本 migration）：drop 整张 plugin.raw_log + 8 张业务表 log_id 列 + 8 条 FK

变更（**destructive**——不可逆，prod-shape dbname 需 ``ALLOW_PROD_DESTRUCTIVE=1``
由 ``alembic/env.py`` 中央 guard 拦截，agent 不自动跑 alembic upgrade；prod 由
用户手动触发）：

- DROP CONSTRAINT × 8（orders / order_lines / shipments / tracking_events /
  settlements / settlement_details / after_sales / after_sale_items 各自的
  ``<table>_log_id_fkey``）
- DROP COLUMN log_id × 8（业务表 + 售后表）
- DROP TABLE IF EXISTS plugin.raw_log CASCADE（含 3 个 ix_raw_log_* 索引 +
  raw_log_id_seq 序列）

对应代码侧 commit（同一 lane 同时改）：
- ``tts_erp_v2/db/models/plugin.py``：删 ``class RawLog`` + 8 处 log_id 字段 + 删
  ``__all__`` 导出
- ``tts_erp_v2/db/models/__init__.py``：删 ``RawLog`` 导入 + ``__all__`` 导出
- ``tts_erp_v2/plugin/orders/repository.py``：删 ``write_raw_log`` 函数 + 还原
  docstring（去掉 "DEPRECATED Phase 1" 标记）
- ``tts_erp_v2/api/v2/order_sync.py``：清 Phase 1 历史注释（3 行）
- ``tts_erp_v2/api/v2/admin.py``：删 ``_PLUGIN_ORDER_RAW_LOG`` + purge 列表项 +
  ``list_known_shops`` 的 raw_log SELECT
- ``schema_tts_erp.sql``：删 8 张业务表 log_id 列 + plugin.raw_log 表 + 索引 +
  序列 + 8 条 FK 约束
- ``tests/conftest.py``：cleanup 列表删 ``plugin.raw_log``
- ``tests/db/test_time_fields_convention.py``：删 ``plugin.raw_log 无 updated_at``
  豁免注释
- ``scripts/oneoff_backfill_plugin_order_times.py``：**整文件删除**（Phase 1
  之前 9d8ad11 已跑完回填，Phase 3 后历史 raw_log 数据也没了，脚本无意义）
- ``tech-doc/intercept-plugin-canonical.md``：§5 Phase 3 状态标 done

downgrade：**不可逆**——``raise NotImplementedError``。理由：
- DROP COLUMN 永久丢失 log_id 历史数据（prod 现存 1,456 orders + 1,493
  order_lines 的 log_id 列值）
- DROP TABLE 永久丢失 plugin.raw_log 全部历史 dump body（52,072+ 行）
- 没有"重做 dump"路径：chrome-plugins 端 dump body 是原始上传，server
  没有备份

如果未来需要恢复，需从 ``/home/schan/backups/`` 的 prod 备份（2026-09-13
之前的 7d 全量备份）恢复整库。
"""

from alembic import op

revision: str = "0033_drop_plugin_raw_log"
down_revision: str | None = "0032_make_plugin_log_id_nullable"
branch_labels = None
depends_on = None


# 8 张业务表 + 各自的 FK constraint 名字
_FK_AND_TABLES = (
    ("plugin.orders",             "orders_log_id_fkey"),
    ("plugin.order_lines",        "order_lines_log_id_fkey"),
    ("plugin.shipments",          "shipments_log_id_fkey"),
    ("plugin.tracking_events",    "tracking_events_log_id_fkey"),
    ("plugin.settlements",        "settlements_log_id_fkey"),
    ("plugin.settlement_details", "settlement_details_log_id_fkey"),
    ("plugin.after_sales",        "after_sales_log_id_fkey"),
    ("plugin.after_sale_items",   "after_sale_items_log_id_fkey"),
)


def upgrade() -> None:
    # 1. Drop 8 FK constraints explicitly（cleaner than DROP COLUMN CASCADE，
    #    也避免 raw_log 后面被 DROP 时反锁）
    for tbl, fk_name in _FK_AND_TABLES:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {fk_name}")

    # 2. Drop log_id 列 × 8
    for tbl, _fk_name in _FK_AND_TABLES:
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS log_id")

    # 3. Drop plugin.raw_log 表（CASCADE 卸 3 个 ix_raw_log_* 索引 +
    #    raw_log_id_seq 序列）
    op.execute("DROP TABLE IF EXISTS plugin.raw_log CASCADE")


def downgrade() -> None:
    # 不可逆：raw_log 历史 dump body 永久丢失，业务表 log_id 历史值永久丢失。
    # 如果未来真需要，参考 migration docstring 的"从 /home/schan/backups/ 恢复
    # 整库"路径。
    raise NotImplementedError(
        "0033_drop_plugin_raw_log 是 destructive migration，不可逆。"
        "raw_log 历史 dump body（52k+ 行）+ 业务表 log_id 历史 FK 值已永久丢失。"
        "如需恢复请从 /home/schan/backups/ 的 prod 备份恢复整库。"
    )