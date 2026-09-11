# pages/spu-roi v7 重构技术方案

> **状态：决策点 D1–D8 全部拍板（2026-09-07），可以开工**。
> 注：D5（未结算退货率折算）与 D4（M13b 切 38301 全损口径）已超出 rubric v7
> 字面——rubric 需升 v8 同步（实施步骤 §8-7）。
> 口径 truth source：`handoff/spu-roi-full-loss-rubric.md`（v7，2026-09-07 用户拍板）+
> `tech-doc/analytics/spu-real-roi-dashboard.md` §4.2（M16/M18/M19 已更新至 v7）。
> 本文档 = 实现侧方案；口径本身以 rubric v7 为准，冲突时 rubric 赢。
>
> 数据可得性已实测（2026-09-07 生产库）：`finance.settlement_transactions` 605 行
> （全部带 order_pk）、其中 591 单有 SETTLEMENT 组件；白名单有效订单 630；
> `fulfillment.tracking_events action_code=38301` 966 条。v7 分层有真实数据底座。

## 1. 背景与现状差距

当前实现（`tts_erp_v2/api/v2/analytics.py::_query_spu_roi`）≈ v5 口径 + 部分 v6：

```text
net_profit = (sales − refund_net)/FX − units_sold×cost − spend − sales×0.308
```

与 v7 的四处硬差距：

| # | 现状 | v7 要求 | 影响 |
| --- | --- | --- | --- |
| G1 | 净收入 = `sales − refund_net`（订单行毛额），平台费再按**全部** sales × 30.8% 平扣 | **按订单分已结算/未结算分层**：已结算用 `SETTLEMENT` 实到账（费用已内含），未结算按 `line_gmv × (1−r̂) × (1−退款率_spu)`（D5）；**不再二次扣 fee** | 净利润从 +$2,384 翻正为 **−$2,266**（rubric v7 旧检查点，D1/D4/D5 后重定），这是本次重构的核心 |
| G2 | COGS = `units_sold × cost`，**缺 v6 的全损取消补扣** | COGS = `(units_sold + full_loss_cancelled_qty) × cost`；`full_loss_cancelled_qty` = 已到海外(38301) 且 CANCELLED 的件数 | 88 单已出海取消单的货本一直没扣 |
| G3 | `return_loss = refund_return_qty × cost`（只算完结退货 case） | 全损件数判定 = **tracking 38301** +（完结 case ∨ CANCELLED），v5→v7 沿用（127 件 vs v4 delivered_at 的 28 件） | 全损列严重低估；需新联表 `fulfillment.shipments/tracking_events` |
| G4 | `platform_fee = sales × r̂`（全量平扣，M18 输入） | M19 缩水为**信息列**：`r̂ × 未结算 sales`（已结算扣费已内含在 SETTLEMENT） | 字段语义变、不再是 M18 输入 |

## 2. 目标公式（v7 实现锚点）

```text
# 行级（per SPU，原币 VND 聚合 → 输出层一次换算 USD，§4.2 通用规则不变）
net_revenue_vnd(s) = Σ_lines  is_settled(o) ? SETTLEMENT(o) × line_gmv / order_gmv
                                             : line_gmv × (1 − r̂) × (1 − refund_rate_spu)   # D5 ✅
refund_rate_spu    = refund_net(s) / sales(s)（M12 金额口径；无退款历史/无销售 → 0；
                     防御性钳位 [0, 1]）
is_settled(o)      = EXISTS finance.settlement_components(component_code='SETTLEMENT'
                       JOIN settlement_transactions ON order_pk = o)
                     # D2 ✅：0 值落库后「有交易必有 SETTLEMENT 行」；行在 = 已结算
                     #（amount=0 = 已结算到手 0，如全额退款/取消冲正单）；无行 = 未结算

net_profit(s)      = net_revenue(s)/FX_VND − spend(s)
                     − (units_sold(s) + full_loss_cancelled_qty(s)) × unit_cost × FX_CNY
platform_fee(s)    = r̂ × unsettled_sales(s)        # M19 纯信息列，不进 net_profit
roi_real(s)        = (net_revenue − return_loss) / spend          # M14，NC′ 换基
roi_breakeven(s)   = NC′ / (NC′ − COGS_kept)                       # M17，fee 项移除（已内含 NC）
return_loss(s)     = full_loss_qty × unit_cost      # M13b：38301 全损口径
                                                     # （含 CANCELLED 已出海件）— D4 ✅ B
```

**不变量（页面红绿判据契约）**：`net_profit ≥ 0 ⇔ roi_real ≥ roi_breakeven`。
v7 下 NC′ − COGS_kept ≡ net_revenue − COGS_all 代数不变，恒等式保持。
D4 选 B 后：M13b 在 NC′ 与 COGS_kept 中等量相消 → **net_profit 不变**；
NC′ 多扣了 CANCELLED 全损件货本 → ROI 绝对值更保守（用户已接受：货拿不回来
就要在 ROI 里看见）；红绿恒等式不受影响。防御：`COGS_kept` 钳位 ≥ 0
（异常状态单的全损件可能不在 units_sold/flc 内）。

## 3. 后端改造

### 3.1 模块抽取（见 D3）

