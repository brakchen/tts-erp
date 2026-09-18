# 基于 `dumps-data-contract.md` 改造 tts-erp — 技术方案（v0 草案，待 review）

> **范围**：以 [`tech-doc/dumps-data-contract.md`](dumps-data-contract.md) 为契约 single-source-of-truth，盘点并改造 tts-erp 订单/物流/结算/售后 4 域的 dumps 接收链路与配套文档。
> **不涵盖**：广告域 `plugin.ad_*`（走 `/v2/analytics/sync/dumps` v4 协议，独立仓 `tech-doc/analytics/`）、`plugin.intercept_*` admin 模块（独立于 chrome-ext dumps 链路）、chrome-plugins 仓代码（仅同步设计预期，仓外）。
> **owner lane**：`redesign/dumps-tts-erp-refactor`（docs-only，本阶段不下任何代码）。
> **拍板日期**：待 review。

---

## §0 TL;DR

| 问题 | 现状（2026-09-18 prod） | 改造方向 |
| --- | --- | --- |
| **契约与代码的差距** | dumps 路由 `VALID_DOMAINS` 不含 `after_sales` → 售后 parser 函数 247 行孤儿 | P0 接入 `after_sales` 域 |
| **物流域完全断流** | `plugin.shipments` / `plugin.tracking_events` 均 0 行，chrome-plugins 抓 100% 空 body 但仍重试 | P0 server 端 `empty_response` 改 422（严格 HTTP 语义）+ plugin 端协议外修复（跨仓） |
| **结算域完全断流** | `plugin.settlements` / `plugin.settlement_details` 均 0 行；`plugin.intercepted_requests` 已抓到 148 条 statement 响应 | P0 根因定位（4 候选）—— **不做回填**（A13，见 §3.4 + §5）|
| **响应 envelope 与 schema 漂移** | `logId: 0` 仍在返（Phase 3 已 drop 列，但响应字段未删）；`createdAt` 双向别名 + 校验不一致；**宽松 HTTP 语义违反 AGENTS.md §2.5 铁律** | P0 响应契约收敛（严格 HTTP 语义） |
| **文档多版本混乱** | `intercept-plugin-canonical.md`（用户原话"看着不太对"，已合并删除 — 见 lane docs/merge-canonical-into-contract）+ `chrome-ext-order-sync-design.md`（设计稿，已落后）+ `dumps-data-contract.md`（现状契约，已扩到 §6-§13 涵盖 ad 域 + 4 域 ID 映射 + 时间线 + ER + 终态 + JOIN + TODO） 三层文档职责不清 | P2 文档分层 + 单一指针（**本 lane 已完成**：canonical 已并入 contract） |

**实施节奏**（详见 §5）：

| 阶段 | 周期 | 投入 | 风险 |
| --- | --- | --- | --- |
| **P0 接入 `after_sales` 域** | 1 天 | 1 改动点 + 3 测试 | 低（parser 已存在，只缺路由） |
| **P0 物流/结算断流根因定位** | 2-3 天 | chrome-plugins 仓协调 | 中（跨仓） |
| **P0 严格 HTTP 语义落地（empty + parse_error 改 422）** | 1.5-2 天 | 单仓改动 + 跨仓同步 | 中（跨仓） |
| **P2 文档分层 / deprecation 标记** | 0.5 天 | 2 文件改动 | 极低 |

---

## §1 现状盘点（prod 2026-09-18 实测）

### 1.1 4 域业务表行数（evidence）

```
plugin.orders              1462   ✅ 订单域正常
plugin.order_lines         1499   ✅
plugin.shipments              0   ❌ 物流域 0 行
plugin.tracking_events        0   ❌
plugin.settlements            0   ❌ 结算域 0 行（旁路 148 条）
plugin.settlement_details     0   ❌
plugin.after_sales           -1   ❌ 售后域未采集（VALID_DOMAINS 不含）
plugin.after_sale_items      -1   ❌
plugin.intercepted_requests 768691   ✅ 抓取端旁路（5 域全抓到，包括结算 148 条 —— **A13 不回填，仅供 Lane C 诊断**）
plugin.raw_log             (DROP)   Phase 3 已下
```

### 1.2 dumps 端点 × domain 路由矩阵（contract §3 vs 代码 `order_sync.py:407-446`）

