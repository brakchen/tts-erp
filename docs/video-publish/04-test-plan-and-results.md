# TikTok 视频发布：测试计划和结果

> **文档导航**：[产品方案](01-product-proposal.md) · [技术设计](02-technical-design.md) · [实施计划](03-implementation-plan.md) · [测试计划和结果](04-test-plan-and-results.md)

> 来源基线：`docs/video-publish-design@b88b26c` 中的 `docs/design/tiktok-video-publish.md`。
> 本目录按职责拆分原 2781 行单体文档；四个文件共同构成当前项目文档集。

## 文档职责

本文同时保存测试策略与按日期固化的回测结果。计划定义“要证明什么”，结果只记录实际执行过的证据；未执行、被跳过或使用 mock 的边界必须明确写出，不得记为通过。

## 当前结论

- **状态：未达到发布完成门槛。**
- 2026-10-06 审计使用真实模板与静态资源，但 API、MinIO、Artemis、ADB 均存在 mock/stub 边界。
- 当时 `tests/publishing` 的替代命令跑过 19 个文件、290 个用例；文档中的 canonical `publishing` 命令当时收集 0 个用例。
- 现有 Wave 2 仍有 10 条缺少 PASS/FAIL 证据。
- Firefox/Safari、真实鉴权 HTTP、真实 MinIO/Artemis/ADB、多 Worker 并发压测和真机发布仍未覆盖。

## 结果记录规则

| 状态 | 含义 |
| --- | --- |
| PASS | 在标明的 commit、环境和命令下实际通过 |
| FAIL | 可稳定复现，已有失败证据 |
| BLOCKED | 缺少账号、设备、服务或明确产品契约 |
| NOT RUN | 尚未执行；不得按通过统计 |
| MOCK ONLY | 只验证本地契约，不代表真实依赖已通过 |

## 15. 测试策略

测试只能通过：

```bash
bash scripts/test_isolated.sh ...
```

### 15.1 Domain 与数据库

- 合法/非法状态转换；
- 原任务多 attempt；
- 相同 session ID 幂等；
- 同任务活跃 attempt 唯一；
- 全局 running task 唯一；
- `FOR UPDATE SKIP LOCKED` 并发领取；
- publish/cleanup 状态正交；
- retry/verify 的服务端 allowedActions。

### 15.2 Adapter

- MinIO ticket、HEAD、下载校验和删除失败；
- ADB 设备离线、锁屏、push、size 和 MediaStore；
- Artemis 指定 `device_serial`、相同 session 重提、状态轮询、404→全局状态 fallback；
- completed/success/failed/rejected/timeout 映射；
- steps=0 安全失败与最终点击后的不确定失败分类。

### 15.3 API

- create 的 `clientRequestId` 幂等；
- confirm 前后状态；
- readonly/readwrite/admin 权限；
- 非法状态返回 409 + allowedActions；
- cancel/retry/verify/cleanup retry；
- 列表不泄露 Prompt、token、完整设备 serial 或大段 output。

### 15.4 前端

- 文件格式/大小、文案计数和按钮可用性；
- XHR 进度、取消、URL 过期后重试；
- 创建双击只生成一个任务；
- 状态轨道节点与服务端 stage 映射；
- needs_review 不显示直接重试；
- drawer 执行历史、每条 attempt 的完整 Artemis ID 和清理状态；
- Artemis ID 一键复制，复制值为完整 UUID，排队未建 attempt 时显示“尚未创建”；
- 智能/固定/关闭三类定时刷新、手动刷新、页面可见恢复、失败退避和 stale response 防护；
- 401 登录跳转、403 只读模式；
- 键盘、dialog 焦点、aria-live、reduced motion；
- 390px、768px、1440px 三档视觉冒烟。

禁止在测试中向真实 TikTok 发布。端到端测试默认使用假 Artemis/ADB adapter；真实设备验证必须先 dry-run，并由用户显式确认。
## 26. Definition of Done

### 26.1 后端

- [ ] migration 在隔离测试库 upgrade/downgrade（空表）通过；
- [ ] create 幂等键在并发双请求下只生成一行；
- [ ] confirm 对不存在、大小错、状态冲突有稳定错误；
- [ ] 两个 Worker 并发领取时全局最多一个 running；
- [ ] attempt 在调用 Artemis 前已持久化 session ID；
- [ ] 相同 session ID 的 transport retry 不重复执行；
- [ ] Worker 重启后继续查询原 session；
- [ ] 安全失败自动重试，模糊失败自动 verify；
- [ ] success 与 cleanup failure 独立；
- [ ] API 列表/详情返回完整 Artemis ID 和 allowedActions；
- [ ] 日志不泄露 caption/Prompt/secret/signed URL。

### 26.2 前端

- [ ] 文件选择、预览、清除和 object URL 回收正确；
- [ ] 文案原样保留并准确计数；
- [ ] 上传进度、取消、失败重试与 confirm 闭环；
- [ ] 双击提交只创建一个任务；
- [ ] 轨道阶段、状态 badge、队列位置与服务端一致；
- [ ] 智能/固定/关闭刷新模式和 localStorage 偏好正确；
- [ ] 轮询不重叠、隐藏降频、失败退避、旧响应不覆盖；
- [ ] 列表和 drawer 展示/复制完整 Artemis ID；
- [ ] 每条 attempt 都保留自己的 ID；
- [ ] needs_review 不显示直接重试；
- [ ] 390/768/1440px 可用；
- [ ] 键盘、焦点、aria-live、reduced-motion 通过。

### 26.3 部署

- [ ] bucket 私有且 CORS 仅允许生产 origin；
- [ ] spool 权限、磁盘和 orphan 清理验证；
- [ ] systemd 重启和 SIGTERM recovery 验证；
- [ ] API/Worker/Artemis/ADB readiness 可诊断；
- [ ] 模拟器完成全流程但不真实发布；
- [ ] 真机 staging-only 通过；
- [ ] 用户显式确认的一条真实任务成功且三处清理完成。

---

## 回测报告：2026-10-06

> 以下为原始回测报告迁移内容。其结论绑定当时标注的 commit 与环境；后续修复必须新增结果记录，不得覆盖历史证据。

- 仓库：`/home/schan/tts-erp`，分支 `master`
- 受审版本：**HEAD `9f325ec`**（`fix(publishing): 确认弹窗接入共享 .op-dialog 并对齐 §22.8 文案`）
- 日期：2026-10-06
- 方法：3 个 Agent 并行 Review（UI / 交互 / 后端）+ 主 Agent 独立复现与交叉验证
- 本轮**未修改任何业务代码**；所有临时产物在 `/tmp/vp-audit/`

