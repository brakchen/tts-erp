# `dumps` 数据同步链路 review 汇总（2026-09-18）

> **来源**：从 dumps-data-contract.md 改造 tts-erp 方案讨论中沉淀的所有结论、待定、bug、可疑代码
> **目的**：用户 review 用——按"已拍板 / 待拍板 / bug / 协议不清 / 逻辑错 / 死代码"6 类组织
> **关系**：与 `tech-doc/dumps-tts-erp-refactor-proposal.md` 配套，proposal 是"做什么"，本文件是"我们聊出来的全清单"

---

## A. 已拍板结论（用户明确表态）

| # | 结论 | 来源 | 落地位置 |
| --- | --- | --- | --- |
| **A1** | **采用严格 HTTP 语义**：2xx = 数据已写入；4xx/5xx = 协议失败/数据失败 | 用户原话 "如果使用 http status code 那 tts-erp 没写入数据之前，就不能返回200，这是严令禁止的行为" | AGENTS.md §2.5；proposal §2 P0-1b |
| **A2** | **通用规则写在 AGENTS.md §2**（与 §2.1/2.2/2.3/2.4 同级）—— 服务端交互协议严格语义是 agent 必读红线 | 用户原话 "这种服务端交互协议非常严格的通用元素，要求 agent 必须遵守，应该写在哪里" | AGENTS.md §2.5 新增 |
| **A3** | **响应 envelope 200 / 非 200 必须结构一致**：4 字段 `code` / `message` / `requestId` / `data` 都有；差别仅在 `code` 类型（int=0 vs str=错误码）和 `data` 是否出现 | 用户原话 "body 200 和 非200时要保持一致，200 时就是 code=0 message=success request_id={genereated id}" | AGENTS.md §2.5；proposal §3.5 Lane E |
| **A4** | **`rowsWritten` 是死字段，删掉** —— chrome-plugins `isDumpAccepted()` 仅看 `data.status`，从不读 `rowsWritten` | 用户原话 "为什么要对 rowsWritten 计数？" | AGENTS.md §2.5；proposal §2 P0-1b step 1 |
| **A5** | **`if parse_error is None and rows_written == 0` hack 删掉** —— 把协议层信号塞进数据计数器是错工具做错事 | 用户原话（隐含）"rowsWritten 的含义本身就不清晰... 如果是解析类的失败直接返回非200 即可" | proposal §2 P0-1b step 2 |
| **A6** | ~~**`intercept-plugin-canonical.md` 不是 single-source-of-truth**~~ → 已通过 lane `docs/merge-canonical-into-contract` 解决：合并到 contract 后删除 canonical.md | 用户原话 "这份文件看着不太对" | （详见 G8 + proposal §3.7 Lane G；现已合并删除） |
| **A7** | **物流 empty response 的范围严格限定在 chrome-ext dumps 同步链路** —— 不引入 `integration.raw_records` 旁路 | 用户原话 "我再说一遍，这是插件的数据同步，不要扯到另一条服务端 API" | 方案已修正 |
| **A8** | **scope of "通用元素" = 所有 tts-erp 服务端端点**（不只是 dumps） | 用户原话 + AGENTS.md §2.5 适用范围 | AGENTS.md §2.5 |
| **A9** | **`data.status` 字段全删**（empty_response / parse_error / inserted 三处都不再返） | 用户原话 "如果没用就删除" | proposal §2 P0-1b step 3；AGENTS.md §2.5 |
| **A10** | **命名 casing 不强制全仓统一**：dumps 端点沿用 `requestId`（camelCase），其他模块跟随文件本地惯例；不引入跨仓重命名 | 用户原话 "全仓库统一，现在逻辑里面写的是requestId 就用，逻辑里面写的是request_id 就用 request_id" | AGENTS.md §2.5 "字段命名规则" |
| **A11** | **per-layer 命名规则明确化**：wire format（HTTP envelope / JSON / Pydantic schema field / TS 变量）= camelCase `requestId`；Python 内部 / DB / URL = snake_case `request_id`；HTTP header = `x-request-id`（RFC 7230） | 调研发现当前仓库已经按层划分（`analytics.py` / `intercept.py` / `test_intercept_sync.py` 都同时存在两种 casing 但分工清晰） | AGENTS.md §2.5 "字段命名规则"（5 行表格 + 5 类约束） |
| **A12** | **empty_response = PERMANENT**（α 方案）：empty body 返 422 → plugin 跳过 + 记日志 + 等下次 alarm，不再无限重试 | 用户原话 "empty body 是 TikTok 那边的问题，重试无意义" | proposal §2 P0-1b step 5；contract §2.3/§2.4 同步 |
| **A13** | **不需要回填 settlement 148 条**：chrome-plugins 修复 dumps 流后自然增量补齐；148 条 `plugin.intercepted_requests` 数据 stale（1-2 周），回填旧数据风险 > 价值 | 用户原话 "B4 不需要回填，我重新抓取就可以了" | review §G7 取消 Lane D；proposal §3.4 + §5 移除回填；contract §13 移除 TODO #10 |

