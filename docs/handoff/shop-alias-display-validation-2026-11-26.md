# 店铺别名展示：隔离验证记录（2026-11-26）

本记录是测试证据快照，不是实时 lane 登记。ERP 页面和 Zabbix 主机显示名优先采用非空 `shop_alias`，否则回退平台店铺名。

## 版本与环境

- 主线比较基准：`caae9dc3ffe95d64cf036fbc425520ea12bf3914`。
- 完整选测候选：`f6f3e38b5f000885b988c1ee6f7b0ffebdd4fdbd`。
- 后续只调整测试输入步骤；应用代码与上述候选一致。
- 测试通过 SSH 别名 `tts-erp-test` 执行，使用独立验证 checkout；未修改该服务器主 checkout 的既有备份脚本改动。
- Python 3.14.4；工作进程使用独立安装并校验下载摘要的 Node 22.23.2。
- 所有测试均通过 `scripts/test_isolated.sh` 或其上层 `scripts/test_baseline.sh`，从 `tts_erp_test_template` 建立独立 `tts_erp_test_*` 克隆，并由入口自动清理。

## 已执行验证

1. `bash scripts/test_baseline.sh --scope auto --base caae9dc3ffe95d64cf036fbc425520ea12bf3914 --scenario page-assets --keep-log /tmp/tts-shop-alias-baseline-pytest.log`
   - 实际执行 3534 条 pytest 用例；选测报告列出 4 个失败节点，未通过门禁。
   - 两条 `test_frontend_remaining_manual_native.py::test_manual_native_write_preview_and_readback[root/tts]`：全局可见店铺范围安全检查未通过。
   - `test_spu_price_columns.py::test_drill_same_row_close_then_reopen_works`：打开、关闭、再次打开均完成，但测得三次点击跨度超过用例的 300ms 时限。
   - `tests/frontend/test_components.py::test_frontend_components`：组件全集超过既有 360 秒进程时限，部分既有用例还出现 5 秒超时。
   - 报告另列 `tests/e2e/test_spu_deterioration_alert_smoke.py` 为 missing：该文件虽进入页面选测清单，当前报告插件仍按 `requires_frontend_build` 和命名 scenario 白名单将其排除。未修改选测或报告插件以绕过这一检查。
2. 隔离入口最小复验上述两条采购页面用例：两条均通过；同轮快速展开用例仍因 300ms 时限失败。
3. `bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py`：5 条真实 HTTP/浏览器 E2E 全部通过，补齐上述 missing 文件的独立执行证据。
4. 通过隔离入口和现有 `TTS_ERP_TEST_VITEST_SELECTION` 选择七个受影响组件文件，并以 `--testNamePattern 'alias|shop label|shop decoding'` 最小复验：20 条组件用例全部通过。文件覆盖 dashboard、shops、sync-jobs、spu-profitability（含重点 SPU）、spu-profit-deterioration、manual-costs 和共享店铺解码。
5. 广告名称/筛选、费率依据、同步任务店铺字段、OAuth HTML 和 Zabbix relay 的新增后端用例已实际执行。最初新增 OAuth 断言错误使用响应顶层 `shops`，修正为既有契约 `result.shops` 后独立复验通过。
6. 前端 typecheck/build、Python 未定义名称检查和 `git diff --check` 已通过。

## 主线对照

在另一个干净 checkout 固定到上述主线基准，使用同一隔离入口和运行时分别执行：

- `src/pages/spu-profitability/index.test.tsx` 的 `both page profiles render through the same React owner and keep the server scope`：未含别名展示改动时也失败，等待期间页面仍显示“正在读取完整关注清单”。
- `tests/browser/test_spu_price_columns.py::test_drill_same_row_close_then_reopen_works`：未含本次改动时也失败，测得约 409ms，超过 300ms 断言。

未降低这些验收标准、延长超时、更新主线失败基线，或把未完整执行的组件全集标记为通过。

## 合并状态

当前不能宣称完整门禁通过，尚未合并 master。分支包含可审阅的实现和测试修正；需先修复剩余主线测试/选测问题，或取得用户对已记录失败的明确合并豁免。未执行生产部署、生产迁移或服务重启。

完整输出保留于测试主机 `/tmp/tts-shop-alias-*.log`；这些日志不作为 Git 内容提交。
