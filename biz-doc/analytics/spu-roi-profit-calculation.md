# SPU ROI 利润计算口径（业务文档）

> **当前口径版本：v10 —— 本文档所有章节共同构成 v10 口径整体，不存在"某一节是 v10、其他节是旧版"的子口径。**
> **本文档只定义业务概念与公式，不出现任何表名 / 字段名 / SQL。**
> 「某个概念在数据库里到底是哪张表哪个字段」见
> [`biz-doc/analytics/spu-roi-data-sources.md`](spu-roi-data-sources.md)（API 数据源 + plugin 数据源两部分）。
> **本文档 = 利润口径的唯一 truth source（计算依据）**。
> 历史演进与实测档案：`handoff/spu-roi-full-loss-rubric.md`（v3–v9 版本史 + 时点数字，**非计算依据**）
> 整理日期：2026-09-07；拆分纯化：2026-09-15；v10 更新：2026-10-07

---

## Changelog

| 版本 | 日期 | 变更内容 |
| --- | --- | --- |
| v10 projection delivered-loss sample | 2026-10-01 | 退款金额率继续使用已结算财务样本；预测全损率改为“已送达且有已完成退款/退货退款的订单数 ÷ 全部已送达订单数”，与结算状态解耦。 |
| v10 projection delivery-aware | 2026-09-30 | 未结算订单因结算周期滞后但已送达时，不再进入未来全损风险暴露；送达证据包括订单已送达/已完成，或物流状态、送达时间、签收事件任一确认。退款金额预测仍覆盖全部未结算销售额。 |
| v10 projection | 2026-09-30 | 在不改变当前指标语义的前提下新增预测层：已结算样本分别计算退款金额率和已结算订单全损率；先估算整批未结算订单的终局退款/全损额度，再扣除已确认结果，只把差额作为未来新增；预计净利润按当前净利润加未结算净收入调整计算，并新增预计 ROI / 保本 ROI / 广告系统 ROI / 广告系统保本 ROI |
| v10 | 2026-10-07 | **大盘与 SPU 明细指标口径统一**：(1) 有效单量 = 有效订单 − 退款订单；(2) 退款数/退款率改为订单维度（退款订单数 / 全部订单）；(3) 全损量/全损率改为订单维度（退款订单 + 海外取消订单）；(4) 取消量/取消率 = 国内取消订单（排除海外取消）；(5) 新增实际保本ROI、广告系统实际ROI和广告系统保本ROI，结算外成本未结构化时标记 `estimated_known_costs`；(6) 每个 SPU 行与大盘使用同一公式；多 SPU 大盘的订单级事实独立全局去重，不能简单累加 SPU 行；(7) 前端只格式化后端结果；(8) 广告 USD、销售/退款 VND 在公式入口按同一汇率快照换算，所有金额统一以 CNY 计算和输出，比例不因换币改变 |
| v9 | 2026-09-15 | 全损 = 完结退货(不论物流) + 海外取消(38301)；国内取消 ≠ 全损 |
| v8 | 2026-09-15 | 广告消耗按日期窗口裁剪（与销售/退款同语义） |
| v7 | 2026-09-07 | 净收入分层（已结算用实际到账、未结算用估算费率） |

---

## 一、名词定义

本节是唯一权威的概念定义。所有公式、看板、报表用到这些词时，以本节为准。

### 1.1 订单层概念

