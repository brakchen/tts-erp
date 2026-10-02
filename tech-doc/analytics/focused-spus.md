# 重点关注 SPU 页面技术方案

> 状态：Implemented，产品已确认并完成实现
> 日期：2026-09-29
> v4 修订：增加 PageProfile、受控 view projection 与演进规则，使两页可差异化而不分叉公共生命周期
> 关联现有页面：`GET /v2/pages/spu-roi`
> 口径真相源：`GET /v2/analytics/spu-roi`

## 1. 背景与目标

新增一个「重点关注 SPU」页面。运营人员先选择店铺，再通过输入、搜索或批量粘贴 SPU ID 维护该店铺的重点关注集合；页面只展示这个集合里的 SPU，并复用现有 SPU ROI 页的计算口径、字段、排序、日期筛选、汇总卡、钻取面板和响应式交互。

本功能解决的是“持久化关注范围”，不是再造一套盈利计算。重点关注页必须始终消费现有 SPU ROI API，避免同一指标出现两套 SQL 或两套前端公式。

## 2. 已确认需求

1. 新增独立页面，不替换现有 SPU ROI 页面。
2. 关注范围按店铺隔离；同一个 `spu_id` 在不同店铺互不影响。
3. 支持输入 SPU ID 新增关注，也支持在编辑状态中移除已关注 SPU。
4. 关注关系需要持久化，刷新页面或重新登录后仍保留。
5. 重点关注页的数据计算口径、展示逻辑和交互 UI 与 SPU ROI 页面一致。
6. 页面必须先选店铺，再读取和编辑该店铺的关注集合。

## 3. 非目标

- 不修改 SPU ROI 的利润公式、费率、汇率、退款、全损、取消或广告归因口径。
- 不新增第二个盈利聚合端点，不复制 `spu_profitability` 计算模块。
- 不向 TikTok Shop 写数据；关注关系只存在本地分析库。
- 不在 v1 支持跨店铺的“全局关注列表”。
- 不在 v1 支持关注分组、标签、备注、负责人或告警通知。

## 4. 产品与交互方案

### 4.1 页面入口

- 页面：`GET /v2/pages/focused-spus`
- 侧边栏：运营分组下新增「重点关注 SPU」
- 页面标题：`重点关注 SPU`
- 页面整体继续使用现有 SPU ROI 的 warm-paper 账页视觉、Bootstrap 布局和相同数据表，不创建另一套视觉语言。

### 4.2 页面结构

