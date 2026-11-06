# 开发测试反馈提速

## 范围与依据

用户批准先缩小日常测试范围、按需构建前端、记录分阶段耗时，再验证浏览器
进程复用。2026-11-03 保存的完整 fast JUnit 报告为 3375 项、1459.948 秒，
其中 browser 781.282 秒；这是历史测量，不作为本次候选提交的验证结果。

TaskStartSnapshot：`origin/master` / `110a2814f57a7395874db22e44c648f77e4cdcae`，
工作树开始时干净，分支 `codex/test-feedback-speed`。

Decision: code-change。保守回退与无条件构建由脚本拥有，单改文档不能消除开销。
选择器保留未知代码、共享安全/数据库边界的扩大覆盖；只收窄已知消费者。
ArchitectureReviewRequired: yes（测试选择、构建和生命周期三个现有 owner 的边界审查，
不增加执行器、数据库入口或依赖）。TDD Mode: off；采用行为回归验证，不强制 RED/GREEN。

## 实施步骤

1. **选择与构建**：复用 `_test_selection.py` 的映射，登记页面浏览器消费者、
   媒体 fixture/helper 消费者；普通测试文件修改只选择该文件，文档按文档检查。
   共享应用壳、全局样式扩大浏览器覆盖。原始窄测在 pytest 完成收集后按标记和
   fixture 依赖构建一次；完整 fast 仍覆盖全部安全测试。
2. **计时**：隔离 wrapper 输出连接准备、等锁、模板准备、克隆、执行、清理、
   总耗时；pytest 使用公开 hooks 记录收集、构建、setup/call/teardown，输出
   最慢用例。已有 selected report 继续拥有成败与完整性判断，不增加状态通道。
3. **浏览器复用**：优先验证模块内复用、每用例独立 context。模块 teardown
   关闭全部浏览器资源后才进入其他模块；混合异步测试是必需反例。保留缺失
   Playwright/Chromium 的既有 skip 语义和只读请求断言。
4. **验证与交付**：相同测试机上记录旧/新窄测；运行选择器、真实收集、构建触发、
   生命周期回归。提交候选，按 AGENTS.md 运行一次完整 fast 基线；失败只复验
   最小集合。提交并推送分支，按锁串行验证 prospective merge、推送 master。

## 边界与验收

- 所有执行仍从 `scripts/test_isolated.sh` 进入，临时库、proof、安全校验、清理
  失败非零退出保持；不修改生产、迁移、鉴权或业务计算。
- 普通页面选择保留相关桌面/移动端参数；登录、权限、真实读回和全量 fast
  的测试集合不能因优化消失。未知修改继续保守扩大覆盖。
- 验证构建只在实际依赖前端产物时发生，缺失依赖必须失败，不得静默跳过。
- 对选择重复/删除、collection failure、构建失败、浏览器状态泄漏和异步冲突
  有可失败的回归。记录实际耗时，不承诺未经测量的提速比例。
- 暂不增加并行、不共享数据库或 node_modules，不改变候选/合并验证要求。

## 复用依据

使用已有 pytest 与 Playwright，无新增依赖。依据 pytest 的公开 collection / report
hooks 和 `--durations`，以及 Playwright 的 BrowserContext 隔离：

- https://docs.pytest.org/en/stable/reference/reference.html
- https://playwright.dev/python/docs/browser-contexts

函数级浏览器启动在模块内复用验证通过后移除；用例级 context、只读约束和模块
退出时释放事件循环保留。若混合执行不能证明安全，则保留现有生命周期并记录限制。

## 初步测量（同一测试机、各一次）

测试机为 `schan@192.168.47.129`，独立验证 checkout，私有 node_modules；
旧版本为上述同步 master，新版本为本分支实现。均从隔离 wrapper 进入、克隆
并清理临时库。没有重新测量整套 master，也没有变更其零失败基线。

| 命令（均以 `bash scripts/test_isolated.sh` 开头） | 旧 wall time | 新 wall time |
| --- | ---: | ---: |
| `fast tests/scripts/test_test_selection.py` | 25.05s（18 项） | 4.65s（23 项） |
| `browser tests/browser/test_frontend_ad_daily_filters.py tests/browser/test_frontend_dashboard_layout.py` | 40.81s（12 项） | 29.11s（同 12 项） |

日志位于测试机 `/tmp/tts-test-speed-{before,after}-{unit,browser}.log`，对应
`.time` 文件记录 `/usr/bin/time` wall time。这是各一次的观察，不是多轮统计
或整套 fast 提速承诺；构建和系统缓存可能影响绝对值。当前窄回归还验证了
构建失败/空收集/收集错误不执行测试、真实 collector 组完整性与混合异步生命周期。
完整候选与合并验证待后续证据补齐。

## 生命周期验证中发现的边界

- 构建锁必须覆盖 pytest 的整个消费会话，通过 `Config.add_cleanup` 释放；
  仅收集、空收集、收集失败不构建。测试验证会话内锁不可再次获取、退出后可获取。
- pytest 的 conftest 导入和 `tests.browser.conftest` 显式导入可能是不同模块对象。
  浏览器状态集中在 `tests/support/browser_runtime.py`，两类调用共享同一生命周期。
  除真实混合异步测试外，回归覆盖显式导入 helper 与 fixture 取得同一个 browser。
- 已中断的早期候选只用于诊断，不是全套通过证据；全套完成证据以最终提交的
  baseline driver 结果为准。失败集仍按已提交的零失败基线比较，未修改基线。