---

### 0. 先说方法论：审查期间目标代码被改动了

**这条影响所有"样式/模板类"结论的读法，请先看。**

- 22:33 主 Agent 首次读取 `video-publish.css` 时，两个确认弹窗**有**完整表面样式。
- 22:48 另一会话（`session-01a1119e-c25facb3`）把提交 `9f325ec` cherry-pick 到 `master` 并重启服务
  （该会话广播："video-publish 弹窗修复 + .op-dialog 收敛已上 master 9f325ec"）。
- 该修复的思路是：把弹窗表面样式上收到共享的 `.op-dialog`（`common.css`），模板给两个 `<dialog>`
  加 `class="op-dialog"`，而不是继续堆页面级 id 选择器。

**由此产生的一次误判（已修正，记录在案）：**

| 结论 | 判定 | 依据 |
| --- | --- | --- |
| UI lane 的 UI-04「两个确认弹窗落到 UA 默认样式」 | **在 `59c06c6` 上为真，在当前 HEAD `9f325ec` 上已修复** | 用当前 HEAD 重新渲染后复测：`#publish-action-dialog` = `background rgb(244,239,228)`（`--paper`）/ `color rgb(27,24,20)`（`--ink`）/ `border 1px` / `padding 22px` / `h2 20px serif` / `::backdrop rgba(27,24,20,0.48)` |
| 主 Agent 中途"推翻"过 UI-04 | **那次推翻是错的** | 推翻时依据的是 22:35 一次性渲染的旧模板 `/tmp/vp-audit/pages/video-publish.html`；该产物在 22:48 提交后已过期 |

教训（已同步到共享 board）：任何样式/模板类结论必须标注所测 commit；harness 的渲染产物必须与 HEAD 一起刷新。

---

### 1. 检查范围

#### 1.1 运行环境

| 项 | 值 |
| --- | --- |
| 服务 | `http://127.0.0.1:9877`（`auth_mode=enforce`，未登录，无法直连真实 API 做端到端） |
| 前端 | Playwright + Chromium 1243，本地 http server 提供**真实渲染**模板 + **真实** static 资源 |
| Mock 边界 | **只 mock `/v2/video-publish/*`** 与 MinIO 预签名 PUT；未连真实设备 / Artemis / MinIO |
| 后端 | `bash scripts/test_isolated.sh`（每次克隆 `tts_erp_test_template` → `tts_erp_test_*` 后即 drop）；**未接触生产库**，未 `alembic upgrade` |
| Mock 契约来源 | `video_publish.py::_snapshot / config / devices / list_tasks / current / detail` + 设计文档 §9/§10/§21.15 |
| 视口 | 2560 / 1440 / 1024 / 900 / 768 / 720 / 480 / 390 / 360 |

#### 1.2 已验证流程

首屏四请求链路（config → devices → current → tasks）、任务列表渲染与筛选、9 列移动端卡片化、
drawer 打开/内容/关闭、两个确认弹窗、drop-zone 点击与拖拽、文件校验矩阵（扩展名/MIME/大小/文案长度/emoji 码点）、
文案计数、完整创建链路（POST /tasks → XHR PUT → confirm-upload）、慢 PUT 下取消上传、上传失败恢复、
行内动作矩阵（查看/取消/重试/核验/重试清理/继续上传/复制）、drawer 动作、ESC/backdrop 关闭、
响应乱序、连续切换筛选、浏览器前进后退、pagehide/visibilitychange、13 种后端状态组合、100 条数据、
长文件名/长文案/空 captionPreview/`queuePosition=1`/`retryBudgetUsed=0`/`artemisSessionId=null`、
`/config` 500、`/tasks` 500、`/devices` 503、PUT 403/413/500、Idempotency 重放、
`tests/publishing` 19 文件 290 用例全绿。

#### 1.3 未覆盖项（明确声明）

- 真实 MinIO / Artemis / ADB / 设备在线探测：全部 stub。
- 未登录态端到端：服务端 `auth_mode=enforce`，没有可用凭据，**所有 API 行为均来自 mock + 服务端代码/测试**，未做过真实 HTTP 往返。
- Firefox / Safari 的 `<dialog>` UA 默认值差异（本环境仅 Chromium）。
- 多 Worker 真实并发压测（靠 `uq_video_publish_one_running` + `pg_advisory_xact_lock` 静态论证）。
- 真实 `<video>` 解码（无 mp4 解码器，预览元数据走 `?×?` 兜底）。
- 剪贴板权限被拒分支。
- 长时间退避 / 时钟漂移。

---

### 2. Bug 清单

> 同一根因的多个现象已合并。`lane` 列标明该条由谁发现、`核验` 列标明主 Agent 是否独立复现。
> 优先级定义按用户口径：P0 严重数据损坏/安全/系统不可用；P1 核心流程受阻或关键状态错误；P2 局部异常或明显影响使用；P3 轻微/一致性。

#### P0

**无。** 后端 lane 报了 1 条 P0（BE-01），主 Agent 逐行核对代码后**下调为 P1**：该缺陷是"必然 500 的单端点"，
事务会回滚、状态无损、系统其余部分可用，不满足"数据损坏 / 安全 / 系统不可用"。它仍是本次最严重的一条。

---

#### P1

##### P1-1　设备清理失败的任务点「重试」必然 500 Internal Server Error
- **层**：后端领域 + 后端 API　**确认状态**：已确认（代码链路完整核实）　**发现**：backend lane (BE-01)
- **前置条件**：`status=failed` / `stage=done`，`cleanup_intent=preserve_state`，`device_cleanup_status='failed'`，
  `publish_budget_used < max`，最后一条 publish attempt `retry_safe is True`，`object_uploaded_at` 非空、`object_deleted_at` 为空。
  该组合由 `_safe_retry_values()`（repository.py:369-374 写 `pending`）→ `finish_cleanup_work`
  （repository.py:1466-1477 落成 `failed`）正常产出，**完全可达**。
- **预期状态转换**：`failed/done` → 点「重试」→ 返回 409 + 稳定错误码，任务保持 `failed/done`。
- **实际状态转换**：`allowed_actions` 判定 `retry` 可用（**所以按钮会显示**）→ `retry_task()` 只检查
  `object_cleanup_blocks_input`（**只管 object 维度**，submission.py:421）→ `queue_task()` 无条件写
  `cleanup_intent='none'`（repository.py:1651）→ 违反 CHECK `video_publish_task_cleanup_owner_check`
  （models/publishing.py:50-68）→ `IntegrityError` **未被捕获** → 500。
