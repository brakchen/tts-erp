# `integration.credentials.provider` / `procurement_accounts.provider` — 凭证提供方

> 区分 TikTok OpenAPI 凭证 vs 妙手 license。

## 来源
- DB column A: `integration.credentials.provider` (Text, NOT NULL)
- DB column B: `procurement.procurement_accounts.provider` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/integration.py:51-65`、`tts_erp_v2/db/models/procurement.py:51`

## 取值（✅ 固化）

| 值 | 含义 | 凭证类型 |
| --- | --- | --- |
| `tiktok` | TikTok OpenAPI 凭证 | access_token / refresh_token（Fernet 加密） |
| `miaoshou` | 妙手 license | licenseId / appSecret |

## 引用
- 代码: `tts_erp_v2/db/models/integration.py:51-65`
- 文档: `tech-doc/architecture-overview.md §4`（凭证管理单源）
