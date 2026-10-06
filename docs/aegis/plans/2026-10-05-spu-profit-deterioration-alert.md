# SPU 利润劣化告警实施计划

> 本计划对应 `feature/spu-profit-deterioration-alert` 的设计/回测交付；projection-window 已合并到 master（merge commit `75e2370`），其 lane 已由 master commit `6c29f46` 清理。实现前，现有 alert lane 必须先合并当前 `origin/master`，在 coordination lock 下更新 `docs/handoff/ACTIVE.md` ownership，并重新检查较新的 active lane owner；不得仅因 projection 曾经活跃而创建重复 successor branch。本文件不是实现提交，也不授权生产 migration、发布配置或 restart。

## 1. Execution readiness

- TaskStartSnapshot：branch `feature/spu-profit-deterioration-alert`；initial HEAD `ce3e9e665e1eeaa2c667b786bf541f27db4c5375`；clean task worktree；tower-do revision `188`。
- Requirement Ready Check：业务窗口、canonical roi_real、样本 gate、prior≤0 状态、configuration authority、可访问 warning visual、API/page/settings/deployment/test 要求已锁定在 design 文档。
- Dependency：`spu-projection-window` 已合并到 master（merge commit `75e2370`），其 lane 已由 master commit `6c29f46` 清理，不再 active 或持有这些路径。实现前，现有 alert lane 先合并当前 `origin/master`，在 coordination lock 下更新 `docs/handoff/ACTIVE.md` ownership，并重新检查较新的 active lane owner；若 `current-net-refund-fix`、`price-statistics` 或其他新 owner 重叠，先协调再编辑。
- Change Necessity：需要每日历史快照和可审计阈值来源，不能以前端条件或一次性 SQL 替代。
- Reuse decision：复用 `_formula_v10.calculate`、runtime config platform、existing JobSpec、snapshot/read-only DB boundary、existing API/page/browser test patterns；不加依赖。
- Current-master baseline：当前 canonical `FormulaInput` requires `confirmed_unsettled_refund_vnd`; successor unit coverage must prove completed `REFUND_ONLY`/`RETURN_AND_REFUND` refund amounts enter this field only when settlement is absent, while cancellation refund amounts remain separate.
- Architecture integrity：domain → API/page adapter → browser；runtime config 是 authority；materialization job 是唯一写告警快照边界。
- Complexity budget：一个 domain module、一个 snapshot table/migration、一个 system job、一个 API/page、一个 settings drawer、四类测试；禁止跨层重复公式。
- Explicit non-goals：见 design §9；不实现通知、机器学习、生产运维或上游写入。

## 2. RED / GREEN / REFACTOR TDD route

TDD route is strict because this feature adds public API, configuration authority, alert behavior and producer/consumer contracts.

### Phase A — RED: contracts first

1. 新建纯 policy tests，覆盖：
   - 1d/3d/7d exact non-overlap windows and confirmation +7-day shift;
   - Decimal aggregation then ROI recomputation, never average daily ROI;
   - previous ROI ≤ 0 produces `profit_to_loss`, `loss_expanding`, `loss_to_profit`, not percent division;
   - explicit evaluability before state classification: required fact rows + defined ROI; zero-spend/null ROI and missing facts become `sample_insufficient`/`unavailable` even when thresholds/gates are zero, never `stable`;
   - one normative evaluator with separate warning/critical configs; exact inclusive warning/critical boundary inputs, critical gates and critical volume distinct from warning volume; state transitions never auto-upgrade critical;
   - null versus zero semantics;
   - executable probe self-checks for SQL scope, zero-threshold zero-spend/null ROI, and missing-fact evaluability before state classification;
   - exact paired recovery: every fast-alert `(anchor, shop×SPU)` pairs its confirmation decision regardless of confirmation alert; recovered states numerator, fast-alert denominator, null on zero denominator;
   - true per-anchor warning/critical volumes with median/mean/max/anchor_count and explicit `30_anchor_total`, never `daily volume`.
