# ERP 前端工程化与渐进式迁移技术方案

> 状态：**实施中（P0/P1 首批候选）** · 2026-10-23。
> 用户已在文档合入后另行授权开发及 agent 并行；当前切片为 P0 地基和 P1 两个试点，证据见[实施计划](frontend-p01-implementation.md)。
> 已完成部分隔离组合验证；独立审查、baseline、Git交付状态以实施回执为准，不等于P2–P6或生产上线完成。
> 开发标准唯一入口：[前端开发规范](../guides/frontend-development.md)。

## 1. 目标、已确认决策与非目标

### 1.1 目标

将当前模板 + 页面级原生 JavaScript 逐步迁移为 **React + TypeScript + Vite + 免费开源 HeroUI**，
获得类型检查、热更新、组件复用和可维护的页面边界。以完整页面的可用结果验收，不以新增目录、
组件数量或构建成功代替迁移完成。

| 用户已确认的决策 | 对设计的约束 |
| --- | --- |
| 首要目标为维护效率，而非独立部署或整体视觉重做 | 初期同源、同包发布，不增加常驻前端生产服务 |
| 使用 HeroUI，不购买 Pro/付费模板 | 原生组件优先；缺失能力可独立实现，不复制未授权源码 |
| 不希望大量手写 CSS，不为旧布局深改组件 | 顺应原生组件调整布局；少量布局代码集中复用，不搬运旧皮肤 |
| 导航、Header、Footer 等必须复用 | 新页面共享唯一 AppShell；差异通过配置/插槽提供 |
| 页面专用内容允许定制 | 业务视图留在功能目录，不把共享骨架做成业务分支集合 |
| 业务能力与操作流程不应因换组件而丢失 | 按有效需求和契约核对功能、权限、数据及失败恢复，不要求像素一致 |

### 1.2 非目标

- 本轮不新增业务能力、不重定义 ROI/净利润/日期/告警口径、不改数据库结构。
- 不强制 SPA、React Router、Next.js、SSR、独立前端域名、CORS 放宽或 token 登录。
- 不改 Chrome 扩展、不变更其子模块指针；不接管并行 lane 的部署脚本、发布工作台或鉴权实现。
- 不从零开发一套设计系统，也不为了统一外观移除专业表格/编辑器必需的功能。
- 不把迁移当成发布授权；生产部署、停机预算和数据库操作继续按原流程由相应 owner/人工管理。

## 2. 基线与变更必要性

### 2.1 TaskStartSnapshot

| 项 | 记录 |
| --- | --- |
| 会话 | `01a16719-eedc-7605-a9b1-1e16d356e488` |
| 日期 | 2026-10-23 UTC |
| 开始时远端基线 | `cf95088`；文档工作树从登记后的 `1173bac` 建立 |
| 工作 lane | `chore/frontend-migration-docs`，独立 worktree；仅文档 |
| 当前改动所有权 | `AGENTS.md`、`docs/README.md`、本方案、前端开发规范；协调登记不属于业务文件 |
| 保留的外部 WIP | 主工作树 `plugins/monitor`、未跟踪 `node_modules/`；均不修改、不暂存 |
| Change Necessity | 当前大脚本与样式/DOM 耦合使复用、类型检查和交互回归成本增加；只换文件后缀不足以解决 |
| ArchitectureReviewRequired | 是；涉及页面所有权、构建产物、运行时路径和旧实现退役，但本次不改运行时 |
| TDD | 文档阶段不适用；实施阶段按风险为路径/授权/错误处理先写回归测试 |

这是设计时快照，不宣称已经同步未来 master，也不替代 lane 最终同步记录。

### 2.2 已读取的权威与实现依据

- 根目录 [AGENTS.md](../../AGENTS.md)：安全、前端计算和协作边界。
- [架构概览](../architecture/architecture-overview.md)、[External API](../api/external-api.md)
  的会话、页面、CORS、错误和路径契约。
