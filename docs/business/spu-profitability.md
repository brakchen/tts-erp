# SPU 盈利计算开发契约

> **业务口径权威来源**：飞书《SPU-ROI 计算口径》
> <https://kcn8bg11alpa.feishu.cn/wiki/SNeXwerJHiiNURkCmZwceF7XnSd>
> 本文最近一次对齐飞书文档 revision `246`（2026-10-05）。
>
> **本文职责**：把飞书定义的概念、口径和高层公式展开成可直接指导开发、测试和审查的详细契约，包括时间边界、状态判定、数据库字段、去重、币种、精度、空值、接口范围与兼容要求。本文不重新拥有或改写业务概念；与飞书冲突时以飞书为准，并应先修正文档差异，再改代码。
>
> **目标状态**：当前指标与预测指标分离。本文描述已确认的目标口径；现有实现尚未完全符合的地方集中列在 §11，不能把旧代码行为反向解释为业务口径。

---

## 1. 责任边界与强制不变量

### 1.1 文档分工

| 层级 | 权威内容 | 不应承载 |
| --- | --- | --- |
| 飞书《SPU-ROI 计算口径》 | 业务概念、指标含义、高层公式、产品行为 | SQL、表字段、wire 精度、兼容路径 |
| 本文 | 开发细则、时间算法、状态映射、数据源、聚合、精度、验收约束 | 与飞书竞争的另一套业务定义 |
| `docs/design/spu-profitability-technical-design.md` | deep module 接口、依赖方向、一致性快照 | 重复公式 |
| `docs/api/external-api.md` | HTTP 参数、响应字段和稳定性 | 独立发明盈利口径 |
| `docs/archive/` | 历史决策和排障证据 | 现行计算依据 |

发现差异时必须按以下顺序处理：

1. 确认飞书当前版本和用户决定；
2. 更新本文的开发契约；
3. 更新接口文档、实现和测试；
4. 不得为了保持旧实现而在本文增加模糊回退或双重口径。

### 1.2 计算不变量

- 当前指标和预测指标是两套时间模型，不共用页面经营日期作为预测样本窗口。
- 当前指标跟随店铺、已应用的 SPU 范围和页面经营日期；预测指标跟随店铺和已应用的 SPU 范围，但忽略页面经营日期。
- 预测只属于大盘 `totals`；单个 SPU 行没有产品意义上的预测内容。
- 订单级数量在大盘范围内按 `order_pk` 全局去重，不能把 SPU 行订单数相加。
- 金额和件数按入选商品行聚合；同一订单含多个入选 SPU 时，订单只计一次，商品行金额各计一次。
- 当前指标只使用截至计算时点已经确认的退款和全损事实；未来损失只进入预测指标。
- 已结算实际到账不得再次扣平台费或预测损失；已经确认的退款不得重复扣除。
- 采购成本和汇率使用计算当天的实时口径，不回溯到经营窗口或预测样本窗口。
- 所有计算在同一个只读 `REPEATABLE READ` 快照中完成，并共享 `calculated_at`、汇率和成本依据。
- 前端只渲染服务端结果与说明，不复制业务阈值、状态判定或公式。

### 1.3 统一术语

本文统一使用以下术语：

- **当前指标**：所选经营日期内，截至计算时点已经确认的业务事实，加上明确标识的未结算平台费估算。近期退款和全损尚未成熟时，它不是最终结果。
- **预测样本**：避开最近 7 个完整自然日后，向前 30 天或 90 天的已完结订单。
- **预测对象**：截至最新完整数据日仍未结算、未送达且尚未确认终局全损的订单。
- **已完结订单全损率 `p`**：预测样本内严格全损订单数除以全部已完结订单数。
- **严格全损**：终局物流全损，或商品已到海外/已送达后的最终全额退款。国内取消和部分退款不是整单全损。

“成熟”只描述预测样本已经跨过观察等待期，不应把近期开单的当前指标称为“已经成熟”。

---

## 2. 查询范围与聚合范围

