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
- 金额底层按原币算（订单 VND / 成本 CNY / 广告 USD），**输出统一 USD**：
  CNY→USD = 0.1477（常量，实际以 _resolve_fx_rates 当期缓存 ~0.1485 为准，全程用同一个值）；
  USD→VND = 26,330。
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
  （cost_source=MANUAL）；未录入默认 30 CNY/件（DEFAULT_K1，行标 ⚠）。
- 广告成本：以用户给定总广告花费为准（USD）；ERP 归因 spend 仅作对照。
- 平台费基线 fee_rate = 30.8%（FEE_RATE_BASELINE，2026-09-06 实测重定，可覆写）。

## 4. ROI / 保本（ERP 财务口径，全 USD，来自 /v2/analytics/spu-roi 同源公式）
- sales = Σ paid 订单行金额（毛额，含之后被退款的原额）；refund_net = Σ 已完结
  REFUND_ONLY + RETURN_AND_REFUND case 行退款（订单∈有效销售）。
- net_cash = sales − refund_net                       # M13 内部量
- return_loss = RAR 已完结件数 × unit_cost_USD       # M13b（只算 RAR，不含到海外取消）
- NC′ = net_cash − return_loss
- COGS_all = 有效销售件数 × unit_cost_USD（含退回件，勿重复扣）；COGS_kept = (件数−RAR件)×unit_cost
- fee_est = sales × fee_rate
- 净利润 M18 = net_cash − COGS_all − 广告费 − fee_est
- 实际 ROI M14 = NC′ ÷ 广告费
- **保本 ROI M17 = NC′ ÷ (NC′ − COGS_kept − fee_est)**；分母 ≤ 0 → 结构性亏损、无保本线
- 判据：净利润 ≥ 0 ⟺ 实际 ROI ≥ 保本 ROI
- 若按用户运营口径（把到海外取消件也算货损）调整：净利润再减 `到海外取消件 × unit_cost`，
  保本线分子不变、分母再减该值。

## 5. GMVMax 商品ROI（平台口径，单独一套）
- 公式（用户给定）：商品ROI = 归因于进行中 GMVMax 计划的付费+自然销售额（不含直播订单）÷ 总广告费用。
- 注意分子是**毛归因 GMV**：可能含自然单、取消/退款单原额、COD 未收款 → 比 ERP"落袋"销售额
  虚高（实测单 SPU 可差 +30%）。
- 净贡献率 = (销售额 − 退款 − return_loss − COGS_kept − fee) ÷ 销售额
- **保本商品ROI = 1 ÷ 净贡献率**（= 销售额 ÷ 毛利贡献 C）；实测商品ROI = 归因销售额 ÷ 广告费。
- 倒推：保本所需归因销售额 = 保本商品ROI × 广告费；实际 − 所需 = 盈亏缺口（+赚/−亏）。
- 复核守则：平台显示 ROI ≥ 保本商品ROI 只说明"广告效率过线"；是否真赚，须把平台分子换成
  落袋口径（ERP 有效销售扣退款）或对账归因销售额构成后再判。

## 6. 输出主表（每 SPU 一行）
SPU | 采购成本 CNY/件 | 广告成本 USD | 总订单数 | 取消订单数(校正) | 取消率 |
全损退货数(件) | 全损率 | 有效销售额 USD | 保本商品ROI | 实际商品ROI |
保本所需归因销售额 | 差额 USD | 净利润 USD(落袋口径) | 判断(赚/亏)
附：GMVMax 口径只适用于分子分母同窗同源；直播单若存在从分子剔除。

## 7. 收尾校验清单
- [ ] TEST_ 数据已剔除；RAR 完结状态集合用的是全称（RETURN_OR_REFUND_REQUEST_COMPLETE）
- [ ] 到海外判定用的是 tracking 轨迹而非 shipped_at
- [ ] 汇率/平台费率全局一致；已在回答里写明用了哪个值
- [ ] 财务口径与运营口径的全损分开标注（RAR-only vs RAR+到海外取消）
- [ ] 与用户外部数（总订单数/广告费/归因GMV）不一致处显式列出并说明影响方向
````

## 与既有文档的关系

- **spu-real-roi-dashboard.md**：M13/M13b/M14/M17/M18/M19 的完整定义与决策记录（本 prompt §4 是其摘要）；
- **external-api.md**：`GET /v2/analytics/spu-roi` 的活契约与 sort 字段（roi_breakeven 等）；
- 汇率/费率等可配置常量以 `.env` / `db/constants.py` / `_resolve_fx_rates` 当期值为准。

## 已知坑（反复踩过）

1. `sales_orders.shipped_at` ≠ 货离仓：8 单标已发货但 tracking 停在 AWAITING_PICKUP 即被取消。
2. case_type 历史双拼写 `CANCELLATION` / `CANCEL`（旧）；`REFUND_ONLY` 与 `RETURN_AND_REFUND`
   是独立类型，勿归并；取消退款桶金额缺失（~219/246 行）——缺失行报"未知行数"不造数。
3. ROI 页"全损(货损)"只算 RAR 完结（系统口径）；用户运营口径会把"取消到海外"也算全损——
   两套并存，输出时必须标注用哪套，不要混。
4. 平台 GMVMax 商品ROI 分子是毛归因销售额，与 ERP 落袋口径可差 +30%（自然单/退款原额/COD），
   平台数超保本线 ≠ 账上赚钱，需换落袋分子复核。