- **触发条件**：用户在列表或 drawer 点一次「重试」。
- **影响**：用户看到裸的 `Internal Server Error`（前端 `runTaskAction` 只对 409 重绘按钮，500 走
  `notice(error.message)`），违反 §10.12「禁止只显示 操作失败 / 原始堆栈」。同一任务上 `retry_cleanup`
  是可用的 → **门禁不对称**。事务回滚，状态无损。
- **代码位置**：`publishing/domain.py:236-259`（FAILED 分支只查 object）、`publishing/submission.py:412-454`
  （`retry_task` 缺 device/spool 门禁）、`publishing/repository.py:1646-1664`、`db/models/publishing.py:50-68`、
  `api/v2/video_publish.py:1225-1263`（只 catch `LookupError` / `ValueError`）。
- **根因**：`retry_task` 的输入门禁没有对齐 `replacement_cleanup_pending`（device/spool 维度）。
  `replace_upload`（submission.py:341-353）**有**这个门禁 → 同一个 `queue_task` 入口，两条路径门禁不一致。
  `allowed_actions` 与写路径又各判各的，导致"按钮可见"≠"服务端可执行"。
- **建议**：把 `retry_task` 的门禁补成与 `replace_upload` 对称；`allowed_actions` 的 FAILED 分支复用同一判定源；
  `retry` 端点对 `IntegrityError` 兜底映射成 409。

##### P1-2　切换筛选命中 304 时，任务表显示的是**上一个筛选**的数据
- **层**：前端交互（跨层：与后端 ETag 契约交互）　**确认状态**：已确认（线缆级证据，主 Agent 独立复现）
  **发现**：interaction lane (UI-01) + 主 Agent (M-01)，两条独立路径得到同一结论
- **前置条件**：队列空闲（`/tasks` 载荷稳定）→ 服务端 `_etag` 排除 `serverTime` 后两次载荷字节相同 → 命中 304。
- **复现**：`/tmp/vp-audit/s-etag.js`
  ```
  -> /v2/video-publish/tasks                INM=-                <- 200   （全部，2 行）
  -> /v2/video-publish/tasks?status=failed  INM=-                <- 200   （失败，1 行）
  -> /v2/video-publish/tasks                INM="etag-for-all"   <- 304   ← 切回「全部」
  -> /v2/video-publish/tasks                INM="etag-for-all"   <- 304   ← 点「立即刷新」仍是 304
  表格：1 行（仍是 failed 子集），而「全部」tab 已高亮
  VERDICT: *** MISMATCH: table still shows the 'failed' rows ***
  ```
- **预期**：切回「全部」后表格显示全部任务。
- **实际**：表格仍是失败子集；「立即刷新」也恢复不了，**只能刷新浏览器**。
- **代码位置**：`static/js/video-publish.js:22`（`etags: new Map()`）、`:47`（按 path 读 ETag）、`:59`（按 path 写 ETag）、
  `refreshList()` 收到 `notModified` 就整段跳过 `renderTasks`；`filter-tabs` click 处理器只重置
  `state.listChannel.nextCursor`，**没有作废该 path 的 ETag**。
- **根因**：`state.etags` 的 key 是**请求 path**，而**已渲染的数据源**（`listChannel.items`）不在 key 里。
  304 的语义是"这个 path 的载荷没变"，代码把它当成了"屏幕是最新的"——但屏幕上次是用**另一个 path** 画的。
- **建议**：把"已渲染来源"纳入缓存有效性——切筛选/重置 cursor 时作废列表 path 的 ETag，
  或这些请求首次不带 `If-None-Match`。不要让 304 跨 path 复用。

##### P1-3　再次打开同一任务的详情 drawer 打不开，且界面完全无反应
- **层**：前端交互　**确认状态**：已确认　**发现**：interaction lane (UI-02)
- **前置条件**：打开任务 A 的 drawer → ESC 关闭 → 再次点 A 的「查看」。
- **复现**：场景 `s-reopen.js`
  ```
  1st open      -> drawer open: true  | content len: 351
  closed        -> true
  detail req INM: "det1"
  2nd REOPEN    -> drawer open: false | content len: 351   ← 351 是上次残留的 DOM
  ```
- **预期**：第二次点击必须重新打开 drawer。
- **实际**：`open: false`，无提示、无报错。用户必须先点别的任务再点回来。
- **代码位置**：`static/js/video-publish.js:892-903`
  ```js
  state.detailTaskId = id;
  await refreshDetail();
  if (state.detail?.taskId === id) { drawer.showModal(); }   // 304 时 state.detail 从未被赋值
  ```
  `:743-745` 的 304 分支不写 `state.detail`；`:924` 的 close 事件已把 `state.detail = null`
  → 条件恒为 false。后端 `api/v2/video_publish.py:1186-1191` 对 detail 也做了 `_conditional`。
- **根因**：与 P1-2 同源——**把 304 当成"状态已最新"**。但这里更严重：drawer 的"打开"动作被绑死在
  "必须拿到响应体"上，一次条件请求命中 304 就等于"不打开"。
- **建议**：`openDetail` 不应以 `state.detail` 刚被赋值为前提。内容刷新与弹窗显示解耦——
  settle 后无条件 `showModal()`，304 时用已有快照渲染。
- **注**：P1-2 与 P1-3 同根因（"304 = 屏幕已最新"这一错误等价），但**修复点不同**
  （P1-2 需作废跨 path 的缓存；P1-3 需解耦打开动作与取数），故分列两条。

##### P1-4　点过「继续上传」后，本页再也无法创建新任务，且没有任何页面内退出口
- **层**：前端交互　**确认状态**：已确认（主 Agent 独立复现）　**发现**：主 Agent (M-02)
- **前置条件**：列表里有一个 `pending/awaiting_upload` 的任务 → 点「继续上传」。
- **复现**：`/tmp/vp-audit/s-main-verify.js` 步骤 E
  ```
  E) after continue_upload, caption = "原文案"  notice = "请选择原视频以继续上传"
  E) pick different file -> notice: "请选择原文件 resume-me.mp4（65536 字节）" | submitDisabled: true
  E) 清除选择 hidden attr =      ← state.file 为 null，两个按钮都隐藏
  E) 重新选择 hidden attr =
  E) brand-new pick  -> notice: "请选择原文件 resume-me.mp4（65536 字节）" | submitDisabled: true
  ```
  连**完全不同的**新文件（`brand-new.mp4` + 全新文案）仍被拒绝。
