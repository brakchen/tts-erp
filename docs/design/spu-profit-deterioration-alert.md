# SPU 利润劣化告警：技术设计与回测结论

> 状态：设计/回测完成，未实现产品页、API、迁移或调度器。所有回测阈值均标记为 **回测暂定**，不得在实现前当作生产阈值。
>
> 业务口径权威仍为 [`../business/spu-profitability.md`](../business/spu-profitability.md)；本设计只定义告警的比较、配置、展示和运维边界。

## 1. TaskStartSnapshot 与交付边界

- branch：`feature/spu-profit-deterioration-alert`
- initial HEAD：`ce3e9e665e1eeaa2c667b786bf541f27db4c5375`
- 初始任务 worktree：clean
- tower-do revision：`188`
- 本阶段唯一写入路径：本设计、实施计划、只读回测 probe。
- 明确不做：产品页实现、生产迁移、生产配置发布、调度器注册、Git stage/commit/push、`docs/handoff/ACTIVE.md` 修改。

### 1.1 Change Necessity

当前 SPU 盈利页有服务端 `roi_real`、净利润和分页/钻取，但没有跨等长窗口的劣化状态、样本门槛、确认窗口、可审计阈值来源或醒目告警。把判断放到前端会复制公式并造成阈值漂移；只在请求时计算又无法稳定提供每日告警量、持久性和恢复率。因此需要一个由盈利领域模块拥有事实、配置平台拥有有效配置、物化任务拥有历史快照、API/页面只消费结果的独立告警能力。

### 1.2 Requirement Ready Check

| 检查项 | 结论 |
| --- | --- |
| 单位 | shop × SPU；同一 SPU 的多个 campaign 在窗口内先聚合 |
| 事实 | 复用现有 `analytics.spu_profitability` 的 canonical `roi_real`：`(current_net_revenue - observed_full_loss_cost) / ad_spend` |
| 窗口 | 等长、非重叠；批次到 T-1，观察锚点 A=T-2；快窗口与确认窗口均按需求定义 |
| 判断 | ROI 劣化为触发主因，净利润劣化为业务影响，广告消耗/订单数为可靠性门槛 |
| 非法百分比 | previous ROI ≤ 0 时绝不计算百分比；使用 `profit_to_loss`、`loss_expanding`、`loss_to_profit` 等状态 |
| 配置 | `config.runtime_config_items` 的已发布版本是运行时唯一权威；seed/backtest 只作没有发布版本时的可见 fallback |
| 透明度 | 返回 effective value、source、version、updatedAt、updatedBy/audit revision、校验状态 |
| 视觉 | 文本 + 图标/徽章 + 行/卡片/横幅，不以颜色作为唯一信号 |

### 1.3 Reuse/existence check

已检查现有维护方案：

- 领域公式：`tts_erp_v2/analytics/spu_profitability/_formula_v10.py` 的纯 `calculate()` 和类型结构；不重新实现盈利公式。
- 查询/快照：`analytics/spu_profitability/_implementation.py` 和 `_snapshot.py`；告警实现应从此 seam 获取事实，不能直接从 HTTP adapter 拼 SQL。
- 配置：`db/models/config.py`、`runtime_config/resolver.py`、`runtime_config/validation.py`、`runtime_config/repository.py`、`api/v2/config.py` 及 `runtime-configs.html/js`；不新增配置库或依赖。告警 key 使用唯一 per-key seam `validate_spu_deterioration_alert_runtime_mutation(...)`，generic rollout 行为保持不变。
- 调度：`sync_worker/scheduler.py` 的 `JobSpec`、系统 job 和 catch-up 语义；告警 job 复用此平台。projection-window 已合并到 master（merge commit `75e2370`），其 lane 已由 master commit `6c29f46` 清理，不再是 active/blocking dependency；实现前按 §3.2 重新同步 master 并检查当前 active owner。
- 页面/API 测试：`tests/api/test_spu_roi_api.py`、`tests/api/test_runtime_config.py`、`tests/sync_worker/test_scheduler_jobs_coverage.py`、现有 browser/E2E smoke 模式。
- 外部生态：没有需要引入的阈值/统计依赖；Decimal、标准库 dataclass 和已有 SQLAlchemy/pytest 足够。新增依赖没有收益，故不加。

### 1.4 Architecture integrity and complexity budget

- 领域层拥有事实、聚合、状态和阈值决策；HTTP 只校验参数、序列化和授权；浏览器不复制公式。
- 配置平台拥有发布、乐观锁、回滚和审计；业务代码只读取已解析 payload。
- 每日物化快照用于历史可追溯；页面读取快照而非重复扫描全部订单。
- 复杂度预算：新增 1 个告警领域 module、1 个只读/管理 adapter 扩展、1 个系统 job、1 张告警快照表（如实现评审决定保留历史）、1 个页面 profile、4 层测试。禁止把告警判断散落到多个 route、template 和 JS 文件。
- 初版不做自动通知、短信/邮件、跨店排名、机器学习阈值、单 SPU 预测内容或任意自定义窗口。

## 2. 回测方法与 observed evidence

### 2.1 可复现命令与安全边界

实际执行的命令（生产数据库只读）：

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

probe 行为：