| 名词 | 定义 |
| --- | --- |
| **订单** | 买家在店铺下的一笔交易。一个订单可含多个商品行（SKU 行）。 |
| **有效销售订单**（口径 B） | 买家已付款、且未被取消的订单。状态枚举层面 = 已付款白名单（待发布 / 部分发货 / 待揽收 / 运输中 / 已送达 / 已完成），**不含**未付款、挂起、已取消。 |
| **退款订单** | 有效销售订单中，存在已完结的退货退款 / 仅退款售后单的订单。按订单去重（一张订单多个退款 case 只计一次）。 |
| **取消订单** | 终态为「已取消」的订单。按取消时货的位置拆两种互斥子类（见下两行）。 |
| **国内取消** | 取消时货**还没到**目的国（物流未出境）。货拿得回来，不算损失。 |
| **海外取消** | 取消时货**已到**目的国（物流已出境）。货拿不回来，等同于全损。 |
| **退货** | 买家已付款、货已发出，事后发起并**已完结**的退货退款 / 仅退款售后单。不论货到哪，一律算全损。 |
| **全损** | **退货 ∪ 海外取消**。这批货的采购成本实打实亏掉，要计入货本。国内取消**不是**全损。 |
| **全部订单** | 有效销售订单 + 取消订单（含国内取消和海外取消）。 |
| **已结算订单** | 平台已出结算单、卖家已实际到账的订单。净收入用实际到账金额。 |
| **未结算订单** | 平台还没结算的订单。净收入只能按估算费率折算。 |

口语记忆：

```text
有效销售额 = 所有已付款订单 − 取消订单 − 退款
             （其中「取消订单」里的海外取消虽已取消，货本仍要算——它是全损）
全损订单   = 退款订单 + 海外取消订单
全损件数   = 退货件数 + 海外取消件数
```

### 1.2 数量层概念

| 名词 | 定义 |
| --- | --- |
| **有效件数**（effective_qty） | 有效销售订单的商品件数合计（排除退货 case 波及的行）。 |
| **全损件数**（full_loss_qty） | 退货件数 + 海外取消件数。 |

### 1.3 金额层概念

| 名词 | 定义 |
| --- | --- |
| **采购成本**（单价，unit_cost） | 一件商品的采购价格（人民币）。仅使用当前有效的人工标注价格；未标注时兜底 40 CNY/件，见 §四。妙手/1688 同步货源价不参与计算。 |
| **有效 GMV** | 有效销售订单的商品成交金额（店铺当地币种），未扣任何平台费用。 |
| **净收入** | 平台结完账后卖家真正拿到的钱。已结算订单用实际到账；未结算订单用 GMV × (1 − 平台费率基线) 估算。详见 §二。 |
| **广告消耗** | 单店铺所有广告消耗 = Σ mixed_real_cost（广告平台原生 USD），按同一汇率快照换算为 CNY。GMV Max 归因含自然单，不是纯广告增量。 |
| **采购成本合计** | (有效件数 + 全损件数) × 采购成本（CNY 单价）。**全损件数要计货本**（货没了钱花了）。 |
| **净利润** | 净收入 − 广告消耗 − 采购成本合计。 |
| **NC'** | 净收入 − 全损成本（= return_loss）。ROI 计算的核心中间变量。 |
| **COGS_kept** | (售出件数 − 退货件数) × 单位成本。保本 ROI 计算用。 |
| **实际 ROI** | NC' ÷ 广告消耗。≥ 保本 ROI = 赚，< 保本 ROI = 亏。 |
| **保本 ROI**（实际保本 ROI） | NC' ÷ (NC' − COGS_kept)。净利润 = 0 时的 ROI 临界值；实际 ROI 低于此值即亏。 |
| **广告系统实际 ROI** | 广告归因 GMV ÷ 广告实际消耗。与财务实际 ROI（NC' ÷ 广告消耗）分子不同，不得混用。 |
| **最大可承受广告费** | 预计净结算收入 − 与收入同范围的采购成本 − 净结算中尚未包含的其他必要成本。结果 ≤ 0 表示当前成本结构下不存在可用广告预算。 |
| **广告系统保本 ROI** | 广告归因 GMV ÷ 最大可承受广告费。分母 ≤ 0 或没有广告归因 GMV 时不可计算。 |
| **退款率** | 退款订单数 ÷ 全部订单（有效订单 + 取消订单）。 |
| **全损率** | 全损订单数 ÷ 全部订单。全损订单 = 退款订单 + 海外取消订单。 |
| **取消率** | 国内取消订单数 ÷ 全部订单。**只含国内取消**——海外取消已计入全损，两个率互斥不重叠。 |

---

## 二、利润公式

