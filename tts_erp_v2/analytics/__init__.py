"""tts_erp_v2.analytics — 广告/ROI 看板读侧。

⚠ 命名历史：本包保留 ``analytics`` 名称，原因是 ``/v2/analytics/sync/*``
URL 前缀是 Chrome 扩展的 stable 契约（AGENTS.md §9.1），路由文件
``api/v2/analytics.py`` 与之同名，本包作为读侧模块沿用同一命名。
实际数据全部在 ``plugin`` schema（``plugin.ad_today`` 等），本包只读不写。

ingest（写入）侧已于 2026-09-11 迁到 ``tts_erp_v2/plugin/ads/``
（原 ``analytics/domain.py`` + ``repository.py``），schema 从 ``analytics.*``
收敛为 ``plugin.*`` —— 插件数据与 API 同步数据（``commerce.*`` 等）物理隔离。

本包只保留**读侧**逻辑：

- ``spu_roi.py`` —— SPU 实际 ROI 看板 + 钻取面板（读 ``plugin.ad_*``）。
"""
