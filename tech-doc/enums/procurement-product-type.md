# `procurement.procurement_products.product_type` — 妙手产品类型

> 妙手侧产品的分类（采集 / 采购 / SPU）。
> 项目**自有**枚举。

## 来源
- DB column: `procurement.procurement_products.product_type` (Text, nullable)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/procurement.py:88-90`（注释）

## 取值（✅ 固化）

| 值 | 含义 | 业务场景 |
| --- | --- | --- |
| `COLLECTED_PRODUCT` | 采集箱产品（只采集不采购） | 1688 等公开来源，仅监控价格/库存 |
| `PROCUREMENT_PRODUCT` | 采购产品（实际下单采购） | 妙手代采链路 |
| `SPU` | 内部 SPU | 项目自有 SPU 体系 |

## 引用
- 代码: `tts_erp_v2/db/models/procurement.py:88-90`
- 文档: `tech-doc/miaoshou-platform.md`