2. 新建 runtime config schema tests: exact payload keys exclude rollout, range validation, critical≥warning, maturityDays=7；named `validate_spu_deterioration_alert_runtime_mutation(...)` rejects payload rollout/draftRollout and every non-empty sidecar rollout/draftRollout. Exercise that seam through create, draft save/update, publish, rollback/history republish; generic rollout remains supported for other keys. Readonly-safe effective projection only contains published global thresholds and asserts no draft/rollout/secret fields, with literal `回测暂定` on seed fallback.
3. New API tests in `tests/api/test_spu_deterioration_alert_api.py`: auth (401/403/readonly), filters/enums including `sample=sufficient|sample_insufficient|unavailable|all` validation and result `sampleStatus` matching, paging totals, nulls, stale/disabled/config fallback, requestId, drill-down links, no identifiers in logs, and equality of API/materialization global published payload revision/hash with no per-shop rollout. Assert `meta.effectiveConfig` is the single readonly-safe complete drawer projection; `meta.config`, if retained, is summary metadata only and never drawer data.
4. New materialization/scheduler tests: read-only input seam, idempotent unique anchor, no partial replacement, failed job preserves last snapshot, `JobSpec` cadence/catch-up；proper unit test in `tests/analytics/test_spu_deterioration_alert.py` (`test_daily_sql_expensive_ctes_are_bounded_and_scoped`) asserts `bounded_keys` precedes `scoped_order_lines`, both bounded-key and scoped-order-line CTEs require `(so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')`, and every sales/settlement/refund/full-loss fact derives only from the scoped relation before aggregation. Add `test_confirmed_unsettled_refund_vnd_matches_formula_input_contract` to prove completed `REFUND_ONLY`/`RETURN_AND_REFUND` case-line refund amounts populate `confirmed_unsettled_refund_vnd` only without settlement, cancellation amounts do not populate it, and the value reaches the canonical formula. Probe executable guards mirror both regressions.
5. Browser/E2E RED: page route/sidebar, settings drawer location, keyboard/focus/aria, loading/empty/error/sample-insufficient/unavailable, row/card/banner warning and drill-down; explicitly assert sample filter options `sufficient`, `sample_insufficient`, `unavailable`, `all`, URL/query round-trip, and matching `sampleStatus` rendering.

### Phase B — GREEN: smallest implementation

1. Add typed domain policy and pure functions. Import/reuse `_formula_v10.calculate`; do not copy formula code.
2. Add read boundary using the maintained `consistent_read_snapshot` seam and shop-local IANA date attribution. Assert repeatable-read/read-only settings. Aggregate selected campaigns and orders at shop×SPU before formula; apply canonical fee-v2 and the maintained date cutoff `calculated_on >= calculated_at.date() - 7 days`, recording shop-estimate/stale-fallback/baseline source counts. Keep `scoped_order_lines` after deterministic `bounded_keys` and scope refund/full-loss CTEs before aggregation. Populate canonical `confirmed_unsettled_refund_vnd` from completed `REFUND_ONLY`/`RETURN_AND_REFUND` case-line refund amounts only where settlement is absent; keep cancellation refund amounts separate and pass the field through `DailyFact` and window aggregation.
3. Add migration/model for alert snapshot. Test only with isolated test-shaped database; never execute production migration.
4. Seed the global runtime config schema and **回测暂定** draft/fallback without a rollout payload field; route every create/draft-save/publish/rollback-history-republish mutation through `validate_spu_deterioration_alert_runtime_mutation(...)`. Reject non-empty sidecar rollout/draftRollout for this key only; materialization and readonly API resolve the same globally published payload/revision, with no per-shop rollout or second owner.
5. Add materialization system job after data sync. Ensure max_instances=1, coalesce=true, bounded query, stale/error counters, no HTTP write path. Probe SQL must bound deterministic keys before materializing facts and retain an immutable aggregate-only SHA-256 evidence artifact. Materialization must persist sample_status/evaluability, not classify missing/null ROI as stable.
6. Add GET API adapter and permission rule. Return typed enums, Decimal strings, coverage/warnings, and make `meta.effectiveConfig` the single readonly-safe complete projection used by the drawer; keep `meta.config` summary-only if retained. Derive both only from published non-secret global values; readonly users must not need existing readwrite runtime endpoints.
7. Add page route/navigation/profile, filters, accessible warning components, settings drawer that uses existing runtime API and optimistic locking. Frontend renders server values only.

