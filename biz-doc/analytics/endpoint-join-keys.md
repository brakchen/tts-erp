# 端点间数据关联键

> ad_raw 表里 3 个 dump endpoint(`post_product_list` / `post_session_list` /
> `campaign_opt_log_list`)之间的关联键,以及如何 JOIN 出业务视图。

## 1. scope 维度(scope keys)

所有 dump 行都有 5 元 scope(用于唯一标识一行 + endpoint 区分):

```text
seller_id, advertiser_id, day, campaign_id, endpoint
```

这 5 个是 `ad_raw` 表的 unique 约束(`uq_analytics_raw_unit_day`),
**JOIN 两个 endpoint 的数据时,scope 必须完全对齐**。

实际数据中,1 个 shop:

```text
seller_id     = 7494763368967603447
advertiser_id = 7661087232599212040
```

跨 endpoint 唯一键计数(2026-09-04 验证):

```text
endpoint                       | rows | unique (seller, advertiser, campaign, day)
-------------------------------+------+---------------------------------------------
post_product_list              |   32 | 32
post_session_list              |   31 | 31
campaign_opt_log_list          |   ?  | ?
(差 1:Chrome 扩展刚补到一个 (campaign, day) 的 product 还没补 session)
```

## 2. post_product_list ↔ post_session_list 关联 ⭐

### 2.1 数据流

```text
[Chrome 扩展 dailySyncOnce 循环]
   1. 抓 post_product_list
      response.body.data.table[*].product_id = "17xxx..." (SPU ID)
   2. extractProductIdsFromPayloads(product_list_dumps)
      → 从所有 product_list 抓出的 product_id 集合
   3. 把这个集合作为 request.body.spu_id_list 发给 post_session_list
   4. 抓 post_session_list
      response.body.data.table[*] = 每个 session 维度的业绩
```

### 2.2 join key

| 维度 | post_product_list 字段 | post_session_list 字段 | 关系 |
| --- | --- | --- | --- |
| **(campaign, day)** | (campaign_id, day) | (campaign_id, day) | **完全相同** |
| **SPU 集合** | `response.body.data.table[*].product_id` (array) | `request.body.spu_id_list` (array) | **31/31 exact match** |
| **业绩指标** | `mixed_real_cost / onsite_roi2_shopping_*` (per SPU) | `mixed_real_cost / onsite_roi2_shopping_*` (per session) | 粒度不同,不能直接相加 |

### 2.3 31/31 exact match 验证

```text
WITH prod AS (
  SELECT campaign_id, day, array_agg(elem->>'product_id' ORDER BY elem->>'product_id') AS product_ids
  FROM analytics.ad_raw r, jsonb_array_elements(r.response->'body'->'data'->'table') elem
  WHERE r.endpoint = '/.../post_product_list' GROUP BY campaign_id, day
), sess AS (
  SELECT campaign_id, day,
         ARRAY(SELECT jsonb_array_elements_text(request->'body'->'spu_id_list')) AS spu_ids
  FROM analytics.ad_raw
  WHERE r.endpoint = '/.../post_session_list'
)
SELECT count(*) AS pairs,
       count(*) FILTER (WHERE prod.product_ids = sess.spu_ids) AS full_match
FROM prod JOIN sess USING (campaign_id, day);

-- 结果:  pairs=31  full_match=31
```

**每个 (campaign_id, day),spu_id_list 严格等于 product_id 集合(无遗漏、无多)**。
Chrome 扩展的 `extractProductIdsFromPayloads` 逻辑完全正确。

## 3. 字段对照表(两个 endpoint 的 table 元素 keys)

### 共有字段(5 个 + bid 类型)

| 字段 | 类型 | 业务含义 |
| --- | --- | --- |
| `mixed_real_cost` | string(decimal) | 真实消耗(per SPU vs per session,粒度不同!) |
| `onsite_roi2_shopping_sku` | string(int) | 出单数(per SPU vs per session) |
| `onsite_roi2_shopping_value` | string(decimal) | 出单 GMV(per SPU vs per session) |
| `onsite_mixed_real_roi2_shopping` | string(decimal) | 真实 ROI |
| `mixed_real_cost_per_onsite_roi2_shopping_sku` | string(decimal) | 单订单成本 |
| `gmv_max_bid_type` | string | GMV max bid 类型 |

