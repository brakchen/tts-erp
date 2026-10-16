# 通用插件状态与单目标恢复 — 当前技术断点

- Task ID: 2026-10-15-plugin-status-implementation
- Current todo: `status-runtime-callers`，仍 `in_progress`；不是 T4 或全功能完成。
- Active slice: `wake-rebuild-producer`源码已局部接受，native终态/七字段/两path/准确gate已核，待规划下一个producer。
- Blocked on: recover/failed/resume等其余producers/mandatory union、预算/实际业务写点fence、action、Cookie、fresh spec→quality、ERP/T5 尚未闭合。
- Next step: 原MiniMax按原生终态续接；下一段仍新有效RED/预审/最小源码/独立postGREEN，不能借本段gate扩范围。

## 技术方案与不可变约束

唯一协议：`docs/plugins/plugin-status-report.md`（批准基线 `fb42e16575a241fec88740d69137ae6a8153c2bb`）。

1. 首期只支持单 Seller/Advertiser/Cookie Store；只扫描已有允许 origin 页面，不自动建页、登录或扩多店。
2. ERP status domain 统一拥有实例、审计事实、投影、受限决策与 outbox；插件 controller 统一拥有绑定生命周期。
3. 每个操作保留首 await 前 generation、首 canonical read 的 scalar PRE tuple 和同一 budget。父操作负证据不能在子失效操作中借新 context。
4. 合法 self-revocation：先同步消费父 predicate，再自身 generation++ 并固定 mutation generation；不能在自增后重测自然失效的父 predicate而拒绝全部正案。
5. 既有 typed commit/storage 是状态与报告的唯一原子写出口；`applied`、`accepted`、执行/cleanup 许可分离。拒绝/未知不返回 proposal、不继续 clear。
6. expired/unknown 不等于 absence，`expect:null` 只表示真实 null；不重开预算、不增加 authority/cache/registry/persisted proof。
7. 已持久化事实先于 business clear，cleanup 后续失权不回滚事实。physical bind/clear 自身 awaited mutation fence 仍是后续预算/T5工作，外层检查不能宣称解决。
8. 严格 TDD，ArchitectureReviewRequired=yes；测试必须实际 entered、立即观察主 Promise、finally 释放并 await 所有主工作。setup/timeout/recorder 契约失败不能包装成已发生 stale mutation。

## 已局部接受的实现（七段）

| 微切片 | 已验证责任 | 最终 OWNER gate |
| --- | --- | --- |
| runtime-result | reportObservation 原样返回 typed result/fence，拒绝不 dispatch、正常链真 dispatch | `b32a5224-0cff-4490-a843-40c98c1a5968` |
| snapshot-producer | 原 generation、首次 scalar PRE tuple，不借初始化后的授权 | `4bc563c0-e586-4e60-b84a-5bef4f58b69d` |
| bootstrap-typed | commit 消费真实 applied/refused；空 bootstrap 原 fence；queue-full application 与 acceptance 分离 | `8709ea55-9c3a-44a5-8657-b806c5522916` |
| safe-stop-producers | HOLD/requireUser 单次同步撤权、立即 read/stop、原 tuple 拒绝迟到写 | `330af378-658c-4b2c-a0c4-1c71a98c45b1` |
| passive-observation | 撤直接预写；原 generation/PRE/budget 交给 canonical commit；expired fail closed | `ed21c35a-f31d-4975-9e47-7c8d62adaf5e` |
| invalidate-canonical-fact | 原 parent tuple/budget、合法自身撤权、实际 fact/report-before-clear、raw read/stop 拒绝观察 | `64f2b158-82fd-42e2-87ca-7c60b33cd06e` |
| wake-rebuild-producer | 原bootstrap tuple/预算、父撤权先消费、own generation、discovering新episode事实先clear | `55f416c9-a968-47c1-a3cb-5d1bddcc1730` |

这些 gate 只接受各自微切片；不接受全部 callers、完整预算或发布。旧十五项仍需保留。

## 第六段已局部通过：失效事实

- Plugin cwd：`/home/schan/pi-workspace/monitor-status-01a1406d`，branch `feature/plugin-status-auto-bind`。
- 最终冻结 controller：`bf2f3c5c3ee3fd4b40337b80ea5cd0180ddbd1ff447a7580406ee5142405818f`。
- 最终冻结 tests：`317f975d7415c843ff61f8607a5ceb2b25757104a11896e27b6f0228bdfcc40d`。
- 正常 public invalidation 和两条 live-parent 路径确实持久化事实/报告后才 clear；ensure 保留原 budget 第二参并传 fence 第三参，observe 也明确传同 budget 第二参和原 fence 第三参。
- OWNER 对该候选亲跑新七+旧十五共 **22 PASS**，实际 unpiped `tsc --noEmit` **exit0**；全 include 含 entrypoints/WXT config。
- 同最终 tests，仅换旧 controller：三业务 RED、两 recorder 契约 RED、两 inherited-negative 行为 coverage PASS。不能称七业务 RED。
- 五个私有单点变体均被真实结果断言拦住：去 own-generation、stop 后借 tuple、分别删除两 parent origin、拒绝全部 inherited origin。38 src 中各仅 controller 不同，tests 逐字同。
- 迟到 raw-read 拒绝缺口已局部修正：同样三 read/两 probe、主流程 STALE_CONTEXT、canonical/report/clear 未改变，unhandled1→0；另一个 live-budget probe保留实际 read error 对象/消息，证明没有错误转 null/permission。
- inherited tuple 已按 scalar复制、保留literal null/optional nonce；两个新增 public entry waits已移进try/finally，本地JSDoc顺序已纠正。未宣称当前两个已克隆 parent 曾发生 alias 导致的实际 stale mutation。