```text
┌─────────────────────────────────────────────────────────────┐
│ 重点关注 SPU                       店铺 [Bridge nook ▼]     │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页的费率提示条：置顶横幅，✕ 可关闭              │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页的汇总卡：总单量 / 广告 / 销售 / 退款 / 利润… │
├─────────────────────────────────────────────────────────────┤
│ 重点关注 SPU [多选：搜索 ID/标题或批量粘贴]                 │
│ 已关注 12 个 · 修改会实时保存                               │
│ 日期范围（昨天/近 7 天/近 30 天/本月/不限）/ 临时费率 /     │
│ 含无活动 / 刷新 / 排序状态                                  │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页主表与行内钻取面板                             │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页分页                                           │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 编辑行为

1. 必须先选择店铺；未选店铺时禁用多选框。
2. 页面内直接展示 Tom Select 多选框，复用 `channel-product-options`：
   - 输入 SPU ID 或标题搜索；
   - 支持中英文逗号、空格和换行批量粘贴；
   - 前端解析后精确校验 SPU 必须属于当前店铺。
3. 切换店铺时按 500 条一页读取完整关注集合并恢复多选值；关注总数不设业务上限，也不写入 URL。
4. 用户选择一个 SPU 后立即发送 `PATCH {addSpuIds:[...]}`；移除 tag 后立即发送 `PATCH {removeSpuIds:[...]}`，不再经过“编辑 → 草稿 → 保存”二步交互。
5. 快速连续增删通过前端 mutation queue 串行提交；批量粘贴最多每批 500 个 ID，与服务端单次 PATCH 上限一致。
6. 每次保存成功后更新关注计数并刷新 focused ROI；失败时回滚对应 tag，并保留明确错误提示。

### 4.4 空状态与过滤状态

- 当前店铺没有关注 SPU：仍请求安全的 `scope=focused`（后端返回空 overview），因此汇总大盘、筛选器、主表、分页和钻取 shell 与普通 SPU ROI 页保持一致；汇总显示 0/—，表格提示在上方多选框添加 SPU，绝不回退整店范围。
- 已关注但当前日期窗口没有活动：保留现有「含无活动」控制；重点关注页默认勾选，尽量让已关注的 ACTIVE SPU 可见。
- 已关注数量与当前表格命中数量分开展示，例如：`已关注 12 个 · 当前窗口展示 9 个`。
- 非 ACTIVE 且当前窗口无活动的 SPU 可能不进入 ROI 结果；编辑器仍显示其状态，页面给出未展示数量提示。

## 5. 核心设计决策

### D0. 三种架构方案比较与结论

本轮按 “Design It Twice” 并行比较了三种 interface：

1. **最小 interface**：盈利模块保留 `read_overview()` / `explain_spu()`；浏览器只暴露一个 `mount()`；关注集合模块只暴露 `list()` / `apply_patch()`。深度最高，调用方最少需要知道内部细节。
2. **可扩展 persisted-scope interface**：引入 provider registry、view-state adapter、projection registry，未来可承载标签、保存视图和其他持久范围。扩展性高，但当前只有 focused 一个持久范围，许多 seam 只有一个 adapter，属于假想 seam。
3. **端到端领域切片**：把 HTTP adapter、盈利 selection、页面 shell、浏览器 controller 和关注持久化分别放在清晰 seam 上，强调 transport、DOM 和 SQL 不互相泄漏。locality 最强，但必须避免把每一层都再包装一遍。

**结论：采用“最小 interface + 端到端 seam”的混合方案。**

- 后端使用具体的 `ActivitySelection | ExactIdsSelection | FocusedSelection`，不提前引入通用 provider registry。
- 浏览器只公开 `mountSpuProfitabilityPage()`；两种 selection adapter 是真实 seam，因为已有两个行为不同的 adapter。
- 关注持久化独立为一个深模块，FastAPI 只做 wire adapter。
- 页面 shell 是领域专用 module，不做通用 dashboard/table 框架。
- 引入两个真实 PageProfile 和受控 view projection，承接已明确会出现的页面差异；不引入动态 plugin registry、saved-view 或 repository port。

删除测试：如果删除公共盈利页面模块，汇总、表格、分页、钻取、错误生命周期会重新散落到两个页面；如果删除 selection adapter seam，两种互斥的编辑语义会重新混进公共文件。两者都能让复杂度明显重新出现，因此不是浅层转发。

### D1. 关注关系按 `(shop_pk, spu_id)` 存储

表名：`reporting.focused_spus`。

选择业务 SPU ID 而不是只存 `spu_pk`，原因：

- 用户的输入和识别对象就是 `spu_id`；
- `commerce.products_spu` 已有 `(shop_pk, spu_id)` 唯一约束；
- 复合外键可从数据库层保证 SPU 属于该店铺；
- 同一字符串 SPU ID 在不同店铺可以分别关注。

拟议结构：

```sql
CREATE TABLE reporting.focused_spus (
    shop_pk    bigint      NOT NULL,
    spu_id     text        NOT NULL,
    active     boolean     NOT NULL DEFAULT true,
    added_by   text,
    removed_by text,
    removed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shop_pk, spu_id),
    FOREIGN KEY (shop_pk)
      REFERENCES commerce.shops(id) ON DELETE RESTRICT,
    FOREIGN KEY (shop_pk, spu_id)
      REFERENCES commerce.products_spu(shop_pk, spu_id) ON DELETE RESTRICT
);
```

索引分两种真实访问路径：

```sql
-- 盈利 scope membership / count
CREATE INDEX ... ON reporting.focused_spus (shop_pk, spu_id)
WHERE active IS TRUE;

-- 关注管理列表的稳定排序
CREATE INDEX ... ON reporting.focused_spus (shop_pk, updated_at DESC, spu_id)
WHERE active IS TRUE;
```

继续使用现有 `public.fn_touch_updated_at()` 维护 `updated_at`。

### D2. 移除关注采用软删除

编辑器中的“删除”在数据库中表现为 `active=false + removed_at`，不执行物理 `DELETE`：

- 保留当前状态和最近一次增删的审计元数据；
- 重新关注时可原行恢复；
- 避免为日常编辑引入生产破坏性操作；
- 与仓库 destructive guard 规则兼容。

单行软状态不是完整事件历史：反复移除/恢复会覆盖最近一次操作字段。v1 不宣称提供完整审计；如果未来需要逐次历史，再新增 append-only 事件表，不扩大当前 interface。

### D3. 用差量 PATCH，不用整表覆盖

API 接收新增集合和移除集合：

```json
{
  "addSpuIds": ["1729...", "1730..."],
  "removeSpuIds": ["1601..."]
}
```

这样可以避免两个浏览器同时编辑时，一个完整集合 PUT 意外覆盖另一个人刚新增的 SPU。服务端在一个事务内校验并应用差量。

约束：

- `addSpuIds` 与 `removeSpuIds` 在 trim、去空、去重后不得重叠；
- 单个 ID 最长 128 字符；
- 每店关注总数不设业务上限；
- 单次 PATCH 最多处理 500 个 ID，这是请求/事务大小限制，不是店铺总数限制；
- 任一新增 SPU 不属于当前店铺时，整次请求 422，不能部分成功；
- 移除一个当前未关注的 SPU 视为幂等成功；
- 新 wire JSON/TypeScript 使用 camelCase；Python 和数据库内部继续 snake_case。

关注持久化不是 FastAPI handler 内的一段 SQL，而是独立深模块：

```python
list_focused_spus(
    session,
    *,
    shop_pk: int,
    query: FocusedSpuQuery,
) -> FocusedSpuPage