1. 未设置 URL、URL 无 database name、malformed URL、`tts_erp_test*`、`tts_erp_v3_test`、`tts_erp_test_template` 均 fail closed；生产形态库由共享 `tts_erp_v2.api.deps.is_prod_shaped_db()` 判定，覆盖 `tts_erp`、`tts_erp_prod`、`tts_erp_prod_*`，且必须显式传 `--confirm-read-only-production`。
2. 复用 `analytics.spu_profitability._snapshot.consistent_read_snapshot`，在首条事实 SELECT 前建立 `REPEATABLE READ` + `READ ONLY`，并断言 `SHOW transaction_isolation=repeatable read`、`SHOW transaction_read_only=on`；随后设置事务本地 90 秒 statement timeout、2 秒 lock timeout，只发 SELECT/只读配置，绝不 commit 写入。
3. 日期和 top shop×SPU 数量均参数化；`bounded_keys` CTE 以 `activity_orders DESC, shop_pk ASC, spu_pk ASC` 在 SQL 内先排序并 LIMIT，之后才读订单/广告事实；默认 anchor end 为当前日 T-2，最大回看 365 天。
4. `scoped_order_lines` 在 `bounded_keys` 后按日期/key 限定订单行，并再次限定 `(so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')`；`selected_orders`、sales/settlement 聚合以及 `refund_lines` 与两个 `full_loss_lines` UNION arms 必须只由此 scoped relation 派生，并在 scope 后再聚合。已对齐当前 master 的 canonical `FormulaInput`：completed `REFUND_ONLY`/`RETURN_AND_REFUND` case-line refund amounts 仅在 settlement absent 时聚合为 `confirmed_unsettled_refund_vnd`，cancellation refund 不折入；SQL grouped alias、`DailyFact`、window aggregation、adapter mapping 及 formula contract self-check 必须保持完整。启动前 `self_check_sql_scope()` 对 bounded-key 与 scoped-order-line 两处 status predicate 及这些 CTE 关系做 executable regression guard；`self_check_formula_input_contract()` 防止 required consumer field 漂移；Python 纯函数负责窗口、canonical formula、evaluability/sample_status、状态、样本门槛和矩阵。
5. JSON 只输出日期、计数、分布和比例，省略店铺/SPU 标识以及所有 row-level 数据；末尾保存 `sha256` canonical digest 的 immutable aggregate-only evidence artifact。

执行结果（observed）：

- query fact date：`2026-08-13` 至 `2026-10-03`。
- observation anchors：`2026-09-04` 至 `2026-10-03`，共 30 个；anchor 语义为 T-2。
- daily fact rows：6,507。
- usable shop×SPU keys：144；shops：2（仅汇总计数，未写入标识）。
- current manual cost coverage：88.1944%；其余使用 canonical K1 fallback 40 CNY/件。
- fee-v2 freshness：probe 只读 `calculation_version='fee-v2'`，严格复用维护 seam 的日期规则：`calculated_on >= calculated_at.date() - 7 days`；本次 144 个 key 均使用 fresh `shop_estimate`，`stale_fallback=0`、`baseline=0`。该结果是当前快照事实，不代表未来配置状态。
- FX：使用最新 `fx.exchange_rates` USD snapshot（未使用 fallback constants）。
- candidate matrix：648 个候选组合被评估，报告保留 24 个汇总候选。
- probe exit：0。

### 2.2 计算定义

对每一个 shop×SPU×window：

- campaign：先按 SPU 和日聚合 `mixed_real_cost`、广告订单数，窗口再求和。
- 销售：按订单行聚合金额/件数；订单数 `COUNT(DISTINCT order_pk)`。
- 结算、售后、全损：按现有实现的订单/line 关系聚合，之后调用 `_formula_v10.calculate()`，而不是平均每日 ROI。completed `REFUND_ONLY`/`RETURN_AND_REFUND` case-line refund amounts 在 settlement absent 时进入 `confirmed_unsettled_refund_vnd`；settled rows 不进入，`CANCELLATION`/`CANCEL` refund amounts 只保留在 cancellation field，不折入该字段。
- 实际 ROI：canonical `roi_real`。广告消耗为 0 时 ROI 为 null，进入 `sample_insufficient`/不可判断，不补 0。
- `net_profit` 使用同一次 aggregate 的公式结果。
- prior ROI > 0 时：`roi_decline = (prior-current)/prior`；prior ROI ≤ 0 时只使用显式状态。
- `net_profit_decline` 同理只在 prior net profit > 0 时计算。
- `sample_status`/evaluability 先于 state classification：任一比较窗口无 required fact row 为 `unavailable`；有 fact 但任一 ROI 为 null（包括 zero-spend）或 warning gates 不满足为 `sample_insufficient`；只有两窗 facts 存在、ROI defined 且 gates 满足才是 `sufficient`。threshold/gate 为 0 不能把 missing/null ROI 变成 `stable`。

比较窗口（首尾均包含）：

| fast | current | previous |
| --- | --- | --- |
| 1d | T-2 | T-3 |
| 3d | T-4..T-2 | T-7..T-5 |
| 7d | T-8..T-2 | T-15..T-9 |

确认窗口整体向前平移 7 天：1d 为 T-9 vs T-10，3d 为 T-11..T-9 vs T-14..T-12，7d 为 T-15..T-9 vs T-22..T-16。窗口不重叠。

状态枚举：`profit_to_loss`、`loss_expanding`、`loss_to_profit`、`roi_deterioration`、`net_profit_deterioration`、`roi_recovery`、`recovery`、`stable`、`sample_insufficient`、`unavailable`。`profit_to_loss` 和 `loss_expanding` 不依赖非法百分比。

### 2.3 指标定义与证据可用性

