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
- 原固定候选8f04534的spec独立审查提出三项阻断：选版前完整manifest闭包、初始entry/eager模块失败不可空白、Retry-After不可缩短。已先观察真实RED（`/tmp/frontend-p01-review-findings-red.log`），补齐闭包/路径/列表含null检查、纯HTML可访问startup提示（React成功挂载移除）、保留长限流等待及本生命周期可取消的单调时钟分段timer；不新增全局重试调度器。
- 修复组合131项先通过（`/tmp/frontend-p01-review-findings-green.log`）；补充null-list反例取得RED后修复。最新132项组合的业务/产物/63组件测试均通过，只有两条手机focus断言因原测试在原生异步恢复前立即取快照失败；改为等待同一activeElement严格相等条件，最窄两例通过（`/tmp/frontend-p01-focus-restore-green.log`）。这是测试时序修复，未手动focus或放宽断言。主入口/eager失败覆盖两个prefix，闭包坏包不改旧metadata，600秒/HTTP-date/超32位timer及取消均有回归。
- e078122 的 retained spec 修正审查及全片独立quality已明确pass，报告 `/tmp/frontend-p01-spec-correction-review.md`、`/tmp/frontend-p01-quality-review.md`；曾遇审查等待到时限/continuation脚本接口错误，均已报告并用原生resume/新quality完成，不以空输出冒认pass。依赖类型/构建与npm audit通过；主动LSP无编译错误，两处跨函数异常传播误报已说明调用者catch并记录false-positive；保留原生组件弃用hint等非阻断建议。bundle许可证采用Vite原生build.license随产物输出。
- 首次且唯一全fast baseline未通过：`/tmp/frontend-p01-baseline-verdict.log` / `-raw.log`，master提交基线0，本轮31。16项frontend setup错误为既有SPU测试在collection全局包装_render_pages只接受一个参数；试点fixture停止传冗余迁移覆盖参数，改为实际唯一运行时集合。显式收集该SPU模块再跑所有16个新browser/HMR用例全通过（`/tmp/frontend-p01-collection-compat-green.log`），不是只避开污染源单跑。
- 其余12项指向共享模板含当前source不存在的0071_plugin_status以及schema清单不匹配；3项publishing测试仍要求旧ACTIVE登记内容。未将它们擅记为允许的稳定基线。已通知登记迁移owner及保留test_final_v16文件的plugin-status-erp owner，不修改其文件或重建ACTIVE旧表。
- envsetup --check通过后，按唯一标准入口执行--refresh-template并只选上述DB相关失败；模板schema-only恢复又被security.api_keys.fk_api_keys_shop_pk依赖commerce.shops主键阻塞，exit4，未进入后续测试（`/tmp/frontend-p01-template-classification.log`）。工具警告测试模板可能部分替换；已停止DB测试、全板通知owner修复并重建canonical模板，未另选共享库/直接runner/生产写入。
- 当前lane因标准测试环境与三条登记迁移断言阻塞，**不是ready/已合入/生产上线**；代码保留并推送分支，模板修复及精确失败分类完成前不得把baseline或交付任务关闭。后续继续P0/P1交付，P2–P6仍未实施。

## 9. 继续交付：登记断言的最小修复片

