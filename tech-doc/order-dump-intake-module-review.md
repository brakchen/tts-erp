# Order dump intake deep module — Review 报告

> Review 状态：**等待用户确认，未 merge master**
>
> Branch：`redesign/order-dump-intake-module`
>
> Worktree：`.worktrees/order-dump-intake-module`
>
> Commits：`b540a8b`（自动 grilling 技术方案）、`15c4ac2`（初版实现；本报告与
> review 修复在后续 commit）

## 1. Review 结论摘要

架构评审 Candidate 01「收拢订单 dump 解释与落库」已按自动 grilling 决策实现。

新的 deep module：

```python
intake_dump(
    session: Session,
    *,
    request: DumpIntakeRequest,
) -> DumpIntakeOutcome
```

HTTP adapter 不再拥有 parser dispatch、savepoint、health、commit 或业务写入顺序。
稳定的 `/v2/order-sync/dumps` URL、wire schema、HTTP status 与 envelope 保持不变。

reviewer 最终结论：**PASS / Merge verdict OK**。

## 2. Grilling 决策

完整问题、推荐答案、理由与默认同意结果见：

- [`order-dump-intake-module.md`](order-dump-intake-module.md) §3

共记录 20 个决策问题，主要结论：

- module 只负责 order-domain dumps intake，不吞并 `has-data` / `reconcile`；
- ad v4 dumps 不合并；
- Pydantic/JSON/body-size 留在 HTTP adapter；
- typed outcome 不暴露 HTTP；
- deep module 拥有 savepoint、health 与 commit；
- parse failure 回滚业务写入但持久化 failure health；
- SQLAlchemy/数据库失败 rollback 后传播，并映射为 `500 INTERNAL_ERROR`；
- PostgreSQL `Session` 直接作为 local-substitutable dependency，不制造 repository port；
- 六个 domain 使用内部静态 dispatch，不提供动态插件 registry；
- 不改变 ADR-0001、ADR-0003、数据库 schema 或 Chrome 协议。

## 3. 变更清单

### 新 module

- `tts_erp_v2/plugin/orders/intake/__init__.py`
  - 唯一 public seam 与导出类型。
- `tts_erp_v2/plugin/orders/intake/_types.py`
  - `DumpDomain`
  - `DumpIntakeRequest`
  - `DumpIntakeOutcome`
  - `IntakeFailure`
- `tts_erp_v2/plugin/orders/intake/_service.py`
  - 六 domain dispatch；
  - statement list/detail shape 分流；
  - domain prerequisite；
  - savepoint 与 partial-write rollback；
  - success/failure health；
  - commit / rollback ownership；
  - parse-error sanitizer。

### HTTP adapter

`tts_erp_v2/api/v2/order_sync.py`：

- 保留 body size、JSON、Pydantic、requestId、audit 与 HTTP envelope；
- 构造 `DumpIntakeRequest` 并调用 `intake_dump`；
- typed rejected outcome → `422`；
- SQLAlchemy failure → `500 INTERNAL_ERROR`；
- 删除 parser、health、savepoint 与 commit 直接依赖。

### Parser contract strengthening

`tts_erp_v2/plugin/orders/parser.py`：

- `order_details.data.main_order` 缺失/非 list → parse error；
- `order_history.data.order_history` 缺失/非 list → parse error；
- 显式空 list 仍是合法 200；
- detail record 缺 `main_order_id` 不再静默跳过。

### 文档

- `tech-doc/order-dump-intake-module.md`：自动 grilling 技术方案。
- `tech-doc/dumps-data-contract.md`：更新为六 domain 与 deep module 现状契约。
- `tts_erp_v2/plugin/orders/__init__.py`：声明 public intake seam。

## 4. Review/Fix 闭环

首轮 reviewer 提出：

