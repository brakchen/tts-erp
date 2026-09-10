"""drop deprecated v3 analytics leftovers — ad_product_links view + ad_raw/ad_sync_audit tables

Revision ID: 0020_drop_v3_analytics_leftovers
Revises: 0019
Create Date: 2026-09-11

背景（2026-09-11 引用面审计）：

- `analytics.ad_raw` / `analytics.ad_sync_audit` 由 migration 0012/0013 创建，
  属 **v3 区间聚合协议**（kind history/today + day_start/day_end 区间语义）。
  该协议已被 v4 逐日协议取代，`db/models/analytics.py` 早已写明「ORM 模型已移除，
  如需 DROP 请通过新的 alembic migration 执行」。
- `analytics.ad_raw` 已**冻结**：现行 `repository.py` 只写 `ad_raw_log`
  （全仓库无 `INSERT INTO analytics.ad_raw`）。最后真实写入为 2026-09-09。
- `analytics.ad_product_links` 视图（0006 建、0014 改）是 `ad_raw` 的**唯一**依赖；
  而该视图**零生产消费者**：SPU ROI 的 `_SQL_ROI_AD`
  （`tts_erp_v2/analytics/spu_roi.py:85`）直接读 `ad_daily UNION ALL ad_today`
  并自行 JOIN `commerce.shops`/`commerce.products_spu`，从不经过此视图。
- `pg_depend` 校验：`ad_product_links` 无任何下游对象；`ad_raw` 的下游只有该视图。

删除顺序必须是**先视图后表**（视图依赖表）。

备份（回滚材料，本迁移不重建数据）：
- `/home/schan/backups/analytics_ad_raw_20260911_0118.sql.gz`（1467 行）
- `/home/schan/backups/analytics_ad_sync_audit_*.sql.gz`

引用清理配套：`tests/analytics/test_ad_product_links_view.py`（9 用例）随视图一并删除；
`schema_tts_erp.sql` 由 `scripts/regen_schema.py` 重生成。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0020_drop_v3_analytics_leftovers"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    """先删视图、再删表 —— 视图是 ad_raw 的唯一依赖者，顺序不可颠倒。

    两张表用 alembic 原生的 ``op.drop_table``（无 SQL 字符串）；
    视图没有对应的 alembic API，只能用一条字面量 DDL（无插值、无外部输入）。
    """
    # 视图：alembic 无 op.drop_view，仅此一处原生 DDL
    op.execute("DROP VIEW IF EXISTS analytics.ad_product_links")
    op.drop_table("ad_raw", schema="analytics")
    op.drop_table("ad_sync_audit", schema="analytics")


def downgrade() -> None:
    """本迁移删除的是**真实数据**，DDL 无法逆 —— 回滚走备份。

    刻意不在此处重建空表/视图：
    `ad_product_links` 依赖 `ad_raw`，只重建空的 `ad_raw` + 视图会得到
    「视图在、永远 0 行」，这种静默失真比明确报错更难排查。

    回滚步骤：
      1. `gunzip -c /home/schan/backups/analytics_ad_raw_*.sql.gz | psql -d tts_erp`
      2. 同理恢复 `analytics_ad_sync_audit_*.sql.gz`
      3. `alembic stamp 0019`
    """
    raise RuntimeError(
        "本迁移刻意不提供 DDL 级回滚：ad_raw / ad_sync_audit 的数据已删除，"
        "无法从结构恢复。请用 pg_restore 从 /home/schan/backups/ 的备份恢复后，"
        "执行 alembic stamp 0019。"
    )
