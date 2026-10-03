# SPU ROI 钻取面板枚举翻译 — 技术方案

> 日期：2026-09-28
> 状态：Draft

---

## 1. 问题现状

SPU ROI 页面（`/v2/pages/spu-roi`）点击 SPU 行展开的钻取面板中，有 5 个子 tab：

| Tab | 表头现状 | 数据值现状 |
| ----- | --------- | ----------- |
| 利润构成 | 已中文 | 无枚举值（金额/比率） |
| 订单·物流 | `is_settled`、`38301` 为英文 | `status` 值全英文（`AWAITING_SHIPMENT`/`IN_TRANSIT`/`DELIVERED` 等） |
| 结算 | `SETTLEMENT`、`statement` 为英文 | `component.code` 值英文 |
| 售后 | `case` 为英文 | `type`（`RETURN_AND_REFUND`/`REFUND_ONLY`/`CANCELLATION`）+ `status` 均英文 |
| 广告 | `campaign_id`、`spend`、`orders` 为英文 | 无枚举值 |

**目标**：表头 + 数据枚举值全部中文化，且提供一个可配置页面让用户自行维护映射。

---

## 2. 需翻译的完整清单

### 2.1 表头（column headers）

| Tab | 当前表头 | → 翻译 |
| ----- | --------- | -------- |
| 订单·物流 | `is_settled` | 已结算 |
| 订单·物流 | `38301` | 到海外 |
| 结算 | `SETTLEMENT` | 结算净额 |
| 结算 | `statement` | 结算时间 |
| 售后 | `case` | Case ID |
| 广告 | `campaign_id` | 广告计划 |
| 广告 | `spend` | 消耗 |
| 广告 | `orders` | 广告订单 |

### 2.2 枚举值（data cell values）

**order.status**（订单状态）：

| 原始值 | 中文 | 说明 |
| -------- | ------ | ------ |
| `AWAITING_SHIPMENT` | 待发货 | |
| `PARTIAL_SHIPPING` | 部分发货 | |
| `AWAITING_COLLECTION` | 待揽收 | |
| `IN_TRANSIT` | 运输中 | |
| `DELIVERED` | 已送达 | |
| `COMPLETED` | 已完成 | |
| `CANCELLED` | 已取消 | |
| `UNPAID` | 未付款 | |
| `ON_HOLD` | 挂起 | |

**case.type**（售后类型）：

| 原始值 | 中文 |
| -------- | ------ |
| `RETURN_AND_REFUND` | 退货退款 |
| `REFUND_ONLY` | 仅退款 |
| `CANCELLATION` | 取消 |
| `CANCEL` | 取消 |

**case.status**（售后状态）：

| 原始值 | 中文 |
| -------- | ------ |
| `CANCELLATION_REQUEST_COMPLETE` | 取消完成 |
| `RETURN_OR_REFUND_REQUEST_COMPLETE` | 退货退款完成 |
| `REFUND_REQUEST_COMPLETE` | 仅退款完成 |

**settlement component.code**（结算组件）：

| 原始值 | 中文 |
| -------- | ------ |
| `SETTLEMENT` | 结算净额 |
| `COMMISSION` | 平台佣金 |
| `SHIPPING_FEE` | 运费 |
| `AFFILIATE_COMMISSION` | 联盟佣金 |

**cost_source**（成本来源，用于利润构成 ⚠ 提示）：

| 原始值 | 中文 |
| -------- | ------ |
| `MANUAL` | 人工标注 |
| `SOURCE_PRICE` | 货源价 |
| `DEFAULT_K1` | 默认40元/件 |

**shipment.status**（物流状态，暂不展示在订单 tab，但 `tracking[].action_code` 相关）：

| 原始值 | 中文 |
| -------- | ------ |
| `PENDING` | 待处理 |
| `SHIPPED` | 已发货 |
| `IN_TRANSIT` | 运输中 |
| `DELIVERED` | 已送达 |

---

## 3. 技术方案

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────┐
│  前端 spu-roi.js                                     │
│  ┌───────────────────────────────────────────────┐   │
│  │  page load → GET /v2/config/enum-map          │   │
│  │  → 一次拿全量，缓存到 state.enumMap            │   │
│  │  renderDrillTabBody() 用 enumMap 翻译         │   │
│  └───────────────────────────────────────────────┘   │
└──────────────────────┬───────────────────────────────┘
                       │ GET (readonly)
