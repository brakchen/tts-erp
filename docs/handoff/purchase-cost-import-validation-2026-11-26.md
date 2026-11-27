# 采购价文件导入验证记录（2026-11-26）

实现已提交并推送；验收门禁尚未通过，分支未进入 ready，未合并或发布。
本文件是本次验证的证据记录，不是实时 lane 登记表。

- 分支：`codex/purchase-cost-import`。
- 同步的 master：`caae9dc3ffe95d64cf036fbc425520ea12bf3914`。
- 受测应用代码：`5cadc08a4be14359cdb912e0618f2e7efd98d6fc`。
- 本记录之后的提交仅增加此文档，不改变受测应用代码或测试。
- 文件、均价、店铺匹配、事务和接口约定见 [External API](../api/external-api.md#import-manual-purchase-costs-from-an-order-package-export)。

## 已通过

- 前端 TypeScript 类型检查和 Vite 构建。
- 新增 Python 文件的 Ruff 检查、受改动报表模块的既有规则检查、生产树 undefined-name 检查。
- 通过唯一隔离入口执行的 47 项定向检查，覆盖新增导入、原有人工成本接口、历史保留、原子回滚、身份消歧、上传组件，以及 375/1440/2560 三种宽度、根路径和 `/tts` 两种前缀的页面检查。上传组件内部的五个 Vitest 用例通过。
- 三种宽度的当前编译产物截图已人工查看。
- 旧浏览器实写用例单独复测时，根路径和 `/tts` 两项均通过。

定向命令（前端选择由已有隔离测试包装器消费）：

```bash
FRONTEND_TEST_FILTER=src/pages/manual-costs/CostImport.test.tsx \
  bash scripts/test_isolated.sh fast \
  tests/api/test_manual_cost_import.py \
  tests/api/test_manual_costs.py \
  tests/api/test_manual_costs_single_tx.py \
  tests/api/test_frontend_remaining_manual_identity.py \
  tests/frontend/test_components.py \
  tests/browser/test_manual_cost_import_layout.py --tb=short -x

bash scripts/test_isolated.sh fast \
  tests/browser/test_frontend_remaining_manual_native.py --tb=short
```

## 未通过的基线及对照

```bash
bash scripts/test_baseline.sh --scope auto \
  --base caae9dc3ffe95d64cf036fbc425520ea12bf3914 \
  --scenario page-assets
```

选择报告记录执行 3,421 项，失败 ID 为：

- `tests/browser/test_frontend_remaining_manual_native.py::test_manual_native_write_preview_and_readback[root]`
- `tests/browser/test_frontend_remaining_manual_native.py::test_manual_native_write_preview_and_readback[tts]`
- `tests/frontend/test_components.py::test_frontend_components`

前两项因看到了不属于该用例夹具的店铺，在实际浏览器操作前拒绝继续；单独隔离复测均通过，表现为整套运行的数据残留。

前端整套组件用例触及 360 秒总时限，独立复测仍超时。采购价三个前端测试文件分组复测为 57 通过、10 失败，新增上传组件五项通过；失败涉及旧用例的 5 秒单例时限。随后从干净的上述 master 检出目录运行原有采购价两个测试文件，对照结果为 52 通过、10 个同类时限失败。两次具体超时用例并非完全相同，不能据此宣称本分支已达到“零新增稳定失败”。

因此没有放宽断言、修改测试时限或更新 master 的已提交 baseline 来取得通过结果。后续需解决现有前端测试时限/环境条件后，先复核对应失败项；若 master 推进，按仓库流程重新同步和验证。

## 验证环境与保留材料

- 测试虚拟机：`192.168.47.129`，本机 SSH 别名 `tts-erp-test`，使用既有 `schan` 密钥登录；没有保存密码。
- 候选检出：`/home/schan/tts-erp-validation/purchase-cost-import`。
- 干净 master 对照检出：`/home/schan/tts-erp-validation/merge-purchase-cost-import-5cadc08a`；未创建 prospective merge。
- Node 22.23.2：运行前将 `/home/schan/.local/tts-erp-test-node/node_modules/.bin` 加到 `PATH`。
- 测试模板通过 `test_isolated.sh --refresh-template` 刷新到 `0077_shop_alias`；每轮使用独占临时 clone，结束后删除该 clone。没有使用生产数据库运行测试。
- 曾修复该虚拟机 PostgreSQL Docker 网桥缺少 IPv4 网关的问题，未改动生产服务或源业务事实。
- 该机另有店铺别名验证任务并行运行，未更改其检出目录、WIP 或进程。
- 完整候选日志：`/tmp/tts-erp-purchase-cost-import-final-baseline.log`。
- 干净 master 对照日志：`/tmp/tts-erp-purchase-master-frontend-compare.log`。

没有调用生产写入接口导入样例文件。本次没有新增 migration；未来人工发布需要同步 `pyproject.toml` 中新增的 `openpyxl` / `defusedxml` 依赖（部署脚本的 `--sync-deps`）。
