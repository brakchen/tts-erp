# SPU 盈利技术方案

> **文档状态：目标实现方案；可直接交给开发模型实施。**
>
> 本文是页面、HTTP adapter、领域 module、查询和部署的技术事实；业务口径、公式、日期边界、字段业务含义和验收口径的唯一权威仍是 [`../business/spu-profitability.md`](../business/spu-profitability.md)。本文只引用该文，不复制公式。当前代码行为与目标行为分开标注；目标契约不能当作当前已上线契约。
>
> 代码基线：`tts_erp_v2.analytics.spu_profitability`、`tts_erp_v2.analytics.spu_roi`、`tts_erp_v2/templates/pages/spu-profitability.html`、`static/js/spu-profitability-page.js`，以及当前测试。所有路径均相对仓库根。

## 1. 权威边界、目标与非目标

### 1.1 文档职责

| 内容 | 唯一来源 | 本文处理方式 |
| --- | --- | --- |
| 指标概念、计算公式、退款/全损/预测口径、日期业务边界 | `docs/business/spu-profitability.md` | 只引用章节，不重新定义 |
| HTTP 参数、响应字段、状态码和 wire 精度 | 本文目标契约；当前兼容细节以 `tts_erp_v2/api/v2` 实现为准 | 逐项列出当前/目标 |
| 页面结构、控件和交互 | `spu-profitability.html`、`spu-profitability-page.js`、CSS | 按源码描述 |
| 深模块接口、查询流程和依赖方向 | `analytics/spu_profitability/` | 约束实现位置 |
| 历史公式、旧页面设计和迁移记录 | `docs/archive/` | 不作为现行依据 |

### 1.2 目标

1. 在同一个只读 `REPEATABLE READ` 快照中产生明细行、大盘、费率、成本、汇率和证据基准。
2. 将页面经营窗口与预测样本窗口彻底分离：经营日期只影响当前指标和证据；预测只在大盘返回，并由 `projection_lookback_days=30|90` 控制样本。
3. 保留 `/v2/analytics/spu-roi`、四个钻取 URL 和历史字段的兼容 adapter；新领域代码不依赖 HTTP 字符串。
4. 前端只请求、格式化和呈现服务端结果，不复制阈值、状态判定或业务公式。
5. 保持订单级全局去重、分页稳定、选择范围一致和可观测的降级说明。

### 1.3 非目标

- 不修改 `docs/business/spu-profitability.md` 的业务定义、公式、日期边界、字段含义或验收口径。
- 不恢复 `reporting.product_profit_daily` 为 SPU 盈利来源；它仍是旧粗略毛利快照。
- 不新增 TikTok Shop 写入 endpoint，不把广告、订单或结算事实写回上游。
- 不在前端重新计算净收入、货本、利润、ROI、退款率或全损率。
- 不把预测字段放进单个 SPU 行的产品展示语义；预测只属于 `totals`。
- 不自动生产迁移、不执行破坏性运维、不把本方案变成生产发布脚本。

### 1.4 当前实现与目标实现

| 主题 | 当前代码（基线事实） | 目标实现（本方案） |
| --- | --- | --- |
| 深模块 | `read_overview()`、`explain_spu()`；SQL 在 `_implementation.py`，纯公式在 `_formula_v10.py` | 保持公开 seam，补齐投影 policy 和 basis 传递 |
| 经营窗口 | `w_start/w_end` 进入主表、广告和证据查询 | 店铺本地日半开区间；主表和证据保持此行为 |
| 预测窗口 | 当前 SQL 的预测也受 `w_start/w_end` 影响；没有 HTTP `projection_lookback_days` | 预测使用 `A=T-1`、7 天成熟等待期和 30/90 日样本，忽略经营日期 |
| 预测行字段 | `SpuProfitability` 与 wire row 仍包含大量 projection 字段 | 兼容字段可保留但页面不展示；目标新客户端只读取 `totals`/`meta.projection` |
| 当前未结算收入 | 当前实现仍有历史 SPU 退款率兼容路径 | 按 business 文档目标口径：只扣已确认退款，未来损失仅进 projection |
| 全损判定 | 代码仍保留 v9 兼容字段和历史诊断 | 权威预测分子使用已完结订单严格全损率；旧诊断只标兼容 |
| 快照时间 | overview 共享 `basis.calculated_at`；detail payload 当前自行取 `datetime.now(UTC)` | `explain_spu()` 的结果、四路 evidence 和 basis 共享同一 `calculated_at` |
| API 文档 | external-api 混有详细盈利契约及 archive 引用 | external-api 仅保留导航、鉴权和稳定性索引，详细契约集中到本文 |

开发时不得把“当前实现”当作目标已完成；每一项目标差异需在相应代码和测试阶段落地。

## 2. 用户旅程和页面信息架构

### 2.1 用户旅程

1. 用户打开 `/v2/pages/spu-roi`（标准看板）或 `/v2/pages/focused-spus`（重点关注看板）。未认证 HTML 请求由 auth middleware 302 到登录页；登录后回到原 URL。
2. 页面加载店铺列表、当前用户、枚举映射、主表和店铺同步新鲜度。没有有效 `shop_pk` 时显示“请选择店铺”对话框，不自动跳首页。
3. 用户在页头“店铺”切换店铺；切换会取消旧请求、清空 SPU 草稿/钻取缓存并重新加载。
4. 用户可在“SPU”多选中搜索、选择或粘贴逗号分隔的 SPU；“清空”只清草稿和已应用范围，“查询”才提交范围。精确 SPU 范围同时约束当前大盘和预测大盘。
5. 用户选择经营日期起止日。默认页面 profile 是 `t-1`，但自动填入的覆盖日期不算用户主动选择；日期只改变当前事实和证据。目标实现的 30/90 日预测切换只改变大盘预测。
6. 用户展开“更多”，可填写本次请求费率覆写或勾选“含无活动”；覆写不持久化，含无活动只控制活动目录行集合。
7. 用户点击列头排序、分页或每页条数。排序和分页只改变 `items`；`totals` 始终是完整业务范围。
8. 用户点击任意 SPU 行展开 accordion：利润构成不请求；订单·物流、结算、售后、广告 tab 首次激活时懒加载，并按 `(spu_pk, tab, 窗口)` 缓存。
9. 页面以页头同步灯、费率来源、汇率时间、估算标识和后端 warning 帮助用户判断结果是否新鲜、完整或降级。错误可通过重试，不以 200 空结果伪装成功。

### 2.2 桌面布局

从上到下是：

1. 侧边栏（共用页面壳）。
2. 页头：`TikTok Shop · Analytics`、页面标题、店铺 select、当前操作员、广告/订单/物流/妙手/汇率五个新鲜度项。
3. 费率事实条：来源、费率、实测估计摘要、基线降级说明、右侧“关闭费率提示”按钮。
4. 大盘结余带：Bootstrap responsive grid，分为总览、有效、退款、全损、国内取消、利润、ROI、预测依据、未结算预测对象、预计十组区域。
5. 工具栏：SPU 选择行；经营日期行；“更多”展开费率覆写和“含无活动”。
6. 表格元信息（当前排序文案）和横向可滚动主表。
7. 行内钻取 accordion。
8. 分页：每页 `50/100/200`、页码提示、上一页/下一页/页码链接。

主表固定展示商品、广告消耗、广告系统实际 ROI、广告系统保本 ROI、有效销售、总单量、有效单量、取消率、全损率、净利润十类列；列显隐设置由 profile 控制，但不能改变返回 totals。

### 2.3 移动布局

Bootstrap `container-fluid`、`row-cols-*` 和 `table-responsive` 负责断点；不使用 CSS `nth-child` 隐藏业务列。页头工具换行，结余组从多列变为一列/两列，日期和 SPU 控件填满可用宽度；主表保留所有列并允许水平滚动，商品首列冻结。钻取 tab 使用水平滚动的 `nav-tabs`。所有按钮最小触控高度约 38px，提示不依赖 hover 才可理解。

### 2.4 无障碍基础

- 每个 select/input 有可见或 `visually-hidden` label；SPU 多选有 `aria-label`。
- 主表容器有 `role=grid` 和描述，分页 nav 有 `aria-label`。
- 大盘区、钻取区、新鲜度区使用 `aria-live=polite`；异步错误使用可读文本，不只用颜色。
- 钻取 tab 使用 `role=tab`、`aria-selected`；弹窗使用 `role=dialog`、`aria-modal`、标题关联。
- `:focus-visible` 使用高对比 outline；所有按钮可用 Tab/Enter/Space 操作，日期输入保留原生键盘控件。
- 图片 alt 为空表示装饰图；缺图显示“无主图”文本。红/绿状态同时输出 `profit_status`/`roi_status` 文本。
- 未知后端枚举显示原始代码，不把未知值静默变成空白。

## 3. 页面区块与控件契约

### 3.1 页头、同步新鲜度和费率

| 位置/控件 | 文案/来源 | 启用与事件 | 状态处理 |
| --- | --- | --- | --- |
| 页头店铺 select `#shop-switcher` | “店铺” | 始终可用；change 调用 `selectShop()` | 加载时 disabled；空列表显示“无可用店铺”；401 回登录，加载失败显示错误并可重试 |
| 操作员 `#ops-identity` | “当前操作员：`role` · 退出” | 点击“退出” POST `/v2/auth/logout` | 失败仍回登录；未知用户显示“登录” |
| 同步灯 `#data-freshness` | 广告、订单、物流、妙手、汇率 | 店铺改变或定时刷新 `/v2/sync/freshness?shop_pk=` | `severity` 仅接受 `ok/warn/crit/unknown`；缺失/非法值按 unknown；时间缺失显示 `—`；错误显示“同步时间加载失败” |
| 费率条 `#fee-card` | 实测/页面覆写/基线 | overview 成功后更新；关闭按钮仅隐藏本次页面生命周期 | `user_override`、`shop_estimate`、`baseline`、`mixed` 来源均渲染；基线显示降级说明；overview error 不显示旧费率冒充新结果 |