┌──────────────────────▼───────────────────────────────┐
│  后端 API                                            │
│  GET    /v2/config/enum-map  → 读 DB，一次返回全量   │
│  PUT    /v2/config/enum-map  → 写单条（admin）       │
│  DELETE /v2/config/enum-map/{id} → 删单条（admin）   │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│  DB: config.enum_map (新建 config schema)             │
│  (enum_type, enum_value) → label_zh                  │
└──────────────────────────────────────────────────────┘
```

**关键改动**：用独立 `config` schema（不是 `analytics`），职责清晰——`config` 放所有可配置的映射/元数据，`analytics` 只放计算结果。

### 3.2 数据库层：新建 `config` schema + `config.enum_map` 表

**`base.py` 注册新 schema**：

```python
# tts_erp_v2/db/base.py — SCHEMAS tuple 新增 "config"
SCHEMAS: tuple[str, ...] = (
    "integration",
    "commerce",
    "procurement",
    "fulfillment",
    "after_sales",
    "finance",
    "linkage",
    "reporting",
    "security",
    "plugin",
    "config",        # ← 新增
)
```

**Alembic migration**：

```sql
CREATE SCHEMA IF NOT EXISTS config;

CREATE TABLE config.enum_map (
    id          SERIAL PRIMARY KEY,
    enum_type   VARCHAR(64)  NOT NULL,  -- 分类：order_status / case_type / case_status / ...
    enum_value  VARCHAR(128) NOT NULL,  -- 原始英文值
    label_zh    VARCHAR(256) NOT NULL,  -- 中文翻译
    sort_order  INT          NOT NULL DEFAULT 0,  -- 显示排序
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_enum_map_type_value UNIQUE (enum_type, enum_value)
);

COMMENT ON TABLE config.enum_map IS 'SPU ROI 钻取面板枚举值中文化映射表';
COMMENT ON COLUMN config.enum_map.enum_type IS '枚举分类：order_status/case_type/case_status/settle_component/cost_source/shipment_status/column_header';
COMMENT ON COLUMN config.enum_map.enum_value IS '原始英文枚举值';
COMMENT ON COLUMN config.enum_map.label_zh IS '中文显示标签';

CREATE INDEX ix_enum_map_type ON config.enum_map (enum_type);
```

```

**enum_type 枚举分类清单**：

| enum_type | 用途 | 映射字段 |
| ----------- | ------ | --------- |
| `order_status` | 订单状态 | orders tab → status |
| `case_type` | 售后类型 | cases tab → type |
| `case_status` | 售后状态 | cases tab → status |
| `settle_component` | 结算组件 | settlements tab → components[].code |
| `cost_source` | 成本来源 | 利润构成 → ⚠ 提示 |
| `shipment_status` | 物流状态 | orders tab → shipment.status |
| `column_header` | 表头翻译 | 各 tab 表头 |

**初始数据 seed**：上面 §2 清单全部 INSERT，保证上线即用。

### 3.3 后端 API 层

新增独立路由文件 `tts_erp_v2/api/v2/config.py`，prefix `/v2/config`。

```python
# GET /v2/config/enum-map — readonly，一次返回全量
# 返回: { "order_status": { "AWAITING_SHIPMENT": "待发货", ... }, ... }
#
# PUT /v2/config/enum-map — admin 可写，body:
#   { "enum_type": "order_status", "enum_value": "NEW_STATUS", "label_zh": "新状态" }
#
# DELETE /v2/config/enum-map/{id} — admin 可删
```

**GET 返回格式**（前端一次调用，按 enum_type 分组的 dict-of-dict）：