apply_patch(
    session,
    *,
    shop_pk: int,
    patch: FocusedSpuPatch,
    actor: str | None,
) -> FocusedSpuPatchResult
```

interface 的不变量包括原子性、幂等性、店铺归属、分页顺序、批次上限和 typed errors。SQL、upsert、软移除、count 与事务 ordering 全部隐藏在 implementation 中；FastAPI route 只做 wire adapter。

### D4. 盈利计算只调用现有 SPU ROI 深模块

重点关注页不把全部 ID 拼进 URL，而给现有端点增加一个可选的服务端范围：

```http
GET /v2/analytics/spu-roi
  ?shop_pk=<当前店铺内部主键>
  &scope=focused
  &w_start=<可选>
  &w_end=<可选>
  &fee_rate=<可选>
  &include_all=true
  &sort=<当前排序>
  &order=<当前方向>
  &limit=<分页>
  &offset=<分页>
```

`scope=focused` 必须与 `shop_pk` 同传；服务端通过 `reporting.focused_spus` 的 `EXISTS`/JOIN 约束 SPU 范围。它不受现有手工 `spu_ids` 查询参数“最多 100 个”的限制，也不受 URL 长度限制。

盈利模块的外部 interface 仍是“给定 ProfitScope + RowView，返回 overview”。只把范围选择从单一 `spu_ids` 扩为领域内的 selection：

```text
SpuSelection
├─ ActivitySelection        # 现有整店/活动范围
├─ ExactIdsSelection        # 现有手工 SPU 筛选，保留 100 个限制
└─ FocusedSelection         # 新增，DB 内按 shop_pk 解析，无总数上限
```

三种 selection 只决定基础 SPU 集合；金额公式、汇率、费率、退款/取消/全损口径、排序、分页、totals 和 evidence 全部继续走同一实现。排序在现有 metric + spend tie 之后显式追加 `spu_pk ASC` 最终 tie-breaker，避免相同指标行在 offset pagination 中漂移。由此自动继承：

- SPU ROI 当前 v10 盈利模块；
- `meta.currency` / `meta.fx`；
- 店铺实测费率与基线回退；
- 退款、国内取消、海外取消、全损、已结算/未结算口径；
- `totals` 在完整关注 scope 内聚合且不受分页影响；
- 订单、结算、售后、广告四类钻取端点。

#### D4.1 selection 的性能契约

当前盈利 implementation 会先计算完整 scope 的行与 totals，再进行公式排序和 response pagination。因此“页面每页 100 条”只限制响应大小，不会自动让计算成本与页大小成比例。

FocusedSelection 的 implementation 必须：

1. 先形成索引可用的 `selected_spus(spu_pk, shop_pk)` relation；
2. 将该 relation 下推到广告、订单、退款、全损、成本和 distinct-order 等事实查询，不能先扫全店/全库再在 Python 丢弃；广告事实还必须把店铺 `seller_id` 下推到 `plugin.ad_daily`，以命中 `ix_ad_daily_roi_seller_product_day` 的 `(seller_id, product_id, day)` 访问路径；
3. 在同一盈利快照中读取关注 membership 与业务事实，避免列表和 totals 跨快照；
4. 保持 totals 对完整 focused scope 精确计算；不能为了性能偷偷加总数上限或只统计当前页；
5. 在 `tts_erp_v3_test` 使用超过 100 个关注 SPU 的样本记录 `EXPLAIN (ANALYZE, BUFFERS)` 与计算耗时。

公式排序和完整 totals 仍可能要求对全部选中 SPU 执行 v10 公式。若未来规模导致不可接受，应在同一 profitability interface 背后优化 SQL/物化事实，而不是新增 focused 专用公式。

#### D4.2 `include_all` 兼容约束

现有 `include_all` wire 语义与内部 `include_inactive` 命名并不完全直观：当前 `include_all=true` 表示把没有活动的 ACTIVE 目录 SPU 纳入结果，并不等于展示所有下架状态。本功能只复用现有运行时语义，不顺手重命名或扩大状态范围；相关清理另开任务。

禁止在重点关注页前端重新计算金额、ROI、费率或汇总。

### D5. 抽出公共盈利页面深模块，不用模式分支堆进原文件

可以抽象，而且应当抽象。但抽象目标不是“通用表格框架”，而是一个领域明确的 **SPU 盈利页面深模块**：用很小的 interface 封装现有页面的大量共同实现。

#### D5.1 公共 seam 与 interface

新增公共文件 `static/js/spu-profitability-page.js`。由于项目无构建步骤，使用单一 namespaced global，只暴露一个挂载 interface：

```js
const page = window.ttsErp.spuProfitability.mount({
  root,
  profile: focusedSpusProfile,
});

page.reload();
page.destroy();
```

`PageProfile` 是页面差异的聚合 value，不是布尔开关集合：

```js
{
  id: "focused-spus",
  pagePath: "/v2/pages/focused-spus",
  defaults: { includeAll: true, limit: 100, sort: "roi_real", order: "asc" },
  selectionAdapter,
  view: {
    summaryIds: [/* allowlisted domain ids */],
    columnIds: [/* allowlisted domain ids */],
    drillTabIds: ["pnl", "orders", "settlements", "cases", "ads"],
  },
  extensions: [/* bounded slot extensions */],
}
```

标准页和重点关注页各有一个显式 profile。v1 可以选择相同的 summary/column 集合，但以后调整某一页的列、顺序或 drill tab 时只修改该 profile，不修改公共状态机，也不复制 renderer。

`summaryIds/columnIds/drillTabIds` 只能引用公共 module 内 allowlist 的领域定义；profile 不能传任意计算 callback。新增业务指标必须先进入后端盈利 module 和 wire contract，再由公共 renderer 登记，不能在 profile 中写前端公式。

不公开 renderer、formatter、state、fetch helper、pager 或 drilldown helper。`selectionAdapter` 是第一个真实变化 seam；目前恰好有两个 adapter：

```text
load(shopPk, signal)
  -> Promise<{queryable, count, label, emptyReason}>