| 指标 | 定义 | 本次证据 |
| --- | --- | --- |
| usable coverage | 有至少一个 paid/cancelled order fact 的 shop×SPU 日事实 key | 可用，6,507 日行 / 144 keys |
| ROI/net-profit distribution | 在各窗口聚合后，按 ROI/net profit 的 count、min、p25、median、p75、max | probe 具备纯计算 seam；若某窗口无可用值返回 null，不伪造 0 |
| candidate matrix | 每个 windowDays × ROI abs/relative × net-profit decline × gates 的 comparison、sufficient、alert count/rate、state counts | 已执行 648 候选并输出汇总 |
| per-anchor alert volume | 每个 anchor、windowDays、config 下 warning/critical 的 shop×SPU 数量；输出 `per_anchor`，不把跨 anchor 总数命名为 daily | 已执行；summary 另给 median/mean/max/anchor_count/anchor_total，并在 30 anchors 给 `30_anchor_total` |
| persistence | fast 告警在对应 shifted confirmation comparison 仍告警的比例 | 已执行；以同一 `(anchor, shop×SPU)` key 的 fast alert 与 confirmation alert 交集 / fast alert key 计算 |
| reversal/recovery | 对每个 fast alert 的同一 `(anchor, shop×SPU)` 配对 confirmation decision，无论 confirmation 是否 alert；state 为 `loss_to_profit`/`recovery`/`roi_recovery` 即 numerator，fast alert 数为 denominator | denominator=0 返回 null；不能把 confirmation 未 alert 的配对丢出分母；当前 evidence 为真实 0 或比例，missing/unavailable 只按此配对口径解释 |
| data limitations | ad_daily 缺失、FX/cost/rate current valuation、时区 | 明确记录，不把缺失当作正常值 |

> 当前 probe 输出是阈值选择证据，不是最终生产物化逻辑。probe 已输出各窗口 current/previous 的 ROI、net-profit、ad-spend、order-count count/min/p25/median/p75/max、sample_status/state counts、per-anchor warning/critical volumes 与 recovery numerator/denominator/rate；`self_check_sql_scope()` 验证 bounded scoped CTE 仍包住 refund/full-loss facts，`self_check_formula_input_contract()` 验证当前 master 所需 confirmed-unsettled-refund consumer path；bounded query 为可重复性使用 UTC 日，生产实现必须使用每店 IANA timezone。

### 2.4 默认层级与回测暂定取舍

**回测暂定** defaults（seed/fallback only）：

| 层 | warning | critical | 取舍 |
| --- | --- | --- | --- |
| fast 1d/3d/7d | ROI abs delta 0.20、relative decline 20%、net-profit decline 25%、min spend 100 CNY、min orders 3、min ad orders 0 | ROI abs delta 0.40、relative 40%、net-profit decline 40%、min spend 300 CNY、min orders 5 | 快层重视及早发现，门槛避免低样本噪声；critical 要求更大绝对/相对变化 |
| confirmation 1d/3d/7d | ROI abs delta 0.15、relative 15%、net-profit decline 20%、min spend 100 CNY、min orders 3、min ad orders 0 | ROI abs delta 0.30、relative 30%、net-profit decline 35%、min spend 300 CNY、min orders 5 | 确认窗应比 fast 更容易确认“有方向的变化”，但 critical 仍保持更高影响门槛 |

`profit_to_loss`、`loss_expanding` 在 warning gates 满足时进入 warning；critical 必须另行满足 critical gates、absolute/relative ROI boundary 和 net-profit boundary，不能仅因 state transition 自动成为 critical。所有边界使用 inclusive `>=`（测试必须覆盖恰好等于 warning 与 critical 的输入）。上述值不是统计显著性结论：当前只有 30 anchors、144 keys，且 fee-v2 fresh estimate 覆盖为 144/144，必须由配置平台作为可修改版本发布并在上线后观察。

本次默认 operational 摘要（aggregate，仅供设计审查）：

- fast policy（每个 window 的 `anchor_count=30`）：1d fast warning summary median/mean/max=`0/0.4000/2`、`30_anchor_total=12`，critical=`0/0.0333/1`、`30_anchor_total=1`；confirmation warning=`0/0.3667/2`、`30_anchor_total=11`，critical=`0/0.0333/1`、`30_anchor_total=1`；recovery `0/13=0%`。3d fast warning=`1/1.1333/4`、`30_anchor_total=34`，critical=`0/0.5667/3`、`30_anchor_total=17`；confirmation warning=`1/1.2333/4`、`30_anchor_total=37`，critical=`0/0.5333/2`、`30_anchor_total=16`；recovery `6/51=11.7647%`。7d fast warning=`1/1.2667/5`、`30_anchor_total=38`，critical=`1/1.1000/4`、`30_anchor_total=33`；confirmation warning=`1/1.0667/5`、`30_anchor_total=32`，critical=`1/1.3000/4`、`30_anchor_total=39`；recovery `16/71=22.5352%`。
- confirmation policy：1d fast warning/critical `30_anchor_total=12/2`，confirmation `30_anchor_total=10/2`，recovery `0/14=0%`；3d fast `33/19`、confirmation `35/18`（均为 `30_anchor_total`），recovery `6/52=11.5385%`；7d fast `35/36`、confirmation `30/41`（均为 `30_anchor_total`），recovery `16/71=22.5352%`。每组均同时输出 per-anchor rows 与 median/mean/max/anchor_count；这些 total 明确命名 `30_anchor_total`，不称 daily volume。
- critical volume 来自独立 critical config 的实际 `severity=critical`；recovery numerator 配对 fast alert 的 confirmation state，即使 confirmation 本身不 alert 也保留在 denominator。
- sample_status（默认 warning config；confirmation status 同时列出）：1d fast `sufficient/sample_insufficient/unavailable=46/4200/7`，confirmation `43/4002/208`；3d fast `192/4052/19`，confirmation `186/3763/314`；7d fast `285/3821/169`，confirmation `269/3499/507`。这些 unavailable/insufficient 均不进入 stable 或 alert；本次 canonical confirmed-unsettled-refund 修复未改变样本可用性计数。
- reversal/recovery 不是“永不恢复”的结论；它受观察期短、连续样本和 missing/unavailable facts 限制，denominator=0 时必须为 null。
- 阈值推荐经本次 canonical confirmed-unsettled-refund 重算后仍不升级：四组值继续保持 **回测暂定** seed/fallback。3d/7d alert volume 与 recovery 已按新语义下降，但样本仍只有 30 anchors 且证据仍为 aggregate evidence；后续发布必须由配置平台和更长观察期决定。

