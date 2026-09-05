#!/usr/bin/env python3
"""生成 migration 0001_add_time_fields.sql(脚本驱动,避免手敲 500+ 行)。

输入:硬编码的 schema.table → columns → comments 字典
输出:tts_erp_v2/db/migrations/0001_add_time_fields.sql

包含:
- public.fn_touch_updated_at() trigger function
- 25 张表 ADD COLUMN updated_at + CREATE OR REPLACE TRIGGER
- 15 张表 ADD COLUMN created_at
- ~300 列 COMMENT ON COLUMN (业务语义)

用法:
    python3 scripts/_build_time_fields_migration.py > tts_erp_v2/db/migrations/0001_add_time_fields.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

# ─── 1. trigger function ─────────────────────────────────────────────
HEADER = """-- =============================================================================
-- Migration 0001: 双时间字段约定(ADR-0001)
-- =============================================================================
-- Adds created_at / updated_at columns to v2 tables + generic BEFORE UPDATE
-- trigger (public.fn_touch_updated_at) for auto-maintenance. Adds business-
-- semantic COMMENT ON COLUMN for every v2 table column.
--
-- Strategy: idempotent (IF NOT EXISTS / OR REPLACE); re-runnable on populated
-- DB. Per ADR-0001 §5 user authorization: no gray release, direct switch.
-- =============================================================================

"""

TRIGGER_FN = """
-- 1. 通用 trigger function (BEFORE UPDATE 自动刷 updated_at = clock_timestamp())
-- 注意:用 clock_timestamp() 而不是 now() — now() 是事务开始时间,事务内多次调用值不变;
--       clock_timestamp() 真实墙钟时间,每次调用都不同(per ADR-0001 §6.1)。
CREATE OR REPLACE FUNCTION public.fn_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := clock_timestamp();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.fn_touch_updated_at() IS
  'BEFORE UPDATE trigger: 自动把 NEW.updated_at 设为 clock_timestamp()(真实当前时间,不是事务开始时间,所有 v2 表统一通过此函数维护最近一次修改时间,per ADR-0001)。';

