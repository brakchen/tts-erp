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
  "shipping_fee": {},                                    // 空对象
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

### 7.8 更新 §6 待确认事项

| 项目 | 状态（09-09） | 状态（09-13） | 备注 |
| --- | --- | --- | --- |
| `main_order_status` 状态码映射 | 待确认 | **部分确认** | 104=已取消，101=已发货，见样本 |
| `sku_display_status` 状态码映射 | 待确认 | 待确认 | 样本 140=已取消 |
| `trade_order_module.fulfillment_type` 枚举 | 待确认 | 待确认 | 样本 0 |
| `trade_order_id` ↔ `main_order_id` | 已确认 | 已确认 | — |
| `statement_sku_detail_id` 获取路径 | 待确认 | **仍未捕获** | §7.6 解释：未访问 Finance 页 |
| 退货订单列表接口 | 待捕获 | 待捕获 | 仍是 `list_seller_announcement`（公告），不是列表 |
| **新增**：action_list 整数动作码 | — | **新增待确认** | §7.4.3 |
| **新增**：request_headers 漏抓 | — | **新增阻塞** | §7.3，扩展侧 bug |
| **新增**：`discover_chatbotevent` 是否应纳入轮询预算 | — | **新增观察项** | §7.7 |
| **新增**：Finance / Earnings 域 host 与 endpoint | — | **新增待捕获** | §7.6 |

### 7.9 临时快照使用提示

> 本节是 09-13 当时观察到的真实数据快照，不是规范。
> 如果后续 burst 又抓到新数据，请**新增 §8 增量（YYYY-MM-DD）**，不要直接覆盖本节。
> §1~§6 的"已有 endpoint"基线仍然有效，新发现的 endpoint 列在 §7.2。