```json
{
  "order_status": {
    "AWAITING_SHIPMENT": "待发货",
    "PARTIAL_SHIPPING": "部分发货",
    "IN_TRANSIT": "运输中",
    "DELIVERED": "已送达",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
    "UNPAID": "未付款",
    "ON_HOLD": "挂起",
    "AWAITING_COLLECTION": "待揽收"
  },
  "case_type": {
    "RETURN_AND_REFUND": "退货退款",
    "REFUND_ONLY": "仅退款",
    "CANCELLATION": "取消",
    "CANCEL": "取消"
  },
  "case_status": {
    "CANCELLATION_REQUEST_COMPLETE": "取消完成",
    "RETURN_OR_REFUND_REQUEST_COMPLETE": "退货退款完成",
    "REFUND_REQUEST_COMPLETE": "仅退款完成"
  },
  "settle_component": {
    "SETTLEMENT": "结算净额",
    "COMMISSION": "平台佣金",
    "SHIPPING_FEE": "运费",
    "AFFILIATE_COMMISSION": "联盟佣金"
  },
  "cost_source": {
    "MANUAL": "人工标注",
    "SOURCE_PRICE": "货源价",
    "DEFAULT_K1": "默认40元/件"
  },
  "shipment_status": {
    "PENDING": "待处理",
    "SHIPPED": "已发货",
    "IN_TRANSIT": "运输中",
    "DELIVERED": "已送达"
  },
  "column_header": {
    "is_settled": "已结算",
    "38301": "到海外",
    "SETTLEMENT": "结算净额",
    "statement": "结算时间",
    "case": "Case ID",
    "campaign_id": "广告计划",
    "spend": "消耗",
    "orders": "广告订单"
  }
}
```

**路由注册 + 权限**：

```python
# tts_erp_v2/app.py — 新增
from tts_erp_v2.api.v2 import config as config_router
app.include_router(config_router.router)

# tts_erp_v2/middleware/auth.py — readonly 路径新增
"/v2/config/enum-map",  # GET readonly; PUT/DELETE 在 handler 层 require_role_at_least("admin")
```

### 3.4 前端改造

#### 3.4.1 页面加载时获取 enum 映射

```javascript
// spu-roi.js — bindControls() 中新增
var state = {
  // ...existing...
  enumMap: {},  // page load 时填充
};

function loadEnumMap() {
  return fetch(`${PREFIX}/v2/config/enum-map`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  })
    .then(r => r.ok ? r.json() : {})
    .then(data => { state.enumMap = data || {}; })
    .catch(() => {});
}
// bindControls() 中: loadEnumMap().then(() => load());
```

#### 3.4.2 翻译辅助函数

```javascript
// 用 enumMap 翻译单个值；type = enum_type，val = 原始英文值
function tr(type, val) {
  if (val == null || val === "") return "—";
  var map = state.enumMap[type];
  return (map && map[val]) ? map[val] : val;  // 无映射 → 原值兜底
}

// 用 column_header 翻译表头
function th(label) {
  var ch = state.enumMap.column_header;
  return (ch && ch[label]) ? ch[label] : label;
}
```

#### 3.4.3 renderDrillTabBody 改造示例

```javascript
// 订单 tab — 改造前
el("th", null, "is_settled"),
el("th", null, "38301"),
el("td", null, o.status),  // 英文原始值

// 改造后
el("th", null, th("is_settled")),    // → "已结算"
el("th", null, th("38301")),          // → "到海外"
el("td", null, tr("order_status", o.status)),  // → "运输中"
```

#### 3.4.4 枚举管理页面

新增独立管理页面 `/v2/pages/enum-map`（或嵌入 admin 面板），功能：

- 按 `enum_type` 分组展示所有映射
- 支持新增 / 编辑 / 删除单条映射
- 实时预览翻译效果（点击"测试翻译"输入英文值看中文）
- 提供"重置为默认"按钮（从 seed 数据恢复）

### 3.5 改动范围汇总

| 文件 | 改动 | 说明 |
| ------ | ------ | ------ |
| `tts_erp_v2/db/base.py` | SCHEMAS 新增 `"config"` | 1 行 |
| `alembic/versions/` (新增) | DDL migration | 创建 `config` schema + `config.enum_map` 表 + seed 数据 |
| `tts_erp_v2/db/models/config.py` (新增) | ORM model | `EnumMap` model |
| `tts_erp_v2/db/models/__init__.py` | 注册 model | import EnumMap |
| `tts_erp_v2/api/v2/config.py` (新增) | API 路由 | GET/PUT/DELETE `/v2/config/enum-map` |
| `tts_erp_v2/app.py` | 路由注册 | `include_router(config_router)` |
| `tts_erp_v2/middleware/auth.py` | 权限配置 | `/v2/config/enum-map` GET → readonly |
| `tts_erp_v2/api/v2/pages.py` | 新增 enum-map 管理页面 | `/v2/pages/enum-map` |
| `tts_erp_v2/static/js/spu-roi.js` | 改造渲染逻辑 | 加载 enumMap + tr()/th() 翻译函数 + 改造 4 个 tab 渲染 |
| `tts_erp_v2/static/js/enum-map.js` (新增) | 管理页面 JS | CRUD 交互 |

