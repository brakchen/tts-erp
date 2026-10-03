# 妙手采购单采购价映射与人工采购价导入

> 数据源：妙手 ERP 页面接口
> `POST https://erp.91miaoshou.com/api/order/purchase/purchase_order/searchList`。
> 本文记录 2026-09-30 实测字段、关系、价格口径与一次性导入结果。

## 1. 结论

采购价必须读取采购单顶层 `purchaseItems[].sourceUnitPrice`，币种为人民币（CNY）。
`opOrderPackageList[].purchaseItems[].unitPrice` 是下游 TikTok 订单商品价格，币种由包裹的
`currency` 指定（本次数据主要为 VND），不能当采购价。

接口没有给顶层货源行与下游 SPU 行提供显式关联键。实测可用的关系是：

1. 顶层 `purchaseItems` 按 `sourceItemId` 去重并保留首次出现顺序；
2. 将所有 `opOrderPackageList[].purchaseItems` 展平，按 `platformItemId` 去重并保留首次出现顺序；
3. 两组的唯一项数量必须相等；
4. 按唯一组序号配对：第 N 个 `sourceItemId` 对应第 N 个 `platformItemId`；
5. 数量不相等的采购单不做推断，整单跳过并记录异常。

**不能直接逐行 `zip(purchaseItems, opOrderPackageList)`。** 同一 SPU/货源商品可能包含多个 SKU，
而两个数组的 SKU 排列并不一致。

## 2. 响应结构与字段关系

### 2.1 采购单层

| JSON 路径 | 含义 | 导入用途 |
| --- | --- | --- |
| `list[]` | 一条采购单查询结果 | 采购单根对象 |
| `list[].purchaseOrderSn` | 采购单号 | 去重与审计来源 |
| `list[].purchaseOrderFilterId` | 妙手查询结果行 ID | 同采购单号重复时的审计字段 |
| `list[].purchaseOrderStatus` | `finished` / `has_send` / `has_sign` / `wait_send` / `wait_pay` / `cancel` | `cancel`、`wait_pay` 不作为已成交采购价 |
| `list[].gmtPurchaseOrderStart` | 采购单创建时间（妙手页面时间） | 同一 SPU 选择最新采购事实 |
| `list[].purchaseOrderPayment` | 整张采购单付款金额 | 对账字段，不直接分摊到 SPU |
| `list[].purchaseOrderShippingFee` | 整张采购单运费 | 本次不计入 SPU 商品单价 |

### 2.2 1688 货源采购行

| JSON 路径 | 含义 | 导入用途 |
| --- | --- | --- |
| `purchaseItems[].sourceItemId` | 1688 offer/item ID | 货源商品（SPU 侧）的分组键 |
| `purchaseItems[].sourceSkuId` | 1688 SKU ID | 审计和 SKU 明细 |
| `purchaseItems[].sourceSkuSubName` | 1688 SKU 规格名 | 人工核对规格 |
| `purchaseItems[].sourceUnitPrice` | 实际采购单价，CNY | 人工采购价的基础金额 |
| `purchaseItems[].sourceQuantity` | 采购数量 | 同货源商品多 SKU 时的加权权重 |
| `purchaseItems[].sourceTitle` | 1688 商品标题 | 人工核对商品 |

同一 `sourceItemId` 在一张采购单中可能出现多个 SKU，SPU 级采购价按数量加权：

```text
spu_purchase_cost =
    Σ(sourceUnitPrice × sourceQuantity) / Σ(sourceQuantity)
```

`sourceQuantity <= 0`、价格缺失或无法解析的行不参与计算；一个货源组最终没有有效数量时跳过。

### 2.3 TikTok SPU / 下游订单行

| JSON 路径 | 含义 | 导入用途 |
| --- | --- | --- |
| `opOrderPackageList[]` | 与采购单关联的下游订单包裹 | 展平 SPU 行 |
| `opOrderPackageList[].shopId` | 妙手侧店铺 ID | 审计字段；不是 `commerce.shops.shop_id` |
| `opOrderPackageList[].shopName` | 妙手店铺名 | 人工核对店铺 |
| `opOrderPackageList[].currency` | 下游订单币种，例如 `VND` | 解释下游 `unitPrice`，不用于采购价 |
| `opOrderPackageList[].purchaseItems[].platformItemId` | TikTok SPU ID | 写入 `commerce.products_spu.spu_id` 对应的人工成本 |
| `...platformOuterSkuId` | 外部 SKU 编码 | 本次数据多数为空，不能作为关联键 |
| `...opOrderItemId` | 妙手下游订单行 ID | 审计字段 |
| `...skuSubName` | TikTok SKU 规格名 | 人工核对规格 |
| `...unitPrice` | TikTok 下游商品售价 | **不是采购价** |

### 2.4 数据库落点

采购价格是独立表，不依赖商品主档或人工采购价维表：

```text
miaoshou.purchase_prices
  miaoshou_shop_id  <- opOrderPackageList[].shopId
  shop_pk           <- 妙手店铺唯一映射到 commerce.shops.id（可为空）
  spu_id            <- platformItemId
  unit_cost         <- 上述 SPU 级采购价
  currency          <- CNY
  source_*          <- 采购单号、1688 offer、采购时间、状态和计算证据
```

唯一键是 `(credential_id, miaoshou_shop_id, spu_id)`。店铺匹配成功即写入，不要求
`commerce.products_spu` 存在；店铺未匹配/歧义只记录 `miaoshou.sync_issues`，不写采购价格表。人工采购价
`procurement.manual_product_costs` 是另一张人工覆盖表，定时清洗不会写入或覆盖它。

