-- Wave 2 schema housekeeping migration (2026-08-27)
-- Plan: plans/review-remediation-2026-08.md
-- Apply to tts_erp DB:
--   docker exec -i postgres psql -U postgres -d tts_erp < migrations/wave2_schema_housekeeping.sql
-- Idempotent: all statements use IF EXISTS / IF NOT EXISTS / CREATE OR REPLACE.

-- ─── W2.1a Drop duplicate / redundant indexes ────────────────────────
-- exact duplicates (same table, same column):
DROP INDEX IF EXISTS idx_logistics_tracking_final;       -- ≡ idx_logistics_tracking_final_status
DROP INDEX IF EXISTS idx_logistics_tracking_tracking;    -- ≡ idx_logistics_tracking_tracking_number
-- redundant with PK (order_id, action_code, event_time) leading column:
DROP INDEX IF EXISTS idx_lt_events_order;
-- boolean full index has near-zero selectivity → replace with partial:
DROP INDEX IF EXISTS idx_logistics_tracking_overseas;
CREATE INDEX IF NOT EXISTS idx_logistics_tracking_overseas
    ON logistics_tracking (arrived_overseas) WHERE arrived_overseas;

-- ─── W2.1b Missing indexes for hot query paths ───────────────────────
-- /db/orders keyset pagination: WHERE shop_id=? ORDER BY create_time DESC, order_id DESC
CREATE INDEX IF NOT EXISTS idx_orders_shop_ct
    ON orders (shop_id, create_time DESC, order_id DESC);
-- cron logistics_target_ids runs every 10 min per shop:
CREATE INDEX IF NOT EXISTS idx_order_shippings_tracking
    ON order_shippings (shop_id, order_id)
    WHERE tracking_number IS NOT NULL AND tracking_number <> '';
-- /db/orders?status= filter:
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (order_status_name);
-- /db/statement_transactions?type= filter:
CREATE INDEX IF NOT EXISTS idx_stmt_txns_type ON statement_transactions (type);

-- ─── W2.2 Dead table + dead column ───────────────────────────────────
-- logistics_events: zero rows, zero INSERT writers in the codebase;
-- /db/logistics_events actually reads logistics_tracking_events.
DROP TABLE IF EXISTS logistics_events;
-- shops.shop_cipher: persist_shop stopped writing it in W1.3 (plaintext
-- signing credential must live only in oauth_receiver, encrypted).
ALTER TABLE shops DROP COLUMN IF EXISTS shop_cipher;

-- ─── W2.3 sync_log retention: single entry point ─────────────────────
-- The trigger previously inlined its own DELETE with a hardcoded 60 days,
-- drifting from cleanup_sync_log(retention_days). Route through the
-- function so there is exactly one retention implementation.
CREATE OR REPLACE FUNCTION public.trg_sync_log_retention_fn() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM cleanup_sync_log(60);
    RETURN NULL;  -- AFTER STATEMENT trigger ignores the return value
END;
$$;