| dumps `domain` | chrome 采集 endpoint | parser 函数 | 落 plugin 表 | 状态 |
| --- | --- | --- | --- | --- |
| `"orders"` | `POST /api/fulfillment/order/list` | `parse_order_response` | `orders` + `order_lines` | ✅ 工作 |
| `"logistics"` | `GET /api/v1/fulfillment/logistic_detail/list` | `parse_logistics_response` | `shipments` + `tracking_events` | ⚠️ 0 行（root cause 见 §3.2） |
| `"statements"` | `GET /api/v1/pay/statement/list/detail`<br>`GET /api/v1/pay/statement/transaction/detail` | `parse_statement_list_response`<br>`parse_statement_transaction_response`（按 `data.sku_record` 分流） | `settlements` + `settlement_details` | ⚠️ 0 行（root cause 见 §3.3） |
| `"after_sales"` | **`POST /return_refund/202309/cancellations/search`** | `parse_after_sales_response` | `after_sales` + `after_sale_items` | ❌ **`VALID_DOMAINS` 不含 → 插件端即便开始采集也会被 400 拒** |

**孤儿函数**：`parse_after_sales_response`（`parser.py:478`，247 行 + 4 个测试 case）从未被 dumps 路由表调用。

---

## §2 契约 vs 代码差距清单（按优先级）

### P0-1 售后域完全未接入

**现状**（contract §5.1）：
- `VALID_DOMAINS = {"orders", "logistics", "statements"}`（`order_sync.py` + 文档 §2.1 双重声明）
- `parse_after_sales_response` 存在但 `if/elif` 链没有 `elif domain == "after_sales":`
- `_domain_must_be_valid` validator 拒收
- 插件端 `~/chrome-plugins/ads-data-sync/` 无 `cancellations/search` 引用（grep 0 hit）

**修复路径**：
1. `order_sync.py::DumpBodyIn` 把 `domain` 字段类型从 `str + 自定义 validator` 改为 `Literal["orders","logistics","statements","after_sales"]`，自动利用 Pydantic V2 枚举校验（与 `analytics.py` v4 protocol 一致风格）
2. `order_sync.py:407-446` if/elif 链加 `elif domain == "after_sales": rows_written = parse_after_sales_response(...)`
3. 测试 `tests/api/test_order_sync_contract.py` 加 2 个 case：合法 after_sales dump 走通 + 非法 domain 返 422（Pydantic 自动）
4. **不涉及** chrome-plugins 端开发（独立仓，本仓只保证 dumps 端 ready）

**预估**：1 天；现有 `parse_after_sales_response` + 4 个 test 不动，只动路由表 + 1 个新测试。

### P0-2 物流域 0 行（chrome 抓了但不进库）

**现状**（contract §5.2 类似 / canonical §2.5）：
- `plugin.intercepted_requests` 含 `logistic_detail/list` 请求记录，**但 `response_body` 100% 为 `null`**（353 条 dump 全部如此，2026-09-14 实测）
- plugin 端**仍把空 body 作为合法 dump 上传**并按 RETRYABLE 无限重试（chrome-plugins 仓 `background.ts` 已修：`payload==null` 不上传 + 带 `responseReadError` 诊断，但修复日期晚于 prod burst）
- dumps 端当前行为：`response_body is None` 走 `_ok_response({status: "empty_response", logId: 0, rowsWritten: 0})`（`order_sync.py:380-389`）→ **违反 AGENTS.md §2.5 严格语义铁律** —— "未写入数据不得返 200"

**根因（双端问题）**：
- **chrome-plugins 端**（跨仓）：`/logistic_detail/list` 接口被 Seller Center 返回非 JSON / 空 body，应在插件层直接丢弃 + 标 `responseReadError` 上报到 `plugin_logs`
- **本仓 dumps 端**：契约 §2.3 错误码清单明确"empty_response 返 200"——**违反 AGENTS.md §2.5；必须改成 422**

**修复路径**：
1. **本仓（必走）**：`order_sync.py:380-389` 把 empty_response 从 200 改成 `422 Unprocessable Entity` + `code=EMPTY_RESPONSE_BODY`+ `message="dump.response.body is null; plugin must not advance progress"`
2. **本仓**：测试加 case `empty_response → 422 + plugin 收到非 200 不前进`（与现行断言 `assert r.status_code == 200` 反转）
3. **跨仓**：协调 chrome-plugins 仓修复 — 这是 `dumps-data-contract.md §5.6`（从原 canonical §2.5 合并）已记录的已知坑，本方案**仅负责本仓侧**，跨仓修复另开 lane

