"""tts_erp_v2.analytics — 分析/看板读侧。

本包只保留**读侧**逻辑（不写任何表）：

- ``spu_roi.py`` —— SPU 实际 ROI 看板 + 钻取面板（SQL 常量 + 公式实现）。

插件 dump 的 **ingest**（写入）侧已于 2026-09-11 迁到
``tts_erp_v2/plugin/ads/``（原 ``analytics/domain.py`` + ``repository.py``），
写入目标 schema 也从 ``analytics.*`` 收敛为 ``plugin.*`` —— 插件数据与
API 同步数据（``commerce.*`` 等）按 schema 物理隔离。
"""
