-- =============================================================================
-- Migration 0001: 双时间字段约定(ADR-0001)
-- =============================================================================
-- Adds created_at / updated_at columns to v2 tables + generic BEFORE UPDATE
-- trigger (public.fn_touch_updated_at) for auto-maintenance. Adds business-
-- semantic COMMENT ON COLUMN for every v2 table column.
--
-- Strategy: idempotent (IF NOT EXISTS / OR REPLACE); re-runnable on populated
-- DB. Per ADR-0001 §5 user authorization: no gray release, direct switch.
-- =============================================================================


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

-- 2. ALTER TABLE: 加 updated_at / created_at + BEFORE UPDATE trigger

CREATE OR REPLACE TRIGGER trg_integration_credentials_touch
  BEFORE UPDATE ON integration.credentials
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

CREATE OR REPLACE TRIGGER trg_linkage_product_links_touch
  BEFORE UPDATE ON linkage.product_links
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE after_sales.cases ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_after_sales_cases_touch
  BEFORE UPDATE ON after_sales.cases
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE analytics.ad_audit_log ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_analytics_ad_audit_log_touch
  BEFORE UPDATE ON analytics.ad_audit_log
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE analytics.ad_daily_completeness ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_analytics_ad_daily_completeness_touch
  BEFORE UPDATE ON analytics.ad_daily_completeness
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE analytics.ad_raw ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_analytics_ad_raw_touch
  BEFORE UPDATE ON analytics.ad_raw
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE analytics.ad_records ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_analytics_ad_records_touch
  BEFORE UPDATE ON analytics.ad_records
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE commerce.channel_accounts ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_commerce_channel_accounts_touch
  BEFORE UPDATE ON commerce.channel_accounts
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE commerce.channel_product_variants ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_commerce_channel_product_variants_touch
  BEFORE UPDATE ON commerce.channel_product_variants
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE commerce.channel_products ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_commerce_channel_products_touch
  BEFORE UPDATE ON commerce.channel_products
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE commerce.sales_order_lines ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_commerce_sales_order_lines_touch
  BEFORE UPDATE ON commerce.sales_order_lines
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE commerce.sales_orders ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_commerce_sales_orders_touch
  BEFORE UPDATE ON commerce.sales_orders
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE finance.payouts ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_finance_payouts_touch
  BEFORE UPDATE ON finance.payouts
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE finance.settlement_statements ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_finance_settlement_statements_touch
  BEFORE UPDATE ON finance.settlement_statements
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE finance.settlement_transactions ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_finance_settlement_transactions_touch
  BEFORE UPDATE ON finance.settlement_transactions
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE fulfillment.shipments ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_fulfillment_shipments_touch
  BEFORE UPDATE ON fulfillment.shipments
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE fulfillment.tracking_events ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_fulfillment_tracking_events_touch
  BEFORE UPDATE ON fulfillment.tracking_events
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE integration.raw_records ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_integration_raw_records_touch
  BEFORE UPDATE ON integration.raw_records
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE linkage.link_issues ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_linkage_link_issues_touch
  BEFORE UPDATE ON linkage.link_issues
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE linkage.link_overrides ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_linkage_link_overrides_touch
  BEFORE UPDATE ON linkage.link_overrides
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.manual_product_costs ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_manual_product_costs_touch
  BEFORE UPDATE ON procurement.manual_product_costs
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.procurement_accounts ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_procurement_accounts_touch
  BEFORE UPDATE ON procurement.procurement_accounts
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.procurement_product_variants ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_procurement_product_variants_touch
  BEFORE UPDATE ON procurement.procurement_product_variants
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.procurement_products ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_procurement_products_touch
  BEFORE UPDATE ON procurement.procurement_products
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.purchase_order_lines ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_purchase_order_lines_touch
  BEFORE UPDATE ON procurement.purchase_order_lines
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.purchase_orders ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_purchase_orders_touch
  BEFORE UPDATE ON procurement.purchase_orders
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE security.api_keys ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_security_api_keys_touch
  BEFORE UPDATE ON security.api_keys
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE analytics.ad_shop_timezones ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_analytics_ad_shop_timezones_touch
  BEFORE UPDATE ON analytics.ad_shop_timezones
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE integration.sync_cursors ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_integration_sync_cursors_touch
  BEFORE UPDATE ON integration.sync_cursors
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE after_sales.case_lines ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE after_sales.case_lines ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_after_sales_case_lines_touch
  BEFORE UPDATE ON after_sales.case_lines
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE finance.settlement_components ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE finance.settlement_components ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_finance_settlement_components_touch
  BEFORE UPDATE ON finance.settlement_components
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE fulfillment.shipment_lines ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE fulfillment.shipment_lines ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_fulfillment_shipment_lines_touch
  BEFORE UPDATE ON fulfillment.shipment_lines
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE integration.sync_issues ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE integration.sync_issues ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_integration_sync_issues_touch
  BEFORE UPDATE ON integration.sync_issues
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE integration.sync_jobs ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE integration.sync_jobs ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_integration_sync_jobs_touch
  BEFORE UPDATE ON integration.sync_jobs
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE linkage.account_links ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE linkage.account_links ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_linkage_account_links_touch
  BEFORE UPDATE ON linkage.account_links
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE linkage.link_evidence ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE linkage.link_evidence ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_linkage_link_evidence_touch
  BEFORE UPDATE ON linkage.link_evidence
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE linkage.variant_links ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE linkage.variant_links ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_linkage_variant_links_touch
  BEFORE UPDATE ON linkage.variant_links
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE procurement.spu_images ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE procurement.spu_images ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_procurement_spu_images_touch
  BEFORE UPDATE ON procurement.spu_images
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE reporting.product_cost_snapshots ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE reporting.product_cost_snapshots ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_reporting_product_cost_snapshots_touch
  BEFORE UPDATE ON reporting.product_cost_snapshots
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE reporting.product_profit_daily ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE reporting.product_profit_daily ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_reporting_product_profit_daily_touch
  BEFORE UPDATE ON reporting.product_profit_daily
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();

ALTER TABLE reporting.shipment_tracking_summary ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();

ALTER TABLE reporting.shipment_tracking_summary ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

CREATE OR REPLACE TRIGGER trg_reporting_shipment_tracking_summary_touch
  BEFORE UPDATE ON reporting.shipment_tracking_summary
  FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at();


-- 3. COMMENT ON COLUMN(关键列精写 + 其他列按类型推导通用描述)

-- after_sales.case_lines
COMMENT ON COLUMN after_sales.case_lines.case_id IS '所属售后单;FK。';
COMMENT ON COLUMN after_sales.case_lines.created_at IS '创建时间。';
COMMENT ON COLUMN after_sales.case_lines.currency IS 'ISO 4217 货币代码(VND/USD/THB 等)。';  -- inferred
COMMENT ON COLUMN after_sales.case_lines.external_case_line_id IS '文本字段。';  -- inferred
COMMENT ON COLUMN after_sales.case_lines.id IS '主键。';
COMMENT ON COLUMN after_sales.case_lines.quantity IS '售后件数。';
COMMENT ON COLUMN after_sales.case_lines.refund_amount IS '金额字段(numeric(20,4) 避免浮点,币种见 currency 列)。';  -- inferred
COMMENT ON COLUMN after_sales.case_lines.sales_order_line_id IS '关联原始订单行;FK。';
COMMENT ON COLUMN after_sales.case_lines.should_replenish_stock IS '布尔字段。';  -- inferred
COMMENT ON COLUMN after_sales.case_lines.updated_at IS '最近修改。';

