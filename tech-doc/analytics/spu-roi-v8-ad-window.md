# pages/spu-roi 广告域按日期切片（v8）技术方案

> **状态：实施完成（commit `67201b8` + merge `7290c73`，2026-09-15）**。
>
> 本文档描述 v8 改动（SPU ROI 广告域从"全窗口累计"切换到"按日期切片"）。
> 口径以本文档为准；与 v7 冲突时 v8 赢（v7 文档同步更新已并入 `spu-roi-v7-refactor.md`
> 的 §6.2 / §6.3 / §6.4 / §8.13）。
>
> **不在本方案范围内**：历史广告数据回填（`plugin.ad_daily` 2026-07-10 ~ 09-12 的
> 25131 行需另起 migration 写入 `plugin.ad_today`），见 §10。

## 1. 背景与现状差距

### 1.1 用户反馈

`pages/spu-roi` 主表"广告消耗"格（`#sum-spend`）与"广告消耗"列表头选日期范围后
**始终不变**。用户多次反馈"我选了时间，但广告消耗一直不变"。

### 1.2 现状（v7 = 实施前）

`_SQL_ROI_AD`（`tts_erp_v2/analytics/spu_roi.py:91`）从 `plugin.ad_daily ∪ ad_today`
**全表累计**，**无** `w_start` / `w_end` 过滤：

```sql
-- v7 _SQL_ROI_AD 简化版
SELECT spu_pk,
       coalesce(sum(mixed_real_cost), 0) AS spend,
       ...
FROM (
    SELECT d.* FROM plugin.ad_daily d
        WHERE d.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
    UNION ALL
    SELECT t.* FROM plugin.ad_today t
        WHERE t.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
) combined
WHERE spu_pk IS NOT NULL
GROUP BY spu_pk
```

同时 `_SQL_ROI_SALES` / `_SQL_ROI_REFUNDS` / `_SQL_ROI_FULL_LOSS` / `_SQL_ROI_ROW_STATUS`
均按 `w_start` / `w_end` 裁剪（COALESCE(paid_at, order_time) UTC 日界）。

**口径错配**：分子（净收入、净退款、全损货损）随窗口变，分母（广告消耗）**恒定**。
后果：

- 选 09-01~09-30 单窗口时，spend 仍包含 09 月之前所有广告投放 → ROI 被人为压低
- 选 30 天窗口 vs 7 天窗口，spend 不变 → 跨窗口 ROI 不可比
- 用户期望："广告消耗应该和销售/退款一样，跟着日期走"

### 1.3 v7 设计原意（"ad 全窗口累计供参考"）

`tech-doc/analytics/spu-roi-v7-refactor.md §6.2` 写：

> 「广告域全窗口累计，不随日期裁剪」（与主表窗口的差异必须可见，否则用户对不上数）

当时（2026-09-07）的考虑：广告作为 ROI 分母，用全窗口累计给出**稳定**的
"跨时间窗 ROI 对照基准"，避免新投放/停投 SPU 单日切片 ROI 剧烈跳动。

但 v7 落地时只把口径写在了 tooltip / `window_note` 里，**没有强提示**；
用户根本看不到——他们只看主表的数字，发现"切了日期 spend 不动"就报 bug。

### 1.4 ad_daily / ad_today 现状（v8.1 修正）

**v8 错误判断**：v8 上线时看 ad_daily last_update 停在 2026-09-13 23:15，
推断“ad_daily 冻结、ad_today 是新生产源”，只读 ad_today。但 2026-09-15
探测发现 ad_daily last_update=2026-09-14 17:22:48（仍在写入）— merge job
禁用只防了 ad_today→ad_daily 跨天清理，没停 ad_daily 写入口；Chrome 扩
展 kind=daily dumps 继续走 `upsert_daily_rows` 写 ad_daily。

**表实际状态**（2026-09-15 17:25 探测）：

