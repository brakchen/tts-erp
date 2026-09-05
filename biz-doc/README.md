# biz-doc — 业务语义文档

> 业务字段含义、跨表/跨系统的映射、单位约定等**业务侧**的文档。
> 与 `tech-doc/`(设计文档 / 端点契约)区分:`tech-doc/` 说系统怎么实现,
> `biz-doc/` 说字段在业务上代表什么。

## 1. 维护原则

- **业务语义单点定义**:同一个字段(如 `onsite_roi2_shopping_sku`)在多张表 / 多个端点出现时,
  在 `biz-doc/` 里只定义一次,其他文档引用它。
- **来源标记**:每个字段语义必须标注来源(`user spec` / `TikTok OEC 文档` / `dump 推断`),
  未验证的标 ⚠️。
- **变更追踪**:业务语义变更时,在 commit message 写 `biz:` 前缀,与代码改动同步。

## 2. 目录结构

```text
biz-doc/
├── README.md                    ← 本文件
└── analytics/
    ├── post-product-list-field-semantics.md    ← post_product_list 字段语义
    ├── endpoint-join-keys.md                   ← 端点间关联键(JOIN 模板)
    ├── post-session-list-field-semantics.md    ← (待补)
    └── campaign-opt-log-list-field-semantics.md ← (待补)
```

## 3. 与其他文档的关系

| 文档 | 关注点 |
| --- | --- |
| `tech-doc/` | 系统设计、端点契约、schema 演进、迁移方案 |
| `biz-doc/` | 字段在业务上代表什么、跨表语义映射、单位约定 |
| `setup/` | 用户向 setup 文档 |
| `CHANGELOG.md` | 版本变更 |
| `AGENTS.md` | agent 操作指南(本仓库专属) |

## 4. 索引与检索

业务文档通过 `ctx_index` 索引后,任何 session 都可以通过 `ctx_search` 检索:

```bash
ctx_search(queries=["onsite_roi2_shopping_sku 含义"], source="biz-doc")
```