ROI 区块（常量 + 9 条 SQL + `_query_spu_roi` + handler，约 700 行）从
`tts_erp_v2/api/v2/analytics.py`（1955 行，ingest 协议与 ROI 两域混住）抽到
`tts_erp_v2/analytics/spu_roi.py`；`api/v2/analytics.py` 只留薄 handler。
路由路径、envelope 形状、鉴权（readonly）不变。
钻取面板的 detail 查询（§6）也放 `spu_roi.py`（与主表共享 CTE/常量/换算函数）。

### 3.2 SQL 层

**`_SQL_ROI_SALES` → 替换为 v7 CTE 版**（一条 SQL 出全部销售侧聚合）：

```sql
WITH order_settlement AS (
  SELECT st.order_pk, SUM(sc.amount) AS settlement_vnd
  FROM finance.settlement_transactions st
  JOIN finance.settlement_components sc
    ON sc.transaction_id = st.id AND sc.component_code = 'SETTLEMENT'
  WHERE st.order_pk IS NOT NULL
  GROUP BY st.order_pk
),
lines AS (
  SELECT sl.spu_pk, sl.order_pk, sl.quantity,
         sl.quantity * sl.unit_price AS line_gmv_vnd,
         SUM(sl.quantity * sl.unit_price) OVER (PARTITION BY sl.order_pk) AS order_gmv_vnd,
         os.settlement_vnd
  FROM commerce.sales_order_lines sl
  JOIN commerce.sales_orders so ON so.id = sl.order_pk
  LEFT JOIN order_settlement os ON os.order_pk = sl.order_pk
  WHERE sl.spu_pk IS NOT NULL
    AND so.status = ANY(CAST(:paid_statuses AS text[]))
    /* 窗口裁剪沿用 COALESCE(paid_at, order_time)，订单粒度，与现行一致 */
)
SELECT spu_pk,
       count(DISTINCT order_pk)                          AS order_count,
       sum(quantity)                                     AS units_sold,
       sum(line_gmv_vnd)                                 AS sales_vnd,
       sum(settlement_vnd * line_gmv_vnd / NULLIF(order_gmv_vnd, 0))
           FILTER (WHERE settlement_vnd IS NOT NULL)               AS settled_net_vnd,
       sum(line_gmv_vnd) FILTER (WHERE settlement_vnd IS NOT NULL) AS settled_sales_vnd,
       sum(line_gmv_vnd) FILTER (WHERE settlement_vnd IS NULL)     AS unsettled_sales_vnd,
       count(DISTINCT order_pk) FILTER (WHERE settlement_vnd IS NOT NULL) AS settled_order_count
FROM lines GROUP BY spu_pk
```

**新增 `_SQL_ROI_FULL_LOSS`**（rubric v5→v7 沿用判定 + v6 取消子集，一条出两桶）：

```sql
SELECT sl.spu_pk,
       sum(sl.quantity) AS full_loss_qty,
       sum(sl.quantity) FILTER (WHERE so.status = 'CANCELLED') AS full_loss_cancelled_qty
FROM commerce.sales_order_lines sl
JOIN commerce.sales_orders so ON so.id = sl.order_pk
WHERE sl.spu_pk IS NOT NULL
  AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
              JOIN fulfillment.tracking_events te
                ON te.shipment_id = sh.id AND te.action_code = 38301
              WHERE sh.order_pk = so.id)
  AND (so.status = 'CANCELLED'
       OR EXISTS (SELECT 1 FROM after_sales.cases c
                  WHERE c.order_pk = so.id
                    AND c.status IN ('RETURN_OR_REFUND_REQUEST_COMPLETE',
                                     'CANCELLATION_REQUEST_COMPLETE')))
  /* + 同一窗口裁剪（COALESCE(paid_at, order_time)） */
GROUP BY sl.spu_pk
```

**不动**：`_SQL_ROI_AD / _ROW_STATUS / _REFUNDS / _ORDER_SCOPE / _CATALOG /
_WINDOW / _DATA_WINDOW / _UNATTRIBUTED`。
**替换**：`_SQL_ROI_COSTS`（只读 manual_product_costs 单层）→ 完整成本链批量解析
（D1 ✅，见 §3.4）。

未结算估算在 **Python 层组合**（D5：`unsettled_sales × (1−r̂) × (1−refund_rate_spu)`）——
refund_rate 是 per-SPU 派生量（依赖_SQL_ROI_REFUNDS 结果），不进 SQL；
SQL 只出 `settled_net_vnd`（SETTLEMENT 分摊后）与 `unsettled_sales_vnd`（原始 GMV）。

**`fee_rate` 参数语义变化**：从「全量平扣费率」变为「未结算订单扣费率 r̂」，
在 Python 层只作用于未结算部分——页面覆写自动只影响未结算估算。
`FEE_RATE_BASELINE = 0.308` 沿用（dashboard D10 实测重定）。

**性能**：数据量极小（订单 ~900、结算组件 ~4.8K、tracking 966 条）；
`ix_settlement_txn_sales_order` + `uq_settlement_components_txn_code` 均在；
tracking 侧无 action_code 索引但全表数千行，不加索引。

### 3.3 Python 计算层 diff