## 3. 示例采购单 `5127802669510007219`

该响应包含 6 个 SKU 采购行、3 个唯一 1688 商品、3 个唯一 TikTok SPU；输入中重复列出的
`1737335457050625154` 是同一 SPU 的不同 SKU，不是第 4 个唯一 SPU。

| 唯一组序号 | TikTok SPU (`platformItemId`) | 1688 offer (`sourceItemId`) | 1688 SKU 数 | 采购单价（CNY） |
| --- | --- | --- | ---: | ---: |
| 0 | `1736929955366339831` | `911814765239` | 1 | 26.00 |
| 1 | `1737335457050625154` | `1053836757309` | 3 | 29.00 |
| 2 | `1737316316203091074` | `1046997445071` | 2 | 65.00 |

其中第二个 SPU 的 3 个 source SKU 单价均为 29.00；第三个 SPU 的 2 个 source SKU 单价均为
65.00，所以数量加权后价格不变。

## 4. 全量分页和选择规则

本次以 `pageSize=100` 从第 1 页开始，请求到 `ceil(total/pageSize)`，并校验累计行数等于响应
`total`。原始响应只保存在仓库外的临时目录，不提交 Cookie、token 或生产业务数据。

每个 SPU 的生效候选按以下顺序产生：

1. 要求每页 `list` / `total` / `page` / `pageSize` 完整且跨页恒定；累计原始行数必须等于 `total`；
   `purchaseOrderFilterId` 的完全相同行可去重，冲突重复 ID 直接失败；
2. 仅处理唯一货源组数等于唯一 SPU 组数的采购单；
3. 对每个货源组计算数量加权采购价，价格和数量必须为有限正数；
4. 以 `(purchaseOrderSn, sourceItemId, platformItemId)` 去重；
5. 排除 `purchaseOrderStatus IN ('cancel', 'wait_pay')`；
6. 对每个 `platformItemId` 选择 `gmtPurchaseOrderStart` 最新的采购事实；
7. 最新时间并列但价格不同则跳过并报警；价格相同可确定性去重；
8. 仅对可唯一匹配 `commerce.shops` 的妙手店铺标注 `shop_pk`；商品主档不存在不阻断写入。

## 5. 2026-09-30 全量数据质量结果

| 指标 | 数值 |
| --- | ---: |
| API `total` / 实际累计采购单行 | 777 / 777 |
| 页数（`pageSize=100`） | 8 |
| 重复采购单号组 | 13 |
| 唯一组数量一致的采购单 | 769 |
| 唯一组数量不一致、跳过的采购单 | 8 |
| 去重后的 offer↔SPU 映射 | 899 |
| 排除 `cancel` / `wait_pay` 后映射 | 857 |
| 可确定最新价格的 SPU | 147 |
| 最新时间并列且价格冲突的 SPU | 0 |
| 可解析到 `commerce.products_spu` 的 SPU | 134 |
| 数据库尚无对应 SPU、未写入 | 13 |

### 5.1 生产写入结果

经用户明确授权后，通过 `POST /v2/reporting/manual-costs` 写入 134 个可解析 SPU：

- 成功 134，失败 0；
- 写后回查 134 个当前生效行全部与候选金额、`CNY` 币种一致；
- 全表不存在同一 `spu_pk` 多条 `valid_to IS NULL`；
- 原有 5 条生效人工成本已正常关闭并保留为历史行；
- 示例 SPU 的当前值为 `26.0000`、`29.0000`、`65.0000 CNY`。

以下 13 个 SPU 因 `commerce.products_spu` 尚无对应记录而未写入：

```text
1734012372389626934
1734145743638725686
1734145957060707382
1734146388974273590
1734629019886978102
1735694933227177248
1735695988596704544
1735829641193030944
1735935233060996852
1735949959049086708
1736092012165170932
1736168310682060532
1736171896698734324
```

这是一次性导入历史结论。定时清洗改为以店铺匹配为前提：只要对应妙手店铺能映射数据库店铺，
即使商品主档不存在也会写入独立采购价格表；店铺未匹配只保留为异常，不写入表。

## 6. 定时化（2026-09-30）

一次性导入已固化为 `miaoshou.purchase_price_clean`（每小时）：

1. 用 `token_service` 解密 `miaoshou_web` browser session；
2. 完整分页并校验累计数等于 `total`；
3. 变化后的原始采购单去重写 `miaoshou.purchase_order_raw_records`；
4. 复用本文 §4 的组序配对、数量加权、状态排除和最新事实规则；
5. 清洗结果写独立表 `miaoshou.purchase_prices`，以 `(miaoshou_shop_id, spu_id)` 区分；
6. 妙手店铺可唯一映射数据库店铺时写入 `shop_pk`；商品主档不存在不阻断；
7. 店铺未匹配/歧义、组数不一致、最新价冲突写 `miaoshou.sync_issues`，不猜测，也不触碰人工采购价表。

首次配置和 migration 命令见 `docs/reference/miaoshou-platform.md` §7。

## 7. 安全与复核要求

- 浏览器 Cookie、`autoLoginToken`、API key 和完整生产响应不得写入仓库或日志。
- 每次全量导入前必须重新校验 `total`、分页累计数、唯一组数量以及并列价格冲突。
- 不允许用 `opOrderPackageList[].purchaseItems[].unitPrice` 填采购价。
- 8 条唯一组数量不一致的采购单和 13 个数据库缺失 SPU 必须保留为异常，不做猜测性写入。
- 写入后按 `spu_id` 回查唯一生效行及金额，并确认旧生效行已历史化。
