# Monitor 新旧列表兼容：owner 审核与验证

- 日期：2026-10-14；owner：Pi `01a13a78-197d-75fe-b961-7d67e36ce6f8`。
- 需求与验收基线：[技术方案](02-technical-design.md) §7 R1–R10；抓包事实以[原始记录](01-api-captures.md)为准。
- 结论：**离线实现及方案一致性审核通过；真实 Seller Center 验证未完成，不等同已部署/实店验收。**
- 修复根因：页面旧 `includes('/post_campaign_list')` 已能碰巧匹配 v2，但后台 URL 校验拒绝 v2，parser/live 容器校验不认识 `table_v2`。不是“页面完全没有捕获 v2”。
- 交付边界：独立插件 task branch；不推插件 master、不自动更新 ERP gitlink、不执行抓包 cURL、不对真实广告做开关操作。

## 1. 审核对象与代码边界

| 项 | 值 |
| --- | --- |
| 插件仓 / 工作树 | 独立 `ads-monitor`，`/home/schan/pi-workspace/monitor-api-01a13a78` |
| 分支 | `feature/campaign-list-v2-compat` |
| 基线 / 同步的插件 master | `e49d00bcea83d1c1607305bfd47493af8c6976f6`，0.4.22 |
| 生产实现 | `c7fd9e48c42b3ca7847b9b1d2487b30464346d96` |
| 最终审核提交 | `a9aae990c0626f23324cad003309c9c7ce39d482`，0.4.23 |
| 补测差异 | 相对 c7fd9e4 只增改 `tests/close-safety.test.ts`，129 行；生产源码差异为空 |
| 最终总差异 | 相对 e49d00b：18 文件、994 additions、79 deletions |
| 父仓文档分支 | `feature/monitor-api-compat`，工作树 `/home/schan/tts-erp/.worktrees/monitor-api-compat` |
| 审核时父仓同步基线 | `e989bb7ad4450e8945c6feefce866700874261ec`；显式 fetch master 后合入分支 |

2026-10-14 owner 显式执行 `git fetch origin refs/heads/master:refs/remotes/origin/master`，避免仅跟踪 task branch 的 fetch 配置导致误把旧 origin/master 当最新；插件 master 仍为 e49d00b，已是最终提交祖先。插件最终 HEAD 与远端 task branch 相同，工作树干净。

唯一协议 owner 为 `src/core/campaign-list-contract.ts`：exact-origin URL 路由、legacy/v2 容器选择、v2 cell 校验。Campaign 字段规范化、分页、查询时间窗、排序、阈值、关闭队列、关闭 API 与持久化继续复用既有 owner。

**复杂度收口**：增加约 150 行纯协议 seam，移除被共享出口替代的旧重复结构检查；没有新依赖、调度器、持久化协议字段或第二套 closer。保留旧接口是用户明确要求的外部兼容例外，只有未来显式批准的退役任务才能移除；没有自动跨版本 fallback。`campaign-pagination.ts`、storage、remote-config、popup、Feishu、Alarm 及关闭执行器未改动。

## 2. R1–R10 逐项审核

