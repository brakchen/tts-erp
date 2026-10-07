# SPU 利润劣化告警：测试计划与结果

> 结果日期：2026-10-07。所有数据库测试只能通过 `bash scripts/test_isolated.sh ...` 运行。

## 1. 验收原则

测试证明产品/技术契约，不得把内部 UI 技法或已经废弃的交互固化为权威。

- 领域测试：evaluator、sample gate、state/severity、confirmation status。
- 物化测试：canonical facts、事务、跨时区替换、catch-up、上游水位。
- API 测试：auth、严格参数、一个 AlertDecision、业务 totals、coverage/error。
- browser 测试：主用户流程、displayChange、键盘、loading/error/stale/disabled。
- E2E：live route/auth/filter/drill，不允许 release gate 以全 skip 假绿。

## 2. 必须新增或重写

### 2.1 领域

- 1d/3d/7d current/previous 精确边界；
- shifted persistence evidence；
- AND/OR owner 决策的 absolute-only/relative-only 边界；
- previous≤0、zero spend、missing facts、zero gates；
- current + persistence -> confirmationStatus 转换矩阵；
- warning/critical inclusive 边界；
- recovery denominator 为 0 时 null。

### 2.2 物化

- `(shop_pk, anchor_date)` 替换不删除其他店铺同日历史；
- 两个 IANA 时区跨日运行两轮；
- 停机多日后补齐缺失 anchors；
- 上游未成功时不生成新 basis；
- 任一失败 rollback 并保留最近成功 snapshot；
- config version/hash 与 API 一致。

### 2.3 API

- 每个 item 是一个用户 AlertDecision，而不是独立 layer row；
- `currentOrderCount` 全局 `COUNT(DISTINCT order_pk)`；
- warning/critical SPU 数按 distinct `spu_pk`；
- totals 不受分页和 severity/state 明细筛选影响；
- sample reason、confirmationStatus、requestId、coverage、warnings；
- config invalid、basis incoherent、never-materialized、stale-readable 分开编码。

### 2.4 Browser

- 页面没有 layer/sample 下拉；
- window/severity/state/activity/anchor URL round-trip；
- displayChange 允许箭头、差额和基于展示值的百分比；
- displayChange 不改变 severity/state/totals/request query；
- Enter 激活 drill CTA；
- loading/error 隐藏旧 summary；
- warning 用文本+图标+结构信号，不断言某个 gradient 字符串；
- publish 后 effective config/audit 自动刷新。

## 3. 2026-10-07 已执行结果

| 命令 | 结果 |
| --- | --- |
| `bash scripts/test_isolated.sh unit tests/analytics/test_spu_deterioration_alert.py` | exit 0 |
| `bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert_page.py` | exit 0 |
| `bash scripts/test_isolated.sh sync tests/sync_worker/test_spu_deterioration_alert_job.py` | exit 0 |
| `bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py -rs` | exit 0，4 passed |
| `bash scripts/test_isolated.sh api tests/api/test_spu_deterioration_alert_api.py tests/api/test_pages.py` | exit 1；`tests/api/test_pages.py:466` 仍要求 `repeating-linear-gradient` |

冻结审查的 34 个相关文件在测试结束时与 live 工作区一致。

## 4. 当前假绿/错误契约

- browser test 固化了前端 displayChange；这是产品允许的展示计算，不应再判为违规，但必须补“不参与业务决策”的断言。
- browser test 要求无纹理、API static test 又要求存在纹理；两者冲突，应改为可观察的非颜色信号测试。
- 旧测试把独立 layer 行和行计数当产品对象；迁移后必须按 AlertDecision/distinct SPU 重写。
- runtime config mutation matrix、scheduler registration/catch-up、真实 HTTP error response、差异 draft reload 仍缺覆盖。
- API fixtures 存在 `severity=critical + sampleStatus=unavailable` 等不可能组合，应改为领域一致 fixture。

## 5. 每个实施阶段的命令

```bash
bash scripts/test_isolated.sh unit tests/analytics/test_spu_deterioration_alert.py
bash scripts/test_isolated.sh api tests/api/test_spu_deterioration_alert_api.py tests/api/test_runtime_config.py tests/api/test_pages.py
bash scripts/test_isolated.sh sync_worker tests/sync_worker/test_spu_deterioration_alert_job.py
bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert_page.py
bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py -rs
bash scripts/test_isolated.sh fast
```

文档拆分本身只需要验证链接、路径、引用和 markdown；一旦修改代码或测试，必须执行对应 narrow tests 与 fast suite。
