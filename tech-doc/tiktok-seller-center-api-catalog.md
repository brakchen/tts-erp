# TikTok Seller Center 接口目录

> 来源：Chrome 扩展域名观察功能捕获的实际请求/响应（2026-09-09）。
> 插件版本：0.1.117，店铺：VN 区域，seller_id=7494763368967603447。
> 本文档记录实际观测到的接口结构，作为解析规则的 truth source。

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
| `/order/detail` | GET | 订单详情页（HTML） | 浏览器导航 |

### 1.2 退货/售后域

| 路径 | 方法 | 说明 | 调用频率 |
| ------ | ------ | ------ | --------- |
| `/api/v1/reverse/orders/list_seller_announcement` | POST | 退货公告列表 | 每次打开订单页 |
| `/api/v1/reverse/orders/get_export_history` | POST | 退货导出历史 | 偶尔 |

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
| `trade_order_module.shipping_fee` | 运费 | orders.shipping_fee |
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

- 退货订单列表（类似 `/api/v1/reverse/orders/list`）
- 退货详情
- 退款详情
- 退货物流跟踪

**操作建议**：在 TikTok Seller Center 打开"退货/售后"页面，用域名观察功能捕获完整请求流。

---

## 6. 待确认事项

| 项目 | 状态 | 说明 |
| ------ | ------ | ------ |
| `order_status_module.main_order_status` 状态码映射 | 待确认 | 101=?, 102=? 需对照 TikTok 文档 |
| `sku_display_status` 状态码映射 | 待确认 | 111=? |
| `trade_order_module.fulfillment_type` 枚举 | 待确认 | 0=? 1=? |
| `trade_order_id` ↔ `main_order_id` 映射关系 | 已确认 | `trade_order_id_mapper` 提供映射 |
| `statement_sku_detail_id` 获取路径 | 待确认 | 需在结算页面捕获 |
| 退货订单列表接口 | 待捕获 | 需在退货页面浏览时抓取 |