---

## B. 待拍板决策（已讨论但未拍）

| # | 议题 | 现状 | 选项 |
| --- | --- | --- | --- |
| ~~**B1**~~ | ~~`data.status` 字段是否完全删除？~~ | **A9 已拍板：完全删除** | — |
| ~~**B2**~~ | ~~`requestId` vs `request_id` casing？~~ | **A11 已拍板：per-layer 划分（wire format camelCase / Python 内部 snake_case），不强制统一** | — |
<<<<<<< Updated upstream
| ~~**B-α/β**~~ | ~~empty_response 是 RETRYABLE 还是 PERMANENT？~~ | **A12 已拍板：PERMANENT**（empty body = TikTok 问题 = 重试无意义）| — |
| **B3** | Lane 合并顺序：Week 1 先合低风险 4 个，Week 2 等跨仓？ | 当前 proposal §5 安排 | 待确认 |
| **B4** | 回填脚本是否等 Lane C 诊断结论？ | 当前 proposal §7 建议等（避免用错 parser 写脏数据） | (α) 等 24h 诊断；(β) 并行写 |
| ~~**B5**~~ | ~~`intercept-plugin-canonical.md` deprecate vs 加 banner？~~ | **已通过 lane `docs/merge-canonical-into-contract` 解决**：合并到 contract 后直接删除 | ~~(α) 加 banner；(β) 移到 `tech-doc/_archive/`~~ |
| **B6** | chrome-plugins 仓协调机制？ | 当前 proposal §7 建议直接 IM + handoff.md 摘要 | (α) 直接 IM；(β) 走 handoff.md 正式 |
| **B7** | Lane 命名风格？ | 当前用 `feat/*` / `fix/*` / `docs/*` | 待确认 |
| ~~**B9**~~ | ~~`_error_response` 中 `request_id or f"req-{uuid.uuid4()}"` 兜底逻辑要不要？~~ | **A14 已拍板：保留（α）** | — |
=======
| ~~**B-α/β**~~ | ~~empty_response 是 RETRYABLE 还是 PERMANENT？~~ | **A12 已拍板：PERMANENT**（empty body = TikTok 问题 = 重试无意义） | — |
| **B3** | Lane 合并顺序 | proposal §5 安排 | **⏵ 推荐 α**：Week 1 合低风险（Lane A / F / G），Week 2 等跨仓同步后合（Lane B / E），Lane C/D 按诊断结论走 |
| **B4** | 回填脚本时机 | proposal §7 建议等 | **⏵ 推荐 α**：等 24h Lane C 诊断结论再决定，避免用错 parser 写脏数据；148 条不紧急 |
| **B5** | canonical.md 处置 | proposal §7 建议 banner | **⏵ 推荐 α**：加顶部 banner 保留，迁 _archive/ 需要 AGENTS.md §9 业务信息索引同步改（额外工作）；等没人引用时再归档 |
| **B6** | chrome-plugins 协调 | proposal §7 建议直接 IM | **⏵ 推荐 α**：直接 IM 协商 + 本仓 `handoff.md` 留摘要记录；不另起一仓的 `handoff/ACTIVE.md` 同步机制 |
| **B7** | Lane 命名风格 | proposal §3 提案 | **⏵ 推荐沿用方案**：`feat/after-sales-routing` / `fix/logistics-empty-response` / `fix/statements-gaps-diagnose` / `feat/statements-backfill` / `fix/strict-http-semantics` / `fix/dumps-validation-align` / `docs/dumps-doc-rationalize`（conventional commits） |
| ~~**B9**~~ | ~~`_error_response` 兜底逻辑~~ | **A14 已拍板：保留（α）** | — |
>>>>>>> Stashed changes

---

## C. 已识别的可疑 bug（不止 dumps 端）