| ID | 审核证据与结果 |
| --- | --- |
| R1 | `campaign-list-contract.test.ts`、`page-hook.test.ts`、`background.test.ts`：新旧 URL、相对路径、任意前缀与 query 正向；foreign/non-HTTPS origin、v20/v2_extra、尾路径、query 假阳性负向。live 对不合法模板在 fetch 前失败。通过。 |
| R2 | contract/core 及既有全套回归：legacy 的所有 data 候选优先于所有 root 候选，保留 `data.list + root.table` 冲突优先级回归；旧消息/常量与 parser 默认 legacy 保留；v2 cell 接入相同字段规范化与排序。通过。 |
| R3 | 结构单测覆盖非 object、非零 code、缺失/错误容器、版本不匹配、坏 row/cell、重复 name 和保留名称；仅显式合法空数组表示空列表，损坏可选指标也失败。通过。 |
| R4 | `live-campaign-fetch.test.ts`：真实 fetch/parser 对两页 v2 聚合去重、保持 v2 URL，检查日期/page/active filter 改写及 query_list/dims/page_size/gmv_roi_mode 保留。通过；服务端是否支持过滤不由 mock 证明。 |
| R5 | `close-safety.test.ts` 新测：原始 v2 cell → 实际 fetch/parser/storage → 阈值评估 → 自动关闭队列，确认生效状态/cost/SKU 与网络/execute 顺序。contract/live 测试分别确认 page 可过滤 inactive、strict live mixed 整页失败。通过。 |
| R6 | 新 close-safety 测试验证捕获后 live 第 2 页坏 cell：存储 `campaignsComplete=false`、仅 page_capture 展示、无关闭执行；旧报告/关闭窗口隔离、queue/generation/concurrency 回归全绿。通过。 |
| R7 | 新 close-safety 自动关闭测试：关闭 API 桩成功后才发生 fresh v2 网络查询；目标仍存在→记录失败/保留 pending；完整显式空页→成功；回查第 2 页损坏→失败/不记成功，并断言该页确实请求。background 的测试关闭用例另验证共享出口转发与错误传播。通过。 |
| R8 | page-hook 的真实 fetch/XHR hook 测试验证 v2 捕获；background 的捕获测试验证 v2 模板接收及坏 payload 不覆盖健康模板；close-safety 的 processCapturedResponse 走实际 live/parser/storage。按层组合验证，未宣称整个真实浏览器桥接端到端运行。通过。 |
| R9 | 审核生产 diff：无新增 operation:1、真实 mutation、调度器、依赖或 storage schema；未提交抓包 fixture/ZIP/产物。测试数据为人工合成 TEST_；抓包只存父仓脱敏文档。通过。 |
| R10 | 五处版本统一 0.4.23，owner 最终重新安装依赖并独立运行 focused/full/coverage/tsc/build，均 exit 0，见下表。通过；静态规则与依赖审计基线不宣称为零。 |

### 审核中已纠正的偏离

1. legacy 容器优先级曾被 data/root 交错，已增加冲突回归并恢复原优先级。
2. 两个原本将 null/缺失容器当空列表的测试，按既定 fail-closed 方案改为检查精确结构错误；正向 fetch fixture 的 foreign origin 改为 Seller Center，负向 origin 测试保留并扩充。未放宽安全检查。
3. c7fd9e4 新增 background 关闭测试 mock 整个 `fetchLiveCampaigns`，不能证明分页/解析联动。owner 要求补测，a9aae99 用现有 close-safety harness 补足实际 fetch/parser/storage/queue 路径；补测未改生产代码。
4. 首轮 T1 的缺模块 import RED 不算业务性失败。补测用受控 mutation-kill 让 v2 URL 暂时路由 legacy，记录业务断言 RED 后恢复 GREEN，未留下 mutation。

## 3. owner 独立执行结果

环境：Node `v22.23.3`、npm `10.9.9`。本机默认 production 环境，安装显式 include dev，测试显式 test，构建显式 production；未修改系统全局环境。所有命令在独立插件仓执行；父仓只有文档变更，不运行 Python/数据库 suite。

