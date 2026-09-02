"""tts_erp_v2.analytics — Chrome extension (tk-adv-cost-monitor) analytics ingest 领域包。

2026-09-02 v2 化（tech-doc/analytics-v2-migration-plan.md）：从仓库根的
``analytics_sync/`` 孤岛包迁入 v2 体系：

- ``domain.py``     —— 纯类型 + 幂等键推导（协议契约代码，零逻辑平移）
- ``repository.py`` —— 存储层（SQLAlchemy session 工厂，schema = analytics.ad_*）

HTTP handler 在 ``tts_erp_v2/api/v2/analytics.py``（/v2/analytics/sync/*）。
"""