### 2.1 店铺和 SPU 范围

`shop_pk` 是单店查询的内部主键。SPU 范围由以下选择之一确定：

1. 默认活动范围 `ActivitySelection`；
2. 精确 `spu_ids` 范围；
3. 重点关注 `FocusedSelection`。

页面搜索词 `q` 只影响行集合的展示，不得改变大盘 `totals`。精确 SPU 选择和重点关注集合属于业务范围，必须同时约束当前大盘和预测大盘。

预测虽然不展示单 SPU 行，但用户选择部分 SPU 后，预测大盘只聚合这些 SPU 对应的订单和商品行。空 SPU 集合返回空结果，不得回退整店。

### 2.2 行级金额与大盘订单去重

- 商品金额、商品件数、货本：按入选 `sales_order_lines` 聚合。
- 订单数、退款订单数、全损订单数、取消订单数、已完结样本数、预测对象订单数：在完整入选关系内 `COUNT(DISTINCT order_pk)`。
- `totals` 不受分页、排序和 `limit/offset` 影响。
- 比率必须使用全局去重后的分子和分母重新计算，不得平均 SPU 行比率。

---

## 3. 四套时间口径

### 3.1 计算时点

`calculated_at` 是本次一致性快照的 UTC 时间。所有结果必须暴露该时间，且同一响应中的订单、结算、售后、物流、广告、成本和汇率不得跨快照拼接。

### 3.2 当前经营窗口

页面通过 `w_start` / `w_end` 选择当前经营窗口：

- 两端均为店铺当地日，`w_end` 包含当天；
- SQL 边界转换为 `[start_local_midnight, end_plus_one_local_midnight)` 的 UTC 半开区间；
- 订单、退款、全损、取消和关联证据统一按订单 `COALESCE(order_time, paid_at)` 归属；
- 售后完成日、结算日和物流事件日不改变订单所属经营日；
- 广告日表使用同一组本地日期，按 `[w_start, w_end + 1)` 过滤 `day`；
- 店铺地区无法映射唯一 IANA 时区时，窗口查询必须失败，不得猜测时区。

当前单时区映射：

| region | IANA 时区 |
| --- | --- |
| VN | `Asia/Ho_Chi_Minh` |
| TH | `Asia/Bangkok` |
| SG | `Asia/Singapore` |
| MY | `Asia/Kuala_Lumpur` |
| PH | `Asia/Manila` |
| CN | `Asia/Shanghai` |
| JP | `Asia/Tokyo` |
| KR | `Asia/Seoul` |
| GB | `Europe/London` |

### 3.3 预测样本窗口

设：

- `A`：店铺当地时区的最新完整数据日，当前定义为计算日 `T` 的前一日，即 `A = T - 1`；
- `G`：成熟等待期，固定为 `7` 个完整自然日；
- `D`：预测回看天数，只允许 `30` 或 `90`，默认 `30`。

首尾均包含：

```text
sample_end   = A - G
sample_start = sample_end - (D - 1)
```

因此：

```text
D = 30: [A - 36, A - 7]
D = 90: [A - 96, A - 7]
```

若用今天 `T` 表示且 `A = T - 1`：

```text
30 天: [T - 37, T - 8]
90 天: [T - 97, T - 8]
```

约束：

- 页面只能切换 30 天和 90 天，不提供自定义预测样本日期；
- 不自动从 30 天回退到 90 天；
- 切换只改变预测样本和预测结果，不改变当前指标；
- 样本订单仍按 `COALESCE(order_time, paid_at)` 归属；
- 样本结果按 `calculated_at` 时已经确认的最终状态判断。

### 3.4 预测对象时点

预测对象不使用 `w_start` / `w_end`。目标范围为：

```text
订单归属日 <= A
AND 已付款
AND 未结算
AND 未送达
AND 尚未确认终局全损
AND 命中当前店铺/SPU 业务范围
```

它没有页面日期下界；只要订单仍属于当前风险池就应进入。今天 `T` 的未完整数据不进入截至 `A` 的目标池。

