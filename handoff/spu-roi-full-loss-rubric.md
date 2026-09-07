# SPU ROI 全损退货口径(项目记忆,v7 当前版)

> **当前唯一有效版本(2026-09-07 用户拍板)**。v6(无平台抽成)、v5(action_code=38301 不补扣 CANCELLED 货本)、v4(delivered_at) 均已作废。
> 上次更新:2026-09-07(会话内多次迭代 → v7 收尾)

## 全损退货件数判定(口径 v5 → v6 → v7 沿用)

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

## v7 关键改动:净利润按"已结算 vs 未结算"分层

v6 公式隐含一个错误假设——把 `effective_gmv` 当作"净收入",**完全没扣平台抽成**(实际抽 ~35.9%)。这是 v6 显示"+盈利 $2,384"的根本原因,业务模式其实在亏。

v7 修复:**逐订单判断"已结算 vs 未结算",分别计算净收入**:

```python
line_net_vnd = is_settled
              ? order_settlement_vnd × line_gmv / order_gmv      # 已结算:按 SETTLEMENT 实际到账
              : line_gmv × (1 - 0.308)                          # 未结算:按 30.8% 基线估算

profit_usd = (Σ line_net_vnd) / USD_VND            # 总净收入
           − ad_cost_usd                              # 广告
           − procurement_cny_per_unit                  # 采购(v6 沿用)
              * CNY_USD
              * (effective_qty + full_loss_cancelled_qty)
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

### 未结算订单:按 30.8% 平台抽成基线估算

无 `settlement_transactions` 记录的订单,假设平台抽成 = `effective_gmv × 30.8%`,剩 69.2% 归卖家。

**基线来源**:`tech-doc/analytics/spu-real-roi-dashboard.md` §4.2 D10(2026-09-06 实测 30.8%,用户本会话确认沿用)。

### 订单 → SPU 行的归属

- settlement_transactions 大多 `sales_order_line_id = NULL`(整单 SETTLEMENT)
- 按订单总 GMV 比例把 SETTLEMENT 分摊到各行

### 已知偏差:30.8% 基线低估了实际抽成

v7 实测已结算订单的 `platform_fee / gross_sales` ≈ **35.9%**(差 5.1pp),差额含:

- 联盟佣金(AFFILIATE_COMMISSION)
- 平台运费(SHIPPING_FEE / ACTUAL_SHIPPING_FEE)
- 平台补贴(PLATFORM_DISCOUNT)
- 退货运费(ACTUAL_RETURN_SHIPPING_FEE)

**结论**:30.8% 是基线,实操应该用 **35~36%**;但因为分项字段复杂,目前还是按 30.8% 估算未结算订单。**等 finance 结算 job 上线后,自动分层(salary)**。

## v7 严格口径 SQL(per SPU)

```sql
-- 1. 每个订单的 SETTLEMENT 总额(VND)
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
           CASE WHEN os.settlement_vnd IS NOT NULL
                THEN os.settlement_vnd * (sol.quantity * sol.unit_price) / NULLIF(SUM(sol.quantity * sol.unit_price) OVER (PARTITION BY sol.order_pk), 0)
                ELSE (sol.quantity * sol.unit_price) * 0.692
           END AS line_net_vnd
    FROM commerce.sales_order_lines sol
    JOIN commerce.sales_orders so ON so.id = sol.order_pk
    LEFT JOIN order_settlement os ON os.order_pk = sol.order_pk
    WHERE so.status IN ('AWAITING_SHIPMENT','PARTIAL_SHIPPING','AWAITING_COLLECTION','IN_TRANSIT','DELIVERED','COMPLETED')
)
SELECT spu_pk, SUM(line_net_vnd) AS total_net_vnd,
       SUM(line_net_vnd) FILTER (WHERE line_net_vnd > 0) AS ...   -- 可继续分已结算/未结算
FROM order_line_net
GROUP BY spu_pk;

-- 3. 采购(全损口径沿用 v6)
SELECT sol.spu_pk,
       SUM(sol.quantity) FILTER (
         WHERE EXISTS (SELECT 1 FROM fulfillment.tracking_events te
                       JOIN fulfillment.shipments sh ON sh.id=te.shipment_id
                       WHERE sh.order_pk=sol.order_pk AND te.action_code=38301)
       ) AS full_loss_cancelled_qty
