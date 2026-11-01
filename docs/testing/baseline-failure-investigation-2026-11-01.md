# 313 项基线差异排查

日期：2026-11-01。按用户后续明确指示，先将 SPU 分支合并并推送 master，再排查失败。合并提交为 `7335af492dc5039b61f2726d03b2a9f0ead8d9ee`，使用独立整合工作树及 `/tmp/tts-erp-master-merge.lock`；合并时保留完整基线未通过的事实，没有部署或重启生产服务。

## 数量含义

313 是中途提交 `f3acd5c0` 与仓库已记录零失败基线的测试 ID 差异数，不是 313 个独立缺陷，也不是最终分支的失败数量。后续已验证通过的对应 ID 共 **164**：

| 原失败范围 | 数量 | 后续处置 |
|---|---:|---|
| 盈利对比 API | 112 | 一个快照边界问题造成大量连带失败：授权 SELECT 先开启普通事务，后续一致只读快照拒绝。已将授权与事实读取放入同一既有快照；整份 118 case 通过。 |
| SPU 价格 / 费率 API | 18 + 5 | 补齐 TEST 店铺现行授权关系；原金额、汇率、空样本与异常断言保留并通过。 |
| ROI API | 3 | 授权夹具、固定授权查询计数与旧源码字符串预期维护后，失败选择通过。 |
| 真实价格浏览器 / 盈利真实读取 | 9 + 4 | 授权夹具、原生弹窗焦点等待及对比快照修复；整份分别 9 / 12 case 通过。 |
| React 激活登记 | 2 | 两页 SPU 已获用户授权迁移，旧禁止启用断言同步；通过。 |
| 字体检查 | 11 | 移除误将 HeroUI 默认字体当作项目白名单的覆盖；继续严格使用 canonical 三栈。另两项真实字体偏离仍保留。 |

剩余 **149 = 119 非浏览器 + 30 浏览器**，均从原失败 ID 清单精确选取，在合并后的 `7335af49` 上通过 `bash scripts/test_isolated.sh fast <失败选择> --tb=short` 复验。没有重新运行完整 fast，也没有更新基线来吸收失败。

119 个非浏览器选择与 30 个浏览器选择均已全部稳定失败，两次隔离运行均正常完成并清理临时数据库；本次剩余集合中未出现最小复验转绿的项。原始日志分别保留在指定测试服务器的 `/tmp/codex-baseline-rerun-api.log` 与 `/tmp/codex-baseline-rerun-browser.log`。以下归因结合实际错误与源码；静态发现的后续阻塞点不冒充已执行到该分支。

## 非浏览器：119 项

| 分组 | 数量 | 原因与维护方向 |
|---|---:|---|
| 商业查询、采购成本、照片、广告、盈利日报、图片、新鲜度、拦截记录 | 73 | 新资格过滤先于原业务断言。多数 fixture 只有店铺 / SPU 行，没有 credential 与本店 App 的完整关联；广告仅 seed 广告事实、没有对应 seller 店铺；拦截已成功持久化，但读取按授权 seller 过滤。补齐真实 TEST 安排后继续验证原金额、事务、分页与错误分支，不能 mock 掉权限。另不存在店铺的零统计旧预期，应按现行 404 合同维护。 |
| 店铺管理 API | 12 | 6 项提交已禁止的名称/地区字段导致 422；2 项 CSRF 用例也先被非法请求体拦截，尚未测试到 CSRF；2 项未授权补录店铺错误使用业务列表而非 management view；1 项把未知上游状态预期为 active；1 项只有 409 文案变化。不能把补录店铺伪造为已授权，或把 CSRF 的 403 改成 422 来过关。 |
| OAuth flow | 10 | 旧 state 不含目标店铺、service 与 App revision，上来即 `state_invalid`；旧多店自动创建预期也已不适用于单目标回调。需同时迁移安排与行为合同。 |
| OAuth API | 8 | 7 项缺必填 `shop_pk`，先 422，或随后读取不存在的 authorize_url；1 项 App seed 缺必填 shop_pk，直接 TypeError。 |
| Get Product API | 11 | 旧测试只 mock 代理，硬编码店铺且未授权，先 404，尚未进入代理异常映射；两项原预期也是 404，但错误消息变化。 |
| Products proxy | 1 | App seed 缺 shop_pk；还需补本店 credential 关联，不能只补函数参数。 |
| Cookie CSRF 单元 | 1 | 构造被禁止的 account_name 请求体时已触发模型异常，未调用 handler。应使用合法 opened_date 继续检验 403 与零数据库访问。 |
| Schema 文档快照 | 1 | 真实文档滞后：migration head 已到 0072，README / DDL 快照仍是旧版本，App 表缺现行字段。应走现有快照生成流程，不能只改版本文字。 |
| 历史 0070 probe 测试 | 1 | probe 如实返回当前库的 0072，测试硬编码预期 0070。维护当前 head 的测试安排；历史脚本限定 0069/0070 的执行安全边界保持。 |
| 样式令牌检查 | 1 | 检查器误报：只收集 `:root` 声明，却检查所有 var 引用。common.css 在局部导航 selector 内同时声明并使用 --sidebar-rule，未被收集；不是缺少 CSS 定义。 |

