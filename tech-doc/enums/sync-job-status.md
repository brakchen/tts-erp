# `integration.sync_jobs.status` — 同步任务状态

> 每个同步 job 一次执行的状态机。

## 来源
- DB column: `integration.sync_jobs.status` (Text, NOT NULL, default `'running'`)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/integration.py:124-128`（注释）、`tech-doc/architecture-overview.md §3`

## 取值（✅ 固化）

| 值 | 含义 | 终态？ |
| --- | --- | :---: |
| `running` | 执行中 | ❌ |
| `succeeded` | 成功 | ✅ |
| `failed` | 失败 | ✅ |

## 状态机

```
running → succeeded
   │
   └────→ failed
```

## 引用
- 代码: `tts_erp_v2/db/models/integration.py:124-128`
- 文档: `tech-doc/architecture-overview.md §3`
