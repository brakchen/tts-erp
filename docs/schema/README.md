# docs/schema/ — 数据库结构（DDL 快照与数据结构索引）

> 本目录是 **tts-erp 数据结构的唯一文档化出口**：一份由脚本从真实库生成的 DDL 快照
> + 一份人读的数据结构索引。**不手改 SQL**；结构变化走 alembic migration，然后重新生成快照。

## 1. 文件

| 文件 | 是什么 |
| --- | --- |
| [`schema_tts_erp.sql`](schema_tts_erp.sql) | 全库 DDL 快照（幂等、可对空库重放）：13 个业务 schema、71 张表 + `public.alembic_version`。由 `scripts/regen_schema.py` 生成，**禁止手改** |
| [`schema_storage.sql`](schema_storage.sql) | `procurement.spu_images`（SPU 图片元数据）的补丁式幂等 DDL，不走 ORM；测试在 `tests/api/conftest.py` 会用到它描述的表 |

## 2. 维护规则（怎么保持"是现在的数据结构"）

1. **每个 alembic migration 合入后必须重新生成快照**，与 migration 同一个提交里更新：
   ```bash
   # 只读 pg_dump；用跑完全部迁移的库（推荐模板库，先确认它在 alembic head）
   python3 scripts/regen_schema.py --db-url "postgresql://postgres@localhost/tts_erp_test_template"
   ```
   `scripts/regen_schema.py` 只读不写库；它剥掉序列/`\restrict` 等噪音、给
   `CREATE TABLE`/`CREATE FUNCTION` 加 `IF NOT EXISTS`，使快照可重复执行。
2. **生成源必须在当前 alembic head**（当前 head：`0064_publish_spool_ownership`）：
   ```bash
   docker exec postgres psql -U postgres -tAc "SELECT version_num FROM alembic_version" -d tts_erp_test_template
   ```
   落后于 head 的库（例如旧的 `tts_erp_v3_test`）会把旧结构写进文档，不要用。
   实例级扩展（如 `pg_stat_statements`）属容器配置，不在快照范围内。
3. **结构变化要同步人读索引**（下表 §3）：新表/新列的业务含义写进对应 schema 段落；
   口径类含义写进 [`docs/business/`](../business/)，枚举值写进 [`docs/reference/enums/`](../reference/enums/)。
4. 历史漂移教训（2026-08-25）：手维护的 schema.sql 曾漏 7 张表、列名写错，
   healthz/sync/db 三处互相矛盾——**只有"生成 + 同步"这条路是可靠的**。

## 3. 数据结构索引（截至 migration 0064）

领域模型与表间关系见 [`docs/architecture/data-model-target-v3.md`](../architecture/data-model-target-v3.md)；
「业务概念 ↔ 物理表字段」映射见 [`docs/business/spu-profitability.md`](../business/spu-profitability.md) 附录 A；
枚举值全集见 [`docs/reference/enums/`](../reference/enums/)。命名约定见
[`docs/architecture/adr/0003-commerce-naming-refactor.md`](../architecture/adr/0003-commerce-naming-refactor.md)
（内部主键 `shop_pk` / `spu_pk`；时间双字段约定见 ADR-0001）。

### commerce —— TikTok 商品与销售（5 表）
| 表 | 含义 |
| --- | --- |
| `shops` | 店铺注册表；`credential_id` 是否为空区分 API / plugin 两条采集链路 |
| `products_spu` / `products_sku` | 商品维度（SPU / SKU）；`spu_pk` 是全库 ROI、成本、广告关联的枢纽 |
| `sales_orders` / `sales_order_lines` | 订单头 / 订单行；行 GMV = `quantity × unit_price`（客户实付）；窗口归属用 `COALESCE(order_time, paid_at)` |

### finance —— 结算与到账（4 表）
| 表 | 含义 |
| --- | --- |
| `settlement_statements` / `settlement_transactions` | 平台结算单 / 订单级结算交易 |
| `settlement_components` | 结算费用分项：`SETTLEMENT`（卖家实际到账，净收入唯一权威）、`FEE`（平台总扣除，费率分子）、`CUSTOMER_REFUND` 等 |
| `payouts` | 提现记录 |

### fulfillment —— 物流（2 表）
`shipments`（包裹）、`tracking_events`（轨迹）；`action_code` 是海外取消/送达/退回的判定依据
（速查见 [`docs/reference/enums/action-code.md`](../reference/enums/action-code.md)）。

