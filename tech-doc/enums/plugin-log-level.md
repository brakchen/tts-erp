# `plugin.plugin_logs.level` — 插件日志级别

> Chrome ext 上传日志的级别。
> 字段用 `CheckConstraint` 强制约束。

## 来源
- DB column: `plugin.plugin_logs.level` (Text, NOT NULL)
- DB constraint: `ck_plugin_logs_level: level IN ('info', 'warn', 'error')`
- 类型: **text**（✅ 数据库层 CK 约束）
- 文档锚点: `tts_erp_v2/db/models/plugin.py:541-547`

## 取值（✅ CK 约束固化）

| 等级 |  值 | 含义 |
| :---: | --- | --- |
| ✅ |  `info` | 信息 |
| ✅ |  `warn` | 警告 |
| ✅ |  `error` | 错误 |

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:541-547`