### 3.5 实时估值时点

采购成本和汇率均使用计算当天的最新有效值：

- 采购成本：`procurement.manual_product_costs.valid_to IS NULL`；缺失时回退 `40 CNY/件`；
- 汇率：最新可用 `fx.exchange_rate_snapshots` / `fx.exchange_rates` 快照；
- 历史经营窗口也按当天成本和汇率重新估值，结果可随成本或汇率更新而变化；
- 页面必须展示成本来源、汇率 `as_of` 和 `calculated_at`，不得暗示为交易日历史会计值。

---

## 4. 订单生命周期与严格全损判定

### 4.1 已付款订单

已付款白名单来自 `tts_erp_v2.db.constants.PAID_SALES_ORDER_STATUSES`：

```text
AWAITING_SHIPMENT
PARTIAL_SHIPPING
AWAITING_COLLECTION
IN_TRANSIT
DELIVERED
COMPLETED
```

`UNPAID`、`ON_HOLD` 不进入盈利事实。`CANCELLED` 进入取消分类，不进入已付款销售集合。

### 4.2 已完结订单

一个订单满足任一条件即为已完结，集合按 `order_pk` 去重：

1. 存在结算事实；
2. 已确认送达；
3. 国内取消且结果已确定；
4. 已确认终局物流全损。

送达证据按以下任一信号成立：

- `sales_orders.status IN ('DELIVERED', 'COMPLETED')`；
- shipment 状态为 `DELIVERED`；
- `shipments.delivered_at IS NOT NULL`；
- tracking event `action_code = 50101`。

### 4.3 严格全损订单

严格全损订单满足任一条件：

```text
is_terminal_logistics_full_loss
OR (
  is_overseas_exposed
  AND order_gmv > 0
  AND final_refund_amount >= order_gmv
)
```

当前结构化证据：

- `action_code = 80101`：退回卖家等终局物流失败信号；
- `action_code = 38301`：已经到达目的国；
- 送达证据见 §4.2；
- `final_refund_amount` 取已结算 `CUSTOMER_REFUND` 绝对值与已完成售后退款金额中的较大值，避免同一退款重复累计。

以下情况不进入严格全损分子：

- 国内取消，即使最终全额退款；
- 部分退款；
- 未付款或挂起订单；
- 只有退款申请、尚未完成；
- 没有海外暴露或送达证据的普通退款。

`REFUND_ONLY` 与 `RETURN_AND_REFUND` 不决定是否全损；决定因素是海外暴露/送达、最终全额退款和终局物流证据。

### 4.4 退款订单与全损订单不得混用

- 退款订单：存在已完成退款事实，按订单去重；部分退款也可成为退款订单。
- 严格全损订单：满足 §4.3；部分退款不是整单全损。
- 当前退款率和当前全损率是两个不同指标；预测输入只使用已完结订单全损率 `p`。

---

## 5. 当前指标开发口径

### 5.1 当前订单指标

所有指标均限定在当前经营窗口和业务范围：

| 指标 | 公式 | 说明 |
| --- | --- | --- |
| 总单量 | 已付款订单数 + 取消订单数 | 大盘按订单去重 |
| 有效单量 | 已付款订单数 − 已确认退款订单数 | 负数钳制为 0 |
| 有效销售额 | 已付款订单 GMV − 已确认退款金额 | 只扣当前已确认退款 |
| 退款数 | 已确认退款订单数 | 一单多个 case 只计一次 |
| 当前退款率 | 已确认退款订单数 ÷ 总单量 | 当前观察值，不作预测输入 |
| 当前全损量 | 已确认严格全损订单数 | 使用 §4.3 |
| 当前全损率 | 已确认严格全损订单数 ÷ 总单量 | 当前观察值，不作预测输入 |
| 国内取消量 | 国内取消订单数 | 海外/终局全损取消不重复进入 |
| 取消率 | 国内取消订单数 ÷ 总单量 | 与严格全损分子互斥 |