```diff
- net_cash_vnd = sales_vnd − refund_net_vnd            # 删（M13 退役为内部参考）
+ net_revenue = settled_net + unsettled_sales × (1−r̂) × (1−refund_rate_spu)  # D5 ✅
- cogs_all = units × cost
+ cogs_all = (units + full_loss_cancelled_qty) × cost  # v6 补扣落地
- return_loss = refund_return_qty × cost
+ return_loss = full_loss_qty × cost                   # D4 ✅ B：38301 全损口径
- fee_usd = sales_usd × rate                           # 从 M18 输入删除
+ platform_fee = rate × unsettled_sales_usd            # 降为信息列
  net_profit = net_revenue_usd − cogs_all − spend      # 无 fee 项
  roi_real = (net_revenue − return_loss)/spend
  roi_breakeven 分母去掉 fee 项
```

退款列（M7–M10 `refund_*`）全部保留为**信息列**：v7 下不再进净利（已结算单的
退款调整内含于 SETTLEMENT；未结算单按基线直估不扣退款——见 D5）。

### 3.4 成本链解析（D1 ✅ 已拍板 2026-09-07）

依据 `tech-doc/procurement-source-price-lookup.md`，单位成本从单层
（manual_product_costs + K1=30 兜底）升级为完整优先链：

```text
1. MANUAL_ENTRY         procurement.manual_product_costs（spu_pk，现行已有层）
                        对外名称 = 「人工标注的采购成交价」（2026-09-07 用户拍板
                        改术语；原「人工成本」作废——它语义上就是采购成交价，
                        只是人工标注而非妙手采购单同步）
2. LATEST_PURCHASE_COST linkage.effective_product_links → purchase_order_lines
                        最新一条 unit_cost（妙手采购单成交价，updated_at 倒序）
3. SOURCE_PRICE         procurement_products.source_unit_cost（1688 挂牌价；
                        先 TK-side 行 external_product_id=spu_id 直取，
                        未命中按 source_item_id 桥公共采集箱行）
4. DEFAULT              40 CNY/件（原 K1=30 作废，用户拍板；≈ $5.95/件 @0.148823）
```

实现参考（已存在，jobs 用）：`tts_erp_v2/jobs/reporting.py::_purchase_order_lookup`
/ `_source_cost_lookup`——但它们是**按 SPU 单查**（N+1）；ROI 页面一次上百 SPU，
在 `spu_roi.py` 写 **set-based 批量版**（两条 `DISTINCT ON` SQL 一次出全量
spu_pk→(cost, currency, source) map），口径与 jobs 版 1:1（同一 SQL 语义，
只改批量形态）。

- 币种：source/purchase 成本均为 CNY（妙手采购单同步口径、1688 ¥ 挂牌价），
  无其他币种可能；换算路径统一走 `× (1 / fx.rates["CNY"])`。
- `cost_source` 枚举扩为 `MANUAL / PURCHASE / SOURCE_PRICE / DEFAULT_K1`；
  页面 ⚠ 仅对 DEFAULT_K1 行（其余三层都是真实成本来源，不标）。
- 页面/endpoint 文案：所有「默认 30 元/件 ≈ $4.43」改「默认 40 元/件 ≈ $5.95」
  （概览 tooltip、行内 warn tip、`meta.cost_assumption`）。

### 3.5 结算组件零值落库（D2 ✅ 配套改动，数据层）

- **现状**：`_write_components`（`jobs/tiktok/finance.py`）跳过
  `amount is None or amount == 0` 的行（v3 规则：防 17× 膨胀）。已实测上游
  payload 53 个字段**全部显式传输**（0 = `"0"` 字符串）——写零不是造数。
- **改动**：删 `amount == 0` 跳过（`None`/字段缺失仍跳过，防御保留）；
  零值行走既有 `on_conflict_do_update` upsert。
- **影响**：
  - 表膨胀 ~17×（~4.7K → ~32K 行，绝对量可忽略；用户拍板：不在意存储，
    数据完整优先）
  - 「无 SETTLEMENT 行」语义从此唯一 = 未结算；`SETTLEMENT=0` = 已结算
    到手 0（全额退款/取消冲正单）。is_settled 读侧判定不再有歧义（A≡B 合并）
  - 既有读查询兼容：`SUM(CASE WHEN component_code='SETTLEMENT' …)` 对 0 行
    天然正确
  - 结算 tab 明细（§6.2）能展示完整 53 字段组件拆分（含 0 行），对账更完整
- **历史回填**：`scripts/oneoff_backfill_settlement_zero_components.py`——
  遍历 `integration.raw_records`（endpoint LIKE '%statement_transactions%'）
  重放 payload，补齐零值组件行（幂等，复用同一 upsert 路径）。
- **连带更新**：`jobs/tiktok/finance.py` 头部 v3 规则注释作废说明 +
  `db/models/finance.py` 的 "One non-zero amount line" docstring 改写；
  原断言「零跳过」的 tests/jobs_tiktok 测试反转。
- ⚠️ 改了 `jobs/` → 上线时必须 `systemctl --user restart tts-erp-sync.service`。
- **执行结果（2026-09-07 已落地）**：writer 改动 merge（8f29a0a + eba20af）→
  sync-worker 重启 → 回填 **2,339 行**零值组件。验证：605/605 交易 payload
  显式携带 `settlement_amount`（127 笔显式 `"0"`）；回填后 **0 笔交易缺
  SETTLEMENT 行——「有交易必有 SETTLEMENT 行」实测成立**；回填脚本重跑
  dry-run 缺失=0（幂等）。组件表 ~4.7K → 31.5K 行。存量另有 114 笔历史零值
  SETTLEMENT 行（更早代码窗口期写入），与本次回填不冲突。陈旧性检查通过：
  0 笔「payload=0 但库存非零」。

