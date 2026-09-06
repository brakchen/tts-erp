# SPU 实际 ROI 看板 — 需求 · 信息源/数据源 · 计算口径 · 技术方案

> 状态：**draft → 2026-09-05 口径 + 页面样式已拍板（见文末 §11），待方案评审**
> 决策记录：① 广告币种全 USD；跨币换算**本期用固定汇率常量**（2026-09-05：USD→VND=26,330 / CNY→USD=0.1477，配置可改），在线汇率机制挂起（§4.6）；② 销售口径 = 排除 CANCELLED 后的**有效销售订单**（口径 B）；③ 平台佣金等不做分项建模，净现金以**每笔订单的结算金额**（净额）为权威口径；④ 货物成本：**按 SPU 解析（人工成本有效行优先，未命中才默认 30 CNY/件）**，使用默认的页面行打 ⚠；退货按**全损退货口径**；⑤ 页面样式 = 方案 A 账页式（§7）；⑥ **金额列统一以 USD 展示**（底层按原币 VND/CNY 计算，输出层一次换算）；⑦ 页面金额核心列 = 净利润（M18，毛利口径），净现金收入仅作内部中间量不展示；⑧ 结算归属解析 job + view 已排期（D7）；**平台佣金 = 平台从销售额直接扣除的全部费用**（抽佣+联盟+运费类等）；已结算订单按**实际扣费**，未结算订单按**参考基线 ≈11.6%** 估算，页面可覆写（D10）

> 2026-09-06 更新：跨币换算已切换为**在线 fx 缓存**（fx.* schema，见 `tech-doc/fx-exchange-rates.md` / `tech-doc/fx-agent-handbook.md`）：ROI 换算
> 直读最新 USD 快照（USD→VND = rates[VND]，CNY→USD = 1/rates[CNY] 量化 8dp，meta.fx.source=fx-cache）；快照缺失自动回退本节固定常量
> （source=fixed-const，账页不空白）—— C9/D9 原“在线汇率机制挂起”状态解除。
> 日期：2026-09-05
> 数据就绪度快照：本文 §8 的计数均为本日对生产库**只读查询**实测
> 关联文档：
>
> - `tech-doc/analytics/ad-product-links-ui.md` —— 「广告 × 商品投放台账」页方案（同家族前作，**未实施**，本文为它的兄弟页「SPU 实际 ROI」，口径只进不退）
> - `biz-doc/analytics/ad-product-links-view.md` / `post-product-list-field-semantics.md` / `endpoint-join-keys.md` —— 广告数据口径 truth source
> - `tech-doc/external-api.md` —— v2 端点活契约（本文端点假设沿用它 TL;DR 的约定）
> - `tech-doc/refactor-tech-plan-v2.md` —— finance 域设计（结算归属现状见 §6/§9）

---

## A. 信息源 · 数据源 · 计算口径 全景（先读总览）

> 阅读顺序：**A 总览 → §2 术语 → §4 公式 → §5 输出契约 → §6 数据表 → §7 UI → §9 缺口 → §11 决策**。
> 口径变更只改 A / §2 / §4 / §5 / §6 对应处，页面与端点只消费不另立口径（§5.1-1）。

### A.1 信息源清单（外部上游 + 占位输入）

| # | 信息源 | 内容 | 抓取通道（仓库既有唯一出口） | 本库落点 | 状态 |
| --- | --- | --- | --- | --- | --- |
| S1 | TikTok Shop Open API | 商品目录 / 订单 / 售后（取消·仅退款·退货） | sync-worker `tiktok.*` jobs（AGENTS §1：打上游唯一路径） | `commerce.*`、`after_sales.*`、`integration.raw_records` | ✅ 在用 |
| S2 | TikTok OEC 广告报表 | `post_product_list`（campaign×SPU×day 出单/消耗/GMV，含自然归因） | Chrome 扩展 `tk-adv-cost-monitor` → `POST /v2/analytics/sync/dumps` | `analytics.ad_raw` → `analytics.ad_product_links`（VIEW） | ✅ 在用（全窗口累计） |
| S3 | TikTok Shop 结算明细 | **59 列宽行、自带 `order_id`**（平台佣金 / 退货运费 / 退款管理费 / 平台补贴 / 运费等，见 §6.11） | sync-worker `tiktok.finance` job + **结算归属解析 job（已排期 D7）** | `integration.raw_records` 原样 + **解析结构化表 + view（待建）** | 🔶 解析 job 已排期（D7） |
| S4 | 妙手开放平台 采购 | 商品目录（216）/ 采购单（0） | sync-worker `miaoshou.*` jobs | `procurement.*` | ⚠️ 采购单未跑；本期不用（成本走 K1） |
| S5 | 汇率（固定值，本期） | USD→VND、CNY→USD | 不接在线源：**固定常量**（2026-09-05 取数，配置可改） | 配置常量（.env 或配置表）；无新 job / 无新表 | ✅ 固定值（在线化挂起，见 §4.6） |
| S6 | 货物成本 | 人工录入 / 采购价（真实值） | manual-costs 页 + cost snapshots job | `procurement.manual_product_costs` / `reporting.product_cost_snapshots` | ⚠️ 人工成本近空（现网 2 行）→ 未命中走默认 K1=30 CNY/件（页面 ⚠） |
| S7 | 用户手工假设 | 及格线 / 退款率警戒 / 成本占位 / 日期窗口 / 汇率兜底 | 页面 ⚙ 配置 | 前端本地或配置 | 可调，不参与口径定义 |

### A.2 数据源 → 用途总映射

| 数据源（schema.table / VIEW / raw） | 角色 | 供哪些指标/页面列 | 详情 |
| --- | --- | --- | --- |
| `commerce.products_spu` / `shops` | SPU / 店铺维度 | 主表行源；ad join 键 | §6.1/6.2 |
| `commerce.sales_orders` + `sales_order_lines` | 有效销售订单行（口径 B） | M5/M5b/M6（销售列） | §6.3/6.4 |
| `after_sales.cases` + `case_lines` | 售后：取消/仅退款/退货退款（含金额、件数、原因） | M7–M12（退款列）+ M9 信息列 | §6.5/6.6 |
| `analytics.ad_raw`（原始 dump） | 广告逐日原始（按天拆窗口的唯一路径） | 校验 / 自定义日期范围 | §6.7 |
| `analytics.ad_product_links`（VIEW） | campaign×SPU 出单量/消耗/GMV + ERP 键 | M1/M2/M2b/M3/M4（广告列） | §6.8 |
| `reporting.product_profit_daily` | (spu,day) units/gross_revenue 中间表 | M5/M6 复用源（可选） | §6.9 |
| 成本链表（`procurement.manual_product_costs` / `reporting.product_cost_snapshots` / `linkage.effective_product_links`） | 单位成本解析（人工优先，未命中默认 K1） | M13b 货损 / M18 净利润 / M17 保本的 unit_cost 来源 | §6.10 |
| `integration.raw_records`（`…/statement_transactions` 59 列） | **结算归属 + 退货运费承担判定的数据源** | M16（未来）、退货运费扣项（未来） | §6.11/§9-3 |
| `reporting.fx_rates`（待建） | 在线汇率（USD→VND、CNY→USD） | 输出层换算（§4.6） | §4.6 |
| `sync_jobs` / `sync_issues` / `sync_cursors` | 同步状态 / 问题哨兵 / 游标 | 口径健壮性（汇率失败、缺数告警） | AGENTS |

### A.3 计算口径决策链总表（本会话全部拍板，一行一决策）

| # | 决策点 | 拍板（决策号/日期） | 规则 / 公式 | 页面表现 | 限制·备注 |
| --- | --- | --- | --- | --- | --- |
| C1 | 广告币种 | USD（D1，09-05） | 原生即 USD | 广告列 $ | — |
| C2 | 销售口径 | **有效销售订单** = 排除 CANCELLED（D2/口径 B） | §4.1 白名单；M5/M6/M5b | 销售列只计有效单 | 与 profit_daily 同款白名单 |
| C3 | 已付被取消单 | 不进销售；退款=信息列 | M9（不扣净额） | “已付被取消”信息列 | 避免重复扣 |
| C4 | 退货口径 | **全损退货**：RETURN_AND_REFUND 已完结即全损（D4） | 件数 M11；金额 M8 | 退货列 + 货损列 | 实测 26/26 均妥投后退 |
| C5 | 货物成本（单位成本解析） | **人工成本优先，未命中 → 默认 K1=30 CNY/件（D4）** ≈ $4.43/件 | ① `manual_product_costs` 有效行(valid_to IS NULL) → MANUAL；② 未命中 → DEFAULT_K1（§4.2） | 使用默认的行打 ⚠，可跳 manual-costs 补录 | 现网人工成本仅 2 行，绝大多数 SPU 本期走默认 |
| C6 | 平台佣金/运费 | **不手工建模**（D3）；净现金权威口径 = 每笔订单结算净额 M16（落地后作 net_cash 的内部替代基础） | M16（待解析 job） | 净利润/保本按“未含平台费”口径并在页面标注，M16 落地后自动变准 | §9-3 |
| C7 | 退货运费谁承担 | 结算明细 `return_shipping_fee_amount≠0`（09-05 探查，608 行→5 单，5/5=R&R） | 待解析 job 归属后作扣项 | 未来列 | 勿用 reason_code 猜 |
| C8 | 显示币种 | **全表 USD**（D6） | 原币算 → 输出层一次换算 | 单币种表格 | fx 时间标注 |
| C9 | 汇率 | **固定常量（D9 更新 D1）**：USD→VND=26,330 / CNY→USD=0.1477（2026-09-05，配置可改） | 金额换算 / 保本 / ROI 直接按固定值 | 全表 USD 换算 | 在线机制挂起（§4.6） |
| C10 | 主指标 | **实际 ROI（M14）** = (净现金 − 全损货损) ÷ 广告 | NC′/spend（全 USD） | 主列 + 合计带 | 退款已含在净现金 |
| C11 | 保本线 | **M17 动态** = NC′ ÷ (NC′ − 正常卖出件货本 − 平台费用 fee) | roi_real≥roi_be ⇔ 净利润≥0 | 红绿判据（默认） | 结构性亏→无解 |
| C12 | 退款金额缺失 | 净额桶 27/27 齐全直取；取消桶缺 219/246 → **报 unknown 行数，不造数** | — | 双字段上报 | §5.6 |
| C13 | 未归属退款 | 66 行无法到 SPU | — | meta + 页脚提示 | 不静默丢 |
| C14 | 明细 / 交互 | 行内展开三 tab；方案 A 账页；默认排序 实际ROI↑（D5） | — | §7 | 及格线/退款率可调 |
| C15 | 结算/保本精度路径 | **解析归属 job + view 已排期（D7，2026-09-05）**；过渡期平台佣金用**费率基线**扣（D10） | M16→落地后自动含平台费/退货运费 | 落地前按费率估扣 | §6.11/§9-3 |
| C16 | 平台佣金（渠道费用）扣法 | **定义 = 平台从销售额直接扣除的全部费用**（抽佣+联盟佣金+运费类+其它扣款；单笔总扣 = 交易级 fee_amount）。**已结算订单 → 实际扣费（固定）；未结算订单 → sales × 参考基线 r̂**（Σ\|fee_amount\|/Σgross，实测 ≈11.6%，页面可覆写 %） | fee = 实际 + 估算（M19） | 默认用基线 r̂；解析上线后自动分层 | D10 |

### A.4 决策台账（D1–D10）

| 决策 | 内容 | 位置 |
| --- | --- | --- |
| D1 | 广告全 USD；跨币换算**本期固定汇率常量**（26,330 / 0.1477，配置可改），在线汇率挂起 | §4.6 |
| D2 | 销售 = 排除 CANCELLED 后的有效销售订单（口径 B） | §4.1 |
| D3 | 平台佣金等不手工分项建模；净现金以每笔订单结算净额（M16）为权威 | §6.11/§9-3 |
| D4 | 退货 = 全损口径；单位成本 = 人工成本有效行优先，未命中默认 K1=30 CNY/件（页面 ⚠，可跳补录） | §2/§4.2 |
| D5 | 页面样式 = 方案 A 账页式（红绿判据 = 实际 ROI vs 保本线） | §7 |
| D6 | 全表金额统一 USD 展示（输出层一次换算） | §3.4/§4.2 |
| D7（已拍板，2026-09-05） | 结算归属解析 job + 视图排期：把 59 列结算 raw 解析为按订单/行/case 归属的结构化数据并建只读 view（M16 数据底座） | §6.11/§9-3/§10 |
| D8（2026-09-05） | 页面金额核心列 = **净利润（毛利口径，M18）**；净现金收入(M13) 仅内部中间量，不展示 | §3.1/§4.2/§5.2/§7 |
| D9（2026-09-05） | 汇率**本期固定常量**（USD→VND=26,330 / CNY→USD=0.1477，配置可改）；在线汇率机制（原 D1）挂起，待后续排期 | §4.6/§6.12 |
| D10（2026-09-05） | 平台佣金（渠道费用）= **平台从销售额直接扣除的全部费用**（抽佣/联盟/运费类等）；**已结算订单用实际扣费，未结算订单用参考基线 r̂ ≈ 11.6%**（Σ\|fee_amount\|/Σgross）估算，页面可覆写；解析 job 上线后自动分层（已结算不再估） | §4.2/§5.2/§6.11 |

