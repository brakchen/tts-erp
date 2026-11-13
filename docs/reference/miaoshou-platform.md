# 妙手开放平台

> 本文档包含 tts-erp 项目中妙手开放平台的详细信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 概述

**平台名称**：妙手开放平台（apifox 标题）

**底层 endpoint**：`openapi.wanshifu.com`

**SDK 包位置**：`miaoshou/`（`MiaoshouClient` / `MiaoshouErpClient` + `miaoshou_signing.py`）

**规模**：38 出站 endpoint + 18 回调 payload

**运行方式**：进程内被 jobs 用，**不**单独起服务

## 2. 接入方式

**人类开发者操作**：申请 licenseId + companySecret 写 .env `MIAOSHOU_*`

**测试账号**：test-user.wanshifu.com

**agent 不代办**：接入申请是人类开发者操作，agent 不代办

## 3. 调度状态

**查看方式**：`sync_worker/scheduler.py` 顶部 `NOTE`

**说明**：谁注册了 / 谁故意不注册 → 以那里为准，勿重复维护

## 4. 签名规范

**参考文档**：apifox doc-824327

**签名算法**：

```python
busData = base64(json.dumps(params, ensure_ascii=False))
sign = MD5(busData + companySecret).upper()
```

**envelope 格式**：

- licenseId
- companySecret
- sign
- busData
- timestamp（毫秒）

**锁定向量**：`tests/miaoshou/test_signing.py::test_build_sign_doc_824327_vector`

## 5. 包裹列表与详情

- Apifox：获取包裹详情 `api-457111980`；批量获取包裹列表 `api-457209915`。
- SDK：`MiaoshouErpClient.packages.get_info()` / `.search()`。
- 列表请求：`POST /open/v1/order/package/fetch/search_package_list`，必填
  `page` / `pageSize`；支持妙手创建/修改时间、订单号、店铺、平台、状态和顶层 tab 过滤。
- 详情请求：`POST /open/v1/order/package/fetch/get_package_info`，必填
  `opOrderPackageId`。
- 响应：列表为 `data.orderPackageList`，带 `total` / `page` / `pageSize`；详情为
  `data.orderPackageInfo`。两者共用订单、商品、赠品、物流和尾程物流字段结构。
- Schema 边界：妙手包裹域数据全部属于 `miaoshou` schema，不投影到
  `commerce` / `fulfillment` / 通用 `integration.raw_records`。迁移
  `0045_miaoshou_package_schema` 建立并维护：
  - `miaoshou.package_raw_records`：每次列表/详情原始 payload（不可变审计历史）；
  - `miaoshou.packages`：包裹最新归一化状态；
  - `miaoshou.package_items` / `miaoshou.package_gift_items`：普通商品与赠品；
  - `miaoshou.sync_cursors` / `miaoshou.sync_issues`：妙手自己的增量水位和数据问题。
- 商品数组是权威快照：明确返回空数组时，旧子项软标记为 `active=false` 并保留历史；字段或数组未返回时，
  不用 NULL/空值覆盖已知状态。
- 增量：`miaoshou.packages` 每 30 分钟运行，按 Miaoshou credential 独立保存最新
  `gmtOrderModified`，下次请求回退 5 分钟重叠以避免边界漏数；分页未达到上游 `total`
  时整次事务失败且不推进 cursor。
- `integration.sync_jobs` 只保留跨数据源统一的任务执行状态，不承载妙手业务 payload、cursor 或 issue。
- 单包修复：`sync_package_detail(session, op_order_package_id=...)` 调详情接口并复用相同的归一化逻辑；
  详情接口不单独定时全量调用，避免 N+1 请求。
- Apifox 同样展示可选 `timerToken` 和 `Cookie`，当前 HMAC ERP 客户端不依赖二者。

### 6.1 生产迁移一键命令

生产迁移由人工执行；脚本会先停 sync worker、备份所有受影响行、应用 0045、验证搬迁和定向清理、
立即补跑 package sync，再恢复 worker：

```bash
ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0045_miaoshou_package_schema.sh --confirm
```

备份默认写入 `$HOME/backups/tts_erp_manual/miaoshou_package_0045_*.jsonl.gz`，并生成带行数和 ID
清单的 `.meta.json`。脚本自身不会设置生产 destructive override；缺少环境变量或 `--confirm` 会拒绝执行。

## 7. 采购价同步

`miaoshou.purchase_price_clean` 每小时使用现有妙手开放平台应用凭据调用采购记录列表。
应用需具备「包裹 → 采购记录 → 采购记录列表」权限。凭据由 `token_service` 加载，
配置入口与妙手包裹同步相同。