-- after_sales.cases
COMMENT ON COLUMN after_sales.cases.case_type IS '售后类型:REFUND / RETURN / EXCHANGE / COMPLAINT。';
COMMENT ON COLUMN after_sales.cases.channel_account_id IS '所属 TikTok shop;FK。';
COMMENT ON COLUMN after_sales.cases.created_at_source IS '时间戳字段。';  -- inferred
COMMENT ON COLUMN after_sales.cases.currency IS '退款币种。';
COMMENT ON COLUMN after_sales.cases.external_case_id IS 'TikTok 售后单 ID;联合 channel_account_id 唯一。';
COMMENT ON COLUMN after_sales.cases.id IS '主键 bigint identity。';
COMMENT ON COLUMN after_sales.cases.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN after_sales.cases.reason_code IS '文本字段。';  -- inferred
COMMENT ON COLUMN after_sales.cases.reason_text IS '文本描述/详情字段(free-form,长文本)。';  -- inferred
COMMENT ON COLUMN after_sales.cases.refund_amount IS '退款金额。';
COMMENT ON COLUMN after_sales.cases.sales_order_id IS '关联原始订单;FK(可空,可能售后单没关联订单)。';
COMMENT ON COLUMN after_sales.cases.status IS '状态:OPEN / PROCESSING / CLOSED_APPROVED / CLOSED_REJECTED / CANCELLED。';
COMMENT ON COLUMN after_sales.cases.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN after_sales.cases.updated_at IS '最近修改。';
COMMENT ON COLUMN after_sales.cases.updated_at_source IS '时间戳字段。';  -- inferred

-- analytics.ad_audit_log
COMMENT ON COLUMN analytics.ad_audit_log.created_at IS '日志行创建时间(等同收到请求的时间,per created_at 列默认 now())。';
COMMENT ON COLUMN analytics.ad_audit_log.endpoint IS 'v2 端点名:''dumps'' / ''cursor''(per tech-doc/analytics/dump-architecture.md)。';
COMMENT ON COLUMN analytics.ad_audit_log.error_code IS '业务错误码:MALFORMED_JSON / SCHEMA_INVALID / SCOPE_DENIED / PAYLOAD_TOO_LARGE 等。';
COMMENT ON COLUMN analytics.ad_audit_log.error_message IS '错误详情文本(脱敏后,前 240 字符,per sanitizeDiagnosticText)。';
COMMENT ON COLUMN analytics.ad_audit_log.id IS '主键 bigint identity。';
COMMENT ON COLUMN analytics.ad_audit_log.key_prefix IS 'API key 前缀(明文 key 永不落库,只存 prefix 用于识别 client)。';
COMMENT ON COLUMN analytics.ad_audit_log.method IS 'HTTP method(GET cursor / POST dumps)。';
COMMENT ON COLUMN analytics.ad_audit_log.path IS '完整请求 path(含 query string),用于排障重现。';
COMMENT ON COLUMN analytics.ad_audit_log.records_in IS '本请求输入的记录数(对 dumps 协议 = 1)。';
COMMENT ON COLUMN analytics.ad_audit_log.records_ok IS '本请求成功写入的记录数。';
COMMENT ON COLUMN analytics.ad_audit_log.records_rej IS '本请求被拒绝的记录数(校验失败)。';
COMMENT ON COLUMN analytics.ad_audit_log.request_id IS '请求链路 ID(uuid,跨表追踪)。';
COMMENT ON COLUMN analytics.ad_audit_log.status IS 'HTTP 响应状态码(200 / 400 / 413 / 500 等)。';
COMMENT ON COLUMN analytics.ad_audit_log.updated_at IS '最近一次修改(trigger 自动维护;正常只 INSERT,UPDATE 罕见)。';

-- analytics.ad_daily_completeness
COMMENT ON COLUMN analytics.ad_daily_completeness.advertiser_id IS 'TikTok advertiser_id。';
COMMENT ON COLUMN analytics.ad_daily_completeness.campaign_id IS 'TikTok 广告计划 ID;5 元组 unique 的一部分。';
COMMENT ON COLUMN analytics.ad_daily_completeness.captured_at IS '最近一次 captured 时间(dumps 协议下 = upsert 时间,语义 = ''最近一次抓过'')。';
COMMENT ON COLUMN analytics.ad_daily_completeness.day IS 'yyyy-mm-dd;5 元组 unique 的一部分。';
COMMENT ON COLUMN analytics.ad_daily_completeness.seller_id IS 'TikTok shop_id;5 元组 unique 的一部分。';
COMMENT ON COLUMN analytics.ad_daily_completeness.storage_key IS 'productAnalyses / sessionAnalyses / campaignChangeLogs(CHECK 约束限制 3 个值)。';
COMMENT ON COLUMN analytics.ad_daily_completeness.updated_at IS '最近一次 dailiness 标记时间(trigger 自动维护)。';

-- analytics.ad_raw
COMMENT ON COLUMN analytics.ad_raw.advertiser_id IS 'TikTok advertiser_id(同 shop_id);同 seller_id 配合用于 API 权限校验。';
COMMENT ON COLUMN analytics.ad_raw.campaign_id IS 'TikTok 广告计划 ID(数字字符串);5 元组 unique 的一部分。';
COMMENT ON COLUMN analytics.ad_raw.captured_at IS 'Chrome 扩展抓取时刻(应用本地时钟,非 TikTok 上游时间)。';
COMMENT ON COLUMN analytics.ad_raw.day IS 'TikTok 数据聚合粒度:yyyy-mm-dd;每条 raw 一行一日。';
COMMENT ON COLUMN analytics.ad_raw.endpoint IS 'TikTok OEC API 端点(per tiktok-endpoint-schemas.ts 的 4 路径白名单)。';
COMMENT ON COLUMN analytics.ad_raw.id IS '主键 bigint identity。';
COMMENT ON COLUMN analytics.ad_raw.idempotency_key IS '幂等键(SHA-256 of 5 元组 + page=1),用于 ON CONFLICT DO UPDATE 判重。';
COMMENT ON COLUMN analytics.ad_raw.method IS 'HTTP method:GET / POST(目前 dumps 协议统一 POST)。';
COMMENT ON COLUMN analytics.ad_raw.protocol_version IS 'dumps 协议版本号(per tech-doc/analytics/dump-architecture.md,目前 2)。';
COMMENT ON COLUMN analytics.ad_raw.received_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN analytics.ad_raw.request IS 'Chrome 扩展抓的完整 HTTP request JSON(url+body+headers,jsonb)。';
COMMENT ON COLUMN analytics.ad_raw.request_id IS '请求链路 ID(uuid,用于跨表追踪 dumps 流程)。';
COMMENT ON COLUMN analytics.ad_raw.response IS 'Chrome 扩展抓的完整 HTTP response JSON(status+body+headers,jsonb,不可变 — source of truth)。';
COMMENT ON COLUMN analytics.ad_raw.schema_version IS 'dump 内 payload 的 schema 版本(目前 1)。';
COMMENT ON COLUMN analytics.ad_raw.seller_id IS 'TikTok shop_id(数字字符串),广告数据所属店铺。';
COMMENT ON COLUMN analytics.ad_raw.source IS '数据来源标识(默认 ''tiktok-shop-data-sync'' = Chrome 扩展 ID)。';
COMMENT ON COLUMN analytics.ad_raw.updated_at IS '最近修改时间(trigger 自动维护;对 source-of-truth 表是 ad_raw upsert 重写时刷)。';

