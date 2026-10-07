# SPU 利润劣化告警：历史回测报告

> **证据状态：历史、不可直接外推到当前生产 evaluator。** 本报告保存 2026-10-05/07 的可复现观察结果；它不是产品契约，也不是当前生产阈值的证明。

## 1. 运行条件

历史 probe：

```bash
rm -f /tmp/spu-profit-deterioration-backtest.json
set -a; . /home/schan/tts-erp/.env; set +a
timeout 180 .venv/bin/python scripts/probe_spu_profit_deterioration_thresholds.py \
  --confirm-read-only-production --lookback-days 30 --max-spus 300 \
  --statement-timeout-ms 90000 \
  --output /tmp/spu-profit-deterioration-backtest.json
.venv/bin/python scripts/probe_spu_profit_deterioration_thresholds.py \
  --verify-artifact /tmp/spu-profit-deterioration-backtest.json
```

安全边界：生产形态数据库必须显式 read-only 确认；事务为 `REPEATABLE READ + READ ONLY`；事实查询有 statement/lock timeout；输出 aggregate only，不含店铺/SPU/订单标识。

## 2. 历史 evaluator 条件

- anchor：T-2；
- 日期：UTC 日；
- observation anchors：30；
- bounded shop×SPU keys：144；
- shops：2；
- candidate matrix：648 组；
- probe recovery 包含 `roi_recovery/recovery`，与现行生产枚举不同；
- ROI absolute/relative 组合语义不能视为当前最终决策。

当前目标生产契约已改为 T-1 + shop-local IANA，因此本报告数字不能证明当前告警量、阈值或 recovery。

## 3. Observed evidence

- fact date：2026-08-13..2026-10-03；
- observation anchors：2026-09-04..2026-10-03；
- daily fact rows：6,507；
- manual cost coverage：88.1944%；
- fee-v2 fresh shop estimate：144/144，stale fallback=0，baseline=0；
- FX：最新 exchange-rate snapshot；
- digest：`sha256:396511d04b029531a3fa038f38cf50e9ba4b34d3d7c389b7c753d45eda7b0b8b`；
- artifact：`immutable=true`、`aggregateOnly=true`、`rowLevelIdentifiers=omitted`；
- SQL scope、evaluability boundary、formula input contract self-check passed。

历史默认 warning config 下 sample status：

| window | fast sufficient / insufficient / unavailable | shifted sufficient / insufficient / unavailable |
| --- | --- | --- |
| 1d | 46 / 4200 / 7 | 43 / 4002 / 208 |
| 3d | 192 / 4052 / 19 | 186 / 3763 / 314 |
| 7d | 285 / 3821 / 169 | 269 / 3499 / 507 |

这些数据说明样本 gate 对结果影响很大，但不能直接说明当前 T-1/shop-local 的覆盖率。

## 4. 历史 seed

历史 seed 使用两组内部 evidence（原名 fast/confirmation）、1d/3d/7d、warning/critical，包含 ROI absolute/relative、net-profit decline、min spend/orders/ad-orders。

所有值只能标记为 `回测暂定/seed_fallback`。在以下条件满足前不得升级为“生产回测阈值”：

1. owner 确定 ROI absolute/relative 的 AND/OR；
2. probe 与生产共用同一个 evaluator；
3. 按 T-1、每店 IANA 日期重跑；
4. recovery/confirmationStatus 与生产枚举一致；
5. 输出 per-anchor volume、distinct SPU volume、样本覆盖和 persistence；
6. 扩大时间/店铺覆盖并记录 evaluator/config hash。

## 5. 下一次回测必须记录

- code commit、evaluator version/hash、config hash；
- anchor/timezone policy；
- source data through；
- shop/key/anchor aggregate counts；
- warning/critical distinct SPU per anchor；
- sample reason distribution；
- confirmationStatus/persistence denominator；
- candidate selection rationale；
- immutable aggregate artifact digest。