- **预期**：用户应能放弃续传，回到正常创建流程（§10.14 前端状态机里没有"锁死"态）。
- **实际**：本页创建入口彻底不可用。唯一退出口是把原文件传完（成功/取消后 `clearForm`）或刷新浏览器。
- **代码位置**：`static/js/video-publish.js`
  - `pick()`：`state.resumeTask && (name!==... || size!==...)` → 拒绝 + `state.file = null`
  - `clearSelection()`：**不清** `state.resumeTask` / `state.clientRequestId`
  - `clearForm()`：才清（只在创建成功/幂等重放/取消上传后调用）
  - `renderForm()`：`reselect.hidden = !file; clear.hidden = !file;` → `state.file` 为 null 时两个按钮都消失
- **根因**：「续传中」是一个**没有退出路径**的隐式状态：它没有 UI 表示，也没有可触发的清理入口。
- **建议**：给续传态一个显式的「放弃续传」出口；`clearSelection` / 取消续传时一并清
  `resumeTask` + `clientRequestId`。

##### P1-5　`GET /tasks/current` 用 `ORDER BY id` 混排 running 与 cleaning，会返回"错误的当前任务"
- **层**：后端 API　**确认状态**：已确认（探针实证 + 代码核对）　**发现**：backend lane (BE-03)
- **预期状态转换**：`/tasks/current` 恒返回**正在跑的**那个设备任务（§10.5「轨道只显示全局唯一的当前设备任务」）。
- **实际**：查询条件是 `(status='running') OR (cleanup_intent!='none' AND device_cleanup_status IN ('pending','failed'))`，
  再 `.order_by(VideoPublishTask.id).limit(1)` —— **按自增 id 升序取最早的一条**。
  若一个较早创建的 cleaning 任务与一个较晚创建的 running 任务同时存在，轨道返回的是 **cleaning 任务**。
- **影响**：轨道（页面最主要的"现在在干什么"）显示错误的文件名、阶段、attempt 与耗时；
  `renderRail` 还会把 `copy-artemis-id` 按钮指向错误任务的 Artemis ID。
- **代码位置**：`api/v2/video_publish.py:933-981`。
- **建议**：显式排序——running 优先，其次 cleaning；或在查询层拆成两次查询取 running。
  同时前端可对"返回的任务 status=running 但 stage 是清理态"做防御。

##### P1-6　任务列表没有独立的错误渲染分支：`/tasks` 500 与"真的没任务"完全同形
- **层**：前端 UI　**确认状态**：已确认（两种失败注入实测）　**发现**：ui lane (UI-03)
- **复现**：`/tasks` → 500：表格渲染成 `op-empty`「还没有发布任务。选择一个 MP4 并填写文案开始。」，
  顶部 notice **为空字符串**。`/config` → 500：`init()` 提前抛错，`GET /tasks` 根本没发出，
  表格永久停在模板占位符「加载中…」，无倒计时无错误文案。
- **预期**：§10.12 要求错误能区分"没有任务"和"加载失败"，并说明发生位置与可执行动作。
- **实际**：用户被告知"还没有任务"，而真实原因是接口 500。列表 DOM 只有"占位符 / 空 / 有数据"三态，
  `renderTasks` 没有 `error` 入参；`refreshList` 的 catch 只调 `notice(...)`，不把错误传给渲染层。
- **代码位置**：`static/js/video-publish.js` `renderTasks()` / `refreshList()` catch / `init()` catch。
- **建议**：`renderTasks` 增加 `error` 态（`common.css:141` 已有 `.op-error`，只差用起来）；
  `init()` 的 config 失败要把占位符从「加载中…」切到终态错误文案。

---

#### P2

##### P2-1　选到非法文件后，页面仍显示上一个文件的预览和文件名
- **层**：前端 UI / 交互　**确认状态**：已确认（主 Agent 独立复现）　**发现**：主 Agent (M-03)
- **复现**：`/tmp/vp-audit/s-main-verify.js` 步骤 B + `shots/01-B-stale-preview-after-reject.png`
  ```
  before: fileName="good.mp4" preview.hidden=false  submitDisabled=false
  after : fileName="good.mp4" preview.hidden=false  submitDisabled=true
          notice="只接受 MP4 视频"  summaryFile="—"   ← 右侧已清空，左侧还挂着旧视频
  ```
- **根因**：`pick()` 的校验失败分支只做 `state.file = null; renderForm(); return;`，
  没有 `URL.revokeObjectURL(state.url)`、没有隐藏 `#publish-video-preview`、没有清 `#publish-file-name`
  （`renderForm` 里 `if (file) …` 为假就不写）。同时 `reselect`/`clear` 按钮因 `state.file` 为 null 而消失 → **无法清理**。
- **影响**：页面自相矛盾，运营可能以为视频已选好而反复点提交（按钮却是灰的），或误认预览里的视频是刚选的。
- **建议**：把"拒绝文件"的收尾与 `clearSelection()` 收敛成同一条路径。

##### P2-2　上传失败一律提示「视频上传失败」，丢掉状态码与 MinIO 错误体；且无「重试上传」
- **层**：前端交互（+ 规格缺口）　**确认状态**：已确认（三种状态码实测）　**发现**：主 Agent (M-04) + interaction lane (UI-03)
- **复现**：`/tmp/vp-audit/s-cancel.js` 步骤 B，PUT 分别返回 403 / 413 / 500（body 为 MinIO XML `SignatureDoesNotMatch`）：
  ```
  PUT 403 -> notice: "视频上传失败"
  PUT 413 -> notice: "视频上传失败"
  PUT 500 -> notice: "视频上传失败"
  button:has-text("重试上传") 数量 = 0
  ```
- **预期**：§10.12 明确要求区分「网络中断、URL 过期、对象校验失败或服务端拒绝」，
  并给出「MinIO 对象校验失败：实际大小与所选文件不一致，请重新上传。」这类可执行文案。
- **实际**：`xhr.onload = () => … : reject(Error("视频上传失败"))` —— 不看 `xhr.status`，不读 `xhr.responseText`。
  运营无法区分"票据过期"与"对象被拒"。§10.4 要求的「重试上传」按钮不存在，
  `POST /tasks/{id}/upload-url`（`api/v2/video_publish.py:749`）**前端从未调用**——
  实际是靠再次 `POST /tasks` 走 `clientRequestId` 幂等重放换新 URL（功能可用，但与规格的接口路径不符；
  已核实 `create_upload_ticket` 重放时确实会 `_issue_upload_ticket` 换新 URL，见 submission.py:198-201）。
- **建议**：按 `xhr.status` 映射可执行文案；实现「重试上传」走 `/tasks/{id}/upload-url`。

