# 浏览器测试与重复验证提速

用户批准继续优化并完成此前剩余项。TaskStartSnapshot：干净的
`origin/master` / `5884b9863a2e4cde68c24f61f85b6f1c8744eaea`，
分支 `codex/test-throughput`。Decision: code-change；TDD Mode: off。

## 范围与实施顺序

1. 替换公共 Renderer 的 networkidle：由页面主体、数据和字体的明确状态决定
   就绪；保留所有原有桌面、移动、路径前缀、鉴权、真实读回和布局断言。
2. `_test_frontend.py` 继续唯一拥有测试构建。工作树私有缓存按实际源码、锁文件、
   工具配置、Node/依赖安装身份和构建环境校验；命中还须核对完整产物。生成时
   独占锁，消费时共享锁；失败、源变化或产物损坏不能发布有效缓存。
3. 将 Chromium 启动/导航/页面等待/清理计时接入现有 pytest 计时出口；扩展模块
   浏览器复用到适合的完整链路夹具，保持每例新 context、登录和数据库证据。
4. 为显式审核过的只读浏览器文件提供最多两路并行入口，每路仍递归调用隔离
   wrapper，独立临时库、proof、端口和输出。使用现有子进程生命周期工具；
   全量 fast 保持完整串行覆盖，重组件测试和可写工作流不混入并行池。
5. 前端执行前检查 package.json 的 Node engine；排查已观察到的组件超时，减少
   重复 UI 操作但保留业务参数覆盖，不能以加大超时或删断言换取通过。
6. 审核浏览器/组件/API的重复覆盖；仅在底层已有同等断言时消除重复浏览器工作，
   关键实际浏览器边界保留。无可证明冗余则记录结论，不人为减少用例。

## 验收与交付

- 测试只通过 `bash scripts/test_isolated.sh`；无生产、迁移或部署操作。
- 增加缓存失效、损坏、失败、读写锁以及并行失败/取消/集合边界的行为回归。
- 同 VM、Node 22.22.2、同 12 项测试，分别记录冷构建、热缓存及两路并行耗时。
  目标热运行不高于 20 秒；它是待测目标，不是性能承诺。上轮同条件为 30.24 秒。
- 候选提交及最终 prospective merge 按仓库要求执行完整 fast；失败仅最小复验。
  不重写主线基线。提交并推送分支，持 merge lock 验证并集成、清理自有资源。

## 复用与边界审查

复用 pytest 公开 hooks、Playwright locator/expect 和现有 owned_process。官方
Playwright 明确不推荐 networkidle 作为测试就绪条件：
https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state 。
已检索 pytest-xdist 的 loadfile/loadgroup：它在 worker 内收集并默认继承同一
数据库配置，不能直接满足本仓每次隔离 wrapper 的 proof 边界。本轮只做对现有
wrapper 的有限两路编排，不实现新的 pytest 调度器或数据库生命周期。

ArchitectureReviewRequired: yes。构建/cache/lease 由同一 owner 管理；报告仍由
selected_test_report 管理；浏览器共享进程而不共享可变 context；隔离数据库仍
由原 wrapper 和连接 authority 管理。并行安全清单为显式审核清单，未知文件拒绝。
