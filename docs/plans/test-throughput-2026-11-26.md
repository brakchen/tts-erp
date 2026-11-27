# 固定容量测试机上的回归提速

## 目标与授权

2026-11-26 用户批准全部六项改进，服务器不能扩容。基于
`caae9dc3ffe95d64cf036fbc425520ea12bf3914` 开始；此前三条回归在同一
8 CPU / 7.2 GiB 验证虚拟机执行，出现持续换页。页面原型落入未知文件兜底，
调度器改动选全后端，组件的一个 pytest 包装项隐藏整套 Vitest。

## 架构、技术栈与边界

Decision: code-change。文档或增加超时无法修正选测、跨进程并发和报告粒度。
沿用 Python 标准库、Bash/flock、pytest 公共 hook、Vitest 官方 JSON reporter；
不增加运行依赖。选择规则仍由 `_test_selection.py` 拥有，数据库身份与清理由
`test_isolated.sh` 拥有，结果完整性由 selected report 拥有。
新增阶段调度只组合这些入口，不运行数据库操作或绕过隔离证明。

TDD Route: mode=off, decision=skipped；用户未要求 RED/GREEN，仍必须增加有意义的
选择、排队、取消、报告缺失和阶段完整性回归，并按测试基础设施规则执行 fast。

保留生产安全、未知业务代码保守回退、真实浏览器边界、每轮临时数据库、
失败/跳过/缺项不能视为通过。修改的兼容边界是测试工具的报告附加字段、可选参数
及正式合并验收流程，不修改 ERP 业务 API、schema 或插件。

## 实施任务与所有权

1. 选测：`scripts/_test_selection.py`、选择器/原型校验测试；原型使用独立校验，
   scheduler、monitor/OAuth 和 shop-name 使用核实过的提供方/消费方映射。
2. 容量：`scripts/test_isolated.sh` 和锁 helper；跨 checkout 共用主机容量锁，
   等待时输出状态，取消后释放；仍各自拥有 clone。资源锁与模板锁分离。
3. 组件报告：`tests/frontend/test_components.py`、组件报告 helper/测试；输出具体
   Vitest 文件、用例和最小复验命令，超时/报告缺失保持 incomplete。
4. 阶段：`scripts/_test_phases.py`、`_run_selected_tests.py`、selected/runtime hooks；
   backend/frontend/browser 顺序使用独立进程和 clone，汇总覆盖，进度每 20 秒可见。
5. 环境与日志：`prepare-worktree.sh`、baseline driver；按计划安装前端，验证依赖
   指纹，统一 Node 发现；日志保存在工作树 Git 目录，失败后仍可检查。
6. 合并：AGENTS、测试/工作流指南；分支做必要窄测，唯一正式验收针对锁内的
   prospective merge。master 变化后重建候选；禁止用旧 SHA 的证据放行新内容。

三个实现子任务使用独立工作树。父会话负责主机锁、baseline、环境、文档与所有
Git 提交/推送/合并操作，整体验证串行。无其他会话文件所有权转移。

## 验证与交付

先通过 `bash scripts/test_isolated.sh fast <受影响的基础设施测试文件>` 验证。
提交候选、同步 master，再在合并锁内的干净集成工作树运行
`bash scripts/test_baseline.sh --scope fast --base <first-parent>` 一次。
新失败只复验最小范围；稳定失败修复后补受影响证据，不吸收到 master baseline。
保留阶段、排队、测试数量、慢项和资源数据；对三组原始 diff 比较选测范围。
检查分阶段无重复、无缺项、退出码一致、取消回收、临时数据库与证明清理。
推送任务分支及已验证 master，记录真实结果与剩余限制。

### 定向验证记录

- 首轮 130 项基础设施回归通过：pytest 47.427 秒，隔离入口总计 57.479 秒。
- 模板与终态完整性补充 35 项通过：pytest 39.341 秒；两个真实临时 clone 的
  schema/migration 指纹一致。报告一致性补充回归另做窄测。
- 将原三条分支相对同一 master 的 diff 交给新选择器：均不再选全后端；
  SPU ROI 只选盈利组件，采购价改动选 11 个后端目标。店铺别名仍保留完整前端，
  因为该分支确实修改共享脚本。
