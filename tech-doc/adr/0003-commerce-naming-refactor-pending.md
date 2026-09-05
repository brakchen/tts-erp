# ADR-0003: commerce 域命名重构请求（pending — 已记录，未实施）

- **Status**: **implemented + deployed 2026-09-05**（merge `6f70e73`；live 已应用 migration 0007；代码/schema/API 全部切到新命名；CHANGELOG 有记录）
- **Date**: 2026-09-05（§2.6 确认记录 = 同日二轮对话）
- **Deciders**: 用户（命名诉求方）+ tts-erp backend（评审）
- **原文出处**: 2026-09-05 对话（commerce 域结构讲解后的重构诉求）

> ⚠️ 本 ADR **只是记录**。仓库当前状态 = 命名维持现状（`channel_accounts` /
> `channel_products` / `channel_account_id` / `external_*` / `source_*_at`…）。
> ✅ 2026-09-05 用户拍板开工（D1-A/命名、D2=API 一起改、D3=order_pk）并已部署完成。本 ADR 由 proposed → implemented。

## 1. Context（背景）

讲解 commerce 域 5 表后，用户提出按**个人习惯思维**重构表/列命名，原始诉求分两类：

- **结构类**（3 条）：合并 `sales_order_lines` 进 `sales_orders`；`sales_orders` 唯一键
  改为 `shop_id+spu_id+sku_id+order_id`；快照数据"update 一次后不再变"故可弱化/简化。
- **命名类**（用户坚持保留）：表名与列名按"业务直觉"重命名。

结构类经评审以**线上数据逐条论证不可行**（见 §3），用户回复"我理解了你的设计和问题"，
表示接受；但明确 **"命名我需要重构，按照我的习惯思维进行修改"**。本文档将两类诉求
**原样存档**（防止丢失），标注各自状态，供实施时点决策。

## 2. 诉求原文（用户原话的命名映射）

### 2.1 `commerce.channel_accounts`（店铺账号）

| 现状 | 用户期望 | 类别 |
| --- | --- | --- |
| 表名 `channel_accounts` | `shops` | 命名（绿桶待实施） |
| 列 `external_account_id` | `shop_id` | 命名（✅ 已确认 §2.6） |
| 列 `source_updated_at` | 统一为 `update_at`（"更新时间命名应该统一"） | 命名（open，见 §5.2） |
| — | 其余未提 | — |

### 2.2 `commerce.channel_products`（TikTok SPU）

| 现状 | 用户期望 | 类别 |
| --- | --- | --- |
| 表名 `channel_products` | `products_spu` | 命名（绿桶待实施） |
| 外键列 `channel_account_id` | `shop_pk`（后续二轮改为 _pk 方案） | 命名（✅ §2.6） |
| 列 `external_product_id` | `spu_id` | 命名（✅ §2.6） |
| 列 `source_created_at` | `created_at` | 命名（open，见 §5.2） |
| 列 `source_updated_at` | `update_at` | 命名（open，见 §5.2） |

### 2.3 `commerce.channel_product_variants`（TikTok SKU）

| 现状 | 用户期望 | 类别 |
| --- | --- | --- |
| 表名 `channel_product_variants` | `products_sku` | 命名（绿桶待实施） |
| 外键列 `channel_product_id` | `spu_pk` | 命名（✅ §2.6） |
| 列 `external_variant_id` | `sku_id` | 命名（✅ §2.6） |
| 唯一键组成 `(spu, variant)` → `shop+spu+sku` | 键组成改动 | 结构（§3.1/§5.3：默认放弃） |

### 2.4 `commerce.sales_orders`（订单头）

| 现状 | 用户期望 | 类别 |
| --- | --- | --- |
| 外键列 `channel_account_id` | `shop_pk` | 命名（✅ §2.6） |
| 列 `external_order_id` | `order_id`（TikTok 单号文本） | 命名（✅ §2.6） |
| 唯一键组成 `(shop, order)` → `shop+spu+sku+order` | 键组成改动 | 结构（已论证不可行，见 §3.1） |