---

## 1. 需求背景（口述整理）

> 原话要点：每个 SPU 的广告消耗不计算退货和取消。买家**秒拍秒退**我会白亏广告费；
> 买家**货已到海外再退**，我要把钱退回给买家，亏掉货物成本 + 广告成本，
> 商家有责的还要我出运费。需要一个页面直观展示每个 SPU 的**实际 ROI**。

平台（TikTok OEC 广告报表）的 ROI 分子分母都是广告口径的「归因出单 GMV」，
**不含退货、取消、退款**，因此广告侧数字永远比真实回款乐观。要回答「这个 SPU 到底赚不赚钱」，
必须把三条数据链并到同一张表上：

```text
广告花钱链：  campaign × SPU 的逐日真实消耗(USD)       → 花了多少广告费
卖货收钱链：  有效销售订单（已支付、未取消）行 × SPU     → 账面进了多少钱(VND)
退钱亏钱链：  取消 / 仅退款 / 退货退款的件数与金额      → 实际退出去多少钱
（以及由此造成的货损/运费损失 — 成本链：货损已可用单位成本解析值计算，退货运费待结算解析 job，见 §9）
```

三种“亏钱场景”在数据上分别长这样：

| 场景 | 在数据里的样子 | 需要看板回答的问题 |
| --- | --- | --- |
| ① 秒拍秒退 / 下单即取消 | 订单 `paid_at` 有值，随后 status=CANCELLED + `after_sales.cases` 一条 CANCELLATION（生产实测 245 个 CANCELLED 订单里 26 个有 paid_at） | 广告消耗算进去了，收入被退光 → 这个 SPU 的广告费在给谁烧 |
| ② 货已到海外再退 | 订单已 shipped（甚至 DELIVERED）+ `cases.case_type=RETURN_AND_REFUND` | 退货件数、退货金额占销售的比例 → 实际回款被削掉多少 |
| ③ 仅退款（未寄回） | `cases.case_type=REFUND_ONLY` | 同上，无货损但有退款 |

**目标（一句话）**：一张按 SPU 分行的页面，把「广告消耗 → 销售金额 → 退货/取消扣减 → **净利润（毛利口径）** → **实际 ROI / 保本线**」完整串起来，让「广告 GMV ROI 很好看、但实际不赚钱」的 SPU 一眼暴露。

**非目标（防范围蔓延）**：

- 不做素材 / 广告计划级的利润归因（只看 SPU 汇总，明细可钻取但不下钻利润）
- 不做归因模型（TikTok GMV Max 归因含自然单，无法拆“纯广告增量”，只能标注口径警告）
- 不做自研汇率换算引擎 —— **本期用固定汇率常量**（USD→VND=26,330 / CNY→USD=0.1477，2026-09-05，配置可改）；在线汇率查询为后续方向（原 D1 挂起，机制见 §4.6）
- 不做预测 / 补货建议

---

## 2. 术语表（全文统一口径）

| 术语 | 定义 |
| --- | --- |
| SPU | TikTok 商品（`commerce.products_spu` 一行）。对外 ID = `spu_id`（如 `1736527242804888823`）；内部主键 = `spu_pk`（BigInteger id，**过滤/聚合一律用它**，`spu_id` 只做展示与搜索） |
| 广告消耗（ad spend） | TikTok OEC `post_product_list` 的 `mixed_real_cost`（真实消耗），跨天求和 = `ad_product_links.real_cost_total`。**币种 = USD（2026-09-05 已确认：广告账户为 USD）**，与订单币种 VND 不同 |
| 出单 GMV（平台归因） | `onsite_roi2_shopping_value` 求和。**归因口径，含自然单**，只用于展示“平台说你卖了多少”，不是回款 |
| **有效销售订单**（口径 B，2026-09-05 拍板） | status ∈ `PAID_SALES_ORDER_STATUSES`（§4.1）且已支付的订单；**CANCELLED / UNPAID / ON_HOLD 一律排除**。排除后即视为有效销售订单 —— 销售金额 / 售出件数 / 订单数全以它为基数，不做任何事后剔除（含“已付后被取消”的单，它们不进销售） |
| 销售金额（ERP） | Σ **有效销售订单**行 `quantity × unit_price` 按 SPU 汇总，原生币 VND（生产 870/870 单全 VND；**展示统一 USD**） |
| 退款（分两类，别混） | ① **有效订单退款（计入净额）**：REFUND_ONLY / RETURN_AND_REFUND，且其订单本身是有效销售订单；② **已付后被取消订单的退款（信息列，不计净额）**：CANCELLATION / CANCEL（订单 status=CANCELLED —— 该单销售本来就没算过，不能把没算过的销售再扣一遍） |
| case / case_lines | 售后单：`after_sales.cases`（单头）+ `after_sales.case_lines`（按订单行拆分） |
| case 状态完结 | 见 §4.3：`CANCELLATION_REQUEST_COMPLETE` / `RETURN_OR_REFUND_REQUEST_COMPLETE` 才代表钱已退 |
| 实际 ROI | 对照档 **L0** = 平台 GMV ROI（广告口径，仅供对照）；**主指标 = 实际 ROI（M14）= (净现金收入 − 全损退货货损) ÷ 广告消耗（全 USD）**；**保本实际 ROI（M17）= 每 SPU 的动态盈亏线**（实际 ROI 低于它即亏，公式见 §4.2）。公式见 §5 |
| **净利润（毛利口径，页面金额核心列）** | M18 = 净现金收入(内部 M13) − 全部售出件货本 − 广告消耗 − **平台佣金（M19：已结算实际 + 未结算 sales×r̂）**（全 USD）；**≥ 0 ⇔ 实际 ROI ≥ 保本 ROI**；r̂ 默认 = 已结算参考基线（≈11.6%，含抽佣/联盟/运费等全部直接扣除），页面可覆写；解析上线后已结算部分自动用实际值。净现金收入本身**不展示** |
| 订单结算金额 | 每笔订单在 TikTok 结算单里的**净额**（平台扣费/退款调整已含其中，净现金口径不做手工分项建模——决策 3）。净现金的**权威口径**（M16）；解析 job + view 已排期（D7），当前未落地（§6.11/§9-3） |
| **平台佣金（渠道费用）** | 平台从销售额**直接扣除的全部费用**（交易抽佣 + 联盟佣金 + 运费类 + 其它扣款；单笔总扣 = 结算交易级 `fee_amount`）。**已结算订单 = 实际扣费；未结算订单 = sales × 参考基线 r̂**（Σ\|fee_amount\|/Σgross，≈11.6%，页面可覆写 %）；解析 job 上线后自动分层（D10/M19，§4.2/§6.11） |
| **全损退货**（口径，2026-09-05 拍板） | RETURN_AND_REFUND 已完结即视为**全损**：货已妥投海外、退不回/不可再售（实测 26/26 全为发货后）→ 除退给买家的钱（已计入退款）外，**每件另计货损** = 件数 × 单位成本解析值（人工优先/缺省 30 CNY ≈ $4.43/件）。REFUND_ONLY（仅退款）不产生货损 |
| **货物成本（占位）** | 2026-09-05 拍板：**按 SPU 解析**——先查 `procurement.manual_product_costs` 有效行（`valid_to IS NULL`），命中即用（`cost_source=MANUAL`，**表内保证人民币 CNY**）；未命中才用默认 **K1 = 30 CNY/件**（`DEFAULT_K1`，页面 ⚠）。两者均为 CNY，统一经 CNY→USD 在线汇率换 USD。 |
| **显示币种（2026-09-05 拍板）** | **全表金额列统一 USD，不再分 USD/VND 栏**：VND 金额 ÷ USD→VND、货损 CNY × CNY→USD（均用在线汇率）；底层计算保持原币（防舍入），输出层一次换算、四舍五入；列头/提示行标注所用 fx 与取值时间 |

---

## 3. 页面内容拆解（需求拆分）

### 3.0 页面定位与入口

- 只读 operator 页，与 `manual-costs` / 规划中的 `ad-products` 同家族（`GET /v2/pages/<name>` HTML shell + `static/js/<name>.js`，原生 fetch，登录走 browser session，未登录 302 → login）。
- 数据走**新增只读 JSON 端点**（role **readonly**），不直连 DB。
- 外网 URL 形态（沿用 `/tts/` 反代约定）：`http://daqiang.nat100.top/tts/v2/pages/spu-roi`。
- 目录：`tech-doc/analytics/`（本文）+ 页面/端点方案按 `ad-product-links-ui.md` 的评审流程走。

### 3.1 P0 主表（页面核心：每 SPU 一行）

列分组与优先级（带 ✓ 的为本期必做，○ 为可选显示开关）。**每个页面列在 §5.2 有且仅有一个 JSON 字段与公式（M#）与之对齐；页面只做格式化、不做任何业务计算。**

| 分组 | 列 | 说明 | 币种/单位 |
| --- | --- | --- | --- |
| A 商品 | ✓ `spu_id`、主图、标题、上架状态（ACTIVATE/已下架）、店铺名 | 维度列，主键 = `spu_pk` | — |
| B 广告 | ✓ 投放广告数（挂该 SPU 的 campaign 数）、✓ 广告消耗合计、✓ 平台出单 GMV、✓ 平台 GMV ROI（L0）、○ 观测窗口（first_day~last_day） | 来自 `analytics.ad_product_links`，窗口=已捕获全量（§4.5 时间口径） | USD（原生） |
| C 销售 | ✓ 售出件数、✓ 销售金额、○ 订单数 | 只统计**有效销售订单**（口径 B：白名单、排除 CANCELLED/UNPAID/ON_HOLD），按 `paid_at` 落入所选范围 | USD（原币 VND 换算） |
| D 退款 | ✓ 有效订单退款（仅退款+退货退款：单数/件数/金额，**计入净额**）、✓ 已付被取消订单退款（件数/金额，**信息列不计净额**）、✓ 退款率=有效订单退款÷销售金额 | case 状态完结才计入；件数取 `case_lines.quantity` | USD（原币 VND 换算） |
| E 实际 ROI | ✓ **净利润**（M18，毛利口径：净现金(内部) − 全部售出货本 − 广告消耗，每 SPU 真赚多少，**页面金额核心列**）；✓ **全损退货货损**（M13b = 全损退货件数 × 单位成本解析值，人工优先/缺省 30 CNY ≈ $4.43/件）；✓ **实际 ROI** = (净现金(内部) − 退货货损) ÷ 广告消耗（M14）；✓ **保本实际 ROI**（M17，实际 ROI 低于它标红） | 净利润是“结余核心”（负值红字），实际 ROI 主指标，保本线是红绿判据；**净现金收入(M13) 仅内部中间量，不展示** | USD（原币 VND/CNY）/ 比值 |

顶部**合计条**（跟随当前筛选实时汇总，家族 signature，**全 USD**）：共 N 个 SPU · 广告消耗 $x · 有效销售 $x · 有效订单退款 $x · 全损货损 $x · **净利润 $x** · **整体实际 ROI n**（固定汇率 D9：26,330 / 0.1477，§4.6）。

**排序默认「实际 ROI 升序」**（最亏的排最前，决策 D5），列头可切：广告消耗 / 退款率 / 净利润 / 销售（交互见 §7.3）。
**搜索框**：`spu_id` 子串（对齐广告报表里看到的商品 ID）。分页 limit/offset 沿用 v2 约定。
SPU 无广告投放 → 广告列显示 0 与“无投放”文案（不隐藏该行——卖得多没投广告也是信息，NULL 语义见 §5.1-5）。

### 3.2 P1 明细钻取（点击行 → 该 SPU 的三个 tab）

| Tab | 内容 | 数据来源（已有端点/表） |
| --- | --- | --- |
| 订单 | 该 SPU 涉及的订单（订单号/状态/件数/金额/paid_at/shipped_at），CANCELLED 单标红 | `commerce.sales_orders` + `sales_order_lines`（by `spu_pk`） |
| 售后 | 该 SPU 的 case 明细（case 类型/状态/退款金额/原因 code+text/时间），未完结 case 标黄 | `after_sales.cases` + `case_lines`（经 order→line→spu） |
| 广告 | 该 SPU 的 campaign×SPU 行（广告 ID/消耗/出单/窗口） | `analytics.ad_product_links`（by `spu_id`/`spu_pk`） |

### 3.3 P2 进阶（预留，本期不做，仅记入需求）

- **退款原因聚合**：按 `cases.reason_code` 堆叠（哪些 SPU 因为“商品质量问题/尺码/不想要”退款多 → 反推是否品控问题）
- **秒拍秒退预警**：`paid_at` → 取消/退款完成 < 阈值（如 10 分钟）的单量占比
- **退货阶段分布**：按订单 `shipped_at`/`delivered_at` 与 case 时间对比，拆「发货前取消 / 发货后退款 / 妥投后退货」三类（场景①②③的量化，字段已具备）
- **成本链已并入主表**：全损货损 M13b 直接在主表（单位成本解析值，D4）——不再有“L2 货损预留”；P2 预留 = finance 结算归属解析 job 落地后，把平台扣费/退货运费并入**净利润计算**（以 M16 订单结算金额替代 net_cash 内部基础，§6.11/§9-3）