$$
\text{净利润}_{\text{CNY}} = \text{净收入}_{\text{CNY}} - \text{广告消耗}_{\text{CNY}} - \text{采购成本合计}_{\text{CNY}}
$$

### 2.1 净收入（按订单是否已结算分层）

$$
\text{净收入}_{\text{CNY}} = \frac{\sum \text{line\_net}_{\text{VND}}}{\text{VND/CNY 汇率}}
$$

每个商品行的净收入：

$$
\text{line\_net} =
\begin{cases}
\text{订单实际到账} \times \dfrac{\text{行 GMV}}{\text{订单 GMV}} & \text{已结算订单} \\[8pt]
\text{行 GMV} \times (1 - \hat{r}) \times (1 - \text{退款率}_{\text{金额}}) & \text{未结算订单}
\end{cases}
$$

- **已结算订单**：平台结算单上的「实际到账」金额已扣完所有平台费 + 运费 + 联盟佣金 + 退款调整，按行 GMV 占比分摊到每个商品行。
- **未结算订单**：按平台费率 $\hat{r}$ 估算（净收入 ≈ 行GMV × (1 − $\hat{r}$)），再乘以 (1 − 退款率) 扣除预期退款损失。$\hat{r}$ 优先取**店铺实测**：每 24h 按该店近 180 天**未退款(kept)** 已结算订单 $\Sigma|\text{FEE}| \div \Sigma\text{行GMV}$ 重算并写 `fee-v2` 快照（窗口内有一单已结算即产出）；无可用实测或快照过期的店铺回退全局基线 30.8%。页面「临时覆写费率 %」可覆盖两者（仅影响本次查询，不写回店铺）。

  > ⚠️ 为何只算未退款订单：本公式已另有 $(1-\text{退款率})$ 扣一次退款；若 $\hat{r}$ 的样本里再混入全额退款订单（其费率仅 ~3%），退款效应会被算两遍。生产反证：kept 口径预测误差 ±1%，混合口径高估 ~16%。

### 2.2 采购成本合计

$$
\text{采购成本合计}_{\text{CNY}} = (\text{有效件数} + \text{全损件数}) \times \text{采购成本（单价）}_{\text{CNY}}
$$

### 2.3 ROI 公式