analyticsParams(selectionState)
  -> 只包含 selection query 的普通对象

mountEditor({root, shopPk, role, selectionState, onCommitted})
  -> cleanup function

destroy()
```

两个 adapter 的语义：

```text
AdHocSelectionAdapter
  load                  从 URL 恢复临时 spu_ids；空 IDs 仍 queryable（整店活动范围）
  analyticsParams       返回 {} 或 {spu_ids: "..."}
  mountEditor           绑定现有 Tom Select / 查询 / 清空 / URL 同步

FocusedSelectionAdapter
  load                  分页读取完整关注集合并恢复 Tom Select；空集合仍 queryable=true
  analyticsParams       固定返回 {scope: "focused"}
  mountEditor           绑定页面内多选、批量粘贴和增删实时 PATCH mutation queue
```

adapter 只能决定“范围从哪里来、如何编辑、怎样翻译成 selection query”。公共 module 始终自行加入 `shop_pk`、日期、费率、include-all、排序和分页，并拒绝 adapter 覆盖这些公共键。adapter 不能访问汇总卡、主表、分页和钻取 DOM，也不参与盈利计算。

#### D5.2 页面差异的扩展矩阵

扩展性不靠不断增加 `if (profile.id === ...)`，而是把每类变化送到对应 seam：

| 将来出现的差异 | 放置位置 | 约束 |
| --- | --- | --- |
| SPU 范围来源、编辑和持久化 | `SelectionAdapter` | 只能贡献 selection query |
| 标题、默认值、登录回跳、空态文案 | `PageProfile` | 纯配置/文案，不接触盈利计算 |
| 汇总卡、列、顺序、可见 drill tab | `ViewProfile` 的 allowlisted IDs | 只能组合后端已有字段 |
| 关注提示条、页面专属操作区 | bounded extension slot | 只访问分配的 region 和只读 snapshot |
| 行级“取消关注”等操作 | row-action extension slot | 通过 selection command 执行，不修改公共 row 数据 |
| 新金额、ROI 或口径 | 后端盈利 module | 禁止前端 extension 计算 |
| 完全不同的请求生命周期 | 新页面 module | 不强迫现有 kernel 继续泛化 |

extension interface 保持很窄：

```text
mount({region, commands}) -> {onSnapshot(readonlySnapshot), destroy()}
```

- `region` 只能是 shell 预留的命名 slot，例如 `scope-banner`、`toolbar-end`、`row-actions`、`empty-state`；
- `commands` 只提供 `reload()`、`openSelectionEditor()` 等受控操作，不暴露可变 state；
- `readonlySnapshot` 只提供当前 shop、selection 摘要、loading/error 和已序列化 overview；
- 新 slot 只在第二个真实页面差异出现时加入，不预设任意插槽或直接交出 root DOM。

由此可以允许页面逐步出现差异，同时保证请求竞态、错误生命周期、totals、分页和钻取仍只有一份 implementation。

#### D5.3 必须抽到公共模块的内容

以下行为两页完全一致，集中到 `spu-profitability-page.js`，作为私有 implementation，不逐个暴露：

1. 页面状态机：当前店铺、日期、费率、include-all、排序、分页、loading version、AbortController。
2. overview 请求生命周期：参数组装、取消旧请求、401 跳登录、FX 错误、竞态保护、空态处理。
3. 汇总卡渲染：`totals` / `meta`；店铺费率状态卡为置顶横幅（两页一致，右上角 ✕ 可关闭；关闭后本次页面生命周期内查询/刷新不再自动弹出）。
4. 主表渲染：商品、广告、销售、取消、全损、净利润列及红绿/警告状态。
5. 排序和分页：列头状态、页码、每页数量、上一页/下一页。所有指标列都在 shell 上声明 `data-sort=<SpuProfitability 字段>`；公共 kernel 对 `th[data-sort]` 做事件委托和方向切换，不维护另一份前端排序白名单。因此后续新增指标列只要后端字段进入 `SortField`、表头声明 `data-sort`，标准页和重点关注页就自动获得同一排序交互。
6. 钻取交互：accordion、四类 evidence 懒加载、缓存 key、筛选变化清缓存。
7. 公共格式化：money、ratio、percent、整数、枚举翻译。
8. 店铺切换、日期校验、临时费率、刷新、登录身份和退出。
9. tooltip、主图 lightbox、无障碍与移动端行为。

公共状态机：

```text
BOOTING → AWAITING_SHOP → RESOLVING_SELECTION
        → EMPTY_SELECTION | LOADING → READY | ERROR
