# 前端 P2 实现计划：普通页面与首个隔离写闭环

> 状态：行为清单与契约核对中，尚未开始业务代码实现/切换；不是 GREEN 或交付声明。
> 权威父方案：[渐进迁移方案](frontend-progressive-migration.md)；组件标准：[前端开发规范](../guides/frontend-development.md)。
> 用户在 P0/P1 交付后连续要求继续开发、直到任务完成：按既定前端路线先完成 P2，再逐批核对 P3–P6 的门槛；不接管其他会话或自动生产上线。

## 1. TaskStartSnapshot、必要性与边界

- Owner/session：`01a16719-eedc-7605-a9b1-1e16d356e488`，canonical board identity 相同；任务 `frontend-p2-contracts`。
- UTC：2026-10-24。独立 `feature/frontend-p2` / `.worktrees/frontend-p2`，从 fresh `origin/master=e86817a3f28d4267ce0527a43ed7887795da81c3` 建立。
- P0/P1 已完成，代码精确集成 `8f1b752` 的 fast 基线零新增稳定失败；不把其证据冒充 P2 通过。
- Change Necessity：三页仍依赖旧模板/脚本，复制了会话、路径、请求与布局逻辑；需接入既有 React/HeroUI 骨架，保留有效查询、管理和错误恢复。
- 当前 lane 只保留本计划文件。任何源代码、依赖、测试或后端 guard 变更，必须先在 canonical lane 完整记录中扩展精确 scope 并读回。没有 child 写 reservation。
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

1. 父进程先复用 `scripts/_test_db_connections.py` 的有效参数解析，要求 wrapper 提供的确切 ephemeral clone，排除模板、prod、v3/shared 与不支持的 namespace；验证 engine 的有效身份，才连接并检查 `current_database()==expected`，之后才可写 TEST seed。拒绝先用新 DSN parser 猜库名或先造数后验证。
2. 子进程继承显式 allowlist，两个 DB 变量都是同一 canonical URL、enforce、测试 Fernet key、no dotenv，无真实 TikTok/生产变量。用 test-support factory 在启动实际 app engine/session 前后验证相同身份，并写无凭据的启动证明；identity 缺失/错误不能以 healthz 成功替代。没有新 production 诊断 HTTP 路由。
3. 在首次 API mutation 前，真实 GET 必须读到父进程已提交的不可预测 TEST canary。之后 UI写→独立 GET/DB读→reload；证明的不只是两份 env 文本相同。
4. test-support factory mount 实际 app，明确测试空前缀和 `/tts`；不同时用会造成双前缀的 uvicorn root-path。app factory 不启动 worker/scheduler；浏览器拒绝非 loopback 网络、callback/upstream，记录允许的 P2 method/path，而不是关闭所有写断言。
5. 硬失败 missing API/Chrome/identity/canary；有界 readiness、进程组清理。测试响应丢失场景必须先经真实 API commit，再丢弃/截断该响应；检查零自动 replay，而非只 abort 一个从未到达服务端的请求。

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

已做：三页旧模板/脚本及关键服务端源码阅读、恢复后的独立 backend contract audit、公开官方/npm 复用核对、计划工作树登记。审计只证明其读取到的合同，不代替独立计划/安全评审。
未做：依赖安装/审计、P2代码实现、任何P2测试、真实写闭环或P2切换/交付。下一门禁是本修订计划评审与精确 source/test scope 登记；后继任务保持依赖，不能把源码审计、本计划或失败委派记成 GREEN。

候选实现文件（不是现有写权）：`frontend/src/pages/{intercept-stats,shops,enum-map}/`；`frontend/src/{bootstrap.ts,bootstrap.test.ts,shared/contracts.ts,shared/api/client.ts,shared/api/client.test.ts,app/page-loader.ts}`；`frontend/{package.json,package-lock.json,NOTICE.md}`；`tts_erp_v2/api/{deps.py,v2/frontend_pages.py,v2/admin.py,v2/config.py}`；`tts_erp_v2/static/js/enum-map.js` 仅旧写请求补头；`tests/api/{test_frontend_pages,test_admin_shops,test_enum_map,test_cookie_csrf}.py`；上述 P2 live/support 文件及本计划。若组件/旧测试还需其他修改，先重新核对 owner 并扩展 scope，不能顺手改其他 lane。
