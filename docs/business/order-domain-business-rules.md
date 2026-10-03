# 订单域业务规则

> 订单域数据同步的业务场景、终态规则、数据流设计。
> 创建：2026-09-12

## 1. 订单状态生命周期

TikTok Shop 订单有 6 种状态，无 "REFUNDED" 状态：

```
AWAITING_SHIPMENT → AWAITING_COLLECTION → IN_TRANSIT → DELIVERED → COMPLETED
      (6 单)              (8 单)            (79 单)      (213 单)     (362 单)

任意阶段 ────────────────────────────────────────────────────────→ CANCELLED
                                                                 (278 单)
```

### 终态

| 状态 | 终态 | 说明 |
| --- | --- | --- |
| COMPLETED | ✅ 绝对终态 | 全部有 delivered_at / paid_at / shipped_at；无任何取消记录 |
| CANCELLED | ✅ 绝对终态 | 全部有 cancelled_at |
| DELIVERED | ❌ | 可能还会变为 COMPLETED |
| IN_TRANSIT | ❌ | 持续更新中 |
| AWAITING_COLLECTION | ❌ | 持续更新中 |
| AWAITING_SHIPMENT | ❌ | 持续更新中 |

**终态订单禁止 upsert**：已到终态的订单数据已定型，不需要再更新（TODO #1 待实现）。

### 数据来源

VN 店铺 Bridge nook（shop_id=7494763368967603447）API 同步路径，共 946 单。
数据在 `commerce.sales_orders`（API 同步），`plugin.orders` 当前为空。

## 2. 订单域数据同步链路

```
TikTok 商家后台
  │
  ├─ ① 订单列表（paged list）
  │    → plugin.orders + plugin.order_lines
  │    → 订单状态会随物流/取消/退款变化
  │    → 终态后不再变化
  │
  ├─ ② 物流详情（per-order logistics query）
  │    → plugin.shipments + plugin.tracking_events
  │    → 持续更新（发货→揽收→在途→签收）
  │    → 几乎不能缓存，Chrome 插件定时重新抓取未到终态订单的物流信息
  │
  ├─ ③ 结算（独立对账单维度）
  │    → plugin.settlements + plugin.settlement_details
  │    → 按 statement_id 聚合，不是订单状态的一个阶段
  │    → 当前数据为空（尚未同步）
  │
  └─ ④ 退款/取消（独立 API，不影响订单状态）
       → 原始数据：integration.raw_records（endpoint=/return_refund/202309/cancellations/search）
       → TODO #2：待建 plugin.cancellations 结构化表
```

### 同步路径互斥

| 同步路径 | 判断依据 | 订单存储 |
| --- | --- | --- |
| API 同步 | `commerce.shops.credential_id IS NOT NULL` | `commerce.sales_orders` |
| 插件同步 | `commerce.shops.credential_id IS NULL` | `plugin.orders` |

同一店铺不会同时走两条路径。

## 3. 退款/取消数据

退款**不会改变订单状态**（无 REFUNDED 状态）。取消/退款是独立数据流。

### TikTok API 端点

`/return_refund/202309/cancellations/search`（当前 raw_records 1443 条，覆盖 283 个订单）

### 取消类型

| cancel_type | 数量 | 含义 |
| --- | --- | --- |
| BUYER_CANCEL | 678 | 买家主动取消 |
| CANCEL | 765 | 系统/卖家取消（如 returned_to_shipper_other） |

### 取消状态

全部为 `CANCELLATION_REQUEST_COMPLETE`。

### 取消粒度

取消数据包含行项目级别信息（`cancel_line_items` → `sku_id` / `order_line_item_id`），
支持部分取消（一个订单可以只有部分 SKU 被取消）。

### 已知异常

6 个 IN_TRANSIT 订单有取消记录（cancel_type=CANCEL，系统取消），
但订单状态仍为 IN_TRANSIT。原因待查（部分取消 / 状态更新延迟）。
暂时不处理。

## 4. 物流信息缺口问题

### 问题描述

订单到达终态（COMPLETED/CANCELLED）后，插件不再查询其物流信息。
但如果物流信息在订单变为终态前有更新，这些更新可能丢失。

时序：

1. 订单状态 IN_TRANSIT，插件在 T1 抓取了物流信息
2. T1~T2 之间物流有新事件（如签收确认）
3. T2 时刻订单状态变为 COMPLETED
4. 插件在 T3（T3 > T2）看到 COMPLETED，跳过物流查询
5. 结果：T1~T2 之间的物流事件丢失

## 5. 设计决策：物流终态独立判断（方案 2）

### 决策

物流信息是否继续拉取，由**物流终态**独立判断，不依赖订单状态。

### 物流终态 action_code（API 级别）

| action_code | 描述 | 含义 |
| --- | --- | --- |
| `50101` | "Your package was delivered!" | 已签收 |
| `80101` | "returned to seller by shipping provider" | 退回卖家 |
| `110101` | "Your package delivery was canceled" | 配送取消 |

依据 VN 店铺 Bridge nook 数据（946 单）分析：

| 订单状态 | 最后 tracking event | 数量 | 物流终态？ |
| --- | --- | --- | --- |
| COMPLETED | `50101` 已签收 | 264 | ✅ |
| DELIVERED | `50101` 已签收 | 195 | ✅ |
| CANCELLED | `80101` 退回卖家 | 55 | ✅ |
| CANCELLED | `110101` 配送取消 | 18 | ✅ |
| IN_TRANSIT | 混合（30501/70201/50101 等） | 75 | 部分是 |

### 插件判断逻辑

```python
LOGISTICS_TERMINAL_CODES = {"50101", "80101", "110101"}

def is_logistics_terminal(tracking_events: list[dict]) -> bool:
    """物流是否到达终态（已签收 / 退回卖家 / 配送取消）"""
    if not tracking_events:
        return False
    last_event = max(tracking_events, key=lambda e: e["update_time_millis"])
    return str(last_event.get("action_code")) in LOGISTICS_TERMINAL_CODES
```

插件按物流终态判断：终态则停止拉物流，非终态继续拉。即使订单已 COMPLETED，
只要物流还没到 `50101`（签收事件延迟回传），插件仍继续拉直到拿到签收事件。

### ⚠ 数据来源说明

以上 action_code 分析基于 **TikTok API 响应数据**（`integration.raw_records` 中
`/fulfillment/202309/orders/{order_id}/tracking` 端点的返回值），**不是** Chrome
扩展页面上展示的数据。

- API 返回的 tracking events 包含完整的 `action_code` + `description` + `update_time_millis`
- Chrome 扩展页面上展示的物流状态可能**没有这么详细**（页面可能只显示简化的状态文案，
  不一定包含 action_code 数字编码）
- **action_code 的可用性取决于 Chrome 扩展实际能拦截到的 response 内容**

### 后续计划

用户正在开发请求拦截插件（Chrome 扩展网络请求拦截），后续基于拦截到的 response
再次分析：

1. Chrome 扩展实际拦截到的物流 response 中是否包含 `action_code` 字段
2. 如果不包含 action_code，页面上展示的状态文案是否可以映射到终态（如通过
   `description` 关键词匹配 "delivered" / "returned" / "canceled"）
3. 是否需要基于实际拦截数据调整终态判断逻辑