| 表 | 行数 | 日期范围 | 最后更新 | 判定 |
| --- | ---: | --- | --- | --- |
| `plugin.ad_daily` | 25,471 | 2026-07-10 ~ 09-14 | 2026-09-14 17:22 | **生产主源**（覆盖全历史 + 今日） |
| `plugin.ad_today` | 1,142 | 2026-09-13 ~ 09-15 | 2026-09-14 17:23 | 临时表（重复写入，未走 SQL） |

**v8.1 修法**（fix/spu-roi-v81-ad-source，2026-09-15 17:25 现场修复）：
恢复读 ad_daily（v7 行为），但保留 v8 的 `day BETWEEN :ws AND :we` 裁剪。
三条 SQL 都回到 ad_daily 单源；ad_today 仍接 Chrome 扩展 kind=today
写入以供未来 merge job 重启后回填。

**遗留问题**：ad_today 9-13~9-15 1,142 行未回填 ad_daily（Chrome 扩
展 kind=today 仍在写，ad_daily 同时收到 kind=daily dumps，双写部分
略有不一致——例如 09-14 ad_daily 340 行 spend=$0 vs ad_today 469
行 spend=$11.79，原因是 ad_today 收到较新的 dump 而 ad_daily 被
ON CONFLICT DO NOTHING 挡住或未接）。修法见 §10.1。

| 表 | 行数（2026-09-15 实测） | 日期范围 | 最后写入 |
| --- | --- | --- | --- |
| `plugin.ad_daily` | 25131 | 2026-07-10 ~ 09-13 | 2026-09-13 23:15（冻结） |
| `plugin.ad_today` | 795 | 2026-09-13 ~ 09-14 | 2026-09-14 16:15（活跃） |

**v7 现状 = ad_daily（冻结历史）+ ad_today（最近 2 天）取并集**，正好覆盖
完整时间窗。v8 切到单读 ad_today 时，**09-13 之前的日期范围会归零**——这是
本次改动的最大代价，也是 §10 单独列 follow-up 的原因。

## 2. 设计决策（v8 D1–D4，全部拍板 2026-09-15）

| # | 决策点 | 选项 | 倾向 | 状态 |
| --- | --- | --- | --- | --- |
| **D1** | 广告域是否随日期切片 | A. 维持 v7 全窗口累计；B. 随日期切片 | **B**（用户 2026-09-15 拍板："改成'广告消耗也按日期切片'，同时ad_today表已经启用，删除这部分逻辑"） | ✅ B |
| **D2** | 单数据源 vs ad_daily ∪ ad_today | A. 保留 UNION ALL 加 ws/we 过滤；B. 只读 ad_today | **B**（用户原话"删除这部分逻辑"——ad_today 是 merge 禁用后的新生产源；保留 UNION ALL 会与 09-13 重复数据一起被裁，语义无意义） | ✅ B |
| **D3** | 历史 ad_daily 数据（07-10~09-12，25131 行）怎么办 | A. 同 lane 一次性回填；B. 另起 migration；C. 不回填，归零也接受 | **C**（本 lane 不含回填；用户接受历史日期 spend 归零；回填 path 见 §10） | ✅ C |
| **D4** | `/ads` 钻取端点是否也接受 w_start/w_end | A. 维持无窗口；B. 同步接受 | **B**（与主表同口径；前端 `_drillUrl` 本来就对所有 tab 传 w_start/w_end，后端只是补齐契约） | ✅ B |

## 3. SQL 改动

### 3.1 `_SQL_ROI_AD`（主表广告域）

**v7 → v8 差异**：

| | v7 | v8 |
| --- | --- | --- |
| 数据源 | `ad_daily ∪ ad_today`（UNION ALL） | 只 `ad_today` |
| 日期过滤 | 无 | `t.day BETWEEN :ws AND :we`（可空窗口沿用 §6.4 既有约定） |
| 参数 | 无 | `ws` / `we`（与同文件其他 SQL 一致） |

