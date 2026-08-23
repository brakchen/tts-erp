-- TikTok Shop ERP schema
-- Run as: docker exec -i postgres psql -U postgres -d tts_erp < schema.sql

CREATE TABLE IF NOT EXISTS shops (
    shop_id        TEXT PRIMARY KEY,
    shop_name      TEXT,
    shop_region    TEXT,
    shop_cipher    TEXT,             -- plaintext (not sensitive; already in oauth-receiver DB too)
    seller_type    TEXT,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    order_status   TEXT,
    create_time    BIGINT,
    update_time    BIGINT,
    buyer_id       TEXT,
    raw            JSONB,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id       TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    line_id        TEXT,
    product_id     TEXT,
    sku_id         TEXT,
    quantity       INT,
    raw            JSONB,
    PRIMARY KEY (order_id, line_id)
);

CREATE TABLE IF NOT EXISTS order_shippings (
    order_id       TEXT PRIMARY KEY REFERENCES orders(order_id) ON DELETE CASCADE,
    package_id     TEXT,
    tracking_number TEXT,
    carrier        TEXT,
    raw            JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id   TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    statement_time BIGINT,
    amount         NUMERIC,
    currency       TEXT,
    raw            JSONB,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id     TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    create_time    BIGINT,
    amount         NUMERIC,
    currency       TEXT,
    raw            JSONB,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS returns (
    return_id      TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    order_id       TEXT,
    return_status  TEXT,
    create_time    BIGINT,
    update_time    BIGINT,
    raw            JSONB,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cancellations (
    cancellation_id TEXT PRIMARY KEY,
    shop_id        TEXT NOT NULL,
    order_id       TEXT,
    cancel_status  TEXT,
    create_time    BIGINT,
    update_time    BIGINT,
    raw            JSONB,
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
    id             BIGSERIAL PRIMARY KEY,
    shop_id        TEXT,
    endpoint       TEXT,
    status         TEXT,
    rows           INT,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sync_log_shop_created ON sync_log (shop_id, created_at DESC);

-- Logistics tracking: per-order latest tracking record (denormalized for fast lookup)
CREATE TABLE IF NOT EXISTS logistics_tracking (
    order_id           TEXT PRIMARY KEY REFERENCES orders(order_id) ON DELETE CASCADE,
    tracking_number    TEXT,
    carrier            TEXT,
    final_status       TEXT,        -- delivered / in_transit / exception / unknown
    arrived_overseas   BOOLEAN,
    last_event_at      TIMESTAMPTZ,
    last_event_desc    TEXT,
    raw                JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_logistics_tracking_final_status ON logistics_tracking (final_status);
CREATE INDEX IF NOT EXISTS idx_logistics_tracking_tracking_number ON logistics_tracking (tracking_number);

-- Logistics events: per-update events (append-only history)
CREATE TABLE IF NOT EXISTS logistics_events (
    id             BIGSERIAL PRIMARY KEY,
    order_id       TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    action_code    INT,
    event_time     TIMESTAMPTZ,
    location      TEXT,
    description   TEXT,
    raw           JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_logistics_events_order ON logistics_events (order_id, event_time DESC);

-- API key auth (3-level: readonly / readwrite / admin).
-- `scopes` carries per-seller restriction; empty array = unrestricted.
-- Used by both tts-erp endpoints AND analytics_sync (Chrome extension
-- sync tokens), so analytics_sync no longer has its own token table.
CREATE TABLE IF NOT EXISTS api_keys (
    id            BIGSERIAL PRIMARY KEY,
    key_prefix    TEXT NOT NULL,
    key_hash      TEXT NOT NULL,           -- SHA-256(plaintext_key)
    role          TEXT NOT NULL CHECK (role IN ('readonly', 'readwrite', 'admin')),
    name          TEXT,
    scopes        TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    expires_at    TIMESTAMPTZ,             -- NULL = never expires
    -- 2026-08-23 fix: explicit named UNIQUE constraints (so IF NOT EXISTS
    -- won't silently drop them during future migrations). Both columns
    -- are referenced by ON CONFLICT clauses in api_keys.py and the
    -- analytics_sync mount; a plain btree index does NOT satisfy
    -- ON CONFLICT.
    CONSTRAINT uq_api_keys_prefix UNIQUE (key_prefix),
    CONSTRAINT uq_api_keys_hash   UNIQUE (key_hash)
);
-- idx_api_keys_prefix removed: the UNIQUE constraint above creates its own
-- btree index; a separate non-unique index would be redundant.
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (key_prefix);

-- ============================================================
-- 妙手 ERP 开放平台 (miaoshou) — 跟 tts 表分开，独立 schema
-- ============================================================

CREATE TABLE IF NOT EXISTS miaoshou_shops (
    shop_id              BIGINT NOT NULL,
    platform             TEXT NOT NULL,
    site                 TEXT NOT NULL,
    platform_shop_name   TEXT,
    shop_nick            TEXT,
    parent_shop_id       BIGINT,
    is_cb                INT,        -- 0/1 是否跨境
    is_cnsc              INT,        -- 0/1 是否全球店铺
    status               TEXT,
    gmt_expire           TEXT,
    gmt_last_auth        TEXT,
    raw_json             JSONB,       -- 完整响应备份
    synced_at            TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (platform, site, shop_id)
);
CREATE INDEX IF NOT EXISTS idx_miaoshou_shops_platform_site ON miaoshou_shops (platform, site);
CREATE INDEX IF NOT EXISTS idx_miaoshou_shops_synced_at ON miaoshou_shops (synced_at DESC);
-- ============================================================
-- miaoshou 新增 3 张表（Round 7：响应已采到 + PK 已定）
-- ============================================================

-- 定价模板（44 字段，price_template_id 全局唯一）
CREATE TABLE IF NOT EXISTS miaoshou_price_templates (
    price_template_id        BIGINT PRIMARY KEY,
    app_account_id           BIGINT,
    sub_app_account_id       BIGINT,
    platform                 TEXT,
    site                     TEXT,
    name                     TEXT,
    remark                   TEXT,
    currency                 TEXT,
    display_weight_unit      TEXT,
    profit_type              TEXT,
    profit_percent           NUMERIC,
    fixed_profit_amount      NUMERIC,
    exchange_rate            NUMERIC,
    discount                 NUMERIC,
    price_tail_compute_type  TEXT,
    price_tail               TEXT,
    price_process_decimal_type TEXT,
    logistics_compute_type   TEXT,
    weight_ref_type          TEXT,
    first_weight_charge       NUMERIC,
    first_weight_interval    NUMERIC,
    continued_weight_charge  NUMERIC,
    continued_weight_interval NUMERIC,
    logistics_charge         NUMERIC,
    platform_charge_percent  NUMERIC,
    payment_charge_percent   NUMERIC,
    activity_charge_percent  NUMERIC,
    withdraw_charge_percent  NUMERIC,
    other_charge             NUMERIC,
    is_cal_light_cargo       INT,
    light_cargo_coefficient  INT,
    weight_logistics_charge_list TEXT,
    domestic_logistics_compute_type TEXT,
    domestic_logistics_first_weight_charge NUMERIC,
    domestic_logistics_first_weight_interval NUMERIC,
    domestic_logistics_continued_weight_charge NUMERIC,
    domestic_logistics_continued_weight_interval NUMERIC,
    domestic_logistics_charge NUMERIC,
    buyer_logistic_charge    NUMERIC,
    seller_logistic_charge   NUMERIC,
    has_seller_logistic_charge INT,
    official_tpl_mode        TEXT,
    official_tpl_logistics_channel TEXT,
    snapshot_id              BIGINT,
    gmt_create               TEXT,
    gmt_modified             TEXT,
    raw_json                 JSONB,
    synced_at                TIMESTAMPTZ DEFAULT now()
);

-- 公共采集箱详情（TK 采集箱产品，PK = platform + common_collect_box_detail_id）
CREATE TABLE IF NOT EXISTS miaoshou_collect_box_details (
    platform                 TEXT NOT NULL,
    common_collect_box_detail_id BIGINT NOT NULL,
    app_account_id           BIGINT,
    sub_app_account_id       BIGINT,
    item_num                 TEXT,
    title                    TEXT,
    thumbnail                TEXT,
    list_thumbnail           TEXT,
    price                    NUMERIC,
    min_sku_price            NUMERIC,
    max_sku_price            NUMERIC,
    stock                    INT,
    remark                   TEXT,
    status                   TEXT,
    reason                   TEXT,
    gmt_create               TEXT,
    gmt_modified             TEXT,
    weight                   NUMERIC,
    max_sku_weight           NUMERIC,
    min_sku_weight           NUMERIC,
    common_collect_box_group_id BIGINT,
    common_collect_box_group_name TEXT,
    owner_sub_account_alias_name TEXT,
    is_mark                  TEXT,
    is_cb                    INT,
    is_cnsc                  INT,
    raw_json                 JSONB,
    synced_at                TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (platform, common_collect_box_detail_id)
);
CREATE INDEX IF NOT EXISTS idx_miaoshou_collect_box_platform_status ON miaoshou_collect_box_details (platform, status);

-- 发布任务（move collect task）
CREATE TABLE IF NOT EXISTS miaoshou_move_collect_tasks (
    platform                 TEXT NOT NULL,
    move_collect_task_detail_id TEXT NOT NULL,
    collect_box_detail_id    TEXT,
    shop_id                  TEXT,
    item_num                 TEXT,
    cid                      TEXT,
    source                   TEXT,
    source_site              TEXT,
    source_item_id           TEXT,
    title                    TEXT,
    thumbnail                TEXT,
    is_timing                TEXT,
    status                   TEXT,
    reason                   TEXT,
    gmt_create               TEXT,
    gmt_modified             TEXT,
    platform_item_id         TEXT,
    is_renew_item            BOOLEAN,
    shop_name                TEXT,
    site_name                TEXT,
    site                     TEXT,
    source_item_url          TEXT,
    item_edit_url            TEXT,
    breadcrumb               TEXT,
    owner_sub_app_account_id BIGINT,
    owner_sub_account_alias_name TEXT,
    raw_json                 JSONB,
    synced_at                TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (platform, move_collect_task_detail_id)
);
CREATE INDEX IF NOT EXISTS idx_miaoshou_move_collect_status ON miaoshou_move_collect_tasks (platform, status);
CREATE INDEX IF NOT EXISTS idx_miaoshou_move_collect_synced ON miaoshou_move_collect_tasks (synced_at DESC);