```

店铺切换的固定顺序：abort 旧 selection/overview/drilldown 请求 → 销毁旧 editor → 清分页与钻取缓存 → load 新 selection → selection 可查询时才 load overview。除了 `AbortController`，还使用单调 version，保证旧响应不能覆盖新店铺。

错误契约：

- selection 加载失败：显示可重试错误，绝不回退整店 scope；
- focused 空集合：仍请求 `scope=focused` 空 overview，以保留和普通 ROI 页一致的汇总大盘与页面结构；
- 401：按配置的 `pagePath` 回到正确页面登录，不再硬编码 `/spu-roi`；
- FX 失败：沿用公共整页错误；
- drilldown 失败：只在面板内显示，不覆盖主表；
- 实时 PATCH 失败：adapter 回滚对应多选 tag 并显示错误；成功后刷新关注计数、重置 analytics offset 并 reload。

这些内容被抽走后，修复一处表格、钻取、分页或错误处理，两页同时生效。

#### D5.4 保留在各自 adapter/profile 的内容

以下行为语义不同，不应硬塞进公共模块：

- 标准 ROI 页：临时选择 SPU、批量粘贴、查询/清空、`spu_ids` URL 同步。
- 重点关注页：关注列表 GET/PATCH、服务端分页搜索、编辑草稿、软移除、readonly 禁用编辑。
- 两页各自的标题、说明文字、空状态 CTA、侧边栏 active 状态和登录回跳路径。

#### D5.5 HTML 与 CSS 的抽象

服务端新增 `_render_spu_profitability_page(config)`，从同一 HTML shell 生成两页。config 是内部 typed value，只允许仓库内的固定页面：

```python
@dataclass(frozen=True)
class SpuProfitabilityPageConfig:
    slug: Literal["spu-roi", "focused-spus"]
    title: str
    page_path: str
    profile_id: Literal["standard-roi", "focused-spus"]
    entrypoint_js: str