### 2.5 Observed evidence vs assumptions

Observed：上述日期范围、计数、FX 来源、cost coverage、fee-v2 freshness source counts、matrix/sample_status state counts、per-anchor warning/critical summary、persistence 与 paired recovery 数字，以及命令 exit 0。最新 sanitized artifact digest 为 `sha256:396511d04b029531a3fa038f38cf50e9ba4b34d3d7c389b7c753d45eda7b0b8b`；artifact 标记 `immutable=true`、`aggregateOnly=true`、`rowLevelIdentifiers=omitted`，并含 `self_checks.sql_scope=passed`、`self_checks.evaluability_boundaries=passed`、`self_checks.formula_input_contract=passed`；独立 `--verify-artifact` 命令重新计算相同 stable digest input 并通过。

Assumptions/provisional：阈值四组默认、critical 规则、`minAdOrders=0`（广告订单计数的上游覆盖仍需治理）、UTC probe 日期、current cost/FX valuation、物化表方案和通知策略。它们必须在设计/配置中显式标记，不可包装成回测事实。

## 3. 目标架构与数据流

```text
sync facts -> read-only consistent snapshot -> alert domain aggregation
                                      -> fast comparison + shifted confirmation
config.runtime published payload --------------------------┘
                                      -> materialized alert snapshot/history
                                      -> GET API -> alert page + drill-down
```

### 3.1 Canonical ownership

- 盈利事实和 ROI：`analytics.spu_profitability`；告警模块调用 typed seam，禁止复制 `_formula_v10`。
- 告警决策：新建唯一领域包 `tts_erp_v2.analytics.spu_deterioration_alert/`（纯 policy/types + SQL read boundary）；不另建同 stem `.py` 模块或 alternate package。
- 阈值 authority：`config.runtime_config_items` key `analytics.spu_profit_deterioration_alert.v1` 的 published revision；页面显示 source/version/audit。
- fallback：未有 published revision 时使用代码内 **回测暂定** seed，并在 API `config.source=seed_fallback`、`config.provisionalLabel=回测暂定`、页面 banner 和日志中强警告；旧 published revision 读取失败时 fail closed，不静默切 seed。
- 历史：告警物化快照保存 calculation basis、config version、effective values hash、calculated_at 和 source；不能把当前配置重算后冒充历史。

### 3.2 Scheduler/materialization decision

采用每日物化，而不是每次页面请求做全表历史扫描：

1. sync worker 在订单/物流/售后/广告任务之后运行 `analytics.spu_deterioration_alert`，建议 anchor T-2 的店铺当地日完成后执行；
2. 任务在一个只读 `REPEATABLE READ` 快照中读取 facts/config，写入专用 alert snapshot（写入只发生在受控 job，不能由 HTTP 写）；
3. 页面读取最新 snapshot，支持按 shop、windowDays、severity、state、sample status 过滤，并钻取至 canonical SPU profit detail；
4. job 使用现有 `JobSpec` 的 `max_instances=1`、coalesce、catch-up；失败记录 SyncJob/error counter，旧 snapshot 保留但页面显示 stale。

projection-window 已合并到 master（merge commit `75e2370`），其 lane 已由 master commit `6c29f46` 清理，不再拥有或阻塞 scheduler、profitability、SPU 页面/API、测试和本设计路径。实现前，现有 alert lane 必须先合并当前 `origin/master`（确认包含 projection），在 coordination lock 下更新 `docs/handoff/ACTIVE.md` ownership，并重新检查任何较新的 active lane owner；若 `current-net-refund-fix`、`price-statistics` 或其他新 owner 与目标路径重叠，先完成协调再编辑。不得仅因 projection 曾经活跃而创建重复 successor branch。

### 3.3 Snapshot schema（实现时）

建议新增 `analytics.spu_deterioration_alerts`：

- `id` bigint；`shop_pk` bigint；`spu_pk` bigint；`anchor_date` date；`window_days` smallint；`layer` enum/text `fast|confirmation`；`severity` enum/text `none|warning|critical`；
- `state` enum/text；`sample_status` enum/text `sufficient|sample_insufficient|unavailable`；
- prior/current ROI、net profit、spend、orders、ad orders numeric/int（wire Decimal string）；decline fields nullable numeric；
- `effective_config_source`、`effective_config_version`、`config_payload_hash`、`basis_calculated_at`、`created_at`；
- unique `(shop_pk, spu_pk, anchor_date, window_days, layer)`；indexes on latest anchor, shop/severity/state/window。

所有金额/比率保持 numeric/Decimal，null 表示数学无解或样本不足，不能用 0 伪装。

## 4. 配置契约与校验

### 4.1 Runtime config payload

```json
{
  "enabled": true,
  "maturityDays": 7,
  "fast": {
    "1": {"warning": {"roiAbsDelta": "0.20", "roiRelativeDecline": "0.20", "netProfitDecline": "0.25", "minSpendCny": "100", "minOrders": 3, "minAdOrders": 0}, "critical": {"roiAbsDelta": "0.40", "roiRelativeDecline": "0.40", "netProfitDecline": "0.40", "minSpendCny": "300", "minOrders": 5, "minAdOrders": 0}},
    "3": "same shape",
    "7": "same shape"
  },
  "confirmation": {
    "1": "same shape",
    "3": "same shape",
    "7": "same shape"
  }
}
```

示例中的 `same shape` 只为避免把同一 seed 重复抄写；真实 JSON 必须为对象，不能提交字符串。

字段语义：