### 3.6 币种清单与换算（dashboard D6 输入，2026-09-07 生产库实测）

所有金额**底层按原币聚合、输出层一次换算成 USD**（dashboard 决策⑥ +
§4.2 通用规则）。各数据源的存储币种：

| 数据源 | 金额字段 | 币种 | 实测证据 |
| --- | --- | --- | --- |
| 广告（`ad_daily` ∪ `ad_today` 聚合） | 广告消耗 / 平台出单 GMV | **USD** | 拍板口径（OEC 账号显示币种），无需换算 |
| 销售 `commerce.sales_order_lines` | `unit_price` | **VND** | 928/928 行全 VND |
| 订单 `commerce.sales_orders` | `total_amount` / `payment_amount` | **VND** | 888/888 行全 VND |
| 退款 `after_sales.case_lines` | `refund_amount` | **VND**（假设） | 58 行 VND + ⚠ 231/289 行 currency IS NULL——按店铺币种 VND 处理（现有代码已隐含此假设） |
| 结算 `finance.settlement_components` | `amount` | **VND** | 31,477/31,477 行全 VND |
| 结算单/打款 `finance.settlement_statements` / `payouts` | — | **VND** | 37 + 32 行全 VND |
| 人工标注的采购成交价 `procurement.manual_product_costs` | `unit_cost` | **CNY** | 7/7 行全 CNY |
| 采购成交价 `procurement.purchase_order_lines` | `unit_cost` | CNY（设计） | ⚠ **表空（0 行）**——成本链第 2 层当前空转，保留待妙手采购单流入 |
| 1688 货源价 `procurement.procurement_products` | `source_unit_cost` | **CNY**（无 currency 列，1688 ¥ 定义） | 812 行 |

换算路径：VND → USD 用 `÷ fx rates["VND"]`；CNY → USD 用
`× (1 / fx rates["CNY"])`（桥式倒数，见 `_resolve_fx_rates`）；USD 直用。
汇率源 = `fx.exchange_rate_snapshots` 在线快照，缺失时回退固定常量
（`fixed-const` 兜底，绝不让账页空白）。

## 4. 端点契约变化（`GET /v2/analytics/spu-roi`，additive 不破已有字段）

行新增字段（money-str / int，序列化规则不变：money 4 位小数、比率 2 位）：

| 字段 | 含义 | 展示 |
| --- | --- | --- |
| `full_loss_rate` | **全损退款率** = `full_loss_qty ÷ (units_sold + full_loss_cancelled_qty)`，分母 0 → null（D8 主列，可排序） | **主列** |
| `net_revenue` | Σ line_net（已结算 SETTLEMENT 分摊 + 未结算 ×(1−r̂)×(1−退货率)，D5），USD | 下钻·结算 tab 顶部 |
| `settled_sales` / `unsettled_sales` | 已/未结算 GMV 拆分，USD | 下钻·同上 |
| `settled_order_count` | 已结算订单数 | 下钻·同上 |
| `full_loss_qty` | 全损件数（38301 口径，rubric 检查点 127） | 下钻·同上 |
| `full_loss_cancelled_qty` | 其中 CANCELLED 已到海外（COGS 补扣基数） | 下钻·同上 |

语义变化（值会变，`tech-doc` §5.2 + `external-api.md` 必须同步标注）：
`net_profit` / `roi_real` / `roi_breakeven` / `platform_fee` / **`return_loss`
（M13b 切 38301 全损口径，含 CANCELLED 已出海件，D4 B）**；
`unit_cost_used` 值来源改成本链（§3.4）；`cost_source` 枚举扩为四值
（`MANUAL / PURCHASE / SOURCE_PRICE / DEFAULT_K1`，原为 MANUAL/DEFAULT_K1 两值）。

meta 变更：

- `meta.fee.note` 改 v7 文案（已结算含在 SETTLEMENT 内不再单扣；未结算按 r̂）
- 概览 10 格汇总不增新字段（coverage 取消——主列「已结算单量」+「有效出
  单量」两值自带比例语义，不需单字段展示，B3 2026-09-07 拍板删除）
- 新增 `meta.rubric_version = "v8"`（页面 stamp 可显示，口径漂移一眼定位；
  实现已含 D4/D5 超出 rubric v7 字面，rubric 升 v8 同步后填 "v8"）

排序白名单新增 `full_loss_rate`（D8 主列可排序）；其余 16 个现字段不动。

**新增端点**（钻取面板数据源，详见 §6.3；D6 已拍板 = 每 tab 懒加载）：
`GET /v2/analytics/spu-roi/{spu_pk}/{orders|settlements|cases|ads}`（readonly），
窗口参数与主表同语义（ads 无窗口——广告全窗口累计，与主表一致）。

## 5. 页面改造（`api/v2/pages.py` 模板 + `static/js/spu-roi.js`）

**主表列精简（D8 ✅ 2026-09-07 用户拍板）**：行维度只保留 6 个指标列，
其余全部移入下钻面板对应 tab 的顶部汇总区（§6.2）。