### post_product_list 独有(4 个)

| 字段 | 业务含义 |
| --- | --- |
| `product_id` | SPU ID(主键) |
| `product_name` | 商品名 |
| `product_picture` | 商品主图 URL |
| `product_status` | 上架状态 |

### post_session_list 独有(2 个)

| 字段 | 业务含义 |
| --- | --- |
| `gmv_max_session_id` | **session 真实 ID**(不是 session_info.session_id) |
| `session_info` | session 元信息(嵌套对象) |

### session_info 嵌套结构

```json
{
  "budget": 500,                  ← session 日预算
  "status": 1,                    ← 1=active / 其他
  "schedule_type": 1,             ← 投放排期类型
  "start_time": "2026-07-20 15:39",
  "end_time": "2036-07-17 15:39", ← ⚠️ 部分 end_time 异常(如 2036 年,TikTok OEC bug 或默认值)
  "session_id": 0                 ← 始终是 0,不是真 session_id
}
```

**注意**:`session_info.session_id` 始终是 0,**真 session_id 是 `gmv_max_session_id` 字段**。

## 4. 实际 JOIN 模板(同 (campaign, day) 内)

```sql
-- 同一 (campaign, day) 把 product 维度和 session 维度拼起来
SELECT
  p.day,
  p.campaign_id,
  p.elem->>'product_id' AS product_id,
  p.elem->>'mixed_real_cost' AS product_cost,
  p.elem->>'onsite_roi2_shopping_sku' AS product_orders,
  s.elem->>'gmv_max_session_id' AS session_id,
  s.elem->>'mixed_real_cost' AS session_cost,
  s.elem->>'onsite_roi2_shopping_sku' AS session_orders
FROM analytics.ad_raw p,
     jsonb_array_elements(p.response->'body'->'data'->'table') p_elem
LEFT JOIN analytics.ad_raw s
  ON s.endpoint = '/.../post_session_list'
  AND s.campaign_id = p.campaign_id
  AND s.day = p.day
  AND s.request->'body'->'spu_id_list' @> to_jsonb(ARRAY[p.elem->>'product_id'])
  , LATERAL jsonb_array_elements(s.response->'body'->'data'->'table') s_elem
WHERE p.endpoint = '/.../post_product_list'
  AND p.campaign_id = '1871221880077554'
  AND p.day = '2026-09-04';
```

## 5. 已知数据特征

- **1 个 campaign 通常只挂 1 个 SPU**:`post_product_list` table 几乎所有 row 都是 1 元素数组
  (32/32 = 100% 都是 t_len=1)
- **1 个 SPU 可能挂多个 session**:`post_session_list` table 同一 (campaign, day) 可能有多个 session
  (例如 session_id 1871221880078978 出现 24 次,可能是按 bid 维度拆)
- **业绩字段粒度**:
  - product_list 的 mixed_real_cost = **该 SPU 跨所有 session 的总消耗**
  - session_list 的 mixed_real_cost = **该 session 的消耗**
  - SUM(session_list.mixed_real_cost WHERE spu_id_list @> [product_id]) = product_list.mixed_real_cost
  - (理论关系,实际数据 0 消耗无法验证)

## 6. 来源

- **ad_raw 当前数据**:`SELECT count(*) FROM analytics.ad_raw`
  - post_product_list: 32 行
  - post_session_list: 31 行
- **Chrome 扩展关联逻辑**:
  - `extractProductIdsFromPayloads`: `tiktok-endpoints.ts:116-127`
  - `createCollectionRequestBody`: `tiktok-endpoints.ts:64-83`
- **server 端存储**:
  - `ad_raw` unique: `(seller_id, advertiser_id, endpoint, day, campaign_id)`
  - `ad_records` unique: `(seller_id, advertiser_id, storage_key, campaign_id, day)`
  - 这两个 5 元组(改 endpoint ↔ storage_key 1:1) 逻辑等价
