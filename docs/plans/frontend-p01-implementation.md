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
- Minimality=充分的owner修复：复用已安装PostgreSQL18 dropdb/createdb -T template0及psycopg.conninfo解析，不实现DDL差异扫描、不引入新依赖/暂存数据库。原schema-only/full-import行为保留；只有标准wrapper显式选择新recreate模式。禁止pg_restore/pg_dump --create，避免archive源库名决定目标。
- Complexity/Owner-fit：500行importer既有职责就是target schema替换；新增显式test-only重建模式属于local-fix-without-new-responsibility。删除wrapper的target-only表cleanup，统一目标清理由模式负责，防止dump/preflight前已删对象。ArchitectureReviewRequired=yes（共享测试/破坏性test DB生命周期），独立方案源审查后再行为实现。

### 边界与行为

1. 新模式`--recreate-test-db`必须同时`--schema-only --yes`；target必须是明确test命名且与source不同，使用psycopg的有效连接参数解析，拒绝query覆盖后的prod/缺失/无效名字，不依赖URI表面路径。共享`is_prod_shaped_db`及`require_destructive_script_guard`绑定有效target名字，生产拒绝不能由override变量绕过；不设置任何生产override。
2. 客户端/server/source检查和完整schema dump成功后才可能重建target；guard/命令可用性检查也在target变更前。维护连接由有效target参数派生，dbname只改为postgres；用官方dropdb/createdb指定验证后的test名字，template0，**不force终止连接**。所有routing/credential参数只在进程内捕获，不打印、不提交。控制字符不能跨两行内部IPC，明确fail closed。
3. 标准wrapper在已有模板和缺失模板两条路径均调用该模式；不在importer前单独DROP表或提前创建空模板。refresh/clone仍持原锁、完成后stamp本branch可识别source revision、upgrade到本branch head并seed权限；ephemeral clone/cleanup流程不变。业务DB验证只在真实刷新成功后解冻。
4. dump失败/安全拒绝不得清理现有target；drop/create/restore失败必须非零并保持“不健康”回执，不能继续clone/宣称成功。不增加新数据库fallback，不承诺无损/原子切换；该目标原本就是可重建测试模板。
5. v16移交后保留admin-only和发布确认祖先，head跟随本branch实际Alembic单head；结构断言绑定已提交生成清单而非写死0071/80或私有board。只改过时owner/head/inventory消费断言，不改业务默认值。

### TDD 与执行顺序

- TDD Mode=auto / Route=strict（承接既有strict，新增共享/安全/持久化生命周期风险）。mock前置基线7例已通过标准ephemeral clone：`/tmp/frontend-p01-template-mock-baseline.log`；没有改fixture/guard或使用直接runner。此7例不证明业务schema健康。
- 1）独立审查本方案；2）在现有command doubles表达“source/target同表、target额外约束导致旧restore失败”，观察RED；另测完整dump失败不重建、安全/确认拒绝、host/container选择和missing-template路径；3）最小实现并跑同一mock集GREEN，保stamp/upgrade/seed顺序；4）独立安全审查固定实现；5）唯一入口真实refresh+最窄原失败（10 order-price+2 schema/head+3 registry），确认clone cleanup；6）只对新失败定点分类，不重跑整套来捕获baseline，最后按master prospective commit要求验证与合入。
- 所有业务测试/基线/交付任务在真实模板健康和失败分类完成前保持blocked。不得把mock、lint或source review作为生产/全套GREEN。
