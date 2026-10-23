# 前端 P0/P1 实施计划与检查点

> 状态：实施中 · 2026-10-23。用户已授权开发和 agent 并行。
> 父方案：[渐进式迁移技术方案](frontend-progressive-migration.md)；标准：[前端开发规范](../guides/frontend-development.md)。
> 本片交付 P0 工程地基和 P1 的 `intercept-requests`、`dashboard` 两个完整试点，不宣称 P2–P6 或生产上线完成。

## 1. 目标、架构、技术栈

- 目标：建立可编译、可隔离测试、能复用的免费 HeroUI 前端；两个真实页面共享唯一 AppShell。
- 架构：保留 FastAPI 明确页面路由和原 gate，React MPA、普通同源链接；没有 catch-all/客户端权限真相。
- 技术栈：React 19、HeroUI 3、Tailwind 4、Vite、TypeScript；Vitest/Testing Library 负责组件回归。
- 新代码的必要性：原生 DOM 大脚本和旧模板不能直接复用 React HeroUI。最小新 owner 为 frontend 工程、
  纯网络/路径层与安全页面 renderer；不向 app.py/access 或旧 pages.py 塞新业务逻辑。
- 复杂度约束：pages.py 仅接线；公共控件直接用 HeroUI，不预建万能组件库；页面 DTO/交互留在页面。

## 2. 基线、授权与快照

| 项 | 记录 |
| --- | --- |
| Parent session | `01a16719-eedc-7605-a9b1-1e16d356e488` |
| 基线 | `a648813`；登记/文档协调后父工作树从 `f67a7b8` 建立 |
| Parent branch/worktree | `feature/frontend-p01` / `.worktrees/frontend-p01` |
| TaskStartSnapshot | task 树 clean；主树已有 `plugins/monitor` 和未跟踪 `node_modules/`，不读改/暂存其 WIP |
| 用户授权 | “开发吧…调度 AGENT 进行并行”；不含生产重启、部署、数据库迁移或扩大业务能力 |
| ArchitectureReviewRequired | 是：新 renderer/bootstrap、构建产物和迁移接线；沿用已确认父方案，不改变授权 owner |
| 权威文档 | AGENTS、frontend-development、父方案、architecture-overview、external-api 会话/路径、agent-safety/testing/git-workflow |
| 已知不一致 | 旧 healthz 仅 liveness，不证明 DB/MinIO/调度健康；试点不再硬编码绿灯，未知明确标注 |

前端原生能力与版本先核实官方文档/npm，提交精确依赖与 lock；运行兼容、安全审计属于 P0 证据，
不能以父方案的候选版本快照当成实测通过。

## 3. TDD Route 与验证边界

- Mode：auto；**TDD Route：strict**，由 parent writing-plans 路由选择。
- 原因：共享路径/client/bootstrap、权限边界、页面迁移、异步行为均有显著回归风险。
- 先明确期望，写测试、观察真实 RED，再写最小行为实现并 GREEN；配置/类型声明/安装不属于行为 TDD。
- 所有测试只经 `bash scripts/test_isolated.sh ...`。不直接跑 pytest/Vitest/npm test。
- npm ci、typecheck、build、audit 是安装/静态检查，可直接运行，但不能替代组件/权限/浏览器测试。
- 前端测试采用 `tests/frontend/` 的 pytest adapter，在隔离 wrapper 注入 clone 后调用本工作树 Vitest。
  `frontend` 选择器复用 domain_frontend；fast 仍收集 adapter，使失败落在 baseline 可识别的 pytest item 上。
- 前端子工作树由 parent 提供同一基础配置/接口/测试 adapter 后，worker 才开始执行组件测试。
- 相关 API 用 `api tests/api/test_frontend_pages.py`；browser 使用新产物与 mock，不连接 live :9877。
- 最终一次 `bash scripts/test_baseline.sh`；只针对新增失败最小重跑，不更新 master baseline。

RED/GREEN 只证明对应局部行为；P1 完成还须有实际构建、浏览器、新旧导航、权限/前缀与两个消费者复用证据。

## 4. 固定的 producer/consumer 接口

### 4.1 TypeScript

