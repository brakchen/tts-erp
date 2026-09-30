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
- 持久化：原始包裹（含商品/赠品）写 `integration.raw_records`；能按 `platformOrderSn`
  唯一解析本地销售单的包裹 upsert 到 `fulfillment.shipments`。实测
  `platformOrderItemIndex` 是平台 SKU ID，不是 TikTok `line_id`，因此商品与包裹的精确隶属关系只保留
  在 raw JSON，不猜测写入 `fulfillment.shipment_lines`。未知/歧义订单记录
  `integration.sync_issues`。
- 增量：`miaoshou.packages` 每 30 分钟运行，按 Miaoshou credential 独立使用
  `integration.sync_cursors` 保存最新 `gmtOrderModified`，下次请求回退 5 分钟重叠以避免边界漏数。
  未解析包裹保留为未解决 issue，并在后续 tick 通过详情接口重试，所以 watermark 前进不会丢失待关联包裹；
  分页未达到上游 `total` 时整次事务失败且不推进 cursor。
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