近期订单尚未成熟时，当前退款率和当前全损率可能偏低。这是“截至当前观察值”的性质，不得用未来预测补写当前事实。

### 5.2 当前净收入

当前指标不得再使用所选经营窗口自身的退款金额率预测未来退款。

订单行净收入：

```text
已结算行净收入
= 订单实际 SETTLEMENT
  × 行 GMV / 订单 GMV

未结算行费后收入
= 行 GMV × (1 - fee_rate_used)
```

聚合：

```text
current_unsettled_net
= unsettled_sales_after_fee
  - confirmed_unsettled_refund_after_fee

current_net_revenue
= settled_net
  + current_unsettled_net
```

约束：

- 已结算实际到账已经包含平台费用与已反映退款，不再乘平台费率或全损率；
- 未结算只估算平台费，并扣已经确认但尚未反映的退款；
- 不得乘当前经营窗口退款率；
- 不得乘 30/90 天预测样本的 `p`；
- 已确认退款只扣一次。

### 5.3 平台费率

`fee_rate_used` 解析优先级：

1. 页面临时覆写，仅当前请求；
2. 7 天内的 `reporting.shop_fee_rate_estimates` `fee-v2` 店铺快照；
3. 全局基线 `0.308`。

店铺快照每 24 小时按近 180 天未退款已结算订单计算：

```text
fee_rate
= Σ |FEE| / Σ line_gmv
```

`line_gmv = quantity × unit_price`，是客户实付，不是折扣前挂牌价。退款订单不进入费率样本，避免与预测损失重复。

### 5.4 当前成本、利润与 ROI

逐商品行：

```text
cogs_sold
= paid_quantity × current_unit_cost

cogs_full_loss_cancelled
= strict_full_loss_cancelled_quantity × current_unit_cost

cogs_total
= cogs_sold + cogs_full_loss_cancelled

observed_full_loss_cost
= confirmed_strict_full_loss_quantity × current_unit_cost

current_cogs_kept
= max(cogs_total - observed_full_loss_cost, 0)
```

当前利润与 ROI：

```text
current_net_profit
= current_net_revenue - cogs_total - ad_spend

current_nc_prime
= current_net_revenue - observed_full_loss_cost

current_roi
= current_nc_prime / ad_spend

current_breakeven_roi
= current_nc_prime
  / (current_nc_prime - current_cogs_kept)
```

空值规则：

- `ad_spend = 0`：`current_roi = null`；
- 保本分母 `<= 0`：`current_breakeven_roi = null`；
- 有未结算订单时，`net_profit` / ROI 必须标记“含未结算平台费估算”，不得描述为全部已结算实际值。

### 5.5 广告系统当前指标

```text
ad_system_actual_roi
= attributed_ad_gmv / ad_spend

current_max_ad_spend
= current_net_revenue
  - cogs_total
  - structured_additional_costs

ad_system_breakeven_roi
= attributed_ad_gmv / current_max_ad_spend
```

当前没有结构化录入退货运费、提现费、汇兑损失、包装耗材等结算外必要成本，因此：

- 状态返回 `estimated_known_costs`；
- 页面用 `≈` 标记已知成本下限估算；
- 最大可承受广告费 `<= 0` 或无归因 GMV 时返回 `null`。

---

## 6. 大盘预测开发口径

### 6.1 展示与范围

- 预测仅返回和展示在大盘 `totals`；`items[]` 没有产品意义上的预测内容。
- 预测忽略 `w_start` / `w_end`。
- 预测跟随 `shop_pk` 和已应用的精确/重点关注 SPU 范围。
- `q`、分页和排序不得改变预测大盘。
- 页面默认 30 天，可切换 90 天；不提供自定义预测日期。

建议新增独立参数：

```text
projection_lookback_days = 30 | 90
默认 30
```

该参数不得复用 `w_start` / `w_end`。

### 6.2 预测样本与比例

样本必须同时满足：