-- analytics.ad_records
COMMENT ON COLUMN analytics.ad_records.advertiser_id IS '同 ad_raw.advertiser_id(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.campaign_id IS 'TikTok 广告计划 ID;5 元组 unique 的一部分。';
COMMENT ON COLUMN analytics.ad_records.captured_at IS '同 ad_raw.captured_at(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.day IS 'yyyy-mm-dd 聚合粒度。';
COMMENT ON COLUMN analytics.ad_records.endpoint IS '同 ad_raw.endpoint(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.id IS '主键 bigint identity;**派生**自 ad_raw,productAnalyses = ad_raw.endpoint=post_product_list。';
COMMENT ON COLUMN analytics.ad_records.idempotency_key IS '同 ad_raw.idempotency_key(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.method IS '同 ad_raw.method(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.protocol_version IS '同 ad_raw.protocol_version(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.received_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN analytics.ad_records.request_body IS '仅 request.body 字段(派生时提取,jsonb)。';
COMMENT ON COLUMN analytics.ad_records.request_id IS '同 ad_raw.request_id(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.response_data IS '仅 response.body 字段(派生时提取,jsonb,业务常用)。';
COMMENT ON COLUMN analytics.ad_records.schema_version IS '同 ad_raw.schema_version(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.seller_id IS '同 ad_raw.seller_id(派生时复制)。';
COMMENT ON COLUMN analytics.ad_records.shop_name IS '店铺展示名(空字符串或 NULL;来自 sales_orders / channel_accounts 反查)。';
COMMENT ON COLUMN analytics.ad_records.source IS '数据来源(同 ad_raw.source)。';
COMMENT ON COLUMN analytics.ad_records.source_record_id IS 'FK 到 ad_raw.id,指明派生自哪条 raw(ON DELETE SET NULL)。';
COMMENT ON COLUMN analytics.ad_records.storage_key IS 'server 端从 endpoint 推导的 storage_key:productAnalyses / sessionAnalyses / campaignChangeLogs(per STORAGE_KEY_BY_PATH)。';
COMMENT ON COLUMN analytics.ad_records.updated_at IS '最近派生时间(trigger 自动维护 — ad_raw 重新 upsert 派生表也重写)。';

-- analytics.ad_shop_timezones
COMMENT ON COLUMN analytics.ad_shop_timezones.advertiser_id IS 'TikTok advertiser_id(可空字符串)。';
COMMENT ON COLUMN analytics.ad_shop_timezones.created_at IS '时区配置首次写入时间(insert 时赋值,通常是首次 sync 该 shop 时)。';
COMMENT ON COLUMN analytics.ad_shop_timezones.seller_id IS 'TikTok shop_id;主键(单一主键)。';
COMMENT ON COLUMN analytics.ad_shop_timezones.timezone IS 'IANA 时区标识(如 ''Asia/Shanghai'' / ''Asia/Ho_Chi_Minh'');空 = 用默认 ''Asia/Shanghai''。';
COMMENT ON COLUMN analytics.ad_shop_timezones.updated_at IS '时区配置最近一次更新(trigger 自动维护,典型场景:运营调整店铺时区)。';

-- commerce.channel_accounts
COMMENT ON COLUMN commerce.channel_accounts.account_name IS '店铺展示名(便于人工识别,不会用作程序逻辑)。';
COMMENT ON COLUMN commerce.channel_accounts.credential_id IS '关联 integration.credentials;NULL 表示已解绑但历史订单保留。';
COMMENT ON COLUMN commerce.channel_accounts.external_account_id IS 'TikTok shop_id(数字字符串)或 miaoshou licenseId;对外唯一。';
COMMENT ON COLUMN commerce.channel_accounts.id IS '主键,系统内部 bigint identity。';
COMMENT ON COLUMN commerce.channel_accounts.platform IS '渠道平台标识:''tiktok'' | ''miaoshou''(单表存所有平台,多未来)。';
COMMENT ON COLUMN commerce.channel_accounts.region IS '店铺地区代码:TikTok 是 ISO 国家码(VN/TH/PH 等),miaoshou 是空。';
COMMENT ON COLUMN commerce.channel_accounts.seller_type IS 'TikTok 卖家类型:local / cross_border / mall / brand 等。';
COMMENT ON COLUMN commerce.channel_accounts.source_updated_at IS 'TikTok/miaoshou 上游 API 返回的最近一次店铺元信息更新时间。';
COMMENT ON COLUMN commerce.channel_accounts.status IS '店铺授权/激活状态:active | suspended | closed;OAuth callback 时写入。';
COMMENT ON COLUMN commerce.channel_accounts.synced_at IS '本地 tts-erp 首次落地此账号记录的时间(INSERT 时赋值,不再变)。';
COMMENT ON COLUMN commerce.channel_accounts.updated_at IS '此行最近一次修改时间(BEFORE UPDATE trigger 自动维护,per ADR-0001)。';

-- commerce.channel_product_variants
COMMENT ON COLUMN commerce.channel_product_variants.attributes IS '变体属性 JSON:颜色/尺码/材质等键值对;NULL 表示无属性。';
COMMENT ON COLUMN commerce.channel_product_variants.channel_product_id IS '所属 SPU;FK 到 channel_products。';
COMMENT ON COLUMN commerce.channel_product_variants.external_variant_id IS 'TikTok SKU ID;联合 channel_product_id 唯一。';
COMMENT ON COLUMN commerce.channel_product_variants.id IS '主键,系统 bigint identity。';
COMMENT ON COLUMN commerce.channel_product_variants.image_url IS '变体图 URL;空字符串或 NULL 时回退到 SPU 主图。';
COMMENT ON COLUMN commerce.channel_product_variants.raw_record_id IS '关联 raw_records,FK。';
COMMENT ON COLUMN commerce.channel_product_variants.seller_sku IS '卖家自定义 SKU 编码(可空,平台可不填)。';
COMMENT ON COLUMN commerce.channel_product_variants.source_updated_at IS 'TikTok 上游的 SKU 最近更新时间。';
COMMENT ON COLUMN commerce.channel_product_variants.status IS '变体上下架状态(同 SPU 状态)。';
COMMENT ON COLUMN commerce.channel_product_variants.synced_at IS '本地首次落地时间(INSERT 时赋值)。';
COMMENT ON COLUMN commerce.channel_product_variants.updated_at IS '最近修改时间(trigger 自动维护)。';
COMMENT ON COLUMN commerce.channel_product_variants.variant_name IS '变体名称(如 ''红色/M'');可空。';

-- commerce.channel_products
COMMENT ON COLUMN commerce.channel_products.category_id IS 'TikTok 商品类目 ID(数字字符串);用于类目过滤 / 类目属性补全。';
COMMENT ON COLUMN commerce.channel_products.channel_account_id IS '所属 TikTok shop;FK 到 channel_accounts。';
COMMENT ON COLUMN commerce.channel_products.external_product_id IS 'TikTok SPU ID(数字字符串),跟 channel_account_id 联合唯一。';
COMMENT ON COLUMN commerce.channel_products.id IS '系统内部 bigint identity;外部用 external_product_id。';
COMMENT ON COLUMN commerce.channel_products.main_image_url IS '商品主图 URL(TikTok CDN,带签名 token,会过期)。';
COMMENT ON COLUMN commerce.channel_products.raw_record_id IS '关联 integration.raw_records(此行由哪条上游 API 响应解析得到);FK 关联。';
COMMENT ON COLUMN commerce.channel_products.source_created_at IS 'TikTok 上游的 SPU 创建时间(店铺侧首次上架时间)。';
COMMENT ON COLUMN commerce.channel_products.source_updated_at IS 'TikTok 上游的 SPU 最近一次更新时间(标题/价格/状态变化等)。';
COMMENT ON COLUMN commerce.channel_products.status IS '商品上下架状态:available / unavailable / draft / deleted;由 TikTok 决定。';
COMMENT ON COLUMN commerce.channel_products.synced_at IS '本地 tts-erp 首次落地此 SPU 的时间(INSERT 时赋值)。';
COMMENT ON COLUMN commerce.channel_products.title IS '商品标题(可能含 unicode / 多语言 / emoji;长度不限制)。';
COMMENT ON COLUMN commerce.channel_products.updated_at IS '此行最近一次修改时间(trigger 自动维护,per ADR-0001)。';

-- commerce.sales_order_lines
COMMENT ON COLUMN commerce.sales_order_lines.channel_product_id IS '关联的 SPU;FK,**nullable** — 产品未同步时行先到,后补。';
COMMENT ON COLUMN commerce.sales_order_lines.channel_product_variant_id IS '关联的 SKU;FK,可空(同上)。';
COMMENT ON COLUMN commerce.sales_order_lines.currency IS 'ISO 4217 货币代码(可能与订单头不同,如跨境多币种)。';
COMMENT ON COLUMN commerce.sales_order_lines.external_line_id IS 'TikTok 订单行 ID;联合 sales_order_id 唯一。';
COMMENT ON COLUMN commerce.sales_order_lines.external_product_id_snapshot IS '下单时 TikTok SPU ID 的快照(产品被删除后仍可溯源)。';
COMMENT ON COLUMN commerce.sales_order_lines.external_variant_id_snapshot IS '下单时 TikTok SKU ID 的快照。';
COMMENT ON COLUMN commerce.sales_order_lines.id IS '主键,系统 bigint identity。';
COMMENT ON COLUMN commerce.sales_order_lines.image_url_snapshot IS '下单时商品图快照。';
COMMENT ON COLUMN commerce.sales_order_lines.line_status IS '订单行状态:NORMAL / CANCELLED / RETURNED。';
COMMENT ON COLUMN commerce.sales_order_lines.product_name_snapshot IS '下单时商品名称快照(防止后改商品标题后历史失真)。';
COMMENT ON COLUMN commerce.sales_order_lines.quantity IS '购买件数,numeric(20,4) 兼容小数(如 0.5 公斤)。';
COMMENT ON COLUMN commerce.sales_order_lines.raw_record_id IS '关联 raw_records,FK。';
COMMENT ON COLUMN commerce.sales_order_lines.sales_order_id IS '所属订单头;FK 到 sales_orders。';
COMMENT ON COLUMN commerce.sales_order_lines.synced_at IS '本地首次入库时间(INSERT 时赋值)。';
COMMENT ON COLUMN commerce.sales_order_lines.unit_price IS '下单单价(原始货币,未含优惠/税)。';
COMMENT ON COLUMN commerce.sales_order_lines.updated_at IS '最近一次修改时间(trigger 自动维护)。';
COMMENT ON COLUMN commerce.sales_order_lines.variant_name_snapshot IS '下单时变体名称快照。';

-- commerce.sales_orders
COMMENT ON COLUMN commerce.sales_orders.cancelled_at IS '取消时间(可与 status=CANCELLED 一起用作 SLA 分析)。';
COMMENT ON COLUMN commerce.sales_orders.channel_account_id IS '所属 TikTok shop;FK。';
COMMENT ON COLUMN commerce.sales_orders.currency IS 'ISO 4217 货币代码:VND / THB / PHP / USD 等。';
COMMENT ON COLUMN commerce.sales_orders.delivered_at IS '签收完成时间。';
COMMENT ON COLUMN commerce.sales_orders.external_order_id IS 'TikTok 订单 ID;联合 channel_account_id 唯一。';
COMMENT ON COLUMN commerce.sales_orders.fulfillment_type IS '履约方式:TikTok shipping / seller shipping / cross_border。';
COMMENT ON COLUMN commerce.sales_orders.id IS '系统内部主键 bigint identity。';
COMMENT ON COLUMN commerce.sales_orders.paid_at IS '买家付款完成时间(关键 SLA 指标)。';
COMMENT ON COLUMN commerce.sales_orders.payment_amount IS '买家实付金额(含运费,不含优惠);numeric(20,4) 避免浮点。';
COMMENT ON COLUMN commerce.sales_orders.raw_record_id IS '关联 raw_records,FK。';
COMMENT ON COLUMN commerce.sales_orders.shipped_at IS '包裹出库时间(用于计算物流时效)。';
COMMENT ON COLUMN commerce.sales_orders.source_created_at IS 'TikTok 上游订单创建时间(买家下单时刻)。';
COMMENT ON COLUMN commerce.sales_orders.source_updated_at IS 'TikTok 上游订单最近一次状态/字段变化时间。';
COMMENT ON COLUMN commerce.sales_orders.status IS '订单状态(枚举):AWAITING_SHIPMENT / AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED / CANCELLED。';
COMMENT ON COLUMN commerce.sales_orders.synced_at IS '本地首次入库时间(INSERT 时赋值,UPDATE 不再变 — per ADR-0001 §2.1)。';
COMMENT ON COLUMN commerce.sales_orders.total_amount IS '订单总金额(payment + 平台优惠 + 折扣);通常 = payment_amount 但可能有差异。';
COMMENT ON COLUMN commerce.sales_orders.updated_at IS '此订单最近一次修改时间(trigger 自动维护,反映 sync 实际活跃度)。';

-- finance.payouts
COMMENT ON COLUMN finance.payouts.amount IS '打款总金额。';
COMMENT ON COLUMN finance.payouts.channel_account_id IS '所属 TikTok shop;FK。';
COMMENT ON COLUMN finance.payouts.currency IS '币种。';
COMMENT ON COLUMN finance.payouts.external_payout_id IS 'TikTok 打款单号;联合 channel_account_id 唯一。';
COMMENT ON COLUMN finance.payouts.id IS '主键。';
COMMENT ON COLUMN finance.payouts.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN finance.payouts.source_created_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN finance.payouts.source_updated_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN finance.payouts.status IS '状态:PENDING / PAID / FAILED。';
COMMENT ON COLUMN finance.payouts.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN finance.payouts.updated_at IS '最近修改。';

-- finance.settlement_components
COMMENT ON COLUMN finance.settlement_components.amount IS '金额(可正可负)。';
COMMENT ON COLUMN finance.settlement_components.component_code IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN finance.settlement_components.created_at IS '创建时间。';
COMMENT ON COLUMN finance.settlement_components.currency IS '币种。';
COMMENT ON COLUMN finance.settlement_components.id IS '主键。';
COMMENT ON COLUMN finance.settlement_components.source_order IS '整数字段。';  -- inferred
COMMENT ON COLUMN finance.settlement_components.transaction_id IS '外键,引用 transaction.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN finance.settlement_components.updated_at IS '最近修改。';

-- finance.settlement_statements
COMMENT ON COLUMN finance.settlement_statements.currency IS '币种。';
COMMENT ON COLUMN finance.settlement_statements.external_statement_id IS 'TikTok 结算单号。';
COMMENT ON COLUMN finance.settlement_statements.id IS '主键。';
COMMENT ON COLUMN finance.settlement_statements.payout_id IS '外键,引用 payout.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN finance.settlement_statements.period_end IS '结算周期结束(包含)。';
COMMENT ON COLUMN finance.settlement_statements.period_start IS '结算周期开始(包含)。';
COMMENT ON COLUMN finance.settlement_statements.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN finance.settlement_statements.statement_time IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN finance.settlement_statements.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN finance.settlement_statements.updated_at IS '最近修改。';

-- finance.settlement_transactions
COMMENT ON COLUMN finance.settlement_transactions.after_sales_case_id IS '外键,引用 after_sales_case.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN finance.settlement_transactions.external_transaction_id IS 'TikTok 结算明细号。';
COMMENT ON COLUMN finance.settlement_transactions.id IS '主键。';
COMMENT ON COLUMN finance.settlement_transactions.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN finance.settlement_transactions.sales_order_id IS '关联订单;FK,SET NULL(订单删除保留结算)。';
COMMENT ON COLUMN finance.settlement_transactions.sales_order_line_id IS '外键,引用 sales_order_line.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN finance.settlement_transactions.settlement_statement_id IS '所属结算单;FK。';
COMMENT ON COLUMN finance.settlement_transactions.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN finance.settlement_transactions.transaction_time IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN finance.settlement_transactions.updated_at IS '最近修改。';

-- fulfillment.shipment_lines
COMMENT ON COLUMN fulfillment.shipment_lines.created_at IS '创建时间。';
COMMENT ON COLUMN fulfillment.shipment_lines.quantity IS '发货数量。';
COMMENT ON COLUMN fulfillment.shipment_lines.sales_order_line_id IS '关联订单行;FK。';
COMMENT ON COLUMN fulfillment.shipment_lines.shipment_id IS '所属运单;FK。';
COMMENT ON COLUMN fulfillment.shipment_lines.updated_at IS '最近修改。';

-- fulfillment.shipments
COMMENT ON COLUMN fulfillment.shipments.delivered_at IS '签收时间(可能 NULL)。';
COMMENT ON COLUMN fulfillment.shipments.external_package_id IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN fulfillment.shipments.id IS '主键。';
COMMENT ON COLUMN fulfillment.shipments.provider_id IS '外键,引用 provider.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN fulfillment.shipments.provider_name IS '文本字段。';  -- inferred
COMMENT ON COLUMN fulfillment.shipments.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN fulfillment.shipments.sales_order_id IS '关联订单;FK。';
COMMENT ON COLUMN fulfillment.shipments.shipped_at IS '出库时间。';
COMMENT ON COLUMN fulfillment.shipments.status IS '运单状态:CREATED / PICKED_UP / IN_TRANSIT / DELIVERED / EXCEPTION / RETURNED。';
COMMENT ON COLUMN fulfillment.shipments.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN fulfillment.shipments.tracking_number IS '物流单号(由 carrier 分配,可能 NULL = 还没发货)。';
COMMENT ON COLUMN fulfillment.shipments.updated_at IS '最近修改。';

-- fulfillment.tracking_events
COMMENT ON COLUMN fulfillment.tracking_events.action_code IS '整数字段。';  -- inferred
COMMENT ON COLUMN fulfillment.tracking_events.description IS '事件描述(自由文本)。';
COMMENT ON COLUMN fulfillment.tracking_events.event_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN fulfillment.tracking_events.external_event_key IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN fulfillment.tracking_events.id IS '主键。';
COMMENT ON COLUMN fulfillment.tracking_events.location IS '事件发生地点(自由文本,如 ''深圳宝安转运中心'')。';
COMMENT ON COLUMN fulfillment.tracking_events.shipment_id IS '所属运单;FK。';
COMMENT ON COLUMN fulfillment.tracking_events.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN fulfillment.tracking_events.updated_at IS '最近修改。';

-- integration.credentials
COMMENT ON COLUMN integration.credentials.account_label IS '人工可读标签(如 ''VN 旗舰店'');仅展示用,不影响逻辑。';
COMMENT ON COLUMN integration.credentials.ciphertext IS 'Fernet 加密的 access_token;密文,plaintext 只在 process memory(per AGENTS.md §4.1)。';
COMMENT ON COLUMN integration.credentials.company_secret_ciphertext IS 'Miaoshou companySecret 的 Fernet 密文(仅 miaoshou 用)。';
COMMENT ON COLUMN integration.credentials.created_at IS '凭证首次入库时间(insert 时赋值,等同首次 OAuth callback 成功时间)。';
COMMENT ON COLUMN integration.credentials.expires_at IS 'access_token 过期时间(ISO 8601);null 表示永不过期或未指定。';
COMMENT ON COLUMN integration.credentials.external_account_id IS 'TikTok shop_id 或 miaoshou licenseId;联合 provider 唯一。';
COMMENT ON COLUMN integration.credentials.extra IS '平台特定扩展字段(Miaoshou license meta、TikTok 店铺元信息等),JSON 灵活存。';
COMMENT ON COLUMN integration.credentials.granted_scopes IS '授权 scope 列表(仅 TikTok):[''product.write'' 等]。';
COMMENT ON COLUMN integration.credentials.id IS '主键 bigint identity。';
COMMENT ON COLUMN integration.credentials.provider IS 'OAuth 提供方:''tiktok'' | ''miaoshou''。';
COMMENT ON COLUMN integration.credentials.updated_at IS '凭证最近一次更新(token 刷新 / scope 重授权);trigger 自动维护。';

-- integration.raw_records
COMMENT ON COLUMN integration.raw_records.captured_at IS 'Chrome 扩展 / sync worker 抓取上游 response 的时刻(应用本地时钟)。';
COMMENT ON COLUMN integration.raw_records.credential_id IS '关联 credentials.id;FK;**use_alter=True** 因为凭证可能延迟插入。';
COMMENT ON COLUMN integration.raw_records.endpoint IS '上游 API 端点路径(如 ''tiktok.order.search'');用于查询和路由。';
COMMENT ON COLUMN integration.raw_records.external_id IS '上游资源 ID(订单 ID / 商品 ID);用 payload_hash 兜底(上游可能不返回 ID)。';
COMMENT ON COLUMN integration.raw_records.id IS '主键 bigint identity,所有规范化表的外键指向此 id。';
COMMENT ON COLUMN integration.raw_records.payload IS '上游 API 完整 JSON 响应(jsonb 原样存,server 不做字段过滤 — per dump-architecture)。';
COMMENT ON COLUMN integration.raw_records.payload_hash IS 'payload 的 sha256 哈希(64 字符 hex),用于幂等去重。';
COMMENT ON COLUMN integration.raw_records.synced_at IS '本地入库时刻(由 trigger/maintain 维护,主要供 ORM 使用)。';
COMMENT ON COLUMN integration.raw_records.updated_at IS '最近一次访问/重写时间(trigger 自动维护)。';

-- integration.sync_cursors
COMMENT ON COLUMN integration.sync_cursors.created_at IS '游标行创建时间(通常是该 (job, scope) 首次 sync 的时刻)。';
COMMENT ON COLUMN integration.sync_cursors.cursor_epoch_ms IS '数值类型游标(通常 = 上次 max(source_updated_at) epoch ms);用于增量同步。';
COMMENT ON COLUMN integration.sync_cursors.cursor_value IS '字符串类型游标(API 文档指明的 opaque token / next_page_url);空表示首次。';
COMMENT ON COLUMN integration.sync_cursors.id IS '主键 bigint identity。';
COMMENT ON COLUMN integration.sync_cursors.job_name IS '作业名(同 sync_jobs.job_name 命名)。';
COMMENT ON COLUMN integration.sync_cursors.scope IS '游标作用域:通常是 shop_id,某些作业用 ''all'' / ''daily'' 等聚合 key。';
COMMENT ON COLUMN integration.sync_cursors.updated_at IS '游标最近一次推进时间(注意:per ADR-0001 §3.2 现状,旧 schema 此字段只在 INSERT 写;本次加 trigger 后会变正确)。';

-- integration.sync_issues
COMMENT ON COLUMN integration.sync_issues.created_at IS '行创建时间(通常 = detected_at 第一次)。';
COMMENT ON COLUMN integration.sync_issues.details IS 'issue 详情 JSON(payload 截断、错误码、上游响应等,便于排障)。';
COMMENT ON COLUMN integration.sync_issues.detected_at IS '检测到 issue 的时间(第一次出现时刻)。';
COMMENT ON COLUMN integration.sync_issues.external_id IS '上游资源 ID(如缺失 payment_id 的 statement_id);null 表示作业级 issue。';
COMMENT ON COLUMN integration.sync_issues.id IS '主键 bigint identity。';
COMMENT ON COLUMN integration.sync_issues.issue_type IS 'issue 类型枚举:TOKEN_REFRESH_FAILED / STATEMENT_PAYMENT_ID_MISSING / UNKNOWN_ORDER / SCHEMA_INVALID 等。';
COMMENT ON COLUMN integration.sync_issues.job_name IS '出 issue 的作业名(同 sync_jobs.job_name)。';
COMMENT ON COLUMN integration.sync_issues.resolved_at IS '解决时间(同 issue_type 重新跑且不再出现的时刻);null = 未解决。';
COMMENT ON COLUMN integration.sync_issues.updated_at IS '最近一次状态变化时间(新建或解决时刷新,trigger 自动维护)。';

-- integration.sync_jobs
COMMENT ON COLUMN integration.sync_jobs.created_at IS '行创建时间(通常 = started_at)。';
COMMENT ON COLUMN integration.sync_jobs.credential_id IS '本次作业用的凭证;FK,SET NULL(凭证解绑不丢历史)。';
COMMENT ON COLUMN integration.sync_jobs.error_message IS '失败时作业级错误(不是行级,行级错在 sync_issues);前 1024 字符。';
COMMENT ON COLUMN integration.sync_jobs.extra IS '作业特定扩展(API 配额、限流重试次数、店铺 ID 列表等)JSON 存。';
COMMENT ON COLUMN integration.sync_jobs.finished_at IS '作业结束时间(succeeded/failed 时由代码显式写,无默认值 — per ADR-0001 §2.1)。';
COMMENT ON COLUMN integration.sync_jobs.id IS '主键 bigint identity。';
COMMENT ON COLUMN integration.sync_jobs.job_name IS '作业名:''tiktok.orders'' / ''tiktok.finance.statements'' / ''miaoshou.collect_box'' 等(per sync_worker/scheduler.py)。';
COMMENT ON COLUMN integration.sync_jobs.rows_failed IS '本次失败的行数(写入 sync_issues 但不阻断作业)。';
COMMENT ON COLUMN integration.sync_jobs.rows_inserted IS '本次新插入到目标表(sales_orders 等)的行数。';
COMMENT ON COLUMN integration.sync_jobs.rows_total IS '本次作业扫描到的上游记录总数(含已存在的,新+旧+失败)。';
COMMENT ON COLUMN integration.sync_jobs.rows_updated IS '本次 UPDATE 已存在行的次数(ON CONFLICT DO UPDATE 触发)。';
COMMENT ON COLUMN integration.sync_jobs.started_at IS '作业开始执行时间(本地时钟)。';
COMMENT ON COLUMN integration.sync_jobs.status IS '作业状态:''running'' | ''succeeded'' | ''failed'';运行时 → 终态。';
COMMENT ON COLUMN integration.sync_jobs.updated_at IS '最近一次状态变化时间(由 trigger 自动维护 — 反映作业真正结束时刻)。';

-- linkage.account_links
COMMENT ON COLUMN linkage.account_links.channel_account_id IS 'TikTok shop;FK。';
COMMENT ON COLUMN linkage.account_links.created_at IS '创建时间。';
COMMENT ON COLUMN linkage.account_links.external_relation_id IS '文本字段。';  -- inferred
COMMENT ON COLUMN linkage.account_links.id IS '主键 bigint identity。';
COMMENT ON COLUMN linkage.account_links.procurement_account_id IS '内部采购账号;FK。';
COMMENT ON COLUMN linkage.account_links.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN linkage.account_links.source_updated_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.account_links.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK)。';  -- inferred
COMMENT ON COLUMN linkage.account_links.updated_at IS '最近修改(trigger 维护)。';
COMMENT ON COLUMN linkage.account_links.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.account_links.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- linkage.link_evidence
COMMENT ON COLUMN linkage.link_evidence.created_at IS '创建时间。';
COMMENT ON COLUMN linkage.link_evidence.evidence_payload IS 'JSON 结构化字段(jsonb 原样存,不做规范化)。';  -- inferred
COMMENT ON COLUMN linkage.link_evidence.evidence_type IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN linkage.link_evidence.id IS '主键。';
COMMENT ON COLUMN linkage.link_evidence.observed_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.link_evidence.product_link_id IS 'FK 到 product_links;NULL 表示 evidence 已被独立于 link 存(老逻辑)。';
COMMENT ON COLUMN linkage.link_evidence.source_external_id IS '外键,引用 source_external.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN linkage.link_evidence.source_table IS '文本字段。';  -- inferred
COMMENT ON COLUMN linkage.link_evidence.updated_at IS '最近修改。';
COMMENT ON COLUMN linkage.link_evidence.variant_link_id IS 'FK 到 variant_links。';

