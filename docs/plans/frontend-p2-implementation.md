# 前端 P2 实现计划：普通页面与首个隔离写闭环

> 状态：run-binding、后端安全、共享契约与未激活统计内容切片已验；统计越界筛选已修回固定7天并通过增量源审。店铺UI三个阻塞及aware-expiry已strict修复并通过增量源审，仍待真实写闭环；枚举内容已通过组件/只读原生预览与独立源审，仍待真实写闭环；test-only child身份支持已实现并验无seed冷启，独立源审PASS并修复非阻塞decoder归一化；未开放seed/业务写。窄测试只证明其覆盖切片，不是 P2 GREEN 或交付声明。
> 权威父方案：[渐进迁移方案](frontend-progressive-migration.md)；组件标准：[前端开发规范](../guides/frontend-development.md)。
> 用户在 P0/P1 交付后连续要求继续开发、直到任务完成。2026-10-24 明确本次不需要灰度逻辑或放量过程：完成改造与必要验收后直接合并 `master` 启用，不做灰度开关、逐页/流量分批放量或观察等待。P0–P6 仅为开发任务分解；权限/隔离数据安全及并行 owner 边界不变。

## 1. TaskStartSnapshot、必要性与边界

- Owner/session：`01a16719-eedc-7605-a9b1-1e16d356e488`，canonical board identity 相同；任务 `frontend-p2-contracts`。
- UTC：2026-10-24。独立 `feature/frontend-p2` / `.worktrees/frontend-p2`，从 fresh `origin/master=e86817a3f28d4267ce0527a43ed7887795da81c3` 建立。
- P0/P1 已完成，代码精确集成 `8f1b752` 的 fast 基线零新增稳定失败；不把其证据冒充 P2 通过。
- Change Necessity：三页仍依赖旧模板/脚本，复制了会话、路径、请求与布局逻辑；需接入既有 React/HeroUI 骨架，保留有效查询、管理和错误恢复。
- 初始 lane 仅本计划；现已按回执 `m-badab46c-d24` 登记并读回本计划、run-binding 的2scripts/conftest/binding与refresh tests/testing guide；`m-f3272c5e-978` 追加 no-dotenv bootstrap 的精确子进程透传修复；`m-296716be-6d8` 追加 deps/admin/config 的最小安全接线、旧enum两头及3个API测试路径；`m-53f8e91b-13d` 登记共享bootstrap/client/契约测试与3个图表依赖文件；`m-b6fa2a47-144` 登记统计4文件、loader及其测试、只读native browser测试共7路径（无backend激活权）；`m-5ec16f02-e60` 追加shops五文件及shared command-errors两文件，并明确日期控件越界应修回原计划，不追认；`m-8ee8f0f6-518` 追加只读shops browser并允许既有loader两文件提供shops代码映射；`m-66070242-2d9` 追加enum contracts两文件，均非backend激活权。其余源代码、依赖、API guard/live支持变更仍须先扩展精确 scope 并读回。没有 child 写 reservation。
- 保留 main 的 `plugins/monitor` 和未跟踪 `node_modules/`；原 child/coord 审计工作树不动。
- 不改 app/access/middleware、schema/models/migrations、财务口径、TikTok 店铺写端点、插件、生产数据/服务。后端现有缺陷只在确定 canonical owner、登记 scope、写回归及安全审查后处理。
- ArchitectureReviewRequired：是，涉及共享权限事实、凭证输入、OAuth 命令及真实浏览器/API 写入边界；沿用原架构，不另造 session/menu/client/chart engine。
- TDD Route：`auto → strict`（共享/行为/权限/持久化/producer-consumer 风险）；真实行为 RED→最小实现→GREEN。依赖、lint、构建、源码审计都不是行为 GREEN。

## 2. 已核对的旧页面行为

### 2.1 intercept-stats

来源：`templates/pages/intercept-stats.html`、`static/js/intercept-stats.js`、`api/v2/intercept.py::get_requests_stats`。

- 初次读取 `/v2/intercept/requests/stats?days=7`；保留总请求、白名单命中、今日请求、错误请求四张卡。
- 保留每日请求柱状图以及域名、方法、状态码三种分布；分布的 `percentage` 已由后端提供，不重算统计口径。
- 当前 endpoint 还提供 `from/to`、camelCase 分布别名；迁移不自行新增日期筛选或改后端查询范围。
- `daily` 的日分组由后端按 UTC 生成；前端展示返回的日期标签，不按浏览器时区重新分组。今日数量与其他范围数量的已有语义不改。
- 保留空数据、加载与错误；增加一致的可访问重试/取消仅限纯读，不产生意外写请求。
- 只允许图形比例等 `display*` 展示变换；卡片数量、分布百分比和日统计均以服务端值为准。

### 2.2 shops

来源：`templates/pages/shops.html`、`static/js/shops.js`、`api/v2/admin.py`。旧 UI 的 admin 文案已过时：`docs/api/external-api.md` 的 shops/register/PATCH 条目、实际 `require_role_at_least(readwrite)` 以及 `tests/api/test_admin_shops.py::{test_register_app_credentials_allow_readwrite,test_update_app_credentials_allow_readwrite}` 一致确认，2026-09-29 已由用户决定 App pair 也是 readwrite。迁移保留该有效契约，不擅自改成 admin-only。

- 列表：`GET /v2/commerce/channel-accounts?platform=tiktok&limit=500`，保留 shop_id、名称、区域、service_id、App 凭证状态、开店日期、同步方式与状态；内部更新使用 `shop_pk`，不拿 external `shop_id` 当主键。
- 人工注册本地店铺记录（readwrite），已有元信息为空时补填，不覆盖既有 OAuth credential_id/同步状态；App pair 的显式提交按 service_id 配置，不能误写成“重复注册永不更新 App 凭证”。service_id 不一致时返回 409 并要求显式编辑；App Key/Secret 成对且 service_id 必填。不是 TikTok 店铺写操作。
- 元信息编辑：名称、区域、service_id、开店日期；保留取消与失败恢复，不照搬旧单元格先改显示再回滚造成输入丢失的实现。
- App 凭证管理：App Key/App Secret 成对，secret 仅 password 输入，不预填、不回显、不写 bootstrap/storage/log；按 service_id 共享的影响必须在对话框中说明。关闭/确认成功后清除 secret；失败处理及未知结果不能自动重放。
- OAuth：生成授权链接、自动尝试复制、手动复制和新窗口打开、单次 state/UTC 到期提示。此 GET 会创建本地授权状态，应按用户命令处理，禁止套用纯读自动重试；测试不实际访问 TikTok。
- 使用 HeroUI 原生表单/对话框和共享 AppShell，不复制旧动态 CSS、window.confirm/prompt 或第二套身份区。

### 2.3 enum-map

