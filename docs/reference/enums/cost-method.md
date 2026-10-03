# `reporting.product_cost_snapshots.cost_method` — 单位成本口径

> SPU 单位成本的**计算口径**——决定 `product_cost_snapshots` 行的来源。
> 这是 v9 ROI 口径的关键字段。

## 来源
- DB column: `reporting.product_cost_snapshots.cost_method` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/reporting.py:32-37`（注释）、`docs/archive/spu-real-roi-dashboard.md`

## 取值（✅ 固化，2 种）

| 等级 | 值 | 含义 | 优先级 | 备注 |
| :---: | --- | --- | :---: | --- |
| ✅ | `MANUAL_ENTRY` | 人工录入（`procurement.manual_product_costs`） | 🥇 最高 | 运营手动维护，最准确 |
| ✅ | `SOURCE_PRICE` | 货源价（妙手公共采集箱挂牌价） | 兜底 | **估算**口径——报表必须标注“估算成本” |

## 兜底行为

> `tts_erp_v2/db/models/reporting.py:35-37` 注释原文：
> “**SOURCE_PRICE = 货源价（procurement_products.source_unit_cost，公共采集箱挂牌价）兜底估算口径，报表须标注“估算成本”。SPU 无任何可用口径 ⇒ 不写行，经 monitoring 暴露。**”

即：
- 如果两种口径都拿不到 → **不写行**
- `SOURCE_PRICE` 行报表侧必须显示"估算"标签
- `active_spus_without_cost` 监控应暴露"完全无成本"的 SPU

## 引用
- 代码: `tts_erp_v2/db/models/reporting.py:32-37`
- 文档: `docs/archive/spu-real-roi-dashboard.md`、`docs/archive/roi-calc-prompt.md`