-- linkage.link_issues
COMMENT ON COLUMN linkage.link_issues.candidate_count IS '整数字段。';  -- inferred
COMMENT ON COLUMN linkage.link_issues.channel_product_id IS '出 issue 的 TikTok SPU;FK。';
COMMENT ON COLUMN linkage.link_issues.created_at IS '创建时间(等同 issue 第一次出现时间)。';
COMMENT ON COLUMN linkage.link_issues.details IS 'issue 详情 JSON。';
COMMENT ON COLUMN linkage.link_issues.id IS '主键。';
COMMENT ON COLUMN linkage.link_issues.issue_type IS 'issue 类型:NO_CANDIDATE / AMBIGUOUS_MATCH / OVERRIDE_CONFLICT 等。';
COMMENT ON COLUMN linkage.link_issues.procurement_product_id IS '外键,引用 procurement_product.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN linkage.link_issues.resolved_at IS '解决时间;NULL = 未解决。';
COMMENT ON COLUMN linkage.link_issues.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK)。';  -- inferred
COMMENT ON COLUMN linkage.link_issues.updated_at IS '最近修改。';

-- linkage.link_overrides
COMMENT ON COLUMN linkage.link_overrides.channel_product_id IS 'TikTok SPU;FK。';
COMMENT ON COLUMN linkage.link_overrides.created_at IS '创建时间。';
COMMENT ON COLUMN linkage.link_overrides.created_by IS '创建人 username。';
COMMENT ON COLUMN linkage.link_overrides.decision IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN linkage.link_overrides.id IS '主键。';
COMMENT ON COLUMN linkage.link_overrides.procurement_product_id IS '强制关联到的内部 SPU;FK。';
COMMENT ON COLUMN linkage.link_overrides.reason IS '覆盖理由(人工必填,便于审计)。';
COMMENT ON COLUMN linkage.link_overrides.updated_at IS '最近修改。';
COMMENT ON COLUMN linkage.link_overrides.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.link_overrides.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- linkage.product_links
COMMENT ON COLUMN linkage.product_links.channel_product_id IS 'TikTok SPU;FK 到 commerce.channel_products。';
COMMENT ON COLUMN linkage.product_links.created_at IS '首次创建时间(insert 时赋值)。';
COMMENT ON COLUMN linkage.product_links.external_relation_id IS '文本字段。';  -- inferred
COMMENT ON COLUMN linkage.product_links.id IS '主键 bigint identity(per product_linkage.py 派生表)。';
COMMENT ON COLUMN linkage.product_links.is_primary IS '布尔字段。';  -- inferred
COMMENT ON COLUMN linkage.product_links.procurement_product_id IS '内部 SPU;FK 到 procurement.procurement_products。';
COMMENT ON COLUMN linkage.product_links.raw_record_id IS '关联 raw_records,FK。';
COMMENT ON COLUMN linkage.product_links.relation_type IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN linkage.product_links.source_updated_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.product_links.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK)。';  -- inferred
COMMENT ON COLUMN linkage.product_links.updated_at IS '最近一次修改(trigger 自动维护 — override 写入会刷)。';
COMMENT ON COLUMN linkage.product_links.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.product_links.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- linkage.variant_links
COMMENT ON COLUMN linkage.variant_links.channel_product_variant_id IS 'TikTok SKU。';
COMMENT ON COLUMN linkage.variant_links.created_at IS '创建时间。';
COMMENT ON COLUMN linkage.variant_links.external_relation_id IS '文本字段。';  -- inferred
COMMENT ON COLUMN linkage.variant_links.id IS '主键 bigint identity。';
COMMENT ON COLUMN linkage.variant_links.procurement_product_variant_id IS '内部 SKU。';
COMMENT ON COLUMN linkage.variant_links.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN linkage.variant_links.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK)。';  -- inferred
COMMENT ON COLUMN linkage.variant_links.updated_at IS '最近修改时间。';
COMMENT ON COLUMN linkage.variant_links.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN linkage.variant_links.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- procurement.manual_product_costs
COMMENT ON COLUMN procurement.manual_product_costs.channel_product_id IS '外键,引用 channel_product.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.manual_product_costs.created_at IS '创建时间。';
COMMENT ON COLUMN procurement.manual_product_costs.created_by IS '创建人 username。';
COMMENT ON COLUMN procurement.manual_product_costs.currency IS '币种。';
COMMENT ON COLUMN procurement.manual_product_costs.id IS '主键。';
COMMENT ON COLUMN procurement.manual_product_costs.note IS '文本描述/详情字段(free-form,长文本)。';  -- inferred
COMMENT ON COLUMN procurement.manual_product_costs.unit_cost IS '手工覆盖的进货单价(优先级高于妙手拉取的实时值)。';
COMMENT ON COLUMN procurement.manual_product_costs.updated_at IS '最近修改。';
COMMENT ON COLUMN procurement.manual_product_costs.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.manual_product_costs.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- procurement.procurement_accounts
COMMENT ON COLUMN procurement.procurement_accounts.account_name IS '账号展示名。';
COMMENT ON COLUMN procurement.procurement_accounts.credential_id IS '关联 integration.credentials。';
COMMENT ON COLUMN procurement.procurement_accounts.external_account_id IS '内部采购账号 ID;unique(用于登录/鉴权)。';
COMMENT ON COLUMN procurement.procurement_accounts.id IS '主键。';
COMMENT ON COLUMN procurement.procurement_accounts.provider IS '数据来源标识(自由文本,用于追溯 NOT NULL)。';  -- inferred
COMMENT ON COLUMN procurement.procurement_accounts.source_updated_at IS '上游(妙手等)账号信息最近更新时间。';
COMMENT ON COLUMN procurement.procurement_accounts.status IS '账号状态:active / suspended / closed。';
COMMENT ON COLUMN procurement.procurement_accounts.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN procurement.procurement_accounts.updated_at IS '最近修改。';

