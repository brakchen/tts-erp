# 重点关注 SPU 页面技术方案

> 状态：Draft，等待产品确认后开发
> 日期：2026-09-29
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
- 侧边栏：运营分组下新增「重点关注」
- 页面标题：`重点关注 SPU`
- 页面整体继续使用现有 SPU ROI 的 warm-paper 账页视觉、Bootstrap 布局和相同数据表，不创建另一套视觉语言。

### 4.2 页面结构

```text
┌─────────────────────────────────────────────────────────────┐
│ 重点关注 SPU                       店铺 [Bridge nook ▼]     │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页的汇总卡：总单量 / 广告 / 销售 / 退款 / 利润… │
├─────────────────────────────────────────────────────────────┤
│ 已关注 12 个 SPU                       [编辑关注 SPU]        │
│ 日期范围 / 临时费率 / 含无活动 / 刷新 / 排序状态            │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页主表与行内钻取面板                             │
├─────────────────────────────────────────────────────────────┤
│ 同 SPU ROI 页分页                                           │
└─────────────────────────────────────────────────────────────┘

点击“编辑关注 SPU”：
┌─────────────────────────────────────────────────────────────┐
│ 编辑 Bridge nook 的重点关注 SPU                             │
│ [Tom Select：搜索、输入精确 ID、批量粘贴、逐项移除]         │
│ 已选择 12 / 100                         [取消] [保存修改]    │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 编辑行为

1. 必须先选择店铺；未选店铺时禁用编辑按钮。
2. 编辑器复用现有 `channel-product-options` 搜索能力：
   - 输入 SPU ID 或标题搜索；
   - 支持中英文逗号批量粘贴；
   - 精确校验 SPU 必须属于当前店铺；
   - 最多关注 100 个 SPU。
3. 打开编辑器时，以数据库中的当前关注集合为草稿。
4. 删除一个 tag 只修改草稿；点击「取消」不写库。
5. 点击「保存修改」时，前端计算 `add_spu_ids` / `remove_spu_ids` 两个差量，一次提交。
6. 保存成功后关闭编辑器、刷新关注集合，再请求 SPU ROI 数据。
7. 保存失败时保留草稿，明确显示失败原因，不静默关闭。

### 4.4 空状态与过滤状态

- 当前店铺没有关注 SPU：不请求无范围的 ROI API，避免误展示整店数据；页面显示「尚未关注 SPU」和「添加关注 SPU」按钮。
- 已关注但当前日期窗口没有活动：保留现有「含无活动」控制；重点关注页默认勾选，尽量让已关注的 ACTIVE SPU 可见。
- 已关注数量与当前表格命中数量分开展示，例如：`已关注 12 个 · 当前窗口展示 9 个`。
- 非 ACTIVE 且当前窗口无活动的 SPU 可能不进入 ROI 结果；编辑器仍显示其状态，页面给出未展示数量提示。

## 5. 核心设计决策

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

另建 `(shop_pk, active)` 索引，并使用现有 `public.fn_touch_updated_at()` 维护 `updated_at`。

### D2. 移除关注采用软删除

编辑器中的“删除”在数据库中表现为 `active=false + removed_at`，不执行物理 `DELETE`：

- 保留操作历史；
- 重新关注时可原行恢复；
- 避免为日常编辑引入生产破坏性操作；
- 与仓库 destructive guard 规则兼容。

v1 不提供历史查询 UI，但保留后续审计能力。

### D3. 用差量 PATCH，不用整表覆盖

API 接收新增集合和移除集合：

```json
{
  "add_spu_ids": ["1729...", "1730..."],
  "remove_spu_ids": ["1601..."]
}
```

这样可以避免两个浏览器同时编辑时，一个完整集合 PUT 意外覆盖另一个人刚新增的 SPU。服务端在一个事务内校验并应用差量。

约束：

- `add_spu_ids` 与 `remove_spu_ids` 不得重叠；
- 去空、trim、去重；
- 单个 ID 最长 128 字符；
- 操作后的有效关注总数不得超过 100；
- 任一新增 SPU 不属于当前店铺时，整次请求 422，不能部分成功；
- 移除一个当前未关注的 SPU 视为幂等成功。

### D4. 盈利计算只调用现有 SPU ROI API

关注集合读取成功后，页面请求：

```http
GET /v2/analytics/spu-roi
  ?shop_pk=<当前店铺内部主键>
  &spu_ids=<关注 SPU ID，逗号分隔>
  &w_start=<可选>
  &w_end=<可选>
  &fee_rate=<可选>
  &include_all=true
  &sort=<当前排序>
  &order=<当前方向>
  &limit=<分页>
  &offset=<分页>
