# `linkage.product_links.relation_type` — 商品链接关系类型

> 妙手（miaoshou）商品 ↔ TikTok 商品 的关系分类。
> 项目**自有**枚举——不是 TikTok / 妙手原始码，是 v2 标准化层定义。

## 来源
- DB column: `linkage.product_links.relation_type` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/linkage.py:114-117`（注释）

## 取值（✅ 固化）

| 值 | 含义 | 业务场景 |
| --- | --- | --- |
| `MIAOSHOU_PUBLISHED_TO_TIKTOK` | 妙手采集并发布到 TikTok | 主动上架流程（采集箱→发布） |
| `MIAOSHOU_BOUND_TO_TIKTOK` | 妙手已有产品，绑定到 TikTok 已上架产品 | 库存/订单关联（不上新架） |
| `MIAOSHOU_PROCUREMENT_SOURCE` | 妙手作为 TikTok 货源（采购源） | 采购链路（1688 等） |

## 引用
- 代码: `tts_erp_v2/db/models/linkage.py:114-117`
- 文档: `tech-doc/refactor-tech-plan-v2.md §3.2`