- `enabled`：是否生成/展示告警；false 时 API 返回 disabled meta，旧 snapshot 只读。seed fallback 的整个 payload 和设置 drawer 均显示 literal `回测暂定`。
- `maturityDays`：确认层平移天数，初始固定 7；v1 只允许 7，不能让用户破坏业务窗口。
- rollout 是 runtime-config sidecar，不是 alert payload 字段；此 global key 的 per-key seam `validate_spu_deterioration_alert_runtime_mutation(...)` 必须拒绝 payload 内的 `rollout`/`draftRollout`，以及非空 sidecar `rollout`/`draftRollout`。generic rollout 仍对其他 runtime keys 可用；本 key 不支持 shop、SPU 或 cohort 定向生效。materialization 与 readonly API 都解析同一份 global published payload/revision，不得创建第二个 owner。
- `fast`/`confirmation`：两个计算层；键 `1|3|7` 是 windowDays。
- `warning`/`critical`：独立严重级别门槛；`roiAbsDelta` 是 ROI 绝对差，`roiRelativeDecline` 与 `netProfitDecline` 为 0..1 比例；`minSpendCny` 非负金额；`minOrders`/`minAdOrders` 非负整数。critical 不从 warning 推导：必须同时满足 critical gates、critical ROI threshold 和 critical business-impact threshold；state transition 先产生 warning，只有数值 critical 条件也成立才升级 critical。
- `source` 不写入 payload，由 resolver/物化结果提供 `runtime_config|seed_fallback`；`version` 是 published revision；`updatedAt/updatedBy` 来自 revision audit。

Validation ranges：`maturityDays=7`；rollout sidecar 对本 key 必须为空（non-empty `rollout`/`draftRollout` rejected）；ROI absolute 0..10；relative/net decline 0..1；spend 0..10,000,000 CNY；orders 0..100,000；critical thresholds must be >= warning thresholds；all fields finite Decimal；payload exactly keys `enabled|maturityDays|fast|confirmation`，unknown keys（包括 rollout）rejected (`additionalProperties=false`)。create、draft save/update、publish、rollback/history republish 均必须调用同一个 named seam；发布前必须检查 global payload 的 `warning`/`critical` 关系和 layer completeness。

### 4.2 Existing platform behavior

使用现有 `/v2/config/runtime/items` 的 create、draft optimistic lock、publish、revisions、rollback、runtime snapshot；不加第二套保存 API。对 key `analytics.spu_profit_deterioration_alert.v1`，create、`PUT .../draft`、`POST .../publish`、`POST .../rollback`/history republish 全部在 generic schema validation 后调用同一个 `validate_spu_deterioration_alert_runtime_mutation(...)`；该 seam 只拒绝本 key 的非空 sidecar rollout/draftRollout 或 payload rollout 字段，不改变其他 key 的 generic rollout 支持。初次 seed 由受控 migration/seed 创建 draft 并由运维发布；生产 migration 不由 agent 执行。该 global key 的 published payload 是 materialization 和 readonly API 的唯一 authority；两者必须使用相同 revision/payload hash，禁止 per-shop rollout 或第二 owner。

页面右上角标题栏“阈值设置”按钮打开 drawer：

- readonly：显示 effective config、source/version、审计时间和“前往运行配置（只读）”；所有 input disabled。
- readwrite/admin：显示 fast/confirmation × 1/3/7 表格；“保存草稿”调用现有 draft endpoint，“发布新版本”调用 publish，409 要求 reload；“重置”恢复当前 published payload，不直接覆盖 runtime authority。
- 另设“载入回测暂定”按钮，必须二次确认，写入 draft 并保留 `回测暂定` 标签；不自动发布。
- drawer 底部固定“取消/关闭”与“保存草稿”；critical 表单错误在对应字段旁显示，不能只用 toast。

## 5. API contract

### 5.1 Main endpoint

`GET /v2/analytics/spu-profit-deterioration`（readonly；浏览器 session 或 Bearer/X-API-Key readonly）。Query：

- `shop_pk`：内部 shop PK，必填；
- `spu_ids`：可选、最多 100，跟随盈利页 exact scope；
- `window_days`：`1|3|7`，可重复；
- `layer`：`fast|confirmation|all`，默认 all；
- `severity`：`none|warning|critical|all`；
- `state`：上述状态枚举，可重复；
- `sample`：`sufficient|sample_insufficient|unavailable|all`；必须与 item 的 `sampleStatus` wire enum 一一对应。
- `anchor_date`：可选当地日期；默认最新已物化 anchor；
- `limit` 1..500、`offset` ≥0；排序只影响 items，不影响 totals。

成功响应（值为占位符，不包含生产标识）：