来源：`templates/pages/enum-map.html`、`static/js/enum-map.js`、`api/v2/config.py`。

- `/v2/config/enum-map/list` 返回完整列表；保留七种已知分类和未知分类、空分类、英文值只读、中文标签/排序编辑、新增和确认删除。
- 既有 wire 为 snake_case；不借前端迁移强改公共 API。组件内可作无损 camelCase 适配，不重新定义业务枚举。
- PUT 为 `(enum_type, enum_value)` upsert：字段长度上限分别 64/128/256，非空，`sort_order` 是非负整数。不能照搬 `parseInt(...) || 0` 的静默截断。
- PUT 与 DELETE 服务端要求 admin；readonly/readwrite 可浏览时不得呈现可执行的写控件。CSRF 使用共享 client，不复制旧脚本缺失请求头的调用。
- **已发现安全门槛**：当前 `delete_enum_map` 仅检查 admin 后直接 `sess.delete/commit`，未见共享 destructive guard。不能因为迁移而扩大/重现未受 guard 保护的配置删除入口。先核对规则与已有测试，登记最小后端 owner scope并以回归驱动修复，再开放该操作；不得设置生产 override 或跳过 guard 制造测试成功。

## 3. 复用与依赖候选

- 复用已落地 AppShell/Header/navigation、bootstrap/path/client、401 单一 handler、纯读 Retry-After 调度及打包 manifest/license 流程；页面只产出内容。
- 项目已 vendored uPlot 1.6.32 MIT，当前 npm latest 仍为 1.6.32。官方 README 提供 bars/React wrapper 链接；不自造图表引擎。
- 优先验证维护中的 `uplot-react@1.2.4`（MIT，peer `react>=16.8.6`、`uplot^1.6.32`），配合锁定 `uplot@1.6.32` 直接采用。GitHub repo 非 archived，最近 pushed 为 2025-09-25；`updated_at` 不是代码维护证明。
- 已 exact 安装两包、无额外runtime transitive、已读两份 MIT 原文及 wrapper 真实 hooks/create/destroy/update 源码，npm audit（含dev）0；声明 peer 与本工程相容。依赖准备时的工程构建并不证明原生消费。后继统计切片已实际 typed import/props、native canvas、React19开发StrictMode重建/销毁、数据更新、resize/empty/unmount验收，见§7；不泛化成该旧wrapper的全部React19用法都已兼容。wrapper类型仍用旧全局JSX，工程原有skipLibCheck未改，无新忽略/peer强制/降级。
- 上游：<https://github.com/leeoniya/uPlot>、<https://registry.npmjs.org/uplot/latest>、<https://github.com/skalinichev/uplot-wrappers>、<https://registry.npmjs.org/uplot-react/latest>。
- 新图表依赖须进入既有 lock/license 包装；提供语义化日期/数量表作为可访问数据出口，图形不是第二个统计真相。

## 4. 共享权限/会话待决项

`auth.py::me` 只自校验 cookie；无 cookie、API-key 页面访问和 auth-off 情形可能返回 `authenticated:false`，不可据此擅自跳登录或猜测角色。

选择最小的、后端拥有的 role capability hints（建议 `roleCapabilities: {readwrite, admin}`）加入受测试的 bootstrap，直接复用 `api.deps.require_role_at_least` 的 canonical `AccessGrant.allows` 语义，不在浏览器重建 role hierarchy。不能只发送 raw apiTier 再忽略 auth-off/bypass 语义，也不能用 cookie-only me 降级有效 API-key/auth-off 页面。未知/缺失 hints 默认禁用写操作；旧 P0/P1 bootstrap 测试兼容性须覆盖。hints 不是资源级授权或持久缓存，下一次 API 仍执行真实 page/api_tier/guard；不增加 session provider，也不改其他 owner 的 access/middleware。

已确认 OAuth authorize 只有显式用户命令时创建单次 state，无 upstream 请求；`format=json` 成功返回 authorize_url/state_expires_at，SigningError 是 HTTP 500；不以 body.ok 另造成功通道。不更改此既有 GET/直接导航合同，不把它包进纯读 retry，也不以修复四个 non-GET 写入口为名改 OAuth 方法/页面。

原 readonly 审计 `5d82ae14` 因 runner 消失失败，未计入证据；明确报告后同协议恢复为 `97eed02b`，有效报告 `/tmp/frontend-p2-backend-contract-audit.md` 已完整读取。它是源码证据，不是计划批准或测试 GREEN。确认的合同如下：

- shops list 是带 `X-Total-Count` 的 bare array，limit 最大 500，不声称全量。内部主键字段为 `id`；日期/null 明确展示，不将 external shop_id 转 Number。`app_credentials_configured` 不等于 OAuth 已授权；PATCH null/缺省保持原值，不提供清空到 null、status/credential_id/seller_type 编辑。
- HTML 三页最低 readonly，cookie 还需相应 page code；这些 data API 本身按 tier、不按 HTML page code 授权。内置 viewer 缺 shops/enum 页面，operator 有页面和 RW 但不能 enum 写；测试只使用账号服务真实允许的组合，不绕过账号校验造不可能权限。
- Cookie 优先于 API key；有效 readonly key 可访问三页。auth-off/shadow 有 bypass，canonical allows 为 true，不得依据 me=false/raw role 空值误关写控件。auth-store 503、权限 403 和匿名 HTML 302/401 分别验证。
- shared client 已支持 204、单次 fetch、cookie/CSRF 头、401 owner 与 Retry-After；但丢弃 422 object/array detail，且不验证业务 DTO。最小添加 unknown typed detail 并仅提取允许的 loc/msg/安全 message，不显示或日志记录 input/ctx/secret payload。requestId 仅在响应提供时显示，不造全局关联保证。
- 网络中断、5xx、截断 2xx 可能在 commit 之后；控件 single-flight，无自动重放，显式 GET 核实后再由用户决定。验证/409/服务器及未知结果在页面内保留输入；401 继续既有单一登录/next 策略，不承诺跨登录恢复 secret、不增加 secret/draft storage。

### 4.1 最小后端安全修复（须先登记写域）