-- procurement.procurement_product_variants
COMMENT ON COLUMN procurement.procurement_product_variants.attributes IS '变体属性 JSON。';
COMMENT ON COLUMN procurement.procurement_product_variants.external_variant_id IS '平台 SKU ID;联合 procurement_product_id 唯一。';
COMMENT ON COLUMN procurement.procurement_product_variants.id IS '主键。';
COMMENT ON COLUMN procurement.procurement_product_variants.procurement_product_id IS '所属商品;FK。';
COMMENT ON COLUMN procurement.procurement_product_variants.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN procurement.procurement_product_variants.status IS '变体状态。';
COMMENT ON COLUMN procurement.procurement_product_variants.supplier_sku IS '文本字段。';  -- inferred
COMMENT ON COLUMN procurement.procurement_product_variants.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN procurement.procurement_product_variants.updated_at IS '最近修改。';
COMMENT ON COLUMN procurement.procurement_product_variants.variant_name IS '变体名(如 ''红色/M'')。';

-- procurement.procurement_products
COMMENT ON COLUMN procurement.procurement_products.external_product_id IS '妙手/平台商品 ID;联合 procurement_account_id 唯一。';
COMMENT ON COLUMN procurement.procurement_products.id IS '主键。';
COMMENT ON COLUMN procurement.procurement_products.procurement_account_id IS '所属采购账号;FK。';
COMMENT ON COLUMN procurement.procurement_products.product_type IS '文本字段。';  -- inferred
COMMENT ON COLUMN procurement.procurement_products.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN procurement.procurement_products.source_item_id IS '外键,引用 source_item.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN procurement.procurement_products.source_item_url IS 'URL 字段(可能含 CDN 签名 token,会过期)。';  -- inferred
COMMENT ON COLUMN procurement.procurement_products.source_platform IS '文本字段。';  -- inferred
COMMENT ON COLUMN procurement.procurement_products.source_updated_at IS '上游商品最近更新时间。';
COMMENT ON COLUMN procurement.procurement_products.status IS '商品状态:active / inactive / delisted。';
COMMENT ON COLUMN procurement.procurement_products.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN procurement.procurement_products.title IS '商品名(中文为主,可能含 emoji)。';
COMMENT ON COLUMN procurement.procurement_products.updated_at IS '最近修改。';