```

公共 shell 包含汇总卡、日期/费率控件、主表、钻取模板、分页、错误区、命名 extension regions 和公共资产。页面 defaults、selection、view 与 extensions 由固定 profile 提供。renderer 不接受任意 HTML/JS URL，也不通过复制整段常量或脆弱的全页字符串替换派生页面。

CSS 分两层：

- `spu-roi.css`（后续可更名为 `spu-profitability.css`）：公共账页 token、汇总卡、工具栏、主表、钻取、分页、tooltip、lightbox、响应式。
- `focused-spus.css`：仅页面内实时多选、关注计数和保存状态。

页面专属规则统一挂在 `[data-page-profile="focused-spus"]` 下，不能通过高 specificity 覆盖公共表格/钻取基础规则。若某页确实需要不同表格表现，应先形成 view/profile 差异，再增加受控 modifier，避免 CSS 漂移成两份隐式实现。

#### D5.6 明确不做的抽象

- 不做通用 dashboard/table/form 框架；列名和盈利语义继续是领域代码。
- 不做动态/用户自定义 projection registry、saved-view adapter 或通用 persisted-scope provider；只支持仓库内 allowlisted view IDs 和两个固定 PageProfile。
- 不新增 repository port 或内存假实现；PostgreSQL 是 local-substitutable，直接用专用测试库验证真实 SQL。
- 不把每个 formatter 或 DOM helper 都变成公共导出；它们是公共模块的私有 implementation。
- 公共 kernel 不出现散落的 `if (profile.id === "focused-spus")`；差异必须进入 PageProfile、selection adapter、view descriptor 或明确的 extension slot。
- 不复制 `spu-roi.js` 后再分别维护。

建议文件结构：

```text
static/js/spu-profitability-page.js   # 公共深模块
static/js/spu-roi.js                  # 小型 AdHoc adapter + bootstrap
static/js/focused-spus.js             # 小型 Focused adapter + bootstrap
static/css/spu-roi.css                # 公共视觉
static/css/focused-spus.css           # focused-only 增量
```

这样删除公共模块时，汇总、表格、分页、钻取、错误处理等复杂度会重新散落到两个页面；说明这个模块确实提供了深度和复用价值，而不是简单转发。

### D6. 权限

| 操作 | 角色 |
| --- | --- |
| 打开页面 | readonly |
| 查看某店关注集合 | readonly |
| 查看关注 SPU 的 ROI 数据 | readonly |
| 新增/移除关注 | readwrite |

浏览器 cookie 会话执行 PATCH 时继续发送：

```http
X-Requested-With: tts-erp
```

readonly 用户可以查看页面和关注数据；编辑按钮禁用并提示需要 readwrite。服务端仍做角色校验，不能只依赖按钮状态。

`/v2/reporting/` 当前整体会命中 readonly prefix，因此 access policy 必须在该规则之前增加“`PATCH` + focused-spus path → readwrite”的 method-specific 判定；不能把整个 focused prefix 提升到 readwrite，否则 GET 也会错误要求 readwrite。

### D7. 依赖分类与 adapter 策略

| 依赖 | 分类 | 设计 |
| --- | --- | --- |
| 浏览器 DOM、Tom Select、普通 JavaScript | in-process | 公共 module 与 selection adapter 直接使用；不为每个 DOM 调用造 wrapper |
| `spu_profitability` | in-process module | 只通过 `read_overview()` / `explain_spu()` interface 调用，不 import 私有 implementation |
| FastAPI route | owned local process | 作为 wire adapter 做参数、鉴权、序列化和 error mapping，不承载领域 implementation |
| PostgreSQL | local-substitutable | `Session` 注入 module；在 `tts_erp_v3_test` 验证真实 SQL，不造行为不同的内存 repository |
| TikTok/Miaoshou/MinIO | 本 slice 无依赖 | 不新增 mock、重试或远程 port |

这里只有两个真实 adapter：AdHocSelectionAdapter 与 FocusedSelectionAdapter。HTTP 和 PostgreSQL 的层次分离是 transport/local persistence locality，不意味着要建立假想 remote port。

## 6. API 契约

### 6.1 获取某店关注集合

```http
GET /v2/reporting/focused-spus/{shop_pk}?q=<可选>&limit=50&offset=0
```

关注管理列表服务端分页；`q` 搜索 `spu_id/title`。响应：

```json
{
  "shopPk": 314,
  "items": [
    {
      "spuPk": 9001,
      "spuId": "1729000000000000001",
      "title": "商品标题",
      "status": "ACTIVATE",
      "createdAt": "2026-09-29T08:00:00Z",
      "updatedAt": "2026-09-29T08:00:00Z"
    }
  ],
  "total": 126,
  "matchedTotal": 9,
  "limit": 50,
  "offset": 0
}
```

`total` 是该店全部 active 关注数；`matchedTotal` 是应用 `q` 后的数量；`items` 是当前搜索页。固定排序为 `updated_at DESC, spu_id ASC`。

错误：

- 401：未登录；
- 403：无 readonly 权限；
- 404：`shop_pk` 不存在。

### 6.2 编辑某店关注集合

```http
PATCH /v2/reporting/focused-spus/{shop_pk}
Content-Type: application/json
X-Requested-With: tts-erp
```

请求：

```json
{
  "addSpuIds": ["1729000000000000002"],
  "removeSpuIds": ["1729000000000000001"]
}
```

成功返回有界 mutation receipt，不返回无限增长的完整集合：

```json
{
  "shopPk": 314,
  "total": 126,
  "addedSpuIds": ["1729000000000000002"],
  "removedSpuIds": ["1729000000000000001"]
}
```

前端随后重新读取当前管理页与关注计数。

错误：

- 401：未登录；
- 403：角色低于 readwrite；
- 404：店铺不存在；
- 422 `SPU_NOT_FOUND_IN_SHOP`：新增 SPU 不属于该店；
- 422 `FOCUSED_SPU_PATCH_CONFLICT`：同一 ID 同时出现在新增和移除列表；
- 422 `FOCUSED_SPU_PATCH_TOO_LARGE`：单次 PATCH 超过 500 个 ID；
- 422：字段格式非法。

## 7. 数据流

```text
选择店铺
   │
   ├─ GET /v2/reporting/focused-spus/{shop_pk}
   │       └─ reporting.focused_spus JOIN commerce.products_spu
   │
   └─ 无论集合是否为空
           └─ GET /v2/analytics/spu-roi?shop_pk=...&scope=focused
                  ├─ 空集合返回 totals=0/items=[]，完整大盘 shell 保留
                  ├─ FocusedSelection 在 DB 内解析完整关注范围
                  ├─ 现有 spu_profitability 计算模块
                  ├─ totals / meta / items
                  └─ 公共 spu-profitability-page.js 渲染

页面内多选实时增删
   │
   └─ mutation queue 串行 PATCH /v2/reporting/focused-spus/{shop_pk}
          ├─ 校验店铺与 SPU 归属
          ├─ 同事务软移除 + 新增/恢复
          ├─ 返回有界 mutation receipt，前端更新计数
          └─ 成功刷新 ROI；失败回滚对应 tag
