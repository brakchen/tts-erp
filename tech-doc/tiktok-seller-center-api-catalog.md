# TikTok Seller Center 接口目录

> 来源：Chrome 扩展域名观察功能捕获的实际请求/响应（2026-09-09）。
> 插件版本：0.1.117，店铺：VN 区域，seller_id=7494763368967603447。
> 本文档记录实际观测到的接口结构，作为解析规则的 truth source。

## 相关文档

本目录涉及的所有 int / text 枚举值在 [`tech-doc/enums/`](enums/) 有独立维护文件。
**遇到 int / text 码不知含义时（如 `main_order_status` / `reverse_type` / `action_code` / `cancel_reason` 等），先查 [`enums/README.md`](enums/README.md) 的"未固化值汇总"表**——`/docs(enum): 逐行加 ✅/🟡/🔴 等级标注` 后的状态都登记了。

**最新状态**（09-15）见 [§7.8](#sec-7-8) 三列对比表。下面是按本目录章节快速跳转表。

### ✅ 已确认字典（可信赖）

| 本目录章节 / 字段 | 关键枚举 | enum 文件 |
| --- | --- | --- |
| §2.1 订单列表 `order_status_module[].main_order_status` | 卖家中心 int 主订单状态 | [`main-order-status.md`](enums/main-order-status.md) |
| §2.1 订单列表 `order_status_module[].sku_display_status` | 卖家中心 int SKU 展示状态 | [`sku-display-status.md`](enums/sku-display-status.md) |
| §2.1 订单列表 `trade_order_module.fulfillment_type` | 履约类型（双口径：text + int） | [`fulfillment-type.md`](enums/fulfillment-type.md) |
| §2.1 订单列表 `trade_order_module.pay_method` | 支付方式（自由文本） | [`pay-method.md`](enums/pay-method.md) |
| §2.1 订单列表 `trade_order_module.sale_region` | 销售地区（ISO 3166-1） | [`sale-region.md`](enums/sale-region.md) |
| §2.2 订单状态计数（v2 标准化） | `commerce.sales_orders.status`（AWAITING_SHIPMENT/CANCELLED/...） | [`order-status.md`](enums/order-status.md) |
| §2.5 退货公告 `reverse_module.reverse_type` | 卖家中心 int 售后/取消子类型 | [`reverse-type.md`](enums/reverse-type.md) |
| §2.5 退货公告 `reverse_module.reverse_status` | 卖家中心 int 售后状态 | [`reverse-status.md`](enums/reverse-status.md) |
| §2.7 退款订单列表 `biz_data.caseType` | v2 标准化售后工单类型 | [`case-type.md`](enums/case-type.md) |
| §2.7 退款订单列表 `cancel_type`（API 原始） | `BUYER_CANCEL` / `CANCEL` | [`cancel-type.md`](enums/cancel-type.md) |
| §2 物流 `track_list[].action_code`（API） | 23 个事件码（38301 = 海外取消判据） | [`action-code.md`](enums/action-code.md) |
| §2 物流 终态白名单 | `LOGISTICS_TERMINAL_CODES` = {50101, 80101, 110101} | [`logistics-terminal-codes.md`](enums/logistics-terminal-codes.md) |
| §2 结算 `settlement_components.component_code` | 53 个 EAV 字段（GROSS_SALES / PLATFORM_COMMISSION / ...） | [`settlement-component-code.md`](enums/settlement-component-code.md) |

### 🟡 部分已观测（仅参考，**禁止拍脑袋补全**）

| 本目录章节 / 字段 | 关键枚举 | enum 文件 |
| --- | --- | --- |
| §2.7 退款 `cancel_status` | 仅 1 码 `CANCELLATION_REQUEST_COMPLETE` 已观测 | [`cancel-status.md`](enums/cancel-status.md) |
| §2.7 退款 `cancel_reason` | 9+ 种已观测（文档原仅 1 种） | [`cancel-reason.md`](enums/cancel-reason.md) |
| §2 物流 `track_list[].track_status`（Chrome ext） | 自由文本，2 个样本 | [`track-status.md`](enums/track-status.md) |
| §2 结算 `settlement_details.settlement_status` | 1 码 `2` 已观测 | [`settlement-status.md`](enums/settlement-status.md) |
| §2 结算 `settlements.payment_status` | 1 码 `1` 已观测 | [`payment-status.md`](enums/payment-status.md) |

### 🔴 待观测（**字典未知**）

| 本目录章节 / 字段 | 关键枚举 | enum 文件 |
| --- | --- | --- |
| §2 结算 `plugin.settlements.statement_type` int | 0 个样本 | [`statement-type.md`](enums/statement-type.md) |
| §2 结算 `plugin.settlements.payment_pending_reason` int | 0 个样本 | [`payment-pending-reason.md`](enums/payment-pending-reason.md) |
| §7.4.3 `action_module.action_list[]` | 卖家中心操作码（**与物流 `action_code` 无关**） | — |

> **完整 38 个枚举值空间 + 逐行 ✅/🟡/🔴 等级标注**见 [`enums/README.md`](enums/README.md)（含"未固化值汇总"表）。

### 互引（上游）

`tech-doc/enums/` 里的 enum 文件也反向引用本目录章节作为"上游来源"。

## 1. 接口总览

### 1.1 订单/履约域

| 路径 | 方法 | 说明 | 调用频率 |
| ------ | ------ | ------ | --------- |
| `/api/fulfillment/order/list` | POST | 订单列表（分页） | 每次打开订单页 |
| `/api/fulfillment/order/search_count` | POST | 按状态统计订单数 | 每次打开订单页 |
| `/api/fulfillment/order/search_layout/get` | POST | 搜索布局配置 | 每次打开订单页 |
| `/api/fulfillment/order/export_record/get` | POST | 订单导出记录 | 频繁 |
| `/api/fulfillment/dashboard/get` | POST | 履约仪表盘 | 偶尔 |
| `/api/fulfillment/print/seller_config/get` | POST | 打印配置 | 偶尔 |
| `/api/v1/trade/orders/buyer` | POST | 批量查买家信息 | 按需 |
| `/api/fulfillment/order/get` | POST | 订单详情（含 reverse_type） | 点订单详情时 |
| `/order/detail` | GET | 订单详情页（HTML） | 浏览器导航 |

### 1.2 退货/售后域

| 路径 | 方法 | 说明 | 调用频率 |
| ------ | ------ | ------ | --------- |
| `/api/v1/reverse/orders/list_seller_announcement` | POST | 退货公告列表 | 每次打开订单页 |
| `/api/v1/reverse/orders/get_export_history` | POST | 退货导出历史 | 偶尔 |
| `/api/v1/reverse/component/orders/list` | POST | **退款订单列表（核心：reverseType/return_price/reason/reverse_main_order_id）** | 每次打开售后页 |

### 1.3 商品域

| 路径 | 方法 | 说明 | 调用频率 |
| ------ | ------ | ------ | --------- |
| `/api/v1/product/local/products/list` | GET | 本地商品列表（分页） | 每次打开商品页 |
| `/api/v1/product/local/same_products/list` | POST | 同款商品查询 | 按需 |
| `/api/v1/product/tab/count/get` | GET | 商品 tab 计数 | 偶尔 |
| `/api/v1/sea_product/growth/aigc_video/info` | GET | AIGC 视频信息 | 偶尔 |

### 1.4 店铺/卖家域

| 路径 | 方法 | 说明 |
| ------ | ------ | ------ |
| `/api/v1/seller/account/get` | GET | 卖家账户信息 |
| `/api/v1/seller/shop_limit_status/get` | GET | 店铺限制状态 |
| `/api/v1/seller/workbench/get_all_sellers` | GET | 获取所有卖家 |
| `/api/v2/seller/menu/get` | GET | 菜单配置 |
| `/api/v1/seller/onboard/v2/config/get` | GET | 入驻配置 |

### 1.5 消息/通知域

| 路径 | 方法 | 说明 |
| ------ | ------ | ------ |
| `/api/v2/seller/message/get_msg_tabs` | GET | 消息标签页 |
| `/api/v1/seller/message/pull_by_category_v2` | POST | 分类消息拉取 |
| `/api/v1/seller/message/list` | POST | 消息列表 |
| `/api/v1/seller/message/outage/list` | POST | 故障消息列表 |

### 1.6 系统/配置域

| 路径 | 方法 | 说明 |
| ------ | ------ | ------ |
| `/api/v1/common/region_domain` | GET | 区域域名解析（高频） |
| `/api/v1/arch/config_center_gw/get_config` | GET | 配置中心 |
| `/api/v1/arch/config_center_gw/mget_config_by_app_name` | GET | 批量配置查询 |
| `/passport/account/info/v2/` | GET | 账户信息 |
| `/api/v1/bs/rt` | POST | 埋点/遥测 |

### 1.7 广告域（已有同步）

| 路径 | 方法 | 说明 |
| ------ | ------ | ------ |
| `/oec_ads/shopping/v1/oec/stat/post_product_list` | POST | 商品分析 |
| `/oec_ads/shopping/v1/oec/stat/post_session_list` | POST | 会话分析 |
| `/oec_ads/shopping/v1/oec/stat/campaign_opt_log_list` | POST | 变更日志 |

---

## 2. 关键接口详细结构

### 2.1 订单列表 `/api/fulfillment/order/list`

**请求**：POST，body 结构：

```json
{
  "search_condition": { "condition_list": {} },
  "offset": 0,
  "count": 20,
  "sort_info": "6",
  "search_cursor": "",
  "pagination_type": 0
}
```

**响应**：`data` 结构：

```json
{
  "offset": 0,
  "count": 20,
  "total_count": "123",
  "has_more": true,
  "next_cursor_token": "...",
  "search_next_cursor": "...",
  "search_next_has_more": true,
  "search_previous_cursor": "...",
  "search_previous_has_more": false,
  "default_search_create_time": "...",
  "main_orders": [...]
}
```

#### `main_orders[]` 模块结构（实测确认）

每个 `main_order` 包含以下模块：

```json
{
  "main_order_id": "585971536482567908",
  "order_status_module": [...],
  "price_module": {...},
  "sku_module": [...],
  "fulfill_line_module": [...],
  "fulfillment_module": [...],
  "delivery_module": [...],
  "trade_order_module": {...},
  "trade_order_id_mapper": {...},
  "fulfill_unit_id_mapper": [...],
  "buyer_info_module": {...},
  "order_label_module": [...],
  "note_module": {...},
  "action_module": {...},
  "logistics_info_module": [...],
  "print_label_module": [...],
  "reminder_module": [...]
}
```

#### ⚠️ `order_status_module` — 是 **数组**，不是对象

```json
"order_status_module": [
  {
    "main_order_status": 101,        // 整数状态码
    "main_sub_order_status": 1,      // 子状态码
    "order_line_id": "585971536482633444",
    "sku_display_status": 111        // SKU 展示状态
  }
]
```

- **不是** `order_status` 字符串，是 `main_order_status` 整数
- 每个 order_line 一个元素（按 `order_line_id` 关联）
- 无 `create_time`/`paid_time` 等时间戳（时间戳在 `trade_order_module`）
- **枚举值见** [`enums/main-order-status.md`](enums/main-order-status.md)（🟡 部分确认，5 个码）、[`enums/sku-display-status.md`](enums/sku-display-status.md)（🟡 7 个码）

#### ⚠️ `price_module` — 字段名与推断不同

```json
"price_module": {
  "main_order_id": "585971536482567908",
  "grand_total": {
    "currency": "VND",
    "format_price": "584.901₫",
    "price_val": "584901",           // 字符串，非数字
    "symbol": "₫"
  },
  "sub_total": {
    "currency": "VND",
    "format_price": "554.901₫",
    "price_val": "554901",
    "symbol": "₫"
  }
}
```

- **无** `payment` 字段 → 用 `grand_total` 代替
- **无** `total_amount` 字段 → 用 `sub_total` 代替
- 金额在 `price_val`（字符串），不在 `amount`
- 额外有 `format_price`（带符号格式化）和 `symbol`

#### ⚠️ `trade_order_module` — 时间戳在这里

```json
"trade_order_module": {
  "business_line": 1,
  "close_sla_time": "1789228799",      // 秒级时间戳字符串
  "create_time": "1788918893",          // ⬅ 下单时间
  "fulfillment_type": 0,                // 整数（0=自发货?）
  "latest_rts_time": "1789091694",      // 最晚发货时间
  "latest_tts_time": "1789228799",      // 最晚交易时间
  "main_order_id": "585971536482567908",
  "main_order_type": 0,
  "need_invoice_flag": false,
  "pay_method": "Cash on delivery",     // 支付方式文本
  "platform": 1,
  "sale_region": "VN",                  // 销售区域
  "shipping_fee": {
    "currency": "VND",
    "format_price": "30.000₫",
    "price_val": "30000",
    "symbol": "₫"
  },
  "update_time": "1788918894223"        // 毫秒级时间戳字符串
}
```

- `fulfillment_type` 整数枚举：完整字典见 [`enums/fulfillment-type.md`](enums/fulfillment-type.md)（双口径：text 端 + plugin 端，0=FBM）
- `pay_method` 自由文本：实测 5 种（Cash on delivery / MoMo 电子钱包 / Credit/debit card / TikTok Shop Balance / VNPAY），见 [`enums/pay-method.md`](enums/pay-method.md)
- `sale_region` ISO 3166-1 alpha-2：实测 `VN`，其他 5 种未观测，见 [`enums/sale-region.md`](enums/sale-region.md)
```

#### ⚠️ `sku_module[]` — 价格字段名不同

```json
"sku_module": [
  {
    "order_line_ids": ["585971536482633444"],
    "product_id": "1736527295109498103",
    "product_image": {
      "height": 200,
      "width": 200,
      "uri": "tos-alisg-i-aphluv4xwc-sg/...",
      "url_list": ["https://..."],
      "thumb_uri": "...",
      "thumb_url_list": ["https://..."]
    },
    "product_name": "Áo thun ngắn tay...",
    "product_type": 0,
    "quantity": 1,
    "sku_id": "1736527277162530039",
    "sku_name": "Màu trắng, XXL",
    "sku_unit_price": {
      "currency": "VND",
      "format_price": "584.901₫",
      "price_val": "584901",
      "symbol": "₫"
    },
    "sku_total_price": {
      "currency": "VND",
      "format_price": "584.901₫",
      "price_val": "584901.00",
      "symbol": "₫"
    },
    "creator_info_name": {...},
    "dangerous_good_level": 0
  }
]
```

- **无** `sale_price` → 用 `sku_unit_price.price_val`
- **无** `sku_image`（字符串）→ 用 `product_image.url_list[0]`
- **无** `seller_sku` → 不可用
- **无** `sku_order_status` → 状态在 `order_status_module` 按 `order_line_id` 关联

#### `delivery_module[]` — 是数组

```json
"delivery_module": [
  {
    "buyer_region": "VN",
    "fulfill_unit_id": "1210315649454474980",
    "last_tracking_no": "",
    "logistics_service_info": {
      "logistics_service_id": "7156147842033714945",
      "logistics_service_level": "经济运输",
      "logistics_service_name": "全球经济运输服务",
      "logistics_service_type": 0
    },
    "payment_total": {
      "currency": "VND",
      "price_val": "584901",
      "symbol": "₫"
    },
    "pickup_type": 2,
    "shipment_provider_info": {
      "id": "74392975844699...",
      "icon_url": "https://..."
    },
    "warehouse_id": "...",
    "warehouse_name": "...",
    "warehouse_region": "...",
    "pkg_attr": {
      "dimension": {"height": "1", "length": "1", "unit": 1, "width": "1"},
      "weight": {"unit": 1, "weight": "260"}
    }
  }
]
```

---

### 2.2 订单状态计数 `/api/fulfillment/order/search_count`

**请求**：POST

```json
{
  "search_key_list": ["100", "101", "1100", "1200", "102"]
}
```

**响应**：

```json
{
  "code": 0,
  "data": {
    "count_map": {
      "101": 35,    // 待发货
      "102": 302,   // 已完成?
      "1100": 7,    // 退货中?
      "1200": 28    // 已取消?
    }
  }
}
```

**状态码映射**（待确认）：

- `100` = 全部
- `101` = 待发货
- `102` = 已完成/已签收
- `1100` = 退货/售后
- `1200` = 已取消

---

### 2.3 买家信息 `/api/v1/trade/orders/buyer`

**请求**：POST

```json
{
  "main_order_ids": ["585862391605135101", "585724606074226260", ...]
}
```

**响应**：

```json
{
  "code": 0,
  "data": {
    "buyer_info": {
      "585610872085710323": {
        "avatar": {
          "url_list": ["https://..."],
          "thumb_url_list": ["https://..."]
        },
        "nickname": "...",
        ...
      }
    }
  }
}
```

---

### 2.4 退货公告 `/api/v1/reverse/orders/list_seller_announcement`

**请求**：POST

```json
{
  "RequestType": 0,
  "scene": 1
}
```

**响应**：

```json
{
  "code": 0,
  "data": {},    // 无退货公告时为空
  "message": "success"
}
```

---

### 2.5 退货导出历史 `/api/v1/reverse/orders/get_export_history`

**请求**：POST

```json
{
  "manage_module": 3
}
```

**响应**：

```json
{
  "code": 0,
  "data": {
    "export_history": [
      {
        "download": 2,
        "export_time": 1788851067016,
        "file_key": "e50184ff-1b52-429a-a9ae-d7294403435b",
        "file_name": "退货/退款订单-2026-09-08-15:04.xlsx"
      }
    ]
  }
}
```

---

### 2.6 商品列表 `/api/v1/product/local/products/list`

### 2.7 退款订单列表 `/api/v1/reverse/component/orders/list`

**核心 endpoint** — 利润计算中"退款扣减项"的唯一结构化数据源。

**request**:
```json
{
  "count": 50,
  "offset": 0,
  "pagination_type": 0,
  "search_condition": {
    "tab": {"str_value_list": ["800"|"100"]},
    "sub_tab_pending": {"str_value_list": ["sub_tab_pending_all"]},
    "order_sort_comp": {"str_value_list": ["OrderSort_UPADTE_TIME_DESC"]}
  },
  "component_version": "hit_ui_opt"
}
```

- `search_condition.tab`：`800` = 待处理；`100` = 全部
- `sub_tab_pending`：仅 tab=800 时生效，指定 pending 子筛选

**response.data**:
- `cards[]` — 每张卡 = 一笔退款
  - `biz_data` ← **结构化数据，直接用于利润计算**：
    - `main_order_id` (str) — 原始订单号
    - `reverse_main_order_id` (str) — 独立售后单号（与 main_order_id 1:N）
    - `reverseType` (int) — 退款类型码（已知 `3` = 改变主意；其他值待映射，§6）
    - `return_price` (str + currency suffix) — 退款金额（如 `"544.116₫"`）
    - `tagged` (bool) — 是否打标
    - **枚举值见** [`enums/case-type.md`](enums/case-type.md)（v2 标准化 3 类：CANCELLATION/REFUND_ONLY/RETURN_AND_REFUND）和 [`enums/reverse-type.md`](enums/reverse-type.md)（卖家中心原始码，🟡 3 个已观测 1/3/4）
  - `card.blocks[].content[].text_pair.content.content` — **i18n 本地化文案**（含 reason 中文显示，**不是结构化字段**，仅供前端渲染）
  - `linked_cards[]` — 关联卡片（如时间线）
- `total_count` (int) — 总条数
- `search_next_cursor` / `search_previous_cursor` — 翻页游标

**利润计算用法**:
- 每笔退款 = 1 row in `cards[]`
- 实际退款金额 = `biz_data.return_price`（去掉 currency 后缀再 parse Float）
- 关联原订单 = `biz_data.main_order_id` → 配 `statement/transaction/detail` 拿原订单成交金额
- 退款维度利润 = `∑ return_price` over cards，按月/周聚合

**示例**（实测 2026-09-14 16:59:38 burst，main_order_id=585921002913891915）:
```json
{"biz_data": {
  "tagged": false,
  "reverseType": 3,
  "return_price": "544.116₫",
  "main_order_id": "585921002913891915",
  "reverse_main_order_id": "4042326121400600139"
}}
```

### 2.8 订单详情 `/api/fulfillment/order/get`

`/api/fulfillment/order/list` 的详情接口。点订单条目时触发。

**response 关键字段**:
- `main_order_id` — 主订单号
- `sku_id`, `sku_name`, `product_id`, `product_name`
- `quantity`
- `reverse_type` (int) — 单订单退款状态枚举；字典见 [`enums/reverse-type.md`](enums/reverse-type.md)
- `reverse_status` (int) — 字典见 [`enums/reverse-status.md`](enums/reverse-status.md)
- `currency` (str)

**用法**：list 拿 `main_order_id` → get 拿完整 modules + 单订单 refund 状态。与 §2.7 的退款列表互为补充：get 看单订单 reverse_type（是否退过），§2.7 看每笔退款的金额。

**请求**：GET，关键 query params：

| 参数 | 说明 |
| ------ | ------ |
| `oec_seller_id` | 卖家 ID |
| `page_number` | 页码 |
| `page_size` | 每页数量（默认50） |
| `sku_number` | SKU 数量 |
| `is_need_target_stock` | 是否需要库存 |
| `same_product_page_size` | 同款商品页大小 |
| `product_sort_fields` | 排序字段 |
| `product_sort_types` | 排序方式 |

**响应**：

```json
{
  "code": 0,
  "data": {
    "products": [...],
    "total_product_count": 123,
    "page_number": 1,
    "page_size": 50,
    "need_async_load_same_products": false
  }
}
```

---

## 3. 接口关系图

```
┌─────────────────────────────────────────────────────────────────┐
│                     TikTok Seller Center                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  订单页 ──┬── /api/fulfillment/order/list          (订单列表)   │
│           ├── /api/fulfillment/order/search_count  (状态计数)   │
│           ├── /api/fulfillment/order/search_layout (搜索布局)   │
│           ├── /api/fulfillment/order/export_record (导出记录)   │
│           ├── /api/fulfillment/dashboard/get       (仪表盘)     │
│           ├── /api/v1/trade/orders/buyer           (买家信息)   │
│           ├── /api/v1/reverse/orders/list_seller_announcement   │
│           │                                      (退货公告)     │
│           └── /api/v1/reverse/orders/get_export_history         │
│                                              (退货导出历史)     │
│                                                                 │
│  订单详情 ── /order/detail                        (HTML 页面)   │
│                                                                 │
│  商品页 ──── /api/v1/product/local/products/list  (商品列表)   │
│             /api/v1/product/local/same_products/list            │
│                                        (同款商品)               │
│                                                                 │
│  广告页 ──── /oec_ads/shopping/v1/oec/stat/*      (已有同步)   │
│                                                                 │
│  消息 ────── /api/v2/seller/message/*             (消息系统)   │
│                                                                 │
│  系统 ────── /api/v1/common/region_domain         (域名解析)   │
│             /api/v1/arch/config_center_gw/*       (配置中心)   │
│             /passport/account/info/v2/            (账户)       │
│             /api/v1/bs/rt                         (埋点)       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 解析规则修正（基于实测）

### 4.1 `order_status_module` 修正

**原推断**（错误）：

```python
status = osm.get("order_status")  # 字符串 "DELIVERED"
```

**实测**（正确）：

```python
osm_list = order.get("order_status_module", [])
if osm_list:
    main_order_status = osm_list[0].get("main_order_status")  # 整数 101
    sku_display_status = osm_list[0].get("sku_display_status")  # 整数 111
```

### 4.2 `price_module` 修正

**原推断**（错误）：

```python
payment_amount = pm.get("payment", {}).get("amount")
total_amount = pm.get("total_amount", {}).get("amount")
```

**实测**（正确）：

```python
grand_total = pm.get("grand_total", {})
sub_total = pm.get("sub_total", {})
payment_amount = grand_total.get("price_val")  # "584901" 字符串
total_amount = sub_total.get("price_val")
currency = grand_total.get("currency")  # "VND"
```

### 4.3 时间戳修正

**原推断**（错误）：

```python
order_time = osm.get("create_time")  # 在 order_status_module 里
paid_at = osm.get("paid_time")
```

**实测**（正确）：

```python
tom = order.get("trade_order_module", {})
order_time = tom.get("create_time")  # "1788918893" 秒级字符串
update_time = tom.get("update_time")  # "1788918894223" 毫秒级字符串
fulfillment_type = tom.get("fulfillment_type")  # 整数 0
sale_region = tom.get("sale_region")  # "VN"
pay_method = tom.get("pay_method")  # "Cash on delivery"
```

### 4.4 `sku_module` 修正

**原推断**（错误）：

```python
unit_price = item.get("sale_price", {}).get("amount")
image_url = item.get("sku_image")
seller_sku = item.get("seller_sku")
line_status = item.get("sku_order_status")
```

**实测**（正确）：

```python
unit_price = item.get("sku_unit_price", {}).get("price_val")  # "584901"
total_price = item.get("sku_total_price", {}).get("price_val")
image_obj = item.get("product_image", {})
image_url = image_obj.get("url_list", [None])[0]  # 从 url_list 取第一张
# seller_sku 不存在于 sku_module
# sku_order_status 不存在 → 需从 order_status_module 按 order_line_id 关联
```

### 4.5 新增可用字段

| 字段路径 | 说明 | 可用于 |
| ---------- | ------ | -------- |
| `trade_order_module.pay_method` | 支付方式 | orders.payment_method |
| `trade_order_module.sale_region` | 销售区域 | orders.region |
| `trade_order_module.shipping_fee` | 运费 | —（2026-09-14 删除：解析时不提取、orders 表已无此列） |
| `trade_order_module.business_line` | 业务线 | orders.business_line |
| `delivery_module[].logistics_service_info` | 物流服务详情 | shipments.logistics_service |
| `delivery_module[].shipment_provider_info` | 承运商信息 | shipments.carrier |
| `delivery_module[].pkg_attr` | 包裹尺寸重量 | shipments.package_attr |
| `buyer_info_module.buyer_nickname` | 买家昵称 | orders.buyer_name |
| `trade_order_id_mapper` | trade_order_id ↔ main_order_id 映射 | 结算关联 |

---

## 5. 退货/售后接口待补充

当前捕获的退货接口只有：

- `/api/v1/reverse/orders/list_seller_announcement` — 退货公告（空数据）
- `/api/v1/reverse/orders/get_export_history` — 退货导出历史

**缺失的退货接口**（需要在退货页面浏览时捕获）：

- 退货订单列表（类似 `/api/v1/reverse/orders/list`） — **已补** → §2.7（实测路径 `/api/v1/reverse/component/orders/list`）
- 退货详情
- 退款详情
- 退货物流跟踪

**操作建议**：在 TikTok Seller Center 打开"退货/售后"页面，用域名观察功能捕获完整请求流。

---

## 6. 待确认事项

> **本节为初始状态。最新进展见 [§7.8](#sec-7-8) 三列对比表（09-09 / 09-13 / 09-15）。**
> 本节保留作为“起点状态”，便于追踪每个项是怎么从“待确认”演进到“已确认”的。

| 项目 | 状态 | 说明 |
| ------ | ------ | ------ |
| `order_status_module.main_order_status` 状态码映射 | 待确认 | 101=?, 102=? 需对照 TikTok 文档 |
| `sku_display_status` 状态码映射 | 待确认 | 111=? |
| `trade_order_module.fulfillment_type` 枚举 | 待确认 | 0=? 1=? |
| `trade_order_id` ↔ `main_order_id` 映射关系 | 已确认 | `trade_order_id_mapper` 提供映射 |
| `statement_sku_detail_id` 获取路径 | 待确认 | 需在结算页面捕获 |
| 退货订单列表接口 | **已捕获** | `/api/v1/reverse/component/orders/list` → §2.7 |
| `reverseType` 枚举映射 | 待确认 | 已知 `3`=改变主意（实测）；其他值需扫描全表 `biz_data.reverseType` + `cards[].card.blocks[].text_pair.content.content` 中的本地化 reason 反查 |

---

## 7. 增量观测 2026-09-13（Chrome 扩展首屏 burst 捕获）

> 状态：临时快照，后续开发要参考的数据点都集中在这里。
> 不替换原 §1~§6 内容；本节是相对 2026-09-09 catalog 的纯增量。
> 数据采集方式：同一卖家 tab 在订单管理页首屏加载的 1 分钟内自动上传。

### 7.1 数据上下文

| 项 | 值 |
| --- | --- |
| 采样时间窗（UTC） | 2026-09-13 11:31:23 → 11:32:31（约 68 秒） |
| 表 `plugin.intercepted_requests` 总记录 | 1962 条（**全库都在这 1 分钟内**，更早无数据） |
| 触发会话 | `tab-1789299056241-yenoznznk`（单 tab） |
| seller_id | `7494763368967603447`（与 09-09 catalog 一致） |
| 区域 | shop_region=VN / x-tt-oec-region=VN / IDC=Singapore-Central |
| 入口页面 | TikTok Seller Center → 订单管理 |
| 间隔 9 分钟后 | 11:41 起 10 分钟窗口 0 写入——**扩展后续上传链路疑似中断**（见 §7.7） |

### 7.2 新发现的 endpoint（相对 09-09 catalog）

10 分钟窗口内 api16-normal-sg.tiktokshopglobalselling.com 共触发 **53 个不同 endpoint**（09-09 仅列了约 25 个），按域分组：

#### 7.2.1 卖家账户 / 上下文（10 个）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/v3/seller/common/get` | GET | 3 | **新**：账户全量 profile，21 keys（含 `markets / global_seller / verified_documents / delegation_mode`） |
| `/api/v1/seller/account/common/cerberus/resource/get` | GET | 3 | **新**：Cerberus 权限网关 |
| `/api/v1/seller/onboard/v2/config/get` | GET | 3 | 已有 |
| `/api/v1/seller/tasks/config/get` | GET | 2 | 已有 |
| `/api/v1/seller/homepage_allowlist/get` | GET | 4 | **新**：首页白名单（决定模块可见性） |
| `/api/v1/seller/workbench/get_all_sellers` | POST | 2 | 已有（catalog 写 GET，实测是 POST） |
| `/api/v1/seller/feelgood/access_token/get` | POST | 2 | **新**：满意度调研 token |
| `/api/v1/seller/global_product_permission/get` | GET | 1 | **新** |
| `/api/v1/seller/shop_limit_status/get` | GET | 2 | 已有 |
| `/api/v1/seller/badge/is_read/get` | GET | 1 | **新**：红点徽章已读状态 |

#### 7.2.2 消息 / 通知（6 个，含一个高频轮询）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/v1/sellerassistant/discover_chatbotevent` | GET | **9** | **新**：chatbot 事件探测，**1 分钟 9 次 ≈ 6.7s 一次**，疑似常驻轮询源 |
| `/api/v1/seller/message/pull_by_category_v2` | GET | 4 | 已有（catalog 写 POST，实测 GET） |
| `/api/v1/seller/message/outage/list` | GET | 4 | 已有（catalog 写 POST，实测 GET） |
| `/api/v1/seller/message/list` | GET | 2 | 已有（catalog 写 POST，实测 GET） |
| `/api/v1/seller/message/get_page_channels` | GET | 2 | **新** |
| `/api/v2/seller/message/get_msg_tabs` | GET | 2 | 已有 |

#### 7.2.3 弹窗 / Island / Banner / 店铺 IM（9 个）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/v1/seller/popup/list` | GET | 2 | **新** |
| `/api/v1/pop/seller_common/island/event/get` | GET | 2 | **新**：Island 浮岛组件事件 |
| `/api/v1/pop/seller_common/entrusted_exporter/popup/get` | GET | 2 | **新**：受托出口商弹窗 |
| `/api/v1/pop/seller_common/entity_change_todo_list/get` | GET | 2 | **新**：实体变更待办 |
| `/api/v1/seller/banner/list` | GET | 1 | **新** |
| `/api/v1/logistics/orderBff/tcc_banners` | GET | 1 | **新**：物流 banner |
| `/api/v1/fulfillment/reach/banner_list` | POST | 1 | **新** |
| `/api/v1/product/stock/banner/check` | GET | 1 | **新** |
| `/api/v1/shop_im/shop/user/get_shop_live_metrics` | GET | 3 | **新**：店铺 IM 实时指标（`delay_seconds / shop_live_metrics`） |

#### 7.2.4 订单 / 履约（14 个，含慢接口）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/fulfillment/order/list` | POST | 1 | 已有，**本次耗时 10.7s**（详见 §7.5） |
| `/api/fulfillment/order/search_count` | POST | 2 | 已有，**本次耗时 9.4s**；请求 body 一次性查 5 个状态码 `["100","101","1100","1200","102"]` |
| `/api/fulfillment/order/search_layout/get` | POST | 1 | 已有 |
| `/api/fulfillment/order/export_record/get` | POST | 1 | 已有 |
| `/api/fulfillment/dashboard/get` | POST | 1 | 已有 |
| `/api/fulfillment/rule_express/list` | POST | 1 | **新**：规则表达式（`ruleExpressScene: [7,6,8]` = 待处理/进行中/已完成） |
| `/api/fulfillment/next_day_delivery/score/get` | POST | 1 | **新**：次日达评分 |
| `/api/v1/fulfillment/shipping/options` | POST | 1 | **新**：物流选项（17 keys：SC/SOF/Invoice/Manifest/drop_off/logistics_services...） |
| `/api/v1/fulfillment/strategy/pickup_type/get` | POST | 1 | **新**：取件策略 |
| `/api/fulfillment/seller_create_label_setting/get` | GET | 1 | **新**：面单创建设置 |
| `/api/fulfillment/seller_print_setting/get` | GET | 1 | **新**：打印设置 |
| `/api/fulfillment/print/seller_config/get` | POST | 1 | 已有 |
| `/api/v1/trade/orders/warehouse/list` | GET | 1 | 已有，**但与下条重复**（见 §7.7） |
| `/api/v1/product/list/seller/warehouses` | GET | 1 | **新**：与上一条同一数据，product 域重复拉一次 |

#### 7.2.5 商品管理 / SPO（8 个）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/v1/product/local/products/list` | GET | 4 | 已有 |
| `/api/v1/product/local/same_products/list` | POST | 2 | 已有 |
| `/api/v1/product/tab/count/get` | GET | 2 | 已有（catalog 写 `products/tab_count`，实测是 `product/tab/count/get`） |
| `/api/v1/product/actions/list` | POST | 2 | **新**：商品批量操作列表（5 项 action） |
| `/api/v1/product/product_creation/preload` | GET | 2 | **新**：**发布预加载 99 keys**（所有发布规则/灰度/SKU 限制/AIGC 开关） |
| `/api/v1/product/regions/mget` | GET | 1 | **新**：可售区域 |
| `/api/v1/product/commission/config/get` | POST | 1 | **新**：平台佣金配置 |
| `/api/v1/product/oc/seller_product_opportunity/product/performance/Card` | POST | 1 | **新**：SPO 表现卡片（12 指标：pv/ctr/sale_rate/gmv/in_checking_spo_num...） |

#### 7.2.6 增长 / AIGC 视频工具（5 个）

| 路径 | 方法 | count | 备注 |
| --- | --- | --- | --- |
| `/api/v1/sea_product/growth/product_list` | POST | 2 | **新**：增长候选商品（`stage_id / page_num / filter_criteria`） |
| `/api/v1/sea_product/growth/batch/recommendation/tasks` | GET | 2 | **新** |
| `/api/v1/sea_product/growth/aigc_video` | POST | 2 | **新**：生成 AIGC 视频（body 为 `{}`） |
| `/api/v1/sea_product/growth/aigc_video/info` | GET | 2 | 已有 |
| `/api/v1/sea_product/growth/aigc_video/batch_tasks` | GET | 2 | **新** |

### 7.3 ⚠️ request_headers 捕获严重不全（需修复）

虽然 config #288 设置 `capture_headers=true`，但实际只截到 1~2 个 key：

```jsonc
// GET /api/v1/seller/message/list
{ "x-tt-oec-region": "VN" }

// POST /api/fulfillment/order/list
{ "content-type": "application/json", "x-tt-oec-region": "VN" }
```

**缺**：cookie / authorization / user-agent / referer / x-tt-token / x-shop-id 等。

| 字段填充率（106 条 host 记录） | 值 |
| --- | --- |
| request_headers | 106/106（都有，但**只 1~2 key**） |
| request_body | 28/106（≈ POST 数，少 3 条 POST 没 body） |
| response_headers | 103/106 |
| response_body | 102/106 |
| `seller_id` / `advertiser_id` 列 | **全 null** ← headers 漏抓的下游症状 |

**怀疑**：扩展 `webRequest.onBeforeSendHeaders` 在 Manifest V3 下，Cookie / Authorization 这类敏感头默认**禁止读取**（需要 `extraHeaders` + `host_permissions` 配合，或迁移到 `chrome.webRequest.onBeforeRequest` 不带 headers，或用 declarativeNetRequest）。

**修复方向**（待评估）：
1. 检查 `manifest.json` 是否声明 `"webRequestExtraHeaders"` 和 `"host_permissions": ["<all_urls>"]`
2. 或扩展侧改用 declarativeNetRequest + 自建 header 注入
3. 或后端改用 cookie 中的 `sid_guard` / `tt_token` 推断 seller_id（v3 抓不到的话可能需要 fallback 到 IP / shop_id path）

### 7.3.1 seller_id fallback 修复（lane feat/settlement-data-usability，13:21 burst 揭示）

13:21 burst 抓到的 URL query string 里**全都带 `oec_seller_id` 和 `seller_id` 作为客户端身份**——这是 TikTok OEC SDK (aid=6556 / app_name=i18n_ecom_shop) 的标准做法，client 身份不进 cookie 而是走 URL 参数。

**实测**（500 条抽样）：
| URL key | 出现次数 | 说明 |
| --- | ---:| --- |
| `oec_seller_id` | 96 | 99% 同时有 `seller_id`（api16 域 JSON API），2 条仅 oec_seller_id |
| `seller_id` | 94 | 与 `oec_seller_id` 几乎重叠 |
| `aid=6556` | 98 | OEC app 标识（仅 api16 + seller 域 JSON API）|
| `aid` 为空 | 402 | 监控/CDN/HTML 页 — 本来就不需 seller 信息 |

**修复**：`tts_erp_v2/api/v2/intercept.py:618+` 的 `/v2/intercept/sync` 端点，在 INSERT 时把 `req.seller_id or _extract_seller_id_from_url(req.url)` 作为新值。辅助函数 `_extract_seller_id_from_url` ：

```python
_URL_SELLER_ID_KEYS = ("oec_seller_id", "seller_id")  # 优先 oec_seller_id

def _extract_seller_id_from_url(url: str) -> str | None:
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=False)
    except (ValueError, TypeError):
        return None
    for key in _URL_SELLER_ID_KEYS:
        values = qs.get(key)
        if values:
            value = values[0].strip()
            if value:
                return value[:128]
    return None
```

**优先级**：body 显式 `sellerId` > URL `oec_seller_id` > URL `seller_id` > NULL。body 显式传的不会被 URL 覆盖。

**测试**（`tests/api/test_intercept_sync.py` 新增 4 条）：
- `test_sync_extracts_seller_id_from_oec_seller_id_query_param`：URL 提取 + list filter 验证
- `test_sync_falls_back_to_seller_id_query_param`：fallback seller_id
- `test_sync_keeps_explicit_seller_id_over_url_extraction`：body 优先级
- `test_sync_url_without_seller_id_leaves_seller_id_null`：URL 缺失时仍为 null

**未做**：
- 现有 3638 条历史记录的 seller_id 列仍为 NULL（这 lane 只改未来写入的逻辑；backfill 需另外 lane、且只对能从中提取 oec_seller_id 的记录有效）
- advertiser_id 未做同样提取（URL 里没看到稳定 key，待确认）
- 扩展侧 headers 漏抓本身（webRequest.onBeforeSendHeaders 拿不到 cookie/authorization）仍未修

### 7.4 订单 19 个 module 完整结构（实测 n=50 orders）

`POST /api/fulfillment/order/list` 响应中每个 `main_orders[i]` 包含 19 个 module：

| # | module | 类型 | 关键字段 | 用途 |
| -: | --- | --- | --- | --- |
| 1 | `main_order_id` | string | `"586043638880437647"` | 主单 ID |
| 2 | `sku_module` | array | `sku_id / sku_name / sku_unit_price / product_image.url_list[0]` | SKU 行 |
| 3 | `fulfill_line_module` | array | 同 `sku_module` 字段 | 履约行（结构与 sku_module 相同，可能是冗余） |
| 4 | `price_module` | object | `sub_total / grand_total`（VND，价格在 `price_val` 字符串） | 价格 |
| 5 | `order_status_module` | array | `main_order_status / sku_display_status`（整数状态码） | 订单状态 |
| 6 | `fulfillment_module` | array | `fulfillment_status_v2=19000 / rts_time / print_time / ship_exception_code` | 履约 |
| 7 | `delivery_module` | array | 详见下文 §7.4.1 | 物流 + 仓库 |
| 8 | `trade_order_module` | object | `pay_method / sale_region / create_time / shipping_fee` | 交易 |
| 9 | `trade_order_id_mapper` | object | `main_order_id ↔ order_line_ids[]` | 映射 |
| 10 | `fulfill_unit_id_mapper` | array | `package_id / fulfill_unit_id` | 履约单元 |
| 11 | `buyer_info_module` | object | `buyer_nickname / cpf / avatar / delivery_preference` | 买家 |
| 12 | `order_label_module` | array | `isPreOrder` | 标签 |
| 13 | `note_module` | object | `has_buyer_note / has_seller_note` | 备注 |
| 14 | `action_module` | object | `action_list[]`（整数动作码）+ `buyer_im_action_link` | 操作 |
| 15 | `logistics_info_module` | array | `fulfill_unit_id + logistics_detail_item.{timestamp,display_msg}` | 物流轨迹文案 |
| 16 | `print_label_module` | array | `label_status=50 / picking_list_status / packing_list_status` | 面单 |
| 17 | `reminder_module` | array | `order_line_id`（仅占位） | 提醒 |
| 18 | `reverse_module` | array | `reverse_type / reverse_status / refund_time / cancel_desc` | 退款（9/50 有） |
| 19 | （无独立 `settlement_module`） | — | — | **结算字段见 §7.6** |

#### 7.4.1 `delivery_module[]` 完整结构（**取物流信息的主入口**）

```jsonc
{
  "pkg_attr": {
    "weight":   { "unit": 1, "weight": "260" },        // 克
    "dimension":{ "unit": 1, "width": "11", "height": "5", "length": "11" }  // cm
  },
  "receipt_id":       "1210841489937433999",
  "fulfill_unit_id":  "1210841489937433999",
  "tracking_no":      "WSWH3398821253",                 // ✅ 当前快递单号
  "last_tracking_no": "TTCB7040449215",                 // 上一个单号（换单时用）
  "pickup_type":      2,
  "buyer_region":     "VN",                              // 收货地
  "warehouse_id":     "7661207737776293652",
  "warehouse_name":   "优航义乌仓库",
  "warehouse_region": "CN",                              // 发货地
  "warehouse_sub_type": 3,
  "payment_total": {
    "symbol": "₫", "currency": "VND",
    "price_val": "577523", "format_price": "577.523₫"
  },
  "shipping_fee": {},                                    // 空对象（本 sample 是包邮单；16% 单运费非 0，详见 §7.4.5 8 单运费样例）
  "logistics_service_info": {
    "logistics_service_id":   "7156147842033714945",
    "logistics_service_name": "全球经济运输服务",          // 服务名（中文）
    "logistics_service_type": 0,
    "logistics_service_level": "经济运输",
    "logistics_service_name_key": "ecom_logistics_type_cb_economy",
    "logistics_service_type_key": "logistic_service_type_shipping_via_platform",
    "logistics_service_delivery_option": 3
  },
  "shipment_provider_info": {
    "id":   "7439297584469903122",
    "name": "Wise Express - DCS",                         // ✅ 承运商
    "icon_url": "https://p16-oec-sg.ibyteimg.com/.../icon.jpeg"
  }
}
```

**3 个样本**：全部 Wise Express - DCS，从义乌发越南。

#### 7.4.2 `logistics_info_module[]`（**轨迹文案**）

```jsonc
[
  {
    "fulfill_unit_id": "1210841489937433999",
    "logistics_detail_item": {
      "timestamp": 1789293724140,
      "display_msg": "你的包裹已取消配送。"
    }
  }
]
```

3 个样本的 display_msg 分布：
- "你的包裹已取消配送。"（×2）
- "你的订单已由商家打包，正在等待承运商上门取件并运送至配送中心。"（×1）

#### 7.4.3 `action_module.action_list[]`（动作码）

实测 3 个主单的 action_list：

| 样本 | action_list | 含义推测 |
| --- | --- | --- |
| order[0] | `[1000, 300, 900]` | 取消 / 标记 / 重新发货？需对照 |
| order[13] | `[100, 900]` | 已付款 / 重新发货？ |
| order[14] | `[100, 900]` | 同上 |

> ⚠ 待对照 seller center 操作菜单确认。

> ⚠️ **命名混淆警告**：本节 `action_list[]` 是**卖家中心操作动作码**（"取消/标记/重新发货"），**不是**物流跟踪事件 `action_code`（§2 物流 24 个事件码，含 `38301` = 海外取消判据）。两者**字段名相近但完全无关**。
> 物流 `action_code` 完整字典见 [`enums/action-code.md`](enums/action-code.md)；本节 `action_list` 字典未文档化（🔴 未观测）。

#### 7.4.4 `reverse_module[]`（9/50 单有退款）

```jsonc
{
  "cancel_desc":        "系统自动批准取消申请",
  "cancelled_time":     "1789293723",          // 秒级字符串
  "refund_time":        "1789293723",          // 退款时间（**关键字段**）
  "reverse_from":       1,
  "reverse_type":       4,                      // 4=买家取消？
  "reverse_status":     100,
  "reverse_order_id":   "4042312456781923727",
  "reverse_reason":     "不想要了",
  "reverse_reminder":   { "items": [...] },     // 文案提醒
  "order_line_ids":     ["586043638880503183"]
}
```

> **2026-09-13 补注（lane feat/after-sales-table）**：上面是 `intercepted_requests.response_body` 里内嵌的 `reverse_module[]`（订单 list 拉到的快照）。独立的售后/取消 API（`/return_refund/202309/cancellations/search`）现在有专门结构化表 `plugin.after_sales` + `plugin.after_sale_items`（migration 0030），见 `tech-doc/intercept-plugin-canonical.md §1.1 / §3`。

##### 7.4.4.1 完整样本：`585900098675508729` 海外取消案例（system cancel vs buyer cancel 区别）

> 这是 §7.8 表里 `reverse_type=1` / `reverse_status=100` 的**唯一已观测样本**——证明 1 类是**系统取消**（不是买家主动取消）。

```jsonc
// 原始 cancelled 时间: 2026-09-14 07:01:46 (UTC+0)
{
  "role":                "SYSTEM",                                  // 关键：SYSTEM 表明系统自动生成
  "order_id":            "585900098675508729",
  "cancel_id":           "4042323178361161209",                     // 独立售后单号
  "cancel_type":         "CANCEL",                                  // 系统/卖家取消（不是 BUYER_CANCEL）
  "cancel_status":       "CANCELLATION_REQUEST_COMPLETE",          // 终态
  "cancel_reason":       "returned_to_shipper_other",               // 原因：退件
  "cancel_reason_text":  "Package delivery failed",                 // 人类可读描述
  "create_time":         1789369306,                                // = 2026-09-14 07:01:46 UTC
  "update_time":         1789369306,
  "should_replenish_stock": true,                                   // 库存回补
  "cancel_line_items": [{
    "sku_id":             "1737133142893102327",
    "sku_name":           "Xám, L 57.5KG‑62.5KG",
    "product_name":       "Áo thun nam tay ngắn cổ đứng cài ba nút, họa tiết sọc ngang dệt thoáng tông xám, phong cách lịch lãm",
    "product_image":      {"url": "https://p16-oec-sg.ibyteimg.com/..."},
    "order_line_item_id": "585900098675574265",
    "cancel_line_item_id": "4042323178361226745"
  }]
}
```

**对应物流时间线**（完整 43 事件，从 `integration.raw_records` 实证）：

```
2026-09-05 03:52  10101  下单（VN Bridge nook，COD，585,540 VND）
2026-09-05 06:48  20101  卖家已打包
2026-09-06 ~09-07     始发国（CN 义乌分拣/离境/出口清关）
2026-09-08 09:20  34301  Departed CN
2026-09-08 12:00  38301  ★ 抵达越南口岸（=海外取消判据的"海外"）
2026-09-08 ~09-10     越南境内运输（Bạc Liêu）
2026-09-10 10:22  40601  ★ 客户拒收（=海外取消触发）
2026-09-10 ~09-14     退件途中（70201 × 15）
2026-09-14 07:00  80101  ★ 退回卖家（=物流终态）
2026-09-14 07:01  —      系统取消订单（cancel_id=4042323178361161209）
```

**关键差异**（`reverse_type=1` vs `reverse_type=4`）：

| 字段 | `reverse_type=1`（系统取消，本样本） | `reverse_type=4`（买家取消，14 单样本） |
| --- | --- | --- |
| 触发原因 | 物流失败（`returned_to_shipper_other`） | 买家主动（"不想要了"/"发现更优惠的价格"/"需要更改收货地址"等） |
| 物流状态 | 必含 38301（已到目的国） + 终态码（80101/110101） | 不一定含海外物流（可能未发货就取消） |
| 取消时序 | `cancelled_time` = 物流终态后 几小时 | 可能在发货前 |
| 库存回补 | `should_replenish_stock=true` | 视情况 |

> **完整 reverse_type / reverse_status 枚举见 [`enums/reverse-type.md`](enums/reverse-type.md) 和 [`enums/reverse-status.md`](enums/reverse-status.md)。cancel_reason 全部 9+ 种已观测样本见 [`enums/cancel-reason.md`](enums/cancel-reason.md)。**

#### 7.4.5 `price_module` 拆解：8 单运费样例

**字段名确认**（订单域用 `price_val`，结算域用 `amount`）：

```jsonc{
  "price_module": {
    "sub_total":   { "symbol": "₫", "currency": "VND", "price_val": "577523", "format_price": "577.523₫" },
    "grand_total": { "symbol": "₫", "currency": "VND", "price_val": "577523", "format_price": "577.523₫" },
    "main_order_id": "586043638880437647"
  }
}
```

订单域金额字段使用 `price_val`（§7.4 / §7.4.1 / §7.4.5 均一致）。**结算域**（`/api/v1/pay/statement/...`）使用 `amount`（如 `total_balance.amount`、`in_come.amount`、`fees.amount`），不要混用。

**公式**（50/50 验证通过）：

```
grand_total = sum(sku_total_price) + shipping_fee
```

**8 单运费明细**（从 11:31 burst 50 单中挑出，`sub_total < grand_total` 的全部 8 单）：

| # | main_order_id | sub_total | shipping_fee | grand_total | ratio | SKU |
| - | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `586026126655260356` | 690,850 | **17,000** | 707,850 | 2.46% | Xanh mực, 2XL(70-77.5kg) |
| 2 | `586014607863481477` | 750,462 | **17,000** | 767,462 | 2.27% | Màu đen, M 57.5KG-67.5KG |
| 3 | `585992484307765000` | 547,523 | **17,000** | 564,523 | 3.10% | Sọc cam, M (45-57.5 kg) |
| 4 | `585992300305745672` | 577,523 | **17,000** | 594,523 | 2.94% | Sọc đen, M (45-57.5 kg) |
| 5 | `585968306069014066` | 577,523 | **17,000** | 594,523 | 2.94% | Sọc xám, 4XL (87.5-95 kg) |
| 6 | `585986403696936677` | 716,645 | **30,000** | 746,645 | 4.19% | Xanh nhạt, L 57.5KG-65KG |
| 7 | `585971536482567908` | 554,901 | **30,000** | 584,901 | 5.41% | Màu trắng, XXL |
| 8 | `585971299152660196` | 647,917 | **30,000** | 677,917 | 4.63% | Đen, 2XL 72.5-80 kg |

**未解问题**（需新 lane 抓已结后 `statement/transaction/detail` 验证）：

- 17000 / 30000 VND 是定档，比例 2.27%–5.41% 不固定——**是运费（按订单金额阶梯）还是平台佣金（按比例）？**需要查看对应单在 `fee_list[]` 中 `subtotal_after_discount` 与 `subtotal_before_discount` 的差额。
- `delivery_module[0].shipping_fee = {}`（空对象）与 `trade_order_module.shipping_fee = 0/17000/30000` 的语义差异是**订单级 vs 包裹级**——需多包裹单验证。
- 8 单全为 1 个 SKU、越南跨境+COD 样本；未覆盖多 SKU/非 COD/多包裹场景。

**summary**：

- 8/50 单（16%）有运费，42/50 单（84%）包邮
- 2 个运费档位：17000 VND 与 30000 VND（都是 0.71~0.75 USD）
- 5 个 `17000` 档平均比 3 个 `30000` 档**低**（金额区间不同，不是商品价不同）

> **2026-09-13 21:50 补注**：17k/30k 拆解（运费 vs 佣金）经产品确认**不拆**。业务侧只需 `price_module.grand_total` 一个数字作为"买家实付总额"，运费/佣金明细不影响使用。问题 1 关闭，不需等 8 单自然结算后反查。

#### 7.4.6 `pay_method` 枚举值 + 多 SKU 订单样例

##### 7.4.6.1 `pay_method` 枚举全集（11:31 burst 50 单实测）

| pay_method | 出现次数 | 含义（猜测） | 业务范围 |
| --- | ---: | --- | --- |
| `Cash on delivery` | 42 | 货到付款（COD） | 跨境 + 越南本地 |
| `MoMo 电子钱包` | 3 | 越南本地电子钱包 | 越南本地买家 |
| `Credit/debit card` | 2 | 信用卡 / 借记卡 | 跨境 + 越南本地 |
| `TikTok Shop Balance` | 2 | TikTok 钱包余额 | 跨境 + 越南本地 |
| `VNPAY` | 1 | 越南本地聚合支付 | 越南本地 |

**可能未覆盖的**（需更多 burst 验证）：Apple Pay、Google Pay、PayPal、Bank Transfer、COD 之外的其他本地钱包。

**8 运费档单的 pay_method 交叉**（验证运费与支付方式无关）：

| main_order_id | 差额 (VND) | pay_method |
| --- | ---: | --- |
| `586026126655260356` | 17,000 | **MoMo 电子钱包** |
| `586014607863481477` | 17,000 | Cash on delivery |
| `585992484307765000` | 17,000 | Cash on delivery |
| `585992300305745672` | 17,000 | Cash on delivery |
| `585968306069014066` | 17,000 | **Credit/debit card** |
| `585986403696936677` | 30,000 | Cash on delivery |
| `585971536482567908` | 30,000 | Cash on delivery |
| `585971299152660196` | 30,000 | Cash on delivery |

→ **17k/30k 不是 COD 专属**，MoMo 和 Credit card 也有 17k。排除"仅 COD 运费"假设。

##### 7.4.6.2 1 单多 SKU 样例（11:31 burst 50 单里唯一）

| 字段 | 值 |
| --- | --- |
| `main_order_id` | **`586042625860273841`** |
| `sku_module.length` | **2** （99% 单是 1 个 SKU，这单罕见） |
| `fulfill_line_module.length` | 2 （与 sku_module 同结构，可能冗余） |
| `delivery_module.length` | 1 |
| `price_module.sub_total` | 1,184,712 VND |
| `price_module.grand_total` | **1,184,712 VND** (= sum(sku_total_price) + shipping_fee 0) |
| `trade_order_module.shipping_fee` | 0 VND |
| `trade_order_module.pay_method` | Cash on delivery |

**2 个 SKU 详情**：

| sku_id | sku_name | product_id | qty | unit_price | total_price |
| --- | --- | --- | ---: | ---: | ---: |
| `1736527243602265335` | Màu xanh lục Xám, L(55-62.5kg) | `1736527242804888823` | 1 | 674,134 | 674,134 |
| `1736931118053491959` | Màu tím, L 57.5KG-65KG | `1736931118766392567` | 1 | 510,578 | 510,578 |
| **sum** |  |  | 2 |  | **1,184,712** ✓ |

**核心结论**：多 SKU 订单的 `grand_total` 计算与单 SKU **完全相同**——仍是 `sum(sku_total_price) + shipping_fee`。`sku_module` 是数组，按位置遍历求和即可。

##### 7.4.6.3 TODO：多包裹订单（`delivery_module.length > 1`）

> **状态**：未抓到。
> **11:31 burst 50 单实测**：`delivery_module` 长度分布 `{1: 50}`，**全 50 单都是 1 包裹**。
> **需要的触发场景**：卖家操作 1 单多包裹的发货（如多 SKU 订单分仓发货、或者大件拆 2 个包裹）。
> **目前推测的字段含义**（需验证）：
> - `trade_order_module.shipping_fee` = 订单级运费（业务定义）
> - `delivery_module[].shipping_fee` = 包裹级运费（实际承运商收的；单包裹时观察到 `{}` 空对象，原因待查）
> - `delivery_module[].payment_total` = 包裹实付总额（与 `price_module.grand_total` 应相等，单包裹时已验证）
> **补抓方式**：
> 1. 让卖家操作 1 单多包裹订单（自然等待 1~2 周）
> 2. 或卖家手动在卖家中心后台拆分一个 1 单多包裹订单触发
> 3. 抓到后对比 `delivery_module[].shipping_fee` vs `trade_order_module.shipping_fee`，确认是 "订单级 ≠ sum(包裹级)" 还是 "相等"

```

### 7.5 关键接口实测性能

按 host `api16-normal-sg.tiktokshopglobalselling.com` 内 106 条统计：

| 路径 | 耗时 | 备注 |
| --- | --- | --- |
| `/api/fulfillment/order/list` | **10690ms** | 50 单/页，total=959 — 后端聚合慢 |
| `/api/fulfillment/order/search_count` | **9415ms**（×2） | 一次性查 5 个状态码，可拆并行 |
| `/api/v1/product/local/products/list` | 4225ms median / 7216ms max | 商品列表聚合慢 |
| `/api/v1/seller/message/outage/list` | 5267ms median | 公告列表 |
| `/api/v1/sellerassistant/discover_chatbotevent` | 613ms median / **6277ms max** | 9 次调用，长尾 6.3s |
| `/api/v1/product/actions/list` | 7235ms median | 商品操作聚合 |
| `/api/v1/seller/popup/list` | 6164ms median | 弹窗聚合 |
| `/api/v1/product/product_creation/preload` | 1716ms median | 99 keys 一次返回，体积大可接受 |
| `/api/v3/seller/common/get` | 1688ms median / 4771ms max | 账户全量 |

**整体**：host p95 ≈ 6.3s，p50 ≈ 1.5s。订单页主查询 10s 级是 TikTok 后端慢，不是我们问题。

### 7.6 结算 / 未结算数据缺失

**当前 api16 域未发现任何 settlement/finance endpoint**。对 1962 条全库 + 50 条订单 detail 用 16 个关键词（`settlement / settle / finance / billing / fund / payout / withdraw / revenue / income / balance / ledger / cash / escrow / payable / receivable` + `账期/结算/账单/资金/提现/应收/应付`）扫描：

| 扫描维度 | 结果 |
| --- | --- |
| endpoint_path 含关键词 | **0** |
| response_body 顶层 key 含关键词 | **0**（最相关的是 `reverse_module[].refund_time`，是退款不是结算） |
| order module 名含关键词 | **0**（无 `settlement_module`） |
| 中文 string 含"结算" | 1 条（订单搜索筛选器文案："赔付结算中"） |
| 中文 string 含"提现" | 3 条（消息中心消息分类名） |

**结论**：卖家今天只打开了订单管理页，**未访问 Finance / Earnings 页**——结算数据在独立域或独立 tab，本次 burst 没覆盖。

**可能位置**（待验证）：
- `seller.tiktokshopglobalselling.com`（config #287 标 blacklist，但实际可能是 Finance 域）
- `fund.tiktokshopglobalselling.com` / `earnings.tiktokshopglobalselling.com`（推测，未观测）
- `/api/v1/finance/*` 或 `/api/v1/settlement/*`（路径推测，未观测）

### 7.6.1 12:55 新 burst 修正（lane fix/seller-finance-path-correction）

12:55 seller 实际访问了 Finance 页，扩展重新上传了 458 条新数据，揭示真实路径模式（**原 §7.6 推测错了一半**）：

| host | 真实路径 | 触发 config |
| --- | --- | --- |
| `api16-normal-sg.tiktokshopglobalselling.com` | `/api/v1/finance/acquiring/query/account`（×3） | **#288 /\* 通配** ✓ |
| `seller.tiktokshopglobalselling.com` | `/finance/analysis`（×2）、`/finance/bills`（×1）、`/finance/bills/1`（×1，账单详情）、`/finance/bill-payment`（×2） | ❌ **之前无 config 匹配** |

**关键发现**：
- **seller 中心 web app 用 `/finance/*` 短前缀**，**不带 `/api/v1` 前缀**（与之前猜的 `/api/v1/finance/*` 不一样）
- api16 才是 REST API server，用 `/api/v1/*`
- seller 是 web app，路径风格更 web-style（`/finance`、`/passport`、`/ttwid`）

**v1 config 错误原因**：第一波 burst 卖家只访问订单管理页，没看到任何 finance 路径；lane `fix/seller-host-path-whitelist` (c2f14eb) 按 REST API 习惯猜了 `/api/v1/{wallet,refund,payout,finance}/*` 4 条，全部没命中（matched=None + whitelisted=False → 只存了 metadata）。

**v2 修复**（lane `fix/seller-finance-path-correction`）：
- POST config **#314**：`seller.tiktokshopglobalselling.com / /finance/* / whitelist`（实测正确路径）
- PATCH /toggle disable #287/#311/#312/#313（v1 4 条错路径全关）
- 当前 enabled = 2 条：`#288 (api16 /*) + #314 (seller /finance/*)`
- v2 burst 验证：#314 命中后 seller 域 finance 请求会从 metadata-only 升级为 full capture（含 headers/body）

**v1→v2 经验**：
- seller 域 REST 风格路径猜测全部失败，下次新增 seller 域 whitelist 应**先实测再配置**，不要从 REST 命名习惯推断
- 路径风格差异（api16 = `/api/v1/*` REST，seller = `/finance/*` web-style）是 TikTok Seller Center 的统一模式，记住

### 7.6.2 13:21 settlement burst 实测（lane feat/settlement-data-usability）

13:17~13:20 seller 访问了 finance 页面，**11 个 settlement endpoint 被抓到**（全部命中 #288 /\* 通配）：

| # | endpoint | method | 作用 | sample key |
| -: | --- | :-: | --- | --- |
| 1 | `/api/v1/pay/statement/order/list` | POST | **已结算+未结算订单列表**（按 `settlement_status` 区分） | `total_record=143`(已结算) / `38`(未结算) |
| 2 | `/api/v1/pay/statement/transaction/detail` | POST | 单条已结算交易详情 | `fees / in_come / fee_list[]` |
| 3 | `/api/v1/pay/statement/stat/info` | POST | **未结算聚合** | `to_settle_amount_stat.amount=66021528 VND` |
| 4 | `/api/v1/pay/statement/payment/list` | POST | 支付列表（当前空） | `total_record=0` |
| 5 | `/api/v1/pay/statement/balance/detail/query` | POST | 余额明细（当前空） | `total=0` |
| 6 | `/api/v1/pay/statement/gray` | POST | 灰度检查 | `gray_scene_list=[1]` |
| 7 | `/api/v1/pay/settlement/settings` | GET | 结算设置 | `reserve_version / statement_version / pc_finance_setting` |
| 8 | `/api/v1/pay/settlement/file/list` | POST | 结算文件 v1（空） | — |
| 9 | `/api/v2/pay/settlement/file/list` | POST | 结算文件 v2（空） | — |
| 10 | `/api/v1/pay/settlement/payout/query_payout_config` | POST | 提现配置 | REQ.body 含 `oec_id` （与 seller_id 同源） |
| 11 | `/api/v1/pay/settlement/payout/reverse_block_check` | POST | 提现反向块检查 | `payout_blocked=false` |

**已结算/未结算 filter 机制**（关键）：

同一个 `/api/v1/pay/statement/order/list` 端点靠 query string 区分：
- `settlement_status=1` + `page_type=10` → 已结算（total_record=143）
- `settlement_status=2` + `page_type=6` → 未结算（total_record=38）
- 带 `reference_id` / `statement_id` → 单条详情

seller.js SPA bundle 里 `apiPrefix='https://api16-normal-sg.tiktokshopglobalselling.com'` → **全部走 api16 域**。

**已结算订单结构**（settlement_status=1，12 字段）：
```
fees, payment_id, trade_type, bill_period, placed_time, sku_records[],
statement_id, earning_amount, payment_status, trade_order_id,
settlement_amount, settlement_status, statement_version
```

**未结算订单结构**（settlement_status=2，**22 字段**，比已结算多 10）：
```
新增字段:
  delivery_time              "0"               # 0=未送达（本次样本就是这个）
  settlement_time            "1789268421260"   # 计划结算
  estimate_settle_time       "1789343999000"   # 预计结算
  estimate_settle_time_not_delivery: {params:["3"], starling_text:"送达后 3 天"}
  payment_status             20               # 已付但未结
  shipping_amount            {...}             # 运费
  settlement_amount          {...}             # 结算金额
  settlement_status 2                # 未结
  statement_detail_id        "7684672666385008391"
  trade_order_id             "586037160850720255"  # = main_order_id 关联到订单表
  platform_name              ""
```

**未结算聚合**（`/api/v1/pay/statement/stat/info`）：
- 总未结算 = **66,021,528 VND**（≈ 1.9万 RMB）
- `reasons_detail[]` 按原因拆分：
  - **49.9% (32,924,847 VND)** = 等待包裹妥投 (reason=1, 7天到账)
  - 2.8% = 退货申请进行中
  - 其他原因（未完整读到）
- 每条带 `starling_text` i18n 文案 + `params` (天数)

**业务洞察**：
- 卖家 143 单已结算 + 38 单未结算 = 181 单总订单（符合 11:31 burst 看到的 11:31 burst是 50 单 × 多页）
- 未结算资金在 VN 卖家这里 ≈ 1.9万 RMB，主要是“在途资金”
- 同一个 endpoint 用不同 status filter → 后端大概率是同一 SQL 表，区别是 status 字段

**§7.6 原始结论已被修正**：
- ~~结算数据缺失~~ → 已抓全（11 个 endpoint × 2 个 status 维度）
- ~~可能在独立 host~~ → 100% 在 api16 域（同一域、同一通配 #288）
- ~~可能叫 /api/v1/settlement/*~~ → 实际是 `/api/v1/pay/statement/*` 和 `/api/v1/pay/settlement/*`（两个不同模块）

**#288 验证**：10/10 个 settlement 命中 matched_config_id=288（api16 域 /\* 通配）✓ **无需新增 config**。

### 7.6.3 精简版端到端数据 lineage（3 endpoint）

> §7.6.2 是 burst 抓取快照（11 个 settlement endpoint + 结构）；本节是从 53+ endpoint 中**只留链路核心 3 个**后的精简 lineage（与产品对齐 2026-09-13 21:50 会议后拍板）。采集/物流/各配置/各 banner 端点全部进「附录（§7.6.4）」。

#### 7.6.3.1 核心 3 个 endpoint

| 域 | endpoint | method | 作用 |
|---|---|:-:|---|
| 订单 | `/api/fulfillment/order/list` | POST | **订单列表主入口**，返 `main_orders[]`（含 19 modules） |
| 结算 | `/api/v1/pay/statement/order/list` | POST | 已结算+未结算列表，靠 `settlement_status=1/2` 区分 |
| 结算 | `/api/v1/pay/statement/transaction/detail` | POST | 单条已结详情，返 `order_record {fees, in_come, fee_list[]}` |

> 被排除的 50+ endpoint 及原因见 §7.6.4。

#### 7.6.3.2 ID 连接键矩阵

```
oec_seller_id (URL query 提取，e1ca6ba lane)
   ↓
main_order_id (订单主键)
   ├─ order_line_id → sku_id → product_id
   ├─ fulfill_unit_id (包裹)
   │     ├─ warehouse_id / name / region    ← 内联在 delivery_module
   │     ├─ shipment_provider_info          ← 内联
   │     └─ logistics_service_info          ← 内联
   └─ payment_id
         ├─ statement_id (已结)
         └─ statement_detail_id (SKU 级)
```

**别名**：`reference_id` (settlement list 单条) = `trade_order_id` (settlement 字段) = `main_order_id` (订单)

#### 7.6.3.3 端到端数据流

```
[订单] /api/fulfillment/order/list
        POST body: {sort_info, search_condition, search_cursor, count, offset}
        out:  main_orders[] {
                 main_order_id  ──────────────────┐
                 fulfillment_module {status_v2}   │
                 delivery_module[] {            │
                   warehouse_*,                 │ 内联
                   shipment_provider_info,      │ 物流
                   logistics_service_info,      │
                   tracking_no,                 │
                   fulfill_unit_id ─────────────┤
                 }                              │
                 payment_id  ───────────────────┤
                 reverse_module[] (有退款时)     │
                 price_module, sku_module[], ...  │
               }                                ↓
[结算] /api/v1/pay/statement/order/list        │
        ?settlement_status=1 (已结)             │
        ?settlement_status=2 (未结)             │
        ?reference_id=<main_order_id> (单条)    │
        out: order_records[] {                   │
                statement_id,                     │
                payment_id,                       │
                trade_order_id = main_order_id,   │
                statement_detail_id,               │
                settlement_status, payment_status, │
                ... 22 字段 (settlement_status=2)  │
              }                                   │
                                               ↓
[结算] /api/v1/pay/statement/transaction/detail
        out: order_record { fees, in_come, fee_list[] }
```

#### 7.6.3.4 4 域采集/物流**无独立 endpoint**（精简后总图）

```
采集 (context)          订单 (data)               物流 (内联)            结算 (data)
─────────          ─────────────────          ────────────         ──────────────
oec_seller_id   →   main_order_id         ←   (delivery_module     →   statement_id
                                              自带:                statement_detail_id
  来源:                                          warehouse_id          payment_id
  1. URL query (e1ca6ba)                       tracking_no           trade_order_id
  2. body.sellerId                            fulfillment_module)
```

- **采集**：oec_seller_id 现在从 URL query 提取（e1ca6ba lane），不需要额外 endpoint
- **物流**：完整信息内联在 `delivery_module[]`（`warehouse_*` / `shipment_provider_info` / `logistics_service_info` / `tracking_no` / `fulfill_unit_id` ），零独立 endpoint
- **结算**：`statement_id` / `statement_detail_id` / `payment_id` 是链路终点

#### 7.6.3.5 典型场景

**场景 A：一笔订单从下单到结算**

```
T0  main_order_id=586037160850720255 出现在 /order/list
    ├─ delivery_module[0].fulfill_unit_id=1210841489937433999
    └─ delivery_module[0].payment_id=3701052790941189367
T1  包裹发货  →  delivery_module[0].tracking_no="WSWH3398821253"
    └─ logistics_info_module 追加 "等待承运商上门取件" 文案
T2  结算周期触发
    →  /api/v1/pay/statement/order/list?settlement_status=1
       出现新 record: { statement_id, statement_detail_id, trade_order_id=586037160850720255, ... }
T3  /api/v1/pay/statement/transaction/detail
    返回 fee_list[] 各 SKU 收税/活动/运费明细
```

**场景 B：退款影响结算**

```
买家发起退款
  → order list 同一条 main_order 出现 reverse_module[0] 字段填充
    ├─ reverse_module[0].refund_time = unix_ts
    └─ reverse_module[0].reverse_status = 100 (?)
  → /statement/order/list?settlement_status=2
    ├─ status 仍 2 (未结)
    └─ amount 减或从 to_settle 移除
```

### 7.6.4 附录：被排除的 50+ endpoint 及原因

> 本节是完整 burst 抓取清单的精简版，记录被排除的 endpoint 与原因（审计用，不进数据 lineage）。

#### 7.6.4.1 Banner 噪声（×6，UI 广告位，零数据价值）

```
/api/v1/logistics/orderBff/tcc_banners
/api/v1/fulfillment/reach/banner_list
/api/v1/seller/banner/list
/api/v1/product/stock/banner/check
/api/v1/seller/popup/list
/api/v1/pop/seller_common/island/event/get
```

#### 7.6.4.2 配置中心 / 身份（×5，seller/common/get 可作身份汇总但不入数据 lineage）

```
/api/v3/seller/common/get                → 21 keys 身份元数据（不进数据流；oec_seller_id 从 URL 提取替代）
/api/v1/arch/config_center_gw/get_config        → 灰度/功能开关
/api/v1/arch/config_center_gw/mget_config_by_app_name
/api/v1/common/region_domain                    → 域名解析
/api/v1/seller/onboard/v2/config/get            → 入驻配置
/api/v1/seller/homepage_allowlist/get           → 首页白名单
```

#### 7.6.4.3 遥测 / 轮询（×2，噪声）

```
/api/v1/bs/rt                            → 埋点上报
/api/v1/sellerassistant/discover_chatbotevent  → chatbot 6.7s/次 高频轮询
```

#### 7.6.4.4 消息中心（×6，独立域，不进订单/结算 lineage）

```
/api/v1/seller/message/pull_by_category_v2
/api/v1/seller/message/outage/list
/api/v1/seller/message/list
/api/v1/seller/message/get_page_channels
/api/v2/seller/message/get_msg_tabs
/api/v1/seller/feelgood/access_token/get
```

#### 7.6.4.5 物流配置（×7，元数据，不进 lineage）

```
/api/v1/trade/orders/warehouse/list        → 仓库元数据（与 delivery_module 重复）
/api/v1/product/list/seller/warehouses    → 同上跨 product 域重复调用
/api/v1/fulfillment/shipping/options      → 17 keys 发货配置
/api/v1/fulfillment/strategy/pickup_type/get
/api/fulfillment/seller_create_label_setting/get
/api/fulfillment/seller_print_setting/get
/api/fulfillment/print/seller_config/get
/api/fulfillment/rule_express/list
/api/fulfillment/dashboard/get
/api/fulfillment/next_day_delivery/score/get
```

#### 7.6.4.6 结算 meta/辅助（×5，备用，按需启用）

```
/api/v1/pay/statement/stat/info                  → 聚合 66M VND（list 已按订单给明细，冗余）
/api/v1/pay/settlement/payout/query_payout_config → REQ.body 含 oec_id 但现在 URL 提取取代
/api/v1/finance/acquiring/query/account            → 资金账户 biz_scene 9/10，按需用
/api/v1/pay/settlement/settings                    → 元数据
/api/v1/pay/settlement/file/list (v1+v2)          → 恒空
/api/v1/pay/settlement/payout/reverse_block_check
/api/v1/pay/statement/payment/list                → 恒空
/api/v1/pay/statement/balance/detail/query         → 恒空
/api/v1/pay/statement/gray                         → 灰度 meta
```

#### 7.6.4.7 订单管理 UI 辅助（×3，count/UI 配置）

```
/api/fulfillment/order/search_count       → UI tab 数字（list 已给明细，count 是 derived）
/api/fulfillment/order/search_layout/get  → 搜索栏 UI 配置
/api/fulfillment/order/export_record/get  → 导出历史
```

#### 7.6.4.8 商品（×9，不在订单/结算 lineage）

```
/api/v1/product/local/products/list
/api/v1/product/local/same_products/list
/api/v1/product/tab/count/get
/api/v1/product/actions/list
/api/v1/product/product_creation/preload
/api/v1/product/regions/mget
/api/v1/product/commission/config/get
/api/v1/product/oc/seller_product_opportunity/product/performance/Card
```

#### 7.6.4.9 IM 实时指标（×1，卖家中心辅助面板）

```
/api/v1/shop_im/shop/user/get_shop_live_metrics
```

#### 7.6.4.10 物流实时跟踪（未抓到，**未来按需补抓**）

```
推测 endpoint: /api/v1/logistics/tracking/query?fulfill_unit_id=...
返回: { tracking_events: [{timestamp, location, status}, ...] }
现状: burst 未触发（卖家未点 "查看完整物流" 按钮）
补抓: 卖家手动点一次后加 whitelist，再开新 lane 补 §7.6.3
```

### 7.7 高频轮询与重复调用

| 现象 | 数据 | 说明 |
| --- | --- | --- |
| chatbot 事件高频轮询 | `/api/v1/sellerassistant/discover_chatbotevent` ×9/68s ≈ **6.7s 一次** | 后台 heartbeat；卖家中心常驻源 |
| 仓库列表跨域重复 | `trade/orders/warehouse/list` + `product/list/seller/warehouses` 同一数据各拉一次 | 前端未去重 |
| 域名解析 (catalog 已记) | `/api/v1/common/region_domain` 高频 | catalog 已记录 |
| 埋点上报 | `/api/v1/bs/rt` | catalog 已记录 |

**扩展上传链路疑似中断**：

- 11:32:31 最后一条 `captured_at`
- 11:41:00 之后 10 分钟窗口 0 写入
- 11:50:32 仍 0 写入
- 整个 DB 共 1962 条，**全部位于 11:31:23~11:32:31 这 68 秒**

排查方向：
1. 扩展 `chrome.alarms` / 后台 service worker 是否被休眠
2. `sync` 端点是否 4xx/5xx（可查 `plugin.intercepted_requests.error_type`）
3. 11:32 之后用户是否真离开了 tab

<a id="sec-7-8"></a>
### 7.8 更新 §6 待确认事项（三列对比：09-09 / 09-13 / 09-15）

| 项目 | 状态（09-09） | 状态（09-13） | **状态（09-15）** | 字典位置 |
| --- | --- | --- | --- | --- |
| `main_order_status` 状态码映射 | 待确认 | **部分确认** | ✅ **已确认 5 码**（100/101/102/103/104） | [`enums/main-order-status.md`](enums/main-order-status.md) |
| `sku_display_status` 状态码映射 | 待确认 | 待确认 | 🟡 **部分确认 7 码**（100/111/112/121/122/130/140） | [`enums/sku-display-status.md`](enums/sku-display-status.md) |
| `trade_order_module.fulfillment_type` 枚举 | 待确认 | 待确认 (0) | 🟡 **0=FBM 已确认** | [`enums/fulfillment-type.md`](enums/fulfillment-type.md) |
| `pay_method` 枚举 | 未列 | §7.4.6 5 个 | ✅ **5 码已确认**（Cash on delivery / MoMo 电子钱包 / Credit/debit card / TikTok Shop Balance / VNPAY） | [`enums/pay-method.md`](enums/pay-method.md) |
| `reverse_type` 完整枚举 | 未列 | "待核实" | 🟡 **3 码已观测**（1=系统取消 / 3=退货退款 / 4=买家取消） | [`enums/reverse-type.md`](enums/reverse-type.md) |
| `reverse_status` 完整枚举 | 未列 | 未列 | 🟡 **2 码已观测**（4=处理中 / 100=已完成） | [`enums/reverse-status.md`](enums/reverse-status.md) |
| `cancel_type` 完整枚举 | 未列 | 未列 | ✅ **已确认**（BUYER_CANCEL / CANCEL） | [`enums/cancel-type.md`](enums/cancel-type.md) |
| `cancel_status` 完整枚举 | 未列 | 未列 | 🟡 **1 码已观测**（CANCELLATION_REQUEST_COMPLETE） | [`enums/cancel-status.md`](enums/cancel-status.md) |
| `cancel_reason` 完整枚举 | 未列 | 仅 1 种（`returned_to_shipper_other`） | 🟡 **9+ 码已观测** | [`enums/cancel-reason.md`](enums/cancel-reason.md) |
| `track_status` 自由文本 | 未列 | §7.7 提到 | 🟡 **2 码已观测**（Package picked up / Delivered） | [`enums/track-status.md`](enums/track-status.md) |
| `track_status` 终结（plugin 路径） | 未列 | 未列 | 🔴 **完全未观测**（plugin.shipments 零行） | [`enums/track-status.md`](enums/track-status.md) |
| `statement_type` int 枚举 | 未列 | 未列 | 🔴 **完全无样本** | [`enums/statement-type.md`](enums/statement-type.md) |
| `payment_pending_reason` int 枚举 | 未列 | 未列 | 🔴 **完全无样本** | [`enums/payment-pending-reason.md`](enums/payment-pending-reason.md) |
| `trade_order_id` ↔ `main_order_id` | 已确认 | 已确认 | ✅ 已确认 | — |
| `statement_sku_detail_id` 获取路径 | 待确认 | **仍未捕获** | 🔴 **仍未捕获** | §7.6 解释：未访问 Finance 页 |
| 退货订单列表接口 | 待捕获 | 待捕获 | 🔴 **仍为公告**，**不是列表** | — |
| **新增**：action_list 整数动作码 | — | **新增待确认** | 🔴 **未对照** | §7.4.3（与物流 `action_code` 无关，见 §7.4.3 ⚠️ 警告） |
| **新增**：request_headers 漏抓 | — | **新增阻塞** | 🔴 **仍为 bug** | §7.3，扩展侧 bug |
| **新增**：`discover_chatbotevent` 是否应纳入轮询预算 | — | **新增观察项** | 🟡 观察中 | §7.7 |
| **新增**：Finance / Earnings 域 host 与 endpoint | — | **新增待捕获** | 🔴 **仍未捕获** | §7.6 |
| **新增**（09-15）：订单详情 `reverse_type`/`reverse_status` 路径 | — | — | ✅ §2.8 已加 enums 链接 | — |
| **新增**（09-15）：`should_replenish_stock` 字段 | — | — | 🟡 **已观测**（=true 1 例，§7.4.4.1） | （写入 cancel-reason.md） |
| **新增**（09-15）：`/tracking` 端点 24 个 action_code 字典 | — | — | ✅ **已文档化** | [`enums/action-code.md`](enums/action-code.md) |

### 7.9 临时快照使用提示

> 本节是 09-13 当时观察到的真实数据快照，不是规范。
> 如果后续 burst 又抓到新数据，请**新增 §8 增量（YYYY-MM-DD）**，不要直接覆盖本节。
> §1~§6 的"已有 endpoint"基线仍然有效，新发现的 endpoint 列在 §7.2。
