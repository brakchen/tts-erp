# ROI 计算口径与可复用 Prompt（tech-doc 分析用）

> 目的：把「取消/全损判定 + 采购/广告成本 + 保本 ROI + GMVMax 商品ROI」的**统一口径**
> 沉淀成一段可直接喂给 AI agent 的 prompt，避免每次分析各算各的。
> 底层权威文档：`tech-doc/analytics/spu-real-roi-dashboard.md`（M 码/公式/决策记录，
> 口径改动以它为准）；本文件只收录跨会话一致的计算约定与坑。
> 整理日期：2026-09-07（会话梳理 2026-09-06 的分析结论）。

**用法**：把下方代码块整段拷给分析型 agent（或人工执行步骤对照），要求它按 §0–§7 输出主表。

````markdown
# 任务：按固定口径计算 SPU 级订单健康与 ROI 指标

你是一个数据分析助手，操作 tts-erp 本地库（Postgres，SQLAlchemy URL 取 .env TTS_ERP_DB_URL），
按下方口径精确计算每个在售 SPU 的：总订单数、取消订单数/率、全损退货数/率、
采购成本、广告成本、保本ROI、保本商品ROI、实际商品ROI，并输出主表。

## 0. 通用规则
- 在售 SPU = `commerce.products_spu.status='ACTIVATE'`；**剔除 spu_id LIKE 'TEST\_%'**（哨兵测试数据）。
- 时间一律 aware UTC；统计日界按店铺当地时区（当前越南 = UTC+7，即 paid_at − 7h 取 date）。
- 金额原始输入分别为订单 VND / 成本 CNY / 广告 USD；必须从同一数据库 USD 基准汇率快照取得 `rates[VND]` 与精确 `rates[CNY]`，按 `USD × rates[CNY]`、`VND ÷ (rates[VND]/rates[CNY])` 在公式入口统一换算为 **CNY** 后计算和输出。禁止硬编码汇率或通过舍入倒数恢复 CNY rate；汇率不可用时失败关闭。
- 件数/单数不混：`_订单数` 用 count(DISTINCT order)；`_件数` 用 Σ line quantity。
- 样本 <10 单的 SPU，比率标注"样本小"不作强结论。

## 1. 订单域口径
- 有效销售订单（paid 白名单）∈ {AWAITING_SHIPMENT, PARTIAL_SHIPPING, AWAITING_COLLECTION,
  IN_TRANSIT, DELIVERED, COMPLETED}（COD 在途未收款也算，2026-09-06 状态口径）；
  总订单数 = 该 SPU 的 paid∪CANCELLED distinct 订单；CANCELLED 不计销售但计 GMV 原额。
- **总订单数可被用户外部口径覆写**（用户提供 "SKU 订单数" 时以用户数为分母）。

## 2. 取消 / 全损（用户 2026-09-06 拍板口径）
- 取消订单数（校正）= status='CANCELLED' **且货未到海外**的单；
  "货到海外"判定只看 `fulfillment.tracking_events`（**不要用 sales_orders.shipped_at**——
  实测有单标了 shipped 但物流从未揽收即被取消）：
  - 到海外 ✓：出现越南段节点 = 38301 Arrived in Vietnam / 34701 Import clearance completed /
    30801 handed to local carrier / 越南地名（Xã/Phường/Quận/Huyện/Thành phố…）/
    派送动作（will be delivered / delivery attempt / couldn't be delivered）/
    60201 退回本地仓 / returned to the seller in <越南仓：Phường Tam Sơn、Từ Sơn、Đồng Nguyên…>；
  - 未到海外 ✗：仅国内段（Order placed / packed awaiting pickup / delivery canceled /
    export clearance / departed origin / awaiting international departure from PingXiang、AiDian）。
- 全损退货数（运营口径，进主表）= **RAR 已完结件数 + 取消单中发货到海外件数**：
  RAR 已完结 = `after_sales.cases.case_type='RETURN_AND_REFUND'` 且
  `status='RETURN_OR_REFUND_REQUEST_COMPLETE'`、订单 ∈ 有效销售（妥投后退 = 货收不回）；
  到海外取消件 = 上述 CANCELLED 单里 tracking 有越南节点的该 SPU 行件数。
  未完结 RAR（如 BUYER_SHIPPED_ITEM）单列"在途潜在全损"，不计入。
- 取消率(校正) = 取消订单数(未到海外) ÷ 总订单数。
- 全损率 = 全损件数 ÷ (有效销售件数 + 到海外取消件数)。