```json
{
  "items": [{
    "shopPk": "<internal-shop-pk>", "spuPk": "<internal-spu-pk>",
    "windowDays": 3, "layer": "fast", "severity": "warning",
    "state": "roi_deterioration", "sampleStatus": "sufficient",
    "previousRoi": "1.1200", "currentRoi": "0.8400", "roiDecline": "0.2500",
    "previousNetProfitCny": "420.0000", "currentNetProfitCny": "290.0000", "netProfitDecline": "0.3095",
    "previousSpendCny": "500.0000", "currentSpendCny": "510.0000",
    "previousOrderCount": 18, "currentOrderCount": 16,
    "previousAdOrderCount": 12, "currentAdOrderCount": 11,
    "anchorDate": "<local-date>", "basisCalculatedAt": "<utc-iso>",
    "configSource": "runtime_config", "configVersion": 3, "provisionalLabel": null,
    "warningCode": "ROI_AND_NET_PROFIT_DETERIORATED",
    "warningText": "实际 ROI 与净利润连续比较恶化；请查看利润构成与订单/售后证据。",
    "drilldown": {"profitabilityUrl": "/v2/analytics/spu-roi/<spuPk>", "pageUrl": "/v2/pages/spu-roi"}
  }],
  "total": 1,
  "totals": {"warningCount": 1, "criticalCount": 0, "insufficientSampleCount": 0, "shopSpuCount": 1},
  "meta": {
    "anchorDate": "<local-date>", "batchThrough": "<local-date>", "maturityDays": 7,
    "timezonePolicy": "shop_local_iana", "calculatedAt": "<utc-iso>",
    "config": {"key": "analytics.spu_profit_deterioration_alert.v1", "source": "runtime_config", "version": 3, "updatedAt": "<utc-iso>", "updatedBy": "<audit-actor>", "validation": "passed"},
    "coverage": {"materialized": true, "stale": false, "lastSuccessAt": "<utc-iso>", "missingWindowCount": 0},
    "warnings": [], "requestId": "<request-id>",
    "effectiveConfig": {"source": "runtime_config", "version": 3, "updatedAt": "<utc-iso>", "updatedBy": "<audit-actor>", "validation": "passed", "provisionalLabel": null, "thresholds": {"fast": {"1": {"warning": "<effective-thresholds>", "critical": "<effective-thresholds>"}}, "confirmation": {"1": {"warning": "<effective-thresholds>", "critical": "<effective-thresholds>"}}}, "drawer": {"mode": "published_effective_readonly_safe", "canEdit": false, "draftIncluded": false, "secretsIncluded": false}}

  }
}
```

Field/enum semantics：

- `items`：完整业务 scope 过滤后的分页告警行；不得从当前页面可见行求 totals。
- `shopPk/spuPk`：内部主键，不接受 upstream `shop_id`；只用于 API filter/drill-down。
- `windowDays`：1/3/7；`layer`：fast/confirmation；`severity`：none/warning/critical。
- `state`：明确状态枚举；`sampleStatus`：`sufficient|sample_insufficient|unavailable`；缺 facts/ROI/gates 的 item 不能是 `stable`。API sample filter 必须接受并校验 `sufficient|sample_insufficient|unavailable|all`，且逐项匹配 `sampleStatus`。
- ROI/net-profit/spend：后端 Decimal wire string；null 代表无解/不足样本；order counts 为 int。
- `configSource`：runtime_config/seed_fallback；`basisCalculatedAt` 是同一只读 snapshot 的 UTC 时间。`provisionalLabel` 在 seed fallback 时必须是 literal `回测暂定`，published runtime config 为 null 或显式非暂定说明。`effectiveConfig` 是 drawer 唯一 readonly-safe complete projection：完整 published effective values 可供展示，但 `draftIncluded=false`、`rolloutIncluded=false`、`secretsIncluded=false` 是契约字段；`meta.config` 仅为 summary metadata。
- `warningCode`：`ROI_AND_NET_PROFIT_DETERIORATED`、`PROFIT_TO_LOSS`、`LOSS_EXPANDING`、`SAMPLE_INSUFFICIENT`、`DATA_STALE`；未知 code 原样显示并记录 warning。
- `drilldown`：服务端生成的相对 URL；前端不得自行拼业务公式。

### 5.2 Settings/effective endpoint

runtime-config sidecar 的 `rollout`/`draftRollout` 只存在 mutation/history wire contract，不进入 alert payload 或 readonly `effectiveConfig`。readonly 不能读取 `/v2/config/runtime/items` 或 `/v2/config/runtime/snapshot`；告警主响应中的 `meta.effectiveConfig` 是专门的 readonly-safe effective projection，也是 drawer 的数据源。它不暴露 draft、rollout payload、secret reference 或明文 secret。readwrite/admin 的保存仍走现有 runtime config draft/publish endpoints。

页面不要求 readonly 用户访问现有 readwrite-only runtime item endpoints。主 endpoint 的 `meta.effectiveConfig` 是 drawer 唯一数据源，也是唯一 readonly-safe complete projection：它提供 `source`、`version`、`updatedAt`、`updatedBy`、`validation`、完整 effective threshold payload、`provisionalLabel` 和仅包含可展示字段的 `drawer`；只从 published revision 生成，绝不包含 draft、rollout sidecar、secret reference 或解密 secret。`meta.config` 若保留，仅是 key/source/version/audit/validation summary metadata，绝不作为 drawer payload。readwrite/admin 仍可从 drawer 链接至运行配置页面进行 draft/publish；readonly drawer 全部 disabled。

错误：401 未认证；403 缺 readonly/page 权限；422 非法 enum/date/filter；409 draft version conflict 由 runtime API 返回；503 物化快照不可用/配置不可解析。2xx 只表示解析和读取完成，不用 HTTP 200 伪装错误。

## 6. Page UX 与 accessible warning

### 6.1 Route/navigation

- route：`GET /v2/pages/spu-profit-deterioration`，权限点 `page:spu-profit-deterioration`，最低 readonly。
- sidebar：Analytics 分组中“利润劣化告警”，紧邻“SPU 实际 ROI”；active 状态沿用共享 sidebar。
- 页面头：左为标题/anchor freshness；右为“阈值设置”按钮（见 §4.2）。
- filters：店铺 select（左上）、SPU scope（其右，精确 scope）、窗口 tabs `1d/3d/7d`、层级 `fast/确认`、severity/state/sample dropdown、anchor date、刷新按钮。sample dropdown 的选项固定为 `sufficient`、`sample_insufficient`、`unavailable`、`all`，与 API validation 和 `sampleStatus` 完全一致。筛选改变 URL/query，不能改变 server totals 语义。

### 6.2 States/interactions