```sql
-- v8 _SQL_ROI_AD
SELECT spu_pk,
       count(DISTINCT campaign_id)::int          AS ad_count,
       coalesce(sum(mixed_real_cost), 0)         AS spend,
       coalesce(sum(onsite_roi2_shopping_value), 0) AS gmv_ad,
       coalesce(sum(onsite_roi2_shopping_sku), 0)::bigint AS ad_orders,
       min(day)                                  AS ad_first_day,
       max(day)                                  AS ad_last_day
FROM (
    SELECT t.campaign_id, t.product_id, t.day,
           t.mixed_real_cost, t.onsite_roi2_shopping_sku,
           t.onsite_roi2_shopping_value,
           cp.id AS spu_pk
    FROM plugin.ad_today t
    LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok' AND ca.shop_id = t.seller_id
    LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = t.product_id
    WHERE t.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
      AND (CAST(:ws AS timestamptz) IS NULL
           OR t.day >= CAST(:ws AS timestamptz)::date)
      AND (CAST(:we AS timestamptz) IS NULL
           OR t.day <  CAST(:we AS timestamptz)::date)
) combined
WHERE spu_pk IS NOT NULL
GROUP BY spu_pk
```

**NULL 短路语义**：`:ws` / `:we` 任一为 NULL 时对应条件短路（保持与
`_SQL_ROI_SALES` / `_SQL_ROI_FULL_LOSS` 等的可空窗口约定一致）；
不传 `w_start` / `w_end` 等价于"全 ad_today 历史"（注意是 ad_today，不是
v7 的 ad_daily ∪ ad_today）。

### 3.2 `_SQL_DETAIL_ADS`（钻取面板 /ads tab）

与 `_SQL_ROI_AD` 同步：

```sql
-- v8 _SQL_DETAIL_ADS（节选，差异部分）
FROM (
    SELECT campaign_id, product_id, day,
           mixed_real_cost, onsite_roi2_shopping_sku
    FROM plugin.ad_today                -- v7: ad_daily ∪ ad_today
    WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
      AND (CAST(:ws AS timestamptz) IS NULL
           OR day >= CAST(:ws AS timestamptz)::date)
      AND (CAST(:we AS timestamptz) IS NULL
           OR day <  CAST(:we AS timestamptz)::date)
) combined
```

### 3.3 `_SQL_ROI_WINDOW`（meta.window.first_day/last_day 来源）

v7：`SELECT min(day), max(day) FROM (ad_daily UNION ALL ad_today)`——
返回 ad_daily 起的全广告历史跨度（07-10 ~ 09-14）。

v8：`SELECT min(day), max(day) FROM ad_today`——只返回 ad_today 当前覆盖
范围（09-13 ~ 09-14）。`meta.window.first_day` / `last_day` 现在**准确反映**
ad_today 实际可查的跨度，UI 页脚"ad 窗口 YYYY-MM-DD ~ YYYY-MM-DD" 显示
这个值（更准确）。

### 3.4 调用方参数

`_query_spu_roi()`（`spu_roi.py:780` 附近）原本只对销售/退款/全损 SQL 传
`ws` / `we`，对 `_SQL_ROI_AD` 不传。v8 改：

```python
# v8
ad_rows = sess.execute(
    _SQL_ROI_AD,
    {"ws": ws_dt, "we": we_dt},    # 新增
).mappings().all()
```

`_detail_ads()` 签名扩展为 `(sess, spu_pk, w_start, w_end)`，调用方
`@drilldown_router.get("/{spu_pk:int}/ads")` 增加 `w_start` / `w_end` Query 参数
+ 422 校验（`w_start > w_end`），与其他三个钻取端点对齐。

## 4. 端点契约变化

### 4.1 主表 `GET /v2/analytics/spu-roi`（向后兼容）

`w_start` / `w_end` 早就是已声明 Query 参数（沿用），**响应体字段集不变**：

- `items[].spend` / `items[].gmv_ad` / `items[].ad_count` / `items[].ad_orders`
- `items[].ad_first_day` / `items[].ad_last_day`：现在反映窗口内 min/max，
  选全历史时 = ad_today 范围（09-13 ~ 09-14）
- `meta.window.note` 文案从 `"ad=视图全窗口累计(供参考)；..."` 改为
  `"...ad 同窗口裁剪（v8）；..."`