- TaskStartSnapshot：用户要求继续；起点1febb27，已同步master64acba9，工作树clean。标准测试模板仍可能部分替换，未获健康回执；不运行DB验证或另选库。`scripts/test_isolated.sh`从本lane未修改scope释放（consent m-be6fbfc0-be8）；plugin-status-erp保留v16文件，父不触碰。
- Change Necessity：code-change（仅两条测试契约）。上游64acba9已批准ACTIVE退化为静态指引，旧测试仍要求其中有活跃文件归属/迁移行；恢复旧行或让CI依赖私有board都是错误的第二权威。退役旧live-row断言，改验证静态指引、canonical入口及单writer/无ACTIVE cleanup规则，同时保留原发布、栅栏、权限、运维与源路径存在性要求。
- Owner/complexity：只改无其他active保留的v14/v24两个测试函数，无生产代码/新helper/新依赖/业务规则；明确排除v16和模板脚本。ArchitectureReviewRequired=no（复用已批准的registry迁移）。已在lane m-bcc97a41-b0a登记精确新增scope。
- TDD Route：沿用strict记录；原baseline两项实际失败为RED，不把静态读取或lint冒充GREEN。标准模板修复后仅复跑这两个原node ID，再结合v16 owner交接和剩余DB失败分类；当前不得关闭测试/交付任务。
- Plan：1）核上游规则与所有权并登记（完成）；2）将两条消费断言重新绑定canonical文档，保留其余原要求；3）模板恢复后通过唯一隔离入口定点验证、独立审查最终delta并续交付。GREEN和环境健康仍待证据。

## 10. 标准测试模板修复 Spec Brief / 实施计划

### 起点、归属与决定

- TaskStartSnapshot：6e3e5c1已push/clean；master64acba9。owner通过m-94762b4e-074/m-5022c8f6-a13移交基础设施与v16，release record m-41c7f7e3-3a9；父完整认领记录m-2e0d994e-e67。保留生产/app/access/plugins/schema模型及migration排除，只有测试模板生命周期变化。
- 证据：真实刷新在既有security.api_keys额外FK处阻塞source PK DROP；`pg_dump --clean`仅清理dump内对象（PostgreSQL18官方 https://www.postgresql.org/docs/18/app-pgdump.html），旧wrapper只清target-only表，因此同表新约束/其他target-only对象可复发。近因已证实，跨branch模板复用是上游生成条件；不声称一个FK名字就是全部根因。
- Change Necessity=code-change：恢复“用完整source schema重建受guard测试目标”的标准入口，不在frontend旁路、不硬删单个FK。CanonicalOwner=importer的schema replacement；wrapper只负责串行refresh/clone及stamp/upgrade/seed。
- Minimality=充分的owner修复：复用已安装PostgreSQL18 dropdb/createdb -T template0、psycopg.conninfo及SQLAlchemy URL构造，不实现DDL差异扫描、不引入新依赖/暂存数据库。原schema-only/full-import行为保留；只有标准wrapper显式选择新recreate模式。禁止pg_restore/pg_dump --create，避免archive源库名决定目标。
- Complexity/Owner-fit：importer仍拥有schema replacement；新增` scripts/_test_db_connections.py`（实施前登记scope）只负责连接身份解析/派生/guard与受支持transport校验，两个shell边界共用此单一authority，复用`scripts/_db_url.py`的driver规范化，不将测试策略塞进通用normalizer。wrapper删除旧target-only表cleanup；保锁与生命周期顺序。ArchitectureReviewRequired=yes，独立方案源审查后再行为实现。

### 边界与行为