- 源码审计确认 shops POST/PATCH、enum PUT/DELETE 均缺 server cookie-CSRF 校验；前端发头不是保护证明。复用 `focused_spus.py::_require_cookie_csrf` / reporting 的现有契约，将相同六行语义抽成 `api/deps.py::require_cookie_csrf`，只接这四个入口；cookie auth 缺/错 `X-Requested-With: tts-erp` →403，API-key/off 按现有认证来源豁免。角色 gate 不变；不改 app/access/middleware、不重构邻居路由。
- enum DELETE 在 admin 和 CSRF 检查后、访问/删除 row 前调用现有 `require_destructive_guard`；不改 guard 实现/默认 override，不用“派生快照”例外。生产拒绝是 403，不能跳登录或显示成功。负例使用无 DB spy/拒绝路径或明确非连接的模拟判定，绝不连生产或设置生产 override。
- 旧 shops 脚本已有 non-GET 头；旧 enum 脚本没有。为避免后端修复后旧页先坏，必须在同一兼容提交给 `static/js/enum-map.js` 的 PUT/DELETE 最小补头，不改其布局/权限模型或移除其他消费者。该精确路径也先登记。
- 候选验证：`tests/api/test_cookie_csrf.py`（helper/认证来源/缺头错头/不触库），`tests/api/test_enum_map.py`（admin独立于guard、拒绝不删/clone删除/404），现有 `tests/api/test_admin_shops.py`（cookie拒绝、RW凭证保留、409事务回滚），保留 API-key 与正常 legacy 调用。

### 4.2 同轮 clone 的真实 API/browser 支持

本节旧术语 `canary` 仅指隔离测试库中不可预测 TEST 数据的认证读回探针，**不是用户流量灰度或放量步骤**。
它用于避免误连数据库，保留数据安全要求，不建立发布系统或流量观察等待。对外统一称“隔离数据读回验证”。

新增候选 `tests/support/frontend_p2_live.py`，调用 `tests/browser/test_frontend_p2_live.py`；拒绝/teardown 单元在 `tests/scripts/test_frontend_p2_live_support.py`。复用 `support/spu_price_stats_live.py` 的 env allowlist、端口/进程组生命周期等已维护机制或小范围适配，不放宽其 `LiveBrowser` 的只读断言，不修改邻居业务场景。

独立计划评审 `6bdedd35` 对 a994ff3 判 FAIL/F1：候选 URL 自己解析出的 expected、实际库名与同库 canary 会共同证实同一个误绑定，现有 wrapper 没有独立 current-run 产物。下列 producer→consumer 修订是代码必须实现的合同；不是用额外 prefix 判断掩盖 F1。

1. **唯一 producer**：`scripts/test_isolated.sh` 已持有原始 BASE_TEST_URL、TEMPLATE_DB、RUN_DB、派生的 canonical RUN_URL 及 pinned transport。保持 derive 的六字段合同、现有锁/clone/cleanup；只有 createdb 成功且 RUN_CREATED=1 后，才在私有临时目录生成全新的 run ID 和 run proof，调用同一 `scripts/_test_db_connections.py` 的新 issue-run-proof 操作。proof 不由候选 URL 或可选输入 TTS_ERP_TEST_DB_NAME 推断。新增路径须先登记脚本 owner scope；不引入第二个 URI parser/生产 override。
2. **独立输出**：wrapper 每次无条件覆盖传给 test.sh 的 `TTS_ERP_TEST_RUN_PROOF` 和 `TTS_ERP_TEST_RUN_ID`，同时传 canonical `TTS_ERP_DB_URL_TEST`。proof 为 version=1、created=true、run ID、wrapper PID/进程启动标识、原始有效 base DB、所选 template DB（含自定义名）、run DB、规范化连接身份摘要的严格 JSON。由 authority 用已验证参数和 run ID 计算带随机 salt 的摘要，覆盖有效 host/port/user/db及其余连接参数；不在 proof/argv/log 中存 URI、password 或完整参数字典。文件 O_EXCL/no-follow、同 UID、目录0700/文件0600；输出只在成功 clone 后存在。cleanup 无论 keep-db 与否都移除自己生成的 proof/目录；遗留库不因此获得下一轮写权。PID 加启动标识与独立 nonce 防 stale/reused-PID 混淆，不宣称防恶意同 UID 进程的密码学边界。
3. **同一 authority 的 consumer API**：新增 load/validate run binding，读取 wrapper 的独立 proof+run ID、核对版本/类型/私有普通文件/活跃 owner+启动标识/created（owner 必须在当前进程的内核 parent 链上，不能接受另一个仍活跃 wrapper 的完整旧环境）、run 与 base/template 各自不同（base 与 template 可相同，保持 derive 旧合同）及共享/prod拒绝，再把候选 URL 的有效身份与摘要比较。新 helper 复用 `_parameters`/`_test_name`/SQLAlchemy serializer；run 候选 URL 的 dbname query override/不一致 intent 一律拒绝，不以 URL 自己的 dbname 当 expected。缺失、陈旧、畸形、不一致均固定脱敏诊断，零 connect；不回退普通 test-shaped URL。
4. **父进程连接前门禁**：`tests/conftest.py` 的既有 autouse schema probe 会先于 P2 fixture 连接，不能略过。将 run binding 消费接到该 canonical 测试入口及 probe/engine 的连接前边界，先验证 wrapper URL，再检查实际 engine 有效身份，最后才 connect。P2 fixture 也只使用通过该 identity 的 engine/session，先 SELECT current_database 精确等于 proof.run_db，再提交 TEST seed。拒绝既有 direct/shared fallback，不修改角色/破坏 guard 来制造通过；纯离线 fake-engine 反例不建真实连接。脚本发行证明、conftest 门禁和对应 fake-wrapper 兼容回归为同一个提交切片。
5. **子进程启动绑定**：继承显式 allowlist，并显式转交 proof/run ID；两 DB 变量同 canonical URL、enforce、测试 Fernet key、no dotenv。每次 launch 新生成独立 launch nonce 和全新私有 attestation 路径，禁止复用旧文件；factory 在连接前验证 run proof、实际 app engine/session 有效身份，连接后核对 actual DB，再构造/服务实际 app，并以 exclusive 文件输出 run ID+launch nonce+child PID/启动标识+identity digest+actual DB。仅 test-support ASGI wrapper 给实际响应附本次 launch nonce，绑定 readiness/canary HTTP 到这个 child，不新增 production 路由或改 app.py 中间件。父进程必须核对本次 launch/run 全部字段、Popen child 身份、owner ancestry/liveness及 probe 响应 nonce，不仅看文件或 healthz 存在。缺失/上一 launch/上一 run 的证明都失败且不开放业务写。没有 production 诊断 HTTP 路由。
6. **明确 canary 顺序**：父进程通过上述门禁后提交不可预测 TEST canary、测试用户及 readonly API key。子进程 identity/readiness 通过后，先用已 seed 的 readonly key 做真实 canary GET 并比对，再允许 cookie login 和被测 OAuth/业务命令，之后 UI写→独立 GET/DB读→reload。源码 `access/_credentials.py::_db_lookup` 会 best-effort 更新 last_used_at；因此不谎称带认证 GET 完全无存储副作用：此认证元数据更新只发生在 child identity 门禁之后，canary 之前仍禁止 login/session 创建、OAuth state 与被测业务写，不预热/伪造 auth cache 绕过真实认证。canary 是数据通路证明，不替代 run provenance。
7. **网络/生命周期**：factory mount 实际 app 测空前缀和 `/tts`，不叠加 uvicorn root-path；不启动 worker/scheduler。浏览器只允许本次 launched exact origin（不是任意 loopback，尤其不是9877）的授权 method/path，禁止 callback/upstream。硬失败 missing API/Chrome/proof/canary；有界 readiness、进程组清理，正常 teardown 先停 child 再由 wrapper 移除 proof/drop clone。丢响应测试先经真实 API commit，再丢弃/截断响应；验证零自动 replay。