## 3. 成本输入
- 采购成本 CNY/件：读 `procurement.manual_product_costs WHERE valid_to IS NULL`
  （cost_source=人工标注价格）；未录入默认 40 CNY/件（默认兜底价格，行标 ⚠）。
- 广告成本：原生 USD，以用户给定总广告花费为准；进入公式后按同一快照换算为 CNY。ERP 归因 spend 仅作对照。
- 平台费基线 fee_rate = 30.8%（`FEE_RATE_BASELINE`，2026-09-06 实测重定，可覆写）。

### 3.1 平台佣金费率 r̂ 的完整口径（务必按此，勿凭直觉）

公式：`unsettled_net = unsettled_sales × (1 − r̂) × (1 − 退款率)`

四件事必须同时对：

1. **分子 = `FEE`**（交易级 `fee_amount`，平台总扣除：抽佣 + 联盟 + 运费类）。
   **不是** `PLATFORM_COMMISSION`（那只是抽佣分项，约占一半）。
   → 等价地：`FEE` 已含运费类，**不可再叠加** `shipping_fee` / `actual_shipping_fee`
   / `shipping_cost`（会重复扣）。
2. **分母 = 行GMV**（`sales_order_lines.quantity × unit_price`）= **客户实付（折扣后）**。
   **不是** `gross_sales_amount` —— 那是**折扣前挂牌价**，实测是行GMV 的 **169%**
   （= `AFTER_SELLER_DISCOUNTS_SUBTOTAL` + `|SELLER_DISCOUNT|`）。
   验证：Σ行GMV 与结算单 `CUSTOMER_PAYMENT` 只差 0.24%。
3. **样本 = 只统计未退款(kept)订单**（`CUSTOMER_REFUND = 0`）。
   因为本公式已另有 `(1 − 退款率)` 扣过一次退款；若 r̂ 的样本里再混入全额退款
   订单（其费率仅 ~3% of 行GMV），退款效应被**算两遍**。
4. **作用域** = 单店铺 + 近 180 天（由 `analytics.shop_fee_rate` 任务每 24h 快照）。

生产反证（把已结算订单当作未结算来预测，与真实 ΣSETTLEMENT 比）：
kept 口径误差 **±1%**；混合口径（含退款单）高估 **~16%**。

逐单恒等式（中位残差 0.000%）：`SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND`

参考量级：kept 口径实测 **32.10%**（两店合计）/ 32.30% / 31.48% —— 与文档长期
记载的基线 **30.8% 一致**。若你算出的值明显偏离 ~30%（例如 ~21% 或 ~12%），
先怀疑自己搞错了上述四点其中之一，**不要先怀疑 30.8% 是错的**。

口径定位全过程与反证数据：`shop-fee-rate-definition-gap.md`。

## 4. ROI / 保本（ERP 财务口径，全 CNY，来自 /v2/analytics/spu-roi 同源公式）
- sales = Σ paid 订单行金额（毛额，含之后被退款的原额）；refund_net = Σ 已完结
  REFUND_ONLY + RETURN_AND_REFUND case 行退款（订单∈有效销售）。
- net_cash = sales − refund_net                       # M13 内部量
- return_loss = RAR 已完结件数 × unit_cost_CNY       # M13b（只算 RAR，不含到海外取消）
- NC′ = net_cash_CNY − return_loss
- COGS_all = 有效销售件数 × unit_cost_CNY（含退回件，勿重复扣）；COGS_kept = (件数−RAR件)×unit_cost_CNY
- fee_est = sales × fee_rate
- 净利润 M18 = net_cash − COGS_all − 广告费 − fee_est
- 实际 ROI M14 = NC′ ÷ 广告费
- **保本 ROI M17 = NC′ ÷ (NC′ − COGS_kept − fee_est)**；分母 ≤ 0 → 结构性亏损、无保本线
- 判据：净利润 ≥ 0 ⟺ 实际 ROI ≥ 保本 ROI
- 若按用户运营口径（把到海外取消件也算货损）调整：净利润再减 `到海外取消件 × unit_cost`，
  保本线分子不变、分母再减该值。