| 主表列 | 字段 | 说明 |
| --- | --- | --- |
| 商品 | `spu_id / title / main_image_url / status` | 维度列（⚠ DEFAULT_K1 标记保留在标题旁） |
| 广告消耗 | `spend` | USD，广告全窗口累计 |
| 有效GMV | `sales` | 白名单有效销售，USD |
| 有效出单量 | `order_count` | 白名单订单数 |
| 取消率 | `cancel_rate` | 取消 ÷（有效+取消） |
| 全损退款率% | `full_loss_rate`（新字段） | 全损件 ÷（售出件+全损取消件）；分母 0 → —（原值展示，不钳位——>100% 表示该 SPU 存在非常规状态单数据问题，需人工核查，B2 2026-09-07 拍板） |
| 净利润 | `net_profit` | M18，负值红字（红绿判据保留） |

- **⚙ 列开关组全部取消**（cg-adref/structure/refundsplit/cancel/fee + 原拟
  新增的 cg-settle 都不再做列）——主表不再有隐藏列。
- 可排序列头 = 6 个指标列；页面 JS 显式传 `sort=net_profit&order=asc`
  （**端点默认值 `sort="roi_real"` 保持不动**——避免改 API 契约，
  保持对外脚本兼容；下钻利润构成 tab 直接展示 ROI）。
- API 契约不变：所有字段照常在 JSON 返回（下钻各 tab 顶部汇总区直接消费
  行字段），本次是展示层精简，不动端点字段。
- 概览（10 格汇总）不动，tooltip 文案按 v7 口径更新（净利润 = 已结算
  SETTLEMENT + 未结算 ×(1−r̂)×(1−退货率) − 货本含全损取消 − 广告；
  全损货损$ = 38301 全损口径）。
- 行点击 → 行内展开钻取面板（§6，D7 ✅ 行内 accordion）；
  `settled_order_count < order_count` 的行标题旁加「含未结算，净利为估算」
  小标（复用 warn 样式 + data-tip）。
- 成本兜底文案 30 → 40：行内 ⚠ warn tip 改「无成本记录（人工标注/采购单/
  货源价均未命中），按默认 40 元/件」（D1）。

## 6. 明细钻取面板设计（P1 落地 + v7 扩展）

对应 dashboard §3.2 的 P1 规划（订单/售后/广告三 tab），按 2026-09-07 用户要求
扩展为**五 tab**（+ 利润构成、物流、结算明细）。

### 6.1 交互形式（D7 ✅ 已拍板：行内 accordion）

- 点击主表行 → **行内 accordion 展开**详情面板（在该行下方插入详情行，
  复用无框架 plain DOM；不引 bootstrap JS）。
- 同时只展开一行；再点该行 / ESC / 点另一行 → 折叠。
- **面板展开本身零请求**：利润构成 tab 直用主表行字段；其余 tab **首次激活才
  fetch 对应端点**（D6 ✅ tab 懒加载），按 `(spu_pk, tab, 窗口)` 前端缓存；
  折叠再开 / 切回已看过的 tab 不重拉。主表筛选（窗口/店铺/搜索）变化 → 清缓存。
- 行 hover 出「点击展开明细」提示；含未结算订单的行在面板顶部先出估算警示条。

### 6.2 面板五个 tab

| Tab | 内容 | 数据来源 |
| --- | --- | --- |
| **利润构成** | 该 SPU 的 v7 P&L 分解瀑布（文本表）：净收入（已结算 SETTLEMENT 分摊 / 未结算 ×(1−r̂) 两行）− 货本（售出件 + 全损取消件分列）− 广告消耗 = **净利润**；每行带口径 tooltip。这是「数字可信」的锚点——用户能逐行对账 | 主表行已有字段直出（不再请求） |
| **订单·物流** | 该 SPU 窗口内订单列表：订单号 / 状态 / 件数 / 行金额 / paid_at / **is_settled** / **已到海外(38301)✓** / 全损标记；每行可再展开 **tracking 时间线**（`tracking_events` 按事件时间排序，action_code + 描述）；CANCELLED 单标红、全损单标 ⚠ | `commerce.sales_orders/lines` + `fulfillment.shipments/tracking_events` |
| **结算** | 已结算订单的**组件拆分明细**：每单一张小表（GROSS_SALES / SELLER_DISCOUNT / PLATFORM_COMMISSION / AFFILIATE_COMMISSION / SHIPPING_FEE / ACTUAL_SHIPPING_FEE / PLATFORM_DISCOUNT / CUSTOMER_REFUND / FEE / **SETTLEMENT**），VND 原值 + USD 换算；statement 时间；**SPU 分摊比例**（line_gmv/order_gmv）；未结算订单不进明细，tab 底部一行汇总「未结算 N 单，估算净收入 $X（基线 r̂ ×(1−退货率)）」（行字段计算，用户拍板 2026-09-07） | `finance.settlement_transactions/components` |
| **售后** | case 明细（**order_id 关联**（可跳订单 tab 对号）/ 类型/状态/退款金额/原因 code+text/时间），未完结标黄 | `after_sales.cases/case_lines`（P1 原规划） |
| **广告** | campaign×SPU 行（campaign_id/消耗/出单/窗口；无名称字段——同步数据不含，已拍板不追） | `plugin.ad_daily` ∪ `ad_today`（2026-09-11 起直读；原规划的 `ad_product_links` 视图已由 migration 0020 删除） |