**F1 的确切拒绝矩阵**：新增 `tests/test_test_run_binding.py` 与支持层单元，覆盖默认/自定义 template、base/v3/shared、prod-shaped、另一活跃或 retained tts_erp_test_* clone、query dbname override、engine host/user/db/连接参数变化、缺 proof/错 nonce/畸形字段/死 owner或复用 PID/非祖先的另一活跃 owner、旧 child run/launch proof/错误 child PID或响应 launch nonce；连接前拒绝都断言 connect 调用=0。正例验证只有 clone 成功才发布、继承的旧 proof 被覆盖、test.sh 原样传播、失败与 keep-db 都撤 proof、六字段 derive/模板锁/无 override/脱敏不退化。既有 `tests/test_test_template_refresh.py` 的 fake runner 也须适配并保留其原安全断言。

## 5. 执行顺序与交付门槛

1. 完成三页源码/权限/DTO及真实 API fixture 清单，补齐本计划的确切测试节点和文件所有权，完成独立架构/安全检查；再完成 `frontend-p2-contracts`。
2. 仅补必要的共享契约及验证后的直接依赖；先观察 focused 行为 RED，再实现，完成 `frontend-p2-core`。没有新路由/权限 authority 或财务计算。
3. 统计→店铺→枚举按工程任务实现与验证，在任务分支接好真实 renderer 以便隔离验收；完成后直接随版本合并 master 启用，不设逐页放量流程。沿用后端唯一 `MIGRATED_PAGE_IDS` 技术接线，不增加环境/灰度开关、第二套名单或缺资产 legacy fallback。
4. 所有测试唯一入口 `bash scripts/test_isolated.sh ...`。写入验收启动临时 API，证明它与浏览器实际连接本轮 `tts_erp_test_*` clone；不使用 live `:9877`，不从生产 `.env` 引入凭据。用 TEST_ 命名、数值字段遵守后端 schema 的隔离测试数据，禁止真实 OAuth/店铺网络变更。
5. 对真实 server 做 read-after-write、刷新、readonly/RW/admin、缺页面权限、CSRF、字段错误保留输入、重复点击单次提交、401/403/429/5xx与未知结果不重放。仅 mock 响应不足以证明持久化/权限闭环。
6. 浏览器覆盖空前缀和 `/tts`、375/768/1440 宽度、键盘/焦点归还、长值/空值/空分类、resize/unmount、坏 chunk。表格可局部滚动而全页不横溢；少量布局样式，不搬旧皮肤。原生产业务数据不进入截图/日志。
7. 窄回归后固定候选运行 baseline 一次；出现新失败只最小重跑分类，不重建 master baseline。独立审查、同步/推送 ready、精确 prospective-master 验证合并、回执和 cleanup 才完成 P2。

## 6. 保留与退役

按用户无灰度口径，P2 候选在任务分支接线和验收，完成后直接交付，不按用户/流量分批切换。旧资源仍须先登记写域，确认替代已验且无消费者即可删除，不等待回退期或流量观察。P3 的高权限/编辑器、P4 的复杂表格/SPU内核、P5 的有效产品需求与专题 owner 仍须对齐；这些是正确性/所有权边界，不是放量门槛。P6 在全部目标页通过必要验收、无旧消费者后完成清理，保留 Git/完整包应急撤销能力，不保留永久灰度/双栈。

## 7. 当前证据与未覆盖

已做：合同审计及 e404c10 计划独立 F1 复审 PASS（`/tmp/frontend-p2-plan-f1-review.md`），精确登记 `m-badab46c-d24`；按原 lock 私有 npm ci 准备工具，未新增图表依赖。实际 strict TDD：producer/保留名称4 RED；canonical preconnection2 RED（旧 probe 请求 fake engine）；非普通 proof/FIFO 真实阻塞 RED 后在读取 owner 非阻塞打开并 fstat 拒绝。对应日志 `/tmp/frontend-p2-run-binding-{red-reserved,consumer-red,fifo-red,checkpoint-green}.log`；24eb775 检查点的两文件窄回归66 passed及独立源码审查 PASS（`/tmp/frontend-p2-binding-implementation-review.md`）只覆盖其版本。后续补齐私有/畸形/oversize/type/symlink、模板/base/retained/URI参数、活跃非祖先/死 owner/start变化、实际返回engine零connect/实际库名错仅identity SQL、真实test.sh传播、clone/issuer/command/proof-cleanup失败与失效proof拒绝；`/tmp/frontend-p2-run-binding-full-matrix.log` exit0/111 passed无skip，Ruff0及fresh LSP3 confirmed clean。RB-01 的本机原生有界深JSON本来已拒绝；明确用decoder RecursionError故障注入观察边界RED，再在load owner归一化，未谎称原生默认解码器已复现该异常。790b135 上一次完整 baseline 如实 exit1/新增6（`/tmp/frontend-p2-binding-baseline-verdict.log`），均为旧 no-dotenv 子进程 allowlist 未传新 proof；最小文件同样6失败，非flake。按追加scope仅透传2个新env及有界timeout、整理旧import空行，原dotenv/no-read/export断言及新guard不变；`/tmp/frontend-p2-binding-dotenv-green.log` 的三文件窄回归exit0/117 passed无skip，Ruff0/LSP1clean。此后只有该测试消费者/说明变更，无应用或runner代码delta；没有重跑全套或改baseline，不能声称完整suite exit0。增量独立复审仍待完成，binding任务未完成，更非P2验收。
run-binding 在 4f5 增量独立源码复审 PASS 后按原子切片完成，原完整执行 exit1/六项已聚焦修复的事实保留，最终 P2 baselines 仍有独立门禁。backend-safety 已观察13个真实RED（缺CSRF前触库、cookie缺/错头仍200、DELETE前无guard），最小实现后51例安全回归及64例连同未改runtime-config邻居全部通过；日志 `/tmp/frontend-p2-backend-safety-{red,matrix,neighbors}.log`。同提交为旧enum PUT/DELETE补头；角色、事务、OAuth GET、app/access/middleware均未改。原 config.py 的18个 FastAPI Depends/B008 Ruff签名与 HEAD完全一致，deps.py 的2个旧get_session返回类型LSP错误对应AST未变；不隐藏或冒充全路径lint clean，不顺手重构P3/旧DI。其余受测路径Ruff0、JS syntax0、LSP5文件clean；backend源码独立审查待做。