"""

# ─── 2. 表级 ALTER(列 + trigger)────────────────────────────────────
# 格式: (schema, table, [add_columns], has_create_already)
# add_columns: 需要 ADD 的列名(list),None 表示不加,只加 trigger
TABLE_ALTERS = [
    # ─── 原本双时间都齐、仅缺 trigger 的 2 张表────────────────────
    ("integration", "credentials", ["trigger"], "created_at"),  # 已有双时间,加 trigger
    ("linkage", "product_links", ["trigger"], "created_at"),  # 已有双时间,加 trigger
    # ─── 缺 updated_at 的 25 张表(已有 synced_at / created_at)────────
    ("after_sales", "cases", ["updated_at"], "synced_at"),
    ("analytics", "ad_audit_log", ["updated_at"], "created_at"),
    ("analytics", "ad_daily_completeness", ["updated_at"], "captured_at"),
    ("analytics", "ad_raw", ["updated_at"], "captured_at"),
    ("analytics", "ad_records", ["updated_at"], "synced_at"),
    ("commerce", "shops", ["updated_at"], "synced_at"),
    ("commerce", "products_sku", ["updated_at"], "synced_at"),
    ("commerce", "products_spu", ["updated_at"], "synced_at"),
    ("commerce", "sales_order_lines", ["updated_at"], "synced_at"),
    ("commerce", "sales_orders", ["updated_at"], "synced_at"),
    ("finance", "payouts", ["updated_at"], "synced_at"),
    ("finance", "settlement_statements", ["updated_at"], "synced_at"),
    ("finance", "settlement_transactions", ["updated_at"], "synced_at"),
    ("fulfillment", "shipments", ["updated_at"], "synced_at"),
    ("fulfillment", "tracking_events", ["updated_at"], "synced_at"),
    ("integration", "raw_records", ["updated_at"], "synced_at"),
    ("linkage", "link_issues", ["updated_at"], "synced_at"),
    ("linkage", "link_overrides", ["updated_at"], "synced_at"),
    ("procurement", "manual_product_costs", ["updated_at"], "synced_at"),
    ("procurement", "procurement_accounts", ["updated_at"], "synced_at"),
    ("procurement", "procurement_product_variants", ["updated_at"], "synced_at"),
    ("procurement", "procurement_products", ["updated_at"], "synced_at"),
    ("procurement", "purchase_order_lines", ["updated_at"], "synced_at"),
    ("procurement", "purchase_orders", ["updated_at"], "synced_at"),
    ("security", "api_keys", ["updated_at", "trigger"], "created_at"),
    # ─── 缺 created_at 的 2 张(已有 updated_at)────────────────────
    ("analytics", "ad_shop_timezones", ["created_at", "trigger"], None),  # updated_at 已存在,加 trigger
    ("integration", "sync_cursors", ["created_at", "trigger"], None),  # updated_at 已存在,加 trigger
    # ─── 双缺的 13 张表(need both)──────────────────────────────────
    ("after_sales", "case_lines", ["created_at", "updated_at"], None),  # 实际已有,跳过
    ("finance", "settlement_components", ["created_at", "updated_at"], None),
    ("fulfillment", "shipment_lines", ["created_at", "updated_at"], None),  # 实际已有,跳过
    ("integration", "sync_issues", ["created_at", "updated_at"], None),
    ("integration", "sync_jobs", ["created_at", "updated_at"], None),
    ("linkage", "account_links", ["created_at", "updated_at"], None),
    # "linkage"."effective_product_links" 是 view,跳过
    ("linkage", "link_evidence", ["created_at", "updated_at"], None),
    ("linkage", "variant_links", ["created_at", "updated_at"], None),
    ("procurement", "spu_images", ["created_at", "updated_at"], None),  # 实际已有,跳过
    ("reporting", "product_cost_snapshots", ["created_at", "updated_at"], None),
    ("reporting", "product_profit_daily", ["created_at", "updated_at"], None),
    ("reporting", "shipment_tracking_summary", ["created_at", "updated_at"], None),
]

# 表级跳过(已经是合规的,不再加列 / trigger)
# 实际已合规:  integration.credentials, linkage.product_links,
#             after_sales.case_lines, fulfillment.shipment_lines,
#             procurement.spu_images
# View: linkage.effective_product_links


def build_table_alters() -> str:
    lines = ["-- 2. ALTER TABLE: 加 updated_at / created_at + BEFORE UPDATE trigger\n"]
    for schema, table, add_cols, has_create in TABLE_ALTERS:
        # ADD COLUMN
        for col in add_cols:
            if col == "updated_at":
                lines.append(
                    f"ALTER TABLE {schema}.{table} "
                    f"ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone "
                    f"NOT NULL DEFAULT now();\n"
                )
            elif col == "created_at":
                lines.append(
                    f"ALTER TABLE {schema}.{table} "
                    f"ADD COLUMN IF NOT EXISTS created_at timestamp with time zone "
                    f"NOT NULL DEFAULT now();\n"
                )
            elif col == "trigger":
                pass  # trigger-only,handled below
        # BEFORE UPDATE trigger(只要这张表需要 trigger 就加,不论是否新加 updated_at)
        if "updated_at" in add_cols or "trigger" in add_cols:
            trigger_name = f"trg_{schema}_{table}_touch"
            lines.append(
                f"CREATE OR REPLACE TRIGGER {trigger_name}\n"
                f"  BEFORE UPDATE ON {schema}.{table}\n"
                f"  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();\n"
            )
    return "\n".join(lines) + "\n\n"


# ─── 3. COMMENT ON COLUMN(业务语义)──────────────────────────────
# 格式: (schema, table, column, comment_text)
# 中文,无歧义,描述业务用途 + 单位/枚举值
COMMENTS: list[tuple[str, str, str, str]] = [
    # ─── commerce ─────────────────────────────────────────────────
    ("commerce", "shops", "id", "主键,系统内部 bigint identity。"),
    ("commerce", "shops", "platform", "渠道平台标识:'tiktok' | 'miaoshou'(单表存所有平台,多未来)。"),
    ("commerce", "shops", "external_account_id", "TikTok shop_id(数字字符串)或 miaoshou licenseId;对外唯一。"),
    ("commerce", "shops", "account_name", "店铺展示名(便于人工识别,不会用作程序逻辑)。"),
    ("commerce", "shops", "region", "店铺地区代码:TikTok 是 ISO 国家码(VN/TH/PH 等),miaoshou 是空。"),
    ("commerce", "shops", "seller_type", "TikTok 卖家类型:local / cross_border / mall / brand 等。"),
    ("commerce", "shops", "status", "店铺授权/激活状态:active | suspended | closed;OAuth callback 时写入。"),
    ("commerce", "shops", "credential_id", "关联 integration.credentials;NULL 表示已解绑但历史订单保留。"),
    ("commerce", "shops", "source_updated_at", "TikTok/miaoshou 上游 API 返回的最近一次店铺元信息更新时间。"),
    ("commerce", "shops", "synced_at", "本地 tts-erp 首次落地此账号记录的时间(INSERT 时赋值,不再变)。"),
    ("commerce", "shops", "updated_at", "此行最近一次修改时间(BEFORE UPDATE trigger 自动维护,per ADR-0001)。"),
    ("commerce", "shops", "created_at", "此行创建时间(同 synced_at 语义,因 shops 几乎不会重复创建)。"),

    ("commerce", "products_spu", "id", "系统内部 bigint identity;外部用 external_product_id。"),
    ("commerce", "products_spu", "shop_pk", "所属 TikTok shop;FK 到 shops。"),
    ("commerce", "products_spu", "external_product_id", "TikTok SPU ID(数字字符串),跟 shop_pk 联合唯一。"),
    ("commerce", "products_spu", "title", "商品标题(可能含 unicode / 多语言 / emoji;长度不限制)。"),
    ("commerce", "products_spu", "category_id", "TikTok 商品类目 ID(数字字符串);用于类目过滤 / 类目属性补全。"),
    ("commerce", "products_spu", "status", "商品上下架状态:available / unavailable / draft / deleted;由 TikTok 决定。"),
    ("commerce", "products_spu", "main_image_url", "商品主图 URL(TikTok CDN,带签名 token,会过期)。"),
    ("commerce", "products_spu", "source_created_at", "TikTok 上游的 SPU 创建时间(店铺侧首次上架时间)。"),
    ("commerce", "products_spu", "source_updated_at", "TikTok 上游的 SPU 最近一次更新时间(标题/价格/状态变化等)。"),
    ("commerce", "products_spu", "raw_record_id", "关联 integration.raw_records(此行由哪条上游 API 响应解析得到);FK 关联。"),
    ("commerce", "products_spu", "synced_at", "本地 tts-erp 首次落地此 SPU 的时间(INSERT 时赋值)。"),
    ("commerce", "products_spu", "updated_at", "此行最近一次修改时间(trigger 自动维护,per ADR-0001)。"),
    ("commerce", "products_spu", "created_at", "此行创建时间(通常 = synced_at)。"),

    ("commerce", "products_sku", "id", "主键,系统 bigint identity。"),
    ("commerce", "products_sku", "spu_pk", "所属 SPU;FK 到 products_spu。"),
    ("commerce", "products_sku", "external_variant_id", "TikTok SKU ID;联合 spu_pk 唯一。"),
    ("commerce", "products_sku", "seller_sku", "卖家自定义 SKU 编码(可空,平台可不填)。"),
    ("commerce", "products_sku", "variant_name", "变体名称(如 '红色/M');可空。"),
    ("commerce", "products_sku", "attributes", "变体属性 JSON:颜色/尺码/材质等键值对;NULL 表示无属性。"),
    ("commerce", "products_sku", "image_url", "变体图 URL;空字符串或 NULL 时回退到 SPU 主图。"),
    ("commerce", "products_sku", "status", "变体上下架状态(同 SPU 状态)。"),
    ("commerce", "products_sku", "source_updated_at", "TikTok 上游的 SKU 最近更新时间。"),
    ("commerce", "products_sku", "raw_record_id", "关联 raw_records,FK。"),
    ("commerce", "products_sku", "synced_at", "本地首次落地时间(INSERT 时赋值)。"),
    ("commerce", "products_sku", "updated_at", "最近修改时间(trigger 自动维护)。"),
    ("commerce", "products_sku", "created_at", "创建时间(通常 = synced_at)。"),

    ("commerce", "sales_orders", "id", "系统内部主键 bigint identity。"),
    ("commerce", "sales_orders", "shop_pk", "所属 TikTok shop;FK。"),
    ("commerce", "sales_orders", "order_id", "TikTok 订单 ID;联合 shop_pk 唯一。"),
    ("commerce", "sales_orders", "status", "订单状态(枚举):AWAITING_SHIPMENT / AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED / CANCELLED。"),
    ("commerce", "sales_orders", "currency", "ISO 4217 货币代码:VND / THB / PHP / USD 等。"),
    ("commerce", "sales_orders", "payment_amount", "买家实付金额(含运费,不含优惠);numeric(20,4) 避免浮点。"),
    ("commerce", "sales_orders", "total_amount", "订单总金额(payment + 平台优惠 + 折扣);通常 = payment_amount 但可能有差异。"),
    ("commerce", "sales_orders", "fulfillment_type", "履约方式:TikTok shipping / seller shipping / cross_border。"),
    ("commerce", "sales_orders", "source_created_at", "TikTok 上游订单创建时间(买家下单时刻)。"),
    ("commerce", "sales_orders", "source_updated_at", "TikTok 上游订单最近一次状态/字段变化时间。"),
    ("commerce", "sales_orders", "paid_at", "买家付款完成时间(关键 SLA 指标)。"),
    ("commerce", "sales_orders", "shipped_at", "包裹出库时间(用于计算物流时效)。"),
    ("commerce", "sales_orders", "delivered_at", "签收完成时间。"),
    ("commerce", "sales_orders", "cancelled_at", "取消时间(可与 status=CANCELLED 一起用作 SLA 分析)。"),
    ("commerce", "sales_orders", "raw_record_id", "关联 raw_records,FK。"),
    ("commerce", "sales_orders", "synced_at", "本地首次入库时间(INSERT 时赋值,UPDATE 不再变 — per ADR-0001 §2.1)。"),
    ("commerce", "sales_orders", "updated_at", "此订单最近一次修改时间(trigger 自动维护,反映 sync 实际活跃度)。"),
    ("commerce", "sales_orders", "created_at", "此订单行创建时间(通常 = synced_at)。"),

    ("commerce", "sales_order_lines", "id", "主键,系统 bigint identity。"),
    ("commerce", "sales_order_lines", "order_pk", "所属订单头;FK 到 sales_orders。"),
    ("commerce", "sales_order_lines", "external_line_id", "TikTok 订单行 ID;联合 order_pk 唯一。"),
    ("commerce", "sales_order_lines", "spu_pk", "关联的 SPU;FK,**nullable** — 产品未同步时行先到,后补。"),
    ("commerce", "sales_order_lines", "sku_pk", "关联的 SKU;FK,可空(同上)。"),
    ("commerce", "sales_order_lines", "external_product_id_snapshot", "下单时 TikTok SPU ID 的快照(产品被删除后仍可溯源)。"),
    ("commerce", "sales_order_lines", "external_variant_id_snapshot", "下单时 TikTok SKU ID 的快照。"),
    ("commerce", "sales_order_lines", "product_name_snapshot", "下单时商品名称快照(防止后改商品标题后历史失真)。"),
    ("commerce", "sales_order_lines", "variant_name_snapshot", "下单时变体名称快照。"),
    ("commerce", "sales_order_lines", "image_url_snapshot", "下单时商品图快照。"),
    ("commerce", "sales_order_lines", "quantity", "购买件数,numeric(20,4) 兼容小数(如 0.5 公斤)。"),
    ("commerce", "sales_order_lines", "unit_price", "下单单价(原始货币,未含优惠/税)。"),
    ("commerce", "sales_order_lines", "currency", "ISO 4217 货币代码(可能与订单头不同,如跨境多币种)。"),
    ("commerce", "sales_order_lines", "line_status", "订单行状态:NORMAL / CANCELLED / RETURNED。"),
    ("commerce", "sales_order_lines", "raw_record_id", "关联 raw_records,FK。"),
    ("commerce", "sales_order_lines", "synced_at", "本地首次入库时间(INSERT 时赋值)。"),
    ("commerce", "sales_order_lines", "updated_at", "最近一次修改时间(trigger 自动维护)。"),
    ("commerce", "sales_order_lines", "created_at", "行创建时间(通常 = synced_at)。"),

    # ─── integration ────────────────────────────────────────────
    ("integration", "credentials", "id", "主键 bigint identity。"),
    ("integration", "credentials", "provider", "OAuth 提供方:'tiktok' | 'miaoshou'。"),
    ("integration", "credentials", "external_account_id", "TikTok shop_id 或 miaoshou licenseId;联合 provider 唯一。"),
    ("integration", "credentials", "account_label", "人工可读标签(如 'VN 旗舰店');仅展示用,不影响逻辑。"),
    ("integration", "credentials", "ciphertext", "Fernet 加密的 access_token;密文,plaintext 只在 process memory(per AGENTS.md §4.1)。"),
    ("integration", "credentials", "expires_at", "access_token 过期时间(ISO 8601);null 表示永不过期或未指定。"),
    ("integration", "credentials", "granted_scopes", "授权 scope 列表(仅 TikTok):['product.write' 等]。"),
    ("integration", "credentials", "company_secret_ciphertext", "Miaoshou companySecret 的 Fernet 密文(仅 miaoshou 用)。"),
    ("integration", "credentials", "extra", "平台特定扩展字段(Miaoshou license meta、TikTok 店铺元信息等),JSON 灵活存。"),
    ("integration", "credentials", "created_at", "凭证首次入库时间(insert 时赋值,等同首次 OAuth callback 成功时间)。"),
    ("integration", "credentials", "updated_at", "凭证最近一次更新(token 刷新 / scope 重授权);trigger 自动维护。"),

    ("integration", "raw_records", "id", "主键 bigint identity,所有规范化表的外键指向此 id。"),
    ("integration", "raw_records", "credential_id", "关联 credentials.id;FK;**use_alter=True** 因为凭证可能延迟插入。"),
    ("integration", "raw_records", "endpoint", "上游 API 端点路径(如 'tiktok.order.search');用于查询和路由。"),
    ("integration", "raw_records", "external_id", "上游资源 ID(订单 ID / 商品 ID);用 payload_hash 兜底(上游可能不返回 ID)。"),
    ("integration", "raw_records", "captured_at", "Chrome 扩展 / sync worker 抓取上游 response 的时刻(应用本地时钟)。"),
    ("integration", "raw_records", "payload", "上游 API 完整 JSON 响应(jsonb 原样存,server 不做字段过滤 — per dump-architecture)。"),
    ("integration", "raw_records", "payload_hash", "payload 的 sha256 哈希(64 字符 hex),用于幂等去重。"),
    ("integration", "raw_records", "synced_at", "本地入库时刻(由 trigger/maintain 维护,主要供 ORM 使用)。"),
    ("integration", "raw_records", "updated_at", "最近一次访问/重写时间(trigger 自动维护)。"),
    ("integration", "raw_records", "created_at", "首次入库时间(insert 时赋值,等同 captured_at 第一个版本)。"),

    ("integration", "sync_jobs", "id", "主键 bigint identity。"),
    ("integration", "sync_jobs", "job_name", "作业名:'tiktok.orders' / 'tiktok.finance.statements' / 'miaoshou.collect_box' 等(per sync_worker/scheduler.py)。"),
    ("integration", "sync_jobs", "credential_id", "本次作业用的凭证;FK,SET NULL(凭证解绑不丢历史)。"),
    ("integration", "sync_jobs", "started_at", "作业开始执行时间(本地时钟)。"),
    ("integration", "sync_jobs", "finished_at", "作业结束时间(succeeded/failed 时由代码显式写,无默认值 — per ADR-0001 §2.1)。"),
    ("integration", "sync_jobs", "status", "作业状态:'running' | 'succeeded' | 'failed';运行时 → 终态。"),
    ("integration", "sync_jobs", "rows_total", "本次作业扫描到的上游记录总数(含已存在的,新+旧+失败)。"),
    ("integration", "sync_jobs", "rows_inserted", "本次新插入到目标表(sales_orders 等)的行数。"),
    ("integration", "sync_jobs", "rows_updated", "本次 UPDATE 已存在行的次数(ON CONFLICT DO UPDATE 触发)。"),
    ("integration", "sync_jobs", "rows_failed", "本次失败的行数(写入 sync_issues 但不阻断作业)。"),
    ("integration", "sync_jobs", "error_message", "失败时作业级错误(不是行级,行级错在 sync_issues);前 1024 字符。"),
    ("integration", "sync_jobs", "extra", "作业特定扩展(API 配额、限流重试次数、店铺 ID 列表等)JSON 存。"),
    ("integration", "sync_jobs", "updated_at", "最近一次状态变化时间(由 trigger 自动维护 — 反映作业真正结束时刻)。"),
    ("integration", "sync_jobs", "created_at", "行创建时间(通常 = started_at)。"),

    ("integration", "sync_cursors", "id", "主键 bigint identity。"),
    ("integration", "sync_cursors", "job_name", "作业名(同 sync_jobs.job_name 命名)。"),
    ("integration", "sync_cursors", "scope", "游标作用域:通常是 shop_id,某些作业用 'all' / 'daily' 等聚合 key。"),
    ("integration", "sync_cursors", "cursor_value", "字符串类型游标(API 文档指明的 opaque token / next_page_url);空表示首次。"),
    ("integration", "sync_cursors", "cursor_epoch_ms", "数值类型游标(通常 = 上次 max(source_updated_at) epoch ms);用于增量同步。"),
    ("integration", "sync_cursors", "updated_at", "游标最近一次推进时间(注意:per ADR-0001 §3.2 现状,旧 schema 此字段只在 INSERT 写;本次加 trigger 后会变正确)。"),
    ("integration", "sync_cursors", "created_at", "游标行创建时间(通常是该 (job, scope) 首次 sync 的时刻)。"),

    ("integration", "sync_issues", "id", "主键 bigint identity。"),
    ("integration", "sync_issues", "job_name", "出 issue 的作业名(同 sync_jobs.job_name)。"),
    ("integration", "sync_issues", "issue_type", "issue 类型枚举:TOKEN_REFRESH_FAILED / STATEMENT_PAYMENT_ID_MISSING / UNKNOWN_ORDER / SCHEMA_INVALID 等。"),
    ("integration", "sync_issues", "external_id", "上游资源 ID(如缺失 payment_id 的 statement_id);null 表示作业级 issue。"),
    ("integration", "sync_issues", "details", "issue 详情 JSON(payload 截断、错误码、上游响应等,便于排障)。"),
    ("integration", "sync_issues", "detected_at", "检测到 issue 的时间(第一次出现时刻)。"),
    ("integration", "sync_issues", "resolved_at", "解决时间(同 issue_type 重新跑且不再出现的时刻);null = 未解决。"),
    ("integration", "sync_issues", "updated_at", "最近一次状态变化时间(新建或解决时刷新,trigger 自动维护)。"),
    ("integration", "sync_issues", "created_at", "行创建时间(通常 = detected_at 第一次)。"),

    # ─── analytics ──────────────────────────────────────────────
    ("analytics", "ad_raw", "id", "主键 bigint identity。"),
    ("analytics", "ad_raw", "idempotency_key", "幂等键(SHA-256 of 5 元组 + page=1),用于 ON CONFLICT DO UPDATE 判重。"),
    ("analytics", "ad_raw", "seller_id", "TikTok shop_id(数字字符串),广告数据所属店铺。"),
    ("analytics", "ad_raw", "advertiser_id", "TikTok advertiser_id(同 shop_id);同 seller_id 配合用于 API 权限校验。"),
    ("analytics", "ad_raw", "endpoint", "TikTok OEC API 端点(per tiktok-endpoint-schemas.ts 的 4 路径白名单)。"),
    ("analytics", "ad_raw", "method", "HTTP method:GET / POST(目前 dumps 协议统一 POST)。"),
    ("analytics", "ad_raw", "day", "TikTok 数据聚合粒度:yyyy-mm-dd;每条 raw 一行一日。"),
    ("analytics", "ad_raw", "campaign_id", "TikTok 广告计划 ID(数字字符串);5 元组 unique 的一部分。"),
    ("analytics", "ad_raw", "request", "Chrome 扩展抓的完整 HTTP request JSON(url+body+headers,jsonb)。"),
    ("analytics", "ad_raw", "response", "Chrome 扩展抓的完整 HTTP response JSON(status+body+headers,jsonb,不可变 — source of truth)。"),
    ("analytics", "ad_raw", "captured_at", "Chrome 扩展抓取时刻(应用本地时钟,非 TikTok 上游时间)。"),
    ("analytics", "ad_raw", "source", "数据来源标识(默认 'tiktok-shop-data-sync' = Chrome 扩展 ID)。"),
    ("analytics", "ad_raw", "request_id", "请求链路 ID(uuid,用于跨表追踪 dumps 流程)。"),
    ("analytics", "ad_raw", "protocol_version", "dumps 协议版本号(per tech-doc/analytics/dump-architecture.md,目前 2)。"),
    ("analytics", "ad_raw", "schema_version", "dump 内 payload 的 schema 版本(目前 1)。"),
    ("analytics", "ad_raw", "updated_at", "最近修改时间(trigger 自动维护;对 source-of-truth 表是 ad_raw upsert 重写时刷)。"),
    ("analytics", "ad_raw", "created_at", "首次入库时间(insert 时赋值,等同 captured_at 第一个版本)。"),

    ("analytics", "ad_records", "id", "主键 bigint identity;**派生**自 ad_raw,productAnalyses = ad_raw.endpoint=post_product_list。"),
    ("analytics", "ad_records", "idempotency_key", "同 ad_raw.idempotency_key(派生时复制)。"),
    ("analytics", "ad_records", "source_record_id", "FK 到 ad_raw.id,指明派生自哪条 raw(ON DELETE SET NULL)。"),
    ("analytics", "ad_records", "seller_id", "同 ad_raw.seller_id(派生时复制)。"),
    ("analytics", "ad_records", "advertiser_id", "同 ad_raw.advertiser_id(派生时复制)。"),
    ("analytics", "ad_records", "storage_key", "server 端从 endpoint 推导的 storage_key:productAnalyses / sessionAnalyses / campaignChangeLogs(per STORAGE_KEY_BY_PATH)。"),
    ("analytics", "ad_records", "campaign_id", "TikTok 广告计划 ID;5 元组 unique 的一部分。"),
    ("analytics", "ad_records", "day", "yyyy-mm-dd 聚合粒度。"),
    ("analytics", "ad_records", "shop_name", "店铺展示名(空字符串或 NULL;来自 sales_orders / shops 反查)。"),
    ("analytics", "ad_records", "endpoint", "同 ad_raw.endpoint(派生时复制)。"),
    ("analytics", "ad_records", "method", "同 ad_raw.method(派生时复制)。"),
    ("analytics", "ad_records", "request_body", "仅 request.body 字段(派生时提取,jsonb)。"),
    ("analytics", "ad_records", "response_data", "仅 response.body 字段(派生时提取,jsonb,业务常用)。"),
    ("analytics", "ad_records", "source", "数据来源(同 ad_raw.source)。"),
    ("analytics", "ad_records", "captured_at", "同 ad_raw.captured_at(派生时复制)。"),
    ("analytics", "ad_records", "schema_version", "同 ad_raw.schema_version(派生时复制)。"),
    ("analytics", "ad_records", "protocol_version", "同 ad_raw.protocol_version(派生时复制)。"),
    ("analytics", "ad_records", "request_id", "同 ad_raw.request_id(派生时复制)。"),
    ("analytics", "ad_records", "updated_at", "最近派生时间(trigger 自动维护 — ad_raw 重新 upsert 派生表也重写)。"),
    ("analytics", "ad_records", "created_at", "首次派生时间(insert 时赋值)。"),

    ("analytics", "ad_daily_completeness", "seller_id", "TikTok shop_id;5 元组 unique 的一部分。"),
    ("analytics", "ad_daily_completeness", "advertiser_id", "TikTok advertiser_id。"),
    ("analytics", "ad_daily_completeness", "storage_key", "productAnalyses / sessionAnalyses / campaignChangeLogs(CHECK 约束限制 3 个值)。"),
    ("analytics", "ad_daily_completeness", "campaign_id", "TikTok 广告计划 ID;5 元组 unique 的一部分。"),
    ("analytics", "ad_daily_completeness", "day", "yyyy-mm-dd;5 元组 unique 的一部分。"),
    ("analytics", "ad_daily_completeness", "captured_at", "最近一次 captured 时间(dumps 协议下 = upsert 时间,语义 = '最近一次抓过')。"),
    ("analytics", "ad_daily_completeness", "updated_at", "最近一次 dailiness 标记时间(trigger 自动维护)。"),
    ("analytics", "ad_daily_completeness", "created_at", "首次标记完整时间(insert 时赋值)。"),

    ("analytics", "ad_audit_log", "id", "主键 bigint identity。"),
    ("analytics", "ad_audit_log", "request_id", "请求链路 ID(uuid,跨表追踪)。"),
    ("analytics", "ad_audit_log", "endpoint", "v2 端点名:'dumps' / 'cursor'(per tech-doc/analytics/dump-architecture.md)。"),
    ("analytics", "ad_audit_log", "method", "HTTP method(GET cursor / POST dumps)。"),
    ("analytics", "ad_audit_log", "path", "完整请求 path(含 query string),用于排障重现。"),
    ("analytics", "ad_audit_log", "status", "HTTP 响应状态码(200 / 400 / 413 / 500 等)。"),
    ("analytics", "ad_audit_log", "key_prefix", "API key 前缀(明文 key 永不落库,只存 prefix 用于识别 client)。"),
    ("analytics", "ad_audit_log", "records_in", "本请求输入的记录数(对 dumps 协议 = 1)。"),
    ("analytics", "ad_audit_log", "records_ok", "本请求成功写入的记录数。"),
    ("analytics", "ad_audit_log", "records_rej", "本请求被拒绝的记录数(校验失败)。"),
    ("analytics", "ad_audit_log", "error_code", "业务错误码:MALFORMED_JSON / SCHEMA_INVALID / SCOPE_DENIED / PAYLOAD_TOO_LARGE 等。"),
    ("analytics", "ad_audit_log", "error_message", "错误详情文本(脱敏后,前 240 字符,per sanitizeDiagnosticText)。"),
    ("analytics", "ad_audit_log", "updated_at", "最近一次修改(trigger 自动维护;正常只 INSERT,UPDATE 罕见)。"),
    ("analytics", "ad_audit_log", "created_at", "日志行创建时间(等同收到请求的时间,per created_at 列默认 now())。"),

    ("analytics", "ad_shop_timezones", "seller_id", "TikTok shop_id;主键(单一主键)。"),
    ("analytics", "ad_shop_timezones", "advertiser_id", "TikTok advertiser_id(可空字符串)。"),
    ("analytics", "ad_shop_timezones", "timezone", "IANA 时区标识(如 'Asia/Shanghai' / 'Asia/Ho_Chi_Minh');空 = 用默认 'Asia/Shanghai'。"),
    ("analytics", "ad_shop_timezones", "updated_at", "时区配置最近一次更新(trigger 自动维护,典型场景:运营调整店铺时区)。"),
    ("analytics", "ad_shop_timezones", "created_at", "时区配置首次写入时间(insert 时赋值,通常是首次 sync 该 shop 时)。"),

    # ─── linkage ───────────────────────────────────────────────
    ("linkage", "product_links", "id", "主键 bigint identity(per product_linkage.py 派生表)。"),
    ("linkage", "product_links", "spu_pk", "TikTok SPU;FK 到 commerce.products_spu。"),
    ("linkage", "product_links", "procurement_product_id", "内部 SPU;FK 到 procurement.procurement_products。"),
    ("linkage", "product_links", "sku_pk", "可选 SKU 关联;FK。"),
    ("linkage", "product_links", "procurement_product_variant_id", "可选内部 SKU 关联;FK。"),
    ("linkage", "product_links", "link_type", "关联类型:auto(系统算的)/ manual(人手配的)/ override(覆盖)。"),
    ("linkage", "product_links", "match_score", "匹配度 0-1;auto 关联的置信度,manual 为 1.0。"),
    ("linkage", "product_links", "rule_version", "匹配规则版本号(hash);变化时旧关联标 stale。"),
    ("linkage", "product_links", "decided_at", "决策时间(最近一次决定关联的时间)。"),
    ("linkage", "product_links", "decided_by", "决策者:auto / user:{username} / admin / system。"),
    ("linkage", "product_links", "is_active", "是否激活:false 表示软删除(被 override 替代)。"),
    ("linkage", "product_links", "notes", "人工备注(为什么这样关联,便于 review)。"),
    ("linkage", "product_links", "raw_record_id", "关联 raw_records,FK。"),
    ("linkage", "product_links", "updated_at", "最近一次修改(trigger 自动维护 — override 写入会刷)。"),
    ("linkage", "product_links", "created_at", "首次创建时间(insert 时赋值)。"),

    ("linkage", "variant_links", "id", "主键 bigint identity。"),
    ("linkage", "variant_links", "spu_pk", "TikTok SPU。"),
    ("linkage", "variant_links", "sku_pk", "TikTok SKU。"),
    ("linkage", "variant_links", "procurement_product_id", "内部 SPU。"),
    ("linkage", "variant_links", "procurement_product_variant_id", "内部 SKU。"),
    ("linkage", "variant_links", "link_type", "同 product_links.link_type。"),
    ("linkage", "variant_links", "match_score", "0-1 匹配度。"),
    ("linkage", "variant_links", "rule_version", "匹配规则版本。"),
    ("linkage", "variant_links", "decided_at", "决策时间。"),
    ("linkage", "variant_links", "decided_by", "决策者。"),
    ("linkage", "variant_links", "is_active", "软删除标记。"),
    ("linkage", "variant_links", "raw_record_id", "关联 raw_records。"),
    ("linkage", "variant_links", "updated_at", "最近修改时间。"),
    ("linkage", "variant_links", "created_at", "创建时间。"),

    ("linkage", "account_links", "id", "主键 bigint identity。"),
    ("linkage", "account_links", "shop_pk", "TikTok shop;FK。"),
    ("linkage", "account_links", "procurement_account_id", "内部采购账号;FK。"),
    ("linkage", "account_links", "raw_record_id", "关联 raw_records。"),
    ("linkage", "account_links", "updated_at", "最近修改(trigger 维护)。"),
    ("linkage", "account_links", "created_at", "创建时间。"),

("linkage", "link_evidence", "id", "主键。"),
    ("linkage", "link_evidence", "product_link_id", "FK 到 product_links;NULL 表示 evidence 已被独立于 link 存(老逻辑)。"),
    ("linkage", "link_evidence", "variant_link_id", "FK 到 variant_links。"),
    ("linkage", "link_evidence", "kind", "evidence 类型:title_match / sku_match / barcode_match / image_hash / manual_review。"),
    ("linkage", "link_evidence", "score", "本 evidence 的 0-1 分数。"),
    ("linkage", "link_evidence", "details", "evidence 详情 JSON(命中的 token / 距离 / 阈值等)。"),
    ("linkage", "link_evidence", "raw_record_id", "关联 raw_records。"),
    ("linkage", "link_evidence", "updated_at", "最近修改。"),
    ("linkage", "link_evidence", "created_at", "创建时间。"),

    ("linkage", "link_issues", "id", "主键。"),
    ("linkage", "link_issues", "spu_pk", "出 issue 的 TikTok SPU;FK。"),
    ("linkage", "link_issues", "sku_pk", "可选 SKU;FK。"),
    ("linkage", "link_issues", "issue_type", "issue 类型:NO_CANDIDATE / AMBIGUOUS_MATCH / OVERRIDE_CONFLICT 等。"),
    ("linkage", "link_issues", "details", "issue 详情 JSON。"),
    ("linkage", "link_issues", "resolved_at", "解决时间;NULL = 未解决。"),
    ("linkage", "link_issues", "resolved_by", "解决者(用户名或 'auto')。"),
    ("linkage", "link_issues", "raw_record_id", "关联 raw_records。"),
    ("linkage", "link_issues", "updated_at", "最近修改。"),
    ("linkage", "link_issues", "created_at", "创建时间(等同 issue 第一次出现时间)。"),

    ("linkage", "link_overrides", "id", "主键。"),
    ("linkage", "link_overrides", "spu_pk", "TikTok SPU;FK。"),
    ("linkage", "link_overrides", "sku_pk", "可选 SKU;FK。"),
    ("linkage", "link_overrides", "procurement_product_id", "强制关联到的内部 SPU;FK。"),
    ("linkage", "link_overrides", "procurement_product_variant_id", "可选 SKU。"),
    ("linkage", "link_overrides", "reason", "覆盖理由(人工必填,便于审计)。"),
    ("linkage", "link_overrides", "created_by", "创建人 username。"),
    ("linkage", "link_overrides", "effective_from", "生效开始时间(默认现在,支持未来生效的覆盖)。"),
    ("linkage", "link_overrides", "effective_to", "失效时间;NULL = 永久。"),
    ("linkage", "link_overrides", "raw_record_id", "关联 raw_records(创建时引用)。"),
    ("linkage", "link_overrides", "updated_at", "最近修改。"),
    ("linkage", "link_overrides", "created_at", "创建时间。"),

    # ─── procurement ───────────────────────────────────────────
    ("procurement", "procurement_accounts", "id", "主键。"),
    ("procurement", "procurement_accounts", "external_account_id", "内部采购账号 ID;unique(用于登录/鉴权)。"),
    ("procurement", "procurement_accounts", "account_name", "账号展示名。"),
    ("procurement", "procurement_accounts", "platform", "采购平台:'miaoshou' / 'wanshifu' / 内部直采。"),
    ("procurement", "procurement_accounts", "credential_id", "关联 integration.credentials。"),
    ("procurement", "procurement_accounts", "status", "账号状态:active / suspended / closed。"),
    ("procurement", "procurement_accounts", "source_updated_at", "上游(妙手等)账号信息最近更新时间。"),
    ("procurement", "procurement_accounts", "synced_at", "本地首次入库时间。"),
    ("procurement", "procurement_accounts", "updated_at", "最近修改。"),
    ("procurement", "procurement_accounts", "created_at", "创建时间。"),

    ("procurement", "procurement_products", "id", "主键。"),
    ("procurement", "procurement_products", "procurement_account_id", "所属采购账号;FK。"),
    ("procurement", "procurement_products", "external_product_id", "妙手/平台商品 ID;联合 procurement_account_id 唯一。"),
    ("procurement", "procurement_products", "title", "商品名(中文为主,可能含 emoji)。"),
    ("procurement", "procurement_products", "spu_code", "内部 SPU 编码(通常 = title 的拼音首字母 + 数字)。"),
    ("procurement", "procurement_products", "category", "类目(自由文本,未结构化)。"),
    ("procurement", "procurement_products", "status", "商品状态:active / inactive / delisted。"),
    ("procurement", "procurement_products", "main_image_url", "主图 URL。"),
    ("procurement", "procurement_products", "source_created_at", "上游商品创建时间。"),
    ("procurement", "procurement_products", "source_updated_at", "上游商品最近更新时间。"),
    ("procurement", "procurement_products", "raw_record_id", "关联 raw_records。"),
    ("procurement", "procurement_products", "synced_at", "本地首次入库时间。"),
    ("procurement", "procurement_products", "updated_at", "最近修改。"),
    ("procurement", "procurement_products", "created_at", "创建时间。"),

    ("procurement", "procurement_product_variants", "id", "主键。"),
    ("procurement", "procurement_product_variants", "procurement_product_id", "所属商品;FK。"),
    ("procurement", "procurement_product_variants", "external_variant_id", "平台 SKU ID;联合 procurement_product_id 唯一。"),
    ("procurement", "procurement_product_variants", "sku_code", "内部 SKU 编码(采购用,通常 = 'SPUCODE-001')。"),
    ("procurement", "procurement_product_variants", "variant_name", "变体名(如 '红色/M')。"),
    ("procurement", "procurement_product_variants", "attributes", "变体属性 JSON。"),
    ("procurement", "procurement_product_variants", "image_url", "变体图。"),
    ("procurement", "procurement_product_variants", "purchase_price", "进货价(numeric(20,4),本币种)。"),
    ("procurement", "procurement_product_variants", "currency", "本币种(与 TikTok 销售币种可能不同)。"),
    ("procurement", "procurement_product_variants", "status", "变体状态。"),
    ("procurement", "procurement_product_variants", "source_updated_at", "上游 SKU 最近更新时间。"),
    ("procurement", "procurement_product_variants", "raw_record_id", "关联 raw_records。"),
    ("procurement", "procurement_product_variants", "synced_at", "本地首次入库时间。"),
    ("procurement", "procurement_product_variants", "updated_at", "最近修改。"),
    ("procurement", "procurement_product_variants", "created_at", "创建时间。"),

    ("procurement", "purchase_orders", "id", "主键。"),
    ("procurement", "purchase_orders", "procurement_account_id", "采购账号;FK。"),
    ("procurement", "purchase_orders", "order_id", "妙手/平台采购单号;联合 procurement_account_id 唯一。"),
    ("procurement", "purchase_orders", "supplier_name", "供应商名称(可能 = procurement_account.account_name)。"),
    ("procurement", "purchase_orders", "status", "采购单状态:PENDING / CONFIRMED / SHIPPED / RECEIVED / CANCELLED。"),
    ("procurement", "purchase_orders", "total_amount", "采购单总金额。"),
    ("procurement", "purchase_orders", "currency", "币种。"),
    ("procurement", "purchase_orders", "ordered_at", "下单时间。"),
    ("procurement", "purchase_orders", "expected_at", "预计到货时间。"),
    ("procurement", "purchase_orders", "received_at", "实际收货时间(可能 NULL = 还没到)。"),
    ("procurement", "purchase_orders", "raw_record_id", "关联 raw_records。"),
    ("procurement", "purchase_orders", "synced_at", "本地首次入库时间。"),
    ("procurement", "purchase_orders", "updated_at", "最近修改。"),
    ("procurement", "purchase_orders", "created_at", "创建时间。"),

    ("procurement", "purchase_order_lines", "id", "主键。"),
    ("procurement", "purchase_order_lines", "purchase_order_id", "所属采购单;FK。"),
    ("procurement", "purchase_order_lines", "external_line_id", "妙手/平台采购单行号;联合 purchase_order_id 唯一。"),
    ("procurement", "purchase_order_lines", "procurement_product_id", "采购商品 SPU;FK。"),
    ("procurement", "purchase_order_lines", "procurement_product_variant_id", "采购 SKU;FK。"),
    ("procurement", "purchase_order_lines", "quantity", "采购数量(numeric)。"),
    ("procurement", "purchase_order_lines", "unit_cost", "进货单价(用于 reporting 算利润;关键财务字段)。"),
    ("procurement", "purchase_order_lines", "currency", "币种。"),
    ("procurement", "purchase_order_lines", "raw_record_id", "关联 raw_records。"),
    ("procurement", "purchase_order_lines", "synced_at", "本地首次入库时间。"),
    ("procurement", "purchase_order_lines", "updated_at", "最近修改。"),
    ("procurement", "purchase_order_lines", "created_at", "创建时间。"),

    ("procurement", "manual_product_costs", "id", "主键。"),
    ("procurement", "manual_product_costs", "procurement_product_id", "采购 SPU;FK;unique(每个 SPU 一条手工成本)。"),
    ("procurement", "manual_product_costs", "unit_cost", "手工覆盖的进货单价(优先级高于妙手拉取的实时值)。"),
    ("procurement", "manual_product_costs", "currency", "币种。"),
    ("procurement", "manual_product_costs", "reason", "覆盖理由(为什么用人工值,例如'促销价' / '包邮口径')。"),
    ("procurement", "manual_product_costs", "created_by", "创建人 username。"),
    ("procurement", "manual_product_costs", "effective_from", "生效开始时间。"),
    ("procurement", "manual_product_costs", "effective_to", "失效时间;NULL = 永久。"),
    ("procurement", "manual_product_costs", "raw_record_id", "关联 raw_records(可空)。"),
    ("procurement", "manual_product_costs", "updated_at", "最近修改。"),
    ("procurement", "manual_product_costs", "created_at", "创建时间。"),

    ("procurement", "spu_images", "id", "主键。"),
    ("procurement", "spu_images", "procurement_product_id", "采购 SPU;FK。"),
    ("procurement", "spu_images", "image_url", "图片 URL(可能是主图或副图)。"),
    ("procurement", "spu_images", "image_role", "图片角色:main / detail / spec;用于页面渲染。"),
    ("procurement", "spu_images", "sort_order", "排序权重(数字越小越靠前)。"),
    ("procurement", "spu_images", "raw_record_id", "关联 raw_records。"),
    ("procurement", "spu_images", "updated_at", "最近修改。"),
    ("procurement", "spu_images", "created_at", "创建时间。"),

    # ─── after_sales ──────────────────────────────────────────
    ("after_sales", "cases", "id", "主键 bigint identity。"),
    ("after_sales", "cases", "shop_pk", "所属 TikTok shop;FK。"),
    ("after_sales", "cases", "external_case_id", "TikTok 售后单 ID;联合 shop_pk 唯一。"),
    ("after_sales", "cases", "order_pk", "关联原始订单;FK(可空,可能售后单没关联订单)。"),
    ("after_sales", "cases", "case_type", "售后类型:REFUND / RETURN / EXCHANGE / COMPLAINT。"),
    ("after_sales", "cases", "status", "状态:OPEN / PROCESSING / CLOSED_APPROVED / CLOSED_REJECTED / CANCELLED。"),
    ("after_sales", "cases", "reason", "买家申请原因(自由文本)。"),
    ("after_sales", "cases", "description", "详细描述(可含图片 URL 列表)。"),
    ("after_sales", "cases", "refund_amount", "退款金额。"),
    ("after_sales", "cases", "currency", "退款币种。"),
    ("after_sales", "cases", "opened_at", "申请时间。"),
    ("after_sales", "cases", "closed_at", "关闭时间(可空,未关闭)。"),
    ("after_sales", "cases", "raw_record_id", "关联 raw_records。"),
    ("after_sales", "cases", "synced_at", "本地首次入库时间。"),
    ("after_sales", "cases", "updated_at", "最近修改。"),
    ("after_sales", "cases", "created_at", "创建时间(通常 = synced_at)。"),

    ("after_sales", "case_lines", "id", "主键。"),
    ("after_sales", "case_lines", "case_id", "所属售后单;FK。"),
    ("after_sales", "case_lines", "external_line_id", "TikTok 售后单行号。"),
    ("after_sales", "case_lines", "sales_order_line_id", "关联原始订单行;FK。"),
    ("after_sales", "case_lines", "spu_pk", "关联 SPU;FK。"),
    ("after_sales", "case_lines", "sku_pk", "关联 SKU;FK。"),
    ("after_sales", "case_lines", "quantity", "售后件数。"),
    ("after_sales", "case_lines", "line_status", "行级状态:PENDING / APPROVED / REJECTED / COMPLETED。"),
    ("after_sales", "case_lines", "raw_record_id", "关联 raw_records。"),
    ("after_sales", "case_lines", "updated_at", "最近修改。"),
    ("after_sales", "case_lines", "created_at", "创建时间。"),

    # ─── finance ─────────────────────────────────────────────
    ("finance", "payouts", "id", "主键。"),
    ("finance", "payouts", "shop_pk", "所属 TikTok shop;FK。"),
    ("finance", "payouts", "external_payout_id", "TikTok 打款单号;联合 shop_pk 唯一。"),
    ("finance", "payouts", "amount", "打款总金额。"),
    ("finance", "payouts", "currency", "币种。"),
    ("finance", "payouts", "status", "状态:PENDING / PAID / FAILED。"),
    ("finance", "payouts", "paid_at", "实际打款时间(可能为 NULL = 待打款)。"),
    ("finance", "payouts", "expected_at", "预计打款时间。"),
    ("finance", "payouts", "raw_record_id", "关联 raw_records。"),
    ("finance", "payouts", "synced_at", "本地首次入库时间。"),
    ("finance", "payouts", "updated_at", "最近修改。"),
    ("finance", "payouts", "created_at", "创建时间。"),

    ("finance", "settlement_statements", "id", "主键。"),
    ("finance", "settlement_statements", "shop_pk", "所属 shop;FK。"),
    ("finance", "settlement_statements", "external_statement_id", "TikTok 结算单号。"),
    ("finance", "settlement_statements", "period_start", "结算周期开始(包含)。"),
    ("finance", "settlement_statements", "period_end", "结算周期结束(包含)。"),
    ("finance", "settlement_statements", "gross_amount", "毛收入(订单 GMV 总和)。"),
    ("finance", "settlement_statements", "fees_amount", "平台抽佣。"),
    ("finance", "settlement_statements", "refunds_amount", "退款金额(负数)。"),
    ("finance", "settlement_statements", "net_amount", "净结算金额(gross - fees + refunds - adjustments)。"),
    ("finance", "settlement_statements", "currency", "币种。"),
    ("finance", "settlement_statements", "status", "状态:PENDING / FINALIZED / PAID。"),
    ("finance", "settlement_statements", "finalized_at", "结算单确认时间(转 FINALIZED 时)。"),
    ("finance", "settlement_statements", "raw_record_id", "关联 raw_records。"),
    ("finance", "settlement_statements", "synced_at", "本地首次入库时间。"),
    ("finance", "settlement_statements", "updated_at", "最近修改。"),
    ("finance", "settlement_statements", "created_at", "创建时间。"),

    ("finance", "settlement_transactions", "id", "主键。"),
    ("finance", "settlement_transactions", "settlement_statement_id", "所属结算单;FK。"),
    ("finance", "settlement_transactions", "order_pk", "关联订单;FK,SET NULL(订单删除保留结算)。"),
    ("finance", "settlement_transactions", "external_transaction_id", "TikTok 结算明细号。"),
    ("finance", "settlement_transactions", "transaction_type", "类型:ORDER / REFUND / FEE / ADJUSTMENT。"),
    ("finance", "settlement_transactions", "amount", "金额(正数 = 收入,负数 = 退款/费用)。"),
    ("finance", "settlement_transactions", "currency", "币种。"),
    ("finance", "settlement_transactions", "external_payment_id", "TikTok 支付号(用于追溯到打款);**可能缺失**,见 sync_issues STATEMENT_PAYMENT_ID_MISSING。"),
    ("finance", "settlement_transactions", "occurred_at", "交易发生时间。"),
    ("finance", "settlement_transactions", "raw_record_id", "关联 raw_records。"),
    ("finance", "settlement_transactions", "synced_at", "本地首次入库时间。"),
    ("finance", "settlement_transactions", "updated_at", "最近修改。"),
    ("finance", "settlement_transactions", "created_at", "创建时间。"),

    ("finance", "settlement_components", "id", "主键。"),
    ("finance", "settlement_components", "settlement_statement_id", "所属结算单;FK。"),
    ("finance", "settlement_components", "component_type", "成分类型:PLATFORM_FEE / PAYMENT_FEE / PROMOTION_DISCOUNT / SHIPPING_FEE / TAX。"),
    ("finance", "settlement_components", "amount", "金额(可正可负)。"),
    ("finance", "settlement_components", "currency", "币种。"),
    ("finance", "settlement_components", "raw_record_id", "关联 raw_records。"),
    ("finance", "settlement_components", "updated_at", "最近修改。"),
    ("finance", "settlement_components", "created_at", "创建时间。"),

    # ─── fulfillment ─────────────────────────────────────────
    ("fulfillment", "shipments", "id", "主键。"),
    ("fulfillment", "shipments", "order_pk", "关联订单;FK。"),
    ("fulfillment", "shipments", "shop_pk", "所属 shop;FK(冗余,便于按 shop 查)。"),
    ("fulfillment", "shipments", "external_shipment_id", "TikTok 运单号;联合 shop_pk 唯一。"),
    ("fulfillment", "shipments", "carrier", "物流商:TikTok shipping / J&T / 顺丰 / etc。"),
    ("fulfillment", "shipments", "tracking_number", "物流单号(由 carrier 分配,可能 NULL = 还没发货)。"),
    ("fulfillment", "shipments", "status", "运单状态:CREATED / PICKED_UP / IN_TRANSIT / DELIVERED / EXCEPTION / RETURNED。"),
    ("fulfillment", "shipments", "shipped_at", "出库时间。"),
    ("fulfillment", "shipments", "delivered_at", "签收时间(可能 NULL)。"),
    ("fulfillment", "shipments", "raw_record_id", "关联 raw_records。"),
    ("fulfillment", "shipments", "synced_at", "本地首次入库时间。"),
    ("fulfillment", "shipments", "updated_at", "最近修改。"),
    ("fulfillment", "shipments", "created_at", "创建时间。"),

    ("fulfillment", "shipment_lines", "id", "主键。"),
    ("fulfillment", "shipment_lines", "shipment_id", "所属运单;FK。"),
    ("fulfillment", "shipment_lines", "sales_order_line_id", "关联订单行;FK。"),
    ("fulfillment", "shipment_lines", "quantity", "发货数量。"),
    ("fulfillment", "shipment_lines", "raw_record_id", "关联 raw_records。"),
    ("fulfillment", "shipment_lines", "updated_at", "最近修改。"),
    ("fulfillment", "shipment_lines", "created_at", "创建时间。"),

    ("fulfillment", "tracking_events", "id", "主键。"),
    ("fulfillment", "tracking_events", "shipment_id", "所属运单;FK。"),
    ("fulfillment", "tracking_events", "external_event_id", "物流商事件 ID(去重用,联合 shipment_id unique)。"),
    ("fulfillment", "tracking_events", "status", "事件状态:同 shipments.status 的子集。"),
    ("fulfillment", "tracking_events", "location", "事件发生地点(自由文本,如 '深圳宝安转运中心')。"),
    ("fulfillment", "tracking_events", "description", "事件描述(自由文本)。"),
    ("fulfillment", "tracking_events", "occurred_at", "事件实际发生时间(物流商时间戳)。"),
    ("fulfillment", "tracking_events", "raw_record_id", "关联 raw_records。"),
    ("fulfillment", "tracking_events", "synced_at", "本地首次入库时间。"),
    ("fulfillment", "tracking_events", "updated_at", "最近修改。"),
    ("fulfillment", "tracking_events", "created_at", "创建时间。"),

    # ─── reporting ────────────────────────────────────────────
    ("reporting", "product_cost_snapshots", "id", "主键。"),
    ("reporting", "product_cost_snapshots", "spu_pk", "TikTok SPU;FK;unique(snapshop 时段+SPU 唯一)。"),
    ("reporting", "product_cost_snapshots", "day", "快照日期 yyyy-mm-dd(per cost_snapshots job,每 6h 跑一次)。"),
    ("reporting", "product_cost_snapshots", "unit_cost", "生效的单位进货价(从 purchase_order_lines 取最新)。"),
    ("reporting", "product_cost_snapshots", "currency", "币种(与 purchase_order_lines.unit_cost 一致)。"),
    ("reporting", "product_cost_snapshots", "source", "成本来源:'purchase_order' / 'manual_override' / 'fallback_default'。"),
    ("reporting", "product_cost_snapshots", "raw_record_id", "关联 raw_records(可能为手工覆盖则 NULL)。"),
    ("reporting", "product_cost_snapshots", "updated_at", "最近重算时间(每 6h 刷一次)。"),
    ("reporting", "product_cost_snapshots", "created_at", "首次快照时间。"),

    ("reporting", "product_profit_daily", "id", "主键。"),
    ("reporting", "product_profit_daily", "spu_pk", "TikTok SPU;FK;unique(SPU+day)。"),
    ("reporting", "product_profit_daily", "day", "yyyy-mm-dd 聚合粒度。"),
    ("reporting", "product_profit_daily", "shop_pk", "所属 shop(冗余,便于按 shop 过滤);FK。"),
    ("reporting", "product_profit_daily", "campaign_id", "TikTok 广告计划(可空:有自然流量订单时);FK。"),
    ("reporting", "product_profit_daily", "orders_count", "当日订单数(distinct 订单头数)。"),
    ("reporting", "product_profit_daily", "units_sold", "当日销售件数(distinct 订单行 quantity 求和)。"),
    ("reporting", "product_profit_daily", "gross_revenue", "总 GMV(未扣平台费)。"),
    ("reporting", "product_profit_daily", "platform_fees", "平台抽佣 + 支付费。"),
    ("reporting", "product_profit_daily", "refunds", "退款金额(负数)。"),
    ("reporting", "product_profit_daily", "net_revenue", "净收入(gross - fees + refunds)。"),
    ("reporting", "product_profit_daily", "cogs", "销货成本(来自 purchase_order_lines.unit_cost * quantity)。"),
    ("reporting", "product_profit_daily", "ad_cost", "广告消耗(来自 analytics.ad_records.mixed_real_cost)。"),
    ("reporting", "product_profit_daily", "gross_profit", "毛利(net_revenue - cogs)。"),
    ("reporting", "product_profit_daily", "net_profit", "净利(gross_profit - ad_cost)。"),
    ("reporting", "product_profit_daily", "roi", "投资回报率 = net_profit / ad_cost,可空(无广告消耗时)。"),
    ("reporting", "product_profit_daily", "currency", "币种。"),
    ("reporting", "product_profit_daily", "raw_record_id", "关联 raw_records。"),
    ("reporting", "product_profit_daily", "updated_at", "最近重算(每 1h 跑一次)。"),
    ("reporting", "product_profit_daily", "created_at", "首次写入。"),

    ("reporting", "shipment_tracking_summary", "id", "主键。"),
    ("reporting", "shipment_tracking_summary", "day", "yyyy-mm-dd 聚合日。"),
    ("reporting", "shipment_tracking_summary", "shop_pk", "所属 shop;FK。"),
    ("reporting", "shipment_tracking_summary", "carrier", "物流商。"),
    ("reporting", "shipment_tracking_summary", "shipments_total", "当日总运单数。"),
    ("reporting", "shipment_tracking_summary", "shipments_in_transit", "在途数。"),
    ("reporting", "shipment_tracking_summary", "shipments_delivered", "已签收数。"),
    ("reporting", "shipment_tracking_summary", "shipments_exception", "异常运单数(用于 SLA 监控)。"),
    ("reporting", "shipment_tracking_summary", "avg_delivery_hours", "平均签收时长(小时,created → delivered 间隔)。"),
    ("reporting", "shipment_tracking_summary", "raw_record_id", "关联 raw_records。"),
    ("reporting", "shipment_tracking_summary", "updated_at", "最近重算。"),
    ("reporting", "shipment_tracking_summary", "created_at", "首次写入。"),

    # ─── security ─────────────────────────────────────────────
    ("security", "api_keys", "id", "主键。"),
    ("security", "api_keys", "key_prefix", "API key 字符串前缀(明文),用于人工识别(整 key 哈希存储)。"),
    ("security", "api_keys", "name", "人工可读名称(例如 'BI dashboard')。"),
    ("security", "api_keys", "role", "角色:readonly < readwrite < admin,per middleware/auth.py::required_role()。"),
    ("security", "api_keys", "scopes", "授权范围 JSON:{'seller_id': [...], 'advertiser_id': [...], '*': ...};空对象 = 无 scope 限制(最高权限)。"),
    ("security", "api_keys", "created_by", "创建人 username(可空 system 创建)。"),
    ("security", "api_keys", "last_used_at", "最近一次使用时间(每次 auth 中间件更新)。"),
    ("security", "api_keys", "expires_at", "过期时间;NULL = 永不过期。"),
    ("security", "api_keys", "revoked_at", "撤销时间;NULL = 仍有效。"),
    ("security", "api_keys", "created_at", "创建时间。"),
    ("security", "api_keys", "updated_at", "最近修改(例如修改 scopes / role 时刷新)。"),
]


def build_comments() -> str:
    """从 live DB 拿所有 v2 列,结合 COMMENTS 字典拼出完整 COMMENT。

    - 关键列(在 COMMENTS 字典里):精确业务语义
    - 其他列:按类型推导的通用描述
    - 缺精写语义的列:后面手动在 COMMENTS 字典补充
    """
    import subprocess

    # 从 DB 拿所有 v2 列
    result = subprocess.check_output(
        "docker exec postgres psql -U postgres -d tts_erp -P pager=off -A -t -F'|' -c "
        "\"SELECT table_schema, table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema IN ('commerce','analytics','integration','linkage',"
        "'procurement','after_sales','finance','fulfillment','reporting','security') "
        "AND table_name <> 'effective_product_links'  -- view skip "
        "ORDER BY table_schema, table_name, ordinal_position\"",
        shell=True, text=True,
    )

    # 索引精确语义
    precise = {(s, t, c): txt for s, t, c, txt in COMMENTS}

    # 实际表+列名(DB 真实存在,用于过滤 COMMENTS 里的过期/错列名)
    actual_columns: dict[tuple[str, str, str], tuple[str, str]] = {}
    for line in result.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) < 5:
            continue
        actual_columns[(parts[0], parts[1], parts[2])] = (parts[3], parts[4])

    by_table: dict[tuple[str, str], list[tuple[str, str, str, str, str]]] = {}
    for (schema, table, col), (dtype, nullable) in sorted(actual_columns.items()):
        key = (schema, table, col)
        if key in precise:
            comment = precise[key]
            is_precise = True
        else:
            comment = _infer_comment(col, dtype, nullable, schema, table)
            is_precise = False
        by_table.setdefault((schema, table), []).append((col, dtype, nullable, is_precise, comment))

    lines = ["-- 3. COMMENT ON COLUMN(关键列精写 + 其他列按类型推导通用描述)\n"]
    for (sch, tbl), cols in sorted(by_table.items()):
        lines.append(f"\n-- {sch}.{tbl}\n")
        for col, dtype, nullable, is_precise, comment in cols:
            safe = comment.replace("'", "''")
            tag = "" if is_precise else "  -- inferred"
            lines.append(
                f"COMMENT ON COLUMN {sch}.{tbl}.{col} IS '{safe}';{tag}\n"
            )

    return "".join(lines) + "\n"


def _infer_comment(col: str, dtype: str, nullable: str, schema: str, table: str) -> str:
    """按列名 + 类型推导通用描述(对关键列需手写精写语义)。"""
    cn = col.lower()
    dt = dtype.lower()
    nn = "" if nullable.upper() == "YES" else " NOT NULL"

    if cn == "id":
        return f"主键,系统内部 {('bigint identity' if 'bigint' in dt else 'int identity')}{nn}。"
    if cn.endswith("_id") and cn not in ("order_id", "external_account_id", "external_product_id", "external_case_id", "external_case_line_id", "external_shipment_id", "external_statement_id", "external_transaction_id", "external_payout_id", "external_payment_id", "external_relation_id", "external_line_id", "external_event_id", "external_purchase_order_id", "external_event_key", "external_package_id", "external_variant_id", "external_purchase_id", "external_id", "external_role_id"):
        # FK to another table
        ref = cn[:-3]  # strip _id
        return f"外键,引用 {ref}.id(级联策略见 ALTER TABLE){nn}。"
    if cn in ("created_at", "synced_at", "captured_at", "received_at", "started_at", "decided_at", "detected_at", "finalized_at", "computed_at", "calculated_at", "occurred_at", "event_at", "uploaded_at", "observed_at", "transaction_time", "shipped_at", "delivered_at", "ordered_at", "expected_at", "received_at", "paid_at", "closed_at", "opened_at", "modified_at", "last_used_at", "expires_at", "revoked_at", "effective_from", "effective_to", "valid_from", "valid_to", "completed_at", "resumed_at", "statement_time", "first_event_at", "last_event_at", "source_created_at", "source_updated_at", "source_modified_at", "gmt_create", "gmt_modified", "decided_at", "decision_at", "occurred_at", "extracted_at", "raw_record_at"):
        return f"时间戳字段{nn}(per ADR-0001 双时间字段约定)。"
    if cn == "updated_at":
        return f"最近一次修改时间(由 BEFORE UPDATE trigger public.fn_touch_updated_at() 自动维护,per ADR-0001){nn}。"
    if cn in ("source", "source_kind", "data_source", "provider"):
        return f"数据来源标识(自由文本,用于追溯{nn})。"
    if cn in ("status", "state", "case_status", "line_status", "fulfillment_status"):
        return f"状态字段(枚举值见业务文档,默认/约束见 CHECK){nn}。"
    if cn in ("amount", "total_amount", "payment_amount", "gross_amount", "net_amount", "fees_amount", "refunds_amount", "unit_cost", "purchase_price", "refund_amount", "gross_revenue", "platform_fees", "refunds", "net_revenue", "cogs", "ad_cost", "gross_profit", "net_profit"):
        return f"金额字段(numeric(20,4) 避免浮点,币种见 currency 列){nn}。"
    if cn in ("quantity", "units_sold", "orders_count", "shipments_total", "shipments_in_transit", "shipments_delivered", "shipments_exception", "rows_total", "rows_inserted", "rows_updated", "rows_failed", "count", "event_count"):
        return f"数量/计数字段(整数或 numeric){nn}。"
    if cn in ("currency",):
        return f"ISO 4217 货币代码(VND/USD/THB 等){nn}。"
    if cn in ("url", "image_url", "main_image_url", "object_key", "filename", "content_type", "source_image", "source_url", "raw_payload"):
        return f"URL/资源标识{nn}。"
    if cn.endswith("_url"):
        return f"URL 字段(可能含 CDN 签名 token,会过期){nn}。"
    if "jsonb" in dt:
        return f"JSON 结构化字段(jsonb 原样存,不做规范化){nn}。"
    if cn in ("notes", "note", "description", "details", "reason", "reason_text", "context", "raw_payload", "extra", "error_message"):
        return f"文本描述/详情字段(free-form,长文本){nn}。"
    if cn in ("name", "title", "label", "account_name", "supplier_name", "variant_name", "product_name_snapshot", "title_snapshot", "filename", "shop_name", "account_label", "spu_code", "sku_code", "seller_sku"):
        return f"名称/标题字段(可含 unicode / emoji){nn}。"
    if cn.endswith("_at"):
        return f"时间戳字段{nn}。"
    if cn.endswith("_by"):
        return f"创建/操作人(用户名){nn}。"
    if cn in ("payload", "raw_payload", "raw_metadata", "raw_body", "request", "response"):
        return f"原始 JSON payload(jsonb 存,server 不做字段过滤){nn}。"
    if "bool" in dt or "boolean" in dt:
        return f"布尔字段{nn}。"
    if "numeric" in dt or "decimal" in dt:
        return f"数值字段(numeric/decimal){nn}。"
    if "timestamp" in dt or "timestamptz" in dt:
        return f"时间戳字段{nn}。"
    if "int" in dt or "bigint" in dt or "smallint" in dt:
        return f"整数字段{nn}。"
    if "text" in dt or "varchar" in dt or "char" in dt or "bytea" in dt:
        return f"文本字段{nn}。"
    return f"字段{nn}。"




def main() -> int:
    out = Path("tts_erp_v2/db/migrations/0001_add_time_fields.sql")
    sql = (
        HEADER
        + TRIGGER_FN
        + build_table_alters()
        + build_comments()
    )
    out.write_text(sql, encoding="utf-8")
    print(f"wrote {out} ({len(sql):,} bytes, {len(COMMENTS)} column comments, "
          f"{len(TABLE_ALTERS)} table alters)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
