# `commerce.sales_orders.status` — 订单状态

> 订单的**全生命周期状态**，由 TikTok API 同步后归一化的文本值。
> 注意：**这是 v2 标准化后的 text**，不是卖家中心原始 int。卖家中心 int 码见 [`main-order-status.md`](main-order-status.md)。

## 来源
- DB column: `commerce.sales_orders.status` (Text, nullable)
- 类型: **text**
- 上游: TikTok OpenAPI `/order/202309/orders/search` 响应 `status` 字段
- 文档锚点: `tech-doc/order-domain-business-rules.md §1`

## 取值（✅ 固化）

| 值 | 含义 | 终态？ | 关键时间戳 |
| --- | --- | :---: | --- |
| `UNPAID` | 未付款 | ❌ | `order_time` |
| `ON_HOLD` | 挂起 | ❌ | — |
| `AWAITING_SHIPMENT` | 待发货 | ❌ | `paid_at` |
| `PARTIAL_SHIPPING` | 部分发货 | ❌ | `shipped_at` (first) |
| `AWAITING_COLLECTION` | 待揽收 | ❌ | `shipped_at` |
| `IN_TRANSIT` | 在途 | ❌ | `shipped_at` |
| `DELIVERED` | 已签收（未完结） | ❌ | `delivered_at` |
| `COMPLETED` | 已完结 | ✅ 绝对终态 | `delivered_at`, `paid_at`, `shipped_at` 全有；无取消 |
| `CANCELLED` | 已取消 | ✅ 绝对终态 | `cancelled_at` 必有 |

## 已固化的白名单

代码 `tts_erp_v2/db/constants.py:73-83`：

```python
PAID_SALES_ORDER_STATUSES = frozenset({
    "AWAITING_SHIPMENT", "PARTIAL_SHIPPING", "AWAITING_COLLECTION",
    "IN_TRANSIT", "DELIVERED", "COMPLETED",
})
UNPAID_SALES_ORDER_STATUSES = frozenset({"UNPAID", "ON_HOLD", "CANCELLED"})
```

> 收入/COGS 聚合**只能用** `PAID_SALES_ORDER_STATUSES`（即排除 UNPAID/ON_HOLD/CANCELLED）。

## 实测分布（2026-09-14 prod）

```
COMPLETED            414
CANCELLED            303
DELIVERED            204
IN_TRANSIT            33
AWAITING_COLLECTION    7
AWAITING_SHIPMENT      1
```

## 引用
- 代码: `tts_erp_v2/db/constants.py:46-87`、`tts_erp_v2/db/models/commerce.py:165`
- 文档: `tech-doc/order-domain-business-rules.md §1`、`tech-doc/order-domain-business-rules.md §2`