- loading：表格骨架 + `aria-live=polite` “正在加载告警”；旧结果不能冒充新 anchor。
- empty：明确“当前 scope 没有达到阈值的告警”，同时显示 `checkedCount`、anchor、config source；不是白屏。
- sample-insufficient：单独灰黄状态“样本不足，未触发告警”，显示缺少 spend/orders/ad-orders 哪一项；不显示红告警。
- error：可读错误、request id、重试；403 显示权限；503 显示 stale/快照时间。
- row click：展开 summary card，显示两个窗口原值、阈值、状态转移、effective config source/version；点击“查看利润详情”进入现有 ROI drilldown。不得在 JS 重新计算百分比。API/browser named tests 必须覆盖 sample filter 的 `unavailable` 选项、URL/query round-trip、匹配 `sampleStatus` 的结果以及 sample-insufficient/unavailable 空态；不得把 unavailable 静默归入 all 之外的其他状态。
- refresh：保留 filters，重新获取同一 endpoint；当新 snapshot 到达时提示“已更新”。
- settings save/reset：遵循 runtime optimistic lock；保存草稿后显示 draft version；发布后刷新 effective values 和 audit metadata。

### 6.2.1 实现注记（已交付的接线细节）

以上 §6.1/§6.2 的要求在 `static/js/spu-profit-deterioration.js` 中的具体落地口径：

- **SPU scope**：`#filter-spu-ids` 粘贴式精确范围，接受逗号串或重复 `spu_ids`，只接受**内部 `spu_pk` 正整数**（与
  §5 的 `spu_ids` wire 契约一致，不接受上游 `spu_id`），上限 100 与服务端一致。非法或超限输入**不静默放宽**为全 SPU：
  保持原 scope、把原因写进 `#filter-spu-feedback`、且不发请求。URL 回写用逗号串（与盈利页同一习惯），发 API 时展开为
  重复参数 `spu_ids=`。服务端 `totals` 仍按完整 scope 计算，因此 SPU scope 只收窄 `items`。
- **state 下拉**：枚举只维护一处（模板 `<option>`），JS 从 DOM 读合法值，不在 JS 硬编码第二份清单。页面一次只发一个
  `state=`；端点参数本身可重复（多值 OR），由 API 层测试固定。
- **row click summary card**：点整行或行内「明细」按钮展开 `#alert-summary`，列出上期/本期 ROI、净利润、消耗、
  订单数、广告订单数、降幅、`state`、`sampleStatus`、`anchorDate`、`basisCalculatedAt`、`configSource`/`configVersion`
  与 `warningCode`/`warningText`。全部照抄服务端 Decimal wire string，null 仍显示「—」；JS 不算任何百分比。
- **新鲜度提示**：`#alert-freshness` 只读 `meta.calculatedAt`、首行 `basisCalculatedAt` 与 `meta.anchorDate`；
  快照三元组变化即视为新快照，文案变为「快照已更新：…」，否则显示「快照新鲜度：…」。刷新期间不重置上一份快照 key。
- **drill CTA**：主 CTA「查看利润详情」指向服务端 `drilldown.pageUrl`（`/v2/pages/spu-roi` + `shop_pk`/`spu_pk`）；
  原始 JSON（`drilldown.profitabilityUrl`）降为次要链接。服务端未下发 `pageUrl` 时主 CTA 退回 JSON 端点，不给死链。
- **配置字段口径**：`enabled` / `maturityDays` 一律读 `meta.effectiveConfig` 的**顶层**字段，不从
  `thresholds` 取（`thresholds` 是完整 published payload，两者同源；见 `docs/api/external-api.md` 的消费方约定）。
- **抽屉渲染竞态**：`settings-drawer` 的 `data-rendered` 标志**只**由阈值表格渲染成功时置位。首个载荷到达前就打开抽屉
  不会把抽屉永久标成已渲染，配置投影到达后会补上表格（`tests/browser` 有专门用例固定这个时序）。

### 6.3 Strong warning visuals

每个 warning row/card/banner 同时包含：

1. 文本徽章：`严重告警`/`告警`/`样本不足`；
2. 非颜色图标（critical 使用八角/警示符号，warning 使用感叹号，sample 使用信息符号）和 `aria-label`；
3. row left border + patterned background/带纹理的 banner（颜色只作辅助）；
4. 警告 banner 位于 filters 下、结果上方，显示 count、anchor、config source；
5. 键盘 focus、tooltip 文本和明确 drill-down CTA。

高对比模式下 icon/text/border 仍可区分；不能用红绿唯一表示状态。未知枚举显示 raw code，不能静默空白。

## 7. Deployment、migration、rollback、observability

### 7.1 Deployment

1. 实现前由现有 alert lane 先合并当前 `origin/master`（其中已包含 projection-window 的 `75e2370` merge），在 coordination lock 下更新 `docs/handoff/ACTIVE.md` ownership，并重新检查任何较新的 active lane owner；若 `current-net-refund-fix`、`price-statistics` 或其他新 owner 重叠，先协调再编辑。不得仅因 projection 曾经活跃而创建重复 successor branch；保持 migration 与代码向后兼容。
2. migration 只在 test-shaped DB 用 `bash scripts/test_isolated.sh --refresh-template ...` 验证；生产 migration 由人工 release window 执行。
3. seed 插入 config item/schema/draft，确认后通过 runtime config 页面发布；所有 seed 值显示“回测暂定”。
4. 发布 API/page/static 后重启 API；新增/修改 `jobs/` 或 `sync_worker/` 后执行 `systemctl --user restart tts-erp-sync.service`。本设计阶段不执行生产 restart。
5. 检查 `/endpoints`、API health、sync job last success、page permission 和 config version。

#### 7.1.1 页面与阈值设置抽屉上线清单（人工执行）

实现层（`GET /v2/pages/spu-profit-deterioration`、`static/js|css/spu-profit-deterioration.*`、阈值设置抽屉）已完成，但不包含任何生产动作。上线时以下步骤全部由**人**执行，agent 不代为执行：

