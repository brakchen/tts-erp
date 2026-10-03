# SPU 盈利（ROI / 利润）计算口径 — 唯一事实文档

> **当前口径版本：v10 —— 本文档所有章节共同构成 v10 口径整体，不存在"某一节是 v10、其他节是旧版"的子口径。**
> **本文档 = tts-erp 盈利口径计算的唯一事实文档（single source of truth）**：概念、公式、参数、
> 预测口径只在本文定义；其他文档只引用本文，不得另行定义或改写公式。任何文档与此处冲突，
> 以本文为准。
> 正文（§一–§五）只定义业务概念与公式；物理表 / 字段 / 枚举映射见**附录 A**
> （原 `spu-roi-data-sources.md`，2026-10-03 并入本文）。
> 历史演进与实测档案：[`docs/archive/spu-roi-full-loss-rubric.md`](../archive/spu-roi-full-loss-rubric.md)
> （v3–v9 版本史 + 时点数字，**非计算依据**）。
> 整理日期：2026-09-07；拆分纯化：2026-09-15；v10 更新：2026-10-07；口径类文档唯一化合并：2026-10-03

---

## Changelog

| 版本 | 日期 | 变更内容 |
| --- | --- | --- |
| v10 projection completed-order full loss | 2026-10-01 | 预计统一使用已完结订单全损率：分母为已结算、已送达或结果已确定的国内取消订单；分子为终局物流全损，以及到达海外/已送达后的最终全额退款订单。国内取消进入分母但不进入分子；同一比例预测风险订单数、件数和费后收入折损。 |
| v10 projection terminal-delivery risk | 2026-10-01 | 预计全损改用历史物流终态：配送失败并退回卖家订单 ÷（成功送达 + 配送失败退回卖家）；只作用于当前尚未送达订单，并以同一终态样本的销售额损失率调整预计净收入。 |
| v10 projection delivered-loss sample | 2026-10-01 | 退款金额率继续使用已结算财务样本；预测全损率改为“已送达且有已完成退款/退货退款的订单数 ÷ 全部已送达订单数”，与结算状态解耦。 |
| v10 projection delivery-aware | 2026-09-30 | 未结算订单因结算周期滞后但已送达时，不再进入未来全损风险暴露；送达证据包括订单已送达/已完成，或物流状态、送达时间、签收事件任一确认。退款金额预测仍覆盖全部未结算销售额。 |
| v10 projection | 2026-09-30 | 在不改变当前指标语义的前提下新增预测层：已结算样本分别计算退款金额率和已结算订单全损率；先估算整批未结算订单的终局退款/全损额度，再扣除已确认结果，只把差额作为未来新增；预计净利润按当前净利润加未结算净收入调整计算，并新增预计 ROI / 保本 ROI / 广告系统 ROI / 广告系统保本 ROI |
| v10 | 2026-10-07 | **大盘与 SPU 明细指标口径统一**：(1) 有效单量 = 有效订单 − 退款订单；(2) 退款数/退款率改为订单维度（退款订单数 / 全部订单）；(3) 全损量/全损率改为订单维度（退款订单 + 海外取消订单）；(4) 取消量/取消率 = 国内取消订单（排除海外取消）；(5) 新增实际保本ROI、广告系统实际ROI和广告系统保本ROI，结算外成本未结构化时标记 `estimated_known_costs`；(6) 每个 SPU 行与大盘使用同一公式；多 SPU 大盘的订单级事实独立全局去重，不能简单累加 SPU 行；(7) 前端只格式化后端结果；(8) 广告 USD、销售/退款 VND 在公式入口按同一汇率快照换算，所有金额统一以 CNY 计算和输出，比例不因换币改变 |
| v9 | 2026-09-15 | 全损 = 完结退货(不论物流) + 海外取消(38301)；国内取消 ≠ 全损 |
| v8 | 2026-09-15 | 广告消耗按日期窗口裁剪（与销售/退款同语义） |
| v7 | 2026-09-07 | 净收入分层（已结算用实际到账、未结算用估算费率） |
| 文档合并 | 2026-10-03 | 口径类文档唯一化：`spu-roi-data-sources.md` 并入附录 A；`shop-fee-rate-definition-gap.md`、`roi-calc-prompt.md` 的口径内容并入附录 B/C；旧 M 码对照见附录 D；其余口径旧文档移入 `docs/archive/` |

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

#### 2.6.1 已完结样本、全损分子与时间归属

