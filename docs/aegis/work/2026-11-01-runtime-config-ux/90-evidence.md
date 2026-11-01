# 最终验证证据

日期：2026-11-01（Asia/Shanghai）。本次交付为技术方案和源码开发，生产服务未部署或重启。

## 结果

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| `bash scripts/test_isolated.sh fast tests/api/test_runtime_config_review.py tests/api/test_runtime_config.py` | exit 0，61 项通过 | 只读检查、原子发布、旧请求兼容、版本/引用/历史护栏、非有限数字和失败无写入 |
| `FRONTEND_TEST_FILTER=src/pages/runtime-configs/index.test.tsx bash scripts/test_isolated.sh frontend tests/frontend/test_components.py` | exit 0 | 真实组件生命周期、输入/取消、分步操作、冲突和未知结果、独立表单、错误映射及筛选隔离 |
| `bash scripts/test_isolated.sh browser tests/browser/test_runtime_config_workflow.py` | exit 0，6 场景通过 | root、/tts × 390/1440/2560；当前临时库绑定、原生浏览器操作、独立 SQL 回读、焦点/无溢出、精确大整数 |
| `bash scripts/test_baseline.sh` | exit 0，suite exit 0；既有失败 0，本次失败 0，新增稳定失败 0 | 最终源代码完整 fast 选择执行一次，包含组件与浏览器回归；未更新主干 baseline 文件 |
| TypeScript / Vite | 通过 | Windows Node 22.23.2 和远端 Linux Node 22.23.3；打包有既有大编辑器 chunk 提示，无编译错误 |
| npm 安装审计 | 0 已知漏洞 | 精确锁定 `diff@9.0.0`，BSD-3-Clause，无新增运行时传递依赖 |
| Ruff / Markdown / 差异检查 | 通过 | 新 Python 文件及受影响文件；原路由既有 B008 单独排除检查，新端点用 Annotated；文档本地链接、围栏与 `git diff --check` |
| 独立代码复核 | 无剩余实质问题 | 后端、前端分别复核规格与质量，修复后再次集成复核 |

## 环境与可复查记录

- 用户指定 Linux 机器上的独立验证工作树：`/home/schan/tts-erp/.worktrees/codex-runtime-config-ux-20261101`，从计划提交 `d5a2e0a862625e21af47da09d88bd89295a6ed24` 创建，仅叠加本任务文件。主干依据为 `8b082808a025c4250852c683ae2ebc54c11df47b`。
- 使用该机器既有测试环境（Python 3.13，项目声明支持 >=3.13）和维护的测试模板。每次应用验证均由唯一 runner 创建、使用并清理独占 `tts_erp_test_*` clone，没有共享/生产数据库替代。
- 18 个应用/测试文件的本地与远端 SHA256 清单完全一致；最终 baseline 运行期间未再修改应用源代码。
- 远端日志：`/tmp/TEST_runtime_api.log`、`/tmp/TEST_runtime_frontend_checked.log`、`/tmp/TEST_runtime_browser.log`、`/tmp/TEST_runtime_baseline.log`。对应 `.exit` 文件均为 0；后者明确输出 `PASS — zero new stable failures`。
- 浏览器截图位于验证工作树的 `logs/TEST_runtime_config/screenshots/`，最终三种宽度的检查页已下载并人工查看。桌面、手机与超宽屏分别确认长名称换行、变更文字/符号、旧新内容布局、发布按钮及全页无横向溢出。
- 原 Linux 主工作树的插件和物流/调度相关未提交工作保留；未调用生产迁移、业务同步、API 或 worker 重启。临时测试引导补丁已撤回，没有进入本分支。

## 边界与续接

发布成功表示新 revision 已提交，后续业务读取时使用；不承诺运行中的任务自动刷新。未知写入结果仍需要手动核对，没有新增持久化回执系统。凭证原 PUT 的 upsert 兼容语义保留。

技术方案、现行 API/运维文档同步更新。后端辅助工作树已通过 Codex 归档保存可恢复快照；主要实现分支保留供合入。Git 提交 SHA、推送和工作树清洁状态以实际 Git 收据及最终回复为准。