### after_sales —— 售后（2 表）
`cases`（售后单：`RETURN_AND_REFUND` / `REFUND_ONLY` / `CANCELLATION` + 状态机）、
`case_lines`（退货件数 / 退款额；行级金额可能缺失，缺失不造数）。

### plugin —— Chrome 扩展 dump 落库（19 表）
`raw_log` 理念已退役，业务表即事实：订单域 `orders / order_lines / order_details / order_timeline`，
物流域 `shipments / tracking_events`，结算域 `settlements / settlement_details`，
售后域 `after_sales / after_sale_items`，广告域 `ad_daily / ad_today / ad_raw_log / campaign_opt_logs`，
拦截管理 `intercept_configs / intercept_sessions / intercepted_requests / intercept_sync_cursors`，
观测 `plugin_logs`。协议契约见 [`docs/api/dumps-data-contract.md`](../api/dumps-data-contract.md)。

### miaoshou —— 妙手采购采集（8 表）
`packages / package_items / package_gift_items`（包裹）、`purchase_prices`（采购价）、
`purchase_order_raw_records / package_raw_records`（原始报文）、`sync_cursors / sync_issues`。
注意：妙手同步货源价**不参与**盈利计算（见 [`docs/reference/miaoshou-platform.md`](../reference/miaoshou-platform.md)）。

### procurement —— 采购与成本（4 表）
`manual_product_costs`（人工标注成本，**ROI 计算唯一成本源**，`valid_to IS NULL` 为当前有效）、
`procurement_products / procurement_accounts`（货源）、`spu_images`（SPU 图片元数据，DDL 见 `docs/schema/schema_storage.sql`）。

### reporting —— 报表与派生快照（4 表）
`shop_fee_rate_estimates`（店铺费率 `fee-v2` 日快照）、`product_cost_snapshots`（成本快照）、
`focused_spus`（重点关注 SPU 集合）、`product_profit_daily`（**旧粗略毛利快照，禁止新增消费者**，
退役约束见 [`docs/design/spu-profitability-module.md`](../design/spu-profitability-module.md) §5）。

### fx —— 汇率（2 表）
`exchange_rate_snapshots / exchange_rates`；盈利换算的唯一汇率来源（快照缺失 = 结果不可计算）。

### integration —— 上游接入与同步（7 表）
`credentials / tiktok_app_credentials / oauth_states`（凭证，只经 `tts_erp_v2.proxy.token_service`）、
`sync_jobs / sync_cursors / sync_issues`（同步任务/游标/异常）、`raw_records`（原始报文留证）。

### config —— 运行配置（4 表）
`enum_map`（枚举翻译）、`runtime_config_items / runtime_config_revisions / runtime_config_secrets`
（运行配置下发，见 [`docs/ops/runtime-config-management.md`](../ops/runtime-config-management.md)）。

### security —— 访问控制（7 表）
`api_keys`、`users / roles / permissions / role_permissions / user_roles / user_sessions`
（见 [`docs/design/user-account-authz-design.md`](../design/user-account-authz-design.md)、
[`docs/design/api-key-auth-design.md`](../design/api-key-auth-design.md)）。

### publishing —— TikTok 视频发布（3 表）
`video_publish_tasks` 保存上传对象身份、任务状态、publish/cleanup 双 owner lease、下载副作用前登记的 `spool_path` 与三类资源清理状态；
`video_publish_attempts` 是 append-only Artemis publish/verify 审计记录，`task_id / sequence_no / kind / related_attempt_id / artemis_session_id` 为不可变身份；
`worker_heartbeats` 保存发布 Worker 的受控 readiness 与设备探测状态。契约见
[`docs/design/tiktok-video-publish.md`](../design/tiktok-video-publish.md)。

### 全库约定
- **FK 策略**：同步镜像表不带外键，写入为幂等 upsert，父行先于子行；仅历史例外已清理。
- **时间**：双时间字段 `created_at` + `updated_at`（ADR-0001），全部 aware UTC。
- **幂等重放**：`CREATE TABLE/INDEX/TRIGGER` 均 `IF NOT EXISTS` / `OR REPLACE`；设计目标是
  "空库干净重放"，不要对已填充的生产库直接重放。