### 3.4 口径切换与展示规则

- **全表统一 USD、不再分栏**：广告（原生 USD）、销售/退款金额（原生 VND ÷ USD→VND）、货损（原生 CNY × CNY→USD）→ 由此算得的各金额列（含净利润）与 ROI **全部以 USD 展示**；底层按原币计算、输出层一次换算（防舍入）；换算用**固定汇率常量（D9）**，其值在栏头/提示行标注；配置缺失时，依赖换算的金额列（如净利润）与 ROI 列置 `—` 并提示（延续 audit P1-4b 的“宁可承认不知道”原则）。
- **口径警告 chip**：广告列旁常驻提示「GMV Max 归因含自然单 + 数据有滞后修正，广告数字≠纯广告增量、≠最终回款」。
- 时间默认 = 全历史累计（销售/退款不裁剪，ad 恒为视图全窗口）；如需同窗口口径，端点显式传 `w_start`/`w_end`，B 组口径说明以 meta.window 为准（§4.5）。

---

## 4. 指标口径与计算公式（核心章）

> 通用规则：
>
> - 聚合键一律 `spu_pk`（内部主键）。
> - money 在 API 序列化为 JSON 字符串（Decimal），前端再格式化。
> - 金额底层按**原币**计算（销售/退款 = VND；货损 = CNY；广告 = USD）；净现金收入(M13) 为内部中间量，不展示；**输出统一换算 USD**：先原币加总、再一次性换算四舍五入，禁止逐行先换再加（防舍入漂移）。
> - 时间全部 aware UTC；订单按 `paid_at` 归属，退款按 case `updated_at_source`（状态完结时间）归属。
> - 页面金额以**净利润（M18）**为结余核心（负值红字），净现金收入(M13) 仅作内部中间量；货损（M13b）单独成列并已并入实际 ROI（M14）与净利润（COGS_all 内含），无“L2 预留”的说法。

### 4.1 订单已支付白名单（复用既有常量，勿新造）

`PAID_SALES_ORDER_STATUSES`（`tts_erp_v2/db/constants.py`，生产实测分布见 §8）：
`AWAITING_SHIPMENT / PARTIAL_SHIPPING / AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED`。
**排除** `UNPAID / ON_HOLD / CANCELLED`。该白名单即口径 B 的「有效销售订单」判定（§2），页面与端点沿用这一处常量，不另维护。

### 4.2 指标公式表

记当前 SPU 为 `s`，日期范围 `W`（订单按 `paid_at`，退款按完结时间）。

**单位成本解析（每 SPU，人工优先 → 默认 30 元）**：

```text
① 命中 procurement.manual_product_costs 有效行（valid_to IS NULL，每 SPU 至多一条） → unit_cost = 行值（**2026-09-05 确认：人工表录入保证为人民币 CNY** → 统一走 CNY→USD fx），cost_source = MANUAL
② 未命中 → unit_cost = K1 = 30 CNY/件（≈ $4.43/件），cost_source = DEFAULT_K1
   → 页面该行打 ⚠：无人工成本记录，按默认 30 元/件计算（可点去 /v2/pages/manual-costs 补录）
```

（人工成本入口 = `POST /v2/reporting/manual-costs` + `GET /v2/pages/manual-costs`；`GET /v2/reporting/missing-cost-products` = 当前无成本的 SPU 清单，即 MANUAL 未命中者的补录入口。）

| # | 指标 | 符号 | 公式 | 粒度 | 数据源 |
| --- | --- | --- | --- | --- | --- |
| M1 | 广告消耗合计 | `spend(s)` | `Σ ad_product_links.real_cost_total`（该 spu 所有 campaign×SPU 行） | 广告窗口全量 | 视图（读 ad_raw 派生） |
| M2 | 投放广告数 | `ad_count(s)` | `COUNT(DISTINCT campaign_id)` | 同上 | 同上 |
| M2b | 平台出单量（SKU 口径，信息列） | `ad_orders(s)` | `Σ order_sku_total`（跨 campaign 求和；TikTok Orders(SKU)，含自然归因） | 广告窗口全量 | 视图 |
| M3 | 平台出单 GMV | `gmv_ad(s)` | `Σ order_value_total`（归因口径，含自然单） | 同上 | 同上 |
| M4 | 平台 GMV ROI | `roiL0(s)` | `gmv_ad(s) / spend(s)`；`spend=0 → NULL`（页面显示 `—`） | 同上 | M3/M1 |
| M5 | 售出件数（有效销售） | `units(s)` | `Σ sales_order_lines.quantity`（join **有效销售订单**且 `paid_at∈W`，`spu_pk=s`） | 按日可拆 | sales_order_lines + sales_orders |
| M5b | 有效销售订单数 | `order_count(s)` | `COUNT(DISTINCT sales_orders.id)`（同一有效销售过滤） | 按日可拆 | 同上 |
| M6 | 销售金额(gross) | `sales(s)` | `Σ quantity × unit_price`（同上过滤条件） | 按日可拆 | 同上 |
| M7 | 仅退款金额（计净额） | `refund_only(s)` | `Σ` 已完结 REFUND_ONLY case 退款，**且其订单 ∈ 有效销售订单** | 按完结时间 | cases(+case_lines) |
| M8 | 退货退款金额（计净额） | `refund_return(s)` | `Σ` 已完结 RETURN_AND_REFUND case 退款，**且其订单 ∈ 有效销售订单** | 同上 | 同上 |
| M9 | 已付被取消订单退款（信息列） | `refund_cancelled(s)` | `Σ` 已完结 CANCELLATION/CANCEL case 退款（订单 status=CANCELLED —— 该单销售本就不在 M6 里，故只展示不扣净额）。**行级金额缺失时不造数**：输出「已知金额小计 + 未知行数」（实测缺失 219/246，见 §5.6） | 同上 | 同上 |
| M10 | 有效订单退款合计（计净额） | `refund_net(s)` | `refund_only(s) + refund_return(s)` | 同上 | M7+M8 |
| M11 | 退款件数 | `units_refunded(s)` | `Σ case_lines.quantity`（M7/M8 对应 case 的件数；取消件数 = M9 对应 case 的 quantity，另列展示；件数无缺失问题，与金额的“未知”处理不同） | 同上 | case_lines→cases |
| M12 | 退款率 | `refund_rate(s)` | `refund_net(s) / sales(s)`（金额口径；另给件数版 `units_refunded/units`） | 同上 | M10/M6 |
| M13 | 净现金收入（**内部中间量，不直接展示**） | `net_cash(s)` | `sales(s) − refund_net(s)`（订单行金额朴素口径：未含平台扣费/运费；供 M18 净利润 / M14 ROI / M17 保本线使用） | 范围求和 | M6−M10 |
| M13b | **全损退货货损** | `return_loss(s)` | `全损退货件数(M11 退货桶) × 单位成本解析值(§4.2，人工优先/缺省 30 CNY) × CNY→USD 汇率`（全损口径：退货即货本全损，§2） | 范围求和 | M11 + 成本解析 + fx(§4.6) |
| M14 | **实际 ROI（页面主指标）** | `roi_real(s)` | `(net_cash(s) − return_loss(s)) / spend(s)`（净现金/货损已由原币经固定汇率换算成 USD，spend 原生 USD，**同币相除无汇率因子**）；`spend=0` 或固定汇率配置缺失/异常 → `null` | 范围 | M13−M13b / M1 |
| M15 | 单订单广告成本 | `cpa(s)` | `spend(s) / ad_orders(s)`（= M1/M2b，平台出单量口径） | 范围 | M1/M2b |
| M16 | **订单结算金额（净现金权威口径，待 finance 落地）** | `settlement_net(s)` | `Σ` 有效销售订单的**每笔订单结算净额**（TikTok 结算单订单级净额，平台扣费/退款调整已含；**不做佣金等分项建模**——决策 3） | 按结算周期 | finance（§6.11） |
| M17 | **保本实际 ROI（每 SPU 动态线）** | `roi_breakeven(s)` | 见下方“保本线口径”：`NC′ ÷ (NC′ − COGS_kept − fee_est)`；`COGS_kept + fee_est ≥ NC′ → NULL`（结构性亏损，无保本线） | 范围 | 由 M13/M13b/M11/M5/M19 推导 |
| M18 | **净利润（毛利口径，页面金额核心列）** | `net_profit(s)` | `net_cash(s) − COGS_all(s) − spend(s) − fee_est(s)`，`COGS_all = units_sold × 单位成本解析值`（全部售出件货本，含退回件——不再单扣 return_loss，避免重复）；**`net_profit ≥ 0 ⇔ roi_real ≥ roi_breakeven`（与 M17 同号）**；fee_est 见 M19（分层：已结算用实际、未结算按 r̂；净额已含部分不再单计） | 范围求和 | M13/M5/M6 + 成本解析 + M1 + M19 |
| M19 | **平台佣金（渠道费用）** | `platform_fee(s)` | `已结算订单实际扣费(Σ\|fee_amount\|) + 未结算订单 sales × r̂`；`r̂` = 参考基线（Σ\|fee_amount\| / Σgross_sales_amount，**2026-09-05 去重实测 ≈ 11.6%**，页面可覆写输入 %）；解析 job 未上线前全按 r̂ 估（本期）；已结算部分用实际后不再估 | 范围 | M6 + 基线（§6.11） |

> 净现金两种口径：M13 = 订单行金额朴素估算（本期先上）；M16 = 每笔订单结算金额（落地后作为 net_cash 的**内部替代基础** → 净利润 M18 / 实际 ROI M14 / 保本 M17 的已结算部分自动含平台扣费/退款调整——此时**已结算部分不再单计 fee**（净额已含），未结算部分仍按参考基线 r̂ 计（M19 分层），避免重复扣）。两者差异 ≈ 平台扣费 ± 退款调整时差。

**平台佣金（渠道费用）处理（D10：已结算按实际，未结算按基线）**：

```text
平台佣金（口径）= 平台从销售额直接扣除的全部费用 = 交易抽佣(platform_commission_amount)
                 + 联盟佣金(affiliate_commission_amount) + 运费类(shipping_fee / shipping_cost /
                   actual_shipping_fee 等) + 其它扣款；单笔“总扣除”= 交易级 fee_amount（与分项自洽）
⚠ 勿用 gross_sales − settlement_amount 当费用（settlement 含未结款/退款偏移，实测会得出 ~78% 的假象）

fee(SPU) = Σ 已结算订单的实际扣费（解析 view 按 order 汇总 |fee_amount|）
         + Σ 未结算订单 sales × r̂
参考基线 r̂ = Σ|fee_amount| ÷ Σgross_sales_amount（已结算交易；作用域 = 当前窗口 + 店铺）
           = 2026-09-05 去重实测 ≈ 11.6%
```

- 本期（解析 job 未上线，无法区分已/未结算）：全按基线估 —— `platform_fee = sales × r̂`（≈11.6%），页面可覆写输入 %（M19）。（下文 M17/M18 公式中以 `fee_est` 作为该费用的代数简写，= M19 `platform_fee`。）
- 解析 job 上线后：自动切“已结算 → 实际 fee_amount、未结算 → 基线 r̂”；同时 net_cash 基础切 M16 后，已结算订单的净额已含扣费，不再重复计 fee。
- fee 只扣进 **净利润（M18）与保本线（M17）**；实际 ROI（M14）分子 NC′ 不含 fee（同 COGS_kept 的处理：扣减项体现在保本线判断上，红绿判据仍与净利润同号，见 §5.4-6）。

**保本线口径（M17，2026-09-05 需求）**：

```text
记 NC′ = net_cash − return_loss            # 扣除退款与退回件货损后的净进账（USD）
   COGS_kept = (units_sold − 全损退货件数) × 单位成本解析值(§4.2，人工优先/缺省 30 CNY) × CNY→USD fx   # 正常卖出件的货本
利润(毛利口径) = NC′ − COGS_kept − fee_est − 广告消耗           # fee_est = 平台佣金（M19/D10：已结算用实际、未结算按 r̂；净额已含扣费的部分不再单计）
保本条件：利润 ≥ 0  ⇔  实际ROI(M14) ≥ roi_breakeven(M17)
roi_breakeven = NC′ ÷ (NC′ − COGS_kept − fee_est)   # COGS_kept + fee_est ≥ NC′ → 无解（结构性亏损）
# 上式“利润(毛利口径)”与 M18 净利润恒等：NC′ − COGS_kept − fee_est − 广告 ≡ net_cash − COGS_all − fee_est − spend
```

- 推导自洽：M14 的分母就是 NC′，因此 `实际ROI ≥ 保本ROI ⇔ 利润 ≥ 0`，页面红/绿与盈亏同源，不会出现“红色却赚钱”。
- 样例（§5.3 同款 SPU）：NC′=430.39 USD、COGS_kept=(27−4)×4.4322=101.94 USD、fee=sales 557.21×11.56%≈64.40 USD → `roi_breakeven = 430.39 ÷ (430.39−101.94−64.40) = 1.63`；实际 ROI 2.73 ≥ 1.63 → 绿。
- **注（平台佣金/运费）**：费率 r̂ 默认 = 参考基线（≈11.6%，页面可覆写）；解析 job 上线后分层——已结算用实际 fee_amount（净额含扣费部分不再单计）、未结算仍按 r̂；卖家承担的退货运费待解析 job 落地后并入（§6.11/§9-3）。

