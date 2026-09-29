# 店铺抽成费率 r̂ 的口径矛盾（待拍板）

> 状态：**未决** — 需要在 feature/shop-fee-rate 上线前确认。
> 发现日期：2026-09-29。发现方式：实现 `analytics.shop_fee_rate` 后，
> 按文档公式实测得 12.2%，与文档写的 30.8% 差 2.5 倍。

## 1. 矛盾是什么

仓库多份文档写：

```text
r̂ = Σ|fee_amount| ÷ Σgross_sales_amount ≈ 30.8%   （2026-09-06 D10 重定）
```

出处：`tech-doc/analytics/spu-real-roi-dashboard.md` §4.2/§5.2/§6.11、
`tech-doc/analytics/roi-calc-prompt.md`、`biz-doc/analytics/spu-roi-profit-calculation.md`。

但按这个公式在真实数据上算，得到的是 **12.2%**：

| 口径 | 实测（tts_erp_v3_test，近 180 天，604 笔已结算交易） |
| --- | ---: |
| `Σ\|FEE\| / Σ GROSS_SALES` | **12.23%** |
| `(Σ\|FEE\| + Σ\|运费类\|) / Σ GROSS_SALES` | 21.47% |
| `Σ\|抽佣+联盟+运费\| / Σ GROSS_SALES` | 14.99% |
| `1 − SETTLEMENT / GROSS_SALES` | 76.20% |

而且**文档自己就自相矛盾**：同一份 `spu-real-roi-dashboard.md` 的「费用字段字典」
记 `fee_amount` 实测占毛销售 **−11.58%** —— 这个值与 A 口径的 12.23% 吻合，
与 30.8% 不吻合。

也就是说：**30.8% 与它自己声称的公式对不上**。这是仓库既有问题，
不是 2026-09-29 店铺级改造引入的；改造只是把 30.8% 换成逐店铺实测后，
矛盾才被显式暴露出来。

## 2. 复现方式（只读，可直接在生产库跑）

```bash
set -a; source .env; set +a
.venv/bin/python scripts/probe_shop_fee_rate_definition.py
# 可选：--days 90 / --shop-pk 12
```

脚本只发 SELECT 且事务设为 READ ONLY，不写库、不需要 `ALLOW_PROD_DESTRUCTIVE`。

## 3. 为什么必须在上线前定

`r̂` 直接决定未结算订单的净利估算：

```text
unsettled_net = unsettled_sales × (1 − r̂) × (1 − 退款率)
```

r̂ 从 30.8% 变成 12.2%，等量 GMV 的估算净收入会高约 27%
（`(1−0.122)/(1−0.308) ≈ 1.27`）。这会直接改变 SPU ROI 页的净利润与保本线，
进而影响投放决策。不能由实现者单方面选择。

## 4. 待拍板选项

| 选项 | 含义 | 需要改什么 |
| --- | --- | --- |
| **A. 采用 `Σ\|FEE\| / ΣGROSS_SALES`** | 认为文档的 30.8% 是历史错算，当前实现正确 | 改文档：把 30.8% 重定为实测值；`FEE_RATE_BASELINE` 重定；UI 文案同步 |
| **B. 采用更宽口径**（`FEE` + 运费类 = 21.47%，或抽佣+联盟+运费 = 14.99%） | 认为「平台抽成」应含卖家承担的运费 | 改 `jobs/finance_fee_rate.py` 的 component 集合；同步改 `FEE_RATE_BASELINE` 与文档 |
| **C. 维持 30.8%** | 认为 30.8% 来自某个尚未查明的、更宽的口径 | **需要先给出 30.8% 的确切推导式**（哪个 component 集合 + 哪个分母 + 哪个作用域），否则无法复现，也无法按店铺细化 |

## 5. 实现现状

当前 `analytics.shop_fee_rate` 实现的是**选项 A**：

```text
fee_rate       = Σ|FEE| / Σ GROSS_SALES          （仅 FEE 与 GROSS_SALES 币种一致的交易）
coverage_ratio = Σ GROSS_SALES(有 FEE) / Σ GROSS_SALES(窗口内全部已结算)
```

- 未过门槛（样本 <50 单 / 覆盖率 <80%）的店铺**不写行**，读取侧回退全局基线
  `FEE_RATE_BASELINE = 0.308`。
- 前端费率状态卡会**同时显示**来源（页面覆写 / 店铺实测 / 全局基线）、
  样本量、覆盖率与快照日期 —— 所以「实测 12% 而基线 30.8%」这个差异是
  可见的，不会被静默吞掉。

调整为 B/C 只需改 `tts_erp_v2/jobs/finance_fee_rate.py` 里的
`_SQL_SHOP_FEE_RATE` component 集合 + `FEE_RATE_BASELINE`，测试相应更新。