| 命令 | exit | 结果 / 日志 |
| --- | --- | --- |
| `NODE_ENV=production npm ci --include=dev` | 0 | 595 packages；`/tmp/monitor-api-owner-a9aae99/install.log`；有基线审计告警，见 §5 |
| `NODE_ENV=test npx vitest run tests/campaign-list-contract.test.ts tests/core.test.ts tests/core-coverage.test.ts` | 0 | 3 files / 53 tests；`/tmp/monitor-api-owner-a9aae99/core.log` |
| `NODE_ENV=test npx vitest run tests/live-campaign-fetch.test.ts tests/page-hook.test.ts tests/background.test.ts tests/close-safety.test.ts` | 0 | 4 files / 231 tests；`/tmp/monitor-api-owner-a9aae99/flow.log` |
| `NODE_ENV=test npm test` | 0 | **26 files / 409 tests**；`/tmp/monitor-api-owner-a9aae99/test.log` |
| `NODE_ENV=test npm run test:coverage` | 0 | 409 tests；**97.35% statements / 93.73% branches / 98.34% functions / 97.35% lines**；`/tmp/monitor-api-owner-a9aae99/coverage.log` |
| `NODE_ENV=test npx tsc --noEmit` | 0 | 无错误；`/tmp/monitor-api-owner-a9aae99/tsc.log` |
| `NODE_ENV=production npm run build` | 0 | Chrome MV3，315.51 kB；`/tmp/monitor-api-owner-a9aae99/build.log` |
| `git diff --check e49d00b..HEAD` | 0 | 无空白错误 |
| `git diff --exit-code c7fd9e4..HEAD -- src entrypoints wxt.config.ts package.json package-lock.json README.md CHANGELOG.md` | 0 | 补测未改变生产实现或版本文件 |

覆盖率 floor 仍为 95/90/95/95，没有降低。机器可读命令结果：`/tmp/monitor-api-owner-a9aae99/receipt.json`。本地临时日志不保证永久留存，本文件保留命令、提交、计数和审核结论。未生成 release ZIP，故不提供虚构 ZIP 路径/hash，也不宣称已发布扩展。

### 行为性 RED / GREEN 证据

owner 检查了 worker 原始日志，而非仅采用摘要：

- `/tmp/monitor-api-followup-D-RED.log`：将 v2 router 临时变为 legacy，3 文件 **10 failed / 49 passed**，含 `expected 'legacy' to be 'v2'`、v2 容器解析失败与不完整 fallback 状态断言失败，非 import/fixture 错误。
- `/tmp/monitor-api-followup-D-GREEN.log`：恢复后同命令 **59 passed**。
- 恢复后的生产 diff 为空；owner 最终全量独立重跑 409 passed。RED 是受控 mutation 证据，不把它伪称为最初不存在功能时的完整 test-first 历史。

## 4. 抓包脱敏与完整性

owner 在最终审核重新从原 Pi 会话消息 `043834da`、`7ecdee03` 提取原文，仅替换 `-b` Cookie 及 `x-csrftoken` header 的值，与文档两个 text fenced block **逐字符完全相同**：分别 1 Cookie + 1 CSRF、2 Cookie + 2 CSRF，共 6 次凭证替换。请求/响应、空白、ID、所有非凭证 header、嵌套 JSON 字符串均未重写。

- 文件：`01-api-captures.md`，83,352 bytes。
- SHA-256：`1a498ab7ef3a62f3bdf85af18123e84a33bd9fbeaa3e94e1ed921c95c50b4ad7`。
- 与最初经校验提交 `518ab92` 的该文件无差异。
- 脱敏值为 `[REDACTED_COOKIE]` / `[REDACTED_CREDENTIAL]`。未把原凭证复制入验证日志、插件 fixture 或提交。

## 5. 明确保留的风险与未覆盖项

