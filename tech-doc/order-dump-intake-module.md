# Order dump intake deep module 技术方案

> 状态：**已实现，待用户 review；不得 merge master**。
>
> 本方案对应架构评审 Candidate 01「收拢订单 dump 解释与落库」。根据用户授权，
> 本轮 grilling 的每个问题均默认采用本文的推荐答案，不等待逐项回复。问题、建议、
> 理由和最终决策完整记录在 §3。
>
> 现行 wire 契约仍以 [`dumps-data-contract.md`](dumps-data-contract.md) 为准；本方案只重画
> module / interface / implementation / seam，不改变 Chrome 插件协议。

## 1. 问题与目标

当前 `POST /v2/order-sync/dumps` 同时负责：

1. HTTP body 大小、JSON 与 Pydantic wire 校验；
2. dump domain 解释和前置条件；
3. 六类 payload 的 parser dispatch；
4. PostgreSQL savepoint、业务 upsert、health 写入和 commit；
5. domain 失败到 HTTP error envelope 的映射。

同时，`parser.py` 的 interface 暴露 `Session`、repository upsert 和写入顺序。结果是一个
业务变化会跨越 HTTP adapter、parser、repository、审计日志和两套测试，locality 与
leverage 都很低。

目标是建立一个更深的 **order dump intake module**：

- interface 只表达「接收一个已通过 wire 校验的 dump，并给出 intake outcome」；
- implementation 隐藏 domain dispatch、payload interpretation、写入顺序、savepoint、
  health outcome 与 commit；
- HTTP adapter 只保留 transport concern 和 outcome → HTTP 映射；
- PostgreSQL 继续直接作为 local-substitutable dependency，不制造假想 repository port；
- 保持现有 URL、JSON schema、HTTP status、error code 和业务表结构。

## 2. module 边界

### 2.1 Public interface

```python
intake_dump(
    session: Session,
    *,
    request: DumpIntakeRequest,
) -> DumpIntakeOutcome
```

```python
@dataclass(frozen=True, slots=True)
class DumpIntakeRequest:
    domain: DumpDomain
    shop_id: str
    endpoint: str
    captured_at: datetime
    response_body: Mapping[str, object] | None
    main_order_id: str | None = None

@dataclass(frozen=True, slots=True)
class DumpIntakeOutcome:
    status: IntakeStatus              # accepted / rejected
    rows_written: int                 # diagnostic only
    failure: IntakeFailure | None     # EMPTY_RESPONSE_BODY / PARSE_ERROR
```

### 2.2 Adapter seam

`tts_erp_v2.api.v2.order_sync.post_dumps` 继续拥有：

- 2 MB body size gate；
- JSON decode；
- Pydantic wire schema；
- requestId、audit log、HTTP response envelope；
- `DumpIntakeOutcome` 到 `200 / 422` 的映射。

adapter 不再 import parser 或 `record_dump_health`，也不再知道 statement list/detail 的
payload shape。

### 2.3 Hidden implementation

建议结构：

```text
tts_erp_v2/plugin/orders/intake/
├── __init__.py       # public types + intake_dump
└── _service.py       # dispatch / savepoint / health / commit
```

本轮不为每个 parser 或 upsert 再造 public interface。现有 `parser.py` 与
`repository.py` 作为 implementation dependency 保留；HTTP 与新调用者不得越过 package
interface。后续若移动物理文件，只能是 locality 优化，不得改变 public seam。

## 3. 自动 Grilling 决策记录

