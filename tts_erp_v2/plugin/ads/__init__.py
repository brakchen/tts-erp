"""tts_erp_v2.plugin.ads — 插件广告消耗 dump 的 ingest 领域包。

数据来源：Chrome 扩展（`tiktok-shop-data-sync`）从 TikTok Seller Center 抓取
广告统计响应，经 ``POST /v2/analytics/sync/dumps`` 写入 ``plugin.*`` schema。

- ``domain.py``     —— 纯类型 + 幂等键推导（协议契约代码）
- ``repository.py`` —— 存储层（coverage 查询 / upsert ad_today·ad_daily·ad_monthly
                        / 写 ad_raw_log·plugin_logs / 跨天固化）

HTTP handler 在 ``tts_erp_v2/api/v2/analytics.py``（``/v2/analytics/sync/*``；
URL 前缀保持不变 —— 它是插件侧 stable 契约，见 AGENTS.md §9.1）。

注：本包原是 ``tts_erp_v2/analytics/``（schema 名为 ``analytics``）。2026-09-11
PLUGIN_ARCH_CLEANUP 把插件数据统一收敛到 ``plugin`` schema —— api 同步数据
（``commerce.*`` 等）与插件 dump 数据（``plugin.*``）物理隔离，不再需要
``commerce.shops.data_source`` 判定来源。ROI 看板读侧（``spu_roi.py``）留在
``tts_erp_v2/analytics/``，只改 SQL 里的 schema 名。
"""