### Phase C — REFACTOR: contract hardening

1. Refactor query/fact and policy seams so SQL is not reachable from API or browser code.
2. Enforce one source of truth for threshold payload and one spelling for enums/field names; preserve `sample_insufficient`/`unavailable` wire states and per-anchor volume/recovery semantics.
3. Add structured logs/metrics and redact shop/SPU/order identifiers.
4. Verify fallback semantics: absent config visibly says `seed_fallback`/“回测暂定”; stale published config fails closed rather than silently selecting seed.
5. Run formatter/linter/type checks relevant to changed files, then full fast suite.
6. Update `docs/api/external-api.md`, design/operational docs, and migration/restart runbook: document `meta.effectiveConfig` as the single complete readonly drawer projection and `meta.config` as summary-only; review diff for scope and secrets.

## 3. Exact implementation file map (successor lane)

| Step | File(s) | Result |
| --- | --- | --- |
| domain | `tts_erp_v2/analytics/spu_deterioration_alert/` | typed facts, `AlertConfig`, states, pure comparison/gate/severity/persistence policy |
| query | `tts_erp_v2/analytics/spu_deterioration_alert/_implementation.py` | canonical profitability seam, consistent snapshot, local timezone, aggregate facts |
| schema | `tts_erp_v2/db/models/analytics.py`, `tts_erp_v2/db/models/__init__.py`, `alembic/versions/<id>_spu_deterioration_alert.py` | snapshot/audit rows and indexes |
| config | `tts_erp_v2/runtime_config/validation.py`, `tts_erp_v2/runtime_config/repository.py`, `tts_erp_v2/api/v2/config.py`, seed migration/controlled seed script, `tests/api/test_runtime_config.py` | named per-key mutation seam; payload excludes rollout; create/draft/publish/rollback/history-republish reject non-empty sidecar rollout only for this key; generic rollout remains for other keys |
| job | `tts_erp_v2/jobs/spu_deterioration_alert.py`, `tts_erp_v2/sync_worker/scheduler.py` | daily materialization and failure/catch-up behavior; implementation must recheck current active owners after syncing master |
| API | `tts_erp_v2/api/v2/spu_deterioration_alert.py`, `tts_erp_v2/api/v2/pages.py`, `tts_erp_v2/access/_policy.py`, `app.py` | readonly endpoint, page route, page permission; `meta.effectiveConfig` is the only complete drawer projection and `meta.config` is summary-only |
| UI | `templates/pages/spu-profit-deterioration.html`, `static/js/spu-profit-deterioration.js`, `static/css/spu-profit-deterioration.css`, page/sidebar registry | filters, cards/table, settings drawer, non-color warning |
| tests | `tests/analytics/test_spu_deterioration_alert.py`, `tests/api/test_spu_deterioration_alert_api.py`, `tests/sync_worker/test_spu_deterioration_alert_job.py`, `tests/browser/test_spu_deterioration_alert.py`, `tests/e2e/test_spu_deterioration_alert_smoke.py`, `tests/api/test_runtime_config.py` | domain/API/scheduler/browser/E2E plus all four runtime mutation-path rejection tests and effectiveConfig drawer contract |
| docs | `docs/api/external-api.md`, `docs/design/spu-profit-deterioration-alert.md`, release/runbook docs | stable wire/deployment/rollback contract; external API documents effectiveConfig as sole complete drawer projection and config as summary-only |

