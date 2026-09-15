# `plugin.ad_raw_log.kind` — 广告 raw log 类型

> 广告 dump 的粒度分类（按日/按当前/按月）。
> 字段用 `CheckConstraint` 强制约束。

## 来源
- DB column: `plugin.ad_raw_log.kind` (Text, NOT NULL)
- DB constraint: `ck_ad_raw_log_kind: kind IN ('daily', 'today', 'monthly')`
- 类型: **text**（✅ 数据库层 CK 约束）
- 文档锚点: `tts_erp_v2/db/models/plugin.py:497-501`

## 取值（✅ CK 约束固化）

| 值 | 含义 | 用途 |
| --- | --- | --- |
| `daily` | 天级 dump | 历史日数据 |
| `today` | 实时 dump | 30s ON CONFLICT DO UPDATE 刷新 |
| `monthly` | 月级 dump | 月度聚合 |

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:497-501`
- 文档: `tech-doc/analytics/daily-sync-with-coverage.md §1.1-§1.3`
