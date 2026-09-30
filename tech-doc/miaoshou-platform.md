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

## 5. EWM 采购单列表

- Apifox：api-479599781（发布状态）。
- 请求：`POST /open/v1/ewm/goods_purchase_order/goods_purchase_order/fetch/search_goods_purchase_order_page`。
- 请求体：`page`（从 1 开始）和 `pageSize`（10–100）；同步任务使用 100。
- 响应：`data.goodsPurchaseOrderList`，行项目为
  `goodsPurchaseOrderSkuList`，并以 `data.total` 记录总条数。
- 调度：`miaoshou.purchase_orders` 每小时执行一次。
- Apifox 同时列出 `timerToken` 查询参数和 `Cookie` 头，但未标为必填；
  当前同步沿用 ERP HMAC 认证（`x-app-key` / `x-timestamp` / `x-sign`）。

## 6. 包裹列表与详情

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

## 7. 测试

```bash
# 妙手 jobs（测试包装器会强制使用 tts_erp_v3_test）
bash scripts/test.sh miaoshou
```

## 8. 注意事项

- **Miaoshou 已无任何 HTTP 面**：不要等它回来
- **出站代理和回调端点未挂 v2**：实测 404
- **签名调试**：`MIAOSHOU_DEBUG_SIGN=1` 在 stderr 打 canonical
