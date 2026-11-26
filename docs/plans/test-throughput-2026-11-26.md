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

## 风险与退役

阶段划分可能遗漏带 build marker 的 API 用例，报告必须检查覆盖；并发测试自身
必须使用私有测试锁，不能与外层资源锁自锁。官方组件 JSON 在超时可能不存在，
不得推断未报告测试已通过。原完整混合进程路径从正式 baseline 退役；原始隔离
定向入口保留。相同内容缓存验证证据不作为本次捷径，以最终合并提交一次验收
消除重复；不可变构建缓存后置，先使用现有缓存。