### C1. 【P0】物流域 0 行的**额外根因**（contract §5.3 没记录）

```python
# order_sync.py:415
if parse_error is None and rows_written == 0 and domain in {"orders", "statements"}:
    parse_error = f"no {domain} rows parsed from response"
```

`domain ∉ {"orders", "statements"}` 时不设 parse_error → logistics 域 rowsWritten=0 直接走 200 + `status: "inserted"` → plugin `isDumpAccepted('inserted')` → true → 不抛错 → 不重试 → 物流表永远 0 行。

**这与 contract §5.3 列的 3 个根因（c1 GET bug / c2 API 改 / c3 没触发详情页）是平行的"客户端误判成功"根因**。

### C2. 【P1】chrome-plugins 本地 polling 返回 0 行直接标 `'ok'`

```ts
// background.ts:22-28
if (!rows.length) {
  await recordOrderProgress(
    'orders',
    { total: 0, covered: 0, uploaded: 0, pending: 0 },
    'ok',  // ← 0 行也标 ok
    state,
    '订单域轮询返回 0 行：bound-page order/list 解析为空（schema 校验失败 / TikTok 返回空 / 解析异常）。',
  );
```

**同类问题**：polling 返回 0 行可能因为 schema 校验失败，但 plugin 标 `'ok'`。与 dumps 端无关，但属于 chrome-plugins 内部 progress 推进逻辑漏洞。

### C3. 【P1】`fetchStatementRows` schema 校验失败返 `[]` + 标 `'ok'`

```ts
// background.ts:724-728（contract §5.2 b3 已记录）
```

`fetchStatementRows` 在 main-frame fetch schema 校验失败返回 `[]` → `recordOrderProgress(... 'ok', '...返回 0 行...')`。**结算域 0 行的根因候选之一**。

### C4. 【P2】`order_sync.py:415` hack 是协议层错误

```python
if parse_error is None and rows_written == 0 and domain in {"orders", "statements"}:
    parse_error = f"no {domain} rows parsed from response"
```

- 把 "数据层计数器" 当 "协议层错误探测"
- 错误：logistics 域被排除（A1 衍生）
- 错误：rowsWritten=0 不能区分合法空 vs 解析失败（rowsWritten 含义本身不清）
- **修复方向**：用 HTTP 4xx 替代（A1 + A4 + A5 一致落地）

### C5. 【P2】`_error_response` 兜底生成 request_id

```python
# order_sync.py:196
"requestId": request_id or f"req-{uuid.uuid4()}",
```

`_audit_and_error` 调用 `_error_response` 时已传 request_id（line 217 提前生成），这个 `or` 兜底几乎永不触发。**死分支**，但保留也无害。详见 B9。

### C6. 【P2】dumps 端 `sess.begin_nested()` savepoint 隔离

```python
# order_sync.py:373
with sess.begin_nested():
    if domain == "orders":
        rows_written = parse_order_response(...)
```

**疑点**：upsert 是 idempotent（自然键 `UNIQUE (shop_id, order_id)`），按理不需要 savepoint 隔离。如果 parser 部分成功部分失败要全回滚——但单 dump 内多个 main_order 之间不应该互相影响（应该是 1 个 main_order 失败不应阻断整个 dump）。**当前 savepoint 把整个 dump 视为原子**——可能是有意（避免半成品 dump），但缺少 commit 注释解释。

### C7. 【P2】`parse_statement_list_response` vs `parse_statement_transaction_response` 按字段分流

```python
# order_sync.py:393-404
elif domain == "statements":
    data = response_body.get("data") or {}
    if "sku_record" in data:
        rows_written = parse_statement_transaction_response(...)
    else:
        rows_written = parse_statement_list_response(...)
```

按 `data.sku_record` 字段是否存在隐式路由——可读性差，容易误判（如果 list 响应意外含空 sku_record 字段呢？）。**建议**：改成 endpoint 路径判断（`/list/detail` vs `/transaction/detail`），或 Pydantic discriminator。

---

## D. 协议不清 / 契约 gap

### D1. **`after_sales` 不在 `VALID_DOMAINS`**

```python
# order_sync.py
VALID_DOMAINS = {"orders", "logistics", "statements"}
```

但 `parse_after_sales_response`（247 行）已存在 + 4 个 test 通过。chrome-plugins 端即使开发 `/return_refund/202309/cancellations/search` 拦截，也会因 dumps 端 400 `SCHEMA_INVALID` 被拒。