### 2.5 `commerce.sales_order_lines`（订单行）

| 现状 | 用户期望 | 类别 |
| --- | --- | --- |
| 表名 → `sales_order_details` | 表改名 | 命名（**用户已决定暂缓**，先不干） |
| 唯一键改 `shop+order+spu+sku` | 键组成改动 | 结构（已论证不可行：同单同 SKU 拆多行 11 单实证等，见 §3.1） |
| 独立行表 → 并进 `sales_orders` | 结构合并 | 结构（已论证不可行，见 §3.1） |
| 行内 FK `sales_order_id`/`channel_product_id`/`channel_product_variant_id` | `order_pk`/`spu_pk`/`sku_pk` | 命名（✅ §2.6，随 _pk 全库统一） |

### 2.6 命名基准线 — 用户拍板确认版（2026-09-05 二轮）

以下为用户对 §5 三问的拍板结果，**作为实施时的命名基准线**。仍未改代码。

**确认的命名规则（"一个词一个意思"）**：

- 指向某张表**主键**的外键列 = `<表业务名>_pk`（如 `shop_pk` 里装的就是
  `channel_accounts.id` 的 314 这类内部号）；
- 上游平台给的**业务文本 id** = `<业务名>_id`（`shop_id`/`order_id`/`spu_id`/`sku_id`
  = TikTok 的 19 位文本串，如 7494763368967603447）；
- 各表**自身主键列 = `id` 不动**。
- `_pk` 规则 **全库一次统一**（Q1 = 全库统一，非只改点名列）。

**范围 = 指向 commerce 4 张核心表主键的全部 FK 列（跨 8 个模型文件 + 1 个 storage SQL）：**

| 目标主键 | 新列名 | 涉及位置（现列名一律 `channel_account_id` / `sales_order_id` / `channel_product_id` / `channel_product_variant_id`） |
| --- | --- | --- |
| channel_accounts.id | `shop_pk` | channel_products、sales_orders、after_sales.cases、finance.payouts、linkage.account_links（5 处 ORM）+ procurement.spu_images（storage/schema_storage.sql，无 ORM 类） |
| sales_orders.id | `order_pk` | sales_order_lines、after_sales.cases、fulfillment.shipments、finance.settlement_transactions（4 处 ORM） |
| channel_products.id | `spu_pk` | channel_product_variants、sales_order_lines、procurement.manual_product_costs、reporting.product_cost_snapshots、reporting.product_profit_daily、linkage.product_links / link_overrides / link_issues（8 处 ORM）+ procurement.spu_images（storage SQL） |
| channel_product_variants.id | `sku_pk` | sales_order_lines、linkage.variant_links（2 处 ORM） |

**文本 id 列改名（确认）**：`external_account_id → shop_id`、`external_product_id → spu_id`、
`external_variant_id → sku_id`、`external_order_id → order_id`。本地 FK 已全部 `_pk` 化，
故 `shop_id` 等词全库只剩"上游文本 id"一个意思，二义消除。

**表改名（绿桶，待施工指令）**：`channel_accounts → shops`、`channel_products → products_spu`、
`channel_product_variants → products_sku`。`sales_order_lines → sales_order_details`
**用户已决定暂缓（先不干）**。

**API 路径歧义（Q3 拍板）**：`order_id` 在 DB 里改为 TikTok 单号文本后，承载**内部主键**
的 API 路径参数（如 `/v2/commerce/sales-orders/{order_id}`）存在歧义 → **路径参数改名**，
表达主键语义：用户指定 "primary_key" 语义（建议落地拼写与列规则一致为 `order_pk`，实施
定稿时确认）。注：FastAPI 路径参数名**不影响 HTTP URL**（只影响 OpenAPI 文档与生成的
客户端签名）；`channel-accounts/{account_id}`、`channel-products/{product_id}` 同理逐路由核对。

**本轮边界（不扩）**：指向 `sales_order_lines.id` 的 3 处 FK（after_sales.case_lines /
fulfillment.shipment_lines / finance.settlement_transactions 的 `sales_order_line_id`）
依赖行表最终命名，**顺延**；shipment / case / statement / credential / raw_record 等
**非 commerce 域** FK 的 `_pk` 化不在本轮（规则可扩展，另行立项）。