- `tts_erp_v2/accounts/pages.py`：当前 **15 个页面**的标识、分组和页面权限唯一目录。
- `tts_erp_v2/api/v2/pages.py`：共享侧边栏、13 个 Jinja 模板的渲染入口；ROI/重点 SPU 复用同一模板。
- `tts_erp_v2/api/v2/ad_daily.py`：广告日明细的 HTML 内联于 Python，不能只扫描 templates 目录。
- `tts_erp_v2/api/v2/auth.py`：登录 HTML 同样不在 pages 模板目录；`/auth/me` 已提供会话信息。
- `static/js/spu-profitability-page.js`：约 3200 行，Tabulator 分组列/冻结列/服务端排序/行内钻取；
  `spu-roi.js`、`focused-spus.js` 已有共享页面内核，应保留这种所有权关系而非重新分叉。
- [vendored assets](../../tts_erp_v2/static/vendor/NOTICE.md)：Bootstrap、Tabulator、Tom Select、
  JSONEditor、uPlot 已有固定版本和许可证。
- [测试指南](../guides/agent-testing.md)、[测试域](../guides/test-domains.md)、
  [浏览器布局巡检](../guides/browser-ui-layout-audit.md)、`tests/browser/conftest.py`。

现状还没有根级前端 package.json、TypeScript/Vite 工程。新工程是新的展示/构建 owner，不是
新增业务规则 owner。禁止把当前源码注释中的“no SPA framework”“旧皮肤”等历史选择当成
所有后续页面的永久要求；旧页面在迁移前继续按原实现运行。

### 2.3 需求与实现冲突的处理

[盈利表现对比产品方案](../spu-profit-deterioration/01-product-proposal.md)已经确认 7/3 天对比、
不做阈值告警，但对应旧实现与部分技术文档仍有告警语义。此方案**不选择恢复旧告警交互，也不
顺带实施新业务需求**。该页迁移前必须由业务 owner 对齐有效需求、API 和测试，再迁移确定的
展示契约；这一页可后置，不阻塞其他页面的工程化。

视频发布等专题文档中的“继续原生 JS”属于原实施上下文。迁移该页前与其 owner 更新对应
技术说明，保留发布权限、状态机、确认和失败恢复；不借 UI 迁移新建或扩大店铺写能力。本次
不修改这些 owner 持有的文档或源码。

## 3. 路径比较与推荐

| 路径 | 收益 | 成本/风险 | 决策 |
| --- | --- | --- | --- |
| 只引入 TS/Vite，保留原生 DOM 大脚本 | 快速获得构建和部分类型能力 | 组件复用和复杂交互耦合仍未解决，HeroUI React 无法自然落地 | 不作为目标架构 |
| **按页面迁移的 React MPA** | 原 URL、页面授权和整页导航都保留；每批可单独交付 | 跨页切换会重新加载，短期新旧视觉不同 | **推荐** |
| 立即切换整站 SPA | 可统一客户端路由和跨页状态 | catch-all、直达链接、权限和回退面扩大，非当前目标必需 | 延后；不预装 Router |

这里 MPA 指每个 URL 仍由 FastAPI 明确处理，返回新或旧页面；新页面共享一个 React 应用入口
和 AppShell，页面模块按需加载。不是为每个页面建一套工程、Header 或网络客户端。

## 4. 目标模块边界

下面是**拟议目录**，不是已经创建的文件；具体小文件拆分由实施 owner 在此边界内决定。

```text
frontend/
  package.json / package-lock.json / tsconfig.json / vite.config.ts
  src/
    main.tsx                  # 读取 bootstrap，装配骨架和当前页面
    app/
      AppShell.tsx            # 唯一骨架；导航/Header/必要时 Footer 的组合
      navigation.tsx          # 渲染服务端菜单，不拥有权限矩阵
      page-loader.ts          # pageId → 页面模块的技术装配；按需加载
    shared/
      api/                    # fetch、路径、错误/取消契约；无 React UI 依赖
      components/             # 有真实消费者的共享组合，不复制全部 HeroUI
    pages/
      intercept-requests/     # 页面入口、API DTO/查询适配、页面专用组件
      dashboard/
      spu-profitability/      # ROI/重点 SPU 的共享功能模块
      ...
    styles.css                # Tailwind/HeroUI 导入和必要的集中主题规则
  dist/                       # 构建产物，忽略 Git，纳入发布包
```