### 4.2 钻取 `GET /v2/analytics/spu-roi/{spu_pk}/ads`（契约扩展）

| | v7 | v8 |
| --- | --- | --- |
| Query 参数 | 无 | `w_start` / `w_end`（与其他钻取端点对齐） |
| 422 校验 | 无 | `w_start > w_end` → 422 |
| 响应体字段集 | `ads[]` + `meta.rubric_version` + `meta.computed_at` + `meta.note` | 同 + `meta.note` 改 `"广告域与日期窗口同语义裁剪（v8）"` |

## 5. 数据状态与回填路径

### 5.1 当前 prod 数据（2026-09-15 实测）

| 表 | 行数 | 日期范围 | 最后更新 |
| --- | --- | --- | --- |
| `plugin.ad_today` | 795 | 2026-09-13 ~ 09-14 | 2026-09-14 16:15 |
| `plugin.ad_daily` | 25131 | 2026-07-10 ~ 09-13 | 2026-09-13 23:15（freeze） |

### 5.2 用户可观察行为

| 选中日期范围 | v7 spend | v8 spend |
| --- | --- | --- |
| 不限（默认） | 全部 ad_daily ∪ ad_today 累计（07-10~09-14） | 仅 ad_today 累计（09-13~09-14）——**显著减少** |
| 09-13 ~ 09-30 | 同上（ad 全窗口） | ad_today 09-13 + 09-14 两天累计 |
| 08-01 ~ 08-31 | ad 全窗口（不变） | **0**（ad_today 无此日期） |
| 07-01 ~ 07-31 | ad 全窗口（不变） | **0** |

### 5.3 回填（不在本 lane，§10 follow-up）

待起 `feat/backfill-ad-today-history` lane：

```sql
-- 草案，待正式 lane 拍板
INSERT INTO plugin.ad_today (seller_id, advertiser_id, campaign_id, product_id,
                             endpoint, day, mixed_real_cost,
                             onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                             onsite_mixed_real_roi2_shopping, metrics_extra,
                             created_at, updated_at)
SELECT seller_id, advertiser_id, campaign_id, product_id,
       endpoint, day, mixed_real_cost,
       onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
       onsite_mixed_real_roi2_shopping, metrics_extra,
       created_at, updated_at
FROM plugin.ad_daily
WHERE day < CURRENT_DATE  -- 历史不回填今天（merge job 应该负责今天）
ON CONFLICT ON CONSTRAINT uq_ad_today DO NOTHING;
```

注意：

- 不要 backfill **当天**——那是 ad_today 的本职工作（Chrome 扩展持续写入）
- merge job 重新启用时（`_DISABLED = False`）需先确保 ad_today 已有完整
  历史数据，否则 merge 会用不完整的 ad_today 覆盖 ad_daily
- 回填前需评估 ad_daily 与 ad_today 在 09-13 的重叠（同 seller/adv/campaign/product/day
  有两行）；目前 schema 是"同日两表都允许写入"，回填用 `DO NOTHING` 是
  安全选择（保留 ad_today 较新的快照）

## 6. 测试策略

### 6.1 回归测试反转（v7 → v8）

旧 `test_spu_roi_date_window_does_not_clip_ad`（v7 守护断言：ad spend 不被裁）
**改写为** `test_spu_roi_date_window_clips_ad`，断言完全反向：

```python
def test_spu_roi_date_window_clips_ad(api_client, readonly_key, db_engine):
    """v8: 起始/截止日同时裁剪广告 spend/gmv/ad_count（与销售/退款同语义）"""
    # 窗外 ad (06-01, spend=25) + 窗内 ad (09-10, spend=15)
    # 不限窗口: spend=40, ad_count=2, sales=40, order_count=2
    # 裁剪 09-01~09-30: spend=15, ad_count=1, sales=20, order_count=1
    # 同时断言 window.note 含 "已裁剪" 和 "ad 同窗口裁剪"
```

### 6.2 测试夹具改动