**预估**：1 天（仅本仓）。

### P0-3 结算域 0 行（4 个候选根因）

**现状**（contract §5.2）：
- 现象：`plugin.settlements` / `plugin.settlement_details` 0 行；`plugin.intercepted_requests` 含 statement endpoint **148 条**
- 假设根因（4 候选，未 root cause）：
  1. chrome-plugins `logistic_detail` 失败触发 `clearBoundDataSyncTab('statement_authentication_failed')` 提前退出（`background.ts:768-775`）
  2. 60min alarm 没真触发（绑定的 tab 访问 Finance 页 < 60min）
  3. chrome `fetchStatementRows` schema 校验失败返回 `[]`（`background.ts:724-728`）
  4. dumps 端 `parse_statement_list_response` 解析失败但 `_ok_response` 仍返 200，错误被吞

**修复路径**：
1. **第 1 步（定位）**：本仓在 `order_sync.py::post_dumps` 增加"per-domain health counter"，写到 `plugin.plugin_logs`（已有表，level=info 字段齐）
   - 维度：`domain`、`shop_id`、`endpoint`、`rows_written`、`parse_error_class`、`captured_at`、`server_received_at`
   - 目的：补 evidence 给 chrome-plugins 仓定位（按 `domain=statements AND rows_written=0` 即可定位是否 dump 端问题）
2. **第 2 步（修复）**：诊断结论后**两类修复**（不互斥）：
   - 若根因在 chrome 端 → 跨仓修（独立 lane）
   - 若根因在 dumps 端 → 本仓 `parse_statement_list_response` 加 fallback：识别空 list、`summary.total` 字段；非空但解析失败的，写一行 `plugin.plugin_logs` level=warn message=具体字段缺失

**预估**：2-3 天（定位 + 修复）。

> **A13：不做旁路回填**（用户拍板）—— chrome-plugins 修复 dumps 流后自然增量补齐更新数据。148 条 `plugin.intercepted_requests` 数据 stale（1-2 周），回填旧数据风险 > 价值。Lane D（`feat/statements-backfill`）从方案中**删除**。

### P0-1b 响应 envelope 漂移清理（严格 HTTP 语义落地 + Phase 3 残留 + rowsWritten 死字段清理）

> **升级为 P0**：因为涉及违反 AGENTS.md §2.5 铁律的现状修正，不是简单清理

**现状**：
- `plugin.raw_log` 已 drop（Phase 3 已合并），但 `_ok_response({... "logId": 0, "rowsWritten": 0})` 仍返 `logId: 0` 字段（contract §2.4 已写"`logId` 字段恒 0，chrome-plugins 端无感知"，但 Phase 3 后这个字段是 0 误导）
- `parse_error` 分支也返 200 + `data.status: "parse_error"`——同 P0-2 一样违反 AGENTS.md §2.5
- **`rowsWritten` 是死字段**（chrome-plugins 端 `order-sync.ts:10-13` typed schema 保留但**从不读取**；line 54-57 `isDumpAccepted()` 仅看 `data.status`；line 316 `rowsWritten: data?.rowsWritten ?? 0` 只是 typed 转换）—— 本仓硬返它既浪费 payload 也造成语义双重表达
- **`rowsWritten=0` 是 `parse_error` 探测 hack**（`order_sync.py:415-416`）—— 把"协议层信号"塞进"数据层计数器"是错工具做错事
- **`order_sync.py:415` 的 hack 不覆盖 logistics 域**——`domain ∉ {"orders", "statements"}` 时不设 parse_error，rowsWritten=0 会走 200 + `status: "inserted"`。这正是 §5.3 物流 0 行事故的**额外根因**（contract 文档未记录此点）