预测使用**已完结订单样本**，生命周期终态与经济全损分开判断：

1. **已完结订单分母**：订单已经结算，或已经送达，或状态为 `CANCELLED` 且取消结果
   已确定。国内取消属于已完结，因此进入分母。
2. **终局物流全损**：`CANCELLED` 且物流事件 `80101` 明确表示配送失败后包裹退回卖家。
3. **海外全额退款全损**：商品已到达海外或已经送达，且最终退款金额达到订单 GMV。
   海外暴露证据包括订单/物流送达、`delivered_at`、`50101`，以及到达目的国事件 `38301`。
   跨境业务没有海外仓，这类商品无法重新入库和二次销售，因此直接进入全损分子；
   `REFUND_ONLY` 与 `RETURN_AND_REFUND` 不再区分。
4. **非全损完结**：国内取消即使全额退款也不进入全损分子；部分退款不按整单全损。
5. **未来全损风险暴露订单**：未结算且尚未确认送达的 paid 订单。已送达但结算滞后的
   订单已经越过未来拒收/配送失败风险窗口，不进入预测风险池。

最终退款金额取已结算 `CUSTOMER_REFUND` 与已完成售后退款金额中的较大值，避免同一笔
退款重复累计。预测样本和预测对象都按订单 `COALESCE(order_time, paid_at)` 落入
`w_start`/`w_end` 窗口；结算日和售后完成日不改变订单归属。

#### 2.6.2 唯一预测比例

$$
\text{completed\_full\_loss\_rate}
= \frac{\text{已完结全损订单数}}{\text{全部已完结订单数}}
$$

该比例同时预测待完结风险订单的未来全损订单数、件数和费后收入折损。wire 字段
`completed_full_loss_rate` 是当前权威预测字段。`projection_refund_amount_rate`、
`pre_delivery_full_loss_rate`、`delivered_full_loss_rate`、
`projection_full_loss_qty_rate` 与 `settled_full_loss_rate` 仅作为旧口径兼容诊断字段，
不得再驱动预计利润或 ROI。当前 `full_loss_rate` 继续展示当前事实，也不作为预测输入。

#### 2.6.3 未结算收入和全损预测

```text
expected_terminal_full_loss_orders
= full_loss_exposure_unsettled_order_count × completed_full_loss_rate

projected_future_full_loss_order_count
= max(expected_terminal_full_loss_orders
      − confirmed_full_loss_exposure_order_count, 0)

expected_terminal_full_loss_qty
= (confirmed_full_loss_exposure_qty
   + unresolved_full_loss_exposure_qty)
  × completed_full_loss_rate

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
expected_exposure_refund
= full_loss_exposure_unsettled_sales_after_fee × completed_full_loss_rate

projected_future_refund
= max(expected_exposure_refund
      − confirmed_full_loss_exposure_refund_after_fee, 0)

projected_terminal_refund
= confirmed_unsettled_refund_after_fee + projected_future_refund

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
projected_ad_gmv = ad_gmv
projected_ad_system_roi = projected_ad_gmv ÷ ad_spend
projected_ad_system_max_ad_spend = projected_net_revenue − cogs_total
projected_ad_system_breakeven_roi
= projected_ad_gmv ÷ projected_ad_system_max_ad_spend
```

待完结全损风险没有订单级广告归因映射，不能把风险率套到全部广告归因 GMV；因此
`projected_ad_gmv` 暂时保持当前 `ad_gmv`，只通过预计净收入改变广告系统保本分母。
广告消耗为 0 时预计 ROI 返回空值；保本分母小于等于 0 时相应保本 ROI 返回空值。
大盘金额和件数按唯一商品行聚合，已完结样本订单数、已完结全损订单数、未结算订单数、
未来全损风险暴露订单数和已确认风险暴露全损订单数按整体范围全局去重，不能简单累加
SPU 行。

#### 2.6.5 预测状态

| 状态 | 条件 | 结果 |
| --- | --- | --- |
| `available` | 有未结算订单，且已完结样本订单数大于 0 | 返回预测比例和预计结果 |
| `no_unsettled_orders` | 当前范围没有未结算订单 | 预计未结算净收入为 0，预计收入/利润/ROI 与当前值一致 |
| `insufficient_sample` | 有未结算订单，但缺少已完结样本订单 | 预计结果返回空值，不得把缺失样本静默解释为 0% |

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

> 每个概念对应的数据表 / 字段 / 枚举值，查**附录 A**。

