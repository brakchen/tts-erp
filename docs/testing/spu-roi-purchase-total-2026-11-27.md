# SPU ROI 采购成本汇总验证（2026-11-27）

实现、口径文档和新增检查已完成并推送到 `codex/spu-roi-purchase-total`。
正式基线存在 10 项原主线可复现的失败。2026-11-27，用户明确授权“不是你的失败的话，合并就行”，
接受下列已在原主线复现、不是本次引入的 10 项失败作为本次合并例外。
没有修改已提交 baseline，也没有将正式门禁的原始失败结果改记为通过；本授权不包含生产发布。

## 改动与口径

- 总览直接返回完整经营范围的 `cogs_total` 与 `net_revenue`，复用既有 Decimal 汇总；不新增 SQL 或改变利润公式。
- 全部已确认全损包含符合条件的海外取消全损；全损货本属于采购成本，每件只计一次。页面改为“其中：全损成本”，默认成本出现时显示估算提示。
- 成本来源、历史窗口按当前价估值、40 CNY/件缺价估算规则保留。业务公式及示例见 [业务口径 §5.4](../business/spu-profitability.md#54-当前成本利润与-roi)，字段及提示契约见 [技术方案](../design/spu-profitability-technical-design.md)。

## 已通过

- 修改的三个 Python 模块 Ruff 检查、生产树 undefined-name 检查、`git diff --check`。
- 技术文档 10 个 JSON 示例可解析且没有重复键；三个修改的契约文档本地文件链接检查通过。
- 定向隔离检查：全部全损示例、跨 SPU 汇总、分页、空范围及 Summary 组件检查通过。
- 示例使用 20 CNY/件、100 件付款销售，其中 7 件全损；额外 3 件海外取消全损。API 返回全部全损 10 件、采购成本 `2060.0000`、其中全损成本 `200.0000`，净利润对账只减总采购成本一次；未付款及普通国内取消不计货本。
- 正式门禁中的前端阶段通过，覆盖 dashboard 与 spu-profitability 的四个组件测试文件及源码契约。
- 正式页面阶段 40 项中，34 项通过；新增标签断言最初精确匹配了带“？”按钮的整个术语，6 项定位失败。只修正测试为定位成本卡的 `dt` 标签后，375/1440/2560 三种宽度 × SPU ROI/重点关注两个页面的 6 项全部通过；金额断言保持不变。
- 已查看当前构建的 375 和 1440 宽截图，采购成本及其中全损成本显示正确。

## 正式门禁与失败对照

- 同步的主线：`b4078b554906736464614bd6b24e24dcab5f8e66`。
- 正式受测 prospective merge：`cd52c659a2662906306c6ae8e20a071bae54085a`，第一父提交为上述主线。
- 修正测试定位后的候选提交：`3ed69ee337307e28ed27dd0ac9f444b2c0030eab`；其应用代码与正式受测版本相同，后续提交只记录本验证文档。

正式门禁只执行一轮：

```bash
bash scripts/test_baseline.sh --scope auto \
  --base b4078b554906736464614bd6b24e24dcab5f8e66 \
  --scenario page-assets \
  --keep-log /tmp/tts-erp-purchase-total-baseline.log
```

后端收集 3,540 项，报告执行 3,526 项，14 项为既有声明跳过；10 项失败。
前端阶段通过；页面阶段的 6 项新增定位失败已按上述最小范围修正和复测，没有重跑整套。

随后在候选提交上仅执行下列 10 个后端失败节点及上述 6 个页面节点：10 项失败、6 项通过。
再从干净的原主线提交只执行同样 10 个后端节点：全部复现，失败集合一致。

```text
tests/api/test_commerce.py::test_list_products_spu_includes_cost_and_image_fields
tests/api/test_llm_context.py::test_llm_context_markdown_contains_required_business_rules
tests/api/test_spu_roi_api.py::test_spu_roi_manual_cost_source
tests/api/test_spu_roi_api.py::test_spu_roi_math_single_spu_default_k1
tests/api/test_spu_roi_api.py::test_spu_roi_uses_verified_miaoshou_purchase_price
tests/scripts/test_frontend_remaining_catalogue_queries_live.py::test_one_canonical_write_is_read_back_with_exact_decimals[root]
tests/scripts/test_frontend_remaining_catalogue_queries_live.py::test_one_canonical_write_is_read_back_with_exact_decimals[tts]
tests/sync_worker/test_reporting_cov.py::test_run_cost_snapshots_uses_purchase_order_lookup
tests/sync_worker/test_scheduler_miaoshou_reporting.py::test_run_cost_snapshots_writes_seeded_spu
tests/sync_worker/test_spu_deterioration_alert_job.py::test_real_canonical_queries_batch_more_than_500_scope
```

这些断言仍期待旧 `MANUAL` / `MANUAL_ENTRY` 来源、旧说明文字、目录成交价不进入统一采购价，
或成本 SQL 的旧 `pks` 参数观测方式。原主线已采用统一 `PURCHASE_PRICE` 和 `spu_pks` 查询参数。
此次没有修改成本解析、目录、成本快照、LLM context、SQL 或公式调用路径来消除这些既有失败。

## 环境与保留证据

- 既有验证机 `tts-erp-test`；检出目录 `/home/schan/tts-erp-validation/spu-roi-purchase-total`。每轮均通过 `scripts/test_isolated.sh` 创建并清理独立临时数据库。
- 完整门禁材料：该目录 `.git/test-runs/run.iWc0Jzn6/`，包括各阶段结果、日志和组件报告。
- 候选失败项及页面复测：`/tmp/tts-erp-purchase-total-rerun.log`。
- 原主线失败项对照：`/tmp/tts-erp-purchase-total-master-compare.log`。
- 截图：验证检出的 `frontend/.cache/screenshots/TEST_spu_react_{spu-roi,focused-spus}_{375,1440,2560}.png`；均为合成测试数据。
- 最新主线未推进时，复用这一轮完整门禁与失败对照证据，在合并锁内对最终 prospective merge 执行业务、组件和六个页面的定向检查；不重复完整套件。仅下列已证明为原主线问题的 10 项失败适用用户授权，其他新增失败仍阻止合并。
- 验证检出及原始报告保留用于追溯。没有生产数据变更、迁移或服务重启。
