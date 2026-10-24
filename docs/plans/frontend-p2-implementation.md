# 前端 P2 实现计划：普通页面与首个隔离写闭环

> 状态：合同/计划门禁完成，进入 run-binding 基础实现；三页业务/API迁移尚未开始。窄测试只证明所覆盖切片，不是 P2 GREEN 或交付声明。
> 权威父方案：[渐进迁移方案](frontend-progressive-migration.md)；组件标准：[前端开发规范](../guides/frontend-development.md)。
> 用户在 P0/P1 交付后连续要求继续开发、直到任务完成：按既定前端路线先完成 P2，再逐批核对 P3–P6 的门槛；不接管其他会话或自动生产上线。

## 1. TaskStartSnapshot、必要性与边界

- Owner/session：`01a16719-eedc-7605-a9b1-1e16d356e488`，canonical board identity 相同；任务 `frontend-p2-contracts`。
- UTC：2026-10-24。独立 `feature/frontend-p2` / `.worktrees/frontend-p2`，从 fresh `origin/master=e86817a3f28d4267ce0527a43ed7887795da81c3` 建立。
- P0/P1 已完成，代码精确集成 `8f1b752` 的 fast 基线零新增稳定失败；不把其证据冒充 P2 通过。
- Change Necessity：三页仍依赖旧模板/脚本，复制了会话、路径、请求与布局逻辑；需接入既有 React/HeroUI 骨架，保留有效查询、管理和错误恢复。
- 初始 lane 仅本计划；现已按回执 `m-badab46c-d24` 登记并读回本计划、run-binding 的2scripts/conftest/binding与refresh tests/testing guide；`m-f3272c5e-978` 追加 no-dotenv bootstrap 的精确子进程透传修复。其余源代码、依赖、API guard/live支持变更仍须先扩展精确 scope 并读回。没有 child 写 reservation。
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
- 实际 npm 安装、审计、TS7/React19 构建和 StrictMode/unmount/resize 验证尚未进行。若不满足，记录具体不兼容证据再选择已维护适配，不强制 peer、降级 React 或偷偷复制上游代码。
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
3. 统计→店铺→枚举逐页实现与验证，每页独立激活提交；沿用后端唯一 `MIGRATED_PAGE_IDS`，不增加环境开关、第二套名单或缺资产 legacy fallback。
4. 所有测试唯一入口 `bash scripts/test_isolated.sh ...`。写入验收启动临时 API，证明它与浏览器实际连接本轮 `tts_erp_test_*` clone；不使用 live `:9877`，不从生产 `.env` 引入凭据。用 TEST_ 命名、数值字段遵守后端 schema 的隔离测试数据，禁止真实 OAuth/店铺网络变更。
5. 对真实 server 做 read-after-write、刷新、readonly/RW/admin、缺页面权限、CSRF、字段错误保留输入、重复点击单次提交、401/403/429/5xx与未知结果不重放。仅 mock 响应不足以证明持久化/权限闭环。
6. 浏览器覆盖空前缀和 `/tts`、375/768/1440 宽度、键盘/焦点归还、长值/空值/空分类、resize/unmount、坏 chunk。表格可局部滚动而全页不横溢；少量布局样式，不搬旧皮肤。原生产业务数据不进入截图/日志。
7. 窄回归后固定候选运行 baseline 一次；出现新失败只最小重跑分类，不重建 master baseline。独立审查、同步/推送 ready、精确 prospective-master 验证合并、回执和 cleanup 才完成 P2。

## 6. 保留与退役

P2 只逐页切换已验 React 页面。旧页面资源暂不在本计划写域内；只有消费者/回退条件确认且登记 scope 后才可删除。P3 的高权限/编辑器、P4 的复杂表格/SPU内核、P5 的产品冲突与专题交接分别审核；P6 必须等全部目标页、无旧消费者及回退期结束，开发指令不是生产上线或回退期验收。