- 订单归属日在 §3.3 样本窗口内；
- 截至 `calculated_at` 已满足 §4.2 已完结条件；
- 命中当前店铺和 SPU 业务范围。

```text
completed_full_loss_rate p
= strict_completed_full_loss_order_count
  / completed_order_count
```

状态：

- `completed_order_count = 0`：`insufficient_sample`，`p` 和预测结果为 `null`；
- `1 <= completed_order_count < 10`：允许计算，但增加 `projection_low_sample` warning；
- `completed_order_count >= 10`：正常计算；
- 不得把无样本解释成 `0%`；
- 30 天样本不足时不得自动使用 90 天，必须由用户切换。

### 6.3 预测对象

```text
risk_order
= paid
  AND unsettled
  AND not delivered
  AND not confirmed_terminal_full_loss
  AND order_day <= A
```

已送达未结算订单只存在结算滞后，不再暴露于未来拒收/配送失败风险；其预计到账按费后收入减已确认退款计算，不乘 `p`。

### 6.4 预计未来全损

预测对象已经排除确认终局全损，因此：

```text
projected_future_full_loss_order_count
= risk_order_count × p

projected_future_full_loss_qty
= risk_qty × p

projected_future_full_loss_cost
= Σ(risk_qty × p × current_unit_cost)
```

大盘成本必须逐 SPU 计算后聚合，不得用一个混合平均成本替代各 SPU 当前成本。

```text
projected_terminal_full_loss_cost
= observed_full_loss_cost
  + projected_future_full_loss_cost
```

预测件数为期望值，可带小数；wire 层不得为展示提前取整。

### 6.5 预计净收入与净利润

```text
delivered_unsettled_expected_net
= delivered_unsettled_sales_after_fee
  - delivered_confirmed_unreflected_refund_after_fee

risk_expected_net
= risk_sales_after_fee × (1 - p)

projected_net_revenue
= settled_actual_net
  + delivered_unsettled_expected_net
  + risk_expected_net
  - remaining_confirmed_unreflected_refund_after_fee

projected_net_profit
= projected_net_revenue
  - cogs_total
  - ad_spend
```

约束：

- 已确认退款只扣一次；
- 付款订单货本已经进入 `cogs_total`，预测未来全损不得再次加入 COGS；
- 未来全损先通过预计收入减少影响利润；
- 海外取消等当前已经发生但未包含在 paid quantity 的严格全损货本，继续进入 `cogs_full_loss_cancelled`。

### 6.6 预计 ROI 与预计保本 ROI

```text
projected_nc_prime
= projected_net_revenue
  - projected_terminal_full_loss_cost

projected_cogs_kept
= max(cogs_total - projected_terminal_full_loss_cost, 0)

projected_roi
= projected_nc_prime / ad_spend

projected_breakeven_roi
= projected_nc_prime
  / (projected_nc_prime - projected_cogs_kept)
```

- `ad_spend = 0`：预计 ROI 为 `null`；
- 保本分母 `<= 0`：预计保本 ROI 为 `null`；
- 样本不足：所有依赖 `p` 的预计结果为 `null`。

### 6.7 预计广告系统 ROI

当前没有订单级广告归因映射，因此预测不得把 `p` 乘到全部广告归因 GMV：

```text
projected_ad_gmv
= current_attributed_ad_gmv

projected_ad_system_roi
= projected_ad_gmv / ad_spend

projected_max_ad_spend
= projected_net_revenue
  - cogs_total
  - structured_additional_costs

projected_ad_system_breakeven_roi
= projected_ad_gmv / projected_max_ad_spend
```

结算外必要成本未结构化时继续返回 `estimated_known_costs`。

---

## 7. 数据源与字段映射

### 7.1 目录和范围

| 概念 | 来源 | 规则 |
| --- | --- | --- |
| 店铺 | `commerce.shops` | `shop_pk = id`；`region` 决定报表时区 |
| SPU | `commerce.products_spu` | `id = spu_pk`；`spu_id` 是平台编号 |
| 商品行 | `commerce.sales_order_lines` | `order_pk`、`spu_pk`、`quantity`、`unit_price` |
| 行 GMV | `quantity × unit_price` | 原生销售币种，VN 店为 VND |