1. **实店 active filter 未验证**：抓包只证明页面使用 `no_delete`，未证明私有 v2 服务端支持监控标准化的 `delivery_ok`。需要在真实店铺先做经授权的只读查询验证。返回 mixed/inactive 时 strict live 会整轮失败，不把过滤失败当空列表或悄悄换接口。
2. **真实 Chrome/TikTok/Feishu 未运行**：网络、Chrome API 和 Cookie 边界均为桩。真实拦截、店铺权限、登录态、服务端分页/状态变化不在此次离线证据中；本轮没有真实广告副作用。
3. **既有无模板跳过回查**：`verifyCampaignClosedAfterClose` 缺 requestTemplate 时直接通过的既有分支未改，本轮所有新增关闭链路测试提供合法模板。不能由此宣称所有关闭情形都有完整回查。
4. **币种**：原始 v2 抓包缺少 currency，保留现有默认 USD，不代表真实店铺币种已被证实。
5. **静态规则基线**：owner 主动检查 12 个源码/测试路径，5 confirmed clean、6 inconclusive、1 有 4 条 page-hook 基线 error：L11/L14/L72 `require-safety-comment-for-as-unknown-as`、L109 `no-unknown-returns`。构造与 e49d00b 逐字相同；另有 nested-ternary/import-extension warning。未加抑制。补测 `close-safety.test.ts` 独立主动 probe 为 0 diagnostics / confirmed clean。`tsc` 成功不等同所有 LSP 诊断 clean。后续事项：TowerDo `f-ae98fa1b-187`。
6. **依赖审计基线**：`npm ci --include=dev` 报 **30 vulnerabilities（7 moderate / 16 high / 7 critical）** 与旧 tar/glob 弃用警告；剔除包自身版本后 lockfile JSON 与 e49d00b 完全一致，未新增依赖风险。此为 npm 摘要，不等同已确认运行期可利用漏洞；需独立依赖维护任务逐项 triage，不在本轮自动 audit fix/升级。TowerDo `f-97be4a56-b03`。
7. **集成边界**：远程配置 lane 同时涉及 background/storage，未将其未合并代码混入此分支。最终实现与当前 e49d00b master 对齐；未来 master 非文档变化后必须重新同步验证，不能直接复用本次检查。

## 6. 离线审核完成时的交付状态（合并前历史）

- 源码：最终 a9aae99 已普通 push 到独立插件 task branch，保留旧接口，不推插件 master、不推进父仓 gitlink。
- 文档：本目录三个文档由父仓分支提交推送；registry 记录该文档 HEAD 与同步 master。父仓存在更早 ready lanes，本轮不越过默认 FIFO 合并顺序；文档分支保留待集成，插件分支保留供实店验证/后续合入。
- 本次审核 stop state：**离线协议兼容切片 done；实店/部署 needs-verification**。证据强度 B：直接目标及旧行为回归通过，保留明确私有 API/浏览器/审计风险。
- 下一步价值最高的验证：人工在已授权店铺确认 v2 的 active-only 过滤及完整分页，先只读，不用自动关闭试探服务端支持。若不支持则报告不支持并回到 owner 决策，不新增 fallback。
- 回滚：独立插件按需 revert 本任务两个提交；无持久化 schema 迁移或源数据清理，不涉及生产数据恢复。

## 7. 用户授权后的合并追记（2026-10-14）

用户在离线终审报告后明确要求“完成了就合并并且推送”。本次据此集成此已验证 lane，覆盖默认等待 FIFO 的此前交付安排；不集成其他 ready lanes，不推进父仓插件 gitlink，不部署、不进行真实广告操作。

- 插件：从最新 e49d00b master 构造 `--no-ff` 合并提交 **`68ecacd58d9be8cb04582f6e4939743d9a4f2ef7`**，树与 a9aae99 完全相同；已普通 push 至 `brakchen/ads-monitor` 的 master，并读回远端 SHA 一致。
- 对该精确插件合并提交重新安装依赖，focused 59 passed、全量 409 passed、coverage 97.35/93.73/98.34/97.35、tsc、生产 build、diff check 全部 exit 0。日志：`/tmp/monitor-api-master-merge-01a13a78/plugin-{integration,install,focused,test,coverage,tsc,build}.log`。
- 父仓：从记录的 ready HEAD e65edc1 合入最新 master，另提交本追记与本 lane registry 清理；只含本目录文档和 registry，不含 Python、tests 或 gitlink 变更。精确 prospective master 需重新检查本地文档链接、抓包 hash 与差异，成功后才普通推送。最终 master SHA 以 Git merge/cleanup 历史及远端读回为准，避免文档自引用 commit hash。
- §5 的实店 active-filter、真实浏览器、旧静态规则与依赖审计风险继续存在；合并成功不表示这些项目已验证。