**退款金额归属规则（重要，防重不漏）**：
0. **先按订单有效性分桶**：join `sales_orders.status` —— 白名单内订单的已完结退款 → M7/M8（按 case_type 分）；`CANCELLED` 订单 → M9（信息列）；`UNPAID/ON_HOLD` 等异常 → 防御性进「未归属」。**实测覆盖（2026-09-05，完结状态）**：净额桶金额 27/27 行齐全 → 行级金额直取即可、无需分摊；取消桶仅 27/246 行有金额 → M9 缺失行如实报「未知行数」，不从 case 级/占比去猜数。

1. case 只有单 SPU（该 order 所有 line 都属于 s）→ 用 case 级金额直接记给 s（`cases.refund_amount` 或 case_lines 求和一致）。
2. 多 SPU 订单（一个 order 含多个 spu 的 line）→ 按该 case 下 **case_lines.refund_amount**（按 line 挂到对应 spu）；某 line 无 refund_amount 时按该 line 的 `quantity×unit_price ÷ 订单行总金额` 分摊 case 级金额，并在行内标「分摊」。
3. 归属不上（case_lines 无 `sales_order_line_id` 或 line 无 spu）→ 计入「未归属退款」合计行（**实测完结状态 66 行**，页面页脚/合计带提示，不静默丢），明细可到 `integration.raw_records` 反查原始 payload。

**L2 说明**：全损货损已并入主指标（M13b→M14），不再单设 L2 净利；剩余可选损失 = 商家有责退货运费（数据源待定，§9），本期无输出。

```text
可选更完整口径 = (net_cash − return_loss − return_shipping) / (spend × fx)   # return_shipping 待数据源
```

### 4.3 case 状态机（钱“已退”才算退款）

生产实测（2026-09-05，281 case）状态全集：

| 状态 | 含义 | 是否算退款金额/件数 |
| --- | --- | --- |
| `CANCELLATION_REQUEST_COMPLETE` | 取消已完成（钱已退） | ✓（250 条） |
| `RETURN_OR_REFUND_REQUEST_COMPLETE` | 仅退款/退货退款已完成（钱已退） | ✓（27 条） |
| `BUYER_SHIPPED_ITEM` | 买家已寄回、退款未完成 | ✗ 标“进行中”（4 条），不进金额，P1 明细标黄 |
| 其他/未知 | 防御性排除 | ✗ 进 P1 明细 + 未归属行 |

> ⚠️ case_type 历史双拼写：生产库同时存在 `CANCELLATION`（175）与旧数据 `CANCEL`（75）。过滤取消一律 `IN ('CANCELLATION','CANCEL')`；模型注释里的三值清单已过时，SQL 以实测集合为准（也把 `REFUND_ONLY` / `RETURN_AND_REFUND` 当两个独立类型处理，不要假设“REFUND_ONLY 归并到 RETURN”）。

### 4.4 与现有 `reporting.product_profit_daily` 的关系

该表已实现「按 (spu, day) 的 units_sold / gross_revenue」聚合（口径 = 本页 M5/M6 的同款白名单），
**可直接复用**当销售侧输入，避免重写聚合；但注意：

- `product_profit_daily.refunds / platform_fees / shipping_cost` 列**当前全为 NULL**（退款/结算归属未接入，见 §9-3），不能指望它给退款；
- `estimated_cogs / estimated_gross_profit` 也全 NULL（cost snapshots 空，见 §9-2）——profit_daily 目前只等价于“卖了多少”。

### 4.5 时间口径（必须向用户说清的一处）

- **默认（不传 `w_start` / `w_end`）= 全历史累计**：销售/退款按各自全历史行累计，不做日期裁剪。
- 可选传 `w_start` / `w_end`（ISO 日期 `yyyy-mm-dd`）裁剪销售与退款：销售按订单 `paid_at`（`>= w_start` 且 `< w_end+1 天`，即**含 `w_end` 当日**）；退款按 case `updated_at_source`（状态完结时间）**同界**。
- **广告消耗**：`ad_product_links` 是**全窗口累计**视图（ad_raw 不 purge），**没有日期参数**——始终整窗累计；`meta.window.first_day/last_day` 只是 ad 视图的观测窗口（供参考），**不代表销售/退款已按该窗口裁剪**。
- 页面/BI 需要同窗口口径时：显式传 `w_start` / `w_end`；口径标注以 `meta.window.note` 为准（默认注记“ad=视图全窗口累计；销售/退款=全历史（未裁剪，可传 w_start/w_end）”）。

### 4.6 汇率换算（D1/D9：广告全 USD；本期固定汇率常量，在线机制挂起）

- **决策（D9，2026-09-05）**：**不接在线汇率源**，统一使用**固定汇率常量**（放配置，可改）：`USD→VND = 26,330`、`CNY→USD = 0.14774`（**展示/四舍五入 0.1477；30 CNY/件 = $4.4322**；取数日 2026-09-05，来源越南银行卖出价 / investing / xe）。**改动常量 → 下次计算即时生效；不重算历史。**
- **换算方向**：VND ÷ 26,330 → USD（销售/退款等）；CNY × 0.1477 → USD（货本）；CNY→VND 交叉值 ≈ 3,890 仅作核对。
- **口径说明**：全页换算基于同一组固定常量，页面无需“取值时间”（meta 保留 `as_of` + `source=fixed-const` 供审计）。
- **在线化（原 D1，挂起，后续方案）**：如需自动更新，按既有机制落地——sync-worker 定时取数 job（候选 open.er-api.com / Frankfurter 等主源+兜底源）→ 落 `reporting.fx_rates`（currency_pair/rate/fetched_at/source）→ 读路径零外网、失败保留上次值、过期标注。**本期不建 job、不建表**；届时固定常量退役。

---

## 5. 输出信息 × 计算方式 对齐契约（单点真相）

> 本节是「页面展示什么 + 每个数字怎么算出来」的**唯一权威**：页面列 ↔ 端点 JSON 字段 ↔ §4.2 公式 M# ↔ SQL 列 四者一一对应。
> 任何口径变更只允许改 §4.2 公式表与本节，其余文件（页面/端点/测试）只消费不另写口径。

### 5.1 对齐原则（防输出与口径漂移）

1. **页面不计算业务数字**：只消费端点已算好的字段做格式化（千分位/小数位/币种符号）。金额与 ROI 全部服务端算好下发 → 页面与端点永不产生第二套结果。
2. **输出字段 ↔ 公式 1:1**：每个输出字段唯一对应一个 M#；一个 M# 只服务一个输出字段。禁止同一字段由两条口径拼凑。
3. **totals 同源**：页首合计由端点用**与行查询相同的 CTE** 再做聚合回传（跨分页加总），不做分页客户端求和。
4. **序列化规则**：金额底层原币计算，**输出统一 USD**（VND ÷ USD→VND、CNY × CNY→USD，服务端一次换算）→ money = `numeric(20,4)` JSON 字符串；比率 = 2 位小数字符串；件数 = 整数（值恒为整时）。
5. **NULL 语义统一**：无投放 → `spend="0.0000"` + `ad_count=0`（页面文案“无投放”）；除数为 0 的 ROI → `null`（页面显示 `—`）；无有效销售 → `sales=0`、`refund_rate/roi_real=null`；**固定汇率配置缺失 → 依赖换算的金额（如 `net_profit`）与 ROI 输出 `null`（页面 `—`），原生 USD 列（广告）不受影响；本期固定值下恒有值**。
6. **时间窗口单一**：行与 totals 使用同一个筛选——默认不传参 = **销售/退款全历史累计**（可传 `w_start`/`w_end` 裁剪：销售按 `paid_at`、退款按 `updated_at_source`，含 `w_end` 当日）；广告 = ad 视图全窗口累计（无日期参数）。`meta.window` 明示 ad 观测窗口**供参考**，销售/退款是否被裁剪见 `meta.window.note`（§4.5）。
7. **行范围**：默认返回「有广告投放 ∨ 有有效销售 ∨ 有退款」的 SPU（不按目录状态裁剪）；可选参数 `include_all` 拉**全部 ACTIVE 目录 SPU**（目录查询按 `cp.status ILIKE 'activate'` 过滤，DEACTIVATE/DELETED 等不进 include_all；无任何活动的行金额全 0）。

### 5.2 行输出字段契约（主表 1 行 = 1 SPU）

| 分组 | 页面列（单位/币种） | JSON 字段 | 类型 | 计算（唯一 M# + 精确规则） |
| --- | --- | --- | --- | --- |
| A 商品 | SPU / 标题 / 状态 / 主图 / 店铺 | `spu_pk, spu_id, title, status, main_image_url, shop_id, shop_name` | int/str | 维度列：`commerce.products_spu` + `shops` 直取（无计算） |
| B 广告 | 投放广告数 | `ad_count` | int | M2：`COUNT(DISTINCT campaign_id)`（该 spu 的 view 行） |
| B 广告 | 平台出单量（信息列） | `ad_orders` | int | M2b：`Σ order_sku_total`（SKU 口径，含自然归因） |
| B 广告 | 广告消耗（USD） | `spend` | money-str | M1：`Σ real_cost_total`；无投放 → 0 |
| B 广告 | 平台出单 GMV（USD，归因） | `gmv_ad` | money-str | M3：`Σ order_value_total`；仅展示非回款 |
| B 广告 | 平台 GMV ROI | `roi_l0` | ratio-str/null | M4：`gmv_ad/spend`；`spend=0 → null` |
| B 广告 | 观测窗口 | `ad_first_day, ad_last_day` | date | 该 spu 的 `MIN(first_day)/MAX(last_day)`（meta 也给全局窗口） |
| C 销售 | 有效销售订单数 | `order_count` | int | M5b：`COUNT(DISTINCT sales_orders.id)`（有效销售过滤 + `paid_at∈W`） |
| C 销售 | 售出件数 | `units_sold` | int | M5：`Σ quantity`（同过滤） |
| C 销售 | 销售金额（USD，原币 VND） | `sales` | money-str | M6：`Σ quantity × unit_price`（同过滤，原生 VND → ÷usd_vnd） |
| D 退款 | 仅退款：件数/金额 | `refund_only_qty, refund_only_amount` | int/money | M7：已完结 REFUND_ONLY 且订单 ∈ 有效销售（金额 = case_lines 行级直取） |
| D 退款 | 退货退款：件数/金额 | `refund_return_qty, refund_return_amount` | int/money | M8：已完结 RETURN_AND_REFUND 且订单 ∈ 有效销售（同上） |
| D 退款 | 有效订单退款小计 | `refund_net_qty, refund_net_amount` | int/money | M10：M7+M8（**计入净现金的唯一退款桶**） |
| D 退款 | 退款率 | `refund_rate` | ratio-str/null | M12：`refund_net_amount/sales`；`sales=0 → null` |
| D 退款 | 已付被取消订单退款（信息列） | `refund_cancelled_qty, refund_cancelled_amount, refund_cancelled_missing_lines` | int/money/int | M9：已完结 CANCELLATION/CANCEL 且订单=CANCELLED；`amount` = **已知行金额小计**，缺失行不造数 → `missing_lines` 上报（页面显示“另有 N 行金额未知”） |
| E 实际 ROI | 退货货损（全损，USD） | `return_loss` | money-str | M13b：全损退货件数 × 单位成本解析值（人工优先，缺省 **30 CNY/件 ≈ $4.43**）→ 折 USD |
| E 实际 ROI | 单件货本来源（成本解析结果） | `unit_cost_used, cost_source` | money/enum | §4.2：`cost_source` ∈ `MANUAL`（命中 manual_product_costs 有效行）/ `DEFAULT_K1`（默认 30 元）；**`DEFAULT_K1` → 页面该行 ⚠ + tooltip，可跳 manual-costs 页补录** |
| E 实际 ROI | **净利润（毛利口径，页面金额核心列）** | `net_profit` | money-str | M18：`net_cash(内部) − units_sold×unit_cost_used − spend − platform_fee`；**负值红字**（与 roi<保本同号，§5.4-6）；M13 net_cash 仅内部不输出 |
| E 实际 ROI | 平台佣金（渠道费用，⚙ 可选列） | `platform_fee` | money-str | M19：已结算实际扣费 + 未结算 `sales × r̂`（本期全按 r̂ ≈11.6% 估；页面可覆写）；解析上线后自动分层 |
| E 实际 ROI | **实际 ROI（主指标）** | `roi_real` | ratio-str/null | M14：`(net_cash − return_loss) / spend`（全 USD 口径）；`spend=0` 或固定汇率配置缺失/异常 → `null` |
| E 实际 ROI | 保本实际 ROI（每 SPU 动态线） | `roi_breakeven` | ratio-str/null | M17：`NC′ ÷ (NC′ − COGS_kept − fee_est)`；`COGS_kept + fee_est ≥ NC′ → null`（结构性亏损，无保本线） |
| E 实际 ROI | 单订单广告成本 | `cpa` | money-str/null | M15：`spend / ad_orders`；`ad_orders=0 → null`（○ 可选列） |
| — 预留 | 订单结算金额 | — | — | M16：未输出（finance 归属落地后加列，见 §6.11） |