backend-safety 在 c59 独立源码审查 PASS 后完成。共享core已观察14个API/5个Vitest真实契约RED（`/tmp/frontend-p2-core-contracts-red.log`），实现最小server-owned roleCapabilities和additive ApiError unknown detail后，含API/旧路由/Node adapter/P0-P1浏览器的97个pytest用例通过（`/tmp/frontend-p2-core-contracts-neighbors.log`，内部Node 76例）。角色hint仅复用canonical gate，含Cookie优先/APIkey/off-shadow bypass；缺失坏值禁写，原4字段payload兼容；不改401 owner或重放。图表两包已安装/许可证/审计，原生图表生命周期仍未验。fresh LSP7无primary类型诊断，另有21条auxiliary advisory（含既有应向上抛出的JSON解析、bundler无扩展名import、I/O边界unknown/typeof及短ApiError参数风格提示），未新增抑制，不声称全部0。core独立审查待做。

core 在 f6d37e2 独立增量源码审查 PASS 后完成（`/tmp/frontend-p2-core-contracts-review.md`），图表准备证据当时仍非native验收。

统计后继：m-b6fa2a47-144登记后观察11个Node行为RED（loader拒绝未提供模块，`/tmp/frontend-p2-stats-red.log`），实现内容页/严格DTO/只读生命周期和上游wrapper适配；85还包含未获授权的UTC日期筛选，后经下述STATS-01纠正，不能把通过测试当成其产品授权。typed import暴露bars可选类型，显式检查缺失时报错，未强制断言/忽略；UTC日历缺失独立真实RED（`/tmp/frontend-p2-stats-utc-red.log`）后使用uPlot.tzDate，UTC+7 Node回归通过。桌面截图发现小时刻度重复日标签，在公开incrs配置前观察独立RED（`/tmp/frontend-p2-stats-ticks-red.log`），最小日刻度/UTC图例后重验。图表仅绘制服务器日计数，分布原样显示server percentage，不做aggregation/业务计算；伴随可访问日期计数表。

实际TS7/React19编译/组件测试、4个375/1440双prefix生产bundle原生蓝色像素/resize/每日空→清理→再挂载，以及1个真实Vite开发React.StrictMode的create≥2/destroy≥1、更新/最终全部native destroy通过。dev探针首轮CJS named import/过早快照问题已在测试owner修正（真实模块interop/等实际创建，不弱化断言），未伪称产品缺陷。102个pytest共享API/旧routes/Node adapter/P0-P1/native browser回归通过（`/tmp/frontend-p2-stats-neighbors.log`）；qualified helper import消除3个新Pyright误解析而不屏蔽，5 browser再通过；最后UTC+7下全90个Node及5 native browser通过（`/tmp/frontend-p2-stats-final.log`）。Ruff0/active LSP7无primary错误，loader仍有5条bundler import/既有Record辅助提示未隐藏。实际bundled license含uplot/uplot-react及两版权人。4张TEST截图在frontend/.cache/screenshots，375和1440人工查看；源码仅增加可加载模块，MIGRATED仍2页。85统计切片独立源码审查FAIL（`/tmp/frontend-p2-stats-review.md`）：STATS-01新增日期筛选违反§2.1，为blocking；STATS-02首尾柱裁切为nonblocking。Parent完整读报告并接受，handoff不能推翻批准范围。保持§2.1不变、先观察no-filter/fixed7真实RED（`/tmp/frontend-p2-stats-scope-red.log`），仅删days/from/to控件及状态，固定days7并保留明确刷新/重试、UTC/today说明与全部数据视图。新增native x-range纯展示半日padding前同样观察undefined range真实RED（`/tmp/frontend-p2-stats-padding-red.log`），不改API范围/数据/业务口径。浏览器改为授权刷新，增加Enter刷新/focus、grid ArrowDown、3个非零柱等宽像素断言，仍保留空→卸载→恢复/resize/StrictMode销毁。一次全Node暴露原表格collection异步快照过早，改等同一99.7%真实元素出现（非弱化断言）；最终UTC+7下全112 Node（含下述22个Shops边界）及5 native browser无skip通过（`/tmp/frontend-p2-stats-scope-final.log`）。已重新查看375截图，首尾柱完整、无日期筛选；9269064修复增量源审PASS（`/tmp/frontend-p2-stats-delta-review.md`）已全文读并关闭STATS-01/02，统计未激活内容task完成，不是服务端路由/权限或生产数据验收。

Shops不相交边界检查点bfad29d：新增DTO投影/string外部ID/内部PK/null日期/计数header未知、command成功身份核对及HTTPS授权URL/expiry契约；共享错误只白名单loc/type和固定已知pair文案，忽略rawmsg/input/ctx/secret/异常消息，5xx/网络/坏成功响应明确unknown，不添加replay。22个Node真实RED（缺新模块）→全112 Node通过（`/tmp/frontend-p2-shops-boundary-{red,final}.log`），只完成该边界实现，UI/角色按钮/single-flight/clipboard/持久化未做。active LSP8无primary错误，shared decoder仍有23条I/O unknown/typeof/Record/bundler辅助提示未抑制；不存在secret回显或新增忽略配置。

Shops UI 后继（m-5e/m-8e登记内）：实际12行为RED（最初测试ByRoleOptions不支持exact已在测试owner修正，之后12个缺模块运行RED，`/tmp/frontend-p2-shops-ui-red.log`）→原生HeroUI表格/三种表单、readwrite hint显式true、页内ref single-flight和逐命令响应身份核对实现。重复submit一次请求；confirmed成功关闭清secret并重新读，422/409/403/5xx/网络/坏2xx保留草稿、无raw错误/自动重放；OAuth GET仅显式命令，正确consume authorize_url，无body.ok依赖，尝试自动复制和手动复制/打开/UTC到期/单次state。没有新增搜索/分页产品面，仍固定limit500并显示server header/未知。

open-draft hint消失与429把取消/输入也锁死分别出现真实2RED（`/tmp/frontend-p2-shops-recovery-red.log`），最小分离pending与blocked后，禁写但可编辑/取消/cooldown说明，无第二权限或命令引擎。同步方式缺失另1RED后恢复原凭据关联的API/插件展示，明确App配置≠OAuth有效。实际原生长名称宽1197px出现RED（`/tmp/frontend-p2-shops-long-name-red.log`），仅max-width/break-all少量布局，不改库内部。原生Dialog关闭后测试最初要求精确子按钮，4次观察实际返回原TR；已核对React Aria useGridCell onFocus源码的单tab-stop键盘恢复策略，不做自有焦点管理。测试契约更正为精确invoking row或button（非任意元素），只证明原上下文恢复，不冒称精确子按钮/完整键盘导航。