### 7.2 订单、结算、售后和物流

| 概念 | 来源 | 规则 |
| --- | --- | --- |
| 订单状态/时间 | `commerce.sales_orders.status/order_time/paid_at` | 日期归属用 `COALESCE(order_time, paid_at)` |
| 结算交易 | `finance.settlement_transactions` | 通过 `order_pk` 关联 |
| 实际到账 | `finance.settlement_components.amount`，`component_code='SETTLEMENT'` | 按订单聚合后按行 GMV 比例分摊 |
| 已结算退款 | `component_code='CUSTOMER_REFUND'` | 取绝对值；与售后退款取较大值避免重复 |
| 售后 | `after_sales.cases` + `after_sales.case_lines` | 只使用完成状态；按商品行合并，确认件数不超过原行数量 |
| 完成状态 | `RETURN_OR_REFUND_REQUEST_COMPLETE` / `CANCELLATION_REQUEST_COMPLETE` | 未完成 case 不进入确认事实 |
| shipment | `fulfillment.shipments` | `order_pk` 关联订单 |
| tracking | `fulfillment.tracking_events` | `50101` 送达、`38301` 到达目的国、`80101` 终局退回证据 |

### 7.3 广告

当前 SPU ROI 广告事实只读取 `plugin.ad_daily`：

| 字段 | 含义 |
| --- | --- |
| `mixed_real_cost` | 广告实际消耗，原生 USD |
| `onsite_roi2_shopping_value` | 广告归因 GMV，原生 USD |
| `onsite_roi2_shopping_sku` | 广告平台归因出单量 |
| `day` | 当前经营窗口的广告日期 |
| `product_id` | 通过店铺 `seller_id` + `spu_id` 关联 SPU |

不得读取 `plugin.ad_today` 拼接历史窗口，也不得把广告赠金混入 `mixed_real_cost`。

### 7.4 成本、费率和汇率

| 概念 | 来源 | 规则 |
| --- | --- | --- |
| 当前人工成本 | `procurement.manual_product_costs.unit_cost` | `valid_to IS NULL` |
| 默认成本 | 后端常量 | `40 CNY/件` |
| 店铺费率 | `reporting.shop_fee_rate_estimates` | 最新 `fee-v2`，超过 7 天回退 |
| 费率基线 | 后端常量 | `0.308` |
| 汇率 | `fx.exchange_rate_snapshots` + `fx.exchange_rates` | 本次快照统一使用最新精确汇率 |

`procurement.procurement_products.source_unit_cost` 不参与盈利计算。

---

## 8. 目标领域接口与 HTTP 契约

### 8.1 领域边界

当前指标范围和预测政策必须在领域层分开表达，禁止继续用一个日期范围同时控制两者。目标形状：

```python
ProfitScope(
    shop_pk=...,
    start_date=...,
    end_date=...,
    selection=...,
)

ProjectionPolicy(
    lookback_days=30,       # Literal[30, 90]
    maturity_lag_days=7,
)
```

`read_overview()` 在一个快照中读取当前大盘与预测大盘，但两者各自解析时间范围。

### 8.2 HTTP 参数

| 参数 | 作用范围 | 约束 |
| --- | --- | --- |
| `shop_pk` | 当前 + 预测 | 预测页面必填 |
| `spu_ids` / `scope=focused` | 当前 + 预测 | 精确业务范围 |
| `q` | 行展示 | 不改变 totals |
| `w_start` / `w_end` | 仅当前指标和证据 | 店铺当地日，结束日包含 |
| `projection_lookback_days` | 仅预测 | `30` 或 `90`，默认 `30` |
| `fee_rate` | 当前 + 预测的未结算费后收入 | 临时覆写，不持久化 |

### 8.3 响应边界