##### P2-3　后端把机器错误码填进人读的 `message`，前端原样透出
- **层**：跨层（后端为主，前端为放大器）　**确认状态**：已确认（静态统计 + 前端解析路径）　**发现**：主 Agent (M-09)
- **证据**：`api/v2/video_publish.py`
  - `_error_detail(...)` 14 处字面量调用中 **13 处** `message` 与 `code` 完全相同
    （`TASK_NOT_FOUND`×6、`ARTEMIS_UNREACHABLE`×2、`OBJECT_STORE_UNAVAILABLE`×2、`INVALID_CURSOR`、
      `CSRF_HEADER_REQUIRED` 是唯一给了人话的）
  - `_action_conflict(...)` 4 处中 **2 处** 相同（`TASK_ACTION_NOT_ALLOWED`×2）
  - 所有 `except ValueError as exc: code = str(exc)` 分支同样是 code 当 message
    （create / confirm / retry / replace-upload / verify / refresh-upload-url）
- **前端**：`request()` 抛错时 `Error(detail.detail?.message || detail.detail?.code || detail.detail)`，
  **第一个命中的就是它** → 运营看到 `TASK_NOT_FOUND` / `ARTEMIS_UNREACHABLE` /
  `OBJECT_STORE_UNAVAILABLE` / `VIDEO_TOO_LARGE` / `CAPTION_TOO_LONG` / `IDEMPOTENCY_PAYLOAD_MISMATCH`。
  例：`loadDevices` 失败显示为「设备列表暂不可用，使用默认设备（ARTEMIS_UNREACHABLE）」。
- **与规格冲突**：§10.12「错误必须说明发生位置和可执行动作」「禁止只显示 操作失败、未知错误 或原始堆栈」。
- **责任边界**：后端 `message` 字段本身不可读是主因；前端没有 code→中文兜底映射是放大器。
- **建议**：后端补齐可执行中文 message 并保留 code 作机器标识；前端在 `message` 缺失或等于 code 时按 code 兜底映射。

##### P2-4　表格单元格无换行策略，长文案/长文件名撑爆布局
- **层**：前端 UI　**确认状态**：已确认（3 档实测，主 Agent 复现）　**发现**：ui lane (UI-02)
- **证据**（`captionPreview = "A"×300`）：
  ```
  @1440: .table-scroll scrollWidth=3975 / clientWidth=1114   （文档本身不溢出，溢出被 .table-scroll 吞成横滚）
  @1024: .table-scroll scrollWidth=3975 / clientWidth=698
  @390 : .table-scroll scrollWidth=2528 / clientWidth=340
  captionTd getComputedStyle().overflowWrap === "normal"
  ```
- **根因**：`.op-table td` 在桌面态与 `@media(max-width:720px)` 卡片态都**未声明** `overflow-wrap`/`word-break`；
  而同文件的 `.artemis-cell`、`.publish-summary dd`、`#publish-caption` 三处都写了 —— **唯独表格 td 漏了**。
  桌面端同时缺 `table-layout:fixed` / 列 `max-width`。
- **建议**：`.op-table td` 补 `overflow-wrap:anywhere`（与 `.artemis-cell` 对齐）；桌面端给"文案"列设
  `max-width` + 摘要截断（`captionPreview` 语义上本就是摘要）。

##### P2-5　移动端「操作」列缺 `data-label`，产生 72px 空白缩进
- **层**：前端 UI　**确认状态**：已确认（主 Agent + ui lane 独立复现）　**发现**：主 Agent (M-06) + ui lane (UI-01)
- **证据**（390px）：
  ```
  {"i":7,"label":"时间","before":"\"时间\"","w":"72px"}
  {"i":8,"label":undefined,"before":"\"\"","w":"72px"}   ← 操作列
  ```
- **根因**：`data-label` 在 `renderTasks` 的 `forEach` 里按下标赋值，只覆盖 0..7；
  「操作」td 是循环外单独 append 的，从未赋值。CSS `.op-table td:before{content:attr(data-label);width:72px}`
  在属性缺失时 content 解析为空串，但 `width:72px` 仍生效。
- **建议**：`actions.dataset.label = "操作"`；并给 `.op-table td:before` 加 `:empty{display:none}` 兜底。

##### P2-6　「目标设备」label 与 select 生命周期不同步：63px 跳动 + 503 时永久悬空
- **层**：前端 UI　**确认状态**：已确认　**发现**：ui lane (UI-07)
- **证据**：`/devices` 延迟 2.5s → label 顶部 y 从 **859 跳到 922（位移 63px）**；
  `/devices` 503 → `select.hidden` **永久保持**，表单区留一个点不动的「目标设备」标题。
- **根因**：label 在模板里无条件渲染，select 由 JS 在成功分支才 `hidden=false`，两者没有共同的显隐源；
  select 也没有 `min-height` 预留位。
- **建议**：包一层 `#publish-device-field` 统一翻转；失败分支把 label 改成
  「目标设备（列表暂不可用，使用默认）」；插入前后预留高度。

##### P2-7　四个状态文本完全没有页面级样式，且 `#publish-write-status` 出没引起 26px 布局位移
- **层**：前端 UI　**确认状态**：已确认（主 Agent 复测仍在）　**发现**：ui lane (UI-08)
- **证据**（主 Agent 在当前 HEAD 复测）：
  ```
  #publish-write-status   {fs:"16px", color:"rgb(27,24,20)", h:0→26}
  #publish-rail-meta     {fs:"16px", color:"rgb(27,24,20)", h:26}
  #publish-last-refreshed{fs:"16px", color:"rgb(27,24,20)", h:24}
  #publish-refresh-status{fs:"16px", color:"rgb(27,24,20)", h:0}
  #publish-notice        {fs:"16px", color:"rgb(184,57,14)", h:24}   ← 有 var(--accent)
  ```
- **根因**：`video-publish.css` 对这四个 id **零选择器**（`#publish-notice`、`#publish-preview-metadata` 都有规则）。
  `#publish-write-status` 是唯一的"为什么不能提交"说明，却用 16px 正文黑渲染，
  既不是 `--danger`/`--warn` 也无描边，与上方 notice 风格割裂。
- **建议**：四个节点补一条共享规则（`var(--mono)/12px/var(--muted)`）；
  write-status 用 `--danger`/`--warn` 区分阻塞等级并加 `min-height` 消除按钮位移。

##### P2-8　设备 offline/unknown 不参与任何视觉分支，原始 adb 英文直出
- **层**：前端 UI　**确认状态**：已确认　**发现**：ui lane (UI-09)
- **证据**：`configOverride={device:{status:"unknown",message:"adb: 未检测到设备"}}` →
  `#publish-device-status` 显示「设备 … · adb: 未检测到设备」；`#publish-write-status` **为空**；
  提交按钮 `disabled:false` —— 页面任何位置都没告诉用户"设备当前不可用"。离线态与在线态计算样式完全相同。