**修复路径**（严格 HTTP 语义落地 + 简化 + envelope 一致）：
1. **必走**：dumps response payload **删 `rowsWritten` 字段**（3 处：empty / parse_error / inserted）；保留为 audit log `records_ok=rows_written`（server 内部 metrics）
2. **必走**：dumps response payload **删 `logId` 字段**（3 处：Phase 3 残留）
3. **必走**：dumps response payload **删 `data.status` 字段**（3 处：empty_response / parse_error / inserted）；HTTP code 已是唯一成功/失败信号
4. **必走**：`order_sync.py:411-446` `parse_error` 分支从 200 改成 **`422 Unprocessable Entity`** + `code=PARSE_ERROR`；同时**删** `if parse_error is None and rows_written == 0` hack（HTTP 4xx 已足够表达协议失败）
5. **必走**：`order_sync.py:380-389` `empty_response` 分支从 200 改成 **`422 Unprocessable Entity`** + `code=EMPTY_RESPONSE_BODY`（**2026-09-18 用户拍板 α 方案**：`empty body = TikTok 问题 = 重试无意义 = PERMANENT`，plugin 不再无限重试；这正是 §5.3 物流 0 行事故的根本修复）
6. **必走**：**envelope 一致化**——`order_sync.py::_ok_response` 加 `message="success"` 字段（仅 200 时）；4xx/5xx `_error_response` 已有 `message`，保持；4 个字段（`code` / `message` / `requestId` / `data`）200 与非 200 都有，差别仅在 `code` 类型 + `data` 是否出现（详见 AGENTS.md §2.5）
7. **必走**：dumps response envelope 简化（200 返空 `data={}` 即可）
8. **必走**：测试 `test_dumps_empty_response_returns_clean_status` 等 4 个 case 反转断言为 422 + `code=EMPTY_RESPONSE_BODY` / `PARSE_ERROR`；删所有 `rowsWritten` 断言 + `data.status` 断言；加 200 envelope 完整性断言（含 `message="success"`）
9. **必走**：同步更新 `dumps-data-contract.md` §2.3 错误码清单 + §2.4 成功 envelope 定义（删 `rowsWritten` / `logId` / `data.status` 字段说明 + 明确 envelope 4 字段结构 + empty_response 明确为 PERMANENT）
10. **跨仓协调**（chrome-plugins 仓，**业务逻辑改动**）：
    - `src/core/order-sync.ts`：
      - `DumpUploadStatus` type 简化为 `'inserted'`（其他状态作废）
      - `isDumpAccepted()` 函数删除（死代码，0 调用点）
      - `uploadOrderSyncDump` 内 `if (result.status === 'parse_error')` / `if (result.status === 'empty_response')` 两个 throw 分支删除（HTTP 4xx/5xx 走通用 `responseError()`；`responseError` 在 `analytics-sync-v2.ts:963-968` 已实现 `response.status >= 500 ? 'RETRYABLE' : 'PERMANENT'`，dumps 端点复用即可）
      - `parseDumpResponse` 删 `status` / `rowsWritten` 赋值（仅保留 `requestId` 用于日志追踪）
    - `src/core/order-sync-schemas.ts`：`OrderSyncDumpResponseSchema` Zod schema 删 `status` / `rowsWritten` 字段（保留 `requestId`），**新增** `message: z.string()` 字段（200 时 server 加了 `message="success"`）
    - `src/extension/storage.ts` line 140：`status: 'inserted' | 'duplicate'` 是 storage 内部状态，不是 HTTP response 字段，**保留**

**关键简化**：

A4 + A5 + 上面 1-3 条联动后，`rowsWritten=0` / `data.status` 双重语义**完全消除**——HTTP code 成为唯一成功/失败信号。`rowsWritten` 在 server-side 仍保留为 `_log_event` 的 `records_ok` 字段（审计用），但**不进 HTTP response**。

**empty_response 行为变化**（2026-09-18 拍板）：

| | 旧行为 | 新行为 |
|---|---|---|
| empty body | 200 + RETRYABLE（无限重试） | **422 + PERMANENT**（plugin 跳过 + 记日志 + 等下次 alarm） |
| parse error | 200 + PERMANENT | **422 + PERMANENT**（不变）|
| HTTP 5xx | RETRYABLE | RETRYABLE（不变）|
| HTTP 4xx 协议错误 | PERMANENT | PERMANENT（不变）|

empty body 改 PERMANENT 是 §5.3 物流 0 行事故的**根本修复**——plugin 不再无限重试无意义的空 body 请求，避免 CPU/流量打满。

**预估**：1.5-2 天（删除为主，跨仓协调轻量）。