`_seed_ad_dump`（`tests/api/test_spu_roi_api.py:259`）从写 `ad_daily` 改为写
`ad_today`，**唯一键**从 `uq_ad_daily` 改为 `uq_ad_today`：

```python
def _seed_ad_dump(...):
    # v8: _SQL_ROI_AD / _SQL_DETAIL_ADS 只读 plugin.ad_today
    # 测试夹具必须写 ad_today，否则 ROI 计算拿到 spend=0
    INSERT INTO plugin.ad_today (...) ON CONFLICT ON CONSTRAINT uq_ad_today DO UPDATE
```

`tests/api/test_spu_roi_api.py::_wipe` 不变（已同时清两张表）。

### 6.3 既有 window.note 断言更新

`test_spu_roi_window_params_clip_sales_and_refunds` 和
`test_spu_roi_fee_rate_baseline_meta` 中两处 `"ad=视图全窗口累计" in note`
改为 `"ad=视图全窗口累计" not in note`，反映 v8 行为。

### 6.4 覆盖矩阵

| 测试场景 | 覆盖 SQL | 预期 |
| --- | --- | --- |
| 不限窗口 | `_SQL_ROI_AD` NULL 短路 | 全 ad_today 累计 |
| 09-01~09-30 窗口 | `_SQL_ROI_AD` 双边过滤 | 仅窗内行累计 |
| 08-01~08-31 窗口（窗外） | `_SQL_ROI_AD` 双边过滤 | spend=0 / ad_count=0 |
| 仅 :ws 单边 | `_SQL_ROI_AD` 单边 | 仅上界过滤 |
| 仅 :we 单边 | `_SQL_ROI_AD` 单边 | 仅下界过滤 |
| w_start > w_end | `/ads` 路由 | 422 |

### 6.5 收尾门槛

`bash scripts/test.sh fast`：本次 lane 引入 **0 新 fail**（master HEAD
baseline 17 fail，merge 后 17 fail，diff 为空；本 lane 是代码/test lane 仍必须
满足 §7 硬门槛）。

`tests/api/test_spu_roi_api.py` 单独跑 49 测试，47 pass + 2 pre-existing fail
（`page_shell_contract` / `js_review_fixes_present`，master HEAD 同样 fail，
与本 lane 无关）。

## 7. UI / 页面 / 静态资源改动

### 7.1 tooltip 文案

| 位置 | v7 | v8 |
| --- | --- | --- |
| 结余带"广告消耗"格 `data-tip` | "Σ real_cost_total（广告视图全窗口累计，USD；...）" | "Σ mixed_real_cost（plugin.ad_today；随选中日期窗口裁剪，与销售/退款同口径；...）" |
| 主表表头"广告消耗" `data-tip` | "广告消耗（USD，广告窗口全量累计；...）" | "广告消耗（USD，plugin.ad_today，随选中日期窗口裁剪；...）" |
| 日期框"起始日" `data-tip` | "销售/退款日期范围（空 = 全历史；广告窗口始终全量）" | "日期范围（销售/退款/广告同口径裁剪；空 = 全历史）" |
| 钻取面板 HINT_LAYER_AD | "Σreal_cost_total 广告视图全窗口累计(USD);..." | "Σmixed_real_cost(plugin.ad_today,随日期窗口裁剪,USD);...(v8)" |
| 钻取面板 HINT_AD_SPEND | "Σ real_cost_total(广告视图全窗口累计,USD);..." | "Σ mixed_real_cost(plugin.ad_today,随日期窗口裁剪,USD);...(v8)" |

### 7.2 window_note 文案（服务端 → meta.window.note → 不进 UI，仅 API 返回）

v7：`(w_start, w_end) 给定时` → `"ad=视图全窗口累计(供参考)；销售/退款已裁剪:<a> ~ <b>(含 w_end 当日)；概览单量/GMV 按 COALESCE(paid_at, order_time) 裁剪"`