**修复路径**：proposal §3.1 Lane A `feat/after-sales-routing`——加 `after_sales` 进 `VALID_DOMAINS` + if/elif 链加分支。

### D2. ✅ **【已简化】`empty_response` 与 `parse_error` 边界**

> **2026-09-18 用户拍板**：rowsWritten 字段删除后，原来 "rowsWritten=0 怎么解释" 的语义问题自动消失。

**简化后规则**（不再依赖 rowsWritten 计数）：

- A. `body 存在且 list=[]`（合法业务空数据）→ 200 + `{code:0, message:"success", requestId, data:{}}`
- B. `body 存在且关键字段缺失`（如 `main_orders` 字段不存在）→ 422 + `MALFORMED_RESPONSE`
- C. `body is None`（插件抓取失败/超时）→ 422 + `EMPTY_RESPONSE_BODY`
- D. `parser 抛异常`（字段类型错/DB 错等）→ 422 + `PARSE_ERROR`

**关键**：HTTP code 是唯一成功/失败信号，4 字段 envelope 是统一的。

### D3. **`mainOrderId` 在 logistics 域是必填，但 Pydantic Optional**

contract §3 末："`mainOrderId` 在 `domain=logistics` 时**必填**"

但 `DumpBodyIn.mainOrderId` 字段定义（order_sync.py:144 附近）是 `str | None = None`，dumps 端在 `order_sync.py:386` 才检查 `if not main_order_id`。**契约说"必填"，代码说"optional + 解析时检查"**——契约和实现不完全一致。

### D4. **`statementId` + `statementVersion` "强烈建议填" vs "不强校验"**

contract §3 末："`statementId` + `statementVersion` 在 `domain=statements` 时**强烈建议填**（`has-data` 用作幂等键），但 dumps 端**不强校验**"

**疑点**：`has-data` 端点的 `(shop_id, statement_id, statement_version)` 唯一性——`has-data` 端点用 `id` + `versions` 参数（参 §2.1）作幂等键；如果 dumps 不强制填这两个字段，`has-data` 端点可能查不到正确结果。

### D5. **`responseReadError` 诊断字段**（chrome-plugins 端设计）

chrome-plugins 仓修复方案："`payload==null` 不上传 + 带 `responseReadError` 诊断"

**问题**：tts-erp dumps 端的 Pydantic schema 没有 `responseReadError` 字段，跨仓 schema 升级时是否同步加？

### D6. **`code` 字段类型不一致**（已拍板）

200 是 `int: 0`，非 2xx 是 `str: "MALFORMED_JSON"`。

**已决定**（A3 拍板时确认）：保留现状，`code` 类型不一致是有意设计。但**没文档化**这个意图——proposal / AGENTS.md 已加注释。

---

## E. dumps 逻辑错 / 不合理

### E1. `rowsWritten=0` 当 parse-error 探测 hack

详见 A5 + C4。

### E2. `data.status` 隐式失败信号违反严格语义

```json
// 当前 dumps 200 响应
{
  "code": 0,
  "requestId": "...",
  "data": {
    "status": "parse_error",  // ← 200 + parse_error 双重语义
    "logId": 0,
    "rowsWritten": 0,
    "parseError": "..."
  }
}
```

**严格语义下**：200 意味着成功，不应该再带 `status: "parse_error"` 隐式失败。修复后应纯 4 字段 envelope。

### E3. `parse_statement_list_response` 隐式分流（详见 C7）

### E4. `sess.begin_nested()` savepoint 隔离（详见 C6）

### E5. `_request_id` header 信任边界

```python
# order_sync.py:_request_id
def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]  # ← 截断到 128 char
    return f"req-{uuid.uuid4()}"
```

- 接受客户端传 `x-request-id` header（chrome-plugins 可以传 UUID 用于链路追踪）
- 截断到 128 char
- **疑点**：是否要校验格式（仅允许 UUID 格式）？恶意客户端能否注入日志注入？
- 当前没有安全影响（request_id 仅在日志/响应里），但**是否应该 bound**）

### E6. `dump.mainOrderId` 缺失时返 `parse_error` 但仍走 200

```python
# order_sync.py:386
elif domain == "logistics":
    if not main_order_id:
        parse_error = "mainOrderId is required for logistics domain"
```

**严格语义下**：`mainOrderId` 缺失是协议层错误（必填字段缺失），应返 422 `MISSING_MAIN_ORDER_ID`，而不是 `parse_error` 200。

