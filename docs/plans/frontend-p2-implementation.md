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

来源：`templates/pages/shops.html`、`static/js/shops.js`；旧注释的 role 有矛盾，必须以实际服务端 gate 为准。

- 列表：`GET /v2/commerce/channel-accounts?platform=tiktok&limit=500`，保留 shop_id、名称、区域、service_id、App 凭证状态、开店日期、同步方式与状态；内部更新使用 `shop_pk`，不拿 external `shop_id` 当主键。
- 人工注册本地店铺记录，已有字段为空时补填，不覆盖既有凭证/状态；可选名称/区域/日期/service_id。不是 TikTok 店铺写操作。
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

优先核对 renderer 已有 request grant，把最小、后端拥有的权限事实加入受测试的 bootstrap（如 apiTier）；不新造 session provider/cache 或客户端授权真相，不改保留给其他 owner 的 access/middleware。UI 禁用仅为展示保护，最终 API gate、页面权限和 api_tier 不退化；未知权限 fail-closed。

仍需直接读取/确认：shops register/patch 的准确 role 与凭证条件；channel-account DTO/null；OAuth 返回与副作用；HTML/API-key/auth-off 的现有 gate；共享 client 的 204/422/CSRF/unknown-outcome；同轮 clone 的真实测试 API fixture。

后台 readonly 审计 `5d82ae14` 因 runner 消失以 `proof-write-failed` 失败，未产出有效结果。已报告；不当作审查通过、不静默切换 agent CLI。父会话继续源码核对，后续独立计划/安全审查仍是实际门槛。

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

已做：三页旧模板/脚本阅读、部分服务端源码阅读、公开官方/npm 复用资料核对、独立计划工作树登记。
未做：完整后端契约核对、依赖安装/审计、P2代码实现、任何P2测试、真实写闭环或P2切换/交付。所有后继任务保持依赖，不把本计划或失败委派记成 GREEN。