### P1-2 Pydantic validator 校验不一致（contract §2.1 末"两端校验差异表"）

**现状**：contract §2.1 末明确指出：
- `protocolVersion`：server 可选（默认 1），plugin **必填**
- `createdAt` 时区：server 必须带 tz（naive 拒），plugin 仅 min(1)

**修复路径**：
1. **本仓**：`DumpBodyIn` 把 `protocolVersion` 改为必填（移除 default=1），与 plugin 对齐
2. **本仓**：`createdAt` 现有 `_created_at_must_be_utc` validator 已正确，不动
3. **测试**：加 case `protocolVersion 缺失 → 422` + `protocolVersion=1 正常通过`

**预估**：0.5 天。

### P2 文档分层与单一指针

**现状**：3 个 dumps 相关文档职责不清：
- ~~`intercept-plugin-canonical.md`（21KB，2026-09-13）：概念拍板稿，但用户原话"看着不太对"~~ — **已合并并删除**（lane `docs/merge-canonical-into-contract`）
- `chrome-ext-order-sync-design.md`（53KB，2026-09-14）：设计稿，已部分落后（raw_log 设计仍存在，Phase 3 已 drop）
- `dumps-data-contract.md`（28KB，2026-09-16）：现状契约（代码为准）

**修复路径**：
1. ~~`intercept-plugin-canonical.md` 加顶部 banner~~ — **已合并并删除**（lane `docs/merge-canonical-into-contract`）
2. `chrome-ext-order-sync-design.md` 加头部 banner 标注哪些章节被 dumps-data-contract 取代（具体 §3.x raw_log 设计、§5 progress 协议等）
3. `dumps-data-contract.md` §0 TL;DR 加指向性交叉链接（来自 §3 路由表、§4 字段映射、§5 已知 gap）

**预估**：0.5 天（docs-only）。

---

## §3 详细修复路径（按 lane 拆分）

> 每个 lane 是**独立 worktree**，可并行（docs-only 不冲突；代码 lane 各自独占文件）。

### 3.1 Lane A：`feat/after-sales-routing`（P0-1）

| 项目 | 内容 |
| --- | --- |
| 文件 | `tts_erp_v2/api/v2/order_sync.py`（domain 字段 + if/elif 链）<br>`tests/api/test_order_sync_contract.py`（+2 case）<br>`tech-doc/dumps-data-contract.md`（§2.1 VALID_DOMAINS 表加 `after_sales` 行 + §3 路由表解 orphan） |
| 改动量 | ~15 行 order_sync.py + 30 行测试 + 2 行文档 |
| 风险 | 低（purely additive） |
| 验证 | `bash scripts/test.sh fast` 0 新 fail；prod 端点带 `domain=after_sales` 的 dump（人工 curl）返 200 + rowsWritten>0 |
| 收尾 | merge → push |

### 3.2 Lane B：`fix/logistics-empty-response`（P0-2）

| 项目 | 内容 |
| --- | --- |
| 文件 | `tts_erp_v2/api/v2/order_sync.py:380-389`（empty_response 200 → 422）<br>`tests/api/test_order_sync_contract.py`（+1 case） |
| 改动量 | ~10 行 |
| 风险 | **中**——plugin 端依赖当前 200 行为（`background.ts:702` 的 `uploadOrderSyncDump`），改 422 会让 plugin 不前进进度 = 物流抓取实际停滞<br>**前提**：chrome-plugins 仓修复先发（empty body 不上传），否则本仓改 422 后 plugin 会无限重试，CPU/流量打满 |
| 依赖 | 跨仓协调 — chrome-plugins lane 先合并 |
| 验证 | test 库用 fixture 模拟 `response_body=None` → 422；prod 监控 24h `plugin_logs` 是否还出现 "logistic_detail empty response retry" 关键字 |
| 收尾 | merge → push → 通知 chrome-plugins 仓升级协议版本 |

### 3.3 Lane C：`fix/statements-gaps-diagnose`（P0-3 第 1 步）