同步 endpoint 零上游外呼；`freshness` 的四个 `sources` 实际来源和红灯规则以 `docs/api/external-api.md` 的同步章节及 `tts_erp_v2/api/v2/sync_status.py` 为准。

### 3.2 大盘区域

所有大盘数字来自 `totals`，不从可见行累加。金额显示 CNY；wire 金额是四位小数字符串，页面可按本地化格式显示两位但 tooltip 不改变单位；比例由后端给的字符串格式化为百分比；数学无解/null 显示 `—`。

| 区域 | 控件/文案 | 服务端字段 | 状态 |
| --- | --- | --- | --- |
| 总览 | 总单量、广告消耗 | `total_orders`, `spend` | 空范围显示 0 或 `—`，不展示旧请求值 |
| 有效 | 有效单量、有效销售 | `effective_order_count`, `effective_sales` | 估算收入带 `≈`/提示；金额仍来自服务端 |
| 退款 | 退款数、退款率 | `refund_order_count`, `refund_rate` | 分母为 0 显示 `—` |
| 当前兼容全损 | v9 兼容全损量、兼容全损率 | `full_loss_qty`, `full_loss_rate` | 不是目标严格全损；后端决定分类，前端不从 case 数推导 |
| 国内取消 | 国内取消量、国内取消率 | `domestic_cancelled_order_count`, `cancel_rate` | 海外取消不重复进入取消率 |
| 利润 | 净利润、实际 ROI | `net_profit`, `roi_real` | `profit_status=loss` 红色；ROI null 显示 `—` |
| ROI | 实际保本 ROI、广告系统实际 ROI、广告系统保本 ROI | `roi_breakeven`, `ad_system_actual_roi`, `ad_system_breakeven_roi` | `estimated_known_costs` 前缀 `≈`；无解显示 `—` |
| 预测依据 | 预测状态、已完结样本单、已完结严格全损单、已完结订单严格全损率 | `projection_status`, `projection_completed_basis_order_count`, `projection_completed_full_loss_order_count`, `completed_full_loss_rate` | 目标契约只来自 totals；状态中文仅由后端/固定枚举映射，不改变值 |
| 未结算预测对象 | 未结算订单、未结算已送达订单、待完结风险订单、预计未来新增严格全损件 | `unsettled_order_count`, `delivered_unsettled_order_count`, `full_loss_exposure_unsettled_order_count`, `projected_future_full_loss_qty` | 目标契约；样本不足时依赖投影的值为 `—`，不是 0 |
| 预计 | 预计净收入、预计净利润、预计 ROI、预计保本 ROI、预计广告系统 ROI、预计广告系统保本 ROI | `projected_*` | `projection_status=insufficient_sample` 时显示 `—`；无风险池时显示无未结算状态 |

### 3.3 工具栏控件

| 位置/控件 | 文案 | 启用条件与事件 | loading/empty/error/success |
| --- | --- | --- | --- |
| SPU 多选 `#filter-spu-ids` | “SPU”；placeholder“请选择 SPU（支持搜索或批量粘贴）” | 选中店铺后启用；Tom Select 搜索 `/v2/commerce/channel-product-options`；最多 100；逗号/中文逗号粘贴异步校验 | 校验时显示“正在校验 N 个”；不存在值显示反馈；AbortController 取消旧校验；成功后显示“已选择 N 个（待查询）” |
| `#btn-spu-clear` | “清空” | 有草稿、已应用范围或校验任务且已选店铺时启用；取消校验并清 scope | 清空后 scope 为空，重新加载 activity；未选店铺 disabled |
| `#btn-spu-apply` | “查询” | 已选店铺、无 loading/校验、草稿与已应用集合不同时启用；click 应用 selection、清 q、offset=0、重拉 overview | 成功保留选择并更新 URL/local state；422 保留草稿、显示错误 |
| 日期 `#filter-w-start/end` | 起始日期/截止日期 | 输入合法 `YYYY-MM-DD` 且 start≤end；change 设置 `datesTouched`、offset=0、重拉 | 空值=不限制；非法顺序显示错误；成功在 meta.window 回显；当前实现按店铺时区，未知 region 422 |
| 预测样本切换（目标） | “预测样本：30 天 / 90 天” | overview 成功后可用；只改变 `projection_lookback_days`、保留经营日期和行分页 | 30 默认；切换期间 disabled/loading；422 显示参数错误；当前模板无该控件，前端实施时加到日期行，不得伪装当前可用 |
| `#btn-toolbar-more` | “更多” | click 切换 `aria-expanded` 和 `#toolbar-more` | 面板展开/收起保持字段值 |
| 费率 `#filter-fee` | “费率覆写”，placeholder“留空=实测” | 可输入非负有限 Decimal；随下一次 overview 请求发送 `fee_rate`，不持久化 | 非法/负值 422；成功在 meta.fee.override 回显 |
| `#filter-include-all` | “含无活动” | change 重拉；控制 activity catalog 行，不改变业务 formula | 默认 false；空活动时 `items=[]` 但 totals 按 scope 返回 |
| 表头列排序 | 现有列标题和箭头 | Tabulator headerSort 调整 `sort/order` 后服务端重拉；默认 `roi_real asc` | 排序 loading 不允许旧请求覆盖新请求；稳定次键由后端 `spend`、最终 `spu_pk ASC` |
| 列开关 `details.op-colswitch`（由 kernel 生成） | “列”/各列 checkbox | change 调用 Tabulator `show/hide`，只改变本地可见列，不发请求 | profile 不允许未知 column id；键盘 Space/Enter 可切换；刷新后按 profile 默认恢复 |
| 行展开 | 主表任意 SPU 行 | click/键盘 Enter 展开或收起；一次只保留一行；切换行先关闭旧 panel | loading/empty/error/partial/success 由 drill panel 显示；筛选变化强制收起 |
| 分页/每页 | “每页显示” 50/100/200，页码 | 改 limit 或页码设置 offset 重拉；生成的上一页/下一页/页码按钮均为 button | `total=0` 显示空状态；页码超范围后端返回空页且 totals 仍完整；无上一页/下一页时 disabled |

分页、列开关和 Tabulator 行操作是 JS 动态生成的控件，不能只在 HTML 静态 shell 检查。动态按钮必须有可见焦点、`aria-label`/文本、disabled 状态和键盘等价事件。当前源码的 `spu-profitability-page.js` 没有 projection toggle；这是明确的目标 UI 改造，不得仅改文案假装已支持。

### 3.4 主表行与钻取

主表商品单元格显示主图、`spu_id`、标题及后端提示：`缺成本`（`uses_default_unit_cost`）、`高退款`（`refund_rate_alert`）、`≈`（`has_unsettled_orders`）。点击行只允许一次展开；再次点击收起，换行先关闭旧面板。

| tab/位置 | 文案 | 请求与字段 | 空/错误/成功 |
| --- | --- | --- | --- |
| 利润构成 | “利润构成” | 不请求；使用行字段及 `meta.presentation` | 立即渲染；null 以 `—`；公式说明来自后端 hints |
| 订单·物流 | “订单·物流” | 首次激活 GET `/{spu_pk}/orders`，渲染订单和 tracking | loading“加载中…”；空“暂无订单”；超过 500 显示截断提示；失败显示重试按钮 |
| 结算 | “结算” | 首次激活 GET `/{spu_pk}/settlements`，每订单 components | 未结算不出现在数组，由行字段汇总提示；空“暂无结算”；失败可重试 |
| 售后 | “售后” | 首次激活 GET `/{spu_pk}/cases` | 未完结 status 以警告样式；空“暂无售后”；错误不清除其他 tab |
| 广告 | “广告” | 首次激活 GET `/{spu_pk}/ads` | 空“暂无广告”；当前实现随日期窗口裁剪；错误可重试 |

钻取缓存必须在店铺、SPU scope、日期、费率或主表筛选变化时清空；不得把旧窗口 evidence 展示在新窗口下。键盘焦点在收起/展开后保持可追踪。

## 4. 前端模块、状态和请求安全

### 4.1 文件边界

- `templates/pages/spu-profitability.html`：HTML shell、语义区域、控件、Bootstrap 响应式结构。
- `static/js/spu-profitability-page.js`：共享 kernel，拥有状态机、API、格式化、Tabulator、钻取、竞态、loading/error 渲染。
- `static/js/spu-roi.js`：标准 profile：`standard-roi`、默认 activity、日期控件、列/汇总/tab 白名单。
- `static/js/focused-spus.js`：重点关注 selection adapter；持久集合来自 focused endpoint/页面策略。
- `static/css/spu-roi.css`：暖纸 token、状态色、表格、工具栏、钻取和响应式皮肤。
- `static/css/focused-spus.css`：重点关注编辑器独有样式。
- `vendor/bootstrap.min.*`、Tom Select、Tabulator：自托管静态依赖，禁止引入运行时 CDN。

### 4.2 状态模型

`state` 当前实际包含：`q`、`spuIds`、`spuSelectionVersion`、`pendingSpuIds`、`spuResolveControllers`、`limit`、`includeAll`、`shopPk`、`shops`、`shopsByPk`、`shopRegion`、`reportingTimeZone`、`wStart`、`wEnd`、`datesTouched`、`feeRate`、`openDrillRow`、`drillCache`、`table`、`sort`、`order`、`offset`、`loading`、`loadVersion`、`loadController`、`freshnessTimer`、`selectionQueryable`、`meta`、`enumMap`。目标增加 `projectionLookbackDays: 30|90`，并把它纳入 URL、请求 key、local preference 和钻取缓存失效条件。

每次主表请求生成不可复用的 `loadVersion` 和 `AbortController`；成功/失败回调先校验 version。AbortError 静默忽略，非 Abort 错误显示状态并保留可重试输入。请求参数只来自 state，q/分页/排序不改变 selection 或 totals。金额/日期格式化集中在 formatter，使用后端精度，不把 parseFloat 结果回传服务器。