**tab 结构统一为「顶部指标汇总区 + 下方明细记录」**（D8：主表移出的指标
按域归位，不再做隐藏列）：

- **利润构成**：v7 P&L 瀑布 + 移入的利润域指标（实际 ROI / 保本 ROI /
  平台佣金 / 全损货损金额 / CPA / 单位成本+来源类型标签——**不下钻来源
  记录**（不要采购单号/1688 offer 链接，展示解析后的价格即可，用户拍板
  2026-09-07））
- **订单·物流**：顶部 = 订单结构指标（有效单量 / 售出件数 / 取消单量 /
  取消原额 / 销售 GMV 全单口径）
- **结算**：顶部 = 结算域指标（net_revenue / 已结算 GMV / 未结算 GMV /
  已结算单量 / 全损件数 / 全损取消件数）
- **售后**：顶部 = 退款指标（净退款额 / 退款率金额·单量两口径 /
  仅退·退货拆分 / 已付被取消信息列含 missing_lines）
- **广告**：顶部 = 广告指标（投放广告数 / 平台出单量 / 平台 GMV / ROI₀ /
  观测窗口），并明示「广告域全窗口累计，不随日期裁剪」（与主表窗口的
  差异必须可见，否则用户对不上数）

### 6.3 端点设计（D6 ✅ 已拍板：每 tab 一个懒加载端点）

```text
GET /v2/analytics/spu-roi/{spu_pk}/orders?w_start&w_end      → 订单·物流 tab
GET /v2/analytics/spu-roi/{spu_pk}/settlements?w_start&w_end → 结算 tab
GET /v2/analytics/spu-roi/{spu_pk}/cases?w_start&w_end       → 售后 tab
GET /v2/analytics/spu-roi/{spu_pk}/ads                       → 广告 tab（全窗口，无日期参数）
```

利润构成 tab **不发请求**——数据全部来自主表行已有字段（net_revenue 拆分 / COGS
/ spend / net_profit），前端直接渲染。

拍板理由（2026-09-07）：面板打开零请求；只看利润构成不拉任何数据；每端点单域
SQL 简单独立；面板一打开就全量拉四个域反而浪费。代价是多 3 个请求（可接受）。

各端点 envelope（字段语义同 §6.2 表）：

```jsonc
// GET …/orders
{ "spu_pk": 1448, "spu_id": "…",
  "window": {"w_start": null, "w_end": null},
  "orders": [{ "order_id": "…", "status": "COMPLETED", "qty": 2,
    "line_gmv": "12.3456",          // USD 换算后
    "paid_at": "…", "is_settled": true,
    "settled_net_share": "9.8765",  // SETTLEMENT × 分摊比例（未结算 → null）
    "arrived_overseas": true,       // 38301 命中
    "full_loss": false,             // 全损判定（38301 ∧ (完结case ∨ CANCELLED)）
    "shipment": {"status": "…", "tracking_number": "…"},
    "tracking": [{"action_code": 38301, "desc": "…", "event_at": "…"}]
  }],
  "meta": {"orders_truncated": false, "rubric_version": "v7", "computed_at": "…"} }

// GET …/settlements（未结算订单不出现）
{ "spu_pk": 1448,
  "settlements": [{ "order_id": "…", "statement_time": "…",
    "share_ratio": "0.62",          // 该 SPU 行占整单 GMV 比例
    "components": [{"code": "SETTLEMENT", "amount_vnd": "…", "amount": "…"}]
  }],
  "meta": {…} }

// GET …/cases
{ "spu_pk": 1448,
  "cases": [{ "case_id": "…", "order_id": "…", "type": "…", "status": "…",
    "refund_amount": "…", "reason": "…", "updated_at": "…" }],
  "meta": {…} }

// GET …/ads（无窗口参数）
{ "spu_pk": 1448,
  "ads": [{ "campaign_id": "…", "spend": "…", "orders": 12,
    "first_day": "…", "last_day": "…" }],
  "meta": {…} }
```

四端点共享约定：

- 金额序列化与主表一致（money-str 4 位小数，USD；结算组件同时给 VND 原值）。
- `orders` 上限 500 条 + `meta.orders_truncated` 防呆（单 SPU 实测最大数十单，打不到）。
- 窗口参数 `w_start/w_end` 与主表同语义（COALESCE(paid_at, order_time)）；
  tracking / settlement / cases 随订单走，不单独裁剪；`ads` 无窗口（§4.5 广告全窗口）。
- spu_pk 不存在 → 404；鉴权沿用 readonly 角色矩阵。

### 6.4 后续展示 backlog（2026-09-07 取舍完毕）

**入选**（主重构后第一批迭代，数据均现成）：

1. 退款原因聚合（cases.reason_code 堆叠 → 品控反推）
2. 秒拍秒退预警（paid_at → 取消/退款完成 < 阈值占比）
3. 退货阶段分布（发货前取消 / 发货后退款 / 妥投后退货三类量化）
4. 结算周期对账视图（按 statement 周期 × SPU 交叉表）
5. 退货运费承担（ACTUAL_RETURN_SHIPPING_FEE 入净利）——**覆盖率已验证
   （2026-09-07）**：payload 键 100% 传输（638/638 显式携带）；零值落库后
   「显式 0 = 平台未收」语义成立；实际非零仅 5 笔（合计 −90,300 VND ≈
   −$3.5，对照 26 个完结退货 case）——不是缺数，是大多退货平台没收运费。
   做进净利公式即可（影响噪声级），不必单独成列。

**放弃（2026-09-07 用户拍板）**：

- ~~利润时间趋势（VN 自然日净利/ROI 曲线）~~——不需要
- ~~campaign 名称采集~~——没有名称就算了；端点契约不预留 `campaign_name`

## 7. 测试方案（TDD）

`tests/api/test_spu_roi_api.py`（2029 行 / 28 测试）：

**改**：`test_spu_roi_math_single_spu_default_k1` 等数学断言换 v7 预期；
新增 settlement fixtures（`finance.settlement_transactions/components` 的
TEST_ 前缀行，走 `tests/conftest.py` 事务回滚隔离惯例）。

**新增用例**：

1. 已结算单行订单：net = SETTLEMENT 直取
2. 已结算多行订单：SETTLEMENT 按 line_gmv/order_gmv 比例分摊
3. 未结算订单：net = gmv × (1 − r̂)
4. 混合 SPU（已结算 + 未结算行）加总正确
5. `fee_rate` override 只影响未结算部分
6. CANCELLED + 38301 → `full_loss_cancelled_qty` 进 COGS；CANCELLED 无 38301 → 不进
7. 完结退货 case + 38301 → 进 `full_loss_qty` 但不重复进 COGS（已在 units_sold 内）
8. 防御分支：SETTLEMENT 行不存在（实测 605/605 不可达）→ 按未结算兜底
    （与 §2 行存在判定一致——SQL JOIN SETTLEMENT 行不在则 settlement_vnd NULL）
9. 整单 GMV=0 的已结算订单（`NULLIF` 兜底，net=0 不除零）
10. `net_profit ≥ 0 ⇔ roi_real ≥ roi_breakeven` 恒等式回归
11. `orders` 端点：`is_settled` / 38301 到达标志 / tracking 时间线按事件时间排序
12. `settlements` 端点：已结算订单返回组件拆分 + SPU 分摊比例（Σ share ≈ 单内占比）；
    未结算订单不出现
13. 钻取端点通用：spu_pk 不存在 → 404；窗口参数与主表一致裁剪（ads 无窗口）；
    readonly 角色矩阵沿用
14. 成本链优先级（D1）：MANUAL > PURCHASE > SOURCE_PRICE > DEFAULT(40)，
    逐层命中/穿透各一例
15. PURCHASE 层多单取最新（updated_at 倒序）；SOURCE 层直取 NULL → 走 offer 桥
16. DEFAULT_K1 行 `unit_cost_used` = 40×fx_cny_usd，`cost_source=DEFAULT_K1`
    （页面 ⚠ 断言随页面测试改）
17. D5：未结算订单净收入按 SPU 退货率折算——有退款历史 → `×(1−rate)`；
    无历史 → rate=0 不折；rate 防御钳位 [0,1]（refund_net > sales 的异常行不炸）
18. D4：M13b = `full_loss_qty × cost`（含 CANCELLED 已出海件）；net_profit 与
    旧口径代数不变（M13b 相消）；`COGS_kept` 钳位 ≥ 0；roi 红绿恒等式在 B
    口径下仍成立
19. D2/§3.5：`_write_components` 零值落库（0 写行、None 仍跳过）；
    回填脚本幂等（重跑不产生重复行）；ROI 侧：`SETTLEMENT=0` 的订单按已结算
    net=0 处理（不当未结算估算）
20. D8：主表仅渲染 6 指标列 + 商品列；列开关组移除；默认排序 = 净利润升序；
    `full_loss_rate` = 全损件 ÷（售出件+全损取消件），分母 0 → null

**验收对账**：`scripts/oneoff_roi_v7_reconcile.py` 对生产库跑出**新基线数字**
（D1/D4/D5 都改了公式，rubric v7 旧检查点（净利 −$2,266.87 / ROI 1.3094 /
保本 1.6286 / 盈利 SPU 25 / 已结算 608 单 95.4%）由这些改动失效，
不再作为 oracle）→ **先用最终公式跑出新基线 → 你确认 → 写回 rubric v8
当新检查点**。unit test 不依赖生产数据；该脚本为一次性验收，不 commit
到业务目录。

页面 shell 契约测试（`test_spu_roi_page_*`）同步更新列/hint 断言。

**收尾门槛**：`bash scripts/test.sh fast` 0 fail +
`bash prod-switch/postswitch-smoke.sh` 7 步冒烟。

## 8. 实施步骤

1. 注册 `handoff/ACTIVE.md` lane + 开 worktree `.worktrees/spu-roi-v7`
   （`feat/spu-roi-v7`；多文件改动按 AGENTS.md §11/§12）
2. **数据层先行（§3.5）**：`_write_components` 零值落库 + 测试反转 →
   回填脚本对生产库跑一把 → 重启 `tts-erp-sync.service`
   （D2 语义从这一步起生效）
   — **✅ 2026-09-07 已落地**：merge eba20af、回填 2,339 行零值组件、
   sync-worker 已重启；详细见 §3.5 执行结果。
3. TDD：先写 §7 测试（红）→ 后端模块抽取 + v7 公式 + 成本链（§3.4，D1）（绿）
4. 明细钻取四个 tab 端点（§6.3，D6 = tab 懒加载）+ 测试（§7 用例 11–13）
5. 页面模板 + JS（隐藏组 + tooltip + stamp + 钻取面板 accordion 五 tab）
6. 跑 reconcile 脚本对 rubric 检查点
7. 文档：`tech-doc/analytics/spu-real-roi-dashboard.md` §5.2/§5.3
   （**注意 §5.2 的 M18 行仍是 v5 老公式文本，本次一并改**）+ D4 成本默认
   30→40 与成本链说明 + `handoff/spu-roi-full-loss-rubric.md` **升 v8**
   （D4 全损 M13b / D5 退货率折算 / D2 零值落库写进版本表）+
   `tech-doc/external-api.md` spu-roi 主表 + 钻取四端点段 + CHANGELOG
8. `test.sh fast` 0 fail → master `git merge --no-ff` → push → 清 worktree +
   ACTIVE.md 删行

## 9. 决策点（D1–D8 全部拍板）

| # | 问题 | 选项 | 倾向 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | **成本解析链 + 默认值**：rubric 写 PROCUREMENT_CNY=40（“用户本会话指定，原默认 30”） | A. 默认仍 30；B. 默认改 40；C. 全链 + 默认 40 | **C**（按 `tech-doc/procurement-source-price-lookup.md` 全链 MANUAL→采购单成交价→1688 货源价，取不到默认 **40**；K1=30 作废；实现见 §3.4） | ✅ C（2026-09-07 用户拍板） |
| D2 | **is_settled 边界 + 零值落库** | A. 有 SETTLEMENT 行才算；B. 有交易即算；C. B + writer 零值落库 | **C**（用户拍板：0 也落库、数据完整优先、不在意 17× 膨胀；上游 53 字段全显式传输已实测；写零后 A≡B 合并——有交易必有 SETTLEMENT 行，amount=0 = 到手 0；含 writer 改动 + raw_records 回填，见 §3.5） | ✅ C（2026-09-07 用户拍板） |
| D3 | **模块抽取**：ROI 区块抽到 `tts_erp_v2/analytics/spu_roi.py`？ | A. 抽取（纯移动 + v7 一次做掉）；B. 原地改 analytics.py | **A** | ✅ A（2026-09-07 用户拍板） |
| D4 | **return_loss(M13b) 口径** | A. 保持完结退货件 + 127 另作信息列；B. M13b 切 127 全损口径 | **B**（用户拍板：发到海外被退款货就拿不回来，全损全显全算；net_profit 代数不变、红绿恒等式保持；ROI 绝对值更保守 = 已接受代价） | ✅ B（2026-09-07 用户拍板） |
| D5 | **未结算订单的退款** | A. 不扣（rubric v7 字面）；B. 扣 case 退款；C. 按 SPU 当前退货率估算 | **C**（未结算净收入 = gmv × (1−r̂) × (1−refund_rate_spu)，rate = M12 金额口径，无历史 → 0，钳位 [0,1]；注意：已偏离 rubric v7 字面，rubric 需升 v8） | ✅ C（2026-09-07 用户拍板） |
| D6 | **钻取端点形态**：单端点全 sections vs 每 tab 一个懒加载端点 | A. 单端点；B. 每 tab 一端点懒加载 | **B**（面板打开零请求、只看利润构成不拉数；每端点单域 SQL 更简单） | ✅ B（2026-09-07 用户拍板） |
| D7 | **钻取交互形式**：行内 accordion vs 右侧 drawer vs modal | A. 行内 accordion（§6.1）；B. drawer；C. modal | **A**（无框架 plain DOM 最简、与列开关 details 同哲学、上下文不丢行位置） | ✅ A（2026-09-07 用户拍板） |
| D8 | **主表列精简** | 用户指定：行维度只保留 广告消耗 / 有效GMV / 有效出单量 / 取消率 / 全损退款率% / 净利润 6 列 | 其余全部移入下钻各 tab 顶部汇总区；⚙ 列开关组取消；新增 `full_loss_rate` 字段（入排序白名单）；排序：端点默认值保持 `sort="roi_real"` 不动，页面 JS 显式传 `sort=net_profit&order=asc`（避免改 API 契约）；API 契约不变（展示层精简）；红绿判据简化为亏损 = 红字（C3 2026-09-07 拍板，不再保留 ROI<1 硬亏档） | ✅（2026-09-07 用户拍板） |

## 10. 已知偏差与后续（不阻塞本次重构）

- 30.8% 基线低估实际抽成（已结算实测 35.9%，差 5.1pp：联盟佣金/运费/平台补贴/
  退货运费）。finance 结算覆盖扩大后未结算部分自动缩窄；长期可把未结算基线
  换成实测滚动值。
- 88 单已到海外 + CANCELLED 订单的 `cases.refund_amount` 全 NULL（缺数不造数，
  暂未计入；修复路径 = TikTok OpenAPI 拉真实 cancellation 退款金额入库）。
- v7 净利润翻负（−$2,266.87）是**业务模式警报**，页面上线后需向用户明确解读：
  已结算口径下 25 个 SPU 盈利（v6 显示 46）。
