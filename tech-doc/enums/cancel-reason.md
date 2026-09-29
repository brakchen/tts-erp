# `plugin.after_sales.reason` / `cancel_reason` — 取消原因（自由文本 / 半枚举）

> TikTok `cancellations/search` 端点返回的取消原因。
> 文档**只列了 1 种**（`returned_to_shipper_other`），实际 prod 样本中**至少 6+ 种**。

## 来源
- DB column: `plugin.after_sales.reason` (Text, nullable)
- 持久化: `integration.raw_records.payload[].cancel_reason` / `cancel_reason_text`
- 类型: **text**（自由）
- 上游: TikTok OpenAPI `/return_refund/202309/cancellations/search`
- 文档锚点: `tech-doc/order-domain-business-rules.md §3`

## 取值（prod 0+ 批，按 order/list reverse_module 抓取）

| 等级 |  文本 | 出现条件 |
| :---: | --- | --- |
| 🟡 |  `'returned_to_shipper_other'` | 退件导致（海外取消典型原因） |
| 🟡 |  `'客户超时支付'` | 超时未付款自动取消 |
| 🟡 |  `'不想要了'` | 买家主动取消 |
| 🟡 |  `'发现更优惠的价格'` | 买家主动取消 |
| 🟡 |  `'需要更改收货地址'` | 买家主动取消 |
| 🟡 |  `'需要更改付款方式'` | 买家主动取消 |
| 🟡 |  `'预计送达时间过晚'` | 买家主动取消 |
| 🟡 |  `'商品与描述不符'` | （推测）售后/退货 |
| 🟡 |  `'改变主意'` | （reverse/component/orders/list 观察） |
| 🟡 |  `'商品太大或太小'` | （同上） |

> 还有 `cancel_reason_text` 字段给出更通用的人类可读描述：
> - `'returned_to_shipper_other'` → `'Package delivery failed'`

## ⚠️ 未固化值速查

- 🟡 **10 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``'returned_to_shipper_other'`` — 退件导致（海外取消典型原因）
  - 🟡 ``'客户超时支付'`` — 超时未付款自动取消
  - 🟡 ``'不想要了'`` — 买家主动取消
  - 🟡 ``'发现更优惠的价格'`` — 买家主动取消
  - 🟡 ``'需要更改收货地址'`` — 买家主动取消
  - …（其余 5 个见下方"## 取值"表）


## 已知 gap

- 🔴 **`cancel_reason` 全部合法值未文档化** —— 文档只列了 1 种（`returned_to_shipper_other`）
- ❌ `plugin.after_sales` 表**目前零行**——reason 字段从未实际写入；上面的样本来自 `intercepted_requests.response_body`（`order/list` reverse_module）和 `raw_records`（`cancellations/search`）
- ❌ 卖家中心 UI 文案 vs API 返回 `cancel_reason` 可能有出入（中文/英文、详简不一）

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:355-360`
- 文档: `tech-doc/order-domain-business-rules.md §3`