### 4.3 竞态、取消和 stale

- 店铺切换：abort overview、SPU options 和 freshness；递增 selection/load version。
- 日期/费率/排序/分页快速连续变更：abort上一 overview；只有最后 version 可写 DOM。
- 粘贴 SPU：每批有独立 controller；“清空”递增 selection version 并 abort 全批。
- 钻取：tab controller 绑定 cache key；主表筛选变化清 cache；失败不写成功 cache。
- 接收 response 时校验 `shopPk`、scope fingerprint、window 和 projection days；任何不一致按 stale 丢弃。
- 401 跳转登录；422 显示可修正字段错误；429 读取 Retry-After；503 FX 缺失显示“汇率数据暂不可用，请稍后重试”；5xx 显示 request id（如响应提供）。

## 5. 后端入口和领域流程

### 5.1 依赖方向

```text
HTML page route (api/v2/pages.py)
  -> template + static profile
browser kernel
  -> GET /v2/analytics/spu-roi (legacy wire adapter: tts_erp_v2.analytics.spu_roi)
  -> GET /v2/analytics/spu-roi/{spu_pk}/{orders|settlements|cases|ads}
  -> tts_erp_v2.analytics.spu_profitability.read_overview/explain_spu
  -> _snapshot + _implementation + _selection + _formula_v10
  -> SQLAlchemy Session -> PostgreSQL schemas
```

HTTP adapter负责鉴权矩阵外的参数校验、异常到 HTTP mapping、Decimal/date/datetime/Enum 序列化；不得直接 import `_implementation` 的私有查询。领域 module 只接受 `Session`、`ProfitScope`、`RowView`、`EvidenceRequest` 等 typed value。`_selection.py` 解析 `ActivitySelection`、`ExactIdsSelection`、`FocusedSelection`；`_formula_v10.py` 是纯函数 seam。

### 5.2 一致快照和 async 边界

当前 route 是同步 `def`，通过同步 SQLAlchemy Session 读取 PostgreSQL，不可在 async handler 中直接调用同步 DB。若未来 route 改 async，必须把整段同步领域调用放在线程池/同步 worker 边界，不能在事件循环中做 psycopg 查询。

`consistent_read_snapshot()` 在首次查询前建立只读 `REPEATABLE READ`；若 caller 已有事务，则必须已是 `read_only=on` 且 `repeatable read`，否则抛 `SnapshotIsolationUnavailable`。`calculated_at=datetime.now(UTC)` 在快照入口产生并向下传递。目标 `explain_spu()` 的 evidence reader 必须复用同一 basis 时间，不在 `_detail_*` 自己调用当前时间。

### 5.3 查询流程

1. adapter 解析 `shop_pk`、`spu_ids`、`scope=focused`、`q`、sort/order、limit/offset、日期、费率和目标 prediction days。
2. 校验互斥条件：`q` 与 `spu_ids` 不同传；精确/重点 scope 必须有 `shop_pk`；日期成对且 start≤end；SPU 不超过 100 个且单项≤128 字符；费率是有限非负 Decimal；projection days 只能 30/90。
3. 领域解析店铺 region 为 IANA 单时区；未知/多时区 region 失败，不猜时区。经营窗口转为 `[local_start, local_end+1day)` UTC 半开区间。
4. 在快照内解析 selection catalog，得到 selected SPU PK、seller IDs、商品维度和活动过滤。空 focused set 继续返回空 overview，不回退整店。
5. 读取同一 FX snapshot、当前有效人工成本（缺失使用 K1）、店铺 fee-v2 快照/页面 override/0.308 baseline、广告、订单行、结算、售后、物流和同期统计。
6. 订单行按 `spu_pk` 聚合金额/件数；订单事实用 `COUNT(DISTINCT order_pk)`。大盘先建立 selected scope 的完整订单关系，再做全局去重；绝不对分页行求和。
7. 计算当前值和目标 projection 分开：当前窗口绑定 `w_start/w_end`；projection 按店铺当地 `T-1`、7 天成熟期和 30/90 样本得到 basis，再作用于风险池。当前和预测不共享“当前日期”过滤器。
8. 领域返回 Decimal、date、datetime、Enum；adapter 产生 `{items,total,totals,meta}`，前端只渲染。
9. `explain_spu` 在一个新快照中重算目标 SPU 与所选 evidence，结果和 evidence 共用 `calculated_at`、FX basis 和 rubric version。

### 5.4 范围、排序和分页

- `shop_pk` 是内部 `commerce.shops.id`，不接受 `shop_id` 作为盈利过滤。
- `spu_ids` 是当前店铺的业务编号精确匹配；`scope=focused` 由 `reporting.focused_spus(active=true)` 在 DB 内解析。
- `q` 只过滤行集合的 `spu_id`/标题展示；目标不改变 totals。当前 `_implementation` 的 catalog search 同时参与 selected catalog，开发时必须以测试锁定该语义并将 q 从业务 selection 中分离。
- `include_all=false` 只含有广告∨有效销售∨退款的 active SPU；`true` 包含所有 ACTIVE 目录 SPU，仍排除 DEACTIVATE/DELETED 等非 active 状态。
- `limit` 1..500，默认 100；`offset≥0`；页面 UI 只提供 50/100/200。
- sort 是 `SortField` 代码；相同值以 spend、最终 `spu_pk ASC` 稳定排序。总数和 totals 在分页前计算。
- 预测范围跟随店铺和已应用 exact/focused SPU scope；搜索、排序、分页不改变 prediction totals。

## 6. HTTP 契约

### 6.1 认证、公共前缀与请求约定

所有盈利 JSON 和 HTML 均为 readonly：API key 使用 `Authorization: Bearer` 或 `X-API-Key`，浏览器使用 `tts_erp_session` cookie；生产 `TTS_ERP_AUTH_MODE=enforce`，角色顺序 `readonly < readwrite < admin`。HTML 页需要 `page:spu-roi` 或 `page:focused-spus` 页面权限（具体页面矩阵由 access 层维护）。

服务直连基址 `http://127.0.0.1:9877`；生产 gateway 为 HTTPS，浏览器通常位于 `/tts`，静态相对路径和 `TTS_ERP_EXTERNAL_PREFIX` 保持前缀。盈利 GET 不执行写入，不要求 CSRF header。默认滑动限流为每 API key/session 每 60 秒 100 次，由 `TTS_ERP_RATE_LIMIT_PER_MIN` 配置；anonymous public paths 不属于盈利 API。

### 6.2 主端点（当前稳定 URL）

`GET /v2/analytics/spu-roi`

当前 query 参数：

| 参数 | 类型/范围 | 当前行为 |
| --- | --- | --- |
| `q` | string≤200 | SPU/title 搜索；不能与 `spu_ids` 同传 |
| `spu_ids` | comma-list≤100，每项≤128 | 精确 scope，必须 `shop_pk` |
| `scope` | `focused` | 重点关注 scope，必须 `shop_pk` |
| `sort` | SortField，默认 `roi_real` | 服务端排序 |
| `order` | `asc|desc`，默认 `asc` | 非 desc 的值当前被 adapter 当 asc；目标应非法值 422 |
| `limit`/`offset` | 1..500 / ≥0 | 分页 |
| `include_all` | bool，默认 false | 活动目录范围 |
| `shop_pk` | int≥1 | 内部店铺 PK |
| `fee_rate` | string Decimal | 页面临时覆写；有限、非负 |
| `w_start`/`w_end` | ISO date | 经营窗口；店铺当地日含结束日 |
| `projection_lookback_days` | **目标参数** `30|90`，默认30 | 仅 prediction；当前 adapter 尚未接受，开发后加入 |

当前请求示例：

```http
GET /v2/analytics/spu-roi?shop_pk=314&w_start=2026-09-01&w_end=2026-09-30&sort=net_profit&order=asc&limit=50&offset=0
Authorization: Bearer ttserp_ro_...
```

目标 90 天示例：

```http
GET /v2/analytics/spu-roi?shop_pk=314&w_start=2026-09-01&w_end=2026-09-30&projection_lookback_days=90
X-API-Key: ttserp_ro_...
```

### 6.3 主端点响应：当前兼容 wire

当前 adapter 的成功 envelope 是 `{items, total, totals, meta}`。当前 `items[]` 序列化的是完整 `SpuProfitability` dataclass 加五个 computed properties，因此仍包含历史 SPU 行级 projection 字段；当前 adapter 不接受 `projection_lookback_days`，也没有完整的 `meta.projection.sample_*` 契约。下面只示意当前兼容事实，不是目标响应：

```json
{
  "items": [{
    "spu_pk": 912, "spu_id": "1730000000000000001", "title": "Example",
    "status": "ACTIVATE", "shop_id": "7494763368967603447", "shop_name": "VN shop",
    "spend": "100.0000", "sales": "1200.0000", "net_profit": "80.0000",
    "roi_real": "1.20", "profit_status": "profit", "roi_status": "non_negative",
    "projection_status": "available", "projected_net_profit": "70.0000",
    "has_unsettled_orders": true, "uses_default_unit_cost": false,
    "refund_rate_alert": false
  }],
  "total": 1,
  "totals": {
    "row_count": 1, "total_orders": 12, "sales": "1200.0000",
    "net_profit": "80.0000", "projection_status": "available",
    "completed_full_loss_rate": "0.1000"
  },
  "meta": {
    "computed_at": "2026-10-08T10:00:00+00:00", "rubric_version": "v10",
    "currency": {"display": "CNY", "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"}},
    "fx": {"snapshot_id": 7, "usd_cny": "6.7686473", "as_of_at": "2026-10-08T00:00:00+00:00"},
    "projection": {"date_attribution": "COALESCE(order_time, paid_at)", "full_loss_rate_source": "已完结全损订单数 ÷ 全部已完结订单数"}
  }
}
```

