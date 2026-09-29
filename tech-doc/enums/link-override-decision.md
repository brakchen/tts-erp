# `linkage.link_overrides.decision` — 链接覆盖决策

> 运营人工对商品链接的覆盖决策（ALLOW / DENY / PRIMARY）。
> 优先级：**override > valid miaoshou product_link**（见 `effective_product_links` VIEW）。

## 来源
- DB column: `linkage.link_overrides.decision` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/linkage.py:202-205`（注释）

## 取值（✅ 固化）

| 等级 |  值 | 含义 | 业务行为 |
| :---: | --- | --- | --- |
| ✅ |  `ALLOW` | 允许该链接 | 强制纳入 effective links |
| ✅ |  `DENY` | 拒绝该链接 | 强制排除 |
| ✅ |  `PRIMARY` | 标记为主链接 | 同 ALLOW + 标记 is_primary=true |

## 引用
- 代码: `tts_erp_v2/db/models/linkage.py:202-205`
- 文档: `tts_erp_v2/db/models/linkage.py:32-39`（VIEW 优先级说明）、`tech-doc/refactor-tech-plan-v2.md §3.2`
