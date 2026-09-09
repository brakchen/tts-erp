# SPU ROI 利润计算口径（业务文档）

> 来源：`tech-doc/analytics/spu-real-roi-dashboard.md` §4.2 + `tech-doc/analytics/roi-calc-prompt.md`
> 底层权威：`handoff/spu-roi-full-loss-rubric.md`（v9 当前）
> 整理日期：2026-09-07

---

## 一、利润公式

$$
\text{净利润} = \text{GMV}_{\text{USD}} - \text{退款}_{\text{USD}} - \text{广告消耗}_{\text{USD}} - \text{采购成本}_{\text{USD}}
$$

### 1.1 净收入（按订单分层计算）

$$
\text{总净收入}_{\text{USD}} = \frac{\sum \text{line\_net\_vnd}}{\text{USD\_VND}}
$$

其中每行的净收入:

$$
\text{line\_net\_vnd} =
\begin{cases}
\text{order\_SETTLEMENT} \times \dfrac{\text{line\_gmv}}{\text{order\_gmv}} & \text{已结算订单} \\
\text{line\_gmv} \times 0.692 & \text{未结算订单}
\end{cases}
$$

**已结算订单**：在 `finance.settlement_transactions` 中有记录。`order_SETTLEMENT` = 该订单的 `SETTLEMENT` component 金额，按行 GMV 比例分摊。已扣完所有平台费 + 运费 + 联盟佣金 + 退款调整。

**未结算订单**：无 SETTLEMENT 记录，按 $1 - 30.8\% = 69.2\%$ 估算（30.8% 为平台佣金基线 $\hat{r}$，2026-09-06 实测；实测验证实际扣费 ≈ 35.9%，差 5.1pp 为联盟 + 运费 + 平台补贴）。

**判定是否已结算**：

```sql
EXISTS (
    SELECT 1 FROM finance.settlement_transactions st
    WHERE st.order_pk = sales_orders.id
)
```

### 1.2 采购成本

$$
\text{采购成本}_{\text{USD}} = (\text{effective\_qty} + \text{full\_loss\_qty}) \times \text{unit\_cost}_{\text{CNY}} \times \text{CNY\_USD}
$$

- $\text{effective\_qty}$：有效件数（PAID_SALES_ORDER_STATUSES 白名单，排除退货 case）
- $\text{full\_loss\_qty}$：全损件数 = 退货件数(27) + 海外取消件数(133) = **160**
- $\text{unit\_cost}_{\text{CNY}}$：真实成本（人工标注价格 > 货源价 > 默认兜底价格 40 CNY）
- $\text{CNY\_USD}$：在线 fx 快照（2026-09-07: 0.148823）

### 1.3 利润

$$
\text{profit}_{\text{USD}} = \text{total\_net}_{\text{USD}} - \text{ad\_cost}_{\text{USD}} - \text{procurement}_{\text{USD}}
$$

---

## 二、口径定义

### 2.1 有效销售订单（口径 B）

`PAID_SALES_ORDER_STATUSES`（`tts_erp_v2/db/constants.py`）：

| 状态 | 计入有效? |
| --- | :---: |
| AWAITING_SHIPMENT / PARTIAL_SHIPPING / AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED | ✓ |
| UNPAID / ON_HOLD / **CANCELLED** | ✗ |

### 2.2 订单分类（v9 口径）

$$
\text{全损件} = \text{退货件数} + \text{海外取消件数}
$$

| 类别 | 条件 | 计入有效? | 全损? | 计入采购? |
| --- | --- | :---: | :---: | :---: |
| 有效订单 | PAID_STATUSES + 无退货 case | ✓ | ✗ | ✓ |
| 退货 | RETURN_AND_REFUND / REFUND_ONLY 已完结 | ✗ | ✓ | ✓(计入全损) |
| 海外取消 | CANCELLED + `action_code=38301` | ✗ | ✓ | ✓(计入全损) |
| 国内取消 | CANCELLED + 未到海外 | ✗ | ✗ | ✗ |

**海外判定**：`fulfillment.tracking_events.action_code = 38301`（"Arrived in destination country/region"）

**实测数据（2026-09-07）**：

| 类别 | 件数 |
| --- | ---: |
| 有效件数(排除退货) | 655 |
| 退货(RETURN_AND_REFUND + REFUND_ONLY 已完结) | 27 |
| 海外取消(CANCELLED + action_code=38301) | 133 |
| **全损合计** | **160** |
| 国内取消(物流未到海外) | 182 |

### 2.3 采购成本优先级链

| 优先级 | 来源 | 表 | 说明 |
| ---: | --- | --- | --- |
| 1 | 人工标注价格 | `procurement.manual_product_costs` | `valid_to IS NULL` |
| 2 | 货源价 | `procurement.procurement_products.source_unit_cost` | 按 `synced_at DESC` 取最新 |
| 3 | 默认兜底价格 | 硬编码 40 CNY/件 | 兜底 |

### 2.4 汇率