## 3. 评审注记（2026-09-05，附证据）

### 3.1 结构类 — 已论证不可行（用户已知晓，默认不再实施，除非用户改口）

- **行表并入订单头不可行**：订单是 1:N 明细（**实测** 867 单 / 904 行，30 单 ≥2 行，
  最大一单 4 行；真实存在一单含 **2 种不同 SPU**、各自独立 quantity/unit_price/行状态
  的订单，如 585723394695005871）。"一单 N 个不同商品各自价格数量"无法用固定列表达，
  用 json 数组又违反"不要 json"的诉求。表头/明细分离是关系模型标准结构。
- **`sales_orders` 唯一键含 spu/sku** 概念错位：订单**没有**单一 spu/sku（跨多 SPU）；
  且与"合并行表"自相矛盾——键若含 spu/sku，"订单"就退化成"订单×行"粒度。
- **行表唯一键改 (order, spu, sku) 不可行**：同单同 SKU 被 TikTok 拆成多条明细的
  **11 单 27 行实证**（如订单 585539574763652305 同一 spu+sku 拆 4 行、单价差 1 VND）；
  且 `channel_product_variant_id` 全表 **0 行非空**（679 行只有 SPU FK、225 行双空），
  键由本地 FK 组成 = 100% NULL 无法约束；用外部快照 id 组键同样 11 例重复。
- **快照列不可删/不可弱化**：904 行中 **224 行（25%）`channel_product_id` 为 NULL**
  （商品未同步/已下架），快照列是这批行的**唯一销售事实**；且快照列现状**已是独立列
  而非 json**（external_product_id/variant_id/名称/变体名/图，5 个 text 列）。
  快照不更新的原因不是"update 过一次"，而是**设计上写入后不回绑**
  （`NEVER auto-bind by title`，见模型 docstring）。
- **"行基本不变"不成立**：904 行里 **897 行 `updated_at > synced_at`**（落库后被后续
  同步改写）；`line_status` 实测 6 种流转值——行是活的（状态组随订单生命周期变），
  快照组是死的（购买时刻事实），两种性质恰是行表独立存在的理由。
- **行表有下游 3 域依赖**：`after_sales.case_lines`（277 行真实数据）、
  `finance.settlement_transactions`、`fulfillment.shipment_lines` 均 FK 指向
  `sales_order_lines.id`。合并/删除行表 = 断这三个域的分析挂点。

### 3.2 命名类 — 用户坚持，基准线见 §2.6（非本 ADR 批准，仅存档）

纯更名技术上可做；实施范围与顺序见 §5 剩余待决项。**现状保持不动**。

## 4. 相关事实更新（2026-09-05）

- **v1 遗留 `public.*` 已归档删除**（CHANGELOG 2026-09-05：19 张业务表 DROP +
  归档 `/home/schan/backups/tts_erp_public_v1_legacy_20260905T110814Z.sql.gz`）。
  实测 live + schema 文件均 0 张 public 表 → 早前"改名 `shops` 会撞 v1 遗留
  `public.shops`"的 blocker **已消除**。
- 保留：`public` schema 本体与 `public.fn_touch_updated_at()`（41 个 v2 表触发器依赖，
  `tests/db/test_time_fields_convention.py` 锁定）。oauth_receiver（独立 DB）未动。
- **变体/SKU 唯一性实测**：`external_variant_id` 跨商品**零重复**，
  `(channel_product_id, external_variant_id)` 已足以唯一（§5.3 依据）。
- **`spu_images` 无 ORM 类**：DDL 与索引定义在 `tts_erp_v2/storage/schema_storage.sql`
  （procurement schema），实施改名时需单独核对引用处。

## 5. 实施前待决项（open questions；✅ = 已拍板见 §2.6，剩余 open）