### E7. `_audit_and_error` + `_error_response` 调用不一致

`_audit_and_error` 接受 `key_prefix` 等参数做 audit 日志 + 返 `_error_response`，但有些 4xx 路径直接 return `_error_response` 不走 `_audit_and_error`（参 `_request_id` 附近的纯 401/403）。**audit 日志覆盖率不均**——但非功能性问题。

---

## F. 死代码 / 无用代码 / 待清理

| # | 项 | 位置 | 删除原因 |
| --- | --- | --- | --- |
| **F1** | response payload `rowsWritten` 字段 | `order_sync.py:362, 439, 460`（empty / parse_error / inserted 三处） | chrome-plugins 从不读（A4） |
| **F2** | response payload `logId` 字段 | `order_sync.py:362, 439, 460` | Phase 3 已 drop `plugin.raw_log`，logId 恒 0（Phase 3 残留） |
| **F3** | response payload `data.status` 字段 | `order_sync.py:362, 439, 460`（"empty_response" / "parse_error" / "inserted"） | 严格语义下隐式失败信号作废；可能完全删除 `data.status` 字段（B1 待定） |
| **F4** | `if parse_error is None and rows_written == 0` hack | `order_sync.py:415-416` | A5 拍板删除 |
| **F5** | `parse_after_sales_response` 247 行孤儿函数 | `parser.py:478` + 4 个 test | routes 未接（D1）；接入后是活函数，不删 |
| **F6** | `plugin.raw_log` 表 | 已 DROP（Phase 3） | 已删 |
| **F7** | `plugin.raw_log` 残留引用 | alembic 0030 注释 `log_id BIGINT NOT NULL REFERENCES plugin.raw_log(id)`；`schema_tts_erp.sql` 残留注释；canonical 文档 §2.4 / §2.5；`scripts/oneoff_backfill_plugin_order_times.py` | Phase 3 后清理不彻底 |
<<<<<<< Updated upstream
| ~~**F8**~~ | ~~`intercept-plugin-canonical.md`~~ | ~~`tech-doc/intercept-plugin-canonical.md`（21KB）~~ | ~~用户原话"看着不太对"；与 `dumps-data-contract.md` 职责重叠（A6）；建议加 banner（B5）~~ — **已合并并删除**（lane `docs/merge-canonical-into-contract`） |
=======
| **F8** | `intercept-plugin-canonical.md` | `tech-doc/intercept-plugin-canonical.md`（21KB） | 用户原话"看着不太对"；与 `dumps-data-contract.md` 职责重叠（A6）；建议加 banner（B5） |
>>>>>>> Stashed changes
| **F9** | `chrome-ext-order-sync-design.md` raw_log 设计章节 | §3.x 整段 | raw_log 已 drop，设计稿章节过时 |
| **F10** | `test_dumps_empty_response_returns_clean_status` 等 4 个 case | `tests/api/test_order_sync_contract.py:493` 等 | 断言反转后改名（如 `_returns_422`）；删所有 rowsWritten 断言 |
| **F11** | `tts_erp_v2/api/v2/order_sync.py:353-355` 注释 "（Phase 1 起 raw_log 不再写...）" | `order_sync.py` | Phase 3 后 raw_log 整个 drop，注释更新 |
| **F12** | `tts_erp_v2/api/v2/admin.py::_PLUGIN_ORDER_RAW_LOG` 常量 + `list_known_shops` 的 raw_log SELECT | `admin.py`（Phase 3 commit 已删但需验证） | raw_log drop 后整个块删 |
| **F13** | `tts_erp_v2/db/models/plugin.py::RawLog` 类 + 8 处 `log_id` 字段 | `plugin.py` | Phase 3 commit 已删但需验证 |
| **F14** | `scripts/oneoff_backfill_plugin_order_times.py` | `scripts/` | 历史 raw_log 已不在，脚本无意义 |

---

## G. 待修复 + 待观察清单（按优先级）

### G1. 【P0，方案 Lane E】

`order_sync.py` 严格 HTTP 语义落地：

- `empty_response` 200 → 422 + `EMPTY_RESPONSE_BODY`
- `parse_error` 200 → 422 + `PARSE_ERROR`
- `rowsWritten=0` 探测 hack 删
- `rowsWritten` / `logId` / `data.status` 字段全删
- `_ok_response` 加 `message="success"`
- `mainOrderId` 缺失（logistics 域）→ 422 `MISSING_MAIN_ORDER_ID`（E6）
- 4 个 envelope 一致性测试

