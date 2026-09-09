# SPU ROI 全损退货口径(项目记忆,v9 当前版)

> **当前唯一有效版本(2026-09-07)**。v8(不区分退货/取消)、v7(真实成本)、v6/CANCELLED 补扣)、v5(action_code=38301)、v4(delivered_at) 均已作废。
> 上次更新:2026-09-07
>
> **基准文档(利润计算唯一 truth source)**：`biz-doc/analytics/spu-roi-profit-calculation.md`
> 采购成本优先级链: 人工标注价格 > 货源价 > 默认兜底价格(40 CNY)
> 其他文档(tech-doc/roi-calc-prompt)已同步到相同命名

## 利润公式(v9)

```
profit_usd = 总净收入(USD) − 广告消耗(USD) − 采购成本(USD)

总净收入(USD) = Σ(line_net_vnd) / USD_VND
  where:
    已结算订单 line_net = SETTLEMENT × (line_gmv / order_gmv)  # 按比例分摊实际到账
    未结算订单 line_net = line_gmv × 0.692                     # 30.8% 平台抽成估算

采购成本(USD) = (effective_qty + full_loss_qty) × unit_cost_cny × CNY_USD
  where:
    effective_qty    = 有效件数(白名单,排除退货 case)
    full_loss_qty    = 退货件数 + 海外取消件数
    unit_cost_cny    = 真实成本(人工标注价格 > 货源价 > 默认兜底价格 40 CNY)

全损 = 退货件 + 海外取消件  ← 国内取消 ≠ 全损
```

## v9 三类订单分类

| 类别 | 条件 | 归类 | 有效? | 全损? | 采购? |
| --- | --- | --- | --- | --- | --- |
| 有效订单 | `PAID_SALES_ORDER_STATUSES` + 无退货 case | 正常销售 | ✓ | ✗ | ✓ |
| 退货 | `RETURN_AND_REFUND / REFUND_ONLY` 已完结 | 全损 | ✗(从有效移出) | ✓ | ✓(计入全损) |
| 海外取消 | `CANCELLED` + `action_code=38301`(到海外) | 全损 | ✗ | ✓ | ✓(计入全损) |
| 国内取消 | `CANCELLED` + 未到海外 | 非全损 | ✗ | ✗ | ✗(不采购) |

## 关键参数

| 项 | 值 | 来源 |
| --- | --- | --- |
| `USD_VND` | 26,001.886 | `fx.exchange_rate_snapshots` 在线(2026-09-07) |
| `CNY_USD` | 0.148823 | 同上(1/6.7194) |
| `PROCUREMENT_CNY` | 40 CNY/件 | 用户指定(默认 30) |
| `FEE_BASELINE` | 0.308 | tech-doc D10(2026-09-06 实测) |
| 物流判定字段 | `tracking_events.action_code = 38301` | "Arrived in destination country/region" |

**不要用过期常量**: 26,330 / 0.1477(已过期 ~1%)

## 分类明细(实测 2026-09-07)

| 类别 | 件数 | 说明 |
| --- | --- | --- |
| 有效件数(排除退货) | 655 | `PAID_STATUSES` − 退货 case |
| 退货 | 27 | RETURN_AND_REFUND(26) + REFUND_ONLY(1) |
| 海外取消 | 133 | CANCELLED + `action_code=38301` |
| **全损合计** | **160** | 退货(27) + 海外取消(133) |
| 国内取消(≠全损) | 182 | CANCELLED,未到海外 |

## 关键数字(实测 2026-09-07)

| 指标 | 值 |
| --- | --- |
| SPU | 112 |
| 广告消耗 | $6,529.57 |
| 有效 GMV | $14,541.76 |
| 总净收入 | $9,622.88 |
| 采购成本 | $4,707.97 |
| **净利润** | **−$1,230.45** |
| 实际 ROI | 1.4737 |
| 保本 ROI | 1.7210 |
| 净利润率 | −8.46% |

## v8 → v9 对比

| 项 | v8 | v9 | 差 |
| --- | ---: | ---: | ---: |
| 净利润 | −$1,367.58 | −$1,230.45 | +$137 |
| 采购成本 | $4,051.73 | $4,707.97 | +$656 |

v9 采购成本更高(含退货件的货本),但总净收入也更高(排除退货 case 后 GMV 减少,但不含退货的净收入更准确)

## 版本历史

| 版本 | 关键变化 | 净利润 |
| --- | --- | ---: |
| v3 | RETURN_AND_REFUND 完结 = 全损 | — |
| v4 | `delivered_at` 替代 | $2,938.89 |
| v5 | `action_code=38301` 替代 | $2,938.18 |
| v6 | + CANCELLED 货本补扣 | $2,384.56 |
| v7 | + 真实成本(货源价 > 40 CNY) | $2,938.89 |
| v8 | + 已结算/未结算分层 | −$1,367.58 |
| **v9** | **退货=全损,海外取消=全损,国内取消≠全损** | **−$1,230.45** |

## 与文档差异(待同步)

- `tech-doc/analytics/spu-real-roi-dashboard.md` §2 全损口径 → v9 已同步
- `tech-doc/analytics/spu-real-roi-dashboard.md` §4.2 M18/M19 → v6 补丁 + v9 改动
- `biz-doc/analytics/post-product-list-field-semantics.md` §9 → 全损联表说明(v9 口径)

## action_code 速查

| code | 含义 | v9 中 |
| ---: | --- | --- |
| **38301** | Arrived in destination country/region | ✅ 海外取消=全损判定 |
| 50101 | Delivered (签收) | — |
| 70201 | Returning | — |
| 80101 | Returned to seller | — |

## data_source 速查(settlement_components)

| component_code | 含义 | v9 用法 |
| --- | --- | --- |
| **SETTLEMENT** | 卖家实际到账 | ✅ 已结算订单净收入 |
| PLATFORM_COMMISSION | 平台抽佣 | 验证(实测 ~15%) |
| AFFILIATE_COMMISSION | 联盟佣金 | 验证 |
| SHIPPING_FEE | 运费 | 验证 |
| CUSTOMER_REFUND | 已退款 | 验证 |