`frontend/src/shared/contracts.ts`（parent 所有）：

```typescript
interface NavigationItem { id: string; label: string; group: string; href: string }
interface FrontendBootstrap {
  rootPath: string;
  pageId: string;
  navigation: NavigationItem[];
  releaseId: string;
}
interface PageProps { bootstrap: FrontendBootstrap }
```

- DOM 数据节点 `frontend-bootstrap`，React mount 节点 `frontend-root`。
- `requestJson<T>(rootPath, path, init?) -> Promise<{data:T, headers:Headers, status:number}>`。
  `ApiError` 带 status、可选 retryAfter/requestId；204 不 JSON 解析，不自动重试写请求。
- `pathFor(rootPath, path)` 负责同源路径与前缀幂等；拒绝跨域/协议相对/反斜杠逃逸。
- 页面 default export 接收 PageProps，只返回内容，不复制导航/Header。
- AppShell default export 接收 bootstrap/title/description?/actions?/children；不反向导入页面。
- 页面元信息和 lazy loader 归 parent；菜单标签/分组/可见性只由后端 PAGES/授权上下文提供。

### 4.2 Renderer 与构建产物

- build 生成 `frontend/dist`，附同版本 Vite manifest；parent 构建脚本把派生产物装入本工作树
  `tts_erp_v2/static/frontend/<releaseId>/`，并写派生选择文件 `static/frontend/release.json`。
- release.json 最小形状 `{releaseId}`；manifest 为 `<releaseId>/.vite/manifest.json`，entry key `src/main.tsx`。
  releaseId/所有 manifest 路径须受限于合法资产目录，递归检查 import/CSS/资源闭合。
- URL 为 request rootPath + `/static/frontend/<releaseId>/<asset>`；不改既有 /static mount。
- 个性化 HTML private,no-store；bootstrap 安全序列化，不暴露 cookie/secret，不执行上游 HTML。
- 缺少/损坏构建产物是明确失败，不静默回退旧页；新页面不得同时加载旧脚本/Bootstrap。
- 显式 loopback-only `TTS_ERP_FRONTEND_DEV_ORIGIN`，dev releaseId=`dev`；按实际 React 插件方式加载
  Fast Refresh preamble。生产不能根据缺 manifest 自动连 dev server。
- 唯一迁移集合 P0 先空；P1 两页浏览器验收后由 parent 在这个集合启用，不增加第二套开关。

## 5. 并行 writer 与任务

三个 child 均从 a648813 的 clean checkout 开始，先只读报告真实身份，再登记。同一 parent lane 内
host-managed patch checkout，不是绕过 ready 前驱规则创建独立发布 lane；Git 生命周期只归 parent。

| 任务 | writer | 精确范围 | 最小验收 |
| --- | --- | --- | --- |
| 工程/client/runner/集成 | parent `01a16719…` | frontend 顶层配置/lock、shared、main、page-loader、styles、build helper、tests/frontend、runner、必要 browser fixture 和文档 | 依赖可安装，类型/构建闭合，路径/client负例和隔离门禁有真实 RED/GREEN |
| 共享骨架 | `01a16865-2e15-734a-9c37-c03c9922f691` | `frontend/src/app/AppShell.tsx`、`AppShell.test.tsx` | 服务端菜单/当前项、共享页头插槽、折叠偏好、窄屏 Drawer、焦点与长标签 |
| 两个页面 | `01a16865-2daa-74e5-8945-511bfbf7a386` | `frontend/src/pages/{intercept-requests,dashboard}/{index.tsx,index.test.tsx}` | 实际列表/筛选/分页/详情/错误/迟到响应；dashboard coverage、X-Total-Count、健康/同步与权限链接 |
| Renderer | `01a16865-2e99-713f-8222-ff0287fc57c3` | `api/v2/frontend_pages.py`、`pages.py` 两页最窄接线、`tests/api/test_frontend_pages.py` | 安全 bootstrap、原 gate、前缀、manifest integrity、负例显式失败；不改 app/access |

各 child 先写测试并取得 RED 再实现；parent 提供基础 scaffold，不让每人重复装不同版本或发明接口。
独立副本中不互写；收取补丁后才由 parent 在集成副本组合，不读取其他 lane 的未提交 WIP。