`/tmp/frontend-p2-shops-neighbors.log` 的80 pytest（API安全/Node adapter/P0-P1/stats/shops）全通过；其后仅长名称wrap/同步文案与对应断言，`/tmp/frontend-p2-shops-layout-final.log` 的全部131 Node+8只读原生browser无skip通过，包含375/1440×双prefix×RO/RW、Dialog Enter/Escape/取消清密钥/原行焦点上下文、长值局部scroll无整页溢出。8张TEST截图在frontend/.cache/screenshots，375RW与1440RO人工查看（后者wrap后重新查看）。Ruff0/active LSP7无primary，29条decoder IO/typeof/Record/bundler辅助提示如实保留。以上写命令只在Node mock层验证；browser严格GET-only，未POST/PATCH或调用OAuth GET；测试preview的直接grant/render不是实际角色鉴权。loader只增加可加载模块，MIGRATED仍2。882的UI/边界源码独立审查FAIL（`/tmp/frontend-p2-shops-review.md`）：SHOPS-01警告不能替代保留草稿的显式GET核对；SHOPS-02确认写后refresh绕过list Retry-After；SHOPS-03 max-width-only仍让375一行约3k高。均按原owner修复，不更改批准合同追认；04 aware expiry为非blocking同步补格式回归。真实写仍未验。

修复严格证据：`/tmp/frontend-p2-shops-review-contract-red.log` 五个组件真实RED（未知后普通Save仍可用/无核对按钮×3/列表reads2≠1）；02测试首轮假计时器+userEvent timeout未冒称行为RED，改先真实打开草稿再注入计时/429后重跑得到确切reads2≠1。native375宽13/高2800的真实RED在`/tmp/frontend-p2-shops-review-red.log`。04 date-only/无offset/非法日历的真实RED在`/tmp/frontend-p2-shops-expiry-red.log`。

恢复UI在原Modal内提供可Tab/Enter到达的查询核对：保留draft/secret、按内部PK单条GET；未知注册没有内部PK时只用既有limit500列表按精确platform/external string选公开记录，不调用另有region门槛的by-external来假称全量查询。不自动重放，不加status endpoint/idempotency token，查询结果/未在前500中找到/404都不证明原提交是否已commit；尤其App配置bit不证明具体密钥更新、公开店铺GET不恢复丢失OAuth state。错误/身份错/坏GET继续禁普通提交，成功读公开事实后按钮明确“核对后明确重新提交”，仍由用户决定；取消会清表单密钥但保留未知核对上下文，不能误开普通命令绕过未核对状态。

feature-owned monotonic readNotBefore及单一read timer不随read Effect刷新cleanup取消，manual/post-success/check均过同一fence；确认写与延期列表读取分开，600秒完整到期只重新开放显式读，不重放写/自动读。测试覆盖599秒无第二listGET/600秒手动读、核对读也禁早发、unmount无后续请求；新写不是第二权限或query engine。窄屏使用公开Table.ScrollContainer region与最小表宽/名称和ID固定可读宽，限定行高400以内、局部横滚身份→操作、完整文本，不删业务列/改库DOM/focus。expiry只校验aware ISO表示和日历，不用browserclock判有效性；2020年有效格式仍接受。旧cancel测试名已更正为实际覆盖，未冒称metadata failure case。

`/tmp/frontend-p2-shops-repair-matrix.log` 全155 Node+8原生只读browser通过无skip；`/tmp/frontend-p2-shops-repair-neighbors.log` 同80 pytest API安全/P0-P1/stats/shops邻居通过。Ruff0/activeLSP6无primary，13条既有IO边界辅助提示未屏蔽。新375RO/1440RW截图人工复核，行高/文本可读且本地滚动；核对与命令仍只在Node mock层证明，未假称实际commit/read-after-write或真实browser写验收。63e363e增量源审PASS（`/tmp/frontend-p2-shops-delta-review.md`，全文已读），SHOPS01/02/03/04源码层已关闭；可选pending-cancel/internal404/capped-absence/shorter-wait回归并非新增阻塞。Shops任务仍不能完成：sourcePASS不替代真实current-run child/HTTP/canary/commit与浏览器写恢复验收。

不相交Enum边界检查点ac3cc10：m-660登记读回后17新Node真实RED→全148 Node通过（`/tmp/frontend-p2-enum-boundary-{red,green}.log`），保留snake_case/后台返回顺序/未知类别、非负安全整数格式拒绝小数/部分数字/空值，不新造排序或业务计算；只有2contracts文件。activeLSP2无primary，8条unknown/typeof辅助提示保留。没有Enum UI/角色控制/确认删除/实际写验收。

Enum UI inline执行snapshot：63e363e clean+push/freshfetch同e868（16ahead）、无Git操作、保留16worktrees/foreignWIP，owner仍parent单writer。2026-10-24 m-bdd323a0-8ce在短锁2335300读全current lanes/reservations无冲突后追加enum index/index.test/EnumForm和tests/browser/test_frontend_enum.py四路径，扩原loader映射测试权限；完整读回后已释放锁。46路径，不包括真实写fixture。Change Necessity=code-change：旧enum页无React内容，非代码不能提供批准的分类浏览/维护；最小页面+表单+测试，复用shared client/commandFailure和HeroUI3.2.6公开Select/Table/Modal/字段，不加包或API。TDD Mode:auto/Route:strict、authority=原auto风险决策（权限/写/异步），先实际UI行为RED再实现，Node仅mock命令/browser仅GET预览。ArchitectureReviewRequired沿e404已审方案，不另造role/query/status/business排序权威；保持已核验guard/CSRF边界。预算page feature lifecycle约230行、Form50，新增内容owner而非塞shared transport/其他页；read-fence仿既有Shops受审feature lifecycle，不抽新全局engine，任何实测owner混杂则停分片。七种known+unknown/空分类采用原生Select（窄屏比横排密集tab更稳），仅选分类与呈现backendorder、不重排/聚合/算业务；编辑英文值只读、label/sort可改，upsert/add及明确delete确认。字段按已有64/128/256与整数输入验证，unknown结果保draft和显式GET核对，不以列表当前事实证原commit；所有读入口保RetryAfter，权限撤回禁写但可取消。原API/source真实写和整套最终验收仍独立待做。

Enum内容后继证据：`/tmp/frontend-p2-enum-ui-red.log` 15个真实Node运行RED（14 UI缺模块+DELETE缺decoder），而非编译失败；最小实现index+EnumForm，新增DELETE合同只接受matching deleted内部PK，不以body.ok/错误身份/204空body称成功。PUT保持tuple upsert，不擅自改现有英文值；Form只读英文值、label/sort保留，Unicode长度按codepoint而非UTF16截断，数值只显式parseSortOrder，坏输入无写。admin exacttrue，非admin无行写控件；capability撤回后已开表单也禁submit但可取消，最终API权限/CSRF/共享guard不变。ref singleflight、safe commandFailure、无自动replay；unknown当场禁普通重提，在Modal内GET完整既有list核对tuple或delete内部PK；当前存在/不存在都不证原commit/rollback，保draft并由用户明确重新提交/再次删除，未核对取消后外层仍保持门禁。list/manual/post-confirmation/check共用feature-owned单读timer与完整monotonic deadline，Effect cleanup不缩短等待。无新增query/status/权限引擎。

