# `reporting.product_cost_snapshots.cost_method` — 单位采购成本来源

当前只接受两种已确认的成本来源：

| 值 | 来源 | 优先级 |
| --- | --- | --- |
| `MANUAL_ENTRY` | `procurement.manual_product_costs` 当前有效人工成本 | 1 |
| `PURCHASE_PRICE` | `miaoshou.purchase_prices` 已匹配店铺和 SPU 的 CNY 采购成交价 | 2 |

公共采集箱的 `procurement.procurement_products.source_unit_cost` 是 1688 挂牌价，保留作货源参考和成交价差异核验，不创建成本快照，也不进入 ROI。历史 `SOURCE_PRICE` 快照保留为审计事实，成本查询、覆盖率和日利润计算忽略它。两种成交成本均缺失时不写新快照，SPU 进入缺成本清单；SPU ROI 使用 `DEFAULT_K1` 40 CNY/件并标明来源。
