# miaoshou 模块 README

妙手开放平台（openapi.wanshifu.com）SDK + 同步集成。本文档记录关键接口的字段语义与
和 TikTok Shop 数据的对应关系，结论均基于生产数据实测。

---

## 核心接口：`search_move_collect_list`（商品发布/搬家记录）

这是妙手侧**商品关联的桥梁接口**：每条记录是一次"采集箱商品 → 搬家/刊登到 TikTok Shop"
的任务明细，同时携带关联两端的外部 id。它是 V3 数据模型中
`linkage.link_evidence` / `linkage.product_links`（`relation_type=MIAOSHOU_PUBLISHED_TO_TIKTOK`）
的事实来源。

- apifox：`api-482189163`
- path：`POST /open/v1/product/collect_box/tiktok/move_collect/search_move_collect_list`
- SDK：`MiaoshouErpClient.tk_collect_box.search_move_collect_list(page_no, page_size, status?, item_id?, source_item_id?)`（`miaoshou/endpoints/tk_collect_box.py`，`page_size` 上限 20）
- 同步入口：`POST :9877/sync/miaoshou_move_collect_tasks` → `tdd/miaoshou_sync.py::sync_miaoshou_move_collect_tasks` → 落库 `miaoshou_move_collect_tasks`

### 实测结论（2026-08-29，生产库）

| 结论 | 依据 |
| --- | --- |
| **`platformItemId` 是 TikTok SPU（product_id），不是 SKU** | 与 `order_items.product_id` 匹配 59 个，与 `order_items.sku_id` 匹配 0 个；`itemEditUrl` 形态为 `seller.tiktokglobalshop.com/product/edit/<id>` |
| 当前订单窗口内商品关联覆盖率 **100%** | 有销售记录的 TikTok 商品 59 个（`order_items` 去重 `product_id`），59/59 全部有妙手发布记录 |
| 妙手已发布 SPU 182 个，其中 59 个有销售 | 123 个 SPU 已上架但在当前已同步订单（719 单）中零销售 |
| 发布任务 237 条 = 182 success + 55 fail | fail 任务没有 `platformItemId`；success 与 `platformItemId` 1:1 |
| 199 个 distinct `collectBoxDetailId` | 存在同一采集商品被多次搬家 |
| 单店铺：`shopId=17060852 / VN / Bridge nook` | 其发布的商品落在 TikTok shop `7494763368967603447` 的销售数据中——`linkage.account_links` 的事实证据 |

⚠️ 覆盖率 100% 是**当前订单同步窗口内**的结论；窗口扩大后可能下降，应以持续监控指标为准。

### 已知问题（记入重构技术方案）

1. **QPS 限流静默截断**：妙手有账户级每秒频率限制（错误码 `accountApiQpsRateLimit`）。
   `sync_miaoshou_move_collect_tasks` 分页循环无重试无延时，第 2 页被限流返回空列表时
   被误判为"末页"，首次同步只落 20/237 条。重试 + 页间 sleep 修复后 12 页 237 条全量落库。
2. **`persist_miaoshou_move_collect_task` 强依赖 SDK model 对象**：直接传 raw dict 会因
   `dict 没有 .moveCollectTaskDetailId 属性` 静默失败（返回 falsy，不抛异常）。

### 请求参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `pageNo` | int | 页码，从 1 开始 |
| `pageSize` | int | 1..20 |
| `filter.status` | string? | 任务状态过滤（success / fail 等） |
| `filter.itemId` | string? | 按 TikTok 商品 id（SPU）精确过滤 |
| `filter.sourceItemId` | string? | 按 1688 等源头商品 id 精确过滤 |

### 响应 envelope

```json
{"result": "success", "code": "success", "message": "", "data": {"moveCollectDetailList": [...], "total": 237}}
```

限流时：`{"result": "fail", "code": "accountApiQpsRateLimit", "message": "账户接口每秒请求频率超限", "data": null}`。

### 字段对应表（API → SDK → DB → 语义 / V3 目标模型）

