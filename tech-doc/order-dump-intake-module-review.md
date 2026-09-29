# 订单转储接入深模块——审阅报告

> 审阅状态：**等待用户确认，尚未合并到 `master`**
>
> 分支：`redesign/order-dump-intake-module`
>
> 工作树：`.worktrees/order-dump-intake-module`
>
> 主要提交：
>
> - `b540a8b`：记录自动追问决策技术方案
> - `15c4ac2`：实现订单转储接入深模块
> - `ff6b2d9`：添加本审阅报告

## 1. 审阅结论摘要

架构评审候选问题 01「收拢订单转储解释与落库」已按自动追问决策完成实现。

新的深模块公开接口为：

```python
intake_dump(
    session: Session,
    *,
    request: DumpIntakeRequest,
) -> DumpIntakeOutcome
```

HTTP 适配器不再拥有解析器分派、保存点、健康记录、事务提交或业务写入顺序。
稳定的 `/v2/order-sync/dumps` URL、线上 JSON 结构、HTTP 状态码和响应信封保持不变。

审查代理最终结论：**通过，可以进入合并审批阶段**。

## 2. 自动追问决策

完整问题、推荐答案、理由与默认同意结果见：

- [`order-dump-intake-module.md`](order-dump-intake-module.md) 第 3 节

共记录 20 个决策问题，主要结论如下：

- 模块只负责订单域转储接入，不吞并 `has-data` 和 `reconcile`；
- 不合并广告 v4 转储协议；
- Pydantic 校验、JSON 解码和请求体大小限制留在 HTTP 适配器；
- 类型化处理结果不包含 HTTP 概念；
- 深模块拥有保存点、健康记录和事务提交顺序；
- 解析失败时回滚业务写入，但保留失败健康记录；
- SQLAlchemy 或数据库失败必须回滚后继续向上传播，并映射为 `500 INTERNAL_ERROR`；
- PostgreSQL `Session` 直接作为本地可替换依赖，不制造虚假的仓储接口；
- 六个数据域使用内部静态分派，不提供动态插件注册表；
- 不改变 ADR-0001、ADR-0003、数据库结构或 Chrome 插件协议。

## 3. 变更清单

### 3.1 新增深模块

- `tts_erp_v2/plugin/orders/intake/__init__.py`
  - 唯一公开接缝及导出类型。
- `tts_erp_v2/plugin/orders/intake/_types.py`
  - `DumpDomain`
  - `DumpIntakeRequest`
  - `DumpIntakeOutcome`
  - `IntakeFailure`
- `tts_erp_v2/plugin/orders/intake/_service.py`
  - 六个数据域的分派；
  - 结算列表与结算明细的结构分流；
  - 数据域前置条件；
  - 保存点和部分写入回滚；
  - 成功与失败健康记录；
  - 提交与回滚所有权；
  - 解析错误信息清理。

### 3.2 HTTP 适配器

`tts_erp_v2/api/v2/order_sync.py`：

- 保留请求体大小、JSON、Pydantic、`requestId`、审计和 HTTP 响应信封；
- 构造 `DumpIntakeRequest` 并调用 `intake_dump`；
- 类型化拒绝结果映射为 `422`；
- SQLAlchemy 故障映射为 `500 INTERNAL_ERROR`；
- 删除对解析器、健康记录、保存点和事务提交的直接依赖。

### 3.3 解析器契约加强

`tts_erp_v2/plugin/orders/parser.py`：

- `order_details.data.main_order` 缺失或不是列表时返回解析错误；
- `order_history.data.order_history` 缺失或不是列表时返回解析错误；
- 显式空列表仍是合法的 `200` 响应；
- 订单详情记录缺少 `main_order_id` 时不再静默跳过。

### 3.4 文档

- `tech-doc/order-dump-intake-module.md`：自动追问技术方案；
- `tech-doc/dumps-data-contract.md`：更新为六数据域和深模块现行契约；
- `tts_erp_v2/plugin/orders/__init__.py`：声明公开接入接缝。

## 4. 审查与修复闭环

首轮审查提出以下问题：

1. 数据库故障没有覆盖分派、健康记录和提交三个路径的完整回滚；
2. 订单详情和订单历史缺少必需结构时可能错误返回 `200`；
3. 六数据域分派测试不足；
4. 现行契约仍只记录四个数据域；
5. 解析错误进入响应前没有折叠为单行并限制在 500 字符内。