### G2. 【P0，方案 Lane A】

`after_sales` 域接入：

- `VALID_DOMAINS` 加 `after_sales`
- if/elif 链加 `elif domain == "after_sales"`
- Pydantic `DumpBodyIn.domain` 改 `Literal[...]` 强校验（参考 `analytics.py` v4 风格）
- 2 个测试 case

### G3. 【P0，方案 Lane B + 跨仓】

物流 empty response 修复：

- **本仓**：empty_response 改 422（G1 已覆盖）
- **跨仓**（chrome-plugins 仓）：`payload == null` 不上传 + `responseReadError` 诊断 + 升级 `recordOrderProgress` 逻辑严格看 HTTP code

### G4. 【P0，方案 Lane C】

结算域诊断：

- dumps 端加 health counter（写到 `plugin.plugin_logs`）—— 维度：domain / shop_id / endpoint / rows_written / parse_error_class / captured_at / server_received_at
- 24h 监控定位 4 个候选根因哪个真触发

### G5. 【P1，方案 Lane F】

Pydantic validator 校验对齐：

- `protocolVersion` server 改必填（与 plugin 对齐）
- `createdAt` 时区 validator 已有，不动
- 2 个测试 case

### G6. 【P2，方案 Lane G】

文档分层：
<<<<<<< Updated upstream
- ~~`intercept-plugin-canonical.md` 加顶部 banner（B5 待定）~~ — **已通过 lane `docs/merge-canonical-into-contract` 解决**
=======

- `intercept-plugin-canonical.md` 加顶部 banner（B5 待定）
>>>>>>> Stashed changes
- `chrome-ext-order-sync-design.md` §3.x 加 raw_log 已 drop banner
- `dumps-data-contract.md` §0 加交叉链接
- `schema_tts_erp.sql` + `alembic/0030` 注释清理（F11）
- `tts_erp_v2/api/v2/admin.py` + `tts_erp_v2/db/models/plugin.py` raw_log 残留清理（F12 + F13）

### G7. ~~【P2，方案 Lane D（条件性）】~~ — **A13 已取消**

~~结算旁路回填：~~ — **不需要**：chrome-plugins 修复 dumps 流后自然增量补齐；148 条 `plugin.intercepted_requests` 数据 stale（1-2 周），回填旧数据风险 > 价值。

> Lane D（`feat/statements-backfill`）整体从方案移除。Lane C（诊断）仍保留 —— 148 条 intercepted_requests 仍有诊断价值（看 dumps 链路是 chrome 没传 / 传了被吞 / parser 挂）。

### G8. 【P1 观察项】

<<<<<<< Updated upstream
~~`intercept-plugin-canonical.md` 弃用机制：~~ — 已通过 lane `docs/merge-canonical-into-contract` 解决（合并后删除）
=======
`intercept-plugin-canonical.md` 弃用机制：

>>>>>>> Stashed changes
- 当前 AGENTS.md §5 端点速查、§9 业务信息索引都引用它
- 一旦 dumps-data-contract.md 接手，canonical 文档应显式标注 "已 superseded by dumps-data-contract.md"
- 用户原话"看着不太对"是这个意图

---

## H. 关联文档 / 数据源

- **现状契约**（single-source-of-truth）：[`tech-doc/dumps-data-contract.md`](dumps-data-contract.md)
- **设计稿**（部分落后）：[`tech-doc/chrome-ext-order-sync-design.md`](chrome-ext-order-sync-design.md)
- ~~**概念拍板稿**（用户原话"不太对"）：[`tech-doc/intercept-plugin-canonical.md`](intercept-plugin-canonical.md)~~ — 已合并删除
- **API catalog**（53+ endpoint 全清单）：[`tech-doc/tiktok-seller-center-api-catalog.md`](tiktok-seller-center-api-catalog.md)
- **订单业务规则**：`tech-doc/order-domain-business-rules.md`
- **AGENTS.md §2.5**（新）：严格 HTTP 语义铁律
- **proposal 主文档**：`tech-doc/dumps-tts-erp-refactor-proposal.md`

---

> **本文件状态**：review 汇总，待用户 review 后逐项打勾 / 修订
> **更新机制**：每个 lane 完成后在本文件对应 G 段加 commit 链接 + 实际工期复盘
