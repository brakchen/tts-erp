# 登录交互优化技术方案

> 依据：`prd/04-error-feedback.md`（2026-11-13，登录交互已由 chen 确认；第 6 节已拍板为方案 A）  
> 日期：2026-11-13  
> 状态：P0 可开发。第 6 节采用方案 A：写操作与读操作一样，超时 / 断网 / 502 / 504 / 500 / 503 都只显示映射文案，**禁止**出现「操作可能已生效」及任何为此预留的字段或分支。整页 403 文案仍待 chen 明示，先不改。  
> 范围：工作台前端 + 两处必要的登录/会话接口调整。不改插件，不改 `prd/prototypes/`，不改限流阈值和会话策略，不改登录认证业务（停用账号仍返回 401）。

本文只写实现约定。产品文案与验收以 04 PRD 第 4、5、7、9 节为准。

## 1. 架构师核对结论

| 待确认项 | 结论 |
| --- | --- |
| 已有有效会话再打开登录页 | **现网不会自动进入工作台。** `GET /v2/auth/login` 是公开页，不检查 cookie；React 登录页挂载时也不请求 `/me`，已登录用户仍会看到登录表单。P0 补齐：后端在会话有效时 302 到 `next`（默认控制台）。库不可用时不跳转，直接渲染登录页。 |
| 停用账号是否有独立登录状态 | **没有。** `accounts.service.authenticate` 在 `status != "active"` 时与密码错误一样返回 `None`，接口统一 **401**「用户名或密码错误」（防用户枚举）。P0 **不**改为 403，也不使用「当前账号无法登录」文案。PRD 验收第 8 条已改为核对这一行为：停用账号与密码错误一样返回 401「用户名或密码错误」，不得单独暴露账号是否存在或是否停用。会话阶段禁用用户会 fail closed，表现为未登录。 |
| 业务 `code` 是否作为映射首选 | **是。** 客户端已解析顶层、符合 `^[A-Z][A-Z0-9_]{0,79}$` 的 `code`。共享映射表中有该 `code` 时，优先于 HTTP 默认文案和 `detail` 原文。另收一份「已知非规范 code」（如运行配置的 `runtime_*`，目前在 `detail.code`）一并纳入同一张表。本次不批量给后端补 `code`。 |
| 30 秒超时 | **P0 登录已包裹** `withinRequestLifetime`（`REQUEST_DEADLINE_MS = 30_000`），只需改超时错误类型和文案，不再说「结果仍需核对」。**P1 在 `requestJson` 内统一加 30 秒超时**，覆盖尚未包裹的店铺 / 枚举 / 运行配置 / 定时任务 / 拦截配置等；删除 `frontend/src/pages/users/requestLifetime.ts` 重复实现。对象存储直传和 ZIP 下载不套这 30 秒。 |
| 遗留 `_LOGIN_HTML` | **现网不可达。** `login` 已在硬编码的 `MIGRATED_PAGE_IDS` 中，`login_page` 只走 React renderer。本次不改、不测这块 HTML。删除放到后续清理，不进本次提交。 |
| 非登录写操作在超时 / 断网时的提示 | **方案 A（已拍板）。** 写操作（含非幂等写）与读操作一样，只显示第 5.2 节 / 本方案 3.1 文案：直接报错，可重试，不锁按钮，不加核对流程，也不附加「操作可能已生效，请刷新后确认再重试」。500 / 503 同样只报确定失败。不要预留 `maybeSettled` 或同类分支。第 5 节清单仅供 P1 拆除门闩和补齐超时时对照。 |

## 2. 原则

- 直接报错，可重试的按钮保持可点；任何页面都不再插入「查询核对 / 允许手动重试」门闩。
- 默认不向用户展示 HTTP 状态码或技术性 `detail`（异常类型名、内部英文、权限码、堆栈、request id）。
- 所有页面的失败都按确定结果处理。超时、断网、502 / 504 / 500 / 503 一律只显示映射文案，可重试，不锁按钮；**禁止**附加「操作可能已生效，请刷新后确认再重试」，也不为此增加字段或分支。登录页仍用第 3.2 节专用句。
- 全局分类在 P0 就改：`commandFailure` 不再把 500 / 502 / 503 / 504 / 超时 / 断网标成 `uncertain`。这会让其他页上依赖 `uncertain` 的门闩提前失效（按钮可点、不再出现核对区），是预期效果；P1 再拆除残留 UI 并统一展示。
- 整页 403 文案仍待 chen 明示：**不得**改现网「无权访问该页面」/「返回登录页」。