---

## 六、文档关系

本文 = 利润口径（概念 / 公式 / 参数 / 预测）的唯一事实文档；实现、契约、历史文档只引用本文。

- **附录 A**：本文所有概念在 **API 数据源**和 **plugin 数据源**里的表 / 字段 / 枚举映射（原 `spu-roi-data-sources.md`）
- [`docs/design/spu-profitability-module.md`](../design/spu-profitability-module.md)：SPU 盈利 deep module 的接口与实现决策（**不定义口径**）
- [`docs/api/external-api.md`](../api/external-api.md)：`GET /v2/analytics/spu-roi` 等端点的 wire 契约与字段语义
- [`docs/business/post-product-list-field-semantics.md`](post-product-list-field-semantics.md) §9：全损 SQL 查询
- 历史档案（`docs/archive/`，**一律非计算依据**）：
  - [`spu-roi-full-loss-rubric.md`](../archive/spu-roi-full-loss-rubric.md)：v3–v9 版本史与 2026-09-07 实测数字
  - [`spu-real-roi-dashboard.md`](../archive/spu-real-roi-dashboard.md)：SPU ROI 看板历史设计稿（M1–M19 旧公式与现行代码不一致，勿作依据）
  - [`roi-calc-prompt.md`](../archive/roi-calc-prompt.md) / [`shop-fee-rate-definition-gap.md`](../archive/shop-fee-rate-definition-gap.md)：旧分析 prompt 与费率口径定位全过程（口径结论已并入附录 B/C）
  - [`spu-roi-v7-refactor.md`](../archive/spu-roi-v7-refactor.md) / [`spu-roi-v8-ad-window.md`](../archive/spu-roi-v8-ad-window.md)：v7 / v8 时代技术方案

---

## 附录 A：数据源映射（API 数据源 + plugin 数据源）

> 本附录回答：正文定义的每个业务概念在数据库里**到底是哪张表、哪个字段、什么枚举值**。
> 概念与公式的定义只在正文；本附录只做「概念 → 物理数据」映射。
> （原独立文档 `spu-roi-data-sources.md`，2026-10-03 并入本文。）

### 0. 总览：两条互斥的数据链路

店铺级互斥（同一店铺不会同时走两条路径）：

| 链路 | 判定依据 | 采集方式 | 落库 schema |
| --- | --- | --- | --- |
| **API 数据源** | `commerce.shops.credential_id IS NOT NULL` | sync-worker 定时调 TikTok Shop Open API | `commerce.*` / `finance.*` / `fulfillment.*` / `after_sales.*` |
| **plugin 数据源** | `commerce.shops.credential_id IS NULL` | Chrome 扩展拦截 Seller Center 页面响应，dump 上传 | `plugin.*` |

**三个例外（两路共用 / 交叉）**：

1. **广告消耗**没有 Open API 路径，两路都来自插件抓取 → `plugin.ad_*`（见 §3）
2. **汇率** `fx.*`、**采购成本** `procurement.*` 与订单来源无关，两路共用（见 §4）
3. 店铺注册表 `commerce.shops` 两路共享（插件店铺由运营人工注册，仅用于查询关联）

---

### 1. API 数据源（sync-worker → TikTok Open API）

> 链路细节：`docs/architecture/architecture-overview.md`；订单域规则：`docs/business/order-domain-business-rules.md`
> 当前实现：`tts_erp_v2/analytics/spu_profitability/`（v10，纯公式 `_formula_v10.py`）；HTTP adapter 为 `tts_erp_v2/analytics/spu_roi.py`

#### 1.1 概念 → 表/字段映射