## 5. 广告系统 ROI（TikTok 后台口径，单独一套）
- 广告系统实际 ROI = 广告归因 GMV ÷ 广告实际消耗。
- 最大可承受广告费 = 预计净结算收入 − 同范围采购成本 − 净结算中尚未包含的其他必要成本。
- 广告系统理论保本 ROI = 广告归因 GMV ÷ 最大可承受广告费；分母 ≤ 0 或无归因 GMV → 无可用保本线，不得填 0。
- 预计净结算收入优先使用已结算实际到账 + 可靠的未结算净额估算；到账金额已扣的平台佣金、联盟佣金、VAT、交易费、平台物流费不得重复扣除。
- 采购成本必须覆盖与收入同范围的待发货/在途/已完成订单及海外取消全损件，禁止计收入漏货本。
- 退货运费、采购退款失败、提现费、汇兑损失、包装耗材等仅在未包含于净结算时另扣；缺数据必须列为风险，不得静默填 0。
- 剩余广告费承受空间 = 最大可承受广告费 − 当前广告实际消耗；负值表示已越过保本预算。
- 建议最低安全 ROI 必须在理论保本 ROI 上另留缓冲并明确规则。历史单店参考是理论约 2.61、最低安全线 2.70、相对稳妥线 2.80；`2.39` 已撤回，禁止使用。这些历史值不是跨店铺硬编码常量。
- GMV Max 分子可能含自然单、取消/退款单原额、COD 未收款，且广告赠金不得混入实际消耗。必须分别说明会计利润口径与现金投入回报口径。
- 当前 ERP 接口因结算外必要成本尚未结构化，返回 `estimated_known_costs` 和 warning；页面用 `≈` 标记“已知成本下限估算”，不得冒充最终保本线。

## 6. 输出主表（每 SPU 一行）
SPU | 采购成本 CNY/件 | 广告实际消耗 CNY | 广告归因GMV CNY | 广告系统实际ROI |
预计净结算收入 CNY | 同范围采购成本 CNY | 结算外必要成本 CNY | 最大可承受广告费 CNY |
广告系统理论保本ROI | 建议最低安全ROI | 剩余广告费承受空间 CNY |
总订单数 | 取消率 | 退款率 | 全损率 | 当前净利润 CNY | 口径/假设/风险
附：广告分子、广告消耗、结算、订单与采购必须同窗或可对齐；赠金单列，不混入实际消耗。

## 7. 收尾校验清单
- [ ] TEST_ 数据已剔除；RAR 完结状态集合用的是全称（RETURN_OR_REFUND_REQUEST_COMPLETE）
- [ ] 到海外判定用的是 tracking 轨迹而非 shipped_at
- [ ] 汇率/平台费率全局一致；已在回答里写明用了哪个值
- [ ] 财务口径与运营口径的全损分开标注（RAR-only vs RAR+到海外取消）
- [ ] 与用户外部数（总订单数/广告费/归因GMV）不一致处显式列出并说明影响方向
- [ ] 已结算、未结算、订单、采购与广告数据的日期范围相同或可对齐
- [ ] 待发货/在途预计收入都有对应采购成本；国内取消可追回货不计货损，海外取消按出境证据判全损
- [ ] 结算外必要成本和广告赠金单独列示；缺失数据只列假设/风险，不自行补数
- [ ] 未成熟订单分别给出当前已实现、全部正常完成、考虑取消/退货率后的保守预测
- [ ] 同时列出广告后台实际 ROI、理论保本 ROI、安全线、利润、距保本差额和剩余广告费承受空间
````

## 与既有文档的关系

- **spu-real-roi-dashboard.md**：M13/M13b/M14/M17/M18/M19 的完整定义与决策记录（本 prompt §4 是其摘要）；
- **external-api.md**：`GET /v2/analytics/spu-roi` 的活契约与 sort 字段（roi_breakeven 等）；
- 汇率取数据库最新可用快照，以 `spu_profitability._resolve_fx_basis` 当期值为准；费率以领域配置/页面覆写为准。

## 已知坑（反复踩过）

1. `sales_orders.shipped_at` ≠ 货离仓：8 单标已发货但 tracking 停在 AWAITING_PICKUP 即被取消。
2. case_type 历史双拼写 `CANCELLATION` / `CANCEL`（旧）；`REFUND_ONLY` 与 `RETURN_AND_REFUND`
   是独立类型，勿归并；取消退款桶金额缺失（~219/246 行）——缺失行报"未知行数"不造数。
3. ROI 页"全损(货损)"只算 RAR 完结（系统口径）；用户运营口径会把"取消到海外"也算全损——
   两套并存，输出时必须标注用哪套，不要混。
4. 平台 GMVMax 商品ROI 分子是毛归因销售额，与 ERP 落袋口径可差 +30%（自然单/退款原额/COD），
   平台数超保本线 ≠ 账上赚钱，需换落袋分子复核。
