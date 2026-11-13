# 广告同步运维

Chrome 扩展 `ads-data-sync` 将 TikTok Seller Center 的广告分析和变更日志以单条 dump
提交给 ERP API。服务端验证请求、处理幂等并写入 PostgreSQL 的 `plugin` schema。
协议以 [dumps 数据契约](../api/dumps-data-contract.md#6-广告消耗ad-域) 为准。

## 当前接口

| 方法和路径 | 用途 |
| --- | --- |
| `GET /v2/analytics/sync/coverage` | 分页查询 `kind=daily` 的已覆盖范围，供扩展计划历史补传。 |
| `POST /v2/analytics/sync/dumps` | 接收一条 protocolVersion 4 dump。 |
| `POST /v2/analytics/sync/plugin-logs` | 接收扩展的运行日志。 |

使用 `readwrite` API key，覆盖相应的 `sellerId` 和 `advertiserId` 作用域。
HTTP 状态是处理结果。可重试错误按响应中的 `retryable` 处理，并保留 `requestId` 用于排查。

## 接入与排查

生产基址为 `https://chen.daqiangplatform.com/tts`。本地验证使用已启动的回环实例和测试用 key。

```bash
BASE_URL=http://127.0.0.1:9877
curl --fail-with-body --get "$BASE_URL/v2/analytics/sync/coverage" \
  -H "Authorization: Bearer $TTS_ERP_TEST_KEY" \
  --data-urlencode "sellerId=$TEST_SELLER_ID" \
  --data-urlencode "advertiserId=$TEST_ADVERTISER_ID" \
  --data-urlencode "endpoint=/oec_ads/shopping/v1/oec/stat/post_product_list" \
  --data-urlencode "kind=daily" \
  --data-urlencode "startDay=$TEST_START_DAY" \
  --data-urlencode "endDay=$TEST_END_DAY"
```

- coverage 以天级范围和计划为维度分页返回，按 `pagination.hasMore` 继续翻页。
- POST dump 的 JSON 结构、允许的 endpoint 和错误语义见 [广告域契约](../api/dumps-data-contract.md#6-广告消耗ad-域)。
- API 在生产机的 `tts-erp.service` 中处理写入。用请求 ID 查 `/opt/tts-erp/logs/{stdout,stderr}.log`，
  服务和日志轮转见 [部署手册](tts-erp.md#运行状态与日志)。
- 源码入口为 [路由](../../tts_erp_v2/api/v2/analytics.py) 和
  [插件广告持久化](../../tts_erp_v2/plugin/ads/repository.py)。测试通过隔离入口执行，
  可按 [coverage 用例](../../tests/api/test_analytics_coverage.py) 或
  [v4 dump 用例](../../tests/api/test_analytics_dumps_v4.py) 选测。

## 协议演进

早期版本使用 `/batches`、cursor work-list 及 `/cursor` 单日预检。当前 coverage
接管日级覆盖查询，旧端点只供历史追溯。见 [dump 架构记录](../archive/dump-architecture.md)、
[历史区间聚合](../archive/range-aggregate-history-sync.md) 和
[daily + coverage 方案](../design/daily-sync-with-coverage.md)。