| 项目 | 内容 |
| --- | --- |
| 文件 | `tts_erp_v2/api/v2/order_sync.py::post_dumps`（加 health counter）<br>`tests/api/test_order_sync_contract.py`（+2 case：rowsWritten=0 必落 plugin_logs；rowsWritten>0 不落） |
| 改动量 | ~30 行（独立函数 `_record_dump_health(sess, ...)`） |
| 风险 | 低（仅新增日志写入） |
| 验证 | 24h 后从 `plugin.plugin_logs` 取 `domain=statements AND rows_written=0` 的样本，按 `parse_error_class` 分布决定根因 |
| 收尾 | merge → push → 监控 24h |

### 3.4 ~~Lane D（条件性）~~ — **A13 已取消**

~~`fix/statements-parser-fallback` 或 `feat/statements-backfill`~~ — **不需要**：chrome-plugins 修复 dumps 流后自然增量补齐；148 条 `plugin.intercepted_requests` 数据 stale，回填旧数据风险 > 价值。

> 若 Lane C 诊断结论为 dumps 端 parser 有 bug，**仅修 parser**（不需要回填）。若根因为 chrome 端，跨仓独立 lane。

### 3.5 Lane E：`fix/strict-http-semantics`（P0-1b，**严格 HTTP 语义落地 + envelope 一致化**）

| 项目 | 内容 |
| --- | --- |
| 文件 | `tts_erp_v2/api/v2/order_sync.py`（`empty_response` / `parse_error` 改 422 + 删 `logId` 字段 + 删 `rowsWritten` 字段 + 删 `data.status` 字段 + 删 `rowsWritten=0` hack + `_ok_response` 加 `message="success"`）<br>`tests/api/test_order_sync_contract.py`（`empty_response` / `parse_error` / `idempotent_dumps` 等 4 个 case 反转 status_code 断言 + 删 logId/rowsWritten/data.status 断言 + 加 200 envelope 完整性断言）<br>`tech-doc/dumps-data-contract.md` §2.3 错误码清单重写 + §2.4 成功 envelope 重新定义（明确 4 字段结构 200/非 200 一致）<br>`AGENTS.md §2.5`（已提交） |
| 改动量 | ~35 行 order_sync.py + 35 行测试 + 30 行文档（**净删比净增多**）|
| 风险 | 中（HTTP code 语义变化 + chrome-plugins 端业务逻辑改动 — `isDumpAccepted` / `parse_error` / `empty_response` 分支删除）|
| 依赖 | 跨仓协调（chrome-plugins 仓需同步升级 `recordOrderProgress` 逻辑 + 删 `DumpResponse.rowsWritten` schema 字段 + `isDumpAccepted` 等业务逻辑调整）|
| 验证 | test 库：`empty_response → 422 + EMPTY_RESPONSE_BODY`、`parse_error → 422 + PARSE_ERROR`；成功响应断言 4 字段齐全（`code=0` + `message="success"` + `requestId` + `data`）且不含 `rowsWritten` / `logId` / `data.status`；chrome-plugins 端 schema 同步发布 |

### 3.6 Lane F：`fix/dumps-validation-align`（P1-2）

| 项目 | 内容 |
| --- | --- |
| 文件 | `tts_erp_v2/api/v2/order_sync.py::DumpBodyIn`（protocolVersion 必填）<br>`tests/api/test_order_sync_contract.py`（+2 case） |
| 改动量 | ~5 行 |
| 风险 | 低（plugin 端实际已必填，本仓对齐是补齐） |
| 验证 | test 库：缺 protocolVersion → 422；plugin 端无回归 |

### 3.7 Lane G：`docs/dumps-doc-rationalize`（P2）

| 项目 | 内容 |
| --- | --- |
| 文件 | ~~`tech-doc/intercept-plugin-canonical.md`（顶部 banner）~~ — **已合并并删除**<br>`tech-doc/chrome-ext-order-sync-design.md`（§3.x raw_log 设计 banner）<br>`tech-doc/dumps-data-contract.md`（§0 交叉链接） |
| 改动量 | ~30 行（3 文件各 10 行） |
| 风险 | 极低 |
| 验证 | 人工 review 文档一致性 |

---

## §4 风险与权衡

### 4.1 跨仓协调成本