- **根因**：`config` 已下发 `device.status`，前端只把 `message` 当字符串拼进去，`status` 整个被丢弃；
  `renderForm` 的 `writeReady` 只看 `canWrite` 与 `worker.status`。
- **与规格冲突**：§10.12「设备不可用：发布设备离线。任务会保留在队列中，设备恢复后继续。」
- **建议**：`device.status → 中文状态词` 映射（online/offline/unknown/locked/busy）；
  离线/unknown 时在表单区补 §10.12 整句，且不能只靠颜色区分。

##### P2-9　移动端 drawer 右侧恒定漏出 38px，`width:100vw` 被 UA `max-width` 夹回
- **层**：前端 UI　**确认状态**：已确认（主 Agent 在当前 HEAD 复测仍在）　**发现**：ui lane (UI-05)
- **证据**（主 Agent 复测）：
  ```
  drawer @720: w=560, maxWidth="calc(100% - 38px)"
  drawer @480: w=442, maxWidth="calc(100% - 38px)"
  drawer @390: w=352, maxWidth="calc(100% - 38px)"
  drawer @360: w=322, maxWidth="calc(100% - 38px)"
  ```
- **根因**：`#publish-task-drawer` 显式重置了 `max-height:none`，**漏掉 `max-width`**，
  于是 Chromium UA 的 `dialog{max-width:calc(100% - 6px - 2em)}` 生效（2em=32px，6+32=38px）。
  §10.3.4 要求 <720px 全屏 dialog。
- **建议**：补 `max-width:100vw`；并把"全屏"断点从 390px 提到 720px。

##### P2-10　「加载更多」的第 2 页会被下一次定时刷新静默丢弃
- **层**：前端交互　**确认状态**：已确认　**发现**：interaction lane (UI-08)
- **预期**：加载更多后继续轮询不应把已加载的分页丢掉。
- **实际**：`refreshList()`（append=false）在定时刷新里被调用时直接 `state.listChannel.items = incoming`，
  覆盖掉累积的第 2 页，且 `nextCursor` 回到第 1 页的游标 → 用户看到的行数突然回退。
- **建议**：定时刷新只更新第 1 页对应的那一段，或在有已加载分页时合并更新。

##### P2-11　`pollState` 未做 owner scope，非 admin 可获知他人是否有任务在跑
- **层**：后端 API　**确认状态**：已确认（探针实证）　**发现**：backend lane (BE-04)
- **根因**：`_poll_state(session)` 不接受 owner 过滤，而 `current` / `list_tasks` 里的 `task` 本身是做了
  `_owner_clause` 的 → 同一个响应里，任务按 owner 过滤了，运营指标没有。
- **影响**：低敏信息泄露（他人是否有任务在跑/在清理/在排队），与 §11 的可见性分级不一致。
- **建议**：`_poll_state` 接 owner 过滤；或明确把 `pollState` 声明为全局指标并在文档写明。

##### P2-12　`queuePosition` 把「Worker 不会领取的任务」也计入排位
- **层**：后端 API　**确认状态**：已确认（探针实证）　**发现**：backend lane (BE-06)
- **根因**：`_queue_positions` 按 `(queued_at, id)` 全局排名，不排除尚未具备领取条件的任务。
- **影响**：列表里「排队 · 前面 N 个任务」里的 N 偏大，运营对等待时长的预期失真。
- **建议**：排名查询加上与 `claim_one` 相同的可领取条件。

##### P2-13　Pydantic 校验错误绕过统一错误信封，且 `caption` NUL 校验泄露非契约错误码
- **层**：后端 API　**确认状态**：已确认（探针实证）　**发现**：backend lane (BE-07)
- **根因**：`CreateIn` / `ActionIn` 的字段级校验由 FastAPI/Pydantic 直接返回 422 `{"detail":[{...}]}`
  （数组形态），与其它端点统一的 `{"detail":{code,message,retryable,requestId}}` 信封不同；
  前端 `detail.detail?.message` 对数组取不到值 → 落到 `|| "请求失败"`。
  另：`normalize_caption` 抛的 `"caption contains NUL"` 会作为 422 的 `code` 透出，不在设计文档的错误码集合内。
- **影响**：契约不一致（AGENTS.md §4 要求 HTTP 状态是成功/失败信号、错误结构统一）；前端提示退化为"请求失败"。
- **建议**：加 `RequestValidationError` 处理器统一信封；把 NUL 校验并入契约错误码集合。

##### P2-14　纯空白 / 只有换行的文案可通过前端校验，提交后才被服务端 422 拒绝
- **层**：前端交互　**确认状态**：已确认　**发现**：interaction lane (UI-06)
- **根因**：`valid()` 只查 `codePointLength(value) > 0`，不查 `value.trim()`；
  后端 `create_upload_ticket` 判的是 `if not caption.strip()`。
- **影响**：用户填了一整屏空格，提交后才失败，且失败发生在 `POST /tasks`（已消耗一次 clientRequestId）。
- **建议**：前端 `valid()` 加 `value.trim().length > 0`；文案计数对纯空白给出即时反馈。

##### P2-15　轨道与 drawer 把内部英文枚举直出给用户
- **层**：前端 UI　**确认状态**：已确认（主 Agent 复现 drawer 标题）　**发现**：主 Agent (M-08) + ui lane (UI-10)
- **证据**：drawer heading = `2026-10-06-clip-01.mp4 · failed`（列表同一任务用的是 `statusLabel`「失败」）。
- **根因**：`renderDetail` 的 `heading.textContent = \`${detail.filename} · ${detail.status}\`` 用了 `status`；
  轨道 `renderRail` 的 `${task.operationalStage || task.stage}`、meta 里的 `${task.stage}`、
  attempt 行与清理行同理。
- **建议**：统一走 `statusLabel` / `stageLabel` / `kindLabel`。

##### P2-16　确认弹窗关闭后焦点掉到 `<body>`（违反 §10.13）
- **层**：前端 UI（可访问性）　**确认状态**：已确认（主 Agent + interaction lane 独立复现）　**发现**：主 Agent (M-07) + interaction lane (UI-05)
- **证据**：点「重试」→ dialog 打开 → 点「返回」→ `document.activeElement = {tag:"BODY"}`
- **根因**：`confirmTaskAction` 在 `showModal()` **之前**就把 `trigger.disabled = true`；
  关闭时执行 `trigger?.focus?.()`，而对 disabled 元素 `focus()` 是空操作，
  且原生 dialog 恢复焦点时"showModal 前的焦点元素"已不可聚焦 → 焦点落到 body。