$$
\text{NC'} = \text{净收入} - \text{全损成本}
$$

$$
\text{实际 ROI} = \frac{\text{NC'}}{\text{广告消耗}}
$$

$$
\text{COGS\_kept} = (\text{售出件数} - \text{退货件数}) \times \text{单位成本}
$$

$$
\text{保本 ROI} = \frac{\text{NC'}}{\text{NC'} - \text{COGS\_kept}}
$$

广告系统使用另一套分子，必须与财务 ROI 分开列示：

$$
\text{广告系统实际 ROI} = \frac{\text{广告归因 GMV}}{\text{广告实际消耗}}
$$

$$
\text{最大可承受广告费} = \text{预计净结算收入} - \text{同范围采购成本} - \text{结算外必要成本}
$$

$$
\text{广告系统保本 ROI} = \frac{\text{广告归因 GMV}}{\text{最大可承受广告费}}
$$

其中预计净结算收入优先使用已结算实际到账，加上按同口径估算的未结算净额；净结算已扣除的平台佣金、联盟佣金、VAT、交易费、平台物流费不得重复扣除。采购成本必须覆盖该收入范围内全部售出件和海外取消全损件。

当前系统尚未结构化录入退货运费、采购退款失败、提现费、汇兑损失、包装耗材等结算外必要成本，因此接口返回的广告系统保本 ROI 状态为 `estimated_known_costs`，页面以 `≈` 标记“已知成本下限估算”。这些成本有实际数据前，不得把该估算解释为最终保本线；若分母 ≤ 0 或无广告归因 GMV，返回空值并显示 `--`。

### 2.4 判亏条件

$$
\text{净利润} < 0 \iff \text{实际 ROI} < \text{保本 ROI}
$$

### 2.5 大盘与 SPU 明细指标公式

同一套公式同时适用于 `GET /v2/analytics/spu-roi` 的每个 `items[]` SPU 明细和 `totals` 盈利大盘。前端只做格式化展示：

- `profit_status`、`roi_status`、`has_unsettled_orders`、`uses_default_unit_cost`、`refund_rate_alert` 由后端按当前口径和阈值直接返回；前端不得从金额、单量或比率重新推导；
- 退款警戒阈值、盈利版本和 P&L 说明由 `meta.presentation` 返回，禁止在页面脚本复制 `0.308`、版本号或公式文本；
- 单 SPU 范围内，行级的有效销售、有效单量、退款数/率、全损量/率、取消量/率必须与大盘相等；
- 多 SPU 范围内，金额可以按行加总；订单可能包含多个 SPU，因此大盘订单数与三个率必须在整体范围内按订单全局去重，不能简单累加 SPU 行；
- 旧金额退款率与件数全损率只作为解释字段 `refund_amount_rate` / `full_loss_qty_rate` 保留，不得用于主表或冒充大盘三率。

| 指标 | 公式 | 说明 |
| --- | --- | --- |
| 广告消耗 | Σ mixed_real_cost（单店铺所有广告消耗） | `items[].spend` / `totals.spend` |
| 有效销售额 | 有效销售订单 GMV − 退款金额 | `items[].effective_sales` / `totals.effective_sales` |
| 有效单量 | 有效订单数 − 退款订单数 | `items[].effective_order_count` / `totals.effective_order_count` |
| 退款数 | 退款订单数（订单维度去重；退款跟随原订单，按订单 `COALESCE(order_time, paid_at)` 时间窗口归属） | `items[].refund_order_count` / `totals.refund_order_count` |
| 退款率 | 退款订单数 ÷ 全部订单 | `items[].refund_rate` / `totals.refund_rate` |
| 全损量 | 退款订单数 + 海外取消订单数（订单维度） | `items[].full_loss_order_count` / `totals.full_loss_order_count` |
| 全损率 | 全损量 ÷ 全部订单 | `items[].full_loss_rate` / `totals.full_loss_rate` |
| 取消量 | 国内取消订单数（排除海外取消） | `items[].domestic_cancelled_order_count` / `totals.domestic_cancelled_order_count` |
| 取消率 | 国内取消订单数 ÷ 全部订单 | `items[].cancel_rate` / `totals.cancel_rate` |
| 实际ROI | NC' ÷ 广告消耗 | `items[].roi_real` / `totals.roi_real` |
| 实际保本ROI | NC' ÷ (NC' − COGS_kept) | `items[].roi_breakeven` / `totals.roi_breakeven` |
| 广告系统实际ROI | 广告归因 GMV ÷ 广告实际消耗 | `items[].ad_system_actual_roi` / `totals.ad_system_actual_roi`；历史 `roi_l0` 为兼容别名 |
| 最大可承受广告费 | 预计净结算收入 − 同范围采购成本 − 结算外必要成本 | `items[].ad_system_max_ad_spend` / `totals.ad_system_max_ad_spend` |
| 剩余广告费承受空间 | 最大可承受广告费 − 广告实际消耗 | 负值表示已经越过已知成本下的保本预算 |
| 广告系统保本ROI | 广告归因 GMV ÷ 最大可承受广告费 | 当前 `estimated_known_costs`，结算外必要成本尚未结构化 |

### 2.6 未结算订单预计终局

预计终局层是对 v10 当前值的**增量补充**。当前 `net_profit`、`roi_real`、
`roi_breakeven` 等字段保持原语义；预测字段不得覆盖当前字段，也不得标记为“实际”。
`spu-roi` 与 `focused-spus` 使用完全相同的后端结果和前端展示组件。

#### 2.6.1 两类订单与时间归属

预测使用两套相互独立的样本：

1. **已结算订单**：继续使用 SETTLEMENT 实际到账，不做预测；同一日期范围内的
   已结算订单只作为退款金额率样本。退款优先依据结算组件 `CUSTOMER_REFUND`，缺失时
   用已完结退款/退货 case 补充。
2. **已送达订单**：订单状态 `DELIVERED`/`COMPLETED`，或 shipment 状态
   `DELIVERED`、`delivered_at`、物流事件 `50101` 任一确认已送达的 paid 订单。
   不区分结算状态，用于计算“已送达订单全损率”；其中存在已完成
   `REFUND_ONLY`/`RETURN_AND_REFUND` case 的订单计为全损订单。
3. **未结算订单**：退款金额预测对象。已经确认退款、退货或全损的商品部分按已知事实
   处理；只有尚未确认结果的商品部分应用预测比例。
4. **未来全损风险暴露订单**：未结算且尚未确认送达的订单。若订单状态已送达/已完成，
   或物流状态、送达时间、签收事件任一确认已送达，则视为已经越过未来全损风险窗口，
   不进入全损预测分母；它仍属于未结算订单，不改变收入和退款金额预测。

预测样本和预测对象都沿用页面现有时间归属：按订单
`COALESCE(order_time, paid_at)` 落入 `w_start`/`w_end` 窗口，结算日和售后完成日
不改变订单归属。时间选择因此会同时改变样本、预测对象和预计终局结果。

> 限制：源数据只有部分订单具有可靠的送达时间戳，因此“送达后退款”按最终事实组合
> 判断，即订单已有送达证据且存在已完成退款/退货退款 case，不强制比较两个事件的时间戳。
> 该比率描述送达后售后风险，不预测未来海外取消。页面同时保留“当前全损率”，两者不得混用。

#### 2.6.2 两个预测比例必须分开

$$
\text{projection\_refund\_amount\_rate}
= \frac{\text{已结算样本已完结退款金额}}{\text{已结算样本销售额}}
$$

$$
\text{delivered\_full\_loss\_rate}
= \frac{\text{已送达且已完成退款/退货退款的订单数}}{\text{全部已送达订单数}}
$$

退款金额率用于全部未结算销售额的收入预测；已送达订单全损率只用于估算尚未送达的
未结算订单最终可能出现的全损。wire 字段 `delivered_full_loss_rate` 是权威字段；兼容字段
`projection_full_loss_qty_rate` 与 `settled_full_loss_rate` 暂时返回同一比率。当前
`full_loss_rate` 继续展示全部当前事实，不作为预测输入。

#### 2.6.3 未结算收入和全损预测

```text
expected_terminal_full_loss_orders
= full_loss_exposure_unsettled_order_count × delivered_full_loss_rate

projected_future_full_loss_order_count
= max(expected_terminal_full_loss_orders
      − confirmed_full_loss_exposure_order_count, 0)

expected_terminal_full_loss_qty
= full_loss_exposure_unsettled_order_count
  × (delivered_refund_qty ÷ delivered_order_count)

projected_future_full_loss_qty
= max(expected_terminal_full_loss_qty
      − confirmed_full_loss_exposure_qty, 0)
```

其中 `full_loss_exposure_unsettled_order_count` 只包括尚未送达的未结算订单。预计新增
订单数和件数还分别以该风险暴露中尚未确认结果的订单数、件数为上限。已送达未结算
订单只因平台结算周期滞后而留在未结算池，不得放大全损预测。大盘在“未结算订单”后
单独展示“未结算已送达”订单量：

```text
delivered_unsettled_order_count
= unsettled_order_count − full_loss_exposure_unsettled_order_count
```

页面只展示“预计未来新增全损”，不展示预计终局全损。退款金额同样按整批额度减已发生
金额处理：

```text
expected_terminal_refund
= unsettled_sales_after_fee × projection_refund_amount_rate

projected_terminal_refund
= max(expected_terminal_refund, confirmed_unsettled_refund_after_fee)

projected_future_refund
= projected_terminal_refund − confirmed_unsettled_refund_after_fee

projected_unsettled_net
= unsettled_sales_after_fee − projected_terminal_refund
```

`fee_rate` 优先级不变：页面临时覆写 > 店铺 fee-v2 实测快照 > 全局基线 0.308。
已结算实际到账不得再次扣平台费或预测退款率。

#### 2.6.4 预计利润和 ROI

```text
unsettled_net_delta
= projected_unsettled_net − current_unsettled_net

projected_net_revenue
= current_net_revenue + unsettled_net_delta

projected_net_profit
= current_net_profit + unsettled_net_delta
```

当前 `cogs_total` 已包含所有有效销售商品货本。未来退款/退货不会再采购一次，因此
预测新增全损**不得再次加入 COGS**；未来全损通过预计收入减少影响利润。海外取消
继续只按现有 `cogs_full_loss_cancelled` 规则补扣货本。

```text
projected_full_loss_cost
= projected_terminal_full_loss_qty 对应的单位成本合计

projected_nc_prime
= projected_net_revenue − projected_full_loss_cost

projected_roi_real
= projected_nc_prime ÷ ad_spend

projected_cogs_kept
= 当前已确认保留货本 − 预计未来新增全损对应货本

projected_roi_breakeven
= projected_nc_prime ÷ (projected_nc_prime − projected_cogs_kept)
```

广告系统预测继续使用广告归因 GMV 分子，不与净收入混用：

```text
projected_ad_gmv = ad_gmv × (1 − projection_refund_amount_rate)
projected_ad_system_roi = projected_ad_gmv ÷ ad_spend
projected_ad_system_max_ad_spend = projected_net_revenue − cogs_total
projected_ad_system_breakeven_roi
= projected_ad_gmv ÷ projected_ad_system_max_ad_spend
```

广告消耗为 0 时预计 ROI 返回空值；保本分母小于等于 0 时相应保本 ROI 返回空值。
大盘金额和件数按唯一商品行聚合，退款金额结算样本订单数、已送达样本订单数、已送达
退款订单数、未结算订单数、未来全损风险暴露订单数和已确认风险暴露全损订单数按整体
范围全局去重，不能简单累加 SPU 行。

#### 2.6.5 预测状态

| 状态 | 条件 | 结果 |
| --- | --- | --- |
| `available` | 有未结算订单，且退款金额结算样本销售额、已送达全损样本订单数均大于 0 | 返回预测比例和预计结果 |
| `no_unsettled_orders` | 当前范围没有未结算订单 | 预计未结算净收入为 0，预计收入/利润/ROI 与当前值一致 |
| `insufficient_sample` | 有未结算订单，但缺少退款金额结算样本或已送达全损样本 | 可独立返回已有样本对应的比例，但完整预计结果返回空值，不得把缺失样本静默解释为 0% |

---

## 三、订单分类矩阵

| 类别 | 条件 | 计入有效销售? | 全损? | 计入采购货本? | 计入退款订单? |
| --- | --- | :---: | :---: | :---: | :---: |
| 有效订单 | 已付款白名单 + 无退款 case | ✓ | ✗ | ✓ | ✗ |
| 退款订单 | 已付款白名单 + 有已完结退款 case | ✗ | ✓（退货部分） | ✓（计入全损） | ✓ |
| 海外取消 | 已取消 + 物流已到目的国 | ✗ | ✓ | ✓（计入全损） | ✗ |
| 国内取消 | 已取消 + 物流未出境 | ✗ | ✗ | ✗ | ✗ |
| 未付款 / 挂起 | 未过支付门槛 | ✗ | ✗ | ✗ | ✗ |

**实现偏差注记（2026-09-13）**：代码实现里退货桶限定订单 ∈ 已付款白名单，与上表字面略有出入——原因是保住 rule 0 不变量：未付款等异常订单的完结退款走「未归属退款」计数，不进任何业务桶，也不应计全损货本。窗口裁剪统一跟随原订单：退货、退款金额/件数、退款订单数与海外取消都按订单 `COALESCE(order_time, paid_at)` 归属；售后完结时间只用于判断是否已完成及明细展示。例如 9 月 1 日订单在 9 月 10 日退款，退款仍归入 9 月 1 日。

---

## 四、参数与优先级链

### 4.1 采购成本（单价）取值

| 优先级 | 来源 | 说明 |
| ---: | --- | --- |
| 1 | 人工标注价格 | 运营手工录入的当前有效价 |
| 2 | 默认兜底价格 | 未命中人工标注时使用 40 CNY/件 |

妙手/1688 同步的货源单价可能是报价或采集价，不代表实际采购成交成本，因此不参与 SPU ROI 计算。

### 4.2 关键参数

| 参数 | 值 | 说明 |
| --- | --- | --- |
| 平台费率 $\hat{r}$ | 店铺实测（回退 30.8%） | 每 24h 按店铺近 180 天**未退款**已结算订单 Σ\|FEE\| ÷ **Σ行GMV**（行GMV = quantity × unit_price = 客户**实付**，不是折扣前挂牌价）重算为 `fee-v2`；窗口内有一单已结算即产出；无实测/过期时回退全局基线 30.8% |
| 未结算折算系数 | 1 − $\hat{r}$ | 随店铺实测费率浮动；回退基线时为 0.692 |
| 汇率 | 在线快照 | 原生广告 USD、销售/退款 VND 统一换算 CNY；使用同一 USD 基准快照的精确 `rates[CNY]` 与 `rates[VND]`，不用硬编码或舍入倒数恢复 |

**不要用过期常量**：USD/VND 26,330、CNY/USD 0.1477（已过期 ~1%）。

---

## 五、可复用分析 Prompt

按以下口径计算每个在售 SPU 的净利润、实际 ROI、保本 ROI，输出主表。

**有效销售订单**：已付款白名单状态，排除退货 case。

**退款订单**：有效销售订单中存在已完结退货退款/仅退款 case 的订单，按订单去重。

**全损件数**：退货（退货退款 / 仅退款已完结）+ 海外取消（已取消且物流已到目的国）；国内取消 ≠ 全损，不计货本。

**净收入**：已结算按实际到账 × (行 GMV / 订单 GMV) 分摊；未结算按行 GMV × 0.692 × (1 − 退款率)。

**采购成本合计**：采购成本（单价）= 当前有效的人工标注价格；未标注则兜底 40 CNY，妙手/1688 同步货源价不参与计算；合计 = (有效件数 + 全损件数) × CNY 单价。

**利润**：净利润 = 净收入 − 广告消耗 − 采购成本合计。

**ROI**：财务实际 ROI = (净收入 − 全损成本) ÷ 广告消耗；财务保本 ROI = (净收入 − 全损成本) ÷ (净收入 − 全损成本 − COGS_kept)。广告系统实际 ROI = 广告归因 GMV ÷ 广告实际消耗；最大可承受广告费 = 预计净结算收入 − 同范围采购成本 − 结算外必要成本；广告系统保本 ROI = 广告归因 GMV ÷ 最大可承受广告费。

**输出字段**：SPU | 广告¥ | 广告归因GMV¥ | 有效销售¥ | 有效单量 | 采购¥ | 退款率 | 全损率 | 取消率 | 净利润¥ | 财务实际ROI | 财务保本ROI | 广告系统实际ROI | 广告系统保本ROI | 剩余广告费承受空间¥

> 每个概念对应的数据表 / 字段 / 枚举值，查
> [`spu-roi-data-sources.md`](spu-roi-data-sources.md)。

---

## 六、文档关系

- [`spu-roi-data-sources.md`](spu-roi-data-sources.md)：本文所有概念在 **API 数据源**和 **plugin 数据源**里的表 / 字段 / 枚举映射（2026-09-15 新增，从本文拆出）
- `tech-doc/analytics/spu-real-roi-dashboard.md` §4.2：M1–M19 完整公式（技术版，含实现 SQL）
- `tech-doc/analytics/roi-calc-prompt.md`：可复用 Prompt（本文 §五同源）
- `handoff/spu-roi-full-loss-rubric.md`：项目记忆 v9 全损口径；**历史版本演进（v3–v9）与 2026-09-07 实测数字也在这里**——本文档不保留历史数据，防止旧版规则 / 时点数字被误当当前口径用于计算
- `biz-doc/analytics/post-product-list-field-semantics.md` §9：全损 SQL 查询
