# Runtime review — 当前技术裁决

## 接受边界

整体仍 **BLOCKED / 未交付**。纯核和七个 controller/runtime 微切片的局部接受，不等于全部 callers、完整预算、真实浏览器副作用、ERP 接线或发布通过。

唯一产品/协议基线：`docs/plugins/plugin-status-report.md`。旧候选的全量计数、coverage/build、过期门禁和 native complete 不作为当前完整验收。

## 当前实现

- storage 以 raw absent/present/unknown 区分状态，实际 mutation 前原子核验原 context 与同步 predicate；返回 typed `ObservationWriteResult`。
- runtime 原样传播 result/fence；不把 rejected 当已应用，不 dispatch 拒绝状态。
- controller 已迁移 snapshot、空 bootstrap、safe-stop、passive observation 和 invalidation；首次 generation、scalar PRE tuple、同一 budget 由原操作拥有。
- `applied` 与 `accepted` 分离；canonical commit 统一状态与报告。满队列可以有事实应用而无报告入队，不能把这两者合成授权。
- negative-parent delegation 同步先消费父许可，然后才做合法自身 generation 撤权；不借子操作新读到的 nonce/gen，也不因自己的撤权拒绝全部正常父操作。
- durable lifecycle fact/report 在 business clear 前实际存在。已应用事实不因后续 cleanup 失权被抹去。

## 第六微切片最终局部通过

OWNER postGREEN gate：`64f2b158-82fd-42e2-87ca-7c60b33cd06e`，只接受 `invalidate-canonical-fact`。

- controller：`bf2f3c5c3ee3fd4b40337b80ea5cd0180ddbd1ff447a7580406ee5142405818f`。
- tests：`317f975d7415c843ff61f8607a5ceb2b25757104a11896e27b6f0228bdfcc40d`。
- OWNER 冻结全 src/tests/entrypoints/config，亲跑 **22 named PASS、实际 unpiped tsc exit0**。
- 五个 source-only 控制重新执行并分别失败于真实结果断言：移除自身 generation、stop 后借 tuple、单独移除 ensure/observe 的 parent origin、always-reject-origin（两个正常父路径均失败）。不只检查 recorder。
- 迟到 read 拒绝探针：同样真实三 read/两 probe、原预算过期、主流程 `STALE_CONTEXT`，修前 unhandled1，最终 unhandled0；canonical/report/clear 不变。
- 额外 live-budget read-error 探针保留原 error 对象/消息，无 unhandled、无写入，证明 observer 没把 I/O 错误转成 null/permission。
- 从上一候选到最终版本，invalidator 以外源码 AST 屏蔽后字节相同；原89个 literal case body无改动（其中包含旧15），runtime/storage/runtime tests/chain/package 与基线相同，package-lock diff0。
- LSP：7个既有 unused hints，controller push-only inconclusive；**不称全 clean**。实际 tsc0 独立成立。

完整证据：`/tmp/plugin-status-owner-invalidate-final-01a1406d/{checks.json,scope-proof.json,current.log,tsc.log,owner-unobserved-read.ts.log,owner-read-live-error.ts.log}`；同目录五个 mutant logs。

预修候选 `1d8/02be` 的 OWNER 证据和 HOLD 原因见 `/tmp/plugin-status-owner-invalidate-canonical-fact-01a1406d/owner-review.md`。初始新七案准确分类是 **3业务RED + 2 recorder契约RED + 2旧源码已PASS的行为coverage**；后续 raw-read 是独立真实异步安全 RED。无效 setup/timeout、移除的无效额外 case 不追认为有效 RED；原日志、supervisor过期、tsx自动安装尝试及比较 seed-normalisation 的诊断错误均保留，不扩写成当前业务漏洞。

## 尚待关闭的技术缺口