-- procurement.purchase_order_lines
COMMENT ON COLUMN procurement.purchase_order_lines.currency IS '币种。';
COMMENT ON COLUMN procurement.purchase_order_lines.external_line_id IS '妙手/平台采购单行号;联合 purchase_order_id 唯一。';
COMMENT ON COLUMN procurement.purchase_order_lines.id IS '主键。';
COMMENT ON COLUMN procurement.purchase_order_lines.line_status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_order_lines.procurement_product_id IS '采购商品 SPU;FK。';
COMMENT ON COLUMN procurement.purchase_order_lines.procurement_product_variant_id IS '采购 SKU;FK。';
COMMENT ON COLUMN procurement.purchase_order_lines.purchase_order_id IS '所属采购单;FK。';
COMMENT ON COLUMN procurement.purchase_order_lines.quantity IS '采购数量(numeric)。';
COMMENT ON COLUMN procurement.purchase_order_lines.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN procurement.purchase_order_lines.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN procurement.purchase_order_lines.unit_cost IS '进货单价(用于 reporting 算利润;关键财务字段)。';
COMMENT ON COLUMN procurement.purchase_order_lines.updated_at IS '最近修改。';

-- procurement.purchase_orders
COMMENT ON COLUMN procurement.purchase_orders.completed_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.currency IS '币种。';
COMMENT ON COLUMN procurement.purchase_orders.external_purchase_order_id IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.id IS '主键。';
COMMENT ON COLUMN procurement.purchase_orders.paid_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.procurement_account_id IS '采购账号;FK。';
COMMENT ON COLUMN procurement.purchase_orders.raw_record_id IS '关联 raw_records。';
COMMENT ON COLUMN procurement.purchase_orders.source_created_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.source_updated_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.status IS '采购单状态:PENDING / CONFIRMED / SHIPPED / RECEIVED / CANCELLED。';
COMMENT ON COLUMN procurement.purchase_orders.supplier_id IS '外键,引用 supplier.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN procurement.purchase_orders.synced_at IS '本地首次入库时间。';
COMMENT ON COLUMN procurement.purchase_orders.total_amount IS '采购单总金额。';
COMMENT ON COLUMN procurement.purchase_orders.updated_at IS '最近修改。';