1. 新模式`--recreate-test-db`必须同时`--schema-only --yes`；target明确test命名且与有效source不同。共享helper以psycopg解析**有效**参数为authority，SQLAlchemy URL.create/render_as_string构造host测试URI与plain维护URI，复用项目driver normalization。共享is_prod_shaped_db/require_destructive_script_guard绑定有效target名字，独立硬拒prod、无效/缺失名字，不设置生产override。parser异常只报固定脱敏错误。
2. 修正plan finding template-plan-1：wrapper不得只换URI path而保留dbname query覆盖。以同一参数authority移除原dbname后分别写入**显式**TEMPLATE_DB、RUN_DB、维护postgres，round-trip断言有效名字严格相等且其余连接参数一致；effective base DB也须test-shaped。query覆盖到另一测试库时可规范化为正确指定模板/clone，绝不drop/restore/测试连接到该旁库；覆盖到prod须在任何生命周期动作前拒绝。reset独立调用另校验DST有效dbname等于显式URI路径意图，拒绝歧义目标。
3. 存在检查、模板recreate/restore、clone、stamp/upgrade/seed、测试连接和cleanup全部使用上述同一server/user/port authority派生的DSN，不再默认-U postgres/-d postgres另走一路。拒绝service/hostaddr/multi-host等未支持routing。host模式可用单一显式TCP endpoint；Docker模式先验证目标（及reset的source）是127.0.0.1:5432且所选container的5432/tcp确实发布到host loopback5432，否则fail closed，不猜网络namespace。当前.env/.env.test已只打印非secret routing确认两者127.0.0.1:5432，docker port证实0.0.0.0/[::]:5432；未改环境。
4. 客户端/server/source检查、guard/工具可用性检查和完整schema dump成功后才重建target。维护dbname只改为postgres；官方dropdb/createdb显式指定校验后的test名字与template0，**不force**。连接参数只在进程内捕获，不日志/落盘；拒绝控制字符破坏内部IPC。wrapper已有/缺失模板均委托新模式，不提前删表/创建空库；持原锁，之后按原规则stamp/upgrade/seed/clone。
5. dump失败/安全拒绝不得清理现有target；drop/create/restore失败必须非零并保持“不健康”回执，不能继续clone/宣称成功。不增加新数据库fallback，不承诺无损/原子切换；该目标原本就是可重建测试模板。业务schema验证只在真实刷新成功后解冻。
6. v16移交后保留admin-only、0066执行栅栏/0069确认祖先；head跟随本branch实际Alembic单head；结构断言绑定已提交生成DDL的表清单而非写死0071/80或私有board。只改过时owner/head/inventory消费断言。

### TDD 与执行顺序

- TDD Mode=auto / Route=strict（承接既有strict，新增共享/安全/持久化生命周期风险）。mock前置基线7例已通过标准ephemeral clone：`/tmp/frontend-p01-template-mock-baseline.log`；没有改fixture/guard或使用直接runner。此7例不证明业务schema健康。
- a9fdee6方案审查因template-plan-1明确fail，未实施破坏性代码；本修订先补生命周期名字与网络namespace一致性，再重审。
- 1）独立重审；2）command doubles观察同表target-only约束RED，另覆盖dbname query到旁测试库/生产、unsupported routing/host模式一致性、不得wrong-target drop/restore/clone/test、完整dump失败不重建、确认/guard、已有/缺失模板及drop/create/restore失败；3）最小实现mock GREEN，保stamp/upgrade/seed顺序；4）固定实现独立安全审查；5）唯一入口真实refresh+最窄原失败（10 order-price+2 schema/head+3 registry），确认clone cleanup；6）只定点分类，最终prospective master按要求验证合入。
- 所有业务测试/基线/交付任务在真实模板健康和失败分类完成前保持blocked。不得把mock、lint或source review作为生产/全套GREEN。

### 实现检查点（顺序记录，以下历史门禁不代表最新状态）