```

## 8. 代码改动面

| 文件 | 计划改动 |
| --- | --- |
| `alembic/versions/0043_focused_spus.py` | 新建关注表、索引、更新时间 trigger |
| `schema_tts_erp.sql` | 迁移验证后重新生成 schema 快照 |
| `tts_erp_v2/reporting/focused_spus.py` | 关注集合深模块：`list_focused_spus()` / `apply_patch()`，隐藏 SQL、校验和事务 |
| `tts_erp_v2/api/v2/focused_spus.py` | GET + PATCH wire adapter；camelCase 序列化和 domain error 映射 |
| `tts_erp_v2/app.py` | 注册新 router |
| `tts_erp_v2/access/_policy.py` | GET readonly、PATCH readwrite |
| `tts_erp_v2/analytics/spu_profitability/_types.py` | 将范围建模为 Activity / ExactIds / Focused selection |
| `tts_erp_v2/analytics/spu_profitability/_selection.py` | 私有 selection relation；把 selected SPU 下推到事实查询 |
| `tts_erp_v2/analytics/spu_profitability/_implementation.py` | 消费 selected relation，复用全部公式、totals、排序和分页 |
| `tts_erp_v2/analytics/spu_roi.py` | 解析 `scope=focused` 并保持现有响应契约 |
| `tts_erp_v2/api/v2/pages.py` | 新页面路由、侧边栏入口、`_render_spu_profitability_page(config)` 共享 shell |
| `tts_erp_v2/static/js/spu-profitability-page.js` | 新公共深模块：状态、overview、汇总、表格、分页、钻取和公共交互 |
| `tts_erp_v2/static/js/spu-roi.js` | 收敛为 AdHoc selection adapter + bootstrap |
| `tts_erp_v2/static/js/focused-spus.js` | Focused selection adapter + bootstrap |
| `tts_erp_v2/static/css/spu-roi.css` | 保留公共盈利账页视觉 |
| `tts_erp_v2/static/css/focused-spus.css` | 仅页面内实时多选、关注计数和保存状态 |
| `tests/reporting/test_focused_spus.py` | 通过关注集合 module interface 测分页、搜索、原子 patch、软移除和恢复 |
| `tests/api/test_spu_roi_api.py` | 通过盈利 module interface 验证 Focused/Exact 等价与空集合；同时覆盖新页面 shell 和现有端点兼容回归 |
| `tests/api/test_focused_spus.py` | wire、权限、camelCase、错误映射、CSRF 与 profile/kernel 契约 |
| 浏览器 DOM harness（路径按现有测试基础设施确定） | 通过 mount interface 测竞态、错误、空态、分页与草稿保留 |
| `tech-doc/external-api.md` | 页面和 API 契约登记 |

注意：`pages.py`、`spu-roi.js`、`spu-roi.css`、`test_spu_roi_api.py` 当前由 `fix/spu-roi-pagination` lane 占用。该 lane 合并释放前不修改这些共享文件；实现时先 rebase 到分页改造后的 master。

## 9. 测试方案

### 9.1 关注集合 module interface

测试只调用 `list_focused_spus()` / `apply_patch()`，不直接断言私有 SQL：

1. A 店关注集合不会出现在 B 店；相同 `spu_id` 可在两店分别关注。
2. 新增、移除、重复操作、重新恢复均幂等。
3. 移除后行仍存在且 `active=false`；只承诺最近状态元数据，不假装完整事件历史。
4. 一批新增中任一 ID 不存在或属于其他店，整次事务零写入。
5. trim/去重后的 add/remove 重叠整批拒绝。
6. 单店超过 100 个关注仍可搜索和稳定分页。
7. disjoint 的并发 delta 可组合；同一 membership 的相反操作允许 last-committer-wins。
8. PATCH result 有界，不返回完整集合。

### 9.2 盈利 module interface

测试只调用 `read_overview()` / `explain_spu()`：

1. 相同 SPU 集合下，`ExactIdsSelection` 与 `FocusedSelection` 的 items、totals、basis 和 evidence 完全一致。
2. FocusedSelection 超过 100 个仍成功；ExactIdsSelection 继续保持现有 100 个限制。
3. 空 focused set 返回空 overview，绝不回退整店 activity scope。
4. focused membership 不可跨店。
5. search/pagination 改变 items/total，但不改变 profitability totals。
6. 相同排序值以 `spu_pk ASC` 最终稳定打破并列，offset pagination 不漂移。
7. `include_all` 保持现有运行时语义，不因内部重构改变。
8. 大范围 fixture 验证 selected relation 下推事实查询，并记录执行计划。

### 9.3 HTTP wire adapter

1. readonly 可 GET，anonymous 401；readwrite/admin 可 PATCH，readonly PATCH 403。
2. cookie PATCH 缺 `X-Requested-With: tts-erp` 时拒绝。
3. 新 focused management JSON 全部 camelCase。
4. `scope=focused` 必须带 `shop_pk`，并与 `spu_ids` 互斥；未知 scope 422。
5. domain error 映射到稳定 code；现有无 scope / exact-ID wire 契约不变。
6. 空 focused analytics 返回空结果，不泄漏整店数据。

### 9.4 页面与浏览器 mount interface

1. 两页使用同一公共 shell、公共 JS module 和公共 CSS；只在 title、selection slot、entry script、focused CSS 与 defaults 上不同。
2. Focused adapter 分页恢复完整关注多选值；空集合仍发送安全的 focused analytics 请求并渲染完整大盘。
3. Focused analytics 只发送 `shop_pk + scope=focused`，不拼完整 ID 列表。
4. 店铺切换 abort 旧 selection/overview/drilldown；旧响应不能渲染。
5. 401 使用配置的 `pagePath`；FX、网络、畸形 payload 和重试生命周期两页一致。
6. PATCH 发送 CSRF header；失败保留草稿，成功刷新管理页/计数/ROI。
7. 共享 conformance suite 对两个 PageProfile 各跑一次：竞态、401、FX、分页、排序、钻取和 retry 必须一致。
8. profile-specific suite 验证不同标题、默认值、列/汇总排列、空态、扩展 slot 和专属操作，不要求两页 DOM 完全相同。
9. adapter/profile 试图覆盖 `shop_pk`、日期、费率、排序或分页等公共 query key 时必须拒绝。
10. 相同 overview fixture 在相同 ViewProfile 下产生相同汇总、表格、分页和钻取行为；不同 ViewProfile 只改变 allowlisted 组合。
11. 将现有依赖 source grep 的断言逐步替换为对 mount interface 的可执行 DOM 测试；不测试私有 formatter 或 renderer。

### 9.5 验证命令

实现阶段只在 `tts_erp_v3_test` 验证迁移。测试按仓库规定执行：

```bash
flock -n /tmp/tts-erp-test.lock \
  bash scripts/test.sh fast \
  tests/reporting/test_focused_spus.py \
  tests/api/test_focused_spus.py \
  tests/api/test_spu_roi_api.py

flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast
```

禁止在生产库运行迁移或测试；生产 migration 与服务重启由人工执行。

## 10. 上线与回滚

### 上线顺序

测试库 migration、focused-spus 窄测试与 fast suite 已完成。生产由人工执行受保护脚本：

```bash
git pull --ff-only origin master
bash scripts/oneoff_deploy_focused_spus.sh --check
ALLOW_PROD_DESTRUCTIVE=1 \
  bash scripts/oneoff_deploy_focused_spus.sh --confirm
```

脚本固定执行：确认数据库当前 revision 只能是 0042/0043 → shared destructive guard → upgrade 0043 → schema catalog 验证 → 仅重启 API 服务 → healthz/OpenAPI/静态资产验证。它不会执行 `git pull`，也不会重启未受本功能影响的 sync worker。

执行后人工用 readwrite 会话选择店铺、添加 1 个 SPU、刷新确认持久化；再用同一批 SPU 对比 `scope=focused` 与标准页精确 scope 在相同日期范围下的金额和 totals。

### 回滚

- 页面/API 可先回滚代码；关注表可保留，不影响现有 SPU ROI。
- 如需回退 migration，必须先确认不再依赖关注数据；`downgrade` 会删除表，属于人工生产操作，agent 不执行。

## 11. 验收标准

1. 选择店铺后只显示该店的关注 SPU。
2. 新增、移除关注后刷新页面仍保持结果。
3. 同一 SPU ID 不会跨店串数据。
4. 页面汇总、行数据和钻取结果与现有 SPU ROI 在相同 scope 下完全一致。
5. 空关注集合绝不展示整店数据。
6. readonly 可查看但不可编辑；readwrite 可编辑。
7. 标准 SPU ROI 页面无行为回归。
8. 超过 100 个关注 SPU 时管理分页、完整 totals 和 ROI 页面正常，且不把 ID 列表放进 URL。
9. selected relation 已下推关键事实查询，并留存测试库执行计划/耗时证据。
10. 窄测试和 fast suite 无新增稳定失败。

## 12. 主要风险与控制

1. **公共 JS 抽取回归**：现有约 1800 行文件混合了选择、请求、渲染和钻取；必须先用当前标准页 fixture 固定 mount interface 的可观察行为，再替换旧实现，不能复制后渐进漂移。
2. **无限关注不等于无限计算资源**：完整 totals 和公式排序仍需处理整个 focused scope；通过 selected relation 下推、执行计划和耗时观测控制，不能用隐式业务上限掩盖。
3. **管理列表 offset 漂移**：并发增删可能让后续页位移；v1 接受该行为，使用确定性排序。真实需要稳定游标时再改 cursor，不提前扩大 interface。
4. **最近状态不等于完整审计**：软状态表只记录当前状态和最近操作；完整审计如有需求另建事件表。
5. **actor 标识**：优先记录认证 key hash/稳定主体标识，绝不接收浏览器自报 actor；若当前 grant 无稳定主体则允许空值并记录为待补能力。
6. **`include_all` 命名误导**：本功能只保持兼容，不顺手修语义，避免把架构重构和业务口径改动混在一起。
7. **PageProfile 变成配置垃圾场**：profile 只允许 identity/defaults/selection/view/extensions；任何影响请求竞态、错误生命周期、totals 或钻取缓存的属性都拒绝进入 profile。新 variation 只有在第二个真实页面差异出现时才建立 seam。

## 13. 开发前待确认

以下为本方案的推荐默认值，等待确认后再开始开发：

1. **每店关注总数不设业务上限**：管理列表服务端分页，ROI 使用 `scope=focused` 在数据库内解析范围；现有 `spu_ids` 的 100 个限制只保留给临时筛选。
2. **采用最小 interface + 端到端 seam**：具体 Activity/Exact/Focused selection；一个公共浏览器 mount；两个固定 PageProfile；allowlisted ViewProfile；独立关注集合 module；不预建动态 plugin/provider/saved-view 框架。
3. **PATCH 返回有界 mutation receipt**：不返回无限增长的完整关注集合，成功后重读当前分页。
4. **readonly 可看、readwrite 可编辑**：与普通运营写操作一致。
5. **软移除**：页面表现为删除，数据库保留 inactive 当前状态和最近操作元数据，但不宣称完整事件历史。
6. **重点关注页默认勾选“含无活动”**：沿用现有语义，让无活动的 ACTIVE SPU 可见，不扩大为全部下架状态。
7. **不做关注备注/分组/告警**：v1 只交付店铺级关注集合和 ROI 展示。