| 币种对 | 在线 fx 值(2026-09-07) |
| --- | ---: |
| $\text{USD→VND}$ | 26,001.886 |
| $\text{CNY→USD}$ | 0.148823 ($= 1/6.7194$) |

数据源：`fx.exchange_rate_snapshots` 最新快照。

### 2.5 平台佣金

- 已结算订单：`SETTLEMENT` 已扣完所有费，无需二次扣
- 未结算订单：基线 $\hat{r} = 30.8\%$（实测 35.9%，差 5.1pp 为联盟 + 运费 + 补贴）

---

## 三、各指标计算方法

### M1: 广告消耗合计

$$
\text{ad\_cost}(s) = \sum \text{ad\_product\_links.real\_cost\_total}
$$

### M5: 售出件数（有效销售）

$$
\text{effective\_qty}(s) = \sum \text{sales\_order\_lines.quantity} \cdot \mathbb{1}[\text{status} \in \text{PAID\_STATUSES}] \cdot \mathbb{1}[\text{无退货 case}]
$$

### M5d: 全损件数（v9）

$$
\text{full\_loss\_qty} = \underbrace{\sum \text{case\_lines.quantity}}_{\text{退货：RETURN\_AND\_REFUND + REFUND\_ONLY}} + \underbrace{\sum \text{sol.quantity}}_{\text{海外取消：CANCELLED} \cap \text{action\_code}=38301}
$$

### M18: 净利润

$$
\text{net\_profit} = \frac{\sum \text{line\_net\_vnd}}{\text{USD\_VND}} - \text{ad\_cost} - \text{procurement\_usd}
$$

其中：

$$
\text{line\_net\_vnd} =
\begin{cases}
\text{order\_SETTLEMENT} \times \dfrac{\text{line\_gmv}}{\text{order\_gmv}} & \text{已结算} \\[6pt]
\text{line\_gmv} \times (1 - 0.308) & \text{未结算}
\end{cases}
$$

$$
\text{procurement\_usd} = (\text{effective\_qty} + \text{full\_loss\_qty}) \times \text{unit\_cost}_{\text{CNY}} \times \text{CNY\_USD}
$$

$$
\text{判亏条件：} \text{net\_profit} < 0 \iff \text{实际 ROI} < \text{保本 ROI}
$$

---

## 四、Prompt（可复用分析）

按以下口径计算每个在售 SPU 的净利润、实际 ROI、保本 ROI，输出主表。

**有效销售订单**：`status ∈ {AWAITING_SHIPMENT, PARTIAL_SHIPPING, AWAITING_COLLECTION, IN_TRANSIT, DELIVERED, COMPLETED}`，排除退货 case。

**全损件数**：

- 退货 = RETURN_AND_REFUND / REFUND_ONLY 已完结件数
- 海外取消 = CANCELLED + `tracking_events.action_code=38301` 件数
- 国内取消（物流未到海外）≠ 全损，不计货本

**净收入**：已结算用 `SETTLEMENT × (line_gmv/order_gmv)` 分摊；未结算用 `line_gmv × 0.692`。

**采购成本**：`unit_cost = 人工标注价格 > 货源价 > 默认兜底价格 40 CNY`，`procurement = (effective_qty + full_loss_qty) × unit_cost × CNY_USD`

**利润**：`profit = Σ line_net_vnd / USD_VND − ad_cost − procurement`

**输出字段**：SPU | 广告$ | 有效GMV$ | 采购$ | 退款$ | 净利润$ | 实际ROI | 保本ROI | 全损件

---

## 五、版本历史

| 版本 | 关键变化 | 全损件数 | 净利润 |
| --- | --- | ---: | ---: |
| v5 | `action_code=38301` 替代 `delivered_at` | 127 | +$2,938 |
| v6 | + CANCELLED 货本补扣 | 127 | +$2,384 |
| v7 | + 已结算/未结算分层（SETTLEMENT） | 127 | −$1,368 |
| **v9** | **退货=全损 + 海外取消=全损 + 国内取消≠全损** | **160** | **−$1,230** |

## 六、实测验证点（2026-09-07）

| 检查项 | 结果 |
| --- | --- |
| 退货件数 | 27 |
| 海外取消 | 133 |
| 国内取消 | 182 |
| 全损合计 | 160 件 |
| 有效件数 | 655 |
| 已结算订单比例 | 608/637 = 95.4% |
| 实测平台扣费 | 35.9% |
| 平台基线 $\hat{r}$ | 30.8% |
| 净利润 | −$1,230.45 |

## 七、文档关系

- `tech-doc/analytics/spu-real-roi-dashboard.md` §4.2：M1–M19 完整公式（本文件是业务友好摘要）
- `tech-doc/analytics/roi-calc-prompt.md`：可复用 Prompt（本文件 §4 精简版）
- `handoff/spu-roi-full-loss-rubric.md`：项目记忆 v9 全损口径
- `biz-doc/analytics/post-product-list-field-semantics.md` §9：全损 SQL 查询