- 0041ae0修订方案已由retained安全审查明确PASS（`/tmp/frontend-p01-template-plan-correction-review.md`），helper精确scope已登记m-0ca65203-52a。复用已安装psycopg3.3.6/LGPL-3.0-only及SQLAlchemy2.1.2/MIT，无新增依赖；新helper统一有效参数、literal lifecycle名字、round-trip、维护DSN、SST guard及受支持路由。Docker先核active本地context/发布端口，随后所有命令pin本地socket；拒绝ambient libpq routing/role变量及缺失显式user。
- `scripts/test_isolated.sh`删除target-only表猜测cleanup与先建空模板路径，委托importer显式recreate；存在/clone/cleanup/stamp/seed/test共用派生身份。保留help无venv可用；自身run clone清理失败不再报绿色。原importer的非recreate模式保留。
- 真实RED来自`/tmp/frontend-p01-template-rebuild-red.log`（12项失败，包含同表额外约束、旁测试库/生产query覆盖及路由），并补cleanup/redaction/parameter-types/help/ambient-route的定点RED。修复后`/tmp/frontend-p01-infra-static-green.log`通过45项（42 CLI/helper替身+3原静态registry node IDs），0失败/0跳过；均经标准ephemeral clone，未覆盖fixture/guard、未直接跑pytest，业务DB验证没有解冻。
- v16已在移交scope内改为本branch单head和0066/0069祖先、保留admin-only精确grant；inventory对照生成DDL的固定表前缀提取清单并验证public.fn_touch_updated_at存在。两条DB consumer尚未执行，不宣称GREEN。
- ruff、bash语法、diff-check与三Python路径主动LSP编译检查通过（后续最终pin前再确认）。下一步只读固定实现安全审查；通过后才允许唯一入口真实refresh和12条DB失败及触发器函数验证，之后续基线/交付。当前没有重建真实模板或生产写入。
- 1ebe7d4实现审查明确FAIL两项：host-only libpq环境检查漏掉container环境，且recreate在安全parser前沿legacy路径打印缺失dbname的URI。未越过真实refresh门。`/tmp/frontend-p01-template-review-findings-red.log`实测10项失败；另有legacy refusal credential反例RED，未把原45例绿当这两项已覆盖。
- 修正在common helper内读取所选container的immutable ID，port/environment probe及后续所有lifecycle exec绑定同一ID；仅用/bin/sh内建test检查已知routing/role变量是否为空，不输出其值或读取其他credential字段。recreate先完成确认/安全解析，再允许legacy分支名称逻辑；所有屏显标签只显示安全名称或redacted，legacy拒绝不反射URI，非recreate操作决策未改。
- `/tmp/frontend-p01-template-review-findings-green.log`现57PASSED/0fail/0skip（新增container PGHOSTADDR/service/role、immutable ID、source/target missing/malformed URI和legacy redaction）。这是修正后的mock/static证据；仍须固定新候选复审，真实模板/12个DB失败/基线/交付均未通过。只读环境诊断已确认当前container这些变量全空，仅报告名称未输出值。
- 88e5e53复审确认container和原missing-path修正，但FAIL残余同类泄密：stdlib urlparse的NFKC netloc ValueError含原始凭据。改变错误边界而非再补单个字符/parser：CLI对所有ValueError只输出固定诊断，内部有效dbname/显式path意图比较不变。`/tmp/frontend-p01-parser-boundary-red.log`四个literal全角字符及外部异常payload共5项实测失败；统一出口修复后`/tmp/frontend-p01-parser-boundary-green.log`62PASSED/0fail/0skip（59helper/CLI+3静态registry）。原57例保留回归；该时点没有真实refresh。negative unit guard-refusal spy不放宽guard；所有运行仍经标准clone，未通过全局fixture/guard bypass制造绿色。

### 最新验证检查点：真实模板健康已恢复，完整基线待验

- 34c9d69固定实现独立审查PASS（`/tmp/frontend-p01-parser-boundary-review.md`），仅解除已批准的test-template验证门禁。`envsetup --check`退出0，唯一入口`--refresh-template fast`真实完成完整schema dump、guarded template重建、stamp源0069、upgrade本branch head、内置权限seed、独立clone及cleanup；无warning。没有prod写入、迁移、restart、直接删FK或强断template连接。
- `/tmp/frontend-p01-template-real-refresh.log`退出0，24PASSED/0failed/0error/0skip：原order-price失败模块及邻域19例、v16 head/admin-only与生成DDL inventory/public.fn存在2例、3条原静态registry断言。此前partial-template警报已解除；这不等于完整fast或交付通过。
- template-rebuild、registry-tests和env-blocker任务已有changedFiles/证据回执并完成；接下来同步master、固定最终候选，按canonical workflow运行baseline，再做独立prospective-master精确提交验证。P2–P6及生产部署仍不在本slice。