| # | 决策问题 | 推荐答案 | 理由 | 决策 |
|---|---|---|---|---|
| G1 | module 是否同时吞并 `has-data` 和 `reconcile`？ | **否，只负责 dumps intake。** | 两者是 progress/diagnostic query，不参与 dump 原子写入；合并会降低 interface coherence。 | 默认同意 |
| G2 | 广告 v4 dumps 是否并入？ | **否。** | `analytics-sync-v2` 的 schema、幂等键和 source-of-truth 完全不同；现行契约明确禁止合并。 | 默认同意 |
| G3 | wire Pydantic model 是否移入 deep module？ | **否，留在 HTTP adapter。** | JSON alias、body size 和 schema error 是 transport concern；module 接受已验证语义值。 | 默认同意 |
| G4 | public interface 是否接收巨大原始 HTTP request？ | **否，使用窄的 `DumpIntakeRequest`。** | 隐藏 FastAPI/Pydantic，减少 seam 面积；仍保留 payload body 供 domain interpretation。 | 默认同意 |
| G5 | module 是否返回 HTTP status / JSONResponse？ | **否，返回 typed outcome。** | HTTP 是 adapter；domain outcome 应能被测试和未来非 HTTP caller 复用。 | 默认同意 |
| G6 | `rows_written` 是否仍是成功信号？ | **否，只作 health diagnostic。** | 现行契约规定 HTTP status 是唯一成功信号；空 list 合法成功。 | 默认同意 |
| G7 | `response.body is None` 在哪里处理？ | **module 内处理并记录 health。** | 它是 intake 语义失败，不是 JSON/Pydantic 失败；所有 caller 应得到同一 outcome。 | 默认同意 |
| G8 | logistics / order_history 缺 `mainOrderId` 在哪里处理？ | **module 内处理。** | 这是 domain 前置条件，不应泄漏到 adapter。 | 默认同意 |
| G9 | statements list/detail 如何分流？ | **implementation 按 validated payload shape 内部分流。** | caller 不应知道 `sku_record` 判别规则。未知 shape 由 parser 产生 PARSE_ERROR。 | 默认同意 |
| G10 | 是否暴露 parser registry 供动态插件注册？ | **否，内部静态 dispatch。** | domain 集合是协议契约，动态扩展只会制造浅 seam 和不可审计行为。 | 默认同意 |
| G11 | transaction 归谁？ | **deep module。** | 原子性是核心行为；adapter 不应知道 savepoint、health 与 commit 顺序。 | 默认同意 |
| G12 | parse 失败时 health 是否与业务行一起回滚？ | **业务行回滚，失败 health 单独持久化。** | 既不能留下半个 dump，又必须保留可诊断证据；保持现行行为。 | 默认同意 |
| G13 | 成功时 health 与业务行是否同一 commit？ | **是。** | 防止 200 已返回但 health/业务事实只有一边持久化。 | 默认同意 |
| G14 | 数据库异常是否继续伪装成 422 PARSE_ERROR？ | **否；SQLAlchemy/DB 异常向上抛出并走 5xx。** | 422 是不可重试 payload 问题；数据库故障应是 retryable 5xx。 | 默认同意 |
| G15 | parser 的普通解释异常如何表达？ | **module 捕获非 DB 异常，形成 `PARSE_ERROR` outcome。** | 保持现有客户端契约，并让 savepoint 回滚。异常详情需经既有 error sanitizer。 | 默认同意 |
| G16 | public interface 是否隐藏 `Session`？ | **不隐藏。** | PostgreSQL 是 local-substitutable dependency；当前只有一个真实 adapter，不建立假 repository。module 隐藏的是写入顺序而非数据库存在。 | 默认同意 |
| G17 | 是否立即删除 parser/repository 单元测试？ | **否。新增 module 行为测试，并保留纯 interpretation/upsert 测试。** | deep module 测试锁定外部行为；内部测试仍能快速定位复杂 payload regression。 | 默认同意 |
| G18 | 是否重命名稳定 URL 或 response envelope？ | **否。** | Chrome 插件依赖 `/v2/order-sync/dumps`；本轮只移动 seam，不做协议迁移。 | 默认同意 |
| G19 | 是否改变 ADR-0001 时间语义或 ADR-0003 order 命名/头行结构？ | **否。** | 明确 guardrail；架构加深不是领域重命名。 | 默认同意 |
| G20 | 何时允许开发？ | **本表决策完成、接口和测试护栏明确后立即开发。** | 用户已授权默认同意推荐；无需额外等待。 | 默认同意 |