依赖为 `main → AppShell + 页面`、`页面 → shared/HeroUI`。共享层不反向导入 pages，不建立通用
事件总线或复杂插件框架。页面私有请求/状态可放在该页目录，不强行挪入全局 store。

### 4.1 AppShell 与页面接口

- AppShell 接收服务端页面元信息、当前 pageId、页面标题/描述和操作区插槽、children。
- Header/Sidebar 为同一个组合 owner 的子组件，不分别为 SPU/采购/配置页面复制。
- 桌面折叠与窄屏导航集中实现，优先使用 HeroUI 的控件和 Drawer 等弹层能力；只有缺失的
  容器布局使用必要 Tailwind 类。页面不重新写侧边栏的宽度、断点、遮罩和焦点处理。
- 页面分页/footer 与应用 Footer 分开；全局 Footer 没有实际用途时不凭空新增。
- 现有已保存导航偏好应在一次性读取适配后进入新骨架状态；只保留界面偏好，不存凭据。
- 主题尽量使用 HeroUI 默认值。旧字体/暖纸 token 不与 HeroUI 建立第二套并行真相；迁移后
  浏览器测试的预期字体来自新规范，不能直接删除字体一致性断言来让测试通过。

### 4.2 状态与 API

- 先使用 React 局部状态/功能 hooks 和平台 fetch；不默认增加 Redux、全局查询缓存或表单框架。
  多页确有共同问题时再按复用规则评估维护中的依赖。
- 页面级 DTO 按真实 API 类型定义，公共分页、错误和会话 DTO 共享；不把所有响应强转 `any`。
  初期不建立全量 API 代码生成系统，避免随 UI 迁移扩大 API 契约改造。
- 公共客户端识别 204、非 JSON 错误、401/403/429/5xx 和中止，不把解析失败当成功或返回空列表。
- 写请求沿用 `X-Requested-With: tts-erp` 和原 Content-Type；multipart/文件上传保持其实际协议，
  不强制套 JSON。浏览器 `credentials: same-origin`，不注入服务 key，不扩大 CORS。
- 401 统一处理登录去向并防重定向风暴；403 就地展示权限不足；429 尊重 Retry-After。
  自动重试不得重放写请求，未知提交结果要求用户核查后再操作。
- 筛选切换要取消/忽略旧读取响应，不能让上一店铺的数据覆盖当前店铺；已有 URL 参数、返回定位、
  日期约束、输入保留、分页排序和刷新语义逐页建行为清单验证。
- 金额、ROI、汇率、阈值、聚合、服务端排序和范围规则不迁到浏览器重算。长 ID 保持字符串；
  null/零/负数以及简单展示计算按根规范处理。

## 5. 新旧共存、权限与路径

### 5.1 按现有页面路由选择 renderer

1. 保留 `/v2/pages/<id>` 的显式路由和原鉴权/handler dependencies。广告日明细虽定义在另一模块，
   也纳入同一迁移登记。
2. 后端增加小的页面 renderer 适配 owner（拟 `tts_erp_v2/api/v2/frontend_pages.py`），只负责已迁移
   pageId 的 HTML shell、bootstrap 和 manifest 读取。业务处理仍归原 API/module。
3. **后端只有一个显式的已迁移页面集合**，随版本发布；初始为空，每批只加入已验收页面。
   不另设浏览器开关、用户灰度开关、服务端自动探测和多个互相兜底的路由注册表。
4. 未迁移页使用旧 renderer；已迁移页使用 React renderer。缺 manifest、entry 或 bootstrap 是
   部署/构建错误，必须显式失败并阻止发布，不能悄悄降级成旧页。
5. 页面间用真实同源链接整页跳转，保留 query 参数和直达/刷新；没有吞掉 API 路由的 SPA catch-all。
6. **同一 HTML 文档只运行一套页面 owner**：新页不再加载对应旧页面脚本、Bootstrap、旧侧边栏
   注入或旧通用 CSS；不通过多 root、iframe 或旧 DOM 与 React 同时写同一节点长期混合。

### 5.2 Bootstrap 与导航的单一来源