## 3. P0 实施

P0 交付：登录主流程 + 它所依赖的全局分类和文案映射。验收以 PRD 第 9 节 1–11 为主，并覆盖第 12、13 条中与登录分类直接相关的部分。

### 3.1 共享错误映射

新增 `frontend/src/shared/api/error-copy.ts`（名称可微调，但必须是全站唯一入口），由 `commandFailure` 调用，页面不再各自拼 HTTP 文案。

**分类与文案（工作台默认；登录页在 3.2 覆盖）：**

| 条件 | 文案 | `retryAfter` / 倒计时 | 备注 |
| --- | --- | --- | --- |
| 400 | 请求未通过检查，请核对后重试 | 否 | |
| 401（非登录页） | 不展示，由现网 `setUnauthorizedHandler` 跳转登录 | 否 | 登录页、`message`、`oauth-onboard` 不跳转，保持 `main.tsx` 现网行为 |
| 403（接口 / 页内操作） | 没有权限执行此操作 | 否 | 不跳整页 403 |
| 404 | 要找的内容不存在，请刷新后再试 | 否 | |
| 409 | 数据已变化，请刷新后确认再提交 | 否 | |
| 422 | 请检查标红的字段；字段错误写在字段下 | 否 | 沿用现有 `safeTypes`；特定店铺凭证英文 `detail` 继续映射到字段，不原文展示 |
| 429 且有正数 `Retry-After` | 操作太频繁，请在 N 秒后重试 | 是 | 倒计时结束不自动重发 |
| 429 且无 `Retry-After` | 操作太频繁，请稍后再试 | 否，按钮可用 | |
| 500 | 服务出错了，请稍后重试 | 仅当意外带了正数 `Retry-After` | |
| 503 | 服务暂不可用，请稍后重试 | 同上 | |
| 502 / 504 | 服务暂时无响应，请稍后重试 | 否 | 与读操作相同，不加「可能已生效」 |
| 其他未列 5xx / 非标准状态 | 按 500 文案 | 同 500 | 一律确定失败，不再 `uncertain` |
| 网络断开（无 HTTP 状态） | 网络已断开，请检查网络后重试 | 否 | |
| 30 秒前端超时 | 等待超时，请稍后重试 | 否 | 登录页用「请求超时，请稍后重试」 |

**`code` 与 `detail` 优先级（对应 PRD 5.3）：**

1. 共享表中的业务 `code`（含从 `detail.code` 读到的已知 `runtime_*`）优先。
2. 否则，若 `detail` 是单行中文字符串、不超过 80 字、且不是技术内部信息，可用它替换或补在默认文案后。技术性内容黑名单按 PRD 5.3（如 `auth store unavailable: OperationalError`、`too many login attempts`、`requires page:…`）。
3. 否则用上表默认文案。
4. 用户主文案去掉 `（HTTP {status}）` 后缀。

P0 就把现网已有的页面私有映射收进同一张表（P1 再删各页重复逻辑），至少包括：

- `ASSET_IN_USE`、`ASSET_NOT_READY`、`PORTRAIT_NOT_READY`、`PACK_REQUIRES_READY_VIDEOS`
- `FX_RATE_UNAVAILABLE`
- `SHOP_REGION_REQUIRED`
- `UPLOAD_NOT_FOUND`、`SIZE_MISMATCH`（若现网仍在用）
- `runtime_schema_invalid`、`runtime_business_invalid`、`runtime_secret_unavailable`、`runtime_source_rollout_invalid`、`runtime_number_invalid`

文案沿用各页现有中文，不重新发明。店铺 App 凭证那几条英文 `detail` 仍只作为字段映射键，不进入横幅原文。

**`uncertain`：** P0 起对所有可识别失败都为 `false`。不要新增 `maybeSettled` 或任何「可能已生效」相关字段、分支、文案常量。