Before implementation, merge current `origin/master`, update `docs/handoff/ACTIVE.md` ownership under the coordination lock, and recheck newer active owners. If `current-net-refund-fix`, `price-statistics`, or another newly active owner overlaps any file above, coordinate before editing; do not create a duplicate successor branch merely because projection-window used to be active.

## 4. API and configuration acceptance gates

- `GET /v2/analytics/spu-profit-deterioration` is readonly and returns `{items,total,totals,meta}`; totals are complete scope, not current page.
- Query supports `shop_pk`, exact `spu_ids`, `window_days=1|3|7`, `layer`, `severity`, `state`, `sample=sufficient|sample_insufficient|unavailable|all`, `anchor_date`, `limit`, `offset` with strict 422 validation.
- Every item exposes prior/current ROI/net profit, decline/null semantics, spend/order gates, explicit `sampleStatus=sufficient|sample_insufficient|unavailable` wire semantics, state, severity, anchor, basis calculatedAt, config source/version and drilldown; missing/null ROI is never stable.
- Runtime config is resolved from one globally published revision; draft is never runtime; rollout remains a sidecar and the named per-key mutation seam rejects any non-empty rollout/draftRollout for this key while preserving generic rollout for other keys; source/version/update/audit are explicit; seed/fallback has strong warning and literal `回测暂定` in payload, API, page drawer, and tests.
- Readwrite/admin settings workflow uses existing draft/publish/rollback optimistic lock. Reset restores published draft; loading seed requires explicit confirmation.
- API/page errors never return HTTP 200 with fake empty alert data.

## 5. Browser/page acceptance gates

- route and sidebar entry are permission-protected; active navigation is visible.
- header-right “阈值设置” opens a drawer; store/SPU/window/layer/severity/state/sample/anchor filters are keyboard accessible and reflected in URL.
- loading, empty, sample insufficient, stale, disabled, 401/403/422/503 states have readable text and `aria-live` updates.
- warning includes Chinese status text, icon/badge, patterned row/card/banner and drill-down link; color is supplementary only.
- settings fields show server effective value, source, version, updatedAt/actor; readonly drawer uses `meta.effectiveConfig` only and asserts no draft/secret exposure; seed values show literal `回测暂定`; inline validation and 409 reload are visible.
- frontend has no ROI/net-profit/threshold formulas or hardcoded effective thresholds.

## 6. Deployment and rollback checklist

1. Before implementation, merge current `origin/master` into the existing alert lane; verify projection-window merge `75e2370` is present, update `docs/handoff/ACTIVE.md` ownership under the coordination lock, and recheck newer active owners before touching overlapping files.
2. Validate migration only with `bash scripts/test_isolated.sh --refresh-template fast` or equivalent test-shaped ephemeral DB. Never run production `alembic upgrade`.
3. Seed config draft, review schema/ranges, publish via authorized runtime config operator; verify source/version and audit.
4. Deploy API/page/static. If `jobs/` or `sync_worker/` changed, human/operator runs `systemctl --user restart tts-erp-sync.service`; API restart follows normal release procedure.
5. Verify `/endpoints`, health, route permission, config snapshot, job last success, snapshot age, and aggregate warning counters.
6. Rollback by disabling config or runtime rollback to prior revision; preserve snapshots/audit. Keep serving last successful snapshot with stale banner or disable route explicitly.

## 7. Risks and mitigations

- **Ad coverage gap**: expose `coverage.adDailyMissingDays` and sample warning; never infer spend from ad GMV.
- **Current valuation drift**: expose FX/cost as-of and calculatedAt; alert is operational signal, not historical accounting.
- **Timezone mismatch**: use shop-local IANA dates in domain; unknown region fails closed for windowed read.
- **Config drift/concurrent edit**: versioned runtime config and 409 optimistic lock; no localStorage authority.
- **Alert fatigue**: gate orders/spend, two-layer confirmation, persistence metrics and severity separation; thresholds remain configurable.
- **Historical incompleteness**: materialization stores config version/basis and stale state; do not retroactively rewrite old rows without explicit rebackfill job.
- **Probe safety**: production shape uses shared `is_prod_shaped_db()` behavior; unset/malformed URL fails closed, test-shaped URL is rejected, and `tts_erp`, `tts_erp_prod`, `tts_erp_prod_*` require explicit read-only confirmation.
- **Lane conflict**: projection-window is already merged and cleaned; after syncing master, coordinate any newly active owner such as `current-net-refund-fix` or `price-statistics` before overlapping edits, with one writer per path.