最终证据：`/tmp/plugin-status-owner-invalidate-final-01a1406d/{checks.json,scope-proof.json,current.log,tsc.log,owner-unobserved-read.ts.log,owner-read-live-error.ts.log}`及五mutant logs。此前HOLD/单变量RED保留在`/tmp/plugin-status-owner-invalidate-canonical-fact-01a1406d/owner-review.md`，包括seed-normalisation诊断比较错误，不冒称canonical被覆盖。

## 第七段已局部通过：wake rebuild

- 原分支 stop→business clear→unfenced commit，真实 store 拒绝后已清业务绑定；期望迁移到原bootstrap tuple/代次/预算的 typed canonical fact，再安全cleanup。
- 保持 fresh episode/epoch/nonce、discovering/suspended/INITIALIZING、instance_started / WORKER_CONTEXT_UNKNOWN，不替换成invalidator的不同事件/phase。
- 新三案在bf2旧分支实际RED；正常被真实store拒绝，两race先失败在clear recorder，预审要求把真实businessBinding断言移前并补fresh incident后重跑，不能凭recorder冒充直接mutation断言。
- 最终controller`f60eeddbe6540436ee491aa22527877c49582134afc6d0a998699e0ff838328a` / tests`0dc5352287c8da1eaa4e18597a1017406fc2d93e2545a843ddad0d33739a057f`，OWNER25named/实际unpipedtsc0，gate`55f416c9-a968-47c1-a3cb-5d1bddcc1730`实际局部接受。
- 同finaltests仅旧bf2三业务RED；去own-generation/stop后借tuple各真实错误返回重建state致FAIL。额外OWNER私有早期list-entry+HOLD隔离，current1PASS、仅移除bootstrapLive precheck即错误重建1FAIL，证明原bootstrap而不只own generation；不加进产品25计数、不倒写初始RED。
- AST只屏蔽existing rebuild branch，其他源码逐字同bf2；旧22 body不变。证据`/tmp/plugin-status-owner-wake-rebuild-01a1406d/{checks.json,scope-proof.json,current.log,old.log,own-generation.log,late-tuple.log,early-current.log,early-missing-parent-check.log,tsc.log}`。

## 当前执行与交付边界

- 原 writer：MiniMax M3.1 Flash Preview；不换 writer/model/CLI，OWNER 不代写产品源码。
- 上一 root `e1f1b5f4-c198-4465-a407-ddf414ba5af2` / child `ee27bcb6-af79-449c-a27f-b913a8deb436` 已 terminal blocked：postGREEN 监督请求过期，没有最终批准；非已证 quota/runner 故障。
- 第六 root `c9712779-bb5e-4f2d-91dd-379a88ed3889` / child `b1ef1d62-0960-4cf3-b1dd-16f393c107de` 已terminal。七字段/两path正确，但handoff将gate误展开为`64f2f1e1-2c39-4b7a-9f4e-0d5a6c8e1b47`；真实接受以明确监督回复`64f2b158-82fd-42e2-87ca-7c60b33cd06e`为准。错误artifact保留，writer新预审已承认；以后OWNER回复正文附完整UUID，不猜短前缀。
- 当前 root：`614318f3-4705-4ef1-8598-070be1b87b0c`，exact 原b1 native resume；child `abcdec02-b392-4c4e-be94-7cc6260ab54c`。
- 最新 packet：`/tmp/monitor-status-wake-rebuild-producer-01a1406d.md`；script `/tmp/monitor-status-workflow-wake-rebuild-01a1406d.js`，validate0。
- Strict 七字段 handoff + native checked evidence 保持；旧 pre-edit 或过期 post 请求不能充当 DONE gate。只按原生事件续接，不轮询等待。
- 任务板：revision633，callers 进行中，其后 budget→actions→cookies→spec→quality；status/owner/deps未变时只传实质进度，不做 no-op 写板。
- ERP core `f2c0fa7`、plugin core `718c83e` 已 push；版本仍0.4.26。runtime WIP 未整体交付/合并/发布；主仓 foreign gitlink/node_modules 不碰。
- ERP 实例锁与 ACK 的独立修正已验证，不重新开启；ERP 最终质量、共享 purpose/auth/model/migration/scheduler/GUI/MAIN/metadata/分页接线须明确 handoff。
- 没有当前 full/coverage/build、ERP baseline 或真实浏览器/HTTPS/CORS/TikTok/通知验收。旧测试数字不能冒充当前全量。

## 记录范围

此文件仅保留技术方案、当前实现与验证边界。早期过程见 Git 提交 `1f90588477314f59b0d1fa41308abdcefc00bb7c` 中本目录、原始 native artifacts 和 `/tmp/plugin-status-owner-invalidate-canonical-fact-01a1406d/docs-before-condense/`；未删除或改写原始证据。8/12/max1 等只属 OWNER 调度配置，不是用户数字额度或 provider hard cap。