服务端 shell 提供最小 JSON bootstrap（拟协议）：

| 字段 | 责任 |
| --- | --- |
| `rootPath` | 来自 `request.scope['root_path']`；本地可为空，代理部署为 `/tts` 等 |
| `pageId` | 已被路由及原权限检查接受的当前页面 |
| `navigation` | 从 `accounts/pages.py::PAGES` 及已有 AccessGrant/用户页面集生成的可见项，包含 id、label、group、href |
| `releaseId` | 发布包标识，用于定位前端资源问题；不包含凭据或用户数据 |

菜单标签、分组和权限仍由后端页面目录拥有；前端 page loader 只拥有 pageId 到代码模块的映射，
不再硬编码第二份菜单或权限规则。后端还未迁移的入口也可出现在菜单中，链接会打开旧页。

- JSON 必须通过安全序列化嵌入 `application/json` 数据节点，转义 `<`/脚本终止序列等，不用
  字符串拼接执行脚本、不用 `dangerouslySetInnerHTML` 渲染上游内容。
- bootstrap 不含 cookie、API key、店铺会话、凭据或服务器环境配置；不把它持久化进构建文件。
- 带权限菜单的 HTML 使用 `Cache-Control: private, no-store`，不能被代理跨用户缓存。
- 会话信息继续复用 `/v2/auth/me`；它的未登录响应可能是 **200 + authenticated:false**，不能只按
  HTTP 401 判断。对 auth off/API key 的原有页面使用方式，保留已有 AccessGrant 语义，不能因
  `/auth/me` 没有 cookie 而对合法的非 cookie 页面访问制造登录死循环。
- 前端菜单可见性不是授权：原页面权限、api_tier、例如 sync-jobs 的 readwrite handler gate，以及
  users/video-publish 的专属 API 权限都保持。会话吊销以后端下一次请求结果为准，不用缓存授权兜底。

### 5.3 根路径、登录和资产

- `rootPath` 由服务端提供；所有 API、页面链接、登录 next 和静态资源使用同一个幂等路径工具，
  测试空前缀、`/tts`、已带前缀的 next 和 query 编码，禁止 `/tts/tts` 与丢前缀。
- 初期保留 `/v2/auth/login` 的现有服务端页面、登录限流、cookie Path 和 open-redirect guard；
  后续如迁移登录 UI，只替换展示，不改登录/退出/改密端点或 session 生命周期。
- 不更改 `tts_erp_v2/app.py` 的 middleware 顺序，不将页面权限检查换成客户端路由守卫。

## 6. 开发、依赖和发布产物

### 6.1 复用证据与版本选择

截至 2026-10-23 的官方 npm `latest` 元数据快照如下，**仅为候选，不是已通过构建或安全审计的
锁文件**。实施 P0 需选同一组兼容稳定版本，核对 Node/TS/类型/测试工具兼容、审计依赖和提交锁文件。
不执行 `latest` 无约束升级，也不把包的许可证等同于所有示例/Pro 内容的授权。