## 4. 事务与 outcome 状态机

```text
validated DumpIntakeRequest
  ├─ body is None
  │    └─ write failed health → commit → rejected(EMPTY_RESPONSE_BODY)
  └─ body exists
       └─ SAVEPOINT
            ├─ handler success
            │    └─ write success health → commit → accepted(rows_written)
            ├─ interpretation/domain exception
            │    └─ rollback SAVEPOINT → write failed health → commit
            │       → rejected(PARSE_ERROR)
            └─ SQLAlchemy/database exception
                 └─ rollback request transaction → propagate → HTTP 5xx
```

### Invariants

1. `accepted` 表示 contracted persistence 已完成；`rows_written == 0` 仍可 accepted。
2. `rejected` 不得留下本 dump 的部分业务写入。
3. 每个 accepted/rejected semantic outcome 恰有一个 `plugin.plugin_logs` health 记录。
4. module 每次调用最多 commit 一次。
5. adapter 不得自行 `commit()` 或 `begin_nested()`。

## 5. Domain dispatch

| `DumpDomain` | hidden handler | 前置条件 |
|---|---|---|
| `orders` | `parse_order_response` | 无额外字段 |
| `order_details` | `parse_order_detail_response` | 无额外字段 |
| `order_history` | `parse_order_history_response` | `main_order_id` 必填 |
| `logistics` | `parse_logistics_response` | `main_order_id` 必填 |
| `statements` | list/detail shape classifier | `sku_record` → transaction detail，否则 statement list |
| `after_sales` | `parse_after_sales_response` | 无额外字段 |

`DumpDomain` 与 Pydantic validator 使用同一组枚举值，避免字符串集合在 adapter 与 module
重复漂移。

## 6. Migration plan

1. 新建 `plugin.orders.intake` package、typed request/outcome 和 transaction service。
2. 将 domain dispatch、body-null、savepoint、health 与 commit 从 `post_dumps` 移入 module。
3. `post_dumps` 只构造 request、调用 module、映射 outcome。
4. 将 API atomicity monkeypatch 从 HTTP module 的 parser symbol 改到 intake implementation seam。
5. 新增 module behavior tests，覆盖 accepted、empty body、缺 mainOrderId、partial write rollback、
   DB exception propagation、statement shape dispatch。
6. 更新现行契约文档中的代码位置与 module 边界。

兼容策略：不删除 `parser.py` / `repository.py` 的现有函数；它们降为 implementation，避免
一次机械搬移造成两千行 diff。删除测试证明的是**调用 seam**：删除 HTTP → parser 直接依赖后，
只有 intake module 需要知道 dispatch/transaction/health 顺序。

## 7. Test guardrails

### Narrow tests

```bash
flock -n /tmp/tts-erp-test.lock \
  bash scripts/test.sh fast \
  tests/api/test_order_sync_contract.py \
  tests/plugin/orders/test_intake.py \
  tests/plugin/orders/test_parser.py \
  tests/plugin/orders/test_parser_after_sales.py
```

必须断言：

- HTTP wire status/envelope 完全不变；
- 所有六类 domain dispatch 正确；
- partial business write 在 parse failure 后为 0；
- failure health 仍持久化；
- DB exception 不被降级为 422；
- empty list 仍是 200；
- idempotent replay 仍是 200；
- adapter 不再 import parser/repository transaction primitive。

### Completion

- 运行 `bash scripts/test.sh fast` 并与当前 master stable baseline 比较；零新增稳定失败。
- LSP / diff check 为零。
- reviewer + fixer 闭环完成。
- 只提交并推送 lane branch；**未经用户 review 不 merge master**。

## 8. Out of scope

- Chrome 插件端采集、重试或 alarm 行为；
- 售后真实 payload 字段补全；
- 结算 0 行与物流空 body 的上游 root cause；
- ad v4 dumps；
- database schema / migration；
- `has-data` / `reconcile` 深化；
- ADR-0001 / ADR-0003 变更。
