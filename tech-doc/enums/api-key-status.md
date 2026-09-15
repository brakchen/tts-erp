# `security.api_keys.status` — API key 状态

> API key 自身的启用状态（与 role 正交）。

## 来源
- DB column: `security.api_keys.status` (Text, NOT NULL, default `'active'`)
- 类型: **text**（项目内部定义）

## 取值（🟡 仅观察到 1 种默认值，可能未穷尽）

| 值 | 含义 | 实测 |
| --- | --- | ---: |
| `active` | 启用 | ✓（默认值） |
| （其他） | 待发现 | 🔴 |

## 已知 gap

- 🔴 `revoked` / `suspended` / `expired` 等其他状态**未在代码里出现**
- ❌ 字段设计上预留了扩展，但**轮换 / 撤销的 API 端点未实现**

## 引用
- 代码: `tts_erp_v2/db/models/security.py:60-62`、`tts_erp_v2/db/models/security.py:65`（`rotated_to_key_hash` 暗示曾设计轮换）