兼容字段只为已部署 consumer 和旧页面保留。新客户端不得读取 `items[].projection_*`、`items[].projected_*` 或把兼容 `meta.projection` 当成支持 30/90 日切换的完整 basis；预测展示必须等待目标 wire。示例中的 `meta.projection.full_loss_rate_source` 描述目标预测使用的已完结严格全损率，不会重新定义 stable wire 的兼容 `full_loss_rate`。

### 6.4 主端点响应：目标 wire（目标契约）

目标主端点仍使用 `{items,total,totals,meta}`，但 `items[]` 只承载当前 SPU 行字段和当前 computed status；**不得包含任何 SPU 行级 `projection_*` 或 `projected_*` 字段**。预测只在 `totals` 和 `meta.projection` 出现。以下是目标形状，不能在当前 adapter 未改造前作为已上线响应：

```json
{
  "items": [{
    "spu_pk": 912, "spu_id": "1730000000000000001", "title": "Example",
    "status": "ACTIVATE", "main_image_url": null,
    "shop_id": "7494763368967603447", "shop_name": "VN shop",
    "spend": "100.0000", "sales": "1200.0000", "net_profit": "80.0000",
    "roi_real": "1.20", "profit_status": "profit", "roi_status": "non_negative",
    "has_unsettled_orders": true, "uses_default_unit_cost": false,
    "refund_rate_alert": false
  }],
  "total": 1,
  "totals": {
    "row_count": 1, "total_orders": 12, "sales": "1200.0000",
    "net_profit": "80.0000", "projection_status": "available",
    "projected_net_revenue": "1100.0000", "projected_net_profit": "70.0000",
    "completed_full_loss_rate": "0.1000"
  },
  "meta": {
    "computed_at": "2026-10-08T10:00:00+00:00", "rubric_version": "v10",
    "projection": {
      "status": "available", "lookback_days": 30,
      "maturity_lag_days": 7, "as_of": "2026-10-08",
      "sample_start": "2026-09-01", "sample_end": "2026-09-30",
      "basis_order_count": 42, "basis_full_loss_order_count": 4,
      "scope": "shop_pk=314 plus applied SPU selection"
    }
  }
}
```

目标 `projection_lookback_days=30|90` 仅影响目标 `totals` prediction 和 `meta.projection` basis；`w_start/w_end` 仍只影响当前事实和 evidence。目标 wire 与当前兼容 wire 并存期间，adapter 必须显式版本/能力切换或保持旧字段，不得把目标字段伪装为当前可用。

### 6.5 四个钻取端点

四个 endpoint 都是 readonly、`spu_pk` 必须是正整数，`w_start/w_end` 与主表相同；未知 SPU 404，参数错误 422，认证失败 401/403，汇率缺失 503。浏览器首次激活请求，主表 scope 变化清 cache。下列 JSON 类型规则适用于四端点：所有 Decimal 均序列化为 JSON **字符串**；money（包括 `amount_vnd`）固定 4 位小数字符串；`share_ratio` 固定 4 位字符串；其他 ratio 按 adapter 的 ratio serializer 为 2 位字符串；int 为 JSON 整数，bool 为 JSON 布尔值，date 为 ISO `YYYY-MM-DD` 字符串，datetime 为 ISO-8601 字符串（当前响应带 UTC offset），nullable 字段明确写 `null`。未知枚举/状态原始代码原样透传。

#### 6.5.1 orders

`GET /v2/analytics/spu-roi/{spu_pk}/orders?w_start=2026-09-01&w_end=2026-09-30`

```json
{
  "spu_pk": 912, "spu_id": "1730000000000000001",
  "window": {"w_start": "2026-09-01", "w_end": "2026-09-30"},
  "orders": [{
    "order_id": "5800001", "status": "DELIVERED", "qty": 2,
    "line_gmv": "160.0000", "paid_at": "2026-09-03T08:00:00+00:00",
    "is_settled": true, "settled_net_share": "145.0000",
    "arrived_overseas": true, "full_loss": false,
    "shipment": {"status": "DELIVERED", "tracking_number": "TN1"},
    "tracking": [{"action_code": 50101, "desc": "Delivered", "event_at": "2026-09-05T08:00:00+00:00"}]
  }],
  "meta": {"orders_truncated": false, "rubric_version": "v9", "computed_at": "2026-10-08T10:00:00+00:00", "currency": {"display": "CNY"}}
}
```

`spu_pk` 为 JSON int、非 null、正数；`spu_id` 为 string/null；`window.w_start/w_end` 为 date string/null；`order_id`、`status` 为 string/null；`qty` 为 JSON int、非负；`line_gmv` 与 `settled_net_share` 为 CNY money string/null；`paid_at` 为 datetime string/null；`is_settled`、`arrived_overseas`、`full_loss` 为 JSON bool、非 null；`shipment` 为 object/null，`shipment.status` 和 `tracking_number` 为 string/null；`tracking[]` 的 `action_code` 为 int/null，`desc` 为 string/null，`event_at` 为 datetime/null。上限 500，达到上限 `meta.orders_truncated=true`。

#### 6.5.2 settlements

`GET /v2/analytics/spu-roi/{spu_pk}/settlements?w_start=2026-09-01&w_end=2026-09-30`

```json
{
  "spu_pk": 912,
  "settlements": [{
    "order_id": "5800001", "status": "DELIVERED", "statement_time": "2026-09-15T00:00:00+00:00",
    "share_ratio": "0.5000",
    "components": [
      {"code": "SETTLEMENT", "amount_vnd": "190000.0000", "amount": "48.0000"},
      {"code": "PLATFORM_COMMISSION", "amount_vnd": "-12000.0000", "amount": "-3.0400"}
    ]
  }],
  "meta": {"rubric_version": "v9", "computed_at": "2026-10-08T10:00:00+00:00", "currency": {"display": "CNY", "settlement_component_native": "VND"}}
}
```

`spu_pk` 为 int；`settlements[]` 每项的 `order_id`、`status` 为 string/null，`statement_time` 为 datetime string/null，`share_ratio` 为 ratio string/null 且 4 位；`components[]` 的 `code` 为 string/null，`amount_vnd` 为 VND money string/null 且 4 位，`amount` 为 CNY money string/null 且 4 位。未结算订单不在数组中；component code 未知时仍返回原始字符串。

#### 6.5.3 cases

`GET /v2/analytics/spu-roi/{spu_pk}/cases?w_start=2026-09-01&w_end=2026-09-30`

```json
{
  "spu_pk": 912,
  "cases": [{
    "case_id": "CASE-1", "order_id": "5800001", "type": "RETURN_AND_REFUND",
    "status": "RETURN_OR_REFUND_REQUEST_COMPLETE", "refund_amount": "20.0000",
    "reason": "DAMAGED: 商品破损", "updated_at": "2026-09-10T08:00:00+00:00"
  }],
  "meta": {"rubric_version": "v9", "computed_at": "2026-10-08T10:00:00+00:00", "currency": {"display": "CNY"}}
}
```

`spu_pk` 为 int；`case_id`、`order_id`、`type`、`status`、`reason` 为 string/null；`refund_amount` 为 CNY money string/null、4 位；`updated_at` 为 datetime string/null。当前 SQL 只返回完成状态的 `REFUND_ONLY`/`RETURN_AND_REFUND` 案例；未知 type/status/reason code 原样返回，不被前端猜测。

#### 6.5.4 ads

`GET /v2/analytics/spu-roi/{spu_pk}/ads?w_start=2026-09-01&w_end=2026-09-30`

```json
{
  "spu_pk": 912,
  "ads": [{"campaign_id": "CAM-1", "spend": "10.0000", "orders": 3, "first_day": "2026-09-01", "last_day": "2026-09-30"}],
  "meta": {"rubric_version": "v9", "computed_at": "2026-10-08T10:00:00+00:00", "note": "广告域与日期窗口同语义裁剪（v8）", "currency": {"display": "CNY", "native": "USD"}}
}
```

`spu_pk` 为 int；`campaign_id` 为 string/null；`spend` 为 CNY money string/null、4 位（源 `mixed_real_cost` USD）；`orders` 为 JSON int、非负；`first_day`/`last_day` 为 date string/null。当前 implementation 读取 `plugin.ad_daily` 的 post_product_list，目标保持该单一来源；不要把 `ad_today` 拼入历史。

### 6.6 页面端点和辅助端点示例

页面端点返回服务端 HTML shell，不返回盈利数字；浏览器必须先通过 session cookie 鉴权。静态资源由相对路径解析到 `/static/*` 或反代前缀下的 `/tts/static/*`。

```http
GET /v2/pages/spu-roi
Cookie: tts_erp_session=v2....
Accept: text/html
```

成功为 `200 text/html`，页面标题为“SPU 实际 ROI · tts-erp”，包含 `#shop-switcher`、`#summaries`、`#toolbar`、`#rows`、钻取模板和 `spu-profitability-page.js`。`GET /v2/pages/focused-spus` 同样返回 `200 text/html`，profile 为 `focused-spus`；无 cookie 为 `302 /v2/auth/login?next=/v2/pages/spu-roi`，已登录但缺页面权限为 403 HTML。页面端点只负责 shell，数据仍由主端点提供。

页面初始化的辅助只读请求示例：

```http
GET /v2/commerce/channel-accounts?platform=tiktok&limit=100&offset=0
X-API-Key: ttserp_ro_...
```

```json
[{"id":314,"platform":"tiktok","shop_id":"7494763368967603447","account_name":"VN shop","region":"VN","status":"active","synced_at":"2026-10-08T09:55:00Z"}]
```

`id` 是后续 `shop_pk`；其余字段由 Commerce live contract 定义，缺失店铺列表时页面不能请求无店铺 overview。

```http
GET /v2/commerce/channel-product-options?shop_pk=314&q=1730&limit=50
X-API-Key: ttserp_ro_...
```

```json
[{"spu_id":"1730000000000000001","title":"Example","status":"ACTIVATE"}]
```