- **建议**：先恢复焦点再置灰，或用 `requestAnimationFrame` 延迟置灰，或 finally 里按当前 disabled 状态决定是否 focus。

##### P2-17　drawer 内容排版与同页摘要面板不一致（`h2` 32px 纯黑 sans，`dl` 退化为 block）
- **层**：前端 UI　**确认状态**：已确认（主 Agent 在当前 HEAD 复测仍在）　**发现**：ui lane (UI-12)
- **证据**（主 Agent 复测）：`#publish-drawer-content h2 {fontSize:"32px", color:"rgb(0,0,0)"}`；
  `#publish-drawer-content dl {display:"block", ddMarginLeft:"0px"}`
- **根因**：`9f325ec` 把弹窗表面样式上收到 `.op-dialog`，但 `#publish-task-drawer` **不是** `.op-dialog`，
  `video-publish.css` 也没有 `#publish-drawer-content h2/dl` 的规则 → 吃 UA/正文默认。
- **建议**：给 drawer 的 h2/dl 补与 `.publish-summary` 一致的排版令牌。

##### P2-18　Artemis ID 单元格里的 `<code>` 命中 Bootstrap 默认：10.5px + 粉红色
- **层**：前端 UI　**确认状态**：已确认（主 Agent 在当前 HEAD 复测仍在）　**发现**：ui lane (UI-13)
- **证据**（主 Agent 复测）：`.artemis-cell code {fontSize:"10.5px", color:"rgb(214,51,132)"}`
  而 `.artemis-cell` 自身是 `{fontSize:"12px", color:"rgb(27,24,20)"}` → 同一格里字号与颜色都打架。
- **建议**：`.artemis-cell code{font:inherit;color:inherit}`，或显式指定 `--mono`/12px/`--ink`。

##### P2-19　筛选 tab 首屏没有选中态
- **层**：前端 UI　**确认状态**：已确认（三方独立复现）　**发现**：主 Agent (M-05) + ui lane (UI-06) + interaction lane (UI-07)
- **证据**：首屏 7 个 tab 全部 `active:false`，但列表展示的正是「全部」；13 次初始加载全部无匹配。
- **根因**：模板 `.filter-tabs` 按钮初始无 `is-active`；JS 只在 click 回调里 toggle，无初始化同步。
- **建议**：初始化时按 `state.filter` 统一同步一次；顺带补 `aria-pressed`（见 P3-6）。

##### P2-20　轨道 `done` 不按 §10.5 收起
- **层**：前端 UI　**确认状态**：**待验证**（规格歧义）　**发现**：ui lane (UI-11)
- §10.5 表格写 `done → 轨道收起，任务进入历史记录`。当前实现下 `/tasks/current` 不返回 done 任务，
  轨道会显示「当前无运行任务」——**这可能就是"收起"的实现**，但没有任何视觉上的"收起"动作。
  需产品确认「收起」的确切含义后才能定性。缺少的验证条件：§10.5 对"收起"的可观测定义。

##### P2-21　`operationalStageStartedAt` 在清理退避期返回**未来时间戳**
- **层**：后端 API（表现落在前端）　**确认状态**：已确认（探针实证）　**发现**：backend lane (BE-05)
- **降级说明**：backend lane 记为 P2，主 Agent 下调为 **P3** —— 数据本身是设计意图
  （用 `device_cleanup_next_attempt_at` 表示"下次重试"），但被前端当"阶段开始时间"用。
- **实际影响**：`renderRail` 算 `max(0, Date.now() - startedAt)` → 清理退避期恒显示「已耗时 0 秒」。
- **建议**：`_snapshot` 区分"阶段开始"与"下次重试"两个字段，前端分别展示。

---

#### P3（摘要）

| # | 问题 | 层 | 来源 |
| --- | --- | --- | --- |
| P3-1 | 前端硬编码 `video/mp4` + `.mp4`，不用 `config.acceptedContentTypes` / `acceptedExtensions`，违反 §9.1「限制值由服务端配置生成；前端不得复制另一套常量」 | 前端交互 | interaction §4.2 |
| P3-2 | `config.suggestedPollSeconds` / `uploadUrlTtlSeconds` / `totalApprox` / `upload.method` / `expiresAt` 后端返回但前端未使用 | 跨层 | interaction §4.2 |
| P3-3 | §22.2 要求的 `#active-task-id` 元素在模板与 JS 中都不存在（自动化测试若依赖该选择器会失败） | 前端 | interaction §4.4 |
| P3-4 | §22.7 要求 drawer header 有「复制任务 ID」按钮，当前只有关闭按钮 | 前端 | interaction §4.4 |
| P3-5 | §10.4 第 7 步「confirm 成功后…聚焦成功提示」未实现（实测 `activeElement` 为 `BODY`） | 前端 | interaction §4.4 |
| P3-6 | 筛选 tab 只用背景色表示"当前"，无 `aria-pressed` / `aria-current`（违反 §10.13「不能只靠颜色」） | 前端 | ui lane (UI-16) |
| P3-7 | drawer 与确认 dialog 无可访问名称（drawer 无 `aria-label`/`aria-labelledby`） | 前端 | ui lane (UI-15) |
| P3-8 | 轨道容器有 `aria-label` 但 `<div>` 无 `role`，节点也无 step 角色 | 前端 | ui lane (UI-17) |
| P3-9 | 隐藏的 `<input type="file">` 聚焦时无任何可见焦点指示 | 前端 | ui lane (UI-14) |
| P3-10 | `retry_task` 在"对象已不存在"时先 `session.commit()` 写副作用、再抛 409 | 后端 | backend lane (BE-08) |
| P3-11 | `create` 返回的 `rowVersion` 与 §21.3 示例不一致（示例 1，实际 2），重放/刷新票据会继续推高版本 | 后端 | backend lane (BE-09) |
| P3-12 | 「排队」筛选（`status=pending`）会带出 `stage=awaiting_upload` 的任务，列表里与真排队行同形 | 跨层 | 主 Agent (M-10) |
| P3-13 | §10.12「队列为空：当前没有等待中的视频。」未实现（空列表统一显示"还没有发布任务"） | 前端 | interaction §4.4 |
| P3-14 | §22.8 要求提交确认按钮文案为「返回修改」/「确认并上传」，实际为「返回」/「确认上传并加入队列」 | 前端 | interaction §4.4 |
| P3-15 | `config.device.status` 字段被前端完全丢弃（与 P2-8 同源，此处只记字段层面） | 跨层 | interaction §4.2 |
| P3-16 | `runTaskAction` 的 `retry_cleanup` 分支里 `task.cleanup || {}` fallback 是死代码（`cleanupRetryableResources` 恒为 truthy 数组） | 前端 | interaction §4.3 |