FROM commerce.sales_order_lines sol
JOIN commerce.sales_orders so ON so.id = sol.order_pk
WHERE so.status='CANCELLED'
GROUP BY sol.spu_pk;
```

## 全损件数判定沿用(v5 → v6, v7 不变)

```sql
-- full_loss_qty:全部全损件数
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

-- full_loss_cancelled_qty:v6 新增,只统计已到海外 + CANCELLED 的件数
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

> **v7 注**:退款金额**仅用于已结算订单的 GMV ↔ SETTLEMENT 交叉验证**,未结算订单用基线 30.8% 直接估算,不依赖 case.refund_amount。

## v7 关键参数

| 项 | 值 | 来源 |
| --- | --- | --- |
| `USD_VND` | 26001.886 | `fx.exchange_rate_snapshots` 在线快照(2026-09-07) |
| `USD_CNY` | 6.7194 → `CNY_USD=0.148823` | 同上 |
| `PROCUREMENT_CNY` | 40 CNY/件 | 用户本会话指定(原默认 30) |
| `FEE_BASELINE` | **30.8%**(未结算订单基线) | tech-doc D10(2026-09-06 实测) |
| 实测平台抽成 | **35.9%**(已结算订单验证) | v7 用 SETTLEMENT 实测 |

**不要用过期常量**:`26330` / `0.1477`(已过期 ~1%)。

## 关键 SQL 检查点(实测 2026-09-07)

| 检查 | 结果 |
| --- | --- |
| 全部订单数 | 887 |
| 有效订单数(白名单) | 637 |
| **已结算订单数** | **608(95.4%)** |
| 未结算订单数 | 29(4.6%) |
| 已结算净收入(SETTLEMENT) | $5,572.04 |
| 未结算估算净收入(GMV × 69.2%) | $3,728.17 |
| 总净收入 | $9,300.21 |
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

## 版本历史

| 版本 | 全损件数判定 | 利润公式 | 全损件数 | 净利润 | 状态 |
| --- | --- | --- | ---: | ---: | --- |
| v3 | RETURN_AND_REFUND 完结 | 旧公式 | 26 | — | ⚠️ 已作废 |
| v4 | `delivered_at IS NOT NULL` + 完结 case | `effective_qty × 40` | 28 | $2,938.89 | ⚠️ 已作废 |
| v5 | `tracking_events.action_code=38301` + 完结 case | `effective_qty × 40` | 127 | $2,938.18 | ⚠️ 已作废 |
| v6 | v5 沿用 | `(effective_qty + full_loss_cancelled_qty) × 40` | 127 | $2,384.56 | ⚠️ 已作废(没扣平台费) |
| **v7** | **v5/v6 沿用** | **`Σ line_net_vnd` + 采购 v6 公式**(line_net 按已结/未结分层) | **127** | **−$2,266.87** | ✅ **当前** |

## 跟文档的差异(已同步 — 见 action items)

- `tech-doc/analytics/spu-real-roi-dashboard.md`:
  - §2 全损退货口径 → 已升级 v5 物流口径
  - §4.2 M5d 新增 → v6 CANCELLED 全损件数
  - §4.2 M18 公式 → v6 加 CANCELLED 货本
  - **§4.2 M18 公式 → v7 加已结算 vs 未结算分层**(本轮新增)
  - **§4.2 M19 平台抽成 → v7 加 DUAL-LAYER**(本轮新增)
- `biz-doc/analytics/post-product-list-field-semantics.md`:
  - 加一条"全损判定需联表 fulfillment.tracking_events"的说明(待办)

## action_code 速查(`fulfillment.tracking_events`)

| code | 含义 | 在 v7 中 |
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

| code | 含义 | v7 用法 |
| ---: | --- | --- |
| **SETTLEMENT** | 卖家实际到手金额 | ✅ **v7 关键字段** |
| GROSS_SALES | 客户支付原价 | 参考 |
| NET_SALES | 退款后净额 | 验证 |
| PLATFORM_COMMISSION | 平台抽佣 | 验证(实测 ~15% of gross) |
| AFFILIATE_COMMISSION | 联盟佣金 | 验证 |
| SHIPPING_FEE / ACTUAL_SHIPPING_FEE | 运费 | 验证 |
| FEE | 平台总扣费(∑各项) | 估算:GMV × r̂ 时用 35.9% 实测基线 |
| CUSTOMER_REFUND | 已退款 | 验证 |
