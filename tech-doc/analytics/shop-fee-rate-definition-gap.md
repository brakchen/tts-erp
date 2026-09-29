# 店铺抽成费率 r̂ 的分母口径（已定位）

> 状态：**已确定**（2026-09-29，在生产库 `tts_erp` 上只读实测）。
> 结论：`r̂ = Σ|FEE| / Σ line_gmv`，`line_gmv` = 订单行 `quantity × unit_price`
> = **客户实付（折扣后）**。
> 复现命令：`.venv/bin/python scripts/probe_shop_fee_rate_definition.py`

## 1. 起点：文档里的 30.8% 对不上

仓库多份文档写 `r̂ = Σ|fee_amount| ÷ Σgross_sales_amount ≈ 30.8%`。
但按这个公式在生产库实测只有 **12.55%**；而且同一份
`spu-real-roi-dashboard.md` 的「费用字段字典」又记 `fee_amount` 实测占毛销售
**−11.58%** —— 字段表与公式自相矛盾，30.8% 无来源。

## 2. 生产库实测（tts_erp，近 180 天，1204 笔已结算订单）

### 2.1 分母到底是什么

| 候选分母 | 合计 | 相对 line_gmv |
| --- | ---: | ---: |
| `Σ(quantity × unit_price)` = **line_gmv** | 727,148,240 | 100.00% |
| `CUSTOMER_PAYMENT`（客户实付） | 728,896,839 | **100.24%** |
| `AFTER_SELLER_DISCOUNTS_SUBTOTAL` | 745,386,338 | 102.51% |
| `GROSS_SALES`（折扣前挂牌价） | 1,230,117,867 | **169.17%** |

**关键**：`sales_order_lines.unit_price` 存的是**折扣后实付价**
（与结算单 `CUSTOMER_PAYMENT` 只差 0.24%），而 `GROSS_SALES` 是
**折扣前挂牌价**（= `AFTER_SELLER_DISCOUNTS_SUBTOTAL` + `|SELLER_DISCOUNT|`，
卖家折扣实测占毛销售 39.4%）。

### 2.2 逐单恒等式（决定性证据）

```text
SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND      （FEE/退款为上游负值）
```

| 指标 | 结果 |
| --- | ---: |
| 相对残差中位数 | **0.000%** |
| \|残差\| ≤ 1% line_gmv | 61.0% |
| \|残差\| ≤ 5% line_gmv | 91.3% |
| 汇总差 | +1.75% of line_gmv |

这条恒等式说明：**`FEE` 已经是「平台从卖家结算款里扣掉的全部」，且其基准
就是 line_gmv**。因此：

* 用 `line_gmv` 当分母 → **21.23%**（正确）
* 用 `GROSS_SALES` 当分母 → 12.55%（错：分母被放大 69%）
* 把 `FEE + 运费类` 相加 → 错：**运费已在 FEE 内**，相加会重复扣
  （`|FEE|` = 154.4M，`|PLATFORM_COMMISSION|` 只有 69.8M，运费类 116.4M）

### 2.3 逐店铺 r̂（正确口径）

| shop_pk | r̂ | 已结算订单数 | line_gmv |
| ---: | ---: | ---: | ---: |
| 314 | **21.35%** | 908 | 549,173,618 |
| 68234 | **20.85%** | 296 | 177,974,622 |

两店覆盖率均 100%、样本量远超 50 单门槛，都会正常产出快照。

> ⚠️ 注意与兜底基线的关系：这两店会走**实测 21%**；`FEE_RATE_BASELINE
> = 0.308` 只作用于**尚无实测快照**的店铺（样本 <50 单 / 覆盖率 <80% /
> 快照 >7 天）。0.308 经用户 2026-09-29 拍板保留不变，见 §4。

## 3. 为什么分母必须是 line_gmv（而不只是「实测更准」）

页面估算未结算订单用的公式是：

```text
unsettled_net = unsettled_sales × (1 − r̂) × (1 − 退款率)
```

其中 `unsettled_sales` = 同一套 `line_gmv`（`quantity × unit_price`）。
费率的分母必须与它作用的变量同基准，否则 `(1 − r̂)` 不是「扣掉平台费后的比例」。
用 `GROSS_SALES` 当分母会把 r̂ 算小 41%，使未结算订单的估算净收入系统性偏高。

## 4. 影响与遗留

**已随实现修正**：

* `analytics.shop_fee_rate` 的分母改为订单级 `line_gmv`
  （表列 `line_gmv_covered` / `line_gmv_total`），并加「一单多笔结算交易时
  line_gmv 只计一次」的处理。
* 测试用 `test_gross_sales_component_does_not_affect_rate` 钉死「GROSS_SALES
  不得影响费率」。
* 文档与 UI 文案同步改为 `Σ|FEE|/Σ行GMV`。

**已决策（2026-09-29 用户拍板）**：

* `FEE_RATE_BASELINE` **保持 0.308 不变**。
  背景：生产实测该公式给出 ~21%，而 0.308 是**无实测快照店铺的兜底值**，
  所以对这类店铺会低估未结算净收入（`1−0.308` vs `1−0.21`）。
  用户明确选择暂不重定——**这是有意保留，不是漏改**，请勿把它当 bug“顺手修复”。
  若将来要改：只需改 `tts_erp_v2/analytics/spu_profitability/`
  `_implementation.py::FEE_RATE_BASELINE` 并同步文档，但属业务口径变更，
  需用户确认。

**仍待处理（非本次范围）**：

* `tech-doc/analytics/spu-real-roi-dashboard.md` 里 M18/M19 多处仍写
  「≈30.8%」作为历史说明，仅 M19 主行与费率段已更新为店铺实测；
  是否全量重写该文档可按需要另开文档 lane。