> D 组列与旧版草案的“取消退款计入退款合计”**已作废**：取消桶 = 信息列（M9），净现金只减 M10。
> **全表金额统一 USD（决策 D6）**：C/D/E 组金额均已是换算后的 USD（原生 VND/CNY 只存在于服务端中间量，见 §4.2 通用规则），不再有“物理分栏标币种”。

### 5.3 端点 envelope 与样例行（实测数值，2026-09-05 窗口全量）

```json
{
  "items": [{
    "spu_pk": 1448, "spu_id": "1736929955366339831",
    "title": "Áo thun nam tay ngắn …", "status": "ACTIVATE",
    "ad_count": 1, "ad_orders": 26, "spend": "157.4000", "gmv_ad": "472.9100",
    "roi_l0": "3.00", "ad_first_day": "2026-08-28", "ad_last_day": "2026-09-05",
    "order_count": 55, "units_sold": 27, "sales": "557.2078",
    "refund_only_qty": 1, "refund_only_amount": "19.5430",
    "refund_return_qty": 4, "refund_return_amount": "89.5445",
    "refund_net_qty": 5, "refund_net_amount": "109.0875", "refund_rate": "0.20",
    "refund_cancelled_qty": 37, "refund_cancelled_amount": "19.2912",
    "refund_cancelled_missing_lines": 36,
    "net_profit": "106.6544",   // M18: net_cash(内部)448.12 − COGS_all 119.67 − 广告157.40 − 平台费用64.40(≈11.6%) = 赚
    "platform_fee": "64.3965",  // M19: sales 557.21 × 11.56%（参考基线，见 meta.fee）
    "return_loss": "17.7288",   // 4 件全损退货 × 30 CNY × 0.1477（占位 K1 ≈ $4.43/件，见 meta.cost_assumption）
    "roi_real": "2.73", "roi_breakeven": "1.63", "cpa": "6.0538",
    "unit_cost_used": "4.4322", "cost_source": "DEFAULT_K1"   // 本样例无人工成本 → 默认 30 元 → 页面 ⚠
  }],
  "total": 111,
  "totals": {"row_count": 111, "spend": "1414.7700", "sales": "…",
              "refund_net_amount": "…", "net_profit": "…", "roi_real": "…"},   // 金额均为 USD(原币加总后一次换算);roi_real = Σ(net_cash−return_loss)/Σspend(服务端)
  "meta": {
    "fx": {"usd_vnd": "26330.0000", "cny_usd": "0.1477",   // 实现常量 0.14774（30 CNY≈$4.4322/件）
          "as_of": "2026-09-05", "source": "fixed-const（在线机制挂起，§4.6）"},
    "cost_assumption": "按 SPU 解析：人工成本(MANUAL)优先，无记录 → K1=30 CNY/件 ≈ $4.43/件（本样例 DEFAULT_K1，页面 ⚠）",
    "fee": {"mode": "baseline", "rate": "0.1156", "override": null, "note": "平台佣金=全部直接扣除(抽佣/联盟/运费类)；已结算按实际，未结算按基线；解析上线后自动分层"},
    "window": {"first_day": "2026-08-28", "last_day": "2026-09-05", "note": "ad=视图全窗口累计(供参考)；销售/退款=全历史(未裁剪，可传 w_start/w_end)"},
    "unattributed_refund_lines": 66,
    "computed_at": "…", "currency": {"display": "USD", "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"}}
  }
}
```

样例对账（同一 SPU，金额已统一 USD）：`L0 = 472.91/157.40 = 3.00`（平台口径，好看）；`实际 ROI = (448.12 − 17.73)/157.40 = 2.73`（17.73 = 4 件全损退货 × 30 CNY × 0.1477）。L0→实际的差距 = 平台归因 GMV（含自然单）≠ ERP 有效销售 + 有效订单退款 109.09 USD + 全损货本 17.73 USD。保本线校验：`COGS_kept = (27−4) 件 × 4.4322 = 101.94 USD`、`platform_fee = 557.21×11.56% = 64.40 USD` → `roi_breakeven = 430.39 ÷ (430.39−101.94−64.40) = 1.63`，实际 2.73 ≥ 1.63 → 绿（赚）。净利润校验：`net_profit = net_cash(内部) 448.12 − COGS_all 119.67 − 广告 157.40 − 平台费用 64.40 = +106.65 USD ≥ 0` —— 净利润为负 ⇔ 实际 ROI < 保本（§5.4-6），两条判断同号。

### 5.4 对齐不变式（端点单测 + 对账脚本断言）

1. **桶不相交**：`refund_net` 的订单 ∈ 白名单 && `refund_cancelled` 的订单 = CANCELLED → 无一行同时落入两桶 ⇒ 加总不重不漏。
2. **行合计 = 全量聚合**：对同一 `W`，Σ行(spend / sales / refund_net_amount / refund_cancelled_amount) == 去掉 products_spu 维后的独立全量聚合（SQL 同 CTE 两写一处校验）。
3. **spend 对账视图**：Σ行(spend) == `analytics.ad_product_links` 全表 `Σ real_cost_total`（2026-09-05 实测 1414.77，随每日 dump 增长）。
4. **roi 服务端自校验**：`roi_real == (net_cash − return_loss) / spend`（全 USD 同币，**无汇率因子**——net_cash/return_loss 已在服务层由原币经固定汇率换算）；`roi_l0 == gmv_ad/spend`（Decimal，无 float）；固定汇率配置缺失/异常 → `roi_real=null`，绝不输出近似值。
5. **金额缺失不静默**：净额桶 `refund_net_amount` 与 `refund_cancelled_missing_lines` 分开上报；未归属行数出现在 meta，页面页脚提示。
6. **保本一致性**：`roi_real ≥ roi_breakeven ⇔ 毛利口径利润 ≥ 0 ⇔ net_profit ≥ 0`（M18 净利润含 fee_est，同源推导，§4.2 M17/M18/M19）——测试断言页面红/绿与净利润正负同号，杜绝“标红却赚钱 / 绿字却亏损”。

### 5.5 SQL 模板（字段名与 §5.2 一一对应；行与 totals 共用同一组 CTE）

```sql
WITH ad AS (                      -- 广告侧：campaign × SPU → SPU（窗口全量）
    SELECT spu_pk,
           count(DISTINCT campaign_id)  AS ad_count,
           coalesce(sum(real_cost_total),0)   AS spend,
           coalesce(sum(order_value_total),0) AS gmv_ad,
           coalesce(sum(order_sku_total),0)   AS ad_orders,
           min(first_day) AS ad_first_day, max(last_day) AS ad_last_day
    FROM analytics.ad_product_links
    WHERE spu_pk IS NOT NULL
    GROUP BY spu_pk
), sales AS (                      -- 有效销售订单（口径 B）：paid_at ∈ W
    SELECT sl.spu_pk,
           count(DISTINCT so.id)  AS order_count,
           sum(sl.quantity)       AS units_sold,
           sum(sl.quantity * sl.unit_price) AS sales
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND so.status IN (:PAID_STATUSES)
      AND so.paid_at >= :w_start AND so.paid_at < :w_end
    GROUP BY sl.spu_pk
), refunds AS (                    -- 退款侧：case 完结 ∈ W；按订单有效性分桶
    SELECT sl.spu_pk,
           sum(cl.quantity)       FILTER (WHERE so.status IN (:PAID_STATUSES) AND c.case_type='REFUND_ONLY')
                                        AS refund_only_qty,
           sum(cl.refund_amount)  FILTER (WHERE so.status IN (:PAID_STATUSES) AND c.case_type='REFUND_ONLY')
                                        AS refund_only_amount,
           sum(cl.quantity)       FILTER (WHERE so.status IN (:PAID_STATUSES) AND c.case_type='RETURN_AND_REFUND')
                                        AS refund_return_qty,
           sum(cl.refund_amount)  FILTER (WHERE so.status IN (:PAID_STATUSES) AND c.case_type='RETURN_AND_REFUND')
                                        AS refund_return_amount,
           sum(cl.quantity)       FILTER (WHERE so.status='CANCELLED')
                                        AS refund_cancelled_qty,
           sum(cl.refund_amount)  FILTER (WHERE so.status='CANCELLED')
                                        AS refund_cancelled_amount,   -- 只含已知金额行
           count(*)               FILTER (WHERE so.status='CANCELLED' AND cl.refund_amount IS NULL)
                                        AS refund_cancelled_missing_lines
    FROM after_sales.cases c
    JOIN after_sales.case_lines cl ON cl.case_id = c.id
    JOIN commerce.sales_order_lines sl ON sl.id = cl.sales_order_line_id
    JOIN commerce.sales_orders so ON so.id = c.order_pk
    WHERE c.status IN ('CANCELLATION_REQUEST_COMPLETE','RETURN_OR_REFUND_REQUEST_COMPLETE')
      AND c.updated_at_source >= :w_start AND c.updated_at_source < :w_end
    GROUP BY sl.spu_pk
)
SELECT cp.id AS spu_pk, cp.spu_id, cp.title, cp.status,
       ad.ad_count, ad.ad_orders, ad.spend, ad.gmv_ad, ad.ad_first_day, ad.ad_last_day,
       s.order_count, s.units_sold, s.sales,
       r.refund_only_qty, r.refund_only_amount, r.refund_return_qty, r.refund_return_amount,
       coalesce(r.refund_only_qty,0)    + coalesce(r.refund_return_qty,0)    AS refund_net_qty,
       coalesce(r.refund_only_amount,0) + coalesce(r.refund_return_amount,0) AS refund_net_amount,  -- M10
       r.refund_cancelled_qty, r.refund_cancelled_amount, r.refund_cancelled_missing_lines,          -- M9
       s.sales - coalesce(r.refund_only_amount,0) - coalesce(r.refund_return_amount,0) AS net_cash   -- M13（原生 VND）
FROM commerce.products_spu cp
LEFT JOIN ad      ON ad.spu_pk = cp.id
LEFT JOIN sales   ON sales.spu_pk = cp.id
LEFT JOIN refunds ON refunds.spu_pk = cp.id
WHERE (ad.spu_pk IS NOT NULL OR sales.spu_pk IS NOT NULL OR refunds.spu_pk IS NOT NULL)  -- 行范围(§5.1-7)
-- 未归属提示(meta)：同 WHERE 条件下，case_lines 无 sales_order_line_id 或 line 无 spu 的行数
-- totals = 同一组 CTE 再聚合（不含 products_spu 维），与行加总必须相等（§5.4-2）
-- 【USD 换算发生在服务层】本 SQL 输出均为原生币（spend/gmv USD；sales/refunds/net_cash VND）；
--   return_loss 用 全损退货件数×K1×cny_usd、VND 金额 ÷usd_vnd，全部在端点里一次性换算后再序列化（§4.2 通用规则）
```

按天拆广告消耗（自定义日期范围时用，替代视图）：

```sql
-- 把 0006 视图的 daily CTE 搬出来，对 r.day 过滤后按 product_id 求和；
-- 展开 SQL 见 biz-doc/analytics/endpoint-join-keys.md §4（注意列名现为 shop_id/spu_id）
```

### 5.6 对齐验证记录（2026-09-05 对生产库只读实测）

| 验证项 | 结果 | 结论 |
| --- | --- | --- |
| 样例 SPU 1736929955366339831 全链可算 | 广告(1 计划, spend 157.40 USD, GMV 472.91, L0=3.00) × 有效销售(55 单/27 件/557.21 USD) × 退款(净额 109.09 USD；取消已知 19.29+缺失 36 行) → **净现金(内部) 448.12 − 货本 119.67 − 广告 157.40 − 平台费用 64.40 → 净利润 106.65 USD → 实际 ROI 2.73 / 保本 1.63** | 输出列全部能按 §5.2 计算，金额统一 USD；净利润含平台费用（基线 ≈11.6%） |
| 净额桶金额覆盖 | 完结状态 27 case/27 line，金额 **27/27（100%）** | M7/M8 行级金额直取即可，无需分摊造数 |
| 取消桶金额覆盖 | 完结状态可归行 236 case/246 line，仅 27 行有金额（缺 219；另 14 个取消 case 无行可归 → 计入未归属） | M9 必须“已知金额+缺失行数”双字段（§5.2） |
| 未归属规模 | 完结状态 66 条 case line 无法落到 spu（无 line 键或行无 spu） | meta `unattributed_refund_lines` + 页脚提示必需 |
| 合计一致性 | Σ行(spend) = 1414.77 == 视图全表总额（=1207.17 为 09-04 前旧快照） | 行/合计同 CTE 可对账；窗口随 dump 增长 |

---

## 6. 数据输入库表与字段含义