**超时与断网的错误类型：** 在 `request-lifetime.ts`（及 P1 的 `requestJson`）抛出可识别的超时错误，不要再用普通 `Error('请求等待已结束，结果仍需核对')`。网络失败（`fetch` 拒绝、无 status）同样要能与超时区分，以便映射到不同文案。

### 3.2 登录页

改 `frontend/src/pages/login/index.tsx` 及配套 `contracts.ts`、单测、浏览器测。

- 删除 recovery 整块：状态、`查询当前会话`、`使用已查询的当前会话继续`、`我已了解，允许手动重新登录`、查询结果区，以及「结果未确认」类文案。失败后不再把按钮锁到必须先查询。
- 请求中：按钮「登录中…」，不可点。
- 失败：按钮上方内联 Alert（危险态，从现网按钮下方上移）。按钮在非请求中、非 429 冷却时立即恢复可点。
- 保留已填用户名；密码保留策略不改。
- 登录专用文案：
  - 401：「用户名或密码错误」（现网 `detail` 已是这句，可直接用）
  - 429：有 `Retry-After` 时「登录过于频繁，请 N 秒后再试」，展示剩余秒数；倒计时期间禁用；到 0 恢复可点、**不自动重发**；改输入不能缩短等待。无 `Retry-After` 时「登录过于频繁，请稍后再试」，按钮可用
  - 500 / 502 / 503 / 504：「登录服务暂不可用，请稍后重试」
  - 断网：「网络已断开，请检查网络后重试」
  - 超时：「请求超时，请稍后重试」
  - 400 / 422：字段错误在用户名 / 密码下方
- 登录失败不加「可能已生效」，也不查询 `/me`。
- 429 冷却只约束本次登录提交。解析继续用现网 `ApiError.retryAfter`。

**已登录再打开登录页（补齐）：**

在 `tts_erp_v2/api/v2/auth.py` 的 `login_page` 中，若请求带会话 cookie 且 `authenticate_session_token` 得到有效用户，返回 **302**，`Location` 为已校验的 `next_url`（与现网 `loginNextPath` 规则一致，默认控制台）。库异常或会话无效时仍渲染登录页，不跳转、不报错到这个公开页。前端不必再挂载时打 `/me`（避免闪一下登录页）。用户若要换账号，先在工作台退出。

**会话过期回跳：** 保持 `main.tsx` 现网行为：非全屏页 401 → `/v2/auth/login?next=当前路径与查询串`；登录成功 `leaveLogin(next)` 回原页。

### 3.3 后端其它

- `GET /v2/auth/me`：无 cookie 或会话无效，仍返回 **200** `{"authenticated": false}`（现网契约，e2e / 单测依赖它）。**仅当访问用户库抛异常时改为 503**，`detail` 不要把异常类型名给前端展示用；前端登录跳转和壳层 `SessionActions` 已把 `/me` 失败当成「会话信息暂不可用」，不会误当成未登录。更新 `tests/api/test_user_auth.py` 等对应用例。
- 不改 `POST /login` 的 401 / 429 / 503 业务语义，不改限流。503 仍在 `set_cookie` 之前返回。
- 不改 `_LOGIN_HTML`。
- 不改整页 403 的标题、按钮、`action="login"`。

### 3.4 壳层小文案

`SessionActions` 退出失败现文案是「退出结果未确认，请重试或重新登录」。P0 若动到共享映射，改为走通用文案（例如服务暂不可用 / 网络已断开），不要再出现「结果未确认」。退出是幂等的，不加「可能已生效」。

### 3.5 P0 测试（开发自测 + 测试回归）

重写 / 删除 `frontend/src/pages/login/index.test.tsx` 里整组「uncertainty and recovery」用例，改为覆盖：503 / 502 / 504 / 500 文案与按钮可点、401 保留用户名、429 倒计时与不自动重发、断网、30 秒超时、无核对 UI、字段错误位置、Alert 在按钮上方。

更新 `tests/browser/test_frontend_remaining_auth_native.py` 中依赖「查询当前会话 / 结果未确认」的路径。

新增或补强 API 测试：已登录 `GET /login` 302；库不可用时 `GET /login` 仍 200 渲染登录页；`GET /me` 在库异常时 503，无 cookie 时仍 200 `authenticated: false`。