1. DB failure 未覆盖 dispatch/health/commit 全路径 rollback；
2. order details/history 缺必需结构可误返 200；
3. 六 domain dispatch 测试不足；
4. 活契约仍写四 domain；
5. parse error 未先做单行与 500 字符限制。

全部修复并补回归测试。复审结果：

- transaction rollback：通过；
- six-domain dispatch 与两种 statement shape：通过；
- order details/history HTTP compatibility：通过；
- parse error sanitizer：通过；
- adapter seam：通过；
- **No issues found / Merge verdict OK**。

fix agent 完成文件修改，但其结果上报 extension 在收尾阶段报 JSON 错误；修改内容已由主 agent
逐项检查、测试，并由 reviewer 复审通过。

## 5. Test evidence

### Unit layer

```bash
timeout 180 bash scripts/test.sh unit tests/plugin/orders/test_intake.py
```

结果：**18 passed**。

覆盖：

- accepted + health + one commit；
- parse rejection + failure health；
- null body；
- dispatch/health/commit 三种 DB failure rollback；
- parse sanitizer；
- caller-owned transaction 拒绝；
- 六 domain dispatch；
- statement list/detail 两种 shape；
- logistics/order_history mainOrderId prerequisite；
- HTTP adapter 不拥有 parser/transaction seam。

### Narrow integration selection

```bash
flock -n /tmp/tts-erp-test.lock \
  bash scripts/test.sh fast \
  tests/api/test_order_sync_contract.py \
  tests/plugin/orders/test_intake.py \
  tests/plugin/orders/test_parser.py \
  tests/plugin/orders/test_parser_after_sales.py
```

结果：没有新增失败；保留 5 个 master 既有稳定失败：

- `test_has_data_logistics_returns_true_after_dump`
- `test_dumps_logistics_inserted`
- `test_reconcile_returns_order_anchors_and_terminal_logistics`
- `TestParseLogisticsResponse::test_multi_package`
- `test_parse_after_sales_missing_cancel_id_is_skipped`

### Fast suite delta

同一共享测试库锁下分别运行当前 master 与 branch：

- master：27 failures
- branch：19 failures
- **new failures：0**
- removed baseline failures：8（OpenAPI 文档断言；不作为本 lane 的目标或承诺）

### Static checks

- 相关 Python LSP：0 diagnostics；
- `py_compile`：通过；
- `git diff --check`：通过。

## 6. Compatibility

保持不变：

- `POST /v2/order-sync/dumps`
- `protocolVersion: 1`
- 2 MB gate
- Pydantic schema 与 aliases
- success 4-field envelope
- `EMPTY_RESPONSE_BODY` / `PARSE_ERROR` 422
- empty list 200
- idempotent replay 200
- `plugin.plugin_logs` health 行为
- 业务表 schema 与自然键

新增并明确：

- SQLAlchemy/数据库故障返回 `500 INTERNAL_ERROR`，不再伪装为不可重试的 422；
- parse error message 在进入 health/outcome 前折叠为单行并限制 500 字符。

## 7. Residual risks / Out of scope

- 物流多包裹 parser 与 after-sales 缺 `cancel_id` 的既有失败未在本 lane 修复；
- Chrome 插件采集、alarm、重试策略未修改；
- 结算 0 行、物流空 body 上游 root cause 未处理；
- parser/repository 物理文件仍保留，当前只收拢调用 seam；后续机械搬移必须独立 review；
- 未新增 migration，未触碰生产数据。

## 8. 用户 Review 清单

请重点确认：

1. 是否认可 module 只负责 dumps intake，而 `has-data` / `reconcile` 保持独立；
2. 是否认可 DB failure 从 422 改为正确的 `500 INTERNAL_ERROR`；
3. 是否认可当前先收拢 public seam、暂不机械搬移 2,000+ 行 parser/repository；
4. 是否认可六 domain 都属于服务端 wire contract；
5. 是否批准后续 merge master。

在用户明确批准前，本 branch **不会 merge master**。