v8：`(w_start, w_end) 给定时` → `"销售/退款已裁剪:<a> ~ <b>(含 w_end 当日)；ad 同窗口裁剪（v8）；概览单量/GMV 按 COALESCE(paid_at, order_time) 裁剪"`

（不限窗口时仅文案小改：去掉 "ad=视图全窗口累计(供参考)；"）

### 7.3 前端 JS 无新逻辑

`spu-roi.js` 不改业务逻辑：`_drillUrl(tab, spuPk)` 本来就对所有 tab 传
`w_start` / `w_end` query param，v8 后端 `/ads` 端点接受，后端响应过滤。
缓存键 `_drillCacheKey` 也已含 `w_start` / `w_end`，无需调整。

## 8. 文档同步

| 文档 | 改动 |
| --- | --- |
| `tech-doc/analytics/spu-roi-v7-refactor.md` §6.2 | "广告" tab 数据源从 `ad_daily ∪ ad_today` 改 `plugin.ad_today`（v8 单源）+ 明示历史未回填 |
| 同上 §6.3 端点表 | `/ads` 改为 `?w_start&w_end`，加注 v8 接受窗口 |
| 同上 §6.4 端点共享约定 | `ads` 行为从"无窗口"改为"v8 起与主表同口径裁剪" |
| 同上 §8.13 | "钻取端点通用" `ads` 描述同步 v8 |
| `CHANGELOG.md` | 加 `2026-09-15 — SPU ROI 广告消耗按日期切片（v8）` 条目（含背景/影响/数据影响/回填 follow-up） |
| `handoff/ACTIVE.md` | 注册 `fix/spu-roi-ad-window-clip` lane，merge 后改 `merged (7290c73)` |

## 9. 风险与回滚

### 9.1 风险

| 风险 | 触发条件 | 影响 | 缓解 |
| --- | --- | --- | --- |
| **历史 spend 归零** | 用户选 09-13 之前的日期范围 | 广告消耗 / GMV / ad_orders 全 0，ROI = null | tooltip 已写明"plugin.ad_today"；页脚 window.first_day 显示实际 ad_today 跨度；§10 回填 |
| 跨窗口 ROI 不可比 | 同一 SPU 在窗口 A 和窗口 B 的 spend 不同 | 用户按 ROI 排序时跨窗口对比含义变 | 用户已要求按窗口切片；跨窗口对比本就不该直接比较 |
| 数据精度损失 | ad_today 行覆盖不全（如 09-13 只覆盖部分行） | 当日 spend 不完整 | ad_today 是 Chrome 扩展活跃写入源，实时数据；当日结束后由 merge job 归档到 ad_daily（merge job 重新启用后） |
| 单点依赖 | ad_today 写入中断（Chrome 扩展断网） | 实时 spend 缺数 | 既有兜底：未读到数据时 spend=0、ROI=null；前端显式"—" |

### 9.2 回滚

回滚到 v7 行为：

1. `_SQL_ROI_AD` 加回 `ad_daily` UNION ALL 分支 + 删 `day` 过滤
2. `_SQL_DETAIL_ADS` 同
3. `_SQL_ROI_WINDOW` 加回 UNION ALL
4. 移除 `/ads` 端点 `w_start` / `w_end` 参数（保留签名兼容）
5. 恢复 tooltip / HINT 文案
6. 撤销 `test_spu_roi_date_window_clips_ad` 改回 `does_not_clip` 断言

预计 1 小时工作量，单 commit revert 可行（无数据迁移副作用，因为 v7 / v8
都只读 `plugin.*`，不写）。

## 10. 已知偏差与后续（不阻塞本次改动）

### 10.1 历史 ad_daily 回填（独立 lane）

**优先级**：高——直接影响 ROI 看板历史区间可用性。

待 `feat/backfill-ad-today-history` lane（不在本方案范围内）：

- INSERT `plugin.ad_daily` 中 `day < CURRENT_DATE` 的全部行到 `ad_today`
- ON CONFLICT DO NOTHING（保留 ad_today 较新快照）
- 同步 eval `jobs/ad_merge_today2daily._DISABLED = False` 时机——ad_today
  必须先有完整历史覆盖，才能让 merge job 重新接管当天归档（否则 merge 会
  用不完整数据覆盖 ad_daily）