---

## 4. 实施计划

### Phase 1: 基础设施（DB + API）

| # | 任务 | 文件 | 预估 |
| --- | ------ | ------ | ------ |
| 1.1 | `base.py` SCHEMAS 新增 `"config"` | `tts_erp_v2/db/base.py` | 5min |
| 1.2 | Alembic migration: `CREATE SCHEMA config` + `CREATE TABLE config.enum_map` + seed INSERT | `alembic/versions/` | 30min |
| 1.3 | ORM model: `EnumMap` | `tts_erp_v2/db/models/config.py` + `__init__.py` | 15min |
| 1.4 | GET `/v2/config/enum-map` 端点（一次返回全量） | `tts_erp_v2/api/v2/config.py` | 30min |
| 1.5 | PUT/DELETE 端点 + admin 权限 | 同上 + `auth.py` | 30min |
| 1.6 | 路由注册 + auth 权限路径 | `app.py` + `auth.py` | 10min |
| 1.7 | 单元测试 | `tests/api/test_enum_map.py` | 30min |

### Phase 2: 前端翻译改造

| # | 任务 | 文件 | 预估 |
| --- | ------ | ------ | ------ |
| 2.1 | 加载 enumMap + tr()/th() 函数 | `spu-roi.js` | 20min |
| 2.2 | 订单 tab 表头 + status 枚举翻译 | `spu-roi.js` renderDrillTabBody("orders") | 20min |
| 2.3 | 结算 tab 表头 + component.code 翻译 | `spu-roi.js` renderDrillTabBody("settlements") | 15min |
| 2.4 | 售后 tab 表头 + type/status 翻译 | `spu-roi.js` renderDrillTabBody("cases") | 15min |
| 2.5 | 广告 tab 表头翻译 | `spu-roi.js` renderDrillTabBody("ads") | 10min |
| 2.6 | cost_source 翻译（利润构成 ⚠ 提示） | `spu-roi.js` renderProfitSummary | 10min |

### Phase 3: 配置管理页面

| # | 任务 | 文件 | 预估 |
| --- | ------ | ------ | ------ |
| 3.1 | enum-map 管理页面 HTML | `pages.py` 新增 `_ENUM_MAP_PAGE_HTML` | 45min |
| 3.2 | 管理页面 JS（CRUD 交互） | `static/js/enum-map.js` | 60min |
| 3.3 | 路由注册 | `pages.py` + `app.py` | 10min |
| 3.4 | E2E 测试（管理页面 + ROI 页面翻译效果） | 手动验证 | 30min |

### 总预估：~6.5 小时

---

## 5. 设计决策记录

### 5.1 为什么用 DB 表而不是前端硬编码？

- 用户需求明确说"提供页面可以直接配置"
- 未来新增枚举值（如新的 order status）不需要改代码发版
- DB 表 + API 的模式与项目现有风格一致（如 intercept_configs）

### 5.2 为什么 column_header 也放 enum_map？

- 统一管理：所有翻译在一个地方
- 可扩展：未来新增 tab 或列不需要改 JS
- 前端只需一个 API 调用

### 5.3 翻译缺失时的兜底策略

- **无映射 → 显示原值**（`tr()` 函数 fallback 到 `val`）
- 这比显示空白或 "???" 更好——至少用户能看到原始值
- 上线前 seed 数据应覆盖所有已知枚举

### 5.4 缓存策略

- 前端：`state.enumMap` 页面生命周期内缓存，刷新页面时重新加载
- 后端：GET 端点直查 DB，不做应用层缓存（数据量极小，< 100 行）
- enum-map 修改后，ROI 页面需刷新才能看到更新（不做强制推送）

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
| ------ | ------ | ------ |
| 后端新增枚举值但忘记同步 enum_map | 显示原值（可接受） | tr() fallback 机制；后续可加日志告警 |
| 用户误删关键映射 | 显示英文原值 | seed 数据提供"重置为默认" |
| 管理页面权限被滥用 | 错误翻译 | PUT/DELETE 限 admin 角色 |