`/tmp/frontend-p2-enum-loader-red.log` 1真实代码映射RED→仅加lazy module，不扩MIGRATED。第一轮UI31/32中唯一失败为Select原生可访问名包含当前值+分类，测试按真实完整名称修正，不改组件ARIA或放松为任意按钮。新增upsert后果说明前1真实RED（`/tmp/frontend-p2-enum-upsert-warning-red.log`）→新增时明确同tuple会更新已有label/sort；最后unknown类别删除后丢失选择1真实RED（`/tmp/frontend-p2-enum-category-continuity-red.log`）→在本地展示选择中保留已由后端出现的类别及空状态，不悄然切另一分类、不新造业务类别/排序。expanded回归含empty-known add/匹配DELETE成功/CSRF/重复提交单次、坏JSON/403/network核对仍保draft和禁写、取消unknown后外层门禁；只有上述Red先行部分声称strict新增行为证明，后续绿色边界测试不伪称均先RED。

`/tmp/frontend-p2-enum-ui-native.log` 全组件与12个真实compiled browser375/768/1440×direct/tts×RO/admin GET-only预览通过；`/tmp/frontend-p2-enum-ui-neighbors.log` 92 pytest API安全/全部Node adapter/P0P1/stats/shops/enum原生回归全PASS/no skips。全部Node由其中一个pytest adapter执行，不能当92个pytest之外额外DB验收；权限/mutation/recovery在Node mock层，不是live API持久化。Native验证长值/name宽≥200/行高≤400/无整页横溢/局部滚动/Select键盘Enter与known/unknown/empty、不改native focus owner的调用行或按钮恢复/两次开关草稿/删除确认取消零写；没有PUT/DELETE，仍Rendererwriteveto。12张TEST截图在frontend/.cache/screenshots，375RO和1440admin人工查看：可读有界长文本，本地横滚非一字符3k竖排。Ruff0、fresh active LSP8无primary；16条I/O decoder/bundler辅助提示未屏蔽。9ba7f92独立源审PASS（`/tmp/frontend-p2-enum-review.md`全文含JSON尾已读），无Critical/Important或spec/架构drift；Unicode边界/verification429/长短重叠wait/wrongtuple/unmount为optional测试深度建议，非源码阻塞。Enum整体仍不能完成，真实current-run/API/浏览器写门禁不因sourcePASS取消。

登记身份异常保留：板显示名变“前后端分离改造”后m-bdd默认message.from与record UUID不符，发现即停下一写；原session环境PI_SESSION_ID核对不变，以显式as原UUID发送owner确认m-cb8d9a15-30f，并在短锁2388406全链/所有lane核对，完整继任m-9909a7e0-f16读回后释放锁。新record identityReconciliation只引用该单一被原owner确认的alias，并非其他owner接管、fork、改历史日志或全局放松sender校验；之后lane工具固定as原UUID。涉及WIP仅既定9文件，无外国WIP混入，scope仍46/无child reservation。未来日志投影需消费该明确owner确认，不能以显示名自发接管。

Child身份支持inline TaskStartSnapshot：HEAD9ba7f92 clean+pushed/freshfetch origin/master103e454，17ahead/2behind、16worktrees、无Git操作。尚未merge新master，不称sync/ready；最终同步与两次候选级验证独立保留。短锁2409396下全current lane/scope/reservations（仅m-bdd按明确owner确认alias正规化）无冲突，m-ce49fefe-546追加3路径tests/support/{frontend_p2_binding.py,frontend_p2_live.py}与tests/scripts/test_frontend_p2_live_support.py后读回、释放锁，49路径，无childwriter。Enum9路径source已验不回改。

Change Necessity=code-change：原spu_price_stats_live是readonly且没有本轮child/run/HTTP独立证明，不能直接放宽；§4.2/e404批准架构要求独立新test-support owner；仅文档不够。TDD Modeauto/Routestrict（跨进程/连接/写前身份安全）：先离线fakeEngine/session零connect、child/HTTP stale身份拒绝RED；实现最小producer/consumer，再通过唯一runner作当前clone无seed/无认证的真实冷启+双prefixhealth验证。预算binding220行（application session/私有attest）、live180行（allowlisted env/factory/Nonce ASGI/启动清理）、tests250左右，按owner分2新模块，不给prod app/deps/dbbase或共享runner加职责，不造DSN parser。复用已有RunBinding/_process_state/_require_owner作为身份authority，原spu_price_stats_live的api_env/port/process-group停止，以及已用pydantic严格DTO/SQLAlchemy Session.get_bind/sessionmaker.kw、Starlette Mount/ASGI、uvicorn--factory官方维护接口，不加依赖；对应SQLAlchemy session_api/uvicorn.org文档已核。actualengine与factory bind/非空binds、实际session.get_bind在firstconnect前都检查；只有精确SELECT current_database核对才构建实际App/独占发布run/launch/PID/start/digest/actualDB无凭证attest，外层test-only ASGI加HTTPnonce，不增加prod诊断endpoint或改middleware顺序。父以Popen本次PID/start、wrapper ancestry/nonce/私有proof/HTTPresponse独立核，body/url/canary都不能自己证自己。父env显式allowlist/enforce/no-dotenv/test Fernet/两DBvar同clone，子nonce/attest路径每次新发，不继承旧launch；不输出DSN/rawstartup日志。任何miss/stale/wrong绑定固定脱敏拒绝、零fallback、停止整个子进程组。

Child支持检查点：`/tmp/frontend-p2-live-support-red.log` 30项因新module不存在而runtime失败/fixture setup ERROR（可用性RED，不冒称旧版实际错误连接复现）；最小新增binding/live两个test-only owner后30项通过（`/tmp/frontend-p2-live-support-green.log`）。actual engine URL、factory.bind/非空binds、实际Session.get_bind均在Session.scalar前核，wrong host/user/db/query或任一session route断言fake连接/identitySQL调用=0；actualDB错只执行identity SELECT（非commit），无备用engine/DSN parser。