以上问题已全部修复，并补充回归测试。复审结果如下：

- 事务回滚：通过；
- 六数据域分派及两种结算数据结构：通过；
- 订单详情和订单历史 HTTP 兼容性：通过；
- 解析错误信息清理：通过；
- HTTP 适配器接缝：通过；
- **未发现剩余问题，可以进入合并审批阶段。**

修复代理已完成文件修改，但其结果上报扩展在收尾阶段发生 JSON 解析错误。所有实际修改均已由
主代理逐项检查并运行测试，随后由审查代理复审通过。

## 5. 测试证据

### 5.1 单元层测试

```bash
timeout 180 bash scripts/test.sh unit tests/plugin/orders/test_intake.py
```

结果：**18 项通过**。

覆盖内容：

- 接受结果、健康记录和单次提交；
- 解析拒绝结果及失败健康记录；
- 空响应体；
- 分派、健康记录和提交三种数据库故障的回滚；
- 解析错误信息清理；
- 拒绝调用者已开启的事务；
- 六数据域分派；
- 结算列表和结算明细两种数据结构；
- 物流及订单历史的 `mainOrderId` 前置条件；
- HTTP 适配器不拥有解析器和事务接缝。

### 5.2 窄范围集成测试

```bash
flock -n /tmp/tts-erp-test.lock \
  bash scripts/test.sh fast \
  tests/api/test_order_sync_contract.py \
  tests/plugin/orders/test_intake.py \
  tests/plugin/orders/test_parser.py \
  tests/plugin/orders/test_parser_after_sales.py
```

结果：没有新增失败；仍存在以下 5 个 `master` 既有稳定失败：

- `test_has_data_logistics_returns_true_after_dump`
- `test_dumps_logistics_inserted`
- `test_reconcile_returns_order_anchors_and_terminal_logistics`
- `TestParseLogisticsResponse::test_multi_package`
- `test_parse_after_sales_missing_cancel_id_is_skipped`

### 5.3 快速测试集差异

在同一共享测试库锁下，分别运行当前 `master` 和本分支：

- `master`：27 个失败；
- 本分支：19 个失败；
- **新增失败：0**；
- 基线失败减少：8 个。这 8 个均为 OpenAPI 文档断言，不属于本开发分支的目标或承诺。

### 5.4 静态检查

- 相关 Python LSP：0 个诊断；
- `py_compile`：通过；
- `git diff --check`：通过。

## 6. 兼容性

以下契约保持不变：

- `POST /v2/order-sync/dumps`
- `protocolVersion: 1`
- 2 MB 请求体限制
- Pydantic 结构及字段别名
- 成功时的四字段响应信封
- `EMPTY_RESPONSE_BODY` 和 `PARSE_ERROR` 使用 `422`
- 空列表返回 `200`
- 幂等重放返回 `200`
- `plugin.plugin_logs` 健康记录行为
- 业务表结构和自然键

新增并明确的行为：

- SQLAlchemy 或数据库故障返回 `500 INTERNAL_ERROR`，不再错误伪装为不可重试的 `422`；
- 解析错误信息在进入健康记录和处理结果前折叠为单行，并限制在 500 字符内。

## 7. 剩余风险与范围外事项

- 物流多包裹解析器和售后记录缺少 `cancel_id` 的既有失败未在本分支修复；
- Chrome 插件采集、定时任务和重试策略未修改；
- 结算零行、物流空响应体的上游根因未处理；
- 解析器和仓储实现的物理文件仍然保留，本次只收拢调用接缝；后续机械搬移必须独立审阅；
- 未新增数据库迁移，未触碰生产数据。

## 8. 用户审阅清单

请重点确认：

1. 是否认可模块只负责转储接入，而 `has-data` 和 `reconcile` 保持独立；
2. 是否认可数据库故障从 `422` 改为正确的 `500 INTERNAL_ERROR`；
3. 是否认可本次先收拢公开接缝，暂不机械搬移两千余行解析器和仓储代码；
4. 是否认可六个数据域均属于服务端线上协议；
5. 是否批准后续合并到 `master`。

在用户明确批准前，本分支**不会合并到 `master`**。