| 业务概念 | 表.字段 | 说明 |
| --- | --- | --- |
| 订单 | `commerce.sales_orders` | `id` = 内部 PK（`order_pk`）；`order_id` = 平台订单号（text） |
| 订单状态 | `sales_orders.status` | text 枚举，见 §1.2 |
| 订单金额 | `sales_orders.payment_amount`（实付）/ `total_amount` | 店铺当地币种（VN = VND） |
| 订单时间 | `sales_orders.paid_at`（付款）/ `order_time`（下单）/ `cancelled_at` | 窗口裁剪用 `COALESCE(order_time, paid_at)`（下单时间优先） |
| 商品行 | `commerce.sales_order_lines` | `order_pk` FK；`spu_pk` 关联 SPU；`quantity` 件数 |
| 行 GMV | `sales_order_lines.quantity × unit_price` | 无独立列，计算得出 |
| 已结算判定 | `EXISTS (SELECT 1 FROM finance.settlement_transactions st WHERE st.order_pk = sales_orders.id)` | |
| 实际到账（SETTLEMENT） | `finance.settlement_components.amount`，`component_code = 'SETTLEMENT'`，经 `transaction_id = settlement_transactions.id` 关联 | 按订单 SUM 后按行 GMV 占比分摊 |
| 退货 / 退款售后单 | `after_sales.cases` + `after_sales.case_lines` | 枚举见 §1.3；`case_lines.quantity` = 退货件数，`refund_amount` = 退款额 |
| 售后单完结时间 | `cases.updated_at_source` | 只用于判断售后是否已完成及明细展示；退款窗口跟随关联订单的 `COALESCE(order_time, paid_at)` |
| 物流 | `fulfillment.shipments`（`order_pk`）→ `fulfillment.tracking_events`（`shipment_id`） | |
| 已到目的国判定 | `tracking_events.action_code = 38301` | integer 列，见 §1.4 |

#### 1.2 订单状态枚举（text）

定义在 `tts_erp_v2/db/constants.py`：

```python
PAID_SALES_ORDER_STATUSES = {   # 已付款白名单 = 有效销售订单口径
    "AWAITING_SHIPMENT", "PARTIAL_SHIPPING", "AWAITING_COLLECTION",
    "IN_TRANSIT", "DELIVERED", "COMPLETED",
}
UNPAID_SALES_ORDER_STATUSES = {"UNPAID", "ON_HOLD", "CANCELLED"}
```

生命周期：`AWAITING_SHIPMENT → AWAITING_COLLECTION → IN_TRANSIT → DELIVERED → COMPLETED`，
任意阶段可 → `CANCELLED`。绝对终态 = `COMPLETED` / `CANCELLED`。无 `REFUNDED` 状态（退款不改订单状态）。

#### 1.3 售后单枚举（`after_sales.cases`）

| 字段 | 枚举 | 说明 |
| --- | --- | --- |
| `case_type` | `RETURN_AND_REFUND` / `REFUND_ONLY` / `CANCELLATION` | 前两个 = 正文定义的「退货」；`CANCELLATION` 只进退款拆分，不进全损 |
| `status`（完结） | `RETURN_OR_REFUND_REQUEST_COMPLETE` / `CANCELLATION_REQUEST_COMPLETE` | 代码常量 `_CASE_COMPLETED_STATUSES`；退货桶只用前者 |

#### 1.4 物流 action_code 速查（`fulfillment.tracking_events.action_code`）

| code | 含义 | 口径用途 |
| ---: | --- | --- |
| **38301** | Arrived in destination country/region | **海外取消 = 全损的判定** |
| 50101 | Delivered（签收） | 物流终态 |
| 80101 | Returned to seller | 物流终态 |
| 110101 | Delivery canceled | 物流终态 |

#### 1.5 结算费用构成（验证用，`finance.settlement_components.component_code`）

`SETTLEMENT`（卖家实际到账，v9 净收入用）、`PLATFORM_COMMISSION`、`AFFILIATE_COMMISSION`、
`SHIPPING_FEE`、`CUSTOMER_REFUND`。

#### 1.6 预计终局预测事实

预测没有新增表或持久化快照，`GET /v2/analytics/spu-roi` 在同一个一致性读快照内，
按当前 SPU/店铺和日期范围实时聚合：