> 本文档字段 = 与生产库 `information_schema` 逐列核对后的现网形态（含 2026-09-05 命名重构后的列名）。
> 公共审计列（下面多数表都有，语义一致，不逐表重复）：
>
> - `id`：BigInteger 自增主键（`generate_always_as_identity`）
> - `raw_record_id`：原始 payload 引用 → `integration.raw_records.id`（查数疑点时反查上游原样 JSON）
> - `synced_at / created_at / updated_at`：同步落库时间 / 建行时间 / 最后更新时间（`updated_at` 由 `public.fn_touch_updated_at()` 触发器维护）

### 6.1 `commerce.shops` — 店铺维度 + 广告 seller 关联键

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | 内部店铺主键（**= shop_pk**，过滤用） | 主表店铺列 |
| `platform` | text | 渠道（本需求取 `'tiktok'`） | join 条件 |
| `shop_id` | text | TikTok 店铺 ID（**= 广告侧 seller_id**，ad_raw/视图用它 join 回店铺） | `ad_product_links.seller_id → shops.shop_id` |
| `account_name` | text | 店铺名 | 展示 |
| `region` / `seller_type` / `status` | text | 地区 / 卖家类型 / 状态 | 筛选 |
| `credential_id` | bigint | → integration.credentials | 不用 |

### 6.2 `commerce.products_spu` — SPU 维度主表（页面行源）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | **内部 SPU 主键（= spu_pk）** | 全部分组/过滤 |
| `shop_pk` | bigint FK | 所属店铺 | 店铺筛选 |
| `spu_id` | text | TikTok SPU 外部 ID（**= ad_product_links.product_id**） | 搜索 + 广告侧 join |
| `title` | text | 商品标题 | 展示 |
| `status` | text | 上架状态（生产存 `ACTIVATE`；下架 = `DEACTIVATE/DELETED/…`） | 展示 + “已下架仍退款”提醒 |
| `category_id` / `main_image_url` / `source_created_at` / `source_updated_at` | text/ts | 类目 / 主图 / 上游时间 | 展示（主图）、预留 |

### 6.3 `commerce.sales_orders` — 订单头（销售侧过滤 + 单头金额）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | 内部订单主键（order_pk） | line join |
| `shop_pk` | bigint FK | 店铺 | 筛选 |
| `order_id` | text | TikTok 订单号 | 钻取展示 |
| `status` | text | 订单状态（`PAID_SALES_ORDER_STATUSES` 白名单外的都排除，见 §4.1） | **销售过滤** |
| `payment_amount` / `total_amount` | numeric(20,4) | 实付金额 / 订单总额（含运费等，单头级，**不按 SPU 拆**） | 对账用（M6 的行级口径 ≠ 单头时给提示）；页面不用它直接算 SPU 金额 |
| `currency` | text | 币种（生产全 VND） | 栏目标注 |
| `order_time` / `paid_at` / `shipped_at` / `delivered_at` / `cancelled_at` | ts | 各生命周期时间 | **paid_at 归日**；shipped/delivered 供 P2 阶段分布（§3.3）；cancelled_at 给秒退预警 |
| `fulfillment_type` | text | 履约类型（平台履约/自履约） | 预留 |

### 6.4 `commerce.sales_order_lines` — 订单行（销售金额/件数按 SPU 的直接来源）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | 行主键 | case_lines / settlement 关联 |
| `order_pk` | bigint FK | 所属订单 | join sales_orders |
| `external_line_id` | text | TikTok 行 ID（与售后行的 `line_id/order_line_item_id` 对齐） | 售后关联键之一 |
| `spu_pk` / `sku_pk` | bigint FK（可空） | 商品/变体内部键（空 = 行先于商品目录到达，靠 snapshot 兜底） | **M5/M6 分组键** |
| `external_product_id_snapshot` / `external_variant_id_snapshot` / `product_name_snapshot` / `variant_name_snapshot` / `image_url_snapshot` | text | 成交时快照（FK 为空时的事实源） | 归属兜底 + 展示 |
| `quantity` | numeric(20,4) | 件数 | M5 |
| `unit_price` | numeric(20,4) | 行单价（商品价，不含运费） | M6 |
| `currency` | text | 币种 | 标注 |
| `line_status` | text | 行状态 | 预留（行级取消判断） |

### 6.5 `after_sales.cases` — 售后单头（取消/仅退款/退货退款）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | case 主键 | — |
| `shop_pk` | bigint FK | 店铺 | 筛选 |
| `order_pk` | bigint FK | 关联订单 | 归属链路起点 |
| `external_case_id` | text | TikTok 售后单 ID | 钻取展示 |
| `case_type` | text | `CANCELLATION` / `CANCEL`(旧) / `REFUND_ONLY` / `RETURN_AND_REFUND` | **退款分类键**（与订单有效性共同决定计入 M7/M8 还是 M9；取消 = IN 前两者） |
| `status` | text | case 状态机（§4.3，完结才算退款） | **金额计入门槛** |
| `reason_code` / `reason_text` | text | 退款/退货原因（商家责任判断的数据点） | P2 原因聚合 |
| `created_at_source` / `updated_at_source` | ts | 上游创建 / 最后更新时间 | **退款按完结(updated_at_source)归日**；与订单 shipped_at 比出阶段 |
| `refund_amount` | numeric(20,4) | case 级退款金额（TikTok 取消单带在 case 上；**生产仅 12 行有值**） | 归属规则 1/2 的来源之一 |
| `currency` | text | 币种 | 标注 |

### 6.6 `after_sales.case_lines` — 售后行（按 SPU 的退款件数/金额来源）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` | bigint PK | 行主键 | — |
| `case_id` | bigint FK | 所属 case | join |
| `sales_order_line_id` | bigint FK | → sales_order_lines（**经它拿到 spu_pk 才能按 SPU 归属**） | **归属关键链** |
| `external_case_line_id` | text | 售后行 ID | 兜底关联 |
| `quantity` | numeric(20,4) | 退/取消件数 | **M11** |
| `refund_amount` | numeric(20,4) | 行级退款金额（完结状态实测覆盖：净额桶 27/27 = 100%，取消桶 27/246，见 §5.6；缺失行不造数） | M7–M12 金额来源 |
| `currency` | text | 币种 | 标注 |
| `should_replenish_stock` | bool | 是否需补货入库（判断货是否退回） | P2 货损估算线索 |

### 6.7 `analytics.ad_raw` — 广告原始 dump（源头，页面只读校验/按天拆）

| 字段 | 类型 | 含义 | 本需求用途 |
| --- | --- | --- | --- |
| `id` / `idempotency_key` | bigint/text | 主键 / 幂等键 | — |
| `seller_id` | text | TikTok 店铺 ID（= `shops.shop_id`） | 店铺过滤 |
| `advertiser_id` | text | 广告账户 ID | 展示（预留多账户） |
| `endpoint` | text | 本需求只认 `…/post_product_list`（响应行带 SPU） | **行过滤** |
| `day` | date | 插件请求的自然日 | 自定义日期范围拆消耗的唯一路径 |
| `campaign_id` | text | 广告计划 ID | campaign 维度 |
| `request` / `response` | jsonb | 一次完整 HTTP 交换原样（`data.table[]` 每行 = campaign×SPU×day 的业绩字符串字段） | 原始校验/追溯 |
| `captured_at` / `received_at` | ts | 抓取/落库时间 | 审计 |
| `source` / `request_id` / `protocol_version` / `schema_version` | text/int | 溯源与版本 | 校验 |

### 6.8 `analytics.ad_product_links`（VIEW，不是表）— 页面 B 组的直接数据源

| 列 | 含义 | 用途 |
| --- | --- | --- |
| `seller_id` / `advertiser_id` / `campaign_id` / `product_id` | 广告 scope + SPU（= products_spu.spu_id） | 维度 |
| `product_name` / `product_status` / `gmv_max_bid_type` | 商品名/上架状态/bid 类型（取最后观测日） | 展示 |
| `observed_days` / `first_day` / `last_day` | 观测窗口元数据 | 窗口口径展示（§4.5） |
| `order_sku_total` | 出单量合计（TikTok Orders(SKU)，含自然归因） | M15 分母 |
| `real_cost_total` | **广告消耗合计** | **M1** |
| `order_value_total` | 出单 GMV 合计（归因口径） | **M3**（仅展示，非回款） |
| `shop_pk` / `spu_pk` | ERP 内部键（LEFT JOIN 富化，目录外为 NULL） | **聚合键**（NULL 行 = 未关联 SPU，另组展示） |

粒度 = (seller, advertiser, campaign, product) 一行；数值字段在视图内做过正则校验再 cast。语义详见 `biz-doc/analytics/ad-product-links-view.md`。

### 6.9 `reporting.product_profit_daily` — 每日 (SPU) 销售/成本中间表（销售侧可复用）

| 字段 | 类型 | 含义 | 用途 |
| --- | --- | --- | --- |
| `spu_pk` / `profit_date` | bigint/date | 商品 / 归属日 | M5/M6 复用的行键 |
| `units_sold` / `gross_revenue` | numeric | 售出件数 / 毛收入（= 本页 M5/M6 同口径，paid_at 归日 + 白名单） | 直接复用 |
| `estimated_cogs` / `estimated_gross_profit` | numeric | 估货本 / 估毛利（当前因无成本快照**全 NULL**） | 不用（见 §9-2） |
| `platform_fees` / `shipping_cost` / `refunds` | numeric | 平台费/运费/退款（当前**全 NULL**，未落地） | 不用（见 §9-3） |
| `currency` / `cost_method` | text | 币种（VND）/ 成本法 | 标注 |
| `calculation_version` / `calculated_at` | int/ts | 版本（重算增量保留旧版） | 取最新版本 |

### 6.10 成本链（本期 K1 = 30 CNY/件 占位；成本页上线后切换真实值）

**`procurement.manual_product_costs`**（人工成本录入，生产仅 2 行）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `spu_pk` | bigint FK | 商品 |
| `unit_cost` / `currency` | numeric(20,4)/text | 单位成本 / 币种 —— **2026-09-05 确认：录入保证为人民币 CNY**（与默认 K1 同币，统一走 CNY→USD；接口 currency 仍为自由 ISO 码，实施时在录入端默认/校验 CNY） |
| `valid_from` / `valid_to` | ts | 生效区间（valid_to NULL = 当前生效；每 SPU 只允许一条生效行） |
| `note` / `created_by` / `created_at` | text/text/ts | 备注/录入人/时间 |

**`reporting.product_cost_snapshots`**（成本快照，6h job 重建；**生产 0 行**）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `spu_pk` / `cost_method` | bigint/text | 商品 / 成本法（MANUAL_ENTRY / LATEST_PURCHASE_COST / …） |
| `unit_cost` / `currency` | numeric(20,4)/text | 单位成本 / 币种 |
| `valid_from` / `valid_to` | ts | 生效区间 |
| `source_purchase_quantity` / `source_purchase_amount` / `source_line_count` | numeric/int | 来源采购量/额/单数（可追溯） |
| `calculation_version` | int | 重算版本 |

**`linkage.effective_product_links`（VIEW）**：`spu_pk → procurement_product_id` 的覆盖关系视图
（列：`spu_pk / procurement_product_id / effective_relation_type / source_link_id / source_kind / effective_from / procurement_account_id / shop_pk`；
**注意 LEFT JOIN 视图对无关联 SPU 也发一行，只有 `effective_relation_type` 非空才算真有关联**）。

> 采购链现状：`procurement.procurement_products` 216 行（商品目录已同步），但
> `purchase_orders / purchase_order_lines` **0 行**（妙手采购单未同步/未跑），故“最新采购价”成本源不可用；
> coverage 端点 `GET /v2/reporting/missing-cost-products` 即“无成本 SPU”清单（页面可链过去）。

> **本期口径（决策 4/6 + 2026-09-05 成本解析确认）**：单位成本**按 SPU 解析**——① 命中 `procurement.manual_product_costs` 有效行（`valid_to IS NULL`，每 SPU 一条）→ 用录入值（**人工表保证人民币 CNY** → CNY→USD fx，`cost_source=MANUAL`）；② 未命中 → **默认 K1 = 30 CNY/件**（`cost_source=DEFAULT_K1`）→ 页面该行 ⚠ 提示并可跳 manual-costs 补录。货损 M13b / 保本 COGS_kept 共用同一解析值；同一公式只换“unit_cost 来源”。现网人工成本仅 2 行 → 绝大多数 SPU 本期显示 ⚠（默认 30 元），属预期。

### 6.11 `finance.settlement_*` + 结算归属解析 job/view —— M16 数据底座（净现金权威口径；解析已排期 D7）

| 表/字段 | 类型 | 含义 | 现状（实测） |
| --- | --- | --- | --- |
| `payouts` | — | 打款单头（shop_pk/external_payout_id/status/currency/amount/时间） | 有数据（打款级） |
| `settlement_statements` | — | 结算单头（payout_id/statement_time/period_start/period_end/currency） | 有数据 |
| `settlement_transactions.*`（结构化） | bigint FK/numeric | 订单/行/售后键 + 金额分项（EAV） | 545 行全无订单键；component 仅 `settlement_amount` —— **解析 job 已排期（D7），输出将含订单键 + 全费用分项** |
| `integration.raw_records`：`/finance/…/statement_transactions` payload | jsonb | **59 列宽行、自带 `order_id`**（上游结算明细原样）—— 事实源 | **2026-09-05 实测 57,027 行**：含 return_shipping_fee_amount（非零 608 行→去重 5 单，全负值，5/5 = RETURN_AND_REFUND）、customer_paid_shipping_fee_refund_amount（1,894 行）、platform_refund_subsidy_amount（121 行）、platform_commission_amount 等 |