| 范围 | 所需实现与证明 |
| --- | --- |
| 其余 producers / mandatory union | ensure rebuild、recover/failed、resume 等不再发送 unfenced binding；所有真实消费者消费 applied/refused；类型强制 observation-only 与 binding+fence 分支 |
| 原90秒窗口 | 初始化、read/stop/commit、两个 refresh-release、晚到值共享同一 budget，不以 Promise.race 假称取消 I/O；恢复有效 entered-boundary 回归 |
| 实际业务写点 | bindTab/clearBinding 必须在自身 await 后实际 mutation 点消费原 fence；外层清理检查不能关闭此缺口，真实 Chrome/shared adapter 仍 T5 |
| Action-start TOCTOU | authorize→await persistDecisionStarted 后，首个 reload/capture/start 前检查 deadline、epoch、nonce、enablement；terminal CAS 不能撤销已发副作用 |
| Cookie 原因保真 | throw/unavailable/timeout 不等 verified different store；wake 和 post-reload 都应只将真实不同身份归为 conflict |
| Fresh reviews | 精确新 candidate 先 spec 再 quality；修后重 spec，不能复用此前 gate |
| ERP / shared / T5 | 最终 isolated baseline、明确 auth/purpose/model/migration/scheduler/storage/GUI/MAIN/metadata/分页 handoff，真实 HTTPS/CORS/Chrome 等分层验证 |

ERP `lock_instance.populate_existing` 的真实两 Session 交错、canonical ACK 对更高 revision/superseder 的拒绝已独立验证，不重开。共享 purpose 缺失必须 fail closed，不能借普通 RW 或新 registry 绕过。

## 后续执行契约

保持原 MiniMax 唯一产品 writer、原 cwd/profile/native checked contract；OWNER负责独立验收、协调和 Git。每段有效 RED→新 pre-edit WAIT→最小 canonical 修复→相关 GREEN/实际 tsc→独立新 postGREEN。不得改生产数据库、真实凭证、TikTok 写请求或真实通知。

第六native rootc971/childb1已terminal；七字段/两changedFiles正确，但ownerGateRef错误展开为`64f2f1e1-2c39-4b7a-9f4e-0d5a6c8e1b47`，不认可该虚构ID，实际接受以完整64f2b158监督回复为准。原artifact保留；writer在新预审已承认，OWNER以后回复正文写完整ID而不让其猜短前缀。

第七root`614318f3-4705-4ef1-8598-070be1b87b0c` exact原b1续接`wake-rebuild-producer`，childabcdec02。只迁移ensureInitialized的unprovable重建分支，保持fresh episode/discovering/INITIALIZING及instance_started WORKER_CONTEXT_UNKNOWN领域含义。新预审`04104954-f51c-42de-8812-efa64bc990a9`要求先真实business断言移到callOrder前、补新incident RED，再原bootstrap tuple/live在自身撤权前消费、typed实际fact-before-clear；保护旧22，相关实际tsc后须独立新postGREEN。随后按条件先在旧bf2直接证明业务null-vs11 RED，再修现有branch；实际新gate`55f416c9-a968-47c1-a3cb-5d1bddcc1730`已局部接受controller`f60eeddbe6540436ee491aa22527877c49582134afc6d0a998699e0ff838328a`/tests`0dc5352287c8da1eaa4e18597a1017406fc2d93e2545a843ddad0d33739a057f`。OWNER25named/tsc0，同finaltests旧bf2三业务RED，own-gen/late-tuple各返回错误重建state致FAIL；额外私有early-list+HOLD隔离current1PASS、删bootstrapLive precheck1FAIL，非产品第26案。AST屏蔽branch其余源码逐字同，旧22不变。证据`/tmp/plugin-status-owner-wake-rebuild-01a1406d`；root/child已terminal，七字段/两path/准确完整postGate已核，非全部callers接受。

下一`resume-fenced-producer`由root`5e886672-cf85-4567-92ff-1f5b1325af8d` exact原abc继续，仅tests-first/新预审；复用已有createSnapshotCommit原frame，不改helper/另存terminal、不闭合physical scheduler-await权限。原25保留，新scope另需准确pre/post门禁。

完整 full/coverage/build、版本同步、ERP baseline、prospective merge 和发布均另行执行，不提前记完成。

## 历史证据

早期过程不再进入当前技术摘要。完整历史保留在 Git 提交 `1f90588477314f59b0d1fa41308abdcefc00bb7c` 的本文件、原始 native artifacts、各 OWNER 冻结目录和本轮 `docs-before-condense/` 快照；没有改写门禁或删除原始产物。