| 预测概念 | 物理来源 | 聚合规则 |
| --- | --- | --- |
| 已结算样本订单 | `finance.settlement_transactions` + `finance.settlement_components` | 存在 `component_code='SETTLEMENT'` 才有可用实际到账；订单数按 `order_pk` 去重 |
| 样本销售额/件数 | `commerce.sales_order_lines.quantity × unit_price` / `quantity` | 只取上述已结算 paid 订单商品行 |
| 样本退款金额/全损件数 | 已完结 `after_sales.cases` + `case_lines.refund_amount/quantity` | 仅 `REFUND_ONLY` / `RETURN_AND_REFUND`；先按 `sales_order_line_id` 合并，确认件数封顶到原行件数 |
| 未结算订单 | paid 订单行不存在可用 `SETTLEMENT` component | 只对这类订单计算预计终局；已结算到账不再折减 |
| 已确认未结算退款/全损 | 未结算订单关联的上述已完结售后商品行 | 退款金额按已知值扣一次；确认件数从待预测件数排除 |
| 待确认未结算件数 | `sales_order_lines.quantity − confirmed_case_qty` | 每行下限为 0；跨 SPU 大盘的订单数另做 `count(DISTINCT order_pk)` |
| 待确认未结算销售额 | `unresolved_qty × sales_order_lines.unit_price` | 部分退款行按剩余件数比例保留销售额 |
| 已观察海外取消全损 | `sales_orders.status='CANCELLED'` + `tracking_events.action_code=38301` | 沿用 v9 当前事实，进入终局已观察全损和现有取消全损 COGS；不进入 paid 已结算预测样本 |
| 单位成本 | `procurement.manual_product_costs`，缺失回退 40 CNY | 预计全损成本和预计保留货本逐 SPU 使用各自单位成本，大盘不能用一个混合单价 |
| 平台费率 | 页面覆写 / `reporting.shop_fee_rate_estimates` fee-v2 / 0.308 | 只作用于未结算收入；大盘逐店/逐 SPU 折算后聚合 |

样本、预测对象、退款和全损都继续按订单
`COALESCE(order_time, paid_at)` 归属页面 `w_start`/`w_end` 窗口，不按结算时间或售后完成
时间切窗。因此改变两个页面的日期选择会改变预测样本、预测对象和预测结果。

---

### 2. plugin 数据源（Chrome 扩展拦截 Seller Center）

> 取数口径权威文档：`docs/api/dumps-data-contract.md`（endpoint 清单 / dump 协议 / 4 域关联；原 intercept-plugin-canonical.md 已并入该契约）
> 核心原则：`plugin.raw_log` 是 source-of-truth，业务表是 derived view，所有业务表 `log_id` FK 可溯源到具体 dump。

#### 2.1 endpoint → 表

| 来源 endpoint | 落库表 | 内容 |
| --- | --- | --- |
| `POST /api/fulfillment/order/list` | `plugin.orders` + `plugin.order_lines` | 订单头 + SKU 行（sku_module[]） |
| `POST /logistic_detail/list` | `plugin.shipments` + `plugin.tracking_events` | 包裹 + 轨迹事件 |
| `POST /api/v1/pay/statement/order/list?settlement_status=1` | `plugin.settlements` | 结算单头 |
| `POST /api/v1/pay/statement/transaction/detail` | `plugin.settlement_details` | SKU 级费用明细 |
| `POST /return_refund/202309/cancellations/search` | `plugin.after_sales` + `plugin.after_sale_items` | 售后/取消单头 + 行项目 |

#### 2.2 概念 → 表/字段映射

| 业务概念 | 表.字段 | 原始响应字段 | 说明 |
| --- | --- | --- | --- |
| 订单 | `plugin.orders.order_id` | `main_order_id` | text 平台订单号 |
| 订单状态 | `plugin.orders.main_order_status` | `main_order_status` | **int 码**，枚举见 §2.3 |
| 订单金额 | `orders.payment_amount` / `total_amount` | `price_module.grand_total.price_val` / `sub_total` | |
| 订单时间 | `orders.order_time` | `time_order_module.create_time`（秒级字符串） | |
| 商品行 | `plugin.order_lines` | `sku_module[]` | `quantity` 件数 |
| 行 GMV | `order_lines.total_price`（或 `quantity × unit_price`） | `sku_total_price` / `sku_unit_price.price_val` | |
| 行级状态 | `order_lines.main_order_status` / `sku_display_status` | 同名 | int 码 |
| 物流包裹 | `plugin.shipments` | package_list[] | `order_id → package_id`、`tracking_number`、`carrier_name` |
| 物流轨迹 | `plugin.tracking_events` | track_list[] | ⚠ 见 §2.4 缺口 |
| 售后/取消单 | `plugin.after_sales` | cancellations[] | `main_order_id` 关联订单；枚举见 §2.5 |
| 售后行项目 | `plugin.after_sale_items` | cancel_line_items[] | `quantity`、`refund_amount`，支持部分取消 |
| 结算单头 | `plugin.settlements` | statement list | statement 级：`settle_amount` / `earning_amount` / `fee_amount` / `payment_status` |
| 结算明细 | `plugin.settlement_details` | transaction detail | SKU 级；**`trade_order_id` = 订单号，是结算 → 订单的关联键** |
| 已结算判定 | `plugin.settlement_details` 存在该 `trade_order_id` 的记录（`settlement_status` / `settlement_time` 辅助） | | 与 API 侧「存在结算记录」同义 |
| 实际到账 | `settlement_details.settlement_amount`（或 `earning_amount`）按 `trade_order_id` 汇总 | | 费用拆分在 `fee_components` jsonb |