## 7. 当前证据与未覆盖

已做：合同审计及 e404c10 计划独立 F1 复审 PASS（`/tmp/frontend-p2-plan-f1-review.md`），精确登记 `m-badab46c-d24`；按原 lock 私有 npm ci 准备工具，未新增图表依赖。实际 strict TDD：producer/保留名称4 RED；canonical preconnection2 RED（旧 probe 请求 fake engine）；非普通 proof/FIFO 真实阻塞 RED 后在读取 owner 非阻塞打开并 fstat 拒绝。对应日志 `/tmp/frontend-p2-run-binding-{red-reserved,consumer-red,fifo-red,checkpoint-green}.log`；24eb775 检查点的两文件窄回归66 passed及独立源码审查 PASS（`/tmp/frontend-p2-binding-implementation-review.md`）只覆盖其版本。后续补齐私有/畸形/oversize/type/symlink、模板/base/retained/URI参数、活跃非祖先/死 owner/start变化、实际返回engine零connect/实际库名错仅identity SQL、真实test.sh传播、clone/issuer/command/proof-cleanup失败与失效proof拒绝；`/tmp/frontend-p2-run-binding-full-matrix.log` exit0/111 passed无skip，Ruff0及fresh LSP3 confirmed clean。RB-01 的本机原生有界深JSON本来已拒绝；明确用decoder RecursionError故障注入观察边界RED，再在load owner归一化，未谎称原生默认解码器已复现该异常。790b135 上一次完整 baseline 如实 exit1/新增6（`/tmp/frontend-p2-binding-baseline-verdict.log`），均为旧 no-dotenv 子进程 allowlist 未传新 proof；最小文件同样6失败，非flake。按追加scope仅透传2个新env及有界timeout、整理旧import空行，原dotenv/no-read/export断言及新guard不变；`/tmp/frontend-p2-binding-dotenv-green.log` 的三文件窄回归exit0/117 passed无skip，Ruff0/LSP1clean。此后只有该测试消费者/说明变更，无应用或runner代码delta；没有重跑全套或改baseline，不能声称完整suite exit0。增量独立复审仍待完成，binding任务未完成，更非P2验收。
未做：图表依赖适配/审计、P2公共权限/业务API安全修复、三页业务实现、child/HTTP/canary真实写闭环、页面切换或交付。run-binding 仍 in_progress，补齐矩阵/实现复审后才解锁 core；不把局部测试、源码审计或计划 PASS 当整体 GREEN。

F1 producer/consumer 已登记的当前写域（回执 m-badab46c-d24）：`scripts/{test_isolated.sh,_test_db_connections.py}`、`tests/conftest.py`、`tests/test_test_run_binding.py`、`tests/test_test_template_refresh.py`、`docs/guides/agent-testing.md`。`scripts/test.sh` 已在 DB URL 存在时不加载 .env.test，只验证新输出能原样传递，不预计修改它。已获计划复审/登记只允许此切片；真实 writable-fixture seed 仍须等实际门禁、child attestation/canary 支持实现并验证。

其他候选实现文件（不是现有写权）：`frontend/src/pages/{intercept-stats,shops,enum-map}/`；`frontend/src/{bootstrap.ts,bootstrap.test.ts,shared/contracts.ts,shared/api/client.ts,shared/api/client.test.ts,app/page-loader.ts}`；`frontend/{package.json,package-lock.json,NOTICE.md}`；`tts_erp_v2/api/{deps.py,v2/frontend_pages.py,v2/admin.py,v2/config.py}`；`tts_erp_v2/static/js/enum-map.js` 仅旧写请求补头；`tests/api/{test_frontend_pages,test_admin_shops,test_enum_map,test_cookie_csrf}.py`；上述 P2 live/support 文件及本计划。若组件/旧测试还需其他修改，先重新核对 owner 并扩展 scope，不能顺手改其他 lane。