-- procurement.spu_images
COMMENT ON COLUMN procurement.spu_images.channel_account_id IS '外键,引用 channel_account.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.channel_product_id IS '外键,引用 channel_product.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.content_type IS 'URL/资源标识 NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.created_at IS '创建时间。';
COMMENT ON COLUMN procurement.spu_images.deleted_at IS '时间戳字段。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.failure_reason IS '文本字段。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.filename IS 'URL/资源标识 NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.id IS '主键。';
COMMENT ON COLUMN procurement.spu_images.object_key IS 'URL/资源标识 NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.raw_metadata IS 'JSON 结构化字段(jsonb 原样存,不做规范化)。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.size_bytes IS '整数字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK) NOT NULL。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.updated_at IS '最近修改。';
COMMENT ON COLUMN procurement.spu_images.uploaded_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.uploaded_by_key_id IS '外键,引用 uploaded_by_key.id(级联策略见 ALTER TABLE)。';  -- inferred
COMMENT ON COLUMN procurement.spu_images.uploaded_by_prefix IS '文本字段。';  -- inferred

-- reporting.product_cost_snapshots
COMMENT ON COLUMN reporting.product_cost_snapshots.calculated_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.calculation_version IS '整数字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.channel_product_id IS 'TikTok SPU;FK;unique(snapshop 时段+SPU 唯一)。';
COMMENT ON COLUMN reporting.product_cost_snapshots.cost_method IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.created_at IS '首次快照时间。';
COMMENT ON COLUMN reporting.product_cost_snapshots.currency IS '币种(与 purchase_order_lines.unit_cost 一致)。';
COMMENT ON COLUMN reporting.product_cost_snapshots.id IS '主键。';
COMMENT ON COLUMN reporting.product_cost_snapshots.source_line_count IS '整数字段。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.source_purchase_amount IS '数值字段(numeric/decimal)。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.source_purchase_quantity IS '数值字段(numeric/decimal)。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.unit_cost IS '生效的单位进货价(从 purchase_order_lines 取最新)。';
COMMENT ON COLUMN reporting.product_cost_snapshots.updated_at IS '最近重算时间(每 6h 刷一次)。';
COMMENT ON COLUMN reporting.product_cost_snapshots.valid_from IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.product_cost_snapshots.valid_to IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred

-- reporting.product_profit_daily
COMMENT ON COLUMN reporting.product_profit_daily.calculated_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.calculation_version IS '整数字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.channel_product_id IS 'TikTok SPU;FK;unique(SPU+day)。';
COMMENT ON COLUMN reporting.product_profit_daily.cost_method IS '文本字段。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.created_at IS '首次写入。';
COMMENT ON COLUMN reporting.product_profit_daily.currency IS '币种。';
COMMENT ON COLUMN reporting.product_profit_daily.estimated_cogs IS '数值字段(numeric/decimal)。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.estimated_gross_profit IS '数值字段(numeric/decimal)。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.gross_revenue IS '总 GMV(未扣平台费)。';
COMMENT ON COLUMN reporting.product_profit_daily.id IS '主键。';
COMMENT ON COLUMN reporting.product_profit_daily.platform_fees IS '平台抽佣 + 支付费。';
COMMENT ON COLUMN reporting.product_profit_daily.profit_date IS '字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.refunds IS '退款金额(负数)。';
COMMENT ON COLUMN reporting.product_profit_daily.shipping_cost IS '数值字段(numeric/decimal)。';  -- inferred
COMMENT ON COLUMN reporting.product_profit_daily.units_sold IS '当日销售件数(distinct 订单行 quantity 求和)。';
COMMENT ON COLUMN reporting.product_profit_daily.updated_at IS '最近重算(每 1h 跑一次)。';

-- reporting.shipment_tracking_summary
COMMENT ON COLUMN reporting.shipment_tracking_summary.calculated_at IS '时间戳字段 NOT NULL(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.calculation_version IS '整数字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.created_at IS '首次写入。';
COMMENT ON COLUMN reporting.shipment_tracking_summary.event_count IS '数量/计数字段(整数或 numeric)。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.first_event_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.id IS '主键。';
COMMENT ON COLUMN reporting.shipment_tracking_summary.last_event_at IS '时间戳字段(per ADR-0001 双时间字段约定)。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.last_event_description IS '文本字段。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.last_location IS '文本字段。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.shipment_id IS '外键,引用 shipment.id(级联策略见 ALTER TABLE) NOT NULL。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.tracking_number IS '文本字段。';  -- inferred
COMMENT ON COLUMN reporting.shipment_tracking_summary.updated_at IS '最近重算。';

-- security.api_keys
COMMENT ON COLUMN security.api_keys.created_at IS '创建时间。';
COMMENT ON COLUMN security.api_keys.id IS '主键。';
COMMENT ON COLUMN security.api_keys.key_hash IS '文本字段 NOT NULL。';  -- inferred
COMMENT ON COLUMN security.api_keys.key_prefix IS 'API key 字符串前缀(明文),用于人工识别(整 key 哈希存储)。';
COMMENT ON COLUMN security.api_keys.last_used_at IS '最近一次使用时间(每次 auth 中间件更新)。';
COMMENT ON COLUMN security.api_keys.name IS '人工可读名称(例如 ''BI dashboard'')。';
COMMENT ON COLUMN security.api_keys.role IS '角色:readonly < readwrite < admin,per middleware/auth.py::required_role()。';
COMMENT ON COLUMN security.api_keys.rotated_to_key_hash IS '文本字段。';  -- inferred
COMMENT ON COLUMN security.api_keys.status IS '状态字段(枚举值见业务文档,默认/约束见 CHECK) NOT NULL。';  -- inferred
COMMENT ON COLUMN security.api_keys.updated_at IS '最近修改(例如修改 scopes / role 时刷新)。';