#### 2.3 订单状态码（`main_order_status`，int）

raw 响应**只有 int 码，无文本枚举**。以下为 2026-09-14 prod 494 单实测交叉验证
（`docs/api/dumps-data-contract.md` §3.5+；原 intercept-plugin-canonical.md）：

| 码 | 推断文本态 | 置信度 |
| ---: | --- | --- |
| 100 | UNPAID（待付款） | 中 |
| 101 | AWAITING_SHIPMENT（待发货） | 中（待 Seller Center tab 终验） |
| 102 | 已发货 / 运输中（IN_TRANSIT 一类） | 中（待终验） |
| 103 | 售后 / 退货中 | 高（7/7 伴随 reverse_type=3） |
| 104 | CANCELLED | 高（86/86 伴随 reverse_type=4） |

逆向信号另一来源：订单 dump 的 `reverse_module[]`（**未结构化，在 `plugin.raw_log.response_body`**），
`reverse_type` 3 = 退货 / 4 = 取消（⚠ 样本推断待核实）。

#### 2.4 ⚠ 已知缺口：plugin 侧无 `action_code` 结构化列

`plugin.tracking_events` 只有 `event_key / event_at / description(=track_status) / location`，
**没有 `action_code` 列**——raw 里的 action_code 只被拼进 `event_key` 复合串。
因此 「海外取消 = 已取消 ∧ action_code=38301」在 plugin 侧**无法直接套 SQL**，需要：
回 `plugin.raw_log.response_body` 重解析，或解析 `event_key`，或给 `plugin.tracking_events` 补列（TODO）。

#### 2.5 售后单枚举（`plugin.after_sales`）

| 字段 | 枚举 | 说明 |
| --- | --- | --- |
| `cancel_type` | `BUYER_CANCEL` / `CANCEL` | 买家取消 / 系统·卖家取消 |
| `cancel_status` | `CANCELLATION_REQUEST_COMPLETE` | 完结（实测全部为此值） |

#### 2.6 其他已知缺口 / TODO

- 101 / 102 码值待 Seller Center 页面 tab 对照终验
- 售后解析器字段名基于设计文档假设，chrome 扩展尚未抓到过真实响应（0 hit），首次真实响应后需校准
- 时间字段解析曾踩坑：raw 是数字字符串（秒/毫秒/微秒混合），字段级解析失败不落 `parse_error`，只看 `log.warning`（2026-09-14 教训）

---

### 3. 广告数据源（两路共用，只有插件一条采集路径）

| 表 | 用途 |
| --- | --- |
| `plugin.ad_daily` | 历史天级，**当前 SPU ROI 唯一取数源**（v8.1 起，按日切片） |
| `plugin.ad_today` | 今天实时（30s 滚动）；暂不进 ROI 取数路径 |
| `plugin.ad_monthly` | 月级聚合 |
| `plugin.ad_raw_log` | 原始 dump（source-of-truth） |

关键字段（沿用 TikTok 原名）：`mixed_real_cost` = 真实消耗；`onsite_roi2_shopping_sku` = 出单量；
`onsite_roi2_shopping_value` = 出单 GMV。SPU 关联：`product_id` → `commerce.products_spu.spu_id`
（经 `commerce.shops.shop_id = seller_id`）。endpoint 过滤
`'/oec_ads/shopping/v1/oec/stat/post_product_list'`。

> ⚠ 历史注记：`analytics.ad_product_links` 视图（migration 0006/0014）已于 migration 0020 删除；
> `docs/archive/ad-product-links-view.md` 为历史档案，勿再作为取数依据。
> 已知窗口缺口：merge job 2026-09-13 禁用后 `ad_daily` 未增，09-14+ 的广告消耗为 0（P0 follow-up）。

### 4. 汇率与采购（两路共用）

| 业务概念 | 表.字段 | 说明 |
| --- | --- | --- |
| 汇率快照 | `fx.exchange_rate_snapshots` + `fx.exchange_rates` | 在线快照，禁用过期硬编码常量；详见 `docs/design/fx-exchange-rates.md` |
| 人工标注成本（优先级 1） | `procurement.manual_product_costs.unit_cost`，`valid_to IS NULL` = 当前有效 | 按 `spu_pk` |
| 兜底（优先级 2） | 硬编码 40 CNY/件 | 未命中人工标注时使用 |

