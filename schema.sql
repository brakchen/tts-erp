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
    order_id              TEXT PRIMARY KEY,
    shop_id               TEXT NOT NULL,
    order_status          INT,
    order_status_name     TEXT,
    payment_amount        NUMERIC(18, 2),
    payment_currency      TEXT,
    total_amount          NUMERIC(18, 2),
    buyer_email           TEXT,
    buyer_message         TEXT,
    create_time           BIGINT,           -- unix ts (seconds)
    update_time           BIGINT,
    paid_time             BIGINT,
    shipped_time          BIGINT,
    delivered_time        BIGINT,
    cancelled_time        BIGINT,
    fulfillment_type      TEXT,             -- 'FULFILLMENT_BY_SELLER' / 'FULFILLMENT_BY_TIKTOK'
    raw                   JSONB NOT NULL,   -- full TikTok order response
    synced_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_orders_shop         ON orders (shop_id);
CREATE INDEX IF NOT EXISTS idx_orders_status       ON orders (order_status);
CREATE INDEX IF NOT EXISTS idx_orders_create_time  ON orders (create_time DESC);
CREATE INDEX IF NOT EXISTS idx_orders_shop_status  ON orders (shop_id, order_status);

CREATE TABLE IF NOT EXISTS order_items (
    order_id       TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    shop_id        TEXT,
    sku_id         TEXT,
    product_id     TEXT,
    product_name   TEXT,
    sku_name       TEXT,
    sku_image      TEXT,
    quantity       INT,
    sku_price      NUMERIC(18, 2),
    raw            JSONB NOT NULL,
    PRIMARY KEY (order_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_order_items_shop ON order_items (shop_id);

CREATE TABLE IF NOT EXISTS order_shippings (
    order_id               TEXT PRIMARY KEY,
    shop_id                TEXT,
    tracking_number        TEXT,
    shipping_provider_id   TEXT,
    shipping_provider_name TEXT,
    raw                    JSONB NOT NULL,
    synced_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              BIGSERIAL PRIMARY KEY,
    shop_id         TEXT,
    sync_type       TEXT,             -- 'orders_search' | 'order_detail' | 'shipping' | 'tracking' | 'statements' | 'payments'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    rows_affected   INT,
    status          TEXT,             -- 'ok' | 'error'
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_log_shop ON sync_log (shop_id, started_at DESC);

-- ============================================================
-- Finance / Statement / Payment tables (get-statements-202309)
-- ============================================================
--
-- statements: each row is one settlement/billing statement.
-- Confirmed 2026-08-16: GET /finance/202309/statements returns these fields.
CREATE TABLE IF NOT EXISTS statements (
    statement_id          TEXT PRIMARY KEY,         -- TikTok "id" field
    shop_id               TEXT,
    payment_id            TEXT,                     -- ties to payments.payment_id
    currency              TEXT,                     -- 'VND' / 'USD' / etc.
    payment_status        TEXT,                     -- 'PAID' / 'PENDING' etc.
    statement_time        BIGINT,                   -- unix ts seconds, period end
    payment_time          BIGINT,                   -- unix ts seconds, actual payment
    revenue_amount        NUMERIC(18, 2),
    fee_amount            NUMERIC(18, 2),           -- negative number in response
    net_sales_amount      NUMERIC(18, 2),
    shipping_cost_amount  NUMERIC(18, 2),
    adjustment_amount     NUMERIC(18, 2),
    settlement_amount     NUMERIC(18, 2),           -- net amount paid to seller
    raw                   JSONB NOT NULL,
    synced_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_statements_shop        ON statements (shop_id);
CREATE INDEX IF NOT EXISTS idx_statements_payment_id  ON statements (payment_id);
CREATE INDEX IF NOT EXISTS idx_statements_stime       ON statements (statement_time DESC);

-- payments: each row is one outgoing payment to seller bank account.
-- Confirmed 2026-08-16: GET /finance/202309/payments returns these fields.
CREATE TABLE IF NOT EXISTS payments (
    payment_id                    TEXT PRIMARY KEY,    -- TikTok "id" field
    shop_id                       TEXT,
    status                        TEXT,                -- 'PAID' / 'PENDING' / 'FAILED'
    currency                      TEXT,
    amount_value                  NUMERIC(18, 2),      -- nested "amount.value"
    settlement_amount_value       NUMERIC(18, 2),      -- nested "settlement_amount.value"
    payment_amount_before_value   NUMERIC(18, 2),      -- before currency exchange
    reserve_amount_value          NUMERIC(18, 2),      -- held in reserve
    exchange_rate                 TEXT,                -- string "1" etc
    bank_account                  TEXT,                -- masked "*************200659"
    create_time                   BIGINT,
    paid_time                     BIGINT,
    raw                           JSONB NOT NULL,
    synced_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_payments_shop      ON payments (shop_id);
CREATE INDEX IF NOT EXISTS idx_payments_status    ON payments (status);
CREATE INDEX IF NOT EXISTS idx_payments_paid_time ON payments (paid_time DESC);

-- ============================================================
-- Return / Refund / Cancellation tables (return-refund-and-cancel-api-202309)
-- ============================================================
--
-- Two READ endpoints confirmed 2026-08-16 (probe_refund_v3/v5):
--   POST /return_refund/202309/returns/search       → data.return_orders[]
--   POST /return_refund/202309/cancellations/search  → data.cancellations[]
--
-- All other paths (/<id> detail, /list, /reverse/202309/*) returned
-- 36009009 or HTTP 404 — see handoff.md.
-- WRITE endpoints (POST /return_refund/202309/returns and
-- POST /return_refund/202309/cancellations, which create new return/
-- cancellation requests) are exposed by TikTok but NOT integrated
-- here per user instruction (no high-risk write testing).

CREATE TABLE IF NOT EXISTS returns (
    return_id          TEXT PRIMARY KEY,           -- TikTok "id" field of return order
    shop_id            TEXT,
    order_id           TEXT,                       -- the order being returned
    return_status      TEXT,                       -- 'AWAITING_SELLER_RESPONSE' / 'REFUND_PENDING' / 'CLOSED' / etc
    return_reason      TEXT,
    return_type        TEXT,
    role               TEXT,                       -- 'BUYER' / 'SELLER'
    create_time        BIGINT,                     -- unix ts seconds
    update_time        BIGINT,
    raw                JSONB NOT NULL,             -- full TikTok response
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_returns_shop        ON returns (shop_id);
CREATE INDEX IF NOT EXISTS idx_returns_order       ON returns (order_id);
CREATE INDEX IF NOT EXISTS idx_returns_status      ON returns (return_status);
CREATE INDEX IF NOT EXISTS idx_returns_create_time ON returns (create_time DESC);

-- cancellations: buyer- or seller-initiated cancellation of an order before fulfilment.
-- Confirmed 2026-08-16: 5 records in shop 7494763368967603447. Each row has
-- nested cancel_line_items[] (sku_id, sku_name, product_image, etc) — kept in raw.
CREATE TABLE IF NOT EXISTS cancellations (
    cancel_id          TEXT PRIMARY KEY,           -- TikTok "cancel_id" field
    shop_id            TEXT,
    order_id           TEXT,
    cancel_status      TEXT,                       -- 'CANCELLATION_REQUEST_COMPLETE' / 'AWAITING_SELLER_RESPONSE' / etc
    cancel_reason      TEXT,                       -- machine code: 'ecom_order_to_ship_canceled_reason_*'
    cancel_reason_text TEXT,                       -- human-readable: 'No longer needed' / etc
    cancel_type        TEXT,                       -- 'BUYER_CANCEL' / 'SELLER_CANCEL'
    role               TEXT,                       -- 'BUYER' / 'SELLER'
    should_replenish_stock BOOLEAN,
    create_time        BIGINT,
    update_time        BIGINT,
    raw                JSONB NOT NULL,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cancellations_shop        ON cancellations (shop_id);
CREATE INDEX IF NOT EXISTS idx_cancellations_order       ON cancellations (order_id);
CREATE INDEX IF NOT EXISTS idx_cancellations_status      ON cancellations (cancel_status);
CREATE INDEX IF NOT EXISTS idx_cancellations_create_time ON cancellations (create_time DESC);

-- updated_at auto-touch
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shops_touch  ON shops;
CREATE TRIGGER trg_shops_touch  BEFORE UPDATE ON shops  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS trg_orders_touch ON orders;
CREATE TRIGGER trg_orders_touch BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