```

由此自动继承：

- SPU ROI 当前 v10 盈利模块；
- `meta.currency` / `meta.fx`；
- 店铺实测费率与基线回退；
- 退款、国内取消、海外取消、全损、已结算/未结算口径；
- `totals` 在完整关注 scope 内聚合且不受分页影响；
- 订单、结算、售后、广告四类钻取端点。

禁止在重点关注页前端重新计算金额、ROI、费率或汇总。

### D5. 共享一个页面实现，不复制 ROI 页面

不复制 `spu-roi.js` 或 `spu-roi.css`。拟采用页面模式配置：

```html
<body data-spu-page-mode="focused">
```

`spu-roi.js` 读取模式：

- `standard`：保持现有 `/v2/pages/spu-roi` 行为；
- `focused`：先加载关注集合，以关注集合固定 `spu_ids` scope，再复用相同渲染、排序、分页、钻取和错误处理。

HTML 骨架由同一 `_SPU_ROI_PAGE_HTML` 派生，仅替换标题、页面说明和关注编辑区。这样后续表格列或口径展示变化只维护一处。

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

## 6. API 契约

### 6.1 获取某店关注集合

```http
GET /v2/reporting/focused-spus/{shop_pk}
```

响应：

```json
{
  "shop_pk": 314,
  "items": [
    {
      "spu_pk": 9001,
      "spu_id": "1729000000000000001",
      "title": "商品标题",
      "status": "ACTIVATE",
      "created_at": "2026-09-29T08:00:00Z",
      "updated_at": "2026-09-29T08:00:00Z"
    }
  ],
  "spu_ids": ["1729000000000000001"],
  "total": 1,
  "max_items": 100
}
```

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
  "add_spu_ids": ["1729000000000000002"],
  "remove_spu_ids": ["1729000000000000001"]
}
```

成功直接返回更新后的完整集合，便于前端以服务端结果覆盖本地草稿。

错误：

- 401：未登录；
- 403：角色低于 readwrite；
- 404：店铺不存在；
- 422 `SPU_NOT_FOUND_IN_SHOP`：新增 SPU 不属于该店；
- 422 `FOCUSED_SPU_LIMIT_EXCEEDED`：操作后超过 100 个；
- 422 `FOCUSED_SPU_PATCH_CONFLICT`：同一 ID 同时出现在新增和移除列表。

## 7. 数据流

```text
选择店铺
   │
   ├─ GET /v2/reporting/focused-spus/{shop_pk}
   │       └─ reporting.focused_spus JOIN commerce.products_spu
   │
   ├─ 空集合 ──> 渲染空状态，不调用 ROI API
   │
   └─ 非空集合
           └─ GET /v2/analytics/spu-roi?shop_pk=...&spu_ids=...
                  ├─ 现有 spu_profitability 计算模块
                  ├─ totals / meta / items
                  └─ 同一 spu-roi.js 渲染

编辑并保存
   │
   └─ PATCH /v2/reporting/focused-spus/{shop_pk}
          ├─ 校验店铺与 SPU 归属
          ├─ 同事务软移除 + 新增/恢复
          └─ 返回最新集合，再刷新 ROI
```

## 8. 代码改动面