## 8. Required commands and evidence

Narrow checks for the current design lane:

```bash
.venv/bin/python -m py_compile scripts/probe_spu_profit_deterioration_thresholds.py
.venv/bin/python scripts/probe_spu_profit_deterioration_thresholds.py --help
.venv/bin/python - <<'PY'
from scripts.probe_spu_profit_deterioration_thresholds import self_check_formula_input_contract, self_check_policy, self_check_sql_scope
self_check_policy(); self_check_sql_scope(); self_check_formula_input_contract(); print('scope-evaluability-formula-contract-smoke: passed')
PY
.venv/bin/python - <<'PY'
from datetime import date
from decimal import Decimal
from scripts.probe_spu_profit_deterioration_thresholds import AlertConfig, WindowMetric, evaluate_alert
m=lambda roi,profit: WindowMetric(1,2,date(2026,1,1),date(2026,1,1),Decimal(roi),Decimal(profit),Decimal('100'),5,0)
w=AlertConfig(Decimal('.2'),Decimal('.2'),Decimal('.25'),Decimal('50'),2,0); c=AlertConfig(Decimal('.4'),Decimal('.4'),Decimal('.4'),Decimal('50'),2,0)
assert evaluate_alert(m('1','100'),m('.8','75'),w,c).severity == 'warning'
assert evaluate_alert(m('1','100'),m('.6','60'),w,c).severity == 'critical'
assert evaluate_alert(m('-0.2','-20'),m('-0.4','-30'),w,c).roi_decline is None
print('alert-boundary-smoke: passed')
PY
rm -f /tmp/spu-profit-deterioration-backtest.json
set -a; . /home/schan/tts-erp/.env; set +a
 timeout 180 .venv/bin/python scripts/probe_spu_profit_deterioration_thresholds.py --confirm-read-only-production --lookback-days 30 --max-spus 300 --statement-timeout-ms 90000 --output /tmp/spu-profit-deterioration-backtest.json
 .venv/bin/python scripts/probe_spu_profit_deterioration_thresholds.py --verify-artifact /tmp/spu-profit-deterioration-backtest.json
 python3 -m json.tool /tmp/spu-profit-deterioration-backtest.json >/dev/null  # aggregate artifact syntax/digest envelope
 git diff --check
```

Implementation lane commands (must use isolated runner; exact selections):

```bash
bash scripts/test_isolated.sh unit tests/analytics/test_spu_deterioration_alert.py
bash scripts/test_isolated.sh api tests/api/test_spu_deterioration_alert_api.py
# Runtime-config acceptance: create/draft save-update/publish/rollback/history-republish mutation cases
bash scripts/test_isolated.sh api tests/api/test_runtime_config.py
bash scripts/test_isolated.sh sync_worker tests/sync_worker/test_spu_deterioration_alert_job.py
bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert.py
bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py
bash scripts/test_isolated.sh fast
```

If the repository test selector does not expose `browser` or `e2e` domains, invoke the named tests through the documented `scripts/test_isolated.sh` domain wrapper and record the exact command/result; never invoke pytest directly. Browser/E2E may require the documented service setup and must remain bounded by timeout.

Definition of done for successor: RED tests exist and fail before implementation; GREEN tests pass; REFACTOR checks pass; exact warning/critical boundary tests and readonly projection redaction tests pass; fast suite has zero new stable failures; `git diff --check` passes; no production identifiers/secrets/staged foreign files; docs/API/operational contracts are updated; predecessor dependency and synchronized master revision are recorded.