Tom Select 搜索和批量粘贴都使用此 endpoint；批量粘贴另带 `spu_ids` 精确匹配，服务端最多返回 100 个选项。

```http
GET /v2/sync/freshness?shop_pk=314
X-API-Key: ttserp_ro_...
```

```json
{"server_time":"2026-10-08T10:00:00Z","shop_pk":314,"shop_id":"7494763368967603447","sources":[{"key":"orders","label":"订单","synced_at":"2026-10-08T09:55:00Z","scope":"shop","basis":"finished_at","job_name":"tiktok.orders","last_status":"success","severity":"ok","detail":null}]}
```

实际 `sources` 固定包含 `ads`、`orders`、`logistics`、`miaoshou`；页面另以主响应 `meta.fx.as_of_at` 渲染汇率灯。此 endpoint 零上游外呼，unknown/stale 只改变灯和 tooltip，不改变盈利结果。

```http
GET /v2/config/enum-map
Cookie: tts_erp_session=v2....
Accept: application/json
```

```json
{"profit_status":{"loss":"亏损","profit":"盈利","break_even":"持平"},"column_header":{"net_profit":"净利润"}}
```

enum map 缺失或无某 code 时，kernel 显示后端原始 code；它不是计算规则来源。认证、Commerce、Sync、Enum-map 的完整公共契约继续由 `docs/api/external-api.md` 和对应 route 实现负责。

### 6.7 主表字段逐项类型、出现范围和序列化

字段矩阵直接按 `SpuProfitability`、`ProfitabilityTotals` dataclass 和 adapter 的 `_row_payload()`/`_totals_payload()` 生成规则整理。`SpuProfitability` 字段只出现在 `items[]`；`ProfitabilityTotals` 字段只出现在 `totals`；两者交集才同时出现。computed properties 不属于 dataclass 字段，单独列出。不得把 row-only 字段写成 totals，也不得把 totals-only 字段写成 row。业务含义/公式仍以 business 文档为准。