共享映射单测：`commandFailure` / `error-copy` 覆盖状态码、code 优先、技术性 detail 被丢弃、中文短 detail 可用、不再带 `（HTTP …）`、`uncertain === false`。

验收第 8 条：停用账号走现网 401 路径，文案与密码错误相同；补或核对对应 API / 前端用例，确认不会出现「当前账号无法登录」。

## 4. P1 实施（登录通过后再做；整页 403 文案除外）

- 拆除各页残留的核对门闩与文案：店铺注册、枚举映射、运行配置、定时任务、采购、拦截配置、用户管理、视频发布、资产管理、换装视频，以及其它仍判断 `failure.uncertain` 或渲染「查询核对」的页面。失败后按钮可点（429 冷却除外），不再要求先查询再重试。
- 展示对齐 PRD 第 7 节：表单错误 = 提交按钮上方内联 Alert；列表加载失败 = 页内 Alert +「重试」；行内 / 弹窗外轻操作 = Toast（优先用 HeroUI 3.2 已有能力，没有再用 Alert 做右上角轻量浮动，**不新增依赖**）；422 仍在字段下；保留现网渲染边界「页面加载失败」。
- 在 `requestJson` 统一 30 秒超时；各页重复的 `withinRequestLifetime` 可逐步去掉。**豁免：** 浏览器直传对象存储（视频发布 / 素材上传的 PUT 到上传 URL）、打包下载 ZIP、其它非工作台 API 的长传输。这些失败仍要能映射到人话，但时限不套 30 秒。
- 删除 `frontend/src/pages/users/requestLifetime.ts` 及其测试，改为引用共享实现。
- `oauth-onboard`、`message` 走同一套映射，不引入核对流程。整页 403 的标题和主按钮**先不动**，等 chen 拍板。
- 按方案 A：所有写操作在超时、断网、502 / 504、500 / 503 时只显示 3.1 节文案。验收第 21 条为**不得出现**「操作可能已生效，请刷新后确认再重试」。不要为实现 B 预留代码。
- Chrome 插件行为与提示保持不变。

## 5. 非登录页写操作清单（P1 拆除门闩与补齐超时时对照）

本表列出工作台里会改数据或有副作用的写接口，供 P1 拆除核对门闩、补齐 30 秒超时时逐项对照。按方案 A，这些接口与读操作使用同一套文案，**不要**按本表附加「可能已生效」。读接口和纯预览不在此列。

| 页面 | 方法与路径 | 说明 |
| --- | --- | --- |
| 用户管理 | `POST /v2/users`；`PATCH /v2/users/{id}`（显示名 / 角色 / 状态）；`POST /v2/users/{id}/password`；`POST /v2/roles`；`PATCH /v2/roles/{code}`；`DELETE /v2/roles/{code}`；`DELETE /v2/users/{id}/sessions` 与 `.../sessions/{sid}` | 创建用户、改状态、重置密码、删角色、吊销会话均有副作用 |
| 店铺注册 | `POST /v2/admin/shops/register`；`PATCH /v2/admin/shops/{id}` | 注册与改店铺 / 凭证 |
| 枚举映射 | `PUT /v2/config/enum-map`；`DELETE /v2/config/enum-map/{id}` | 保存带创建语义；删除有副作用 |
| 运行配置 | `POST /v2/config/runtime/items`；`PUT .../draft`；`POST .../publish` 及停用 / 恢复 / 回滚等生命周期 `POST`；`PUT /v2/config/runtime/secrets/{name}` | `POST .../preview` 与 `/schema/preview` 是预览，**不列入** |
| 定时任务 | `POST /v2/admin/sync-jobs/{name}/trigger`；`PATCH /v2/admin/sync-jobs/{name}/enabled` | 立即执行可能对上游重复同步 |
| 采购工作台 | `POST /v2/reporting/manual-costs` | 写入人工成本 |
| 重点关注 SPU | `PATCH /v2/reporting/focused-spus/{shopPk}` | 增删关注列表 |
| 拦截配置 | `POST /v2/intercept/configs`；`PUT .../{id}`；`POST .../import`；`POST .../batch`；`DELETE .../{id}`；`PATCH .../{id}/toggle` | 导入和批处理尤其不可盲目重试 |
| 视频发布 | `POST /v2/video-publish/tasks`；`POST .../upload-url`；`POST .../confirm-upload`；以及任务上的 settings / replace-upload / cleanup/retry / 发布相关 POST、PATCH | 浏览器直传存储本身豁免 30 秒，但创建任务和 confirm 仍算非幂等写 |
| 资产管理 | `POST /v2/media/assets/upload-url`；`POST .../assets/{id}/confirm`；`PATCH/DELETE .../assets/{id}`；`POST .../packs` 与 packs 视频增删；数字人资产挂接 | 同上，直传豁免 30 秒 |
| 数字人 | `POST/PATCH/DELETE /v2/media/digital-humans`；资产与素材包的挂接 / 移除 / `batch-remove` | |
| 换装视频 | `POST /v2/runninghub/templates`；`POST /v2/runninghub/batches`；`POST .../pause`；`POST /v2/runninghub/dispatch/resume`；`POST .../jobs/{id}/recover|retry|resolve` | `retry` 在真实模式可能再次计费，尤其不要在结果未知时自动重发 |
| 壳层 | `POST /v2/auth/logout` | 幂等；失败也只走通用文案 |