- `items[]`：当前指标与当前证据，不新增产品可见的 SPU 预测含义；
- `totals`：当前大盘 + 大盘预测；
- `meta.projection` 至少返回：
  - `as_of`；
  - `lookback_days`；
  - `maturity_lag_days`；
  - `sample_start` / `sample_end`；
  - `basis_order_count`；
  - `basis_full_loss_order_count`；
  - `completed_full_loss_rate`；
  - `scope` 说明；
  - 状态和 warning；
- `meta.fx` 返回汇率值、snapshot id 与 `as_of`；
- `meta.fee` 返回实际费率来源、逐店费率和降级原因；
- `meta.presentation` 返回页面说明和告警文案。

稳定 `/v2` 兼容边界：旧客户端若仍依赖 `items[]` 的历史预测字段，adapter 可以临时保留兼容字段，但业务层和前端不得继续计算或展示单 SPU 预测。删除稳定字段需要版本化或明确的 breaking 变更授权。

---

## 9. 精度、空值与状态

### 9.1 wire 精度

- CNY 金额：4 位小数字符串；
- 一般 ROI / 比率：2 位小数字符串；
- 费率、预测 `p`、大盘率：4 位小数字符串；
- 实际订单/件数：整数；
- 预计订单/件数：Decimal 字符串，不提前取整；
- `null` 表示数学无解、样本缺失或分母为 0，不等同于 0。

### 9.2 预测状态

| 状态 | 条件 | 结果 |
| --- | --- | --- |
| `available` | 有预测对象且样本分母 > 0 | 返回预测结果 |
| `no_unsettled_orders`（兼容名） | 当前范围没有待完结风险订单 | 未来新增全损为 0；预计结果与当前结果一致 |
| `insufficient_sample` | 有预测对象但样本分母为 0 | 依赖 `p` 的字段为 null |

warning：

- `projection_low_sample`：样本 1–9 单；
- `projection_insufficient_sample`：样本 0 单；
- `default_unit_cost_used`：存在默认 40 CNY 成本；
- `fee_rate_baseline_used`：费率回退 0.308；
- `estimated_known_costs`：广告保本线缺结算外成本。

---

## 10. 测试与验收矩阵

实现变更必须覆盖以下场景：

1. 改变 `w_start/w_end`：当前指标改变，预测样本范围、预测对象和预测结果不变；
2. 改变店铺或已应用 SPU 集合：当前指标和预测大盘同时改变；
3. 仅改变 `q`、分页或排序：大盘当前值和预测值都不变；
4. 默认 30 天，切换 90 天后只改变预测；非法值和自定义日期返回 422；
5. 30 天样本为空不自动读取 90 天，状态为 `insufficient_sample`；
6. 样本 1–9 单仍计算，但返回 `projection_low_sample`；
7. 国内取消进入已完结分母、不进入严格全损分子；
8. 部分退款进入退款指标、不进入整单全损分子；
9. 已到海外/已送达后的最终全额退款进入严格全损分子；
10. 已送达未结算订单不进入预测风险池；
11. 当前未结算净收入不乘页面经营窗口退款率，也不乘预测 `p`；
12. 已确认退款只扣一次，已结算到账不再折减；
13. 预测新增全损不重复加入 `cogs_total`；
14. 多 SPU 同订单的大盘订单数全局去重；
15. 当前有效成本或最新汇率改变时，历史经营窗口金额按新估值变化；
16. 广告消耗为 0、保本分母 <= 0、样本为空时分别返回 null；
17. items 不展示单 SPU 预测，totals 返回大盘预测；
18. 同一响应所有 basis 共享同一个 `calculated_at`。

数据库测试只能通过 `bash scripts/test_isolated.sh ...` 运行；禁止直接调用 pytest 或生产形状数据库。

---

## 11. 当前实现与目标契约的已知差异

以下差异是有意保留的兼容边界，不得误读为预测口径未实现：