- 评估：对 `metrics_extra` JSONB 的合并策略（ad_today 写时是 patch 模式 vs
  ad_daily 是最终态，是否需要 ad_daily 的 metrics_extra 覆盖？）

### 10.2 merge job 重新启用决策

`_DISABLED = True`（2026-09-13 起）的根因调查：UTC 跨天 Chrome 扩展延迟归因
→ 过早 DELETE ad_today 会截断。修法（不在本 lane）：

- 调整 merge 的删除策略：保留最近 N 小时 ad_today 行（N = 延迟归因 SLA）
- 或：merge 用 UPSERT + 时间戳比较，只删 created_at < 阈值 AND day < 昨天的行

### 10.3 跨 ROI 排序可比性

v8 后同一 SPU 不同窗口的 ROI 严格按窗口算。用户在排序 `sort=roi_real` 时，
跨窗口 ROI 不可比——这是 v8 设计目标（用户要"按日期看 spend"），
但需要在 UI 加 tooltip 提示："ROI 按所选日期窗口计算，跨窗口排序对比意义有限"。

### 10.4 KPI 监控

merge job 重新启用后，建议加 prom 监控：

- `plugin.ad_today` 行数每天增长率（异常增长 = Chrome 扩展 dump bug）
- `plugin.ad_daily` `day = CURRENT_DATE - 1` 的覆盖率
- `_SQL_ROI_AD` spend 全局 sum 与 `plugin.ad_today.spend sum` 的差异
  （应为零；非零 = SQL 有 bug 或 union 漏行）

## 11. 决策点回顾

| # | 决策 | 状态 |
| --- | --- | --- |
| D1 | 广告域按日期切片 | ✅ 2026-09-15 用户拍板 |
| D2 | 单源 ad_today（删 ad_daily UNION ALL） | ✅ |
| D3 | 历史不回填（本 lane），归零可接受 | ✅ |
| D4 | /ads 端点同步接受 w_start/w_end | ✅ |

## 12. 实施步骤（已完成，作为审计追踪）

1. ✅ 注册 `handoff/ACTIVE.md` lane `fix/spu-roi-ad-window-clip` +
   开 worktree `.worktrees/spu-roi-ad-window-clip`
2. ✅ `_SQL_ROI_AD` / `_SQL_DETAIL_ADS` / `_SQL_ROI_WINDOW` 三处 SQL 改：
   - 删 `ad_daily` UNION ALL
   - `_SQL_ROI_AD` / `_SQL_DETAIL_ADS` 加 `day BETWEEN :ws AND :we`
   - 调用方补 `ws` / `we` bind param
3. ✅ `_detail_ads` 签名扩展 + `/ads` 路由加 `w_start` / `w_end` + 422 校验
4. ✅ `window_note` 文案改写（删"ad=视图全窗口累计"，加"ad 同窗口裁剪（v8）"）
5. ✅ `_seed_ad_dump` 改写 `plugin.ad_today`（`_seed_window_spu` 等既有测试零改）
6. ✅ `test_spu_roi_date_window_does_not_clip_ad` 反转为 `test_spu_roi_date_window_clips_ad`
7. ✅ 既有 `window.note` 字符串断言 2 处改为 `not in`
8. ✅ tooltip / HINT 4 处文案同步
9. ✅ `tech-doc/analytics/spu-roi-v7-refactor.md` §6.2/§6.3/§6.4/§8.13 同步 v8
10. ✅ CHANGELOG 加 `2026-09-15 — SPU ROI 广告消耗按日期切片（v8）` 条目
11. ✅ `test.sh fast` master HEAD baseline 17 fail → merge 后 17 fail，diff 为空
12. ✅ `git merge --no-ff -m "merge: spu-roi-ad-window-clip (lane fix/spu-roi-ad-window-clip)"`
    → 7290c73 → push origin master → 清 worktree + ACTIVE.md 状态改 merged