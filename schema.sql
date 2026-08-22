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

-- API key auth (3-level: readonly / readwrite / admin)
CREATE TABLE IF NOT EXISTS api_keys (
    id            BIGSERIAL PRIMARY KEY,
    key_prefix    TEXT NOT NULL UNIQUE,   -- 8-char prefix for display + lookup
    key_hash      TEXT NOT NULL,           -- SHA-256(plaintext_key)
    role          TEXT NOT NULL CHECK (role IN ('readonly', 'readwrite', 'admin')),
    name          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    expires_at    TIMESTAMPTZ                  -- NULL = never expires
);
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