1. `items[]` / 领域行类型仍携带历史 SPU 行级 projection 字段；产品展示和新客户端只读取 `totals` 与 `meta.projection`，这些字段仅为 v9/v10 兼容 consumer 保留；
2. 当前值仍保留历史 SPU 退款率兼容路径和 v9 `full_loss_qty/full_loss_rate` 诊断字段；严格全损预测分子不复用这些兼容字段；
3. 未结算收入的旧 wire 字段仍按兼容 serializer 输出；当前净收入仍保留历史 SPU 退款率兼容路径，尚未按目标口径完全收敛；目标口径是只扣确认退款，未来风险损失只进入 projection 结果；
4. 部分接口历史文档仍可能引用 `docs/archive/` 的 M 码和旧 v9 口径，业务计算不得以 archive 为准。

当前已经生效的预测契约：

- 预测样本、风险池、预测 totals、诊断 rates、warning、status 和 `meta.projection` 均独立于 `w_start/w_end`，跟随店铺和已应用 SPU 范围；
- HTTP 接受独立 `projection_lookback_days=30|90`，默认 30；页面可切换 90，非法值返回 422；
- 严格终局全损以 80101 物流证据和 canonical paid/payment evidence 为前提；UNPAID、ON_HOLD 等非付费取消单不进入严格样本；
- 样本 1–9 单返回 `projection_low_sample`，样本 0 返回 `projection_insufficient_sample`；已送达但未结算且无风险池时返回 `no_unsettled_orders`。

### 11.1 当前实现复核清单

- 预测窗口使用店铺本地 `T-1`、7 天成熟等待期和 30/90 lookback；
- 报表日期只改变当前事实和 evidence，不改变 projection aggregate；
- projection aggregate 在同一只读快照内复用已应用 selection/catalog，并独立于分页、排序和 `q`；
- v9 兼容 fields 保留但不得作为严格预测公式或产品文案的依据。

---

## 12. 开发复核清单

提交实现前逐项确认：

- [ ] 飞书业务口径版本已读取并记录；
- [x] 当前窗口和预测窗口使用不同参数；
- [x] 预测只在 totals 计算和展示（items 仅保留兼容字段）；
- [x] 30/90 天公式、7 天成熟等待期和本地时区边界有测试；
- [ ] 当前净收入未使用当前窗口退款率预测未来；
- [x] 严格全损与普通退款分开；
- [x] 国内取消、部分退款、已送达未结算分类正确；
- [x] 80101 终局证据必须同时满足 canonical paid/payment evidence；
- [ ] 订单级事实全局去重，金额按商品行聚合；
- [ ] 成本和汇率使用计算当天快照，并暴露依据；
- [ ] 已确认退款、平台费、货本和预测损失均未重复扣除；
- [ ] 预计件数保留 Decimal，不为显示提前取整；
- [ ] 无样本、零广告、无效保本分母返回 null 而不是 0；
- [ ] 前端没有复制后端阈值、状态或公式；
- [ ] 相关 API 文档、测试和 UI 说明与本文一致。

---

## 13. 代码与文档锚点

| 类型 | 位置 |
| --- | --- |
| 业务口径权威 | 飞书《SPU-ROI 计算口径》 |
| 本开发契约 | `docs/business/spu-profitability.md` |
| deep module 接口 | `docs/design/spu-profitability-technical-design.md` |
| HTTP 契约 | `docs/api/external-api.md` |
| 领域入口 | `tts_erp_v2/analytics/spu_profitability/__init__.py` |
| 领域类型 | `tts_erp_v2/analytics/spu_profitability/_types.py` |
| PostgreSQL facts | `tts_erp_v2/analytics/spu_profitability/_implementation.py` |
| 纯公式 seam | `tts_erp_v2/analytics/spu_profitability/_formula_v10.py` |
| 一致性快照 | `tts_erp_v2/analytics/spu_profitability/_snapshot.py` |
| HTTP adapter | `tts_erp_v2/analytics/spu_roi.py` |
| 页面 kernel | `tts_erp_v2/static/js/spu-profitability-page.js` |
| 历史资料 | `docs/archive/`（非现行计算依据） |