| API 字段 (camelCase) | SDK model 属性 | DB 列（`miaoshou_move_collect_tasks`） | 语义 | V3 目标模型 |
| --- | --- | --- | --- | --- |
| `moveCollectTaskDetailId` | `moveCollectTaskDetailId: str` | `move_collect_task_detail_id` (PK 之一) | 搬家任务明细 id | `linkage.link_evidence.source_external_id` |
| `collectBoxDetailId` | `collectBoxDetailId: str` | `collect_box_detail_id` | 采集箱商品 id（妙手侧商品） | `procurement.procurement_products.external_product_id` |
| `shopId` | `shopId: str` | `shop_id` (text) | 妙手店铺 id（非 TikTok shop id） | `linkage.account_links` / `procurement.procurement_accounts` |
| `platformItemId` | `platformItemId: str` | `platform_item_id` | **TikTok 商品 SPU id**（= `order_items.product_id`） | `commerce.channel_products.external_product_id` |
| `source` | `source: str` | `source` | 采购源头平台（1688 等） | `procurement.procurement_products.source_platform` |
| `sourceItemId` | `sourceItemId: str` | `source_item_id` | 源头平台商品 id（1688 offer id） | `procurement.procurement_products.source_item_id` |
| `sourceSite` | `sourceSite: str` | `source_site` | 源头站点 | 同上 |
| `sourceItemUrl` | `sourceItemUrl: str` | `source_item_url` | 源头商品 URL | `procurement.procurement_products.source_item_url` |
| `itemNum` | `itemNum: str` | `item_num` | 货号 | — |
| `cid` | `cid: str` | `cid` | TikTok 类目 id | `commerce.channel_products.category_id`（旁证） |
| `title` | `title: str` | `title` | 商品标题（快照） | 仅证据，不作正式关联依据 |
| `thumbnail` | `thumbnail: str` | `thumbnail` | 主图 URL（1688 源图） | 仅证据 |
| `isTiming` | `isTiming: str` | `is_timing` | 是否定时发布（"0"/"1"） | — |
| `status` | `status: str` | `status` | 任务状态（success / fail） | `linkage.product_links.status` |
| `reason` | `reason: str` | `reason` | 失败原因 | `linkage.link_issues.details` |
| `gmtCreate` | `gmtCreate: str` | `gmt_create` (text) | 任务创建时间（UTC+8 字符串，无时区） | `linkage.product_links.valid_from`（需转 timestamptz） |
| `gmtModified` | `gmtModified: str` | `gmt_modified` (text) | 最后修改时间（同上） | `source_updated_at`（需转 timestamptz） |
| `isRenewItem` | `isRenewItem: bool` | `is_renew_item` | 是否重新刊登 | — |
| `shopName` | `shopName: str` | `shop_name` | 妙手店铺名（快照） | 仅证据（不可作关联依据） |
| `site` / `siteName` | `site` / `siteName: str` | `site` / `site_name` | TikTok 站点（VN 等） | `commerce.channel_accounts.region`（旁证） |
| `itemEditUrl` | `itemEditUrl: str` | `item_edit_url` | TikTok 卖家中心编辑页 URL，含 product id | 仅证据 |
| `breadcrumb` | `breadcrumb: str` | `breadcrumb` | TikTok 类目路径 | 仅证据（类目校验辅助） |
| `ownerSubAppAccountId` | `ownerSubAppAccountId: int` | `owner_sub_app_account_id` | 妙手子账号 id（0=主账号） | — |
| `ownerSubAccountAliasName` | `ownerSubAccountAliasName: str` | `owner_sub_account_alias_name` | 子账号别名 | — |
| — | — | `platform` | 平台代号（固定 "tiktok"），PK 之一 | `integration.credentials.provider` |
| — | — | `raw_json` | 完整原始 JSON | `integration.raw_records` |
| — | — | `synced_at` | 落库时间 | `synced_at` |

### Demo 记录（生产实测，2026-08-20 发布）

```json
{
  "moveCollectTaskDetailId": "8507531700",
  "collectBoxDetailId": "3303946302",
  "shopId": "17060852",
  "platformItemId": "1737133200968680695",
  "source": "1688",
  "sourceItemId": "1047858038849",
  "sourceItemUrl": "http://detail.1688.com/offer/1047858038849.html",
  "itemEditUrl": "https://seller.tiktokglobalshop.com/product/edit/1737133200968680695?shop_region=VN",
  "cid": "601226",
  "breadcrumb": "Trang phục nam & Đồ lót>Áo nam>Áo thun",
  "title": "2026夏款男士圆领短袖休闲薄款T恤户外透气弹力夏季印花潮流迷彩",
  "status": "success",
  "isRenewItem": false,
  "shopName": "Bridge nook",
  "site": "VN",
  "siteName": "越南",
  "gmtCreate": "2026-08-20 19:05:33",
  "gmtModified": "2026-08-20 19:39:57",
  "ownerSubAppAccountId": 0,
  "ownerSubAccountAliasName": "主账号"
}
```

对应关系实例：该记录的 `platformItemId=1737133200968680695` 在 TikTok 侧
`order_items.product_id` 中存在（有真实销售），`collectBoxDetailId=3303946302` 指向
妙手采集箱中的 1688 源商品——一条完整的 `采购源头(1688) → 妙手采集 → TikTok 上架 → 销售`
链路证据。