| 文件 | 计划改动 |
| --- | --- |
| `alembic/versions/0043_focused_spus.py` | 新建关注表、索引、更新时间 trigger |
| `schema_tts_erp.sql` | 迁移验证后重新生成 schema 快照 |
| `tts_erp_v2/api/v2/focused_spus.py` | GET + PATCH API；校验、事务、软移除 |
| `tts_erp_v2/app.py` | 注册新 router |
| `tts_erp_v2/access/_policy.py` | GET readonly、PATCH readwrite |
| `tts_erp_v2/api/v2/pages.py` | 新页面路由、侧边栏入口、共享 ROI shell 模式 |
| `tts_erp_v2/static/js/spu-roi.js` | 增加 focused 模式；复用所有展示与钻取逻辑 |
| `tts_erp_v2/static/css/spu-roi.css` | 仅补编辑弹窗/空状态所需少量样式 |
| `tests/api/test_focused_spus.py` | API、权限、店铺隔离、原子性、软移除测试 |
| `tests/api/test_spu_roi_api.py` | 新页面 shell 与共享前端模式回归测试 |
| `tech-doc/external-api.md` | 页面和 API 契约登记 |

注意：`pages.py`、`spu-roi.js`、`spu-roi.css`、`test_spu_roi_api.py` 当前由 `fix/spu-roi-pagination` lane 占用。该 lane 合并释放前不修改这些共享文件；实现时先 rebase 到分页改造后的 master。

## 9. 测试方案

### 9.1 API 集成测试

1. readonly 可 GET，anonymous 401。
2. readwrite/admin 可 PATCH，readonly PATCH 403。
3. A 店关注集合不会出现在 B 店。
4. 相同 `spu_id` 可在两个店分别关注。
5. 新增、移除、重新新增均幂等。
6. 移除后数据库行仍存在且 `active=false`。
7. 新增不存在或属于其他店的 SPU 返回 422，整次事务不产生部分写入。
8. 新增/移除重叠返回 422。
9. 操作后超过 100 个返回 422。
10. 空关注集合 GET 返回 `items=[]`，页面不得退化为整店 ROI。

### 9.2 页面契约测试

1. `/v2/pages/focused-spus` 使用同一 ROI CSS/JS 资产。
2. 页面有 `data-spu-page-mode="focused"`、店铺选择器、编辑按钮、汇总卡、主表、分页和钻取模板。
3. focused 模式先拉关注集合，再拉 ROI；空集合不拉 ROI。
4. ROI 请求始终同时带 `shop_pk` 与精确 `spu_ids`。
5. 保存时发送差量 PATCH 和 CSRF header。
6. 401 跳登录；403 显示只读提示；422 保留编辑草稿。
7. 标准 SPU ROI 页面原有交互不变。

### 9.3 验证命令

实现阶段只在 `tts_erp_v3_test` 验证迁移。测试按仓库规定执行：

```bash
flock -n /tmp/tts-erp-test.lock \
  bash scripts/test.sh fast tests/api/test_focused_spus.py tests/api/test_spu_roi_api.py

flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast
```

禁止在生产库运行迁移或测试；生产 migration 与服务重启由人工执行。

## 10. 上线与回滚

### 上线顺序

1. 在测试库执行 migration 0043。
2. 跑 focused-spus 窄测试与 fast suite。
3. 人工在生产执行 migration 0043。
4. 部署并重启 API 服务。
5. 用 readwrite 会话选择店铺、添加 1 个 SPU、刷新确认持久化。
6. 对比同一 `shop_pk + spu_ids + 日期范围` 下重点关注页与 SPU ROI 页的 API 响应，确认金额和 totals 完全一致。

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
8. 窄测试和 fast suite 无新增稳定失败。

## 12. 开发前待确认

以下为本方案的推荐默认值，等待确认后再开始开发：

1. **每店最多关注 100 个 SPU**：与现有 `spu_ids` API 上限一致，避免新增第二套范围协议。
2. **readonly 可看、readwrite 可编辑**：与普通运营写操作一致。
3. **软移除**：页面表现为删除，数据库保留 inactive 历史。
4. **重点关注页默认勾选“含无活动”**：优先让被关注但当前窗口无活动的 ACTIVE SPU 仍可见。
5. **不做关注备注/分组/告警**：v1 只交付店铺级关注集合和 ROI 展示。
