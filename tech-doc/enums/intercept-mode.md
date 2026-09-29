# `plugin.intercept_configs.mode` — 拦截配置模式

> Chrome ext 请求拦截的两种模式：白名单 / 黑名单。

## 来源
- DB column: `plugin.intercept_configs.mode` (Text, NOT NULL, default `'whitelist'`)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/intercept.py:64-67`（注释）、`tech-doc/dumps-data-contract.md`

## 取值（✅ 固化）

| 等级 |  值 | 含义 | 行为 |
| :---: | --- | --- | --- |
| ✅ |  `whitelist` | 白名单 | **仅记录**匹配请求（含 headers/body），其他不拦截不上传 |
| ✅ |  `blacklist` | 黑名单 | **完全跳过**——匹配请求不抓取 |

## 引用
- 代码: `tts_erp_v2/db/models/intercept.py:64-67`
- 文档: `tech-doc/dumps-data-contract.md §6.1`、`tech-doc/chrome-ext-order-sync-design.md §1`