1. **权限点种子（不新增 alembic revision）**：新增页面只需在 `tts_erp_v2/accounts/pages.py` 的 `PAGES` 加一行；
   `page:spu-profit-deterioration` 自动进入 `ALL_PERMISSION_CODES`。部署后在目标库跑既有幂等命令
   `python -m tts_erp_v2.accounts.cli sync-permissions`（即 `accounts.service.seed_builtin_roles`），
   它会补 `security.permissions` 行并给内置角色授权：`admin`/`operator` 自动包含新页面，`viewer` 是显式白名单，
   如需只读岗也能看告警页，由运维在用户管理页或 `edit-role` 显式授予。0053 之后不再新增 revision，避免迁移 DAG 出现新 head。
2. **配置发布**：key `analytics.spu_profit_deterioration_alert.v1` 的首次发布必须由**人**在运行配置页面/端点执行。
   在发布之前，API 与页面一直显示 `source=seed_fallback` 和字面量“回测暂定”。agent 不执行生产 publish。
3. **重启 sync worker（必需）**：告警快照由 sync worker 的 `analytics.spu_deterioration_alert` job 写入，
   任何 `tts_erp_v2/jobs/` 或 `tts_erp_v2/sync_worker/` 的变更后必须
   `systemctl --user restart tts-erp-sync.service`；不重启就没有新的 anchor，页面只能看到旧快照，
   且 `read_alerts()` 会以 stale `503` 拒绝，而不是返回伪装成空结果的 `200`。API/静态资源变更后另行重启 API（`bash restart.sh`）。
4. **破坏性重算开关**：`ALLOW_PROD_SPU_DETERIORATION_ALERT_REPLACE` 是人工设置的生产环境变量，
   用于显式批准生产形态库上的替换写。它只能由运维在明确批准后设置；页面、API、job 与 agent 都不得自行设置或绕过。
5. **验收顺序**：`/endpoints` 出现新路由 → 页面 `200` + 侧边栏“利润劣化告警”入口（经营分析组）→
   权限点行为（无权限会话 `403`、readonly 抽屉全 disabled）→ `meta.effectiveConfig` 的版本/审计/`payloadHash` →
   sync job 最近成功时间与 anchor freshness。

### 7.2 Rollback

- 运行时先将 `enabled=false` 发布（保留旧 snapshot，页面显示 disabled），或 rollback 到上一 revision；不删 audit history。
- API/page 可独立回退；旧客户端未知字段必须忽略，HTTP route 关闭时返回明确 404/503 而非空 200。
- job 失败不覆盖最近成功 snapshot；修复后 catch-up 单次重建缺失 anchor。
- migration 回滚由人工 DBA 按 release plan 处理；不删除已有 audit/snapshot 数据作为自动 rollback。

### 7.3 Observability

结构化日志字段：`request_id`、`job_name`、`anchor_date`、`window_days`、`layer`、`config_source/version`、`rows_read`、`alerts_warning`、`alerts_critical`、`insufficient_sample`、`duration_ms`；不得记录 shop/SPU 标识、token 或原始订单。

指标：materialization success/failure、snapshot age、query duration、fact coverage、config fallback count、invalid-config count、alert volume per layer/severity/window、persistence/recovery denominators、API 4xx/5xx、stale response count。页面仅展示 aggregate values。

## 8. Exact implementation file map and lane dependency

本阶段不编辑下列文件；它们是未来 successor lane 的目标：

- `tts_erp_v2/analytics/spu_deterioration_alert/`（新，唯一纯 types/policy + read/materialize domain package；不得另建同 stem `.py` 或 alternate package）
- `tts_erp_v2/api/v2/spu_deterioration_alert.py`、`api/v2/pages.py`、`access/_policy.py`、`app.py`（实现前须按同步规则检查最新 active owner；`docs/api/external-api.md` 必须同步 wire contract）
- `tts_erp_v2/db/models/analytics.py` / `db/models/__init__.py`、`alembic/versions/<new_spu_deterioration_alert_snapshot>.py`（schema/migration）
- `tts_erp_v2/runtime_config/validation.py`、`runtime_config/repository.py`、`api/v2/config.py`、`tests/api/test_runtime_config.py`（global key mutation seam and create/draft/publish/rollback/history-republish rejection tests）
- `tts_erp_v2/sync_worker/scheduler.py`、`tts_erp_v2/jobs/spu_deterioration_alert.py`（projection 已在 master；实现前须按同步规则检查最新 active owner）
- `templates/pages/spu-profit-deterioration.html`、`static/js/spu-profit-deterioration.js`、`static/css/spu-profit-deterioration.css`、sidebar page registry（实现前须按同步规则检查最新 active owner；重叠 owner 必须先协调）
- `tests/analytics/test_spu_deterioration_alert.py`、`tests/api/test_spu_deterioration_alert_api.py`、`tests/browser/test_spu_deterioration_alert.py`、`tests/e2e/test_spu_deterioration_alert_smoke.py`、`tests/sync_worker/test_spu_deterioration_alert_job.py`（后继）

当前 lane 实际拥有且已修改：`docs/design/spu-profit-deterioration-alert.md`、`docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md`、`scripts/probe_spu_profit_deterioration_thresholds.py`。

## 9. Non-goals

- 不实现产品页或阈值 API。
- 不改变 canonical profitability 公式、projection 窗口或订单/售后事实口径。
- 不向 TikTok、Miaoshou 或任何上游发送写请求。
- 不自动发送邮件/短信/IM。
- 不在浏览器计算 ROI、净利润、阈值或状态。
- 不将告警阈值硬编码为不可覆盖常量；seed 只能 fallback 且必须显眼标注。
- 不执行生产 migration/restart/config publish；不暴露生产店铺/SPU/订单标识。
