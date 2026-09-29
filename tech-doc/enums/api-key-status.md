# `security.api_keys.status` — API key 状态

> API key 自身的启用状态（与 role 正交）。

## 来源
- DB column: `security.api_keys.status` (Text, NOT NULL, default `'active'`)
- 类型: **text**（项目内部定义）

## 取值

| 等级 | 值 | 含义 | 实测 |
| :---: | --- | --- | ---: |
| 🟡 | `active` | 启用 | ✓（默认值） |
| 🔴 | `revoked` | 已撤销（设计预留） | （未观测） |
| 🔴 | `suspended` | 已暂停（设计预留） | （未观测） |
| 🔴 | `expired` | 已过期（设计预留） | （未观测） |

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``active`` — 启用

- 🔴 **3 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``revoked`` — 已撤销（设计预留）
  - 🔴 ``suspended`` — 已暂停（设计预留）
  - 🔴 ``expired`` — 已过期（设计预留）


## 已知 gap

- 🔴 `revoked` / `suspended` / `expired` 等其他状态**未在代码里出现**
- ❌ 字段设计上预留了扩展，但**轮换 / 撤销的 API 端点未实现**

## 引用
- 代码: `tts_erp_v2/db/models/security.py:60-62`、`tts_erp_v2/db/models/security.py:65`（`rotated_to_key_hash` 暗示曾设计轮换）