> 决策 3（净现金口径）：以每笔订单结算净额 M16 为准、不做手工费用分项建模；D10 补充过渡层 —— **平台费用 = 已结算实际 fee_amount + 未结算 sales × r̂**（见 §4.2 M19）。
> 现状：结构化表未解析，原始 59 列（含 fee_amount / 退货运费 / 佣金）已在 raw_records，可经 order_id → 订单 → case → lines(spu) 归属（2026-09-05 验证 5/5 命中 RETURN_AND_REFUND）；**解析 job + view 已排期（D7）**。
> 落地前：净现金(M13, 内部) 用 sales − refund_net 预估，净利润/保本按「未含真实平台扣费（按费率基线 ≈11.6% 估）」口径并标注。

**退货运费承担判定（2026-09-05 数据探查）**：

```text
卖家承担该笔退货的运费 ⇔ 该订单在 statement_transactions 结算明细中存在 return_shipping_fee_amount ≠ 0
  （= actual_return_shipping_fee_amount，负值 = 从卖家结算款扣除；勿用 reason_code 猜）
辅助字段：platform_refund_subsidy_amount（平台承担/补贴）、customer_paid_shipping_fee_refund_amount（退买家原付运费）
归属链：raw.order_id → commerce.sales_orders.order_id → after_sales.cases.order_pk → case_lines → spu_pk
```

→ **已排期（D7，2026-09-05）**：结算归属解析 job + 只读 view（finance 域工作线）。产出后：① 已结算订单启用**实际扣费（fee_amount）**、未结算按基线 r̂（M19 自动分层），net_cash 基础切 M16 后已结算部分不再单扣平台费用；② 卖家承担的退货运费（return_shipping_fee_amount≠0）可直接扣入净利润；③ 参考基线 r̂ 与“已结算/未结算”分层由 view 现算。

**解析 job + view 规格（D7）**：

- 输入：`integration.raw_records` `…/statement_transactions`（59 列），按 `payload->>'id'` 去重（同一交易会被多次抓取重复入库）。
- 输出：结构化表（按 order_pk 归属，含 settlement_amount / gross_sales_amount / platform_commission_amount / return_shipping_fee_amount / refund 系列等，不再只留 settlement_amount 一个 component）+ **只读 view**（如 `finance.v_settlement_order`；模式参照 `analytics.ad_product_links`：DB 层 view、无 HTTP 端点、端点只读 view）。
- 归属链：`payload->>'order_id'` → `commerce.sales_orders` → `after_sales.cases`（退货/运费按 case）→ case_lines → spu_pk；多 SPU 订单按订单行金额占比分摊到 SPU。
- 参考基线：`r̂ = Σ|fee_amount| ÷ Σgross_sales_amount`（已结算交易级；作用域 = 当前窗口 + 店铺；2026-09-05 去重实测 ≈ **11.6%**）。

**费用字段字典（2026-09-05 实测，已结算交易去重后）：**

| 字段 | 实测占毛销售比 | 含义 | 归属 |
| --- | --- | --- | --- |
| `fee_amount` | **−11.58%** | **单笔平台总扣除（汇总字段，与分项自洽）** | 作为“平台佣金=全部直接扣除”的口径与基线 r̂ |
| `platform_commission_amount` | −5.03% | 平台交易抽佣 | 已含在 fee_amount |
| `affiliate_commission_amount`（=before_pit） | −0.33% | 联盟/达人佣金 | 已含 |
| `affiliate_ads_commission_amount` | −0.01% | 联盟广告佣金 | 已含 |
| `actual_shipping_fee_amount` | −3.96% | 实际运费 | 已含 |
| `shipping_fee_amount` | −2.54% | 运费（口径有别） | 已含 |
| `shipping_cost_amount` | −2.48% | 物流成本 | 已含 |
| `customer_shipping_fee_amount` | +0.13% | 买家运费返还/调整 | 已含（正向） |
| 其余 30+ 字段（支付费/退款管理费/税/FBT-FBM/保险/签收等） | 0 | — | — |

> ⚠ 勿用 `gross_sales_amount − settlement_amount` 当费用：settlement 含未结款/退款偏移，实测会得到 ≈78% 的错误“费率”。

### 6.12 配置与占位输入（非库表：页面 ⚙ 与端点参数）

| 配置项 | 默认值 | 影响口径 | 决策/出处 |
| --- | --- | --- | --- |
| `K1` 单位货本（**仅作未命中人工成本时的默认**） | **30 CNY/件 ≈ $4.43/件** | 货损 M13b / COGS_kept（M17）；命中人工成本时不用它 | D4；解析见 §4.2 |
| 及格线（心理线，不标红） | 1.5（可关） | 浅橙标注 | §7.2 |
| 退款率警戒线 | 30% | ⚠ + 红字 | §7.2 |
| 广告回本线（实际 ROI < 1.0） | 1.0 | 更深红（红底浅字） | §7.2 |
| 汇率（固定常量，D9） | **USD→VND = 26,330 / CNY→USD = 0.14774**（展示 0.1477；30 CNY ≈ $4.4322/件；2026-09-05，配置可改） | 全表金额换算 / 净利润 / 保本 / ROI | §4.6 |
| 日期窗口(端点参数,2026-09 review 补) | **默认不传 = 销售/退款全历史累计**;可选 `w_start`/`w_end`(ISO 日期)裁剪(销售 paid_at / 退款 updated_at_source);ad 无日期参数,恒整窗累计 | 行/合计同筛选 | §4.5/§5.1-6 |
| 行范围 | 有活动 SPU；可选 `include_all` | 空行金额全 0 | §5.1-7 |
| 平台佣金费率 r̂ | **参考基线 ≈11.6%（Σ\|fee_amount\|/Σgross，含抽佣/联盟/运费等全部直接扣除；页面可覆写 %；无结算样本 → 0 并标注）** | 保本 M17 / 净利润 M18 的 platform_fee（M19） | D10；解析上线后已结算部分自动用实际值 |

> 这些只影响**显示 / 换算策略 / 标色**，不改变底层原币口径定义（§4.2）。

---

## 7. 页面 UI 定稿（方案 A：账页式，2026-09-05 确认；对齐 §3.1 列与 §5.2 字段）

### 7.1 骨架（真实样例行数据）

```text
┌ [tts-erp]  SPU 实际 ROI ── 数据截至 2026-09-05（ad 窗口 08-28 ~ 09-05 全量）────┐
│ 结余带:  N 个 SPU · 消耗 $x · 销售 $x · 退款 $x · 货损 $x · 净利润 $x        │
│         · 整体实际 ROI n        （全表 USD · 固定汇率 D9 · 09-05）        │
├─────────────────────────────────────────────────────────────────────────────┤
│ [🔍 搜索 spu_id…] [店铺▾] [日期▾] [列开关⚙] [保本线=动态] [及格线▾1.5(可关)]  默认排序: 实际ROI↑ │
├─────────────────────────────────────────────────────────────────────────────┤
│  商品        │ 广告          │ 销售（有效单）   │ 退款               │ 净/ROI                     │
│ 图·spu_id·标题│广告数│消耗│平台GMV│ROI₀│有效单│件数│销售$│仅退│退货退款$│退款率│净利润$│货损$│保本│实际ROI│
│ 1736… Áo…   │  1  │157.40│472.91│3.00│ 55 │ 27 │557.21│…│109.09  │19.6%│106.65│17.73│1.63│ 2.73 │
│ 1736… 爆退品  │  4  │312.00│…   │3.80│ 90 │ 98 │930.50│…│372.20  │40.0%│−295.66│44.32│1.90│0.85  │ ← 红边：净利润<0（ROI 0.85<保本 ≈1.9）
├─────────────────────────────────────────────────────────────────────────────┤
│ ← 上一页  p/M  下一页 →   每页 100 条                                        │
└─────────────────────────────────────────────────────────────────────────────┘
[提示行] “全表 USD（原币 VND/CNY 服务端换算）· N 个 SPU 使用默认 30 元/件成本(⚠) · 已付被取消退款 N 行金额未知 · 未归属退款 N 行”
[警告 chip] GMV Max 归因含自然单、数据滞后修正 —— 广告列仅供对照，实际 ROI 以 ERP 侧为准
```

### 7.2 标色与阈值（默认值，页面 ⚙ 可调，不锁死）

| 情形 | 默认阈值（可调） | 视觉 |
| --- | --- | --- |
| 实际 ROI < 保本ROI（该 SPU 动态线 M17） | 结构亏损线（**默认主红判**） | 整行浅红边 + ROI 红字；显示“实际 x.xx < 保本 y.yy” |
| 使用默认成本（`cost_source=DEFAULT_K1`） | — | 标题旁/货损列 ⚠ + tooltip“无人工成本记录，按默认 30 元/件计算”，可点跳 manual-costs 补录 |
| 实际 ROI < 1.0 | 广告回本线（实际 ROI < 1.0 = 连广告费都带不回） | 更深红（红底浅字），置顶视觉更重 |
| 实际 ROI ≥ 保本 但 < 及格线 | **1.5**（心理及格线，⚙ 可调/可关） | 浅橙标注，不标红 |
| 退款率 > 警戒线 | **30%** | 商品标题旁 ⚠ + 退款率红字 |
| ROI / 除数为 0 / 固定汇率未配置 | — | `—`（不猜数）；配置恢复后自动出现 |
| 未完结售后存在（行内展开可见） | — | 售后 tab 内黄色“进行中”标 |

### 7.3 默认排序与交互

- **默认排序：实际 ROI 升序（最亏的排最前）**；列头可点切（消耗降序 / 退款率降序 / 净利润 / 销售）升/降三态。
- **行内展开（不跳页）**：点行 → 下方展开该 SPU 三个 tab —— `订单`（CANCELLED 标红）/ `售后`（未完结标黄、显示未知金额行）/ `广告 campaign×SPU`；再点收起。
- 搜索：spu_id 子串（placeholder 给示例引导）；去抖 300ms；空态给方向性文案。
- 加载 skeleton、`aria-live` 结余带、sticky thead、focus 可见、键盘可达、`prefers-reduced-motion` 关过渡；401 → 跳 login（沿用 console.js）。

### 7.4 展示细节（数字格式 / 口径标注）

- 金额：服务端下发 USD 字符串 → 前端千分位 `$`（2 位小数）；货损列头随行标注单件成本来源（人工价 MANUAL，或 `默认 30元/件 ≈ $4.43 ⚠`）；件数整数；ROI 2 位、退款率百分比显示。
- **换算单点**：换算只发生在服务端输出层一次（先原币加总再换，§4.2/§4.6）；页面拿到即 USD，不做二次换算；fx 取值时间随 meta 展示（合计带旁一行小字）。
- 列开关 ⚙：整组收/展（如“已付被取消（信息列）”“平台出单量(ad_orders)/单订单成本(cpa)”默认折叠，想看再开；平台 GMV 默认显示，对齐 §7.1 骨架）。
- 顶部提示行 + 结余带随筛选实时刷新；每页 100（上限 500 走 v2 分页约定）。
- 移动端（2026-09-06 重构）：布局走 Bootstrap 5.3.8 栅格/工具类 —— 结余带 xs 2 列 →
  lg 7 列降密度、工具栏 flex-wrap 纵向堆叠、列开关折叠进 `<details>`；表格
  `.table-responsive` 横滚 + 首列/表头 sticky（≤lg 首列吸左），小屏按断点
  nth-child 裁掉次要对比列（广告数/平台GMV/ROI₀/件数，576–991 裁 广告数/ROI₀）
  降低横滚量；列开关信息列（§7.5）全尺寸可用。

### 7.5 列可见性默认（⚙ 开关分组）

| 分组 / 列 | 默认 | 说明 |
| --- | --- | --- |
| 商品：图 / spu_id / 标题 / 状态 | 显示 | 标题 ellipsis + hover 全文 |
| 店铺 / 观测窗口 | 折叠 | ⚙ 可开 |
| 广告：广告数 / 消耗 / ROI₀ | 显示 | L0 仅供对照 |
| 广告：平台GMV | 显示 | 默认可见，对齐 §7.1 定稿骨架（页首/行内与 ROI₀ 同组对照） |
| 广告：平台出单量(ad_orders) / 单订单成本(cpa) | 折叠 | 信息列，⚙ 可开 |
| 销售：有效单 / 件数 / 销售$ | 显示 | |
| 退款：净额小计 / 退款率 | 显示 | |
| 退款：仅退 / 退货退款拆分、已付被取消（含 unknown 行数） | 折叠 | 钻取 tab 内可见；“另有 N 行金额未知”提示行保留在页脚 |
| 净/ROI：**净利润$** / 货损$ / 保本 / 实际ROI | 显示 | 主区域（净现金收入不展示；净利润负值红字，与红绿判据同号；平台费用（渠道费用）为 ⚙ 可选列） |