1. **✅ 已拍板（§2.6）`shop_id` 语义二义**：现在"本地 bigint 主键"由子表 FK 列
   `channel_account_id` 引用，上游文本 id 叫 `external_account_id`。若
   `external_account_id → shop_id`，系统里 `shop_id` 同时指两个东西。
   **拍板**：本地 FK 一律 `<业务名>_pk`（`shop_pk`…），`shop_id` 专指上游文本 id，二义消除。
2. **open `source_updated_at → update_at` 撞现有列**：每张表现**已有 `updated_at`**
   （我方行 touch，`onupdate=now()`）。重命名后一列两名；且 `source_updated_at`
   （上游更新时间，增量同步判据）与 `synced_at`（我方拉取时间）、`updated_at`
   （我方改行时间）三者语义由 ADR-0001 锁定，合并会破坏增量判据。
   用户拼写为 `update_at`，仓库约定是 `updated_at`。**当前建议：维持 `source_*` 不动**，
   若用户坚持，需先解决撞列与增量判据问题（默认不实施）。
3. **✅ 已拍板不扩（默认放弃）变体唯一键 `shop+spu+sku`**：见 §4 实测，现键已唯一；
   三列键 = 冗余反规范化。用户二轮未再坚持键组成改动；仅列名业务化（`spu_pk`/`sku_id`）
   已入 §2.6。
4. **open 爆炸半径**（无论哪种路径都需评估）：SQLAlchemy models → `regen_schema.py`
   重生成 schema SQL → jobs（products/orders/order_detail…）→ api/v2 路径与字段
   （`external-api.md` 是**活契约**）→ linkage/reporting/fulfillment/after_sales/finance
   跨域引用（§2.6 清单）→ tests。路径参数名改动不影响 HTTP URL（§2.6）。
5. **open 整体路径选择（用户需拍板）**：
   - (i) 仅 API/视图层**别名**（存储结构不动，外部可读性提升，零爆炸）；
   - (ii) 真 schema rename（DB migration + 全链路同步改，破坏性大）；
   - (iii) 折中：内部命名按习惯改、API 契约保持现状。
   API 路径参数歧义部分已在 §2.6 拍板（改主键语义命名）；(i)/(ii)/(iii) 仍未定。

## 6. 状态与约束

- **§2.6 命名基准线已获用户拍板并实施**：merge `6f70e73`、live migration 0007 已应用、regen 已跑、全量 fast 912 passed / 0 fail。
- 遗留（§5 未拍板项）：source_* 其余表维持现状（建议不动）；API 路径整体 (i)/(ii)/(iii) 未选——本轮按 D2 直接改了对外字段+路径参数。
- `sales_order_details` 表改名 = 用户**暂缓**，不随本轮实施。
- 实施时建议在独立 worktree 进行，遵守 AGENTS.md §11 收尾流程；schema 变更走
  `regen_schema.py` 再生成；全量测试 0 fail 是收尾硬门槛。

## 7. 关联

- `tts_erp_v2/db/models/commerce.py` — 5 张表模型（改名主战场）
- `tts_erp_v2/db/models/{after_sales,finance,fulfillment,linkage,procurement,reporting}.py`
  — §2.6 `_pk` 清单跨域涉及文件
- `tts_erp_v2/storage/schema_storage.sql` — `procurement.spu_images`（无 ORM 类）
- `tech-doc/data-model-target-v3.md` §5 — 销售域模型设计原文
- `tech-doc/external-api.md` — `/v2/commerce/*` 活契约（路径参数/字段改名影响）
- `tts_erp_v2/api/v2/commerce.py` — `/v2/commerce/*` 路由（`{account_id}`/`{product_id}`/`{order_id}` 参数）
- `tech-doc/adr/0001-time-fields-convention.md` — `source_*` / `synced_at` / `updated_at` 语义
- `scripts/regen_schema.py` — schema SQL 再生成入口
- `tts_erp_v2/jobs/tiktok/{products,orders,order_detail}.py` — 写 commerce 表的 job
- CHANGELOG 2026-09-05 — v1 legacy `public.*` 归档删除（消除 shops 撞名 blocker）