- 真实组件回归与旧 runner 重叠，360 秒未完成，不能作为独占性能基准。
  最慢五文件占已完成文件时长约 60%。保留所有断言和 50/101 项数据边界，
  缩小 DOM 查询范围、合并重复编辑弹窗；仅准备字符串的输入使用现有
  user-event 的 `click` + `paste`，保留键盘/逐字纠错用例。
  上游用法：<https://testing-library.com/docs/user-event/clipboard/>；沿用仓库
  锁定的 `@testing-library/user-event` 14.6.8（MIT），不新增依赖。
- 旧 checkout 和已启动进程不会自动遵守新容量锁；更新 runner 后新启动的任务
  才进入统一队列。正式完整验收须在现有 master 合并锁内串行完成。
- CPU profile 进一步定位 50 行筛选与 101 标签边界：超时截断前 React 开发模式
  渲染已占 5.408/5.766 秒，角色查询仅 0.282/0.177 秒，机器时钟源已为 TSC。
  两个压力测试声明使用 10 秒局部预算，保留规模/断言和全局 5 秒、组件 360 秒
  上限；不为测试改生产组件、替换时钟或关闭 React 开发检查。获得独占容量后
  Vitest 使用 3 个隔离 worker（实测单 worker 约 0.5 GiB），其余入口仍为 2。
- SPU 的请求范围和 101 标签分页用例隔离无关的 Summary 展示边界（独立
  `Summary.test.tsx` 保持真实覆盖；页面布局/店铺别名用例仍渲染真实 Summary）。
  真实 Selection、101 标签、分页和全部请求断言保留。两项定向复验通过，
  分别为 1.090/7.869 秒；50 行筛选的三个原稳定超时用例也通过。

## 风险与退役

阶段划分可能遗漏带 build marker 的 API 用例，报告必须检查覆盖；并发测试自身
必须使用私有测试锁，不能与外层资源锁自锁。官方组件 JSON 在超时可能不存在，
不得推断未报告测试已通过。原完整混合进程路径从正式 baseline 退役；原始隔离
定向入口保留。相同内容缓存验证证据不作为本次捷径，以最终合并提交一次验收
消除重复；不可变构建缓存后置，先使用现有缓存。

## 最终基础设施验收（2026-11-27）

六项改造已随 `e603c19bdf04209a0bb1cff24a3f9669570106d4` 推送 master，
第一父提交为 `7ab5a93e9ab5a126e89e141d4c701b17d433332e`。正式 fast 验证
针对该精确合并提交执行，未使用跨提交验收例外，也未修改稳定失败 baseline。

- 完整回归 2943.061 秒（49 分 03 秒），3900 个 pytest 执行条目，缺项为零。
- 后端 3508 项零失败；前端阶段 25 项，其中组件包装运行 54 文件、800 用例，
  组件耗时 272.778 秒，完整报告且未触发原有 360 秒上限；浏览器执行 367 项。
- 原始全量退出码为 1：focused-spus 布局组件，以及媒体上传用例进入素材页前的
  导航各失败一次。按测试指南 §6，在同一合并提交上各做一次最小复测，均通过；
  分类为 likely flake，保留失败报告和复测收据，以零新增稳定失败完成验收。
- 修复的店铺筛选、快速切店、三次弹窗开关、P2 错误反馈均在整轮回归中通过。
  后续单独用 async `act` 等待布局测试的即时 mock 请求及状态更新完成，保留
  `findByText`、真实 Summary 和全部布局断言，不提高全局测试超时。
- 资源日志排除首行开机平均后共 587 个采样区间，87 个存在 swap-out，峰值
  88982 KiB/s；平均 I/O wait 为 1.64%。期间观察到主机 AIDE 后台扫描。
  新锁只能协调采用该入口的测试，不能保证其他系统工作负载不引发换页。
  未停止或更改安全扫描，没有同 SHA、同负载对照，不宣称全量提速倍数。

原始证据保存在验证工作树的 Git 私有目录：`test-runs/run.TSyR8sbP/` 保存
三阶段、组件及两项最小复测，`test-runs/integration-e603c19b-final/` 保存
控制器、资源采样与 acceptance 收据；本地主仓 `.git/test-runs/throughput-six-analysis/`
保留副本。此前失败候选的日志保留为历史诊断，不替代此次精确提交验收。

日常收益主要来自减少不相关测试、取消分支与合并的重复全量，以及让新会话共用
容量队列。原三条差异均不再选全后端；更新过的 checkout 才遵守新锁，已启动
的旧 runner 不会自动加入队列。