> 折叠列仍参与排序与合计，只是不占横向空间。
> 成本 ⚠ 不占独立列：凡 `cost_source=DEFAULT_K1` 的行在标题旁 + 货损列头显示 ⚠，tooltip 文案见 §7.2，点击可跳 `/v2/pages/manual-costs` 补录。

### 7.6 状态、文案与交互细则

- **加载**：首载表格 skeleton；筛选/翻页只刷新数据区与结余带（局部更新）。
- **错误态**：5xx/超时 → 表格区留白 + “加载失败 · 重试”；401 → 跳 login（沿用 console.js 的 fetch 401 处理）；403 → “无权限查看”。
- **空态**：搜索无结果 → “没有匹配该 spu_id 的 SPU（试试完整 ID）”；全窗口无任何活动 → “所选窗口暂无广告/销售/退款记录”。
- **口径 tooltip**（列头 hover 一句话，含 M# + 公式摘要 + 币种说明）：例：净利润 = “(净现金收入(内部) − 全部售出件货本) − 广告消耗 − 平台费用（已结算按实际、未结算按参考基线，页面可覆写）（USD）；≥ 0 = 赚，< 0 = 亏”；实际ROI = “净利润 ≥ 0 ⇔ ≥ 保本线”；货损 = “全损退货件数 × 单位成本解析值（默认 30元/件 ≈ $4.43，可改）”。
- **排序次级键**：实际 ROI 升序遇同值时按 广告消耗降序 排（结果可复现）。
- **刷新**：手动刷新按钮；进页/切窗口/改配置后重拉；不做自动轮询（报表型数据，避免跳动）。
- **键盘/a11y**：可见 focus、sticky thead + `scope`、结余带与加载态 `aria-live`、行内展开按钮可 Tab 到、`prefers-reduced-motion` 关闭过渡。
- **数字排版**：金额/ROI/退款率等宽右对齐；**净利润可负（负值红字）**，其余金额非负；`—` 表示 null/无解。

> 本稿只定义展示；数据字段与计算严格走 §5.2 契约，前端不做任何业务计算（§5.1-1）。

---

## 8. 数据就绪度实测（2026-09-05，只读查询）

| 输入 | 就绪度 | 实测 | 影响 |
| --- | --- | --- | --- |
| SPU 目录 | ✅ | products_spu 全量、含标题/状态 | 主表行源 |
| 广告消耗/出单（B 组） | ✅ | ad_raw 7,754 行；视图 337 对（228 广告 / 111 SPU）；窗口 08-28~09-05；其中 106 SPU 能带出 spu_pk | M1–M4 可算 |
| 销售（C 组） | ✅ | 870 单 / 907 行全 VND；**有效销售订单（白名单，口径 B）625 单**；679/907 行有 spu_pk（其余靠 snapshot，见 §6.4 兜底） | M5/M6 可算 |
| 退款（D 组） | ⚠️ 部分 | 281 case：取消 250（可归行 236 → M9 信息列）/ 退货退款 30 / 仅退款 1；**完结金额覆盖：净额桶 27/27、取消桶 27/246**；未归属 66 行 | M7–M12 可算：净额桶金额齐全直取；取消桶缺失行如实报 unknown（§5.2/§5.6） |
| 净利润（M18）/ 实际 ROI（M14） | ✅ | M18 = net_cash(M13, 内部) − 售出货本 − 广告消耗 − 平台费用（费率基线 ≈11.6%，M19）；M14 = (net_cash − 货损) ÷ 消耗（**金额统一 USD**，固定汇率 D9：26,330 / 0.1477） | 净利润/实际 ROI/保本本期即可算（固定汇率 + 费率基线） |
| 订单结算金额（M16）/ 退货运费 | 🔶 解析 job 已排期（D7） | raw 59 列已含 order_id 与 fee_amount / 退货运费 / 佣金等；`finance.settlement_*` 结构化解析 + view 排期中 | 落地前：净利润/保本用 M13 + 平台费用基线（M19 ≈11.6%）预估并标注；落地后按实际自动变准 |
| 货物成本（货损 M13b） | ⚠️ 人工优先+默认 | 人工成本**现网仅 2 行**；解析 = 命中 `manual_product_costs` 有效行 → MANUAL，未命中 → **默认 30 CNY/件（DEFAULT_K1）** | 货损可算；DEFAULT_K1 的行页面 ⚠ 并可跳 manual-costs 补录（§4.2/§7.2） |
| profit_daily | ⚠️ | 1,509 行 VND（08-31~09-05）；cogs/profit/fees/refunds 全 NULL | 只复用 units/gross_revenue |

**结论：P0 页面的“消耗(USD) / 销售(有效销售订单，口径 B) / 退款件数 / 退款金额(净额+信息列分桶、缺失兜底) / 净利润 M18（净现金 M13 为内部中间量）/ 全损货损 M13b / 实际 ROI M14 / 保本线 M17”本期全部可做（固定汇率 D9 → M14/M17/M18 本期即可算）；M16 结算口径与退货运费扣项依赖 finance 归属**解析 job**（解析 job 已排期，D7，见 §6.11/§9-3）。**

---

## 9. 数据缺口与阻塞项（诚实清单）

1. ~~**币种不统一（主指标换算阻塞）**~~ → **已解决（2026-09-05 拍板）**：广告账户币种 = USD，销售/退款 = VND；跨币换算走**在线最新汇率**（机制见 §4.6）。唯一残留 = 汇率是**外部在线依赖**，需 job + 落库 + 失败/过期降级（机制已定，属实施项，不再是口径阻塞）。
2. **货物成本：按 SPU 解析——人工成本优先，未命中才默认 30 CNY/件（已解，不再阻塞）**：现网人工成本仅 2 行（快照/采购单近空）。2026-09-05 确认解析顺序：① `manual_product_costs` 有效行（valid_to IS NULL）→ 用录入值（MANUAL）；② 未命中 → 默认 K1=30 CNY/件（DEFAULT_K1）并**页面 ⚠ 提示（可跳 manual-costs 补录）**。命中率低 → 多数 SPU 显示 ⚠ 属预期；成本页铺开后自动切真实值。成本币种 CNY → USD 走在线汇率（§4.6 已含 CNY/USD 币对）。
3. **每笔订单结算金额（M16）→ 解析 job 已排期（D7，2026-09-05）**：数据源已定位（`integration.raw_records` 的 `/finance/…/statement_transactions` 59 列 + 自带 order_id，交易级 `fee_amount` = 平台总扣除，与分项自洽）。交付 = finance 归属解析 job + 只读 view（§6.11 规格）。**落地前**：净利润/保本以 M13 + 平台费用基线（M19 ≈11.6%）预估并标注口径；**落地后**：已结算订单用实际 fee、未结算用基线 r̂，M16 净额基础启用后已结算部分不再单扣，均自动变准。
4. **取消退款金额大面积缺失（M9 信息列精度受限）**：完结状态取消桶 246 行仅 27 行有金额（缺 219）；净额桶 27/27 齐全不受影响。处理：M9 输出「已知金额小计 + 缺失行数」如实上报，**不造数**；如需要精确取消退款额，走 `integration.raw_records` 反查 cancellation payload 的 refund_amount blob（P2）。另有完结状态 66 条 case line 无法归属 SPU（无 line 键/行无 spu）→ meta `unattributed_refund_lines` 页脚提示。
5. **广告归因非纯广告增量**：GMV Max 归因含自然单，且 TikTok 数据有滞后/回滚修正（已退订单在广告侧可能隔天才剔除）。页面只标警告，不做修正。
6. **广告消耗无“截止某日”精确口径**：ad_product_links 全窗口累计（§4.5），自定义日期范围会口径错位——默认窗口方案规避。
7. **case_type 历史双拼写 + 状态枚举漂移**：SQL 按实测集合（§4.3）并留防御性未知分支。

---

## 10. 实现路径建议（评审后执行，对齐 ad-product-links-ui 的流程）

1. **本文定稿**：核心口径 + 页面样式（含全表统一 USD）已拍板（§11 决策记录），进入方案评审（plannotator 流程同 ad-product-links-ui）。
2. 方案评审（plannotator 流程同 ad-product-links-ui）。
3. TDD 实施：
   - ~~汇率在线 job~~ —— D9：本期**固定汇率常量**（§4.6），不建 job/不建表；净利润(M18)/实际 ROI(M14)/保本(M17) 本期直接可算；
   - 端点 `GET /v2/analytics/spu-roi`（readonly；SQL 参照 §5；money 字符串、limit/offset、`{items,total,totals}` 家族 envelope）；
   - 页 `GET /v2/pages/spu-roi` + `static/js/spu-roi.js`（§7 骨架）；
   - finance 结算归属解析 job + view（D7，finance 域工作线）：产出 M16 数据底座 + 平台佣金参考基线 r̂（§6.11 规格）；
   - P1 钻取（复用已有只读端点，零新表）。
   - 测试：造数用 TEST_ 前缀（参照 `tests/analytics/` + `tests/api/` 现有惯例），全量 `bash scripts/test.sh fast` 0 fail。
4. 文档：external-api.md TL;DR + 章节、CHANGELOG、biz-doc 口径落点。

---

## 11. 决策记录与剩余实施项

**已拍板（2026-09-05）**：

1. **币种与汇率（D9 更新）**：广告全部按 USD；跨币换算**本期用固定汇率常量**（USD→VND=26,330 / CNY→USD=0.1477，配置可改，§4.6）；在线汇率（原 D1）挂起。
2. **销售口径 = 口径 B**：排除 CANCELLED（含已付后被取消的单），排除后即**有效销售订单**（白名单见 §4.1）——销售金额/件数全以它为基数；已付被取消订单的退款进 M9 信息列，**不扣净额**，避免“没算过的销售又被扣一遍”。
3. **不建模平台佣金/运费手工分项（决策 3，净现金口径）**：净现金权威口径 = **每笔订单结算金额**（净额，M16，含平台扣费与退款调整）；解析 job 已排期（D7）；未落地前用 M13（sales − refund_net）预估，并以费率基线补偿平台费用（D10，见已拍板 9）。
4. **退货 = 全损口径；单位成本 = 人工成本优先**：RETURN_AND_REFUND 已完结即全损（实测 26/26 妥投后退、货退不回）；每件货损 M13b = 全损件数 × 单位成本解析值（命中 `manual_product_costs` 用真实 CNY 价，未命中默认 30 CNY ≈ $4.43 并 ⚠）；**实际 ROI（M14）= (净现金(内部) − 全损货损) ÷ 广告消耗（全 USD）**。命中人工成本即自动换真实值，公式不变。
5. **页面样式 = 方案 A 账页式（D5）**（2026-09-05）：沿用操作台家族（米白+砖红+mono）；默认排序实际 ROI 升序；**红绿判据 = 实际 ROI vs 该 SPU 保本线（M17，动态）**，广告回本线（实际 ROI<1.0）/ 退款率警戒 30% 为辅（⚙ 可调）；点行行内展开订单/售后/广告三 tab。UI 定稿见 §7。
6. **全表金额统一 USD 展示（D6）**：不再分 USD/VND 栏——VND（销售/退款金额）÷ USD→VND、CNY（货本 30 元）× CNY→USD 均在服务端输出层一次换算；底层计算保持原币防舍入；固定汇率常量随配置（D9，见 §4.6/§3.4）。
7. **页面不展示净现金收入（2026-09-05）**：净现金收入(M13) 仅作内部中间量；金额核心列 = **净利润（毛利口径，M18）**（负值红字），与 ROI/保本判据同号（§5.4-6）。
8. **结算归属解析 job + view 排期（D7）**：把 59 列结算 raw 解析为按订单/行/case 归属的结构化数据并建只读 view（§6.11 规格），作为 M16 数据底座与佣金参考基线的来源。
9. **保本线/净利润扣除平台佣金（D10）**：平台佣金 = 平台从销售额直接扣除的全部费用（抽佣/联盟/运费类等；单笔总扣 = 交易级 `fee_amount`）。**已结算订单 → 实际扣费；未结算订单 → sales × 参考基线 r̂**（Σ\|fee_amount\|/Σgross，实测 ≈11.6%），页面可覆写输入 %；解析上线后自动分层。

**剩余实施级小问题（不阻塞需求定稿，方案评审时定）**：

1. 在线汇率（原 D1）何时恢复：本期固定常量即可；如需在线化再启动 §4.6 机制方案（届时定主源/兜底源）。
2. ~~finance 结算归属解析 job 是否排期~~ → **已解决（D7：已排期，见已拍板 8）**。
3. 页面角色：readonly 即可，还是退款金额明细需要更高角色？（建议 readonly）
4. ~~日期默认窗口：ad 观测窗口（推荐，口径自洽）还是自然月？~~ → **已解决（2026-09 review）**：端点默认不裁剪（销售/退款 = 全历史累计；ad = 视图全窗口累计），需要窗口时显式传 `w_start`/`w_end`（§4.5）；`meta.window` 明示 ad 观测窗口供参考。
5. （D10 已定基线段）参考基线的统计粒度：当前 = 窗口 + 店铺全量；是否需按近 N 天 / 按类目细分？（默认不细分）