- 分页使用 `purchaseOrderStatus=finished`，在一次同步内固定查询截止时间。校验分页的
  `data.total`（生产接口可能返回十进制字符串）和每页记录数，按响应内容去重，
  保留同一采购单号下不同的记录。
- 对采购平台为 `1688` 或 `1688妙手` 的记录，要求实付金额、采购行单价和数量均为正，
  并且单内货源标题唯一、有效关联指向同一个 TikTok SPU。同一货源的多个规格按
  `Σ(sourceUnitPrice×sourceQuantity)/Σ(sourceQuantity)` 算出 CNY 单位成本。
- 用 TikTok 订单号和 SPU 在同应用已同步的包裹中确定妙手店铺 ID，再匹配 ERP 店铺。
  多商品单、跨店、无法匹配、同时间不同价等情况写入 `miaoshou.sync_issues`。
  如果更新的采购记录无法确认价格，保留该店铺和 SPU 的已有价格。
- 原始采购记录写入 `miaoshou.purchase_order_raw_records`，结果写入
  `miaoshou.purchase_prices`。开放平台凭据接管有同等或更新证据的历史价格行，
  历史采购原文仍保留在原凭据名下。采购价同步独立于人工成本。

### 7.1 开放 API 采购价核验

- [批量获取包裹列表](https://qrzmpphmpd.apifox.cn/492780556e0.md) 的
  `data.orderPackageList[].items[].originalPrice` 是平台商品原始售价，币种随包裹的
  `orderInfo.currency`。2026-11-11 用现有签名凭据实测，TikTok 样本的订单币种为 VND，
  单件商品的 `originalPrice` 与订单的 `productAmount` 相等。这个字段不能写入 CNY 采购成本。
- [获取采购单列表](https://qrzmpphmpd.apifox.cn/492780567e0.md) 的
  `data.goodsPurchaseOrderList[].goodsPurchaseOrderSkuList[].purchasePrice` 才是该接口定义的 SKU
  采购单价。同一账号实测无筛选、1688 来源、已完成状态、已知采购单号均返回 `total=0`。
  文档也没有给出采购明细到 TikTok 店铺和 SPU 的明确关联键。
- 代发采购记录另有两个签名接口。列表（Apifox `api-523326861`）为
  `POST /open/v1/order/purchase_order/purchase_record/search_list`，详情（`api-523329034`）为
  `POST /open/v1/order/purchase_order/purchase_record/get_detail`。两者都定义了顶层采购商品的
  `purchaseItems[].sourceUnitPrice` / `sourceQuantity`，以及关联包裹的
  `platformOrderSn` / `shopName` / `purchaseItems[].platformItemId`。
- 2026-11-13 权限生效后，现有应用用签名成功查询列表和详情。按日期筛选后与 100 张历史
  采购单的 Cookie 存档逐单核对，采购行的标题、规格、单价和数量，以及关联的 TikTok 订单
  与 SPU 全部一致。180 组 `platformOrderSn` 和 `platformItemId` 均在已同步的
  `miaoshou.packages` 与 `miaoshou.package_items` 中唯一定位到店铺。
- 列表和详情实测都省略了采购状态、币种、`shopId`、`sourceItemId` 和 `sourceSkuId`。
  已取消的采购单仍会出现在无状态筛选的列表里，且可能保留实付金额和有效关联。
  一段完整日期范围内，`finished` 与 `cancel` 无共同单号，但与 `has_send`、
  `wait_send` 共享部分单号，对应响应内容也不同。无筛选结果中有同一单号对应不同采购行和
  关联包裹的情况，因此不能仅按采购单号去重或以正金额推断成交。
- 对 857 张历史采购单复核，853 张货源组数与 SPU 数相等，用货源标题在单内分组的结果
  与存档中的 `sourceItemId` 分组一致，其中 97 张含多个 SPU。这个核对覆盖历史样本，
  官方接口仍缺少货源行到 TikTok 商品行的直接关联键，当前按 §7 的条件产出价格。

## 8. 测试

```bash
# 妙手 jobs（每次使用独立 ephemeral test clone）
bash scripts/test_isolated.sh miaoshou
```

## 9. 注意事项

- **Miaoshou 已无任何 HTTP 面**：不要等它回来
- **出站代理和回调端点未挂 v2**：实测 404
- **签名调试**：`MIAOSHOU_DEBUG_SIGN=1` 在 stderr 打 canonical