`procurement.procurement_products.source_unit_cost` 保存的妙手/1688 同步货源价不参与 SPU ROI 采购成本计算。

### 5. 锚点

| 类型 | 位置 |
| --- | --- |
| 概念/公式定义 | 本文正文（v10） |
| API 侧实现 | `tts_erp_v2/analytics/spu_profitability/`（v10 实现）+ `tts_erp_v2/analytics/spu_roi.py`（adapter） |
| 状态枚举代码 | `tts_erp_v2/db/constants.py` |
| plugin 解析器 | `tts_erp_v2/plugin/orders/parser.py` |
| plugin 取数口径 | `docs/api/dumps-data-contract.md`（原 intercept-plugin-canonical.md 已并入该契约） |
| 订单域业务规则 | `docs/business/order-domain-business-rules.md` |

---

## 附录 B：平台费率 r̂ 口径定位与反证（摘要）

> 定位全过程与生产反证明细见 [`docs/archive/shop-fee-rate-definition-gap.md`](../archive/shop-fee-rate-definition-gap.md)
> （2026-09-29 定案）；复现脚本 `scripts/probe_shop_fee_rate_definition.py`。

**最终口径（v10 §四同源）**：`r̂ = Σ|FEE| ÷ Σ line_gmv`，**只统计未退款（kept）订单**，
单店铺 + 近 180 天，由 `analytics.shop_fee_rate` 任务每 24h 写 `fee-v2` 日快照。

四件事必须同时对（错一层就得到 ~21% 或 ~12% 的假费率）：

| # | 项 | 错的做法 | 对的做法 | 依据 |
| --- | --- | --- | --- | --- |
| 1 | 分子取哪个 component | `PLATFORM_COMMISSION`（抽佣分项，约占一半） | **`FEE`**（交易级平台总扣除 = 抽佣 + 联盟 + 运费类） | 逐单恒等式 `SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND`（中位残差 0.000%）；`FEE` 已含运费类，**不可再叠加**运费分项 |
| 2 | 分母用什么 | `GROSS_SALES`（折扣前挂牌价，实测 = 行 GMV 的 169%） | **行 GMV = `quantity × unit_price` = 客户实付（折扣后）** | Σ 行 GMV 与结算单 `CUSTOMER_PAYMENT` 只差 0.24% |
| 3 | 样本 population | 窗口内全部已结算订单 | **只统计未退款（kept）订单** | 本公式另有 `(1 − 退款率)` 扣一次退款；样本混入退款订单会把退款效应算两遍 |
| 4 | 作用域 | 全局或跨店混算 | 单店铺 + 近 180 天，`fee-v2` 快照；`fee-v1-legacy` 不可用于计算 | 每 24h 重算；窗口内有一单已结算即产出（`MIN_ELIGIBLE_ORDER_COUNT = 1`） |

**生产反证**（把已结算订单当作未结算来预测，与真实 ΣSETTLEMENT 比，近 180 天）：

| shop_pk | 真实 SETTLEMENT | kept 口径预测 | 全部口径预测 | kept 误差 | 全部误差 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 314 | 234,747,738 | 233,401,405 | 271,139,041 | **−0.6%** | **+15.5%** |
| 68234 | 76,295,977 | 77,046,204 | 88,999,139 | **+1.0%** | **+16.6%** |

**分组实测**（近 180 天，两店合计）：kept 757 单费率 **32.10%**（逐店 32.30% / 31.48%）、
全额退款 444 单 3.19%、部分退款 3 单 16.95%。与全局基线 **30.8%** 同一量级 ⇒
**基线 0.308 保留**（无实测/快照过期店铺的兜底，2026-09-29 用户拍板）。

- `kept_share = Σ 行GMV(kept) ÷ Σ 行GMV(窗口内全部已结算)` 仅供观测，**不作门槛**。
- 2026-09-29 曾一度判定“30.8% 与公式对不上”，那是只查了分子/分母两层、未查样本 population 的误判；
  当时的 21% 是混合样本产物，不是真值。
- 遗留（不影响 r̂）：`finance.settlement_components` 有 249 单完全没有分项（近 180 天，占 line_gmv 17.4%），
  是分项落库缺口而非数据不存在；修复入口 `scripts/oneoff_regen_finance_components.py`
  （注意其旧约定“跳过零值”与当前 job“显式落零”不一致）。