| Lane | 跨仓依赖 | 协调方式 |
| --- | --- | --- |
| A (after_sales 路由) | chrome-plugins 端未启动采集，本仓 ready 即够 | 不依赖 chrome-plugins；本仓先发 |
| B (logistics empty) | chrome-plugins 必须先修 `response_body=null` 不上传 | **先**在 chrome-plugins 仓开 lane，本仓随后 |
| C (statements 诊断) | 无 | 本仓独立 |
| D (statements 修复) | 取决于根因；若 chrome 端需另开 | 视诊断结论 |
| E (envelope) | chrome-plugins schema 升级 | 同步发版本；deprecation 周期 30 天 |
| F (validation) | chrome-plugins 端实际已正确 | 不需要协调 |
| G (docs) | 无 | 本仓独立 |

### 4.2 ~~数据回填策略~~ — **A13 已取消**

~~仅 `plugin.intercepted_requests` 里有 statement 148 条可回填。物流域无 body 无法回填——只能等 chrome-plugins 修复后自然增量补齐。~~

**A13 拍板**：不做任何回填。chrome-plugins 修复 dumps 流后，自然增量补齐**更新**数据（最新 1 周/1 月结算）；148 条 intercepted_requests 旧数据 stale，回填旧数据风险 > 价值。物流域无旁路数据可回填，必须等 chrome-plugins 修复 + 自然增量。

### 4.3 不做什么（明确 out-of-scope）

- **不**重构 `parse_*` 函数签名（不变，向后兼容）
- **不**改 dumps 端点 path（`/v2/order-sync/dumps` 不动）
- **不**把 dumps 接收端点合并到 `/v2/analytics/sync/dumps`（ad 域 v4 vs order 域 v3 协议不同，合并会让 Pydantic schema 复杂度爆炸）
- **不**改 chrome-plugins 仓代码（仅协调 + 协议文档）
- **不**碰 `plugin.intercept_*` admin 模块（独立 lane 范围）

---

## §5 实施节奏（推荐顺序）—— A15 推荐1 修正

> **修正**：以下不严格按 Week 1 / Week 2 时间分周。7 个 lane 并行开 worktree，merge 按依赖关系走（A/C/F/G 无依赖立即合；B/E 等 chrome-plugins 同步发布后再合）。详见 review §A15 + B3 决策。

```
【独立 — 可随时 merge】
├── Lane A  feat/after-sales-routing      [P0-1]  1 天
├── Lane C  fix/statements-gaps-diagnose   [P0-3 step 1] 2 天
├── Lane F  fix/dumps-validation-align     [P1-2]  0.5 天
├── Lane G  docs/dumps-doc-rationalize     [P2]   0.5 天

【需跨仓同步 — chrome-plugins 仓对应 lane 发布后才合】
├── Lane E  fix/strict-http-semantics      [P0-1b] 1.5-2 天
│           本仓只动 order_sync.py + 4 个测试 + dumps-data-contract.md；
│           跨仓同步需同步协调，但本仓先行 merge 没问题（chrome-plugins 仓升级前
│           不会返非 200，旧 plugin 仍按 200 + data.status 走，不影响现有功能）
└── Lane B  fix/logistics-empty-response   [P0-2] 1 天
           （前提：chrome-plugins 仓 B-frontend（response_body=null 不上传）合并）
```

**并行模式**：
- 所有 7 个 lane 可同时在 7 个 worktree 并行开发
- 跨仓协调通过 A16 推荐的正式"跨仓 handoff"模式（每个仓维护自己的 `handoff/ACTIVE.md`）
- 本仓 merge 不依赖 chrome-plugins 仓发布（A/C/F/G 无依赖；E/B 等 chrome-plugins ready）

**关键路径**（依赖图）：
```
chrome-plugins 仓 B-frontend → 本仓 Lane B merge
chrome-plugins 仓 E-frontend → 本仓 Lane E merge
（其余 5 lane 无跨仓依赖）
```
但 **Lane E（本仓严格 HTTP 语义）不需等跨仓**：本仓先合、不推 prod，等跨仓同步后一齐上。
**Lane D（回填）已取消**（A13）：不实施，只剩 Lane C 诊断 + parser 修复。

---

## §6 验证标准（每个 lane 的 hard gate）