## 6. 顺序、验收和审查

1. 完成登记、接口与基础工具链；读取/核实依赖许可证、peer、维护状态和安全审计结果。
2. 后端可独立在 tmp assets 上做 renderer TDD；前端 scaffold 准备好后 shell/pages 并行 TDD。
3. Parent 在自己的副本完成 path/client/entry/build helper；收到 child 结果时检查实际 diff，而非相信口头 DONE。
4. 两页组合、构建派生产物、浏览器测试；保留服务端当前页面授权及 auth off/API key 的原有语义。
   `/auth/me` 返回 authenticated:false 不等于所有访问模式都应跳转登录。
5. 按父方案真实场景覆盖前缀/no prefix、匿名/会话/缺权限、204/401/403/429/5xx、取消/竞态、长 ID、
   手机/桌面视口、键盘/弹层、无双皮肤、导航到旧页及返回。
6. 独立 spec reviewer 对固定组合候选检查父方案一致性；问题修复并复核通过后，再由另一 reviewer 做质量审查。
   两阶段都不能用实现者自评代替；未解决 finding 保持未完成。
7. 窄测试、主动诊断、一次 baseline、无 secrets/无 foreign staging 检查，提交/push，按当前主线规则同步/集成。

## 7. 兼容、退役和停止条件

- 保持旧 URL/业务 API/权限 owner/精度/日期口径；不改 app.py/access、不做数据库/生产操作。
- 新页所有内容由 React 管；旧页继续旧 renderer。仅迁移集合决定选择，不保留临时自动 fallback。
- 旧两页模板/脚本须按实际最后消费者逐步退出；更新 browser fixture 后才能删除，不能为让测试绿而
  放弃行为覆盖。未迁移页面仍使用的 vendor/tokens 不删除；整体退役仍属 P6。
- 旧全站健康绿灯无真实数据支持；保留上下文但显示未知，不新增健康 API，不伪造健康成功。
- 若必须修改 app/access，停止该窄修改、向对应 owner 交换补丁；其余独立工作继续。
- 若测试 clone/依赖不可用，按 agent-testing 检查并修复，不换数据库或直接 runner。
- P0/P1 结束只报告实际已验证的片；P2–P6 保留为明确后续，不声称整站迁移或部署完成。

## 8. 当前检查点

- 2026-10-23：父/child 登记已在 `f67a7b8` 推送；child scope 已确认，renderer 的测试入口依赖已准备。
- shell/pages 已收到统一 scaffold 后并行实施：骨架 8 例、页面 19 例由 child 取得 RED/GREEN。renderer 69 例局部回归及 pages.py 纯接线修正已由 parent 收取；不采 host 暂存的格式化旧字节或 node_modules 链接。
- Parent 的路径/client、bootstrap、产物打包、会话退出和统一401回调均有隔离 RED/GREEN。首次 npm 因继承 NODE_ENV=production 省略 dev 依赖；修为显式 --include=dev，测试 adapter 固定 NODE_ENV=test，不绕过隔离入口。
- 两页在真实 Chromium 中覆盖375/1440、空前缀与/tts、表单/详情/分页/导航、失败提示；Vite dev origin、React刷新preamble及一次实际模块HMR更新通过。组合窄选择119个pytest item通过，日志 `/tmp/frontend-p01-activation-narrow.log`。未运行生产服务。
- 已在唯一迁移集合启用 dashboard/intercept-requests。旧模板/脚本暂仅供离线probe及未迁移消费者，已迁移实际HTTP文档不加载旧皮肤；P6迁移这些工具的最后消费者后移除，不声称旧资源已全退役。
- 主线64acba9已将lane登记迁入主仓board；本lane当前完整记录为m-5a128171-185，保留真实child归属及app/access排除。后续不再维护ACTIVE或另起worktree分板。
- 当前准备固定review候选：独立spec/质量审查、一次baseline与Git交付尚未完成。依赖类型/构建与npm audit通过，bundle许可证采用Vite原生build.license随产物输出，不新增许可证生成框架。
