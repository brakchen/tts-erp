# `sales_order_lines.line_status` — 订单行项目状态

> 订单行（SKU 粒度）的状态，与 `sales_orders.status` 同维度但**行级别**。
> 目前**未实际使用**——空表无样本。

## 来源
- DB column: `commerce.sales_order_lines.line_status` (Text, nullable)
- 类型: **text**
- 上游: TikTok OpenAPI `/order/202309/orders/search` 响应 `line_items[].status` 字段（待核实）

## 取值

| 值 | 含义 | 备注 |
| --- | --- | --- |
| （待发现） | — | 表里目前**未观察到任何样本**——code 路径没真正落过 line_status |

## 已知 gap

- ❌ **完全未知**——`tts_erp_v2/jobs/tiktok/orders.py` 同步 job 未填充此字段
- 🔴 **任何使用此字段的代码会读 NULL**——禁止拍脑袋假设

## 引用
- 代码: `tts_erp_v2/db/models/commerce.py:230`
- 文档: 无（未列入任何已写文档）