私有attest采用已用Pydantic strict/frozen/extra-forbid wire DTO（version bool拒绝、nonce/digest/start/PID类型），限定同UID/private目录和普通文件、NOFOLLOW/NONBLOCK/8192上限、exclusive600写+flush/fsync，无URL/password/plaintext routing。fresh proof重新加载、Popen独立PID/start/kernel parent、parentstart、runID/launchNonce/digest/actualDB及HTTPnonce全部一致才yield identity-only VerifiedChild；无seed/write grant。复用既有price api_env/port/processgroup，lazy import前先proof/launch门禁；已AST核其module顶层只有Path/Decimal/SeedShop/SeedLine常量和regex，无顶层DB操作。两个DBvar同clone、enforce/testFern/no dotenv、oldlaunch被新nonce覆盖，ambient PG路由拒绝而非悄然清除，TIKTOK/override不传子；不保存/回显原始server启动/请求trace（DEVNULL），已知文件/解析/DB异常固定脱敏，无blind Exception或抑制。test-only NonceApp响应只保留1个本次nonce header，actual app公共Mount空/tts，无新生产路由/middleware/job。

扩展readiness坏HTTP body有2个真实RED（`/tmp/frontend-p2-live-readiness-red.log`）：list.get AttributeError与截断JSON非固定诊断；按同owner显式dict/JSON解析归一化修复。`/tmp/frontend-p2-live-support-identity-matrix.log` 42例全通过，其中真实child在本轮wrapper clone内仅metadata/identity SELECT和GET /healthz、/tts/healthz均验证nonce+private attestation；停止后PID已回收/临时attest已删，再启动nonce/path全新，绝无auth/seed/OAuth/业务命令。声明的identitySQL-only针对代码/fixture业务调用（连接driver可能有初始化metadata查询），不伪称采集了所有PG wire SQL。新增无proof零Popen、failed readiness不yield且收进程组/临时目录、ASGI单nonce无新路由回归后，`/tmp/frontend-p2-live-support-binding-neighbors.log` 162例（45新support+117 producer/conftest/dotenv邻居）全PASS/no skips/exit0，且wrapper最后清除clone。一次Ruff SIM117阻止起跑已改原test with结构，未说那次测试失败或已运行；当前Ruff3files0/freshLSP3 confirmed0（含aux），无新ignore。实现146/191行在预算内；test matrix330行多于最初约250估计，仍单一身份/生命周期责任的45例，未把业务fixture/seed掺入。

在固定checkpoint前另补child于HTTP返回期间退出的liveness回归，`/tmp/frontend-p2-live-child-exit-red.log`明确DID NOT RAISE真实RED；原来只在请求前poll，改为private/HTTP验证后、yield前再poll，拒绝已死child，仍不声称能原子保证未来永远存活。`/tmp/frontend-p2-live-support-final-identity.log` 最终163例（46 support+117原producer/consumer邻居）全PASS/no skips/exit0含2次真实冷启/双prefix健康GET和完整停止，Ruff0；一次并发LSP2clean/1silent不确定后对变动2路径serverScopeall主动补探针均confirmedclean0，未把silent当clean（未变binding上轮confirmed0）。对peer m-465254c6-418已读并按原UUID回信确认：此刻0071窗口未申请、不暂停clone、不复活旧partial告警；保持runner/guard/conftest/v16冻结/释放合同，未来明确begin/end再暂停。d3b1138独立身份/readiness-only源审PASS（`/tmp/frontend-p2-child-identity-review.md`全文含JSON已读），无Critical/Important，minor为深JSON可能抛RecursionError而漏固定诊断；不是ready旁路，finally仍cleanup。首次fresh默认Python subprocess测2200bytes depth1100实际未触发RecursionError（`/tmp/frontend-p2-live-json-recursion-red.log` nativeRecursion=False/normalised=True/closed=True，预期nativeTrue断言失败只是错误复现假设，不算source行为RED），与早前RunBinding原生深JSON不复现历史一致；不调全局recursionlimit凑复现，不谎称当前平台原生漏洞。native诊断program不作为冗余测试框架保留，只保已有HTTP-body参数化深数组用例；单独decoder故障注入在原owner真实RED（`/tmp/frontend-p2-live-json-decoder-boundary-red.log` RecursionError:TEST_decoder_depth穿出），仅为异常边界证明。最小将RecursionError加入该JSON解析tuple，不造新parser/深度fallback/修改上游authority；injected例及bounded深body均断言固定ValueError和close。首次含诊断program全166例green（`/tmp/frontend-p2-live-json-recursion-green.log`），随后去掉一次性native诊断program、不削弱已有owner回归；最终`/tmp/frontend-p2-live-json-minor-final.log`165例（48support+117邻居）全PASS/no skips/exit0、clone已清，Ruff0/freshLSP2confirmed0；不把该166冒充最后代码快照。sourcePASS不取消canary/真实写/同步baseline门禁。

0d0f57d 身份/readiness-only source 已PASS，minor归一化已修且165窄例通过；此前scope确仅允许无seed身份验证。m-3738951b-785 在短锁2492631读全lane/ownership后追加2份前端规范文档与2个隔离读回支持/测试路径，总53路径并读回，允许**下一工程步骤**在fresh parent/child/实际session门禁后仅向本轮clone提交随机TEST探针和readonly ApiKey再做真实认证GET，不是放量。当前尚未执行该seed；用户/cookie login/OAuth/Shops或Enum业务命令/浏览器写仍禁止，须相应后续范围与门禁。尚无authenticated隔离读回，更非持久化/wholeP2 GREEN；认证GET可能写last_used_at事实保留。未做：child/HTTP/canary真实写闭环、页面切换或交付。后续P2 fixed/prospective baselines保留，不把局部测试、源码审计或计划 PASS 当整体 GREEN。

F1 producer/consumer 已登记的当前写域（回执 m-badab46c-d24）：`scripts/{test_isolated.sh,_test_db_connections.py}`、`tests/conftest.py`、`tests/test_test_run_binding.py`、`tests/test_test_template_refresh.py`、`docs/guides/agent-testing.md`。`scripts/test.sh` 已在 DB URL 存在时不加载 .env.test，只验证新输出能原样传递，不预计修改它。已获计划复审/登记只允许此切片；真实 writable-fixture seed 仍须等实际门禁、child attestation/canary 支持实现并验证。

其他候选实现文件（不是现有写权）：`frontend/src/pages/{intercept-stats,shops,enum-map}/`；`frontend/src/{bootstrap.ts,bootstrap.test.ts,shared/contracts.ts,shared/api/client.ts,shared/api/client.test.ts,app/page-loader.ts}`；`frontend/{package.json,package-lock.json,NOTICE.md}`；`tts_erp_v2/api/{deps.py,v2/frontend_pages.py,v2/admin.py,v2/config.py}`；`tts_erp_v2/static/js/enum-map.js` 仅旧写请求补头；`tests/api/{test_frontend_pages,test_admin_shops,test_enum_map,test_cookie_csrf}.py`；上述 P2 live/support 文件及本计划。若组件/旧测试还需其他修改，先重新核对 owner 并扩展 scope，不能顺手改其他 lane。