若发现遗漏的写接口，先补进本清单再改对应页面。

## 6. 明确不做

- 不改插件。
- 不改原型。
- 不改登录限流、Cookie 策略、密码校验、停用账号防枚举策略。
- 不把登录 503 的事务拆分做成原子事务（不在 04 范围；需要的话另开任务）。
- 不改整页 403 文案（仍待 chen 明示）。
- 任何页面都不得出现「操作可能已生效，请刷新后确认再重试」，也不为此预留字段或分支。
- 不删除 `_LOGIN_HTML`（本次只确认不可达）。

## 7. 任务安排

**开发（P0 先做，通过自测后再做 P1；仅整页 403 文案继续停住）：**

1. 落地 3.1 共享映射与 `commandFailure` / 超时错误类型改造，补单测。
2. 按 3.2–3.4 改登录页、`login_page` 302、`/me` 的 503，改相关前端单测与 API / 浏览器测试。
3. 自测：PRD 第 9 节 1–11，以及第 12、13 条中与登录分类相关的部分。在 `E:\tts-erp` 按仓库既有方式运行受影响的前端单测、`tests/api/test_user_auth.py`、`tests/browser/test_frontend_remaining_auth_native.py`（及因共享映射失败而必须改的用例）。
4. P1：拆核对门闩、统一 30 秒超时（含豁免）、Toast / Alert 位置、把各页私有映射收进共享表。写操作超时 / 断网只显示通用文案，禁止「可能已生效」。整页 403 文案**停住等通知**。
5. 完成后向架构师汇报：改了哪些文件、怎么跑测试、测试结果、遗留问题。不要擅自改整页 403 文案。

**测试：**

1. 按 PRD 第 9 节写用例。P0 先准备 1–11。第 8 条：用停用账号登录，应返回 401，文案为「用户名或密码错误」，不得出现「当前账号无法登录」。第 12、13 条中登录分类相关部分随 P0 回归。
2. 开发提交 P0 后，在真实或本地工作台按第 4.3 节把 401 / 429 / 503 / 断网 / 超时跑一遍，确认无核对门闩、按钮可再试、已登录打开登录页会进工作台、会话过期回跳仍带原查询串。
3. P1 再跑 12–25。第 21 条：范围内不得出现「操作可能已生效，请刷新后确认再重试」（方案 A，已拍板）。第 20 条（整页 403 文案）仍待明示，未拍板前不作为失败项。
4. 向架构师给出是否可交付的结论，问题写清复现、预期、实际。

## 8. 风险

- P0 改共享 `commandFailure` 会使其它页的核对门闩提前消失，需要同步改掉因此失败的页面单测；不要为了保绿而把 `uncertain` 改回去。
- `GET /me` 改为库异常 503，必须保住「无 cookie → 200 authenticated:false」这条契约。
- 已登录 302 后，原先在登录页上换账号的路径不再成立，需先退出；这符合「不必再登一次」的产品要求。