关键依据：

- `tts_erp_v2/commerce/shop_access.py:8–34`：credential/provider/external ID/service/App/revision 资格；`tests/_job_invocation.py::authorize_test_shop` 已是现有复用入口。
- `tts_erp_v2/api/v2/commerce.py:87`、`ad_daily.py:29–35`、`reporting.py:100–103`、`intercept.py:828–836`：授权查询范围；未命中不等于 ingestion 或金额公式错误。
- `tts_erp_v2/api/v2/admin.py:619–650`、`docs/plans/2026-10-31-shop-onboarding-design.md:119`：名称/地区只读及管理视图合同。
- `tts_erp_v2/api/v2/oauth.py:119–141`、`proxy/tiktok_oauth.py:156–197`：逐店 App 与单目标 state；`proxy/token_service.py:137`：shop_pk 参数。
- `tests/api/test_style_tokens.py:70–94` 对比 `tts_erp_v2/static/css/common.css:32–44`：只扫描 root 的检查器遗漏局部属性。

这些店铺接入和授权实现来自此前 `b8a64dc5`，相关受审实现未在 `ed4da9ad..7335af49` 的 SPU 合并中修改。这是沿革证据；稳定性结论使用本轮实际失败复验，不把未复验的猜测标为既有问题。

## 浏览器：30 项 / 8 个测试函数

| 测试范围 | 数量 | 原因 |
|---|---:|---|
| P2 HTTP 错误恢复 | 2 | 旧“注册店铺”按钮、旧注册时填写 Service/App 的表单；当前已拆成补录、开店日期编辑、独立 App 配置。 |
| P2 真实持久化 / 权限 / 丢响应 | 4 | 旧按钮、编辑只读名称和旧核对操作；读取补录店铺应使用 management view。 |
| 原生登录 / Enter / cookie / 退出 | 6 | 共用 arrived() 仍以旧“注册店铺”按钮确认落地，现为“手工补录”。 |
| 原生 401 与提交丢响应恢复 | 2 | 同一旧落地信号；还存在下述请求拦截合同过期。 |
| 原生成本编辑 | 2 | 旧“编辑成本草稿 / 确认此行草稿”与当前“编辑商品成本 / 保存成本”不匹配。保留后续精确 PK、Decimal、隔离与历史读回断言。 |
| 原生店铺读取 / 取消 | 8 | 旧 grid“已注册店铺”现为“店铺接入列表”，还有计数、按钮与说明变化。旧几何阈值需实际测量后评估，不删除长 ID 完整可见和整页无溢出检查。 |
| 统计图表像素 | 4 | 测试硬编码蓝色 RGB(37,99,235)，当前图使用 canonical --accent (#214F43)。按规范色取样，保留非零柱、等宽、时间、请求次数与空态断言。 |
| 控制台 / 广告页字体 | 2 | **真实产品字体旁路**：font-mono 与裸 code/pre 使用 Tailwind 默认等宽栈，没有桥接到 canonical --mono。应修共享字体映射，保留三栈白名单。 |

跨 P2 的后续阻塞点：`tests/support/frontend_p2_browser.py:261–267` 仍只允许旧列表、单店读取和 OAuth query；当前页面使用 `view=management&limit=50&offset=0`、带 management 的单店读回及 shop_pk 授权。应按已核验 owned PK 和固定查询形状精确维护，不能放行任意 GET。登录、CSRF、未知写入不重放等保护继续保留。

字体依据为 `frontend/src/pages/dashboard/index.tsx:144`、`pages/ad-daily/AdTable.tsx:13,33`、`frontend/src/styles.css` 与 `static/css/tokens.css:47`；统计图依据为 `pages/intercept-stats/DailyChart.tsx:33` 与 `test_frontend_stats.py:58,64`。

## 结论与后续顺序

问题集中于前一次逐店接入/授权改造后，旧测试安排、请求体、OAuth state、浏览器拦截器及页面选择器未同步；同时存在少量真实产品/文档问题。一个前置条件可连带阻塞很多测试，不能按失败 ID 数量计算独立 bug 数。

建议先修正授权 fixture 与管理/业务读取语义，再迁移 OAuth 单目标测试及精确浏览器拦截合同；随后维护文案、图表规范色和局部 token 检查器，修复真实字体旁路并生成现行 schema 快照。保留金额 oracle、权限、CSRF、只读与禁止自动重放断言。

额外静态发现，不计入上述 313：旧 `/onboard` 页面仍调用不带 shop_pk 的 authorize URL（oauth.py:589、634），与当前必填参数冲突；旧 OAuth 专页文档仍描述全局 App / 多店自动创建。这两项需后续处理，尚未实际点击复现。

本轮执行合并及归因，没有批量修改产品或测试来消除失败，没有更新零失败基线，没有生产部署。合并已成功不代表完整测试通过。
