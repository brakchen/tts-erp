# tech-doc & README 审计报告

> 审计时间：2026-09-12
> 审计范围：tech-doc/ 目录所有 .md 文件 + README.md
> 审计目标：检查文档与代码事实是否相符，判断是否需要归档或修改

## 1. 审计结果摘要

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| **需要修改** | 2 | schema/table 数量错误、中间件顺序描述错误 |
| **需要归档** | 0 | 所有文档都有实际用途 |
| **内容准确** | 20+ | 大部分文档与代码事实相符 |

## 2. 需要修改的问题

### 2.1 Schema/Table 数量错误（Critical）

**问题描述**：

- **AGENTS.md** 说："11 schema / 51 表 + 1 view"
- **README.md** 说："10 schema / 37 表 + 2 view"
- **实际代码**（schema_tts_erp.sql）：11 schemas, 54 tables, 1 view

**修复方案**：

- 更新 AGENTS.md 和 README.md 中的 schema/table 数量为实际值

### 2.2 中间件顺序描述错误（Critical）

**问题描述**：

- **AGENTS.md** 说："RateLimit 最内 → Auth → CORS → AccessLog 最外；Auth 必须在 RateLimit 之前才能按 key 分桶"
- **app.py** 实际代码说：
  - registration order = RateLimit → Auth → CORS → AccessLog
  - resulting order = AccessLog → CORS → Auth → RateLimit
- **实际顺序**：AccessLog 是最外层，不是 Auth

**修复方案**：

- 更新 AGENTS.md 中的中间件顺序描述为实际顺序

## 3. 文档分类评估

### 3.1 新增的 tech-doc 文件（刚刚创建）

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `architecture-overview.md` | ✅ 保留 | 从 AGENTS.md 拆分的架构概览，内容准确 |
| `commands-reference.md` | ✅ 保留 | 从 AGENTS.md 拆分的命令参考，内容准确 |
| `tiktok-hmac-signing.md` | ✅ 保留 | 从 AGENTS.md 拆分的签名规范，内容准确 |
| `common-bugs.md` | ✅ 保留 | 从 AGENTS.md 拆分的常见 bug，内容准确 |
| `process-architecture.md` | ✅ 保留 | 从 AGENTS.md 拆分的进程架构，内容准确 |
| `miaoshou-platform.md` | ✅ 保留 | 从 AGENTS.md 拆分的妙手平台，内容准确 |

### 3.2 已有的 tech-doc 文件

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `external-api.md` | ✅ 保留 | 完整端点契约，内容准确且详细 |
| `api-key-auth-design.md` | ✅ 保留 | 有 "As-built 差异" 章节，记录了 v1→v2 的变化 |
| `browser-login-design.md` | ✅ 保留 | 浏览器登录设计，内容准确 |
| `test-domains.md` | ✅ 保留 | 测试域划分，内容准确 |
| `fx-agent-handbook.md` | ✅ 保留 | 汇率查询手册，内容准确 |
| `fx-exchange-rates.md` | ✅ 保留 | 汇率存储/同步设计，内容准确 |
| `data-model-target-v3.md` | ✅ 保留 | 数据模型设计文档，内容准确 |
| `chrome-ext-order-sync-design.md` | ✅ 保留 | Chrome 扩展订单同步设计，内容准确 |
| `order-domain-business-rules.md` | ✅ 保留 | 订单域业务规则，内容准确 |
| `pg-backup-design.md` | ✅ 保留 | PG 备份设计，内容准确 |
| `procurement-source-price-lookup.md` | ✅ 保留 | 采购源价格查询，内容准确 |
| `procurement-ui-redesign.md` | ✅ 保留 | 采购 UI 重设计，内容准确 |
| `refactor-tech-plan-v2.md` | ✅ 保留 | 重构技术方案，内容准确 |
| `tiktok-seller-center-api-catalog.md` | ✅ 保留 | TikTok Seller Center API 目录，内容准确 |

### 3.3 子目录文档

| 目录 | 状态 | 说明 |
| --- | --- | --- |
| `adr/` | ✅ 保留 | 架构决策记录，有 3 个 ADR |
| `analytics/` | ✅ 保留 | 分析相关文档，有 2 个文件 |
| `api/` | ✅ 保留 | API 相关文档，有 4 个文件 |
| `_archive/` | ✅ 保留 | 归档区，包含 v1 时代文档 |

### 3.4 README.md

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `README.md` | ⚠️ 修改 | schema/table 数量需要更新 |

## 4. 详细修复方案

### 4.1 修复 AGENTS.md 中的 schema/table 数量

**当前位置**：§1 Stack（项目栈）

**修改内容**：

```
# 原文
Python 3.14 · FastAPI + uvicorn（`:9877`）· SQLAlchemy 2 + psycopg3 · PostgreSQL 容器（`:5432`，11 schema / 51 表 + 1 view）

# 修改为
Python 3.14 · FastAPI + uvicorn（`:9877`）· SQLAlchemy 2 + psycopg3 · PostgreSQL 容器（`:5432`，11 schema / 54 表 + 1 view）
```

### 4.2 修复 AGENTS.md 中的中间件顺序描述

**当前位置**：§6 Boundaries（不要碰）

**修改内容**：

```
# 原文
- ❌ 不要改 `tts_erp_v2/app.py` 中间件顺序（RateLimit 最内 → Auth → CORS → AccessLog 最外；Auth 必须在
  RateLimit 之前才能按 key 分桶）

# 修改为
- ❌ 不要改 `tts_erp_v2/app.py` 中间件顺序（注册顺序：RateLimit → Auth → CORS → AccessLog；
  实际顺序：AccessLog 最外 → CORS → Auth → RateLimit 最内；Auth 必须在 RateLimit 之前才能按 key 分桶）
```

### 4.3 修复 README.md 中的 schema/table 数量

**当前位置**：架构章节

**修改内容**：

```
# 原文
数据模型（10 schema / 37 表 + 2 view）

# 修改为
数据模型（11 schema / 54 表 + 1 view）
```

## 5. 建议的后续行动

1. **立即修复**：更新 AGENTS.md 和 README.md 中的错误数据
2. **定期审计**：建议每季度检查一次文档与代码的一致性
3. **自动化检查**：考虑添加 CI 检查，自动验证 schema/table 数量

## 6. 结论

tech-doc/ 目录整体结构良好，大部分文档与代码事实相符。只有 2 处关键错误需要修复：

1. schema/table 数量过时
2. 中间件顺序描述错误

修复后，文档体系将保持准确和一致。