| 包 | 候选版本 | 许可证 / 上游 |
| --- | --- | --- |
| react / react-dom | 19.3.1 / 19.3.1 | MIT · [React](https://github.com/facebook/react) |
| @heroui/react / @heroui/styles | 3.2.6 / 3.2.6 | MIT · [HeroUI](https://github.com/heroui-inc/heroui) |
| vite | 8.3.5 | MIT · [Vite](https://github.com/vitejs/vite) |
| @vitejs/plugin-react | 6.1.2 | MIT · [Vite React 插件](https://github.com/vitejs/vite-plugin-react) |
| tailwindcss / @tailwindcss/vite | 4.3.3 / 4.3.3 | MIT · [Tailwind](https://github.com/tailwindlabs/tailwindcss) |
| typescript | 7.0.2 | Apache-2.0 · [TypeScript](https://github.com/microsoft/TypeScript) |

HeroUI 3.2.6 的 peer 要求包括 React/React DOM ≥19、Tailwind ≥4，以及 React Aria 相关 peers；
不能只安装两个 HeroUI 包就假定 peer 全部满足。Vite 8.3.5 声明 Node `^20.19.0 || >=22.12.0`。
Node 采用实施时仍受支持且兼容的 LTS，并固定开发/构建版本；TypeScript、React 类型和测试工具
需要共同编译验证，不盲目追最新大版本。

- [HeroUI Quick Start](https://heroui.com/en/docs/react/getting-started/quick-start)、
  [Styling](https://heroui.com/en/docs/react/getting-started/styling) 和
  [Table](https://heroui.com/en/docs/react/components/table) 为原生能力参考。
- [Vite Backend Integration](https://vite.dev/guide/backend-integration.html) 为开发客户端注入及
  manifest 产物消费参考；采用官方机制，不自行开发打包器或热更新协议。
- npm 核查入口示例：[@heroui/react](https://registry.npmjs.org/@heroui/react/latest)、
  [vite](https://registry.npmjs.org/vite/latest)。版本、peer 和许可证记录在 P0 的依赖清单中，不能仅引用本快照。

### 6.2 开发模式

采用 Vite 官方后端集成：浏览器仍从本地 FastAPI 的受保护页面 URL 进入；显式开发配置才注入
loopback Vite client/entry 获得 HMR。React 插件需要的 Fast Refresh preamble 按该版本官方后端集成
方式加载，不能只注入入口就假定热更新可用。API、cookie、页面权限和 bootstrap 仍由同源 FastAPI 处理。

- Vite 与本地 FastAPI 的 dev origin/Host allowlist 只允许必要的本地地址，不开启公网 wildcard。
- 需要在空 rootPath 和代理前缀两种本地测试场景验证 WebSocket、动态 import、字体和图标。
- 不向 Vite 配置注入服务端 secrets；`VITE_*` 是公开浏览器值，不得放入密钥。
- 生产模式只读 manifest，不允许回落连接开发服务器。开发模式不能自动从“manifest 不存在”推断。

### 6.3 生产构建与交付

- 只构建一次，经 typecheck/build 后将 `frontend/dist`（含 Vite manifest、entry/chunks/CSS 和必要
  资源）纳入**同一版本包**；前端由 FastAPI 托管，不新增 Node/Vite 生产进程或联网安装前端依赖。
  现有 sync/publish 等进程的职责不因前端工程化改变。
- 前端产物使用相对资源基址；服务端按 manifest 和 request rootPath 生成带前缀的资源地址。
  初始模块、CSS、modulepreload 和动态 chunk 都要验证，不能只验证首页 JS。
- 产物拟安装到 `/static/frontend/<releaseId>/` 对应的发布目录；manifest 与后端 renderer 使用
  同一个 releaseId。实际磁盘拷贝和校验归发布构建 owner，不在 UI 层另造 deploy.sh。
- 静态 hash 资源可 immutable 缓存，个性化 HTML 不缓存。发布包缺 entry、依赖 chunk、CSS 或映射
  不一致时 preflight 失败，未通过前不切流；绝不能返回 200 的空 React 容器冒充页面成功。
- 旧发布资产不能随一次新发布立即清空。至少保留当前与上一成功发布所需资产，回退窗口和额外
  保留量由发布 owner 在上线前记录；旧 tab 遇已回收 chunk 时明确提示刷新，不自动重放任何操作。
- 不在运行时访问第三方 CDN 或下载字体；沿用现有自托管安全边界，依赖许可说明随包保留。

部署方案并行设计尚未确定停机预算，本方案仅定义前端产物接口，不声称实现原子切流或零停机。

## 7. 渐进迁移批次

每批都应有独立 lane、最窄验证和可回退提交；前一批验收后再扩大集合，不按页面数量硬定工期。

| 批次 | 范围与交付 | 进入条件 | 退出/验收条件 |
| --- | --- | --- | --- |
| P0 工程地基 | 免费依赖/lock、TS/Vite、bootstrap/路径/client、manifest renderer、测试和构建入口 | 技术方案批准；协调共享热点写队列 | 不切生产页面；构建产物闭合，空/有前缀、会话/错误负例可验证 |
| P1 完整试点与复用证明 | `intercept-requests` 完整只读查询/筛选/详情/分页，随后 `dashboard`，共享同一 AppShell | P0 通过，逐页行为清单齐全 | 两页在隔离环境通过；跨新旧导航正确；Header/导航修改一次两页生效，无重复骨架 |
| P2 普通页面与首个写闭环 | `intercept-stats`、`shops`、`enum-map`；按页面切换而非同时放量 | P1 通过；统计图表原生/第三方缺口有结论 | 原有效查询/写能力、只读禁用、字段校验、CSRF、失败输入保留、无重复提交均验证 |
| P3 运维与高权限页 | `sync-jobs`、`users`、`intercept-configs`、`runtime-configs` | 对应 owner 可交接；权限和专业编辑器行为清单确认 | api_tier/页面权限不退化；配置编辑/脱敏/确认无损；专业控件归属明确 |
| P4 复杂经营分析 | `ad-daily`、`manual-costs`，以及一起迁移的 `spu-roi`/`focused-spus` | 表格/选择器能力验证完成，盈利口径不变 | 列/排序/筛选/钻取/精度/图片/编辑与返回定位验证；不复制盈利页面内核 |
| P5 独立专题尾部 | `spu-profit-deterioration`、`video-publish`，以及视情况最后迁移的登录展示 | 需求与实现冲突已由 owner 解决；相应专题交接 | 仅迁移确认后的有效契约，权限/状态/确认/恢复不变；登录安全不变 |
| P6 退役与收口 | 删除已迁移页面的旧模板/脚本/样式与无消费者的 vendor、旧 renderer/迁移集合 | 所有目标页完成且回退期结束；消费者清单为空 | 新页面只有一套骨架/客户端；无旧资源引用，无永久兼容分支，文档和测试入口完成更新 |

**试点不是 Hello World。** `intercept-requests` 已有 GET 列表/详情、筛选和分页，适合检验读路径；
第二个 dashboard 用来检验不同页面是否真能共享骨架，不能等迁完整站才发现公共组件实际不可复用。
首个写操作在隔离测试数据上完成，不拿生产配置或真实发布任务测试防重复提交。

### 7.1 复杂控件的能力验证

P0/P1 建工程，P4 以前另做小范围、可丢弃的真实能力验证；不把试验 adapter 当长期架构：

| 现有能力 | 原生优先验证 | 不满足时的处理 |
| --- | --- | --- |
| ROI/重点 SPU 表格 | HeroUI Table 的分组列、冻结首列、远程排序、行展开、真实宽表和密集数值 | 先调整列呈现/钻取布局，但不删除信息或改客户端排序；仍不足则评审免费专业库的窄适配，不重写表格引擎 |
| SPU 多选/搜索 | HeroUI Select/ComboBox 等适用能力、键盘、长标签和实际选项规模 | 只补实际缺口；不能为了旧 Tom Select 外观复制其行为 |
| JSON/结构化配置 | 原生字段组合能否表达现有编辑/校验/脱敏要求 | 有真实编辑器需求时复用维护中的开源控件，限定在配置功能模块 |
| 统计图表/盈利瀑布 | 用已有数据与组件组合能否保持含义、标签和可读性 | 业务图允许页面定制；已有 uPlot 等可评估复用，不用 HeroUI 缺图表作为自造图表引擎理由 |

判断依据必须是代表性载荷、键盘/窄屏交互和性能测量，不是空表截图。若必须保留某旧专业库，
仅允许独立 DOM 容器、明确 mount/destroy、无跨节点写入和无并行状态真相的窄适配，并记录退出
条件；如果该例外超出已批准设计则先评审，不默认为永久双栈。并非每页都强制“所有东西 HeroUI”。

## 8. 验证策略与测试入口

### 8.1 分层验证

| 层 | 主要覆盖 | 机制 |
| --- | --- | --- |
| 类型/构建 | DTO、props、模块依赖、manifest 与全部依赖资源闭合 | 拟 `npm ci`、`npm run typecheck`、`npm run build`；这些脚本在 P0 创建，当前不可执行 |
| 前端单元/组件 | 路径幂等、错误解析、取消/迟到响应、骨架复用、弹层/表单交互 | P0 评估并锁定 Vitest + Testing Library 等维护中工具，接入隔离 wrapper 后运行 |
| 后端页面/鉴权 | renderer 选择、原 handler gate、bootstrap 转义/no-store、302/403、缺产物失败 | 现有 API/鉴权测试域，使用测试会话与显式 rootPath |
| browser | 当前分支构建产物、mock 业务响应、真实渲染/键盘/截图/布局 | 扩展现有 `tests/browser/`，保持进 fast，不能只继续打开旧 Jinja 而宣称新页被覆盖 |
| 隔离全链路 | 会话登录/撤销、授权、真实 API 数据与读写闭环 | 临时 API 必须连接该轮 clone；只用 TEST_ 数据，服务基址与凭据显式注入 |

**入口约束：** `bash scripts/test_isolated.sh ...` 仍为 agent 唯一测试入口。P0 必须先在原 runner
中接入前端测试及前端构建准备；拟新增 `frontend` 选择器，并将快速组件回归接入 baseline 所用
fast 流程。该选择器目前不存在，不能直接运行或绕过 wrapper 执行 npm test/Vitest。
`npm ci/typecheck/build` 是安装/静态构建，不是测试入口替代品。

现有可用窄选择包括 `bash scripts/test_isolated.sh browser`、`api`；代码/测试变更最终执行
`bash scripts/test_baseline.sh`，不要另跑整套 fast 去重建 baseline。只对新失败最小重跑一次。
前端测试接入不能使新 React 页面从 baseline/browser 门禁消失；构建失败应使测试失败而非跳过。

`tests/e2e/` 当前部分用例默认连接 live `:9877`，wrapper 创建 DB clone **不等于那个 live 服务
也切换到了 clone**。不得用默认 live 地址验证 mutation。P0/P2 须在现有隔离 fixture 模式下显式
启动测试 API 并注入该轮 DB/测试凭据，不读取生产 `.env`；如无法隔离就阻止相应验证而非降级。

### 8.2 必须覆盖的验收矩阵

- 路径：无前缀、本地模拟 `/tts`、直接进入深链接、刷新、query、登录 next、动态资源和前缀幂等。
- 身份：匿名 HTML 302/登录返回、正常会话、readonly/readwrite/admin、缺页面权限 HTML 403、
  会话过期/撤销、auth store 失败，以及既有 auth off/API-key 页面访问语义。
- 数据：正常、空、缺字段、null/零/负金额、长 ID/中文/越南语长名称、无图、较大列表；
  比对权威服务端值，不在浏览器“修正”业务结果。
- 请求：204、非 JSON 错误、401/403/429/5xx、慢响应/取消/切店竞态、失败保留输入、未知写结果
  不自动重试；纯读页网络记录无意外写请求。
- 交互：菜单与当前页高亮、折叠偏好、键盘 Tab/Enter/Escape、弹窗焦点归还、主操作可见且反馈明确。
- 布局：建议代表视口 375/768/1440/2560；宽表可局部滚动，全页不意外横溢，长标题不挡操作。
  原视觉基线按已接受的 HeroUI 风格更新，不以“允许布局变化”删除所有截图/字体断言。
- 复用：至少两个不同页面共用骨架；代码与 DOM 证据确认没有第二份 nav/header/footer；改一处全生效。
- 资源：新页不加载旧页面脚本/Bootstrap，旧页继续正常；生产网络无 Vite/CDN/字体外链；坏 manifest
  阻止切流，页面初始化/动态 chunk 失败有可见提示，不长期空白。

每页维护简短行为清单、请求/响应固定样例和截图/交互证据。清单归该页功能测试/迁移提交，不新建
第二份业务口径；删除旧选择器断言前，必须用新组件语义补上同等行为断言。

## 9. 切换、回退与旧代码退出

### 9.1 每批切换步骤

1. 从当前 master 起独立 lane，核对目录/页面 owner；登记该页行为清单和未迁移消费者。
2. 完成新页面及测试，在隔离环境用拟发布产物验证，再将 pageId 加入后端迁移集合。
3. 同步 master、窄测试与 baseline、提交/推送；由发布 owner 用同版本后端+manifest+资源交付。
4. 观察错误、缺 chunk、授权拒绝和真实操作结果；不记录 token、cookie、完整敏感业务载荷。
5. 本批验收后清理对应旧模板/页面脚本；只保留其他未迁移页仍需的资源，记录其最后消费者和 P6 退出门槛。

### 9.2 回退契约

- 迁移阶段不改业务表和 API 契约，所以 UI 回退不需要数据回滚，不撤销已经完成的业务写入。
- 首选回退到上一已验证的完整发布包；局部 UI 问题也可通过受控提交撤回该页迁移登记，前提是该
  版本仍有经过验证的旧实现。两者都必须明确版本，不在运行时静默 fallback。
- 发布后如果旧实现已经删除，需要回退完整旧发布包而非随意切一个前端开关。
- 回退时校验页面/manifest/asset 同版本与原鉴权，保留回退所需 hash 资产；页面初始化失败提示
  刷新/联系管理员，不能自动提交、重复执行或恢复未知结果的 mutation。
- P6 仅在所有页面验收、所有旧消费者退出、回退期结束后执行；保留 Git/发布包回退能力，不永久
  保留两套运行时、Bootstrap 皮肤或“临时”大 adapter。

## 10. 所有权、风险与评审结论

### 10.1 实施前需要协调的热点

`tts_erp_v2/api/v2/pages.py`、`ad_daily.py`、`auth.py`、`app.py`、`accounts/pages.py`、`access/`、
`tests/browser/conftest.py`、`scripts/test_isolated.sh` 及底层 runner/发布脚本等须按 ACTIVE.md
重新核对当前 owner。本文描述未来修改面**不等于认领这些文件**；一个 writer 改共享 renderer，
页面 lane 通过其公开接口接入，不能多 writer 同时改骨架。

| 风险 | 设计防线 | 能否局部阻塞 |
| --- | --- | --- |
| 只换控件却复制公共代码 | AppShell 单 owner、两页复用验收、不机械包装 | P1 不通过不扩大迁移 |
| 权限/前缀因 SPA 改造退化 | MPA 原路由/gate、rootPath bootstrap、负向测试 | P0 阻塞 |
| 样式串扰 | 同文档不加载双皮肤、原生主题优先 | 单页阻塞 |
| Tabulator 等替换损失业务功能 | 能力验证与例外评审，ROI/重点 SPU 共用内核 | 只阻塞 P4 相关页 |
| 旧告警业务与当前产品需求冲突 | 业务 owner 对齐后才迁移，禁止借迁移追认旧功能 | 只阻塞 P5 相关页 |
| Node/测试工具/包版本不兼容或依赖风险 | P0 锁版本、peer/许可证/审计和干净构建验证 | P0 阻塞 |
| 发布包缺失或新旧资源错配 | manifest preflight、同包版本、保留可回退资产 | 阻止发布，不运行时兜底 |

### 10.2 评审范围与完成条件

**标准及方案方向已获用户认可，P0/P1 正在实施。** 认可的方向包括页面级 React MPA、后端单一迁移
集合、同源会话与 rootPath、安全 bootstrap、唯一 AppShell、原生 HeroUI 优先、P0→P6 分批及上述
验收/回退门槛。阶段性类型/构建、热更新、页面和权限证据见实施计划；不能外推到所有其他页面
或生产发布。具体 DOM、函数名、目录内小文件拆分属于实施决定；付费模板、全站 SPA、业务规则
变化或永久双栈不在默认授权内。

本次文档完成条件：规范可被开发入口发现、方案覆盖现状/差异/所有权/风险/验收/退出，链接与
路径可验证；**不等于依赖已安装、测试已执行、页面已迁移或生产已上线**。
文档先行阶段已结束，用户已授权开始开发。当前按独立P0/P1切片实施，后续仍须逐批确认实际
写队列并通过对应验证门槛；开发授权不是生产部署或无限扩大范围的授权。全部迁移落地后再按
文档目录规则将本方案移至 `docs/design/` 并更新状态与引用，不提前写入“已接受/已实施”的 ADR。