---

## 附录 C：避坑清单与计算复核清单

沉淀自历史分析记录（[`docs/archive/roi-calc-prompt.md`](../archive/roi-calc-prompt.md)）与生产实测，
均为口径层面反复踩过的坑：

1. **`sales_orders.shipped_at` ≠ 货离仓**：实测有订单标了已发货但物流从未揽收即被取消。
   「货是否到目的国」只能看 `fulfillment.tracking_events`，不得用 `shipped_at`。
2. **行级退款金额缺失不造数**：历史取消桶仅 27/246 行有金额；缺失行如实报
   「已知金额小计 + 未知行数」，不得按 case 级金额或占比倒推补数。
3. **平台 GMV Max 商品 ROI ≠ 账上赚钱**：广告系统口径分子是毛归因销售额（含自然单、
   退款原额、COD 未收款），比财务落袋口径可高约 30%。平台数超保本线必须用财务 ROI 复核。
4. **广告赠金不得混入实际消耗**；退货运费、提现费、汇兑损失等结算外成本缺数据时列风险，
   不得静默填 0（见正文 §2.3 `estimated_known_costs`）。
5. **样本 < 10 单的 SPU，比率只标注“样本小”**，不作强结论。
6. **禁用过期汇率常量**：USD/VND 26,330、CNY/USD 0.1477（已过期约 1%）；一律用数据库 fx 快照（正文 §四）。

计算复核清单（对外报数 / 让 agent 代算时逐项过）：

- [ ] `TEST_` 哨兵数据已剔除；售后完结状态用全称 `RETURN_OR_REFUND_REQUEST_COMPLETE`
- [ ] 「到海外」判定用 tracking 轨迹而非 `shipped_at`
- [ ] 汇率与平台费率全局一致，且在结果里写明实际取值
- [ ] 订单、结算、售后、采购与广告数据的日期范围相同或可对齐；退款/全损按订单 `COALESCE(order_time, paid_at)` 归属
- [ ] 已结算、未结算、订单、采购与广告的口径分开标注；预计终局字段不得标为“实际”
- [ ] 已确认的未结算退款/全损不重复预测；预计新增全损不重复计入当前 COGS
- [ ] 财务 ROI 与广告系统 ROI 分列；结算外必要成本与广告赠金单独列示，缺失数据只列假设/风险
- [ ] 与用户外部数（订单数/广告费/归因 GMV）不一致处显式列出并说明影响方向

---

## 附录 D：历史 M 码对照（仅解释遗留引用）

`M1–M19` 是历史设计稿（[`docs/archive/spu-real-roi-dashboard.md`](../archive/spu-real-roi-dashboard.md)）
给指标起的编号，仍散见于代码注释、UI tooltip 与旧文档。**现行口径与 wire 字段以正文 §2.5 和
[`docs/api/external-api.md`](../api/external-api.md) 为准**；本附录只解释遗留引用，防止误读旧公式：

| 历史 M 码 | 含义 | v10 现行对应 |
| --- | --- | --- |
| M13 | 净现金收入（内部中间量，不展示） | `sales − refund_net`，仅作内部推导 |
| M13b | 全损货损成本 | 全损口径见正文 §一（订单维度：退款订单 + 海外取消订单）；成本进 `cogs_full_loss_cancelled` / `return_loss` |
| M14 | 实际 ROI | `items[].roi_real` = NC' ÷ 广告消耗 |
| M17 | 保本 ROI | `items[].roi_breakeven` = NC' ÷ (NC' − COGS_kept)。⚠ 旧稿公式 `NC' ÷ (NC' − COGS_kept − fee_est)` **已废弃**，与现行代码 `tts_erp_v2/analytics/spu_profitability/_formula_v10.py` 不符，勿再引用 |
| M18 | 净利润 | `items[].net_profit`（正文 §二） |
| M19 | 未结算平台费估算 | `r̂ × 未结算销售额`（附录 B / 正文 §四） |
| M12 / M12d | 退款金额率 / 退款率 | `refund_amount_rate`（解释字段）/ `refund_rate`（主口径，正文 §2.5） |
| M5d | 全损件数 | 解释字段 `full_loss_qty_rate` 对应的件数口径；主口径为订单维度 `full_loss_rate` |
| M4 / `roi_l0` | 平台 GMV ROI | `ad_system_actual_roi` 的历史别名（正文 §2.5） |