**当前 stable wire 的全损字段必须按 v9 兼容口径解读，不得称为严格全损。** 源码 `_formula_v10.calculate_order_metrics()` 明确为 `full_loss_order_count = refund_order_count + overseas_cancelled_order_count`，其中 `refund_order_count` 是已完成退款订单（可包含部分退款），`full_loss_rate = full_loss_order_count / total_orders`。源码 `_SQL_ROI_FULL_LOSS` 的 `full_loss_qty` 是已完成 `REFUND_ONLY`/`RETURN_AND_REFUND` case lines 的 `quantity` 加上海外取消（`CANCELLED` 且命中 action `38301`）订单行的 `quantity`，因此也可能包含部分退款件数；`full_loss_qty_rate` 继续使用当前代码的件数分母。该兼容语义对应 [business §11.4（第 4 项）](../business/spu-profitability.md#11-当前实现与目标契约的已知差异)，目标严格全损会排除部分退款；目标实现切换前，stable wire 不能解释为严格全损。带 `projection_` 的目标预测字段若明确标注“严格全损”，则遵循目标契约，不回写当前兼容字段。

序列化规则：Decimal 全部是 JSON string；`_MONEY_FIELDS` 为 4 位，`_RATIO_FIELDS` 为 2 位，`_FOUR_DECIMAL_RATIO_FIELDS` 为 4 位，totals 的 `refund_rate`/`full_loss_rate`/`cancel_rate` 也为 4 位；未进入这些集合的 Decimal（当前为预测数量）由 adapter `str()` 原样输出。date/datetime 为 ISO 字符串，int/bool 为 JSON 原生类型，Enum 为其 code string。

#### 6.7.1 仅 `items[]` 的 row-only 字段

| 字段 | 出现 | JSON 类型/精度 | 含义 | nullable |
| --- | --- | --- | --- | --- |
| `spu_pk` | `items[]` | JSON int | `commerce.products_spu.id` 内部 SPU 主键 | 非 null |
| `spu_id` | `items[]` | JSON string | 平台业务 SPU 编号 | 可 null |
| `title` | `items[]` | JSON string | 商品标题 | 可 null |
| `status` | `items[]` | JSON string | 商品目录状态原值 | 可 null |
| `main_image_url` | `items[]` | JSON string | 商品主图 URL | 可 null |
| `shop_id` | `items[]` | JSON string | 上游店铺编号 | 可 null |
| `shop_name` | `items[]` | JSON string | 店铺名称 | 可 null |
| `ad_count` | `items[]` | JSON int | 投放 campaign 数 | 非 null |
| `ad_orders` | `items[]` | JSON int | 广告归因出单量 | 非 null |
| `gmv_ad` | `items[]` | JSON string（money，4 位） | 广告归因 GMV | 非 null |
| `roi_l0` | `items[]` | JSON string（ratio，2 位） | 广告归因 GMV/广告消耗兼容比率 | 可 null |
| `ad_first_day` | `items[]` | JSON string（ISO date） | 广告观测起始日 | 可 null |
| `ad_last_day` | `items[]` | JSON string（ISO date） | 广告观测结束日 | 可 null |
| `units_sold` | `items[]` | JSON int | 售出件数 | 非 null |
| `gmv_sales` | `items[]` | JSON string（money，4 位） | 含取消的销售金额 | 非 null |
| `refund_rate_qty` | `items[]` | JSON string（ratio，2 位） | 按件退款率 | 可 null |
| `refund_only_qty` | `items[]` | JSON int | 仅退款件数 | 非 null |
| `refund_only_amount` | `items[]` | JSON string（money，4 位） | 仅退款金额 | 非 null |
| `refund_return_qty` | `items[]` | JSON int | 退货退款件数 | 非 null |
| `refund_return_amount` | `items[]` | JSON string（money，4 位） | 退货退款金额 | 非 null |
| `refund_net_qty` | `items[]` | JSON int | 净退款件数 | 非 null |
| `refund_amount_rate` | `items[]` | JSON string（ratio，2 位） | 兼容退款金额率 | 可 null |
| `refund_cancelled_qty` | `items[]` | JSON int | 已付款被取消件数 | 非 null |
| `refund_cancelled_amount` | `items[]` | JSON string（money，4 位） | 已付款被取消金额 | 非 null |
| `refund_cancelled_missing_lines` | `items[]` | JSON int | 无法归属商品行的取消 case 行数 | 非 null |
| `platform_fee` | `items[]` | JSON string（money，4 位） | 未结算平台费信息列 | 非 null |
| `cpa` | `items[]` | JSON string（money，4 位） | 广告消耗/广告归因订单 | 可 null |
| `unit_cost_used` | `items[]` | JSON string（money，4 位） | 本次采用的单位采购成本 | 非 null |
| `cost_source` | `items[]` | JSON string | 单位成本来源 | 非 null |
| `net_revenue` | `items[]` | JSON string（money，4 位） | 净收入 | 非 null |
| `settled_net` | `items[]` | JSON string（money，4 位） | 已结算净收入 | 非 null |
| `unsettled_net` | `items[]` | JSON string（money，4 位） | 未结算净收入估算 | 非 null |
| `settled_sales` | `items[]` | JSON string（money，4 位） | 已结算销售额 | 非 null |
| `unsettled_sales` | `items[]` | JSON string（money，4 位） | 未结算销售额 | 非 null |
| `cogs_sold` | `items[]` | JSON string（money，4 位） | 售出货本 | 非 null |
| `cogs_full_loss_cancelled` | `items[]` | JSON string（money，4 位） | v9 兼容海外取消桶货本，不等同于目标严格全损货本 | 非 null |
| `cogs_total` | `items[]` | JSON string（money，4 位） | 总货本 | 非 null |
| `settled_order_count` | `items[]` | JSON int | 已结算订单数 | 非 null |
| `full_loss_qty_rate` | `items[]` | JSON string（ratio，2 位） | v9 兼容件数率：`full_loss_qty / (units_sold + full_loss_cancelled_qty)`；沿用兼容 `full_loss_qty`，不是目标严格全损率 | 可 null |
| `fee_rate_used` | `items[]` | JSON string（ratio，4 位） | 本次采用的平台费率 | 非 null |
| `fee_source` | `items[]` | JSON string | 平台费率来源 | 非 null |

#### 6.7.2 仅 `totals` 的 totals-only 字段

| 字段 | 出现 | JSON 类型/精度 | 含义 | nullable |
| --- | --- | --- | --- | --- |
| `row_count` | `totals` | JSON int | 大盘 SPU 行数 | 非 null |
| `gmv` | `totals` | JSON string（money，4 位） | 全范围含取消 GMV | 非 null |

#### 6.7.3 同时出现在 `items[]` 和 `totals` 的 shared 字段

| 字段 | 出现 | JSON 类型/精度 | 含义 | nullable |
| --- | --- | --- | --- | --- |
| `spend` | `items[] + totals` | JSON string（money，4 位） | 广告实际消耗 | 非 null |
| `order_count` | `items[] + totals` | JSON int | 已付款白名单有效销售订单数 | 非 null |
| `cancelled_order_count` | `items[] + totals` | JSON int | CANCELLED 订单数 | 非 null |
| `total_orders` | `items[] + totals` | JSON int | 有效销售与取消订单总数 | 非 null |
| `effective_order_count` | `items[] + totals` | JSON int | 有效订单数 | 非 null |
| `refund_order_count` | `items[] + totals` | JSON int | 已确认退款订单数 | 非 null |
| `full_loss_order_count` | `items[] + totals` | JSON int | **v9 兼容计数**：`refund_order_count + overseas_cancelled_order_count`；含已完成退款订单，可能包含部分退款，不是严格全损订单数 | 非 null |
| `domestic_cancelled_order_count` | `items[] + totals` | JSON int | 国内取消订单数 | 非 null |
| `overseas_cancelled_order_count` | `items[] + totals` | JSON int | 海外取消订单数 | 非 null |
| `sales` | `items[] + totals` | JSON string（money，4 位） | 销售行金额 | 非 null |
| `effective_sales` | `items[] + totals` | JSON string（money，4 位） | 有效销售金额 | 非 null |
| `cancel_rate` | `items[] + totals` | JSON string（items ratio 2 位；totals ratio 4 位） | 国内取消率 | 可 null |
| `refund_net_amount` | `items[] + totals` | JSON string（money，4 位） | 净退款金额 | 非 null |
| `refund_rate` | `items[] + totals` | JSON string（items ratio 2 位；totals ratio 4 位） | 退款率 | 可 null |
| `return_loss` | `items[] + totals` | JSON string（money，4 位） | 当前兼容货损：源码按兼容 `full_loss_qty × unit_cost_used` 计算；不得解释为目标严格全损货损 | 非 null |
| `net_profit` | `items[] + totals` | JSON string（money，4 位） | 净利润 | 非 null |
| `roi_real` | `items[] + totals` | JSON string（ratio，2 位） | 实际 ROI | 可 null |
| `roi_breakeven` | `items[] + totals` | JSON string（ratio，2 位） | 实际保本 ROI | 可 null |
| `full_loss_qty` | `items[] + totals` | JSON int | **v9 兼容件数**：已完成 `REFUND_ONLY`/`RETURN_AND_REFUND` case line `quantity` + 海外取消 `CANCELLED` 行 `quantity`；可能包含部分退款件数，不是严格全损件数 | 非 null |
| `full_loss_cancelled_qty` | `items[] + totals` | JSON int | **v9 兼容海外取消桶件数**：命中 action `38301` 的 `CANCELLED` 行 `quantity`，计入兼容 `full_loss_qty`；不得解释为目标严格全损件数 | 非 null |
| `full_loss_rate` | `items[] + totals` | JSON string（items ratio 2 位；totals ratio 4 位） | **v9 兼容率**：`full_loss_order_count / total_orders`，即 `(refund_order_count + overseas_cancelled_order_count) / total_orders`；可能包含部分退款，不是目标严格全损率 | 可 null |
| `ad_system_actual_roi` | `items[] + totals` | JSON string（ratio，2 位） | 广告系统实际 ROI | 可 null |
| `ad_system_breakeven_roi` | `items[] + totals` | JSON string（ratio，2 位） | 广告系统保本 ROI | 可 null |
| `ad_system_max_ad_spend` | `items[] + totals` | JSON string（money，4 位） | 最大可承受广告费 | 非 null |
| `ad_system_remaining_ad_spend_capacity` | `items[] + totals` | JSON string（money，4 位） | 剩余可承受广告费 | 非 null |
| `ad_system_breakeven_roi_status` | `items[] + totals` | JSON string（enum code） | 广告系统保本 ROI 计算状态 | 非 null |
| `projection_status` | `items[] + totals` | JSON string（enum code） | 大盘预测状态（兼容行字段仅为旧 consumer 保留） | 非 null |
| `projection_basis_order_count` | `items[] + totals` | JSON int | 预测基础样本订单数 | 非 null |
| `projection_basis_qty` | `items[] + totals` | JSON int | 预测基础样本件数 | 非 null |
| `projection_basis_sales` | `items[] + totals` | JSON string（money，4 位） | 预测基础样本销售额 | 非 null |
| `projection_basis_refund_amount` | `items[] + totals` | JSON string（money，4 位） | 预测基础样本退款金额 | 非 null |
| `projection_terminal_basis_order_count` | `items[] + totals` | JSON int | 物流终态样本订单数 | 非 null |
| `projection_terminal_basis_sales` | `items[] + totals` | JSON string（money，4 位） | 物流终态样本销售额 | 非 null |
| `projection_terminal_full_loss_sales` | `items[] + totals` | JSON string（money，4 位） | 预测诊断：物流终态严格全损销售额；不回写 stable `full_loss_*` 字段 | 非 null |
| `projection_terminal_full_loss_order_count` | `items[] + totals` | JSON int | 预测诊断：物流终态严格全损订单数；不回写 stable `full_loss_order_count` | 非 null |
| `projection_terminal_full_loss_qty` | `items[] + totals` | JSON int | 预测诊断：物流终态严格全损件数；不回写 stable `full_loss_qty` | 非 null |
| `projection_completed_basis_order_count` | `items[] + totals` | JSON int | 已完结预测分母订单数 | 非 null |
| `projection_completed_full_loss_order_count` | `items[] + totals` | JSON int | **目标预测字段**：已完结严格全损分子订单数；不等同于 stable `full_loss_order_count` | 非 null |
| `projection_full_loss_basis_order_count` | `items[] + totals` | JSON int | 兼容物流全损样本分母 | 非 null |
| `projection_basis_full_loss_order_count` | `items[] + totals` | JSON int | 兼容已送达退款样本订单数 | 非 null |
| `projection_basis_full_loss_qty` | `items[] + totals` | JSON int | 兼容已送达退款样本全损件数，可能含部分退款；不等同于目标严格全损件数 | 非 null |
| `projection_refund_amount_rate` | `items[] + totals` | JSON string（ratio，4 位） | 兼容预测退款金额率 | 可 null |
| `pre_delivery_full_loss_rate` | `items[] + totals` | JSON string（ratio，4 位） | 兼容送达前全损率 | 可 null |
| `completed_full_loss_rate` | `items[] + totals` | JSON string（ratio，4 位） | **目标预测字段**：已完结订单严格全损率；不等同于 stable `full_loss_rate` | 可 null |
| `delivered_full_loss_rate` | `items[] + totals` | JSON string（ratio，4 位） | 兼容已送达全损率 | 可 null |
| `settled_full_loss_rate` | `items[] + totals` | JSON string（ratio，4 位） | 兼容已结算全损率 | 可 null |
| `projection_full_loss_qty_rate` | `items[] + totals` | JSON string（ratio，4 位） | 兼容件数预测率 | 可 null |
| `unsettled_order_count` | `items[] + totals` | JSON int | 未结算订单数 | 非 null |
| `delivered_unsettled_order_count` | `items[] + totals` | JSON int | 未结算但已送达订单数 | 非 null |
| `full_loss_exposure_unsettled_order_count` | `items[] + totals` | JSON int | 未结算未送达风险订单数 | 非 null |
| `full_loss_exposure_unsettled_sales` | `items[] + totals` | JSON string（money，4 位） | 风险订单销售额 | 非 null |
| `confirmed_full_loss_exposure_order_count` | `items[] + totals` | JSON int | 预测风险池已确认全损订单数；属于 projection 诊断，不是 stable `full_loss_order_count` | 非 null |
| `confirmed_full_loss_exposure_qty` | `items[] + totals` | JSON int | 预测风险池已确认全损件数；属于 projection 诊断，不是 stable `full_loss_qty` | 非 null |
| `confirmed_full_loss_exposure_refund_amount` | `items[] + totals` | JSON string（money，4 位） | 风险池已确认退款金额 | 非 null |
| `unresolved_unsettled_order_count` | `items[] + totals` | JSON int | 仍有未确认事实的未结算订单数 | 非 null |
| `unresolved_full_loss_exposure_order_count` | `items[] + totals` | JSON int | 仍有未确认事实的风险订单数 | 非 null |
| `unresolved_unsettled_qty` | `items[] + totals` | JSON int | 未结算待确认件数 | 非 null |
| `unresolved_full_loss_exposure_qty` | `items[] + totals` | JSON int | 风险池待确认件数 | 非 null |
| `unresolved_unsettled_sales` | `items[] + totals` | JSON string（money，4 位） | 未结算待确认销售额 | 非 null |
| `confirmed_unsettled_refund_amount` | `items[] + totals` | JSON string（money，4 位） | 未结算订单已确认退款金额 | 非 null |
| `confirmed_unsettled_full_loss_order_count` | `items[] + totals` | JSON int | 预测风险池外未结算已确认全损订单数；属于 projection 诊断 | 非 null |
| `confirmed_unsettled_full_loss_qty` | `items[] + totals` | JSON int | 预测风险池外未结算已确认全损件数；属于 projection 诊断 | 非 null |
| `projected_future_refund_amount` | `items[] + totals` | JSON string（money，4 位） | 预计未来退款金额 | 可 null |
| `projected_terminal_refund_amount` | `items[] + totals` | JSON string（money，4 位） | 预计终局退款金额 | 可 null |
| `projected_future_full_loss_order_count` | `items[] + totals` | JSON string（Decimal，adapter 原样 str，不提前取整） | 目标预测的预计未来新增严格全损订单数 | 可 null |
| `projected_future_full_loss_qty` | `items[] + totals` | JSON string（Decimal，adapter 原样 str，不提前取整） | 目标预测的预计未来新增严格全损件数 | 可 null |
| `projected_terminal_full_loss_qty` | `items[] + totals` | JSON string（Decimal，adapter 原样 str，不提前取整） | 目标预测的预计终局严格全损件数 | 可 null |
| `projected_full_loss_cost` | `items[] + totals` | JSON string（money，4 位） | 目标预测的预计终局严格全损货损 | 可 null |
| `projected_unsettled_net` | `items[] + totals` | JSON string（money，4 位） | 预计未结算净收入 | 可 null |
| `projected_net_revenue` | `items[] + totals` | JSON string（money，4 位） | 预计净收入 | 可 null |
| `projected_net_profit` | `items[] + totals` | JSON string（money，4 位） | 预计净利润 | 可 null |
| `projected_roi_real` | `items[] + totals` | JSON string（ratio，2 位） | 预计实际 ROI | 可 null |
| `projected_roi_breakeven` | `items[] + totals` | JSON string（ratio，2 位） | 预计保本 ROI | 可 null |
| `projected_nc_prime` | `items[] + totals` | JSON string（money，4 位） | 预计扣全损后的净收入 | 可 null |
| `projected_cogs_kept` | `items[] + totals` | JSON string（money，4 位） | 预计保留货本 | 可 null |
| `projected_ad_gmv` | `items[] + totals` | JSON string（money，4 位） | 预计广告归因 GMV | 可 null |
| `projected_ad_system_actual_roi` | `items[] + totals` | JSON string（ratio，2 位） | 预计广告系统实际 ROI | 可 null |
| `projected_ad_system_max_ad_spend` | `items[] + totals` | JSON string（money，4 位） | 预计最大可承受广告费 | 可 null |
| `projected_ad_system_breakeven_roi` | `items[] + totals` | JSON string（ratio，2 位） | 预计广告系统保本 ROI | 可 null |

#### 6.7.4 adapter computed properties

| 字段 | 出现 | JSON 类型 | 含义 | nullable |
| --- | --- | --- | --- | --- |
| `profit_status` | `items[] + totals` | JSON string | 领域根据 `net_profit` 返回 `loss|profit|break_even`；不是 dataclass field | 非 null |
| `roi_status` | `items[] + totals` | JSON string | 领域根据 `roi_real` 返回 `negative|non_negative|unavailable` | 非 null |
| `has_unsettled_orders` | `items[]` | JSON bool | 行是否存在未结算订单 | 非 null |
| `uses_default_unit_cost` | `items[]` | JSON bool | `cost_source == DEFAULT_K1` | 非 null |
| `refund_rate_alert` | `items[]` | JSON bool | 后端配置警戒线判定 | 非 null |

兼容 wire 的 row projection 字段（上表 `projection_*`/`projected_*` 中标为 `items[]` 的字段）只为现有 consumer 保留；目标 wire 的 `items[]` 必须删除这些字段，预测只能位于 `totals`/`meta.projection`。当前 `ProfitabilityTotals` 没有 `has_unsettled_orders`、`uses_default_unit_cost` 或 `refund_rate_alert`，因此这些字段不得出现在 totals。

### 6.8 `meta` 字段

- `computed_at`：UTC datetime；目标 overview/result/evidence 共享快照时间。
- 主表 overview 当前 `rubric_version` 为 `v10`；四个当前 drill payload 仍各自返回 `v9`，这是待修复的当前实现差异；目标所有结果统一由同一领域 basis 输出 v10，不能前端推断。
- `currency.display` 固定 `CNY`；`native.ad=USD`、`native.sales_refund=VND`、`native.cost=CNY`。
- `fx`：`snapshot_id` int、`usd_cny/cny_usd/usd_vnd/cny_vnd/vnd_cny` Decimal 字符串、`as_of/as_of_at`、`source=fx-cache`。缺完整 snapshot 返回 503。
- `fee`：`mode` 是旧 alias（override/baseline）；`source=user_override|shop_estimate|baseline|mixed`；`rate`/`override` 4 位；`degraded` bool；`fallback_message`；`per_shop[]` 的 `shop_pk/shop_name/rate/source/fallback_reason/estimate`。
- `window`：广告和 coverage 的首尾日期、窗口说明；目标还需明确 `w_start/w_end` 本地解释。
- `warnings[]`：当前代码实际可能有 `default_unit_cost_used`、`unsettled_orders_estimated`、projection warning；未知 warning 原样显示。
- `presentation`：`rubric_label`、退款警戒阈值、默认成本/未结算提示、`pnl_hints`；前端只显示。
- `projection` 目标至少包含 `status`、`lookback_days`、`maturity_lag_days`、`as_of`、`sample_start/end`、basis counts、范围说明、warning 和 status labels；当前没有完整字段，不能把当前 meta 当目标契约。

### 7.1 实际稳定代码值

| 类别 | 代码值 | 中文含义 | 来源/未知处理 |
| --- | --- | --- | --- |
| selection | `activity`（`ActivitySelection`）、`exact_ids`、`focused` | 活动范围、精确 SPU、重点关注 | Python 类型；未知 selection 422 |
| evidence | `orders`、`settlements`、`cases`、`ads` | 订单物流、结算、售后、广告 | `EvidenceKind`；未知 tab 不发请求 |
| sort | `roi_real`,`spend`,`ad_system_actual_roi`,`ad_system_breakeven_roi`,`refund_rate`,`refund_rate_qty`,`cancel_rate`,`net_profit`,`sales`,`effective_sales`,`gmv_sales`,`ad_count`,`gmv_ad`,`order_count`,`total_orders`,`effective_order_count`,`cancelled_order_count`,`units_sold`,`refund_net_amount`,`return_loss`,`roi_breakeven`,`full_loss_rate` | 对应后端排序字段 | `SortField` 当前全集；未知 422 |
| order | `asc`,`desc` | 升序、降序 | `SortDirection`；目标未知 422 |
| formula status | `calculated`,`estimated_known_costs` | 已覆盖成本、已知成本下限估算 | `FormulaStatus`；未知 raw code + warning |
| projection | `available`,`no_unsettled_orders`,`insufficient_sample` | 可预测、无未结算订单、样本不足 | `ProjectionStatus`；未知 raw code，不当 0 |
| profit | `loss`,`profit`,`break_even` | 亏损、盈利、持平 | `_profit_status`；未知 raw code |
| ROI | `negative`,`non_negative`,`unavailable` | 负、非负、无解 | `_roi_status`；null 为 unavailable |
| fee source | `user_override`,`shop_estimate`,`baseline`,`mixed` | 页面覆写、店铺实测、全局基线、多店混合 | row 不出现 mixed；未知 raw code |
| cost source | `MANUAL`,`DEFAULT_K1` | 当前有效人工采购价、40 CNY/件默认 | 当前源码稳定全集；未知 raw code并标数据异常 |
| case type | `REFUND_ONLY`,`RETURN_AND_REFUND`,`CANCELLATION`,`CANCEL` | 仅退款、退货退款、取消 | business/source DB；未知不纳入计算但 evidence 原值显示 |
| case completed | `RETURN_OR_REFUND_REQUEST_COMPLETE`,`CANCELLATION_REQUEST_COMPLETE` | 退货/退款完成、取消完成 | `_CASE_COMPLETED_STATUSES`；其他 status 非完成 |
| tracking | `38301`,`50101`,`80101` | 到达目的国、已送达、退回卖家终局 | implementation constants；未知 action 只保留 evidence |
| order paid | `AWAITING_SHIPMENT`,`PARTIAL_SHIPPING`,`AWAITING_COLLECTION`,`IN_TRANSIT`,`DELIVERED`,`COMPLETED` | 已付款白名单状态 | `PAID_SALES_ORDER_STATUSES`；`UNPAID/ON_HOLD` 不进 paid facts；`CANCELLED` 单独分类 |
| sync severity | `ok`,`warn`,`crit`,`unknown` | 正常、滞后、严重滞后、未知 | `/v2/sync/freshness`；未知按 unknown |

业务文档明确但源码没有稳定全集的枚举（例如上游扩展 status、case reason、tracking desc）必须按“不翻译未知值、保留 raw code、记录 warning、不得进入稳定条件分支”处理。`GET /v2/config/enum-map` 可提供中文显示映射；缺映射时 JS `tr()` 返回原值。

### 7.2 HTTP 错误矩阵

| HTTP | code/detail | 条件 | 前端动作 |
| --- | --- | --- | --- |
| 401 | missing/invalid credential | 未认证、过期 key/cookie | HTML 跳登录；JSON 显示登录入口 |
| 403 | requires role/page | readonly 之外的页面权限问题 | 显示无权限，不重试 |
| 404 | resource not found | drill `spu_pk` 不存在 | 关闭该面板并提示资源不存在 |
| 409 | domain conflict | 仅保留通用 API 语义；盈利 GET 不应产生 | 显示 request id |
| 422 | FastAPI detail | 参数、日期、scope、fee、sort 无效 | 保留输入，修正后重试 |
| 429 | rate limit exceeded | shared sliding window | 使用 Retry-After 倒计时，禁止立即循环 |
| 503 | `FX_RATE_UNAVAILABLE` | FX 缺失/不完整 | 显示“汇率不可用”，稍后重试；不渲染半套金额 |
| 500 | unexpected | 服务异常 | 显示通用错误和 request id，记录日志 |

### 7.3 限流、缓存和幂等边界

盈利 GET 是只读、无写副作用，不需要业务幂等 key；每请求都可能读到新的约十分钟同步事实，但请求内必须快照一致。服务端不承诺跨请求强一致，也不把结果永久缓存。前端只缓存当前页面生命周期的钻取成功响应，key 包含 SPU、tab、窗口、scope fingerprint 和 projection policy；任何范围变化清空。同步/认证的限流由 middleware 统一执行，不能在页面重复请求风暴中绕过。

## 8. 数据源和状态来源

| 事实 | 实际表/字段 | 处理 |
| --- | --- | --- |
| 店铺 | `commerce.shops.id/shop_id/region/account_name` | `id=shop_pk`；region 映射时区 |
| SPU | `commerce.products_spu.id/spu_id/title/status/main_image_url` | 目录和展示 |
| 商品行 | `commerce.sales_order_lines.order_pk/spu_pk/quantity/unit_price` | 行 GMV、件数、SPU scope |
| 订单 | `commerce.sales_orders.status/order_time/paid_at` | `COALESCE(order_time,paid_at)` 归属 |
| 结算 | `finance.settlement_transactions`、`finance.settlement_components` | `SETTLEMENT` 实际到账、`CUSTOMER_REFUND` 退款；按订单聚合、行 GMV 比例分摊 |
| 售后 | `after_sales.cases/case_lines` | 完成 case、退款/退货数量和金额 |
| 物流 | `fulfillment.shipments/tracking_events` | delivery/overseas/terminal evidence |
| 广告 | `plugin.ad_daily` | `mixed_real_cost`、`onsite_roi2_shopping_value`、`onsite_roi2_shopping_sku`；按 endpoint 和 day 过滤 |
| 成本 | `procurement.manual_product_costs.unit_cost valid_to IS NULL` | 缺失使用 40 CNY/件；`procurement_products.source_unit_cost` 不进入当前目标口径 |
| 费率 | `reporting.shop_fee_rate_estimates` | 新鲜 `fee-v2` 实测；缺失/过期 0.308；页面覆写优先 |
| 汇率 | `fx.exchange_rate_snapshots/exchange_rates` | 统一 snapshot；广告 USD、销售/退款 VND 转 CNY |
| 重点 SPU | `reporting.focused_spus` | active membership；按店铺隔离 |

当前 `_implementation.py` SQL CTE 负责 selected order 去重、settlement、completed cases、delivery 和 projection facts；后续拆分时保持这些查询事实关系，不将大 facts 对象交给 HTTP adapter。

## 9. 部署、运维和回滚

### 9.1 入口和环境

- Python 服务入口：`tts_erp_v2.app:app`，FastAPI/uvicorn，默认监听 `127.0.0.1:9877`。
- API 读取仓库根 `.env` 的 `TTS_ERP_DB_URL`、`TTS_ERP_AUTH_MODE`、`TTS_ERP_EXTERNAL_PREFIX`、`TTS_ERP_RATE_LIMIT_PER_MIN`、会话相关环境；不要把 secret 写入本文、HTML 或日志。
- FX 上游 key 只由 sync-worker 的 `EXCHANGERATE_API_KEY` 使用；盈利 API 只读本地 fx cache，禁止请求路径直拨上游。
- 静态文件来自 `tts_erp_v2/static`，模板来自 `tts_erp_v2/templates`；Bootstrap/Tom Select/Tabulator 自托管。

### 9.2 systemd、反向代理和健康检查

- `tts-erp.service` 托管 API；改 `api/`、analytics、templates 或 static 后执行 `bash restart.sh`（由人工/部署流水线执行）。
- `tts-erp-sync.service` 托管 APScheduler；本方案不修改 jobs/sync_worker，若后续改动这些目录，按项目规定单独 `systemctl --user restart tts-erp-sync.service`。
- 生产 nginx gateway 保留 `/tts/` 前缀并反代到 `127.0.0.1:9877`；应用从 `TTS_ERP_EXTERNAL_PREFIX` 派生 root path。模板静态链接必须保持相对路径，避免丢 `/tts`。
- 检查：`curl http://127.0.0.1:9877/healthz`、`GET /endpoints`、带 readonly key 的主端点和四路 drill；页面路径在未登录时应 302。
- 日志：`journalctl --user -u tts-erp -n 50`；同步：`journalctl --user -u tts-erp-sync -n 50`。日志只记录 request id、状态和错误摘要，不记录 API key、cookie、token、FX secret 或订单敏感 payload。

### 9.3 回滚

1. 先停止继续发布并保留服务日志、版本和响应样本。
2. 回退代码到上一已验证版本，执行 `bash restart.sh`；不删除数据库事实，不执行自动生产 migration。
3. 验证 `/healthz`、登录、主表、四路钻取和非盈利导航；确认旧客户端仍读到稳定字段。
4. 若问题只在静态资源，回退对应 JS/CSS 版本并清页面缓存；若 API 契约问题，优先恢复 adapter 兼容字段，再按新版本发布目标变更。
5. 数据库 schema 回滚由人工按项目 migration/备份规程执行，本方案不提供 destructive 命令。

## 10. 文件地图和实施顺序

### 10.1 文件地图

| 层 | 文件 |
| --- | --- |
| 业务口径 | `docs/business/spu-profitability.md` |
| 技术方案 | `docs/design/spu-profitability-technical-design.md` |
| 页面 route | `tts_erp_v2/api/v2/pages.py` |
| HTML | `tts_erp_v2/templates/pages/spu-profitability.html` |
| JS kernel/profile | `tts_erp_v2/static/js/spu-profitability-page.js`、`spu-roi.js`、`focused-spus.js` |
| CSS | `tts_erp_v2/static/css/spu-roi.css`、`focused-spus.css` |
| HTTP adapter | `tts_erp_v2/analytics/spu_roi.py` |
| module seam/types | `tts_erp_v2/analytics/spu_profitability/__init__.py`、`_types.py` |
| query/normalization | `_implementation.py`、`_selection.py` |
| formulas | `_formula_v10.py` |
| snapshot | `_snapshot.py` |
| API tests | `tests/api/test_spu_roi_api.py`、`test_spu_roi_fee_rate.py` |
| domain tests | `tests/analytics/test_spu_profitability_formula.py`、`test_spu_profitability_sorting.py` |
| page/e2e | `tests/e2e/test_spu_roi_smoke.py` |
| deployment context | `docs/architecture/process-architecture.md`、`docs/guides/commands-reference.md` |

### 10.2 分阶段顺序

1. **契约和类型**：新增 projection policy（30/90）、明确 basis/meta 类型、保留兼容 wire；先写参数/错误/枚举测试。
2. **快照和查询范围**：把经营窗口与 projection sample/risk window 分开；确认 shop timezone、selected SPU、订单去重和排序稳定。
3. **领域公式接线**：仅调用 `_formula_v10.py` 和 business 文档目标口径；移除当前窗口退款率驱动当前未结算收入的错误路径；保持当前/预测字段隔离。
4. **HTTP adapter**：加入 `projection_lookback_days`，序列化 meta projection，统一 drill 的 calculated_at；旧字段仅作兼容。
5. **前端**：加入 30/90 toggle、状态/错误/stale 呈现、请求竞态、钻取缓存 key 和无障碍检查；不复制公式。
6. **文档和导航**：本方案作为技术唯一入口，external-api 只保留索引；README、docs README、schema README、e2e 注释改链接。
7. **验证和发布**：isolated tests、diff/link/引用检查、人工页面检查，再由部署方重启 API；不得自动生产迁移。

## 11. 测试、验收和可观测性矩阵

| 领域 | 必须断言 |
| --- | --- |
| 参数 | shop/spu scope 互斥、q 规则、sort 全集、日期顺序、fee finite/non-negative、projection days 只接受 30/90 |
| 时间 | shop local date 半开区间；经营日期改变当前不改变 projection；样本 `[A-(D+6),A-7]` 的 30/90 选择；未知时区失败 |
| scope | activity/exact/focused；空 focused 不回退；SPU 选择同时约束当前和 prediction；q/分页/排序不改变 totals |
| 去重 | 多 SPU 同订单 totals 订单全局去重；金额按行；退款/取消/全损订单各按订单去重 |
| 当前 | 已结算不再扣费；未结算不乘当前窗口退款率；确认退款不重复；0 ad/无解返回 null |
| 预测 | 已完结订单分母/严格全损分子；国内取消入分母不入全损；已送达未结算排除风险池；0 样本 insufficient；1–9 warning；不自动 30→90 |
| 精度 | money 4 位；ratio 2 位；rate 4 位；预测数量 Decimal 不提前取整；null 不变 0 |
| HTTP | readonly/admin 200；无 key 401；角色/页面权限 403；unknown SPU 404；invalid 422；FX missing 503；429 Retry-After |
| drill | 四 endpoint 字段、500 截断、窗口、空/错误/重试、共用 calculated_at（目标） |
| 前端 | loading/empty/error/stale/partial/success；AbortController；旧响应不能覆盖新状态；scope 变化清 cache；键盘/ARIA；移动横滚 |
| 部署 | `/healthz`、`/endpoints`、`/v2/pages/*` 登录跳转、`/tts` 前缀、静态资源、systemd 日志和 rollback 检查 |

测试命令必须使用项目安全入口：

```bash
bash scripts/test_isolated.sh unit
bash scripts/test_isolated.sh api
bash scripts/test_isolated.sh e2e
bash scripts/test_isolated.sh fast
```

最小新增/更新测试文件是 `tests/api/test_spu_roi_api.py`（projection 参数和 meta）、`tests/analytics/test_spu_profitability_formula.py`（窗口隔离和样本）、`tests/analytics/test_spu_profitability_sorting.py`（scope/totals）、`tests/e2e/test_spu_roi_smoke.py`（仅文档注释路径）。不要直接运行 pytest，不要连接 `tts_erp`/`tts_erp_prod`。

可观测性必须记录：request id、shop_pk（内部值可记录）、scope 类型、lookback days、calculated_at、FX snapshot id、fee source、projection status、warning、返回 HTTP status、耗时和行/证据计数；禁止记录密钥、cookie、订单完整 payload。系统同步新鲜度由 `/v2/sync/freshness` 提供，API 不主动向上游补数。

## 12. 未决风险与明确处理

1. **当前 projection 仍受经营窗口影响**：在代码完成前，任何页面文案必须称其为当前实现；目标测试先锁定窗口隔离，再移除旧路径。
2. **当前 wire rubric/meta 分层不一致**：主表 overview basis 为 v10，而四个 drill payload 仍含 v9；保持旧字段直至版本化，新增目标字段必须显式标“目标契约”，不能覆盖旧字段含义。
3. **当前 drill `_detail_*` 自行生成 computed_at**：目标必须把 snapshot basis 传入，避免 UI 声称证据与行同一快照。
4. **广告日表覆盖缺口**：实现只读 `plugin.ad_daily`；数据新鲜度和 coverage 必须在页面呈现，不得用 `ad_today` 偷拼历史。
5. **结算外成本尚未结构化**：广告系统保本 ROI 继续状态 `estimated_known_costs` 并以 `≈` 展示，不得描述为最终财务保本线。
6. **上游枚举可扩展**：没有源码稳定全集的 status/reason/action 只 raw passthrough；不得根据未知值猜分类。
7. **同步延迟造成跨请求差异**：这是允许的实时读取性质；只要求请求内 repeatable-read，不承诺跨请求强一致。

方案完成条件是：业务文档无公式 diff；新技术文档内目标/当前标记清楚；external-api 不再承载盈利详细契约但保留导航；规范性活跃文档和当前代码注释不引用已删除的旧模块；`docs/archive/**` 与 `CHANGELOG.md` 的历史记录允许保留旧路径作为历史证据，且不构成当前事实源；链接和 diff 检查通过。