---

#### 待验证（不计入已确认 Bug）

| # | 现象 | 为什么待验证 |
| --- | --- | --- |
| V-1 | 确认弹窗内双击提交会重复 `POST /tasks` 与重复 PUT（interaction lane 复现为 3 次 PUT） | 复现用的是**同一 tick 内的 `b.click(); b.click()`**。真实指针双击的第 2 次点击会落在已打开的 modal 之上；键盘 Enter 重复也会被焦点移入 dialog 截走。**当前没有找到普通用户输入可达的路径**，故降为待验证。代码层的重入缺口是真的（`state.creating=true` 在 `await confirmPublish()` 之后才置位；`showConfirmationDialog` 无重入短路），但需要真实输入序列或慢机复现才能定性。 |
| V-2 | `POST /verify` 在"上一条 attempt 仍为 unknown"时必然 409，错误码误导（backend lane BE-02 记 P1） | 探针实证了 409，但**这是否是设计意图**存疑：§7.3/§10.10 描述了 verify 的正常流转，未明文禁止对 unknown 结果再次 verify。需产品确认该状态是否**应当**可重入核验。 |
| V-3 | 真实端到端 HTTP 往返 | 服务端 `auth_mode=enforce` 且无可用凭据，所有 API 行为来自 mock + 代码/测试推断。**需要一把测试环境只读/只写测试账号**才能补齐。 |
| V-4 | Firefox / Safari 的 `<dialog>` UA 默认值 | 本环境仅 Chromium 1243。P2-9（38px）在其它引擎的 `2em` 取值可能不同。 |
| V-5 | P2-20 轨道 `done` 收起 | 规格对"收起"缺少可观测定义。 |

---

### 3. 规格与实现差异（记录，不据此判 Bug）

1. **测试命令不可用**：文档与 AGENTS.md 给的 `bash scripts/test_isolated.sh publishing`
   **恒定收集 0 个用例**——`scripts/test.sh` 的 `run_domain()` 展开为 `-m "domain_publishing and not slow"`，
   而 `pyproject.toml:51-78` 的 markers 列表里没有 `domain_publishing`，
   `tests/publishing/*.py` 也没打任何 domain marker。可用等价命令：
   `bash scripts/test_isolated.sh fast tests/publishing -p no:randomly`（290 用例全绿）。
   **这条建议单独修**：否则「跑一遍发布测试」永远是绿的假象。
2. **`tests/publishing` 当前全绿**（19 文件 / 290 用例，`EXIT=0`）——
   也就是说 P1-1 到 P1-6、P2-1 到 P2-21 **全部不在既有测试覆盖范围内**，需要补回归。
3. `config.acceptedContentTypes` / `acceptedExtensions` 已下发但前端另写一套常量（见 P3-1）。
4. `suggestedPollSeconds` §9.5 明确要求返回，前端不用（信息性差异，可接受）。
5. 设计文档引用的 §22.x 段落（`#active-task-id`、drawer 复制任务 ID、确认按钮文案）在实现中缺失（见 P3-3/4/14）。

---

### 4. 建议修复顺序与回归验证点

| 批次 | 内容 | 为什么这个顺序 | 回归验证点 |
| --- | --- | --- | --- |
| **0** | 修 `scripts/test_isolated.sh publishing` 的 marker（§3.1） | 否则后面所有回归都跑不起来 | `bash scripts/test_isolated.sh publishing` 收集到 290 个用例且 `EXIT=0` |
| **1** | P1-1（retry 500）、P1-2（304 串筛选）、P1-3（drawer 打不开）、P1-4（续传锁死） | 这四条是"用户点了没反应/点了报错/看到错数据"，直接毁信任，且都是单点可测 | 新增：①设备清理 failed 的任务点重试返回 409 而非 500；②筛选来回切换 3 次表格始终正确；③同一任务 drawer 开关 3 次都能打开；④点继续上传后能选任意新文件提交新任务 |
| **2** | P1-5（`/tasks/current` 排序）、P1-6（列表错误态）、P2-3（错误码 message） | 修"显示错的东西"和"看不懂的错" | ①running + cleaning 并存时 `/current` 返回 running；②`/tasks` 500 时表格显示错误态而非空态；③所有错误响应 `detail.message` 为中文可执行文案 |
| **3** | P2-1、P2-2、P2-14、P2-10（创建链路的输入/失败/分页） | 都在同一条用户主路径上，改动互相影响小 | 非法文件后预览/文件名/按钮三者一致；PUT 403/413/500 三种文案不同且可执行；纯空白文案前端即拦截；加载更多后轮询不丢页 |
| **4** | P2-4、P2-5、P2-9、P2-17、P2-18、P2-6、P2-7（布局与排版） | 纯样式，可批量；P2-7 的 26px 位移与 P2-6 的 63px 跳动建议一起做（都是预留位） | 9 档视口回归：`documentElement.scrollWidth == clientWidth`；表格 9 列 `data-label` 齐全；drawer `max-width` 在 720/480/390/360 均为 100vw；`write-status` 出没不引起位移 |
| **5** | P2-8、P2-11、P2-12、P2-13、P2-18 系列（契约与语义收口） | 需要先定产品语义（`pollState` 是否全局、排队位算法、校验错误信封） | 每条对应 §3 的契约表 |
| **6** | P3 全量 + V-1/V-2 定性 | 收口 | — |

**跨批次提醒**：P1-2 与 P1-3 同根因但修复点不同，改 P1-2 时**不要**顺手动 P1-3 的 `openDetail`——
它们共用 `refreshDetail` 的 304 分支，容易互相回归。

---

### 5. 产物索引

| 文件 | 内容 |
| --- | --- |
| `/tmp/vp-audit/report-ui.md` | UI lane 原始报告（18 条） |
| `/tmp/vp-audit/report-interaction.md` | 交互 lane 原始报告（8 条 + 字段契约盘点） |
| `/tmp/vp-audit/report-backend.md` | 后端 lane 原始报告（9 条） |
| `/tmp/vp-audit/MAIN-AGENT-VERIFIED.md` | 主 Agent 独立核验记录 M-01..M-10 |
| `/tmp/vp-audit/harness.js` / `mocks.js` | Playwright harness（真实模板 + 真实 static，只 mock `/v2/video-publish/*`） |
| `/tmp/vp-audit/s-*.js` | 全部可复现场景脚本 |
| `/tmp/vp-audit/shots/` | 截图 |
| `/tmp/vp-audit/pytest*.log` | 隔离库测试输出 |