| Lane | 必须通过 |
| --- | --- |
| A | `bash scripts/test.sh fast` 0 新 fail；prod curl `domain=after_sales` 返 200 |
| B | test 库：`response_body=None` → 422 + `code=EMPTY_RESPONSE_BODY` |
| C | test 库：`rowsWritten=0` 必落 `plugin_logs`；prod 监控 24h 看到 statements 域样本 |
| D | 一次性脚本：`--dry-run` 预览与 `plugin.intercepted_requests` 行数匹配；`--confirm` 实跑后 `plugin.settlements > 0` |
| E | test 库：`empty_response → 422 + EMPTY_RESPONSE_BODY`、`parse_error → 422 + PARSE_ERROR`；成功响应断言 4 字段齐全（`code=0` + `message="success"` + `requestId` + `data`）且不含 `rowsWritten` / `logId` / `data.status`；chrome-plugins 端 `DumpResponse` schema 删 `rowsWritten`/`data.status` + 业务逻辑升级（`isDumpAccepted` 删除、`parse_error`/`empty_response` 分支删除）同步发布 |
| F | test 库：缺 `protocolVersion` → 422；plugin 端无回归 |
| G | 人工 review 3 个文档无矛盾 |

---

## §7 待用户确认的决策点

1. **Lane 命名**：用 `redesign/*` / `feat/*` / `fix/*` 前缀？建议：
   - `feat/after-sales-routing` (新增能力)
   - `fix/logistics-empty-response` (修空 body 处理)
   - `fix/statements-gaps-diagnose` (诊断性)
   - ~~`feat/statements-backfill` (一次性回填)~~ — **A13 已取消**
   - `fix/strict-http-semantics` (严格 HTTP 语义落地)
   - `fix/dumps-validation-align` (对齐)
   - `docs/dumps-doc-rationalize` (文档)

2. ~~**合并顺序** — 推荐：~~
   - ~~Day 1（Lane A）单 lane merge → push~~
   - ~~Day 4-5（Lane F + Lane G）一起合并（无依赖 + 都是低风险）~~
   - ~~Lane B / Lane E 必须等跨仓，先合并 docs/contracts，代码 lane 排 Week 2~~
   - **A15 已拍板（推荐1）**：7 个 lane 并行开 worktree，merge 按依赖关系走（A/C/F/G 无依赖立即合，B/E 等 chrome-plugins 同步发布后再合）。详见 §5 修正版。

3. ~~**回填脚本是否要等 Lane C 诊断结论** — 还是 24h 监控就先并行写？~~
   - ~~建议：等诊断结论。如果 dumps 端没问题，chrome 端修了自然有数据；148 条不是紧急数据~~
   - ~~如果 dumps 端有 bug，回填脚本反而会用错 parser 重写脏数据~~
   - **A13 已拍板：不回填**（用户原话 "B4 不需要回填，我重新抓取就可以了"）—— chrome-plugins 修复 dumps 流后自然增量补齐更新数据；148 条 intercepted_requests 旧数据 stale，回填风险 > 价值

4. ~~**`intercept-plugin-canonical.md` 是否要 deprecate（移到 `_archive/`）**，还是仅顶部加 banner 保留历史？~~ — **已通过 Lane `docs/merge-canonical-into-contract` 解决**：合并有效内容到 `dumps-data-contract.md` 后直接删除 canonical.md

5. **chrome-plugins 仓协调机制**：本仓 owner 直接联系 chrome-plugins 仓 owner，还是通过 `handoff.md` 提需求？
   - 当前倾向：直接 IM/口头 + 本仓写 RFC-like 摘要贴回 `handoff.md`

---

## §8 关联文档

- 现状契约（single-source-of-truth）：[`tech-doc/dumps-data-contract.md`](dumps-data-contract.md)
- 设计稿（部分落后）：[`tech-doc/chrome-ext-order-sync-design.md`](chrome-ext-order-sync-design.md)
- ~~概念拍板稿（用户原话"不太对"）：[`tech-doc/intercept-plugin-canonical.md`](intercept-plugin-canonical.md)~~ — 已合并删除
- API catalog（53+ endpoint 全清单）：[`tech-doc/tiktok-seller-center-api-catalog.md`](tiktok-seller-center-api-catalog.md)
- 订单业务规则：`tech-doc/order-domain-business-rules.md`
- AGENTS.md §6（destructive guard）、§7（worktree 纪律）、§11（master 重测）、§12（WIP 归属）

---

> **本方案状态**：v0 草案，等用户 review 后开 P0 lane 实施。
> **更新机制**：每个 lane 完成后在本 doc §3 加 commit 链接 + 实际工期复盘。