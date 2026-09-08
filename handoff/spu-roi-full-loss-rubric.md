# SPU ROI 全损退货口径(项目记忆,v8 当前版)

> **当前唯一有效版本(2026-09-07 用户拍板)**。v6(无平台抽成)、v5(action_code=38301 不补扣 CANCELLED 货本)、v4(delivered_at) 均已作废。
> 上次更新:2026-09-07(v8 = v7 + D4 全损 M13b 切 38301 口径 + D5 未结算按 SPU 退款率折算 + D2 零值落库语义锁)

## 全损退货件数判定(口径 v5 → v8 沿用)

`sales_order_lines` 满足以下**全部**条件算作"全损退货件":

1. **物流已到达海外** — 该订单的 shipment 有 tracking event:

   ```sql
   EXISTS (SELECT 1 FROM fulfillment.tracking_events te
           WHERE te.shipment_id = sh.id
             AND te.action_code = 38301)
   ```

   - `action_code = 38301` 对应描述:**"Arrived in destination country/region"**(到达目的国 = 越南海外)
   - **不要**用 `sales_orders.delivered_at`(只代表最终签收,会漏掉已到海外但被取消/拒收的单)
   - **不要**用 `shipments.delivered_at`(同上问题)

2. **发生退款或取消** — 二选一:
   - 该订单有 `after_sales.cases` 且 `status IN ('RETURN_OR_REFUND_REQUEST_COMPLETE','CANCELLATION_REQUEST_COMPLETE')`
   - 或该订单 `status = 'CANCELLED'`(防御性兜底)

**v8 关键改动(M13b 切换)**:`return_loss` 现按"全损件数 × 单位成本"计(不再用"完结退货件 × 单位成本")。
理由:发到海外的货被退款或取消 = 货拿不回来,必须全部按全损计;原"完结退货件"口径会把已出海被取消
(CANCELLED 已到海外)件的货本漏掉——典型 4 件退款单对应 127 件全损(差 4.5×),净利润翻正为翻负就是这
个差距触发的。

## v7 关键改动:净利润按"已结算 vs 未结算"分层

v6 公式隐含一个错误假设——把 `effective_gmv` 当作"净收入",**完全没扣平台抽成**(实际抽 ~35.9%)。这是 v6 显示"+盈利 $2,384"的根本原因,业务模式其实在亏。

v7 修复:**逐订单判断"已结算 vs 未结算",分别计算净收入**:

```python
line_net_vnd = is_settled
              ? order_settlement_vnd × line_gmv / order_gmv      # 已结算:按 SETTLEMENT 实际到账
              : line_gmv × (1 - 0.308)                          # v7 未结算:按 30.8% 基线估算
              # v8 新增:未结算按 SPU 退款率再折算(见 v8 改动章节)
```

### 已结算订单:SETTLEMENT 是真理

**口径链**:

```
finance.settlement_transactions
  └── order_pk → commerce.sales_orders.id
finance.settlement_components
  └── transaction_id → st.id, component_code='SETTLEMENT' → amount(卖家实际到手 VND)
```

`SETTLEMENT` 字段是**卖家实际到账金额**,已经扣完所有平台抽成 + 运费 + 联盟佣金 + 退款调整,不需要再二次扣减。

### 未结算订单(D2 + D5 配合)

**D2 零值落库(配套)**:见下文 D2 章节——"有交易必有 SETTLEMENT 行"成立,未结算订单 = 完全没有任何
settlement_transactions 记录(`SETTLEMENT=0` 也算已结算,只是到手 0)。

**D5 未结算净收入按 SPU 退款率折算(v8 新增)**:

```python
line_net_vnd = line_gmv × (1 - 0.308) × (1 - refund_rate_spu)
refund_rate_spu = refund_net(s) / sales(s)   # M12 金额口径,无历史 → 0,钳位 [0, 1]
```

理由:未结算订单的退款金额(`case.refund_amount`)在 88 单已到海外 + CANCELLED 上全 NULL
("缺数不造数"原则),与其让未结算订单的净收入始终按 0.692 估(隐藏实际退款风险),不如用 SPU 自身
历史退款率做折扣——SPU 真退货多的,未结算单估的净收入更低,数字更接近真实结算后的口径。

> v7 原写法"× 0.692 直估"作废,改为"× (1−0.308) × (1−退款率)"。
> rubric 升 v8 唯一对外含义:这条未结算折算与 D2 数据层改动同步落地。

## D2 零值落库(2026-09-07,数据层配套改动)

上游 202309 `statement_transactions` payload 的 53 个 `*_amount` 字段**全部显式传输**(`"0"` 字符串,
不是字段缺失)。v3 之前 `_write_components` 跳过了 `amount == 0` 的行(防 17× 膨胀);v8 之后写零——
理由:写零是忠实存储上游数据,0 = "该维度结算过但金额为 0"(典型如全额退款单 `settlement_amount="0"`),
与字段缺失(None)语义不同。

**核心结论:有交易必有 SETTLEMENT 行**。已实测 605 笔结算交易 / 591 单有 SETTLEMENT 组件——0 落库
后 605/605 交易全部带 SETTLEMENT 行,`SETTLEMENT=0` 的 127 笔是已结算到手 0 的全额退款/冲正单。

is_settled 判定:**存在 SETTLEMENT 组件行 = 已结算**(含 amount=0);**无 SETTLEMENT 行 = 未结算**(订单
还没进任何结算单,业务上未结算)。

落地执行结果(2026-09-07 已落地):
- `jobs/tiktok/finance.py::_write_components` 删除 `amount == 0` 跳过,保留 `None`/缺失跳过
- 回填脚本 `scripts/oneoff_backfill_settlement_zero_components.py` 补 2,339 行零值组件(幂等,重跑
  缺失=0)
- sync-worker 重启后新同步的数据走新规则
- 组件表 ~4.7K → 31.5K 行

## 订单 → SPU 行的归属

- settlement_transactions 大多 `sales_order_line_id = NULL`(整单 SETTLEMENT)
- 按订单总 GMV 比例把 SETTLEMENT 分摊到各行

## 已知偏差:30.8% 基线低估了实际抽成

v7 实测已结算订单的 `platform_fee / gross_sales` ≈ **35.9%**(差 5.1pp),差额含:

- 联盟佣金(AFFILIATE_COMMISSION)
- 平台运费(SHIPPING_FEE / ACTUAL_SHIPPING_FEE)
- 平台补贴(PLATFORM_DISCOUNT)
- 退货运费(ACTUAL_RETURN_SHIPPING_FEE)

**结论**:30.8% 是基线,实操应该用 **35~36%**;但因为分项字段复杂,目前还是按 30.8% 估算未结算订单。**等 finance 结算 job 上线后,自动分层(salary)**。

## v8 严格口径 SQL(per SPU)

```sql
-- 1. 每个订单的 SETTLEMENT 总额(VND);D2 零值落库后,任意有交易订单必有 SETTLEMENT 行
WITH order_settlement AS (
    SELECT st.order_pk,
           SUM(CASE WHEN sc.component_code='SETTLEMENT' THEN sc.amount ELSE 0 END) AS settlement_vnd
    FROM finance.settlement_transactions st
    JOIN finance.settlement_components sc ON sc.transaction_id = st.id
    WHERE st.order_pk IS NOT NULL
    GROUP BY st.order_pk
),
-- 2. 每个订单行的 line_gmv + 归属判定
order_line_net AS (
    SELECT sol.spu_pk, sol.id AS line_id, sol.order_pk,
           sol.quantity * sol.unit_price AS line_gmv_vnd,
           SUM(sol.quantity * sol.unit_price) OVER (PARTITION BY sol.order_pk) AS order_gmv_vnd,
           os.settlement_vnd,   -- NULL = 未结算
           -- SPU 维 refund_rate (v8 新增,未结算折算用)
           (SELECT NULLIF(SUM(cl.refund_amount), 0)
              FROM after_sales.case_lines cl
              JOIN commerce.sales_order_lines sl2 ON sl2.id = cl.sales_order_line_id
              WHERE sl2.spu_pk = sol.spu_pk
                AND cl.refund_amount IS NOT NULL) AS s_refund_amt,
           (SELECT NULLIF(SUM(sl2.quantity * sl2.unit_price), 0)
              FROM commerce.sales_order_lines sl2
              JOIN commerce.sales_orders so2 ON so2.id = sl2.order_pk
              WHERE sl2.spu_pk = sol.spu_pk
                AND so2.status = ANY(:paid_statuses)) AS s_sales
    FROM commerce.sales_order_lines sol
    JOIN commerce.sales_orders so ON so.id = sol.order_pk
    LEFT JOIN order_settlement os ON os.order_pk = sol.order_pk
    WHERE so.status IN ('AWAITING_SHIPMENT','PARTIAL_SHIPPING','AWAITING_COLLECTION','IN_TRANSIT','DELIVERED','COMPLETED')
),
-- 3. v8 行级 net_vnd:已结算按 SETTLEMENT 分摊,未结算按 SPU 退款率折算
order_line_with_net AS (
    SELECT spu_pk, order_pk, line_gmv_vnd, order_gmv_vnd, settlement_vnd,
           GREATEST(0, LEAST(1, COALESCE(s_refund_amt,0) / NULLIF(s_sales,0))) AS refund_rate_spu,
           CASE WHEN settlement_vnd IS NOT NULL
                THEN settlement_vnd * line_gmv_vnd / NULLIF(order_gmv_vnd, 0)
                ELSE line_gmv_vnd * 0.692 * (1 - GREATEST(0, LEAST(1, COALESCE(s_refund_amt,0) / NULLIF(s_sales,0))))
           END AS line_net_vnd
    FROM order_line_net
)
SELECT spu_pk, SUM(line_net_vnd) AS total_net_vnd
FROM order_line_with_net
GROUP BY spu_pk;

-- 4. v8 全损件数 = 38301 口径(含 CANCELLED 已出海件)
SELECT sol.spu_pk,
       SUM(sol.quantity) AS full_loss_qty,
       SUM(sol.quantity) FILTER (WHERE so.status='CANCELLED') AS full_loss_cancelled_qty
FROM commerce.sales_order_lines sol
JOIN commerce.sales_orders so ON so.id = sol.order_pk
WHERE sol.spu_pk IS NOT NULL
  AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
              JOIN fulfillment.tracking_events te
                ON te.shipment_id = sh.id AND te.action_code = 38301
              WHERE sh.order_pk = so.id)
  AND (so.status = 'CANCELLED'
       OR EXISTS (SELECT 1 FROM after_sales.cases c
                  WHERE c.order_pk = so.id
                    AND c.status IN ('RETURN_OR_REFUND_REQUEST_COMPLETE',
                                     'CANCELLATION_REQUEST_COMPLETE')))
GROUP BY sol.spu_pk;
```

## 全损件数判定沿用(v5 → v8)

`full_loss_qty` 与 `full_loss_cancelled_qty` 的 SQL 与 v5 沿用,只是 **M13b 切到 38301 全损口径**
（v7 用的是完结退货件,v8 用 full_loss_qty=127）:

```sql
-- full_loss_qty(v8 起进 M13b / return_loss)
SELECT sol.spu_pk, SUM(sol.quantity) AS full_loss_qty
FROM commerce.sales_order_lines sol
JOIN commerce.sales_orders so ON so.id = sol.order_pk
LEFT JOIN fulfillment.shipments sh ON sh.order_pk = so.id
WHERE EXISTS (SELECT 1 FROM fulfillment.tracking_events te
              WHERE te.shipment_id=sh.id AND te.action_code=38301)
  AND (EXISTS (SELECT 1 FROM after_sales.cases c
               WHERE c.order_pk=so.id
                 AND c.status IN ('RETURN_OR_REFUND_REQUEST_COMPLETE','CANCELLATION_REQUEST_COMPLETE'))
       OR so.status='CANCELLED')
GROUP BY sol.spu_pk;

-- full_loss_cancelled_qty(只统计已到海外 + CANCELLED 的件数,进 COGS 补扣)
SELECT sol.spu_pk, SUM(sol.quantity) AS full_loss_cancelled_qty
FROM commerce.sales_order_lines sol
JOIN commerce.sales_orders so ON so.id = sol.order_pk
LEFT JOIN fulfillment.shipments sh ON sh.order_pk = so.id
WHERE so.status='CANCELLED'
  AND EXISTS (SELECT 1 FROM fulfillment.tracking_events te
              WHERE te.shipment_id=sh.id AND te.action_code=38301)
GROUP BY sol.spu_pk;
```

## 退款金额口径(独立判定,不依赖物流)

`refund_usd`(从 GMV 减) = `SUM(case_lines.refund_amount)` 其中
`case_type IN ('RETURN_AND_REFUND','REFUND_ONLY')` 且 `status = 'RETURN_OR_REFUND_REQUEST_COMPLETE'`,
按 `case_lines.sales_order_line_id → sales_order_lines.spu_pk` 归集。

**已知数据缺口**:88 单已到海外 + CANCELLED 订单的 `cases.refund_amount` 全是 NULL。按
tech-doc 约定"缺数不造数",暂未计入。**修复路径**:TikTok OpenAPI 拉真实 cancellation 退款金额入库。

> **v8 注**:退款金额用于两处——(1) 已结算订单的 GMV ↔ SETTLEMENT 交叉验证(沿用 v7);
> (2) **未结算订单净收入折算**(v8 新增,见 D5 章节)。

## v8 关键参数

| 项 | 值 | 来源 |
| --- | --- | --- |
| `USD_VND` | 26001.886 | `fx.exchange_rate_snapshots` 在线快照(2026-09-07) |
| `USD_CNY` | 6.7194 → `CNY_USD=0.148823` | 同上 |
| `PROCUREMENT_CNY` | 40 CNY/件(全链兜底 DEFAULT_K1) | 用户本会话指定(原默认 30 作废) |
| `FEE_BASELINE` | **30.8%**(未结算订单基线) | tech-doc D10(2026-09-06 实测) |
| 实测平台抽成 | **35.9%**(已结算订单验证) | v7 用 SETTLEMENT 实测 |
| `cost_source` 枚举 | `MANUAL / PURCHASE / SOURCE_PRICE / DEFAULT_K1` | v8 新增四值 |

**不要用过期常量**:`26330` / `0.1477`(已过期 ~1%)。

## 关键 SQL 检查点(v7 实测 2026-09-07)

| 检查 | 结果 |
| --- | --- |
| 全部订单数 | 887 |
| 有效订单数(白名单) | 637 |
| **已结算订单数** | **608(95.4%)** |
| 未结算订单数 | 29(4.6%) |
| 已结算净收入(SETTLEMENT) | $5,572.04 |
| 未结算估算净收入(GMV × 69.2%,v7 旧口径) | $3,728.17 |
| 总净收入(v7 旧口径) | $9,300.21 |
| **实测平台抽成比例**(已结算) | **35.9%** |
| **净利润(v4 delivered_at)** | $2,938.89 |
| **净利润(v5 action_code=38301)** | $2,938.18 |
| **净利润(v6 + CANCELLED 货本)** | $2,384.56 |
| **净利润(v7 已结算 + 30.8% 估算)** | **−$2,266.87** |
| 净利润变化 v6 → v7 | **−$4,651.43**(扣平台费 35.9% ~ 30.8%) |
| 实际 ROI / 保本 ROI | 1.3094 / 1.6286 |
| 净利润率 | **−15.6%** |
| 整体状态 | ❌ **整体亏损**(业务模式警报) |
| 盈利 SPU 数 | **25**(v6 是 46) |
| SPU `1736527242804888823` 净利润 | $47(基本保本) |
| **D2 验证:有交易必有 SETTLEMENT 行** | **0 笔缺**(v7 缺 13 笔,D2 写零后补齐) |

> **v8 重要提醒**:v7 的实测数字(`净利 −$2,266.87` / `ROI 1.3094` / `保本 1.6286` / `盈利 SPU 25`)是
> **v7 公式的结果**。v8 加了 D5(未结算按 SPU 退款率折算)与 D4(M13b 切 38301 全损),实际数字会变;
> 业务模式警报(整体亏损)的结论**不变**——只是绝对数偏移。实施时先用 v8 公式在生产库跑新基线 → 用
> 户确认 → 写回本表。**不要把这些 v7 数字当 oracle**。

## 版本历史

| 版本 | 全损件数判定 | 利润公式 | 全损件数 | 净利润 | 状态 |
| --- | --- | --- | ---: | ---: | --- |
| v3 | RETURN_AND_REFUND 完结 | 旧公式 | 26 | — | ⚠️ 已作废 |
| v4 | `delivered_at IS NOT NULL` + 完结 case | `effective_qty × 40` | 28 | $2,938.89 | ⚠️ 已作废 |
| v5 | `tracking_events.action_code=38301` + 完结 case | `effective_qty × 40` | 127 | $2,938.18 | ⚠️ 已作废 |
| v6 | v5 沿用 | `(effective_qty + full_loss_cancelled_qty) × 40` | 127 | $2,384.56 | ⚠️ 已作废(没扣平台费) |
| v7 | v5/v6 沿用 | `Σ line_net_vnd` + 采购 v6 公式(line_net 按已结/未结分层,未结 × 0.692) | 127 | **−$2,266.87** | ⚠️ 已作废(未结未按退款率折算) |
| **v8** | **v5/v6/v7 沿用** | **Σ line_net_vnd + 采购 v6 公式**(line_net 按已结/未结分层,**未结 × 0.692 × (1−退款率_spu)**) | **127** | **待 reconcile 后重定** | ✅ **当前** |

v8 相对 v7 变更:
- D2 零值落库已落地:有交易必有 SETTLEMENT 行,is_settled 判定无歧义
- D5 未结算按 SPU 退款率折算:未结净收入公式 `× (1−refund_rate_spu)`(新增,refund_rate = M12)
- D4 M13b 切 38301 全损口径:return_loss = `full_loss_qty × cost`(已含 CANCELLED 已出海件)
- cost_source 枚举扩为 `MANUAL / PURCHASE / SOURCE_PRICE / DEFAULT_K1`(四值,v7 仅两值)
- PROCUREMENT_CNY 默认 40 CNY/件(v7 默认 30 作废)

## 跟文档的差异(已同步 — 见 action items)

- `tech-doc/analytics/spu-real-roi-dashboard.md`:
  - §2 全损退货口径 → 已升级 v5 物流口径
  - §4.2 M5d 新增 → v6 CANCELLED 全损件数
  - §4.2 M18 公式 → v6 加 CANCELLED 货本
  - §4.2 M18 公式 → v7 加已结算 vs 未结算分层
  - §4.2 M19 平台抽成 → v7 加 DUAL-LAYER
  - **§5.2/§5.3 → v8 实测口径(本轮升级):M18 含 D5 折算,M13b 切 38301,cost_source 四值**
- `biz-doc/analytics/post-product-list-field-semantics.md`:
  - 加一条"全损判定需联表 fulfillment.tracking_events"的说明(待办)

## action_code 速查(`fulfillment.tracking_events`)

| code | 含义 | 在 v8 中 |
| ---: | --- | --- |
| 10101 | Order placed | — |
| 20101 | Packed by seller | — |
| 30201 | Arrived sorting center (origin) | — |
| 30501 | Handed over to international carrier | — |
| 34301 | Departed country/region of origin | — |
| **38301** | **Arrived in destination country/region** | ✅ **关键:判定到海外** |
| 40101 | Arrived at last mile delivery station | — |
| 50101 | Delivered (签收) | — |
| 70201 | Returning | — |
| 80101 | Returned to seller | — |

## finance.settlement_components component_code 速查

| code | 含义 | v8 用法 |
| ---: | --- | --- |
| **SETTLEMENT** | 卖家实际到手金额 | ✅ **v7/v8 关键字段** |
| GROSS_SALES | 客户支付原价 | 参考 |
| NET_SALES | 退款后净额 | 验证 |
| PLATFORM_COMMISSION | 平台抽佣 | 验证(实测 ~15% of gross) |
| AFFILIATE_COMMISSION | 联盟佣金 | 验证 |
| SHIPPING_FEE / ACTUAL_SHIPPING_FEE | 运费 | 验证 |
| FEE | 平台总扣费(∑各项) | 估算:GMV × r̂ 时用 35.9% 实测基线 |
| CUSTOMER_REFUND | 已退款 | 验证 |
| ACTUAL_RETURN_SHIPPING_FEE | 退货运费 | v8 验证(覆盖 638/638 键,非零 5 笔 ≈ −$3.5,做进净利噪声级) |
