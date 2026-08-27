-- analytics_sync schema
-- Run as: docker exec -i postgres psql -U postgres -d tts_erp < analytics_sync/schema.sql
--
-- This service lives inside the tts-erp PostgreSQL database (same pattern
-- as miaoshou_* tables). All analytics tables are namespaced with
-- `analytics_*` to keep them isolated from tts-erp's own data.
--
-- Bearer tokens for analytics_sync are NOT stored here — they share
-- tts-erp's `api_keys` table (with the `scopes` column for per-seller
-- restriction). Use `python3 api_keys.py create --role readwrite
-- --scopes "seller:..."` to issue a Chrome extension sync token.
--
-- Idempotent: every CREATE uses IF NOT EXISTS. Safe to re-apply on a
-- populated database.

-- ─── Records ─────────────────────────────────────────────────────────
-- Raw response JSON + normalized scope columns. The unique index on
-- idempotency_key makes inserts idempotent: a duplicate insert is
-- treated as a duplicate, not an error.
CREATE TABLE IF NOT EXISTS analytics_records (
    id BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    source_record_id TEXT,
    seller_id TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    day DATE NOT NULL,
    page INT NOT NULL,
    shop_name TEXT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    request_body JSONB,
    response_data JSONB NOT NULL,
    source TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    -- v2: total pages for this day; NULL for legacy rows
    expected_page_count INT,
    schema_version INT NOT NULL DEFAULT 1,
    protocol_version INT NOT NULL DEFAULT 1,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id TEXT,
    CONSTRAINT uq_analytics_records_idem UNIQUE (idempotency_key),
    constraint CK_ANALYTICS_RECORDS_STORAGE CHECK (
        storage_key IN (
            'productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'
        )
    ),
    constraint CK_ANALYTICS_RECORDS_PAGE CHECK (page > 0),
    constraint CK_ANALYTICS_RECORDS_SCHEMA CHECK (schema_version > 0),
    constraint CK_ANALYTICS_RECORDS_PROTOCOL CHECK (protocol_version > 0)
);

-- Lookup by scope (cursor queries).
CREATE INDEX IF NOT EXISTS idx_analytics_records_scope
ON analytics_records (seller_id, advertiser_id, storage_key, campaign_id, day);

-- v2: lookup by scope + page for idempotency and completeness checks.
CREATE INDEX IF NOT EXISTS idx_analytics_records_scope_page
ON analytics_records (
    seller_id, advertiser_id, storage_key, campaign_id, day, page
);

-- Diagnostics by request_id (audit lookup).
CREATE INDEX IF NOT EXISTS idx_analytics_records_request
ON analytics_records (request_id);

-- Recent ingest for ops monitoring.
CREATE INDEX IF NOT EXISTS idx_analytics_records_received
ON analytics_records (received_at DESC);


-- ─── Daily pages (v2) ─────────────────────────────────────────────────
-- One row per (scope, storageKey, campaignId, day, page) that has been
-- durably stored. The PK makes concurrent inserts safe and lets the
-- completeness check rely on the set of pages.
CREATE TABLE IF NOT EXISTS analytics_daily_pages (
    seller_id TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    day DATE NOT NULL,
    page INT NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_analytics_daily_pages PRIMARY KEY (
        seller_id, advertiser_id, storage_key, campaign_id, day, page
    ),
    constraint CK_ANALYTICS_DAILY_PAGES_STORAGE CHECK (
        storage_key IN (
            'productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'
        )
    ),
    constraint CK_ANALYTICS_DAILY_PAGES_PAGE CHECK (page > 0)
);

CREATE INDEX IF NOT EXISTS idx_analytics_daily_pages_unit
ON analytics_daily_pages (
    seller_id, advertiser_id, storage_key, campaign_id, day
);


-- ─── Daily completeness (v2) ──────────────────────────────────────────
-- Aggregate source of truth for "is this day complete?". Updated inside
-- the batch transaction after analytics_daily_pages changes.
CREATE TABLE IF NOT EXISTS analytics_daily_completeness (
    seller_id TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    day DATE NOT NULL,
    expected_page_count INT NOT NULL,
    is_complete BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    last_recomputed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_analytics_daily_completeness PRIMARY KEY (
        seller_id, advertiser_id, storage_key, campaign_id, day
    ),
    constraint CK_ANALYTICS_DAILY_COMPLETENESS_STORAGE CHECK (
        storage_key IN (
            'productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'
        )
    ),
    constraint CK_ANALYTICS_DAILY_COMPLETENESS_EXPECTED CHECK (
        expected_page_count > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_analytics_daily_completeness_unit_complete
ON analytics_daily_completeness (
    seller_id, advertiser_id, storage_key, campaign_id, day, is_complete
);


-- ─── Cursors ─────────────────────────────────────────────────────────
-- One row per (sellerId, advertiserId, storageKey, campaignId).
-- latest_completed_day is the most recent calendar day (in shop TZ) for
-- which at least one record has been durably stored. Atomic upsert
-- (see analytics_sync/app.py::_advance_cursors) only ever advances it,
-- never regresses.
CREATE TABLE IF NOT EXISTS analytics_cursors (
    seller_id TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    latest_completed_day DATE,
    first_seen_day DATE,                  -- v2: earliest day with any record; anchor of the contiguity chain
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id TEXT,
    PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id),
    constraint CK_ANALYTICS_CURSORS_STORAGE CHECK (
        storage_key IN (
            'productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'
        )
    )
);

-- v2 upgrades on existing installs:
ALTER TABLE analytics_cursors ADD COLUMN IF NOT EXISTS first_seen_day DATE;


-- ─── Per-shop timezone ───────────────────────────────────────────────
-- Plugin's request body may not carry a timezone. The server keeps the
-- canonical IANA timezone per seller (1 seller typically = 1 advertiser,
-- but we don't enforce that here — a seller may operate multiple
-- advertiser accounts). The cursor bootstrap date and `nextRequiredDay`
-- are computed in this timezone. Default Asia/Shanghai matches the
-- current extension deployment.
CREATE TABLE IF NOT EXISTS analytics_shop_timezones (
    seller_id TEXT PRIMARY KEY,
    advertiser_id TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- (Sync tokens table removed in 2026-08-23 refactor; see api_keys table
-- in schema.sql at the repo root.)


-- ─── Audit log ───────────────────────────────────────────────────────
-- requestId-keyed audit trail for ops diagnostics. No secrets: only
-- status codes, record counts, key prefix. Retention is operator's
-- responsibility (cron job).
CREATE TABLE IF NOT EXISTS analytics_audit_log (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INT NOT NULL,
    key_prefix TEXT,
    records_in INT,
    records_ok INT,
    records_rej INT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_audit_request
ON analytics_audit_log (request_id);

CREATE INDEX IF NOT EXISTS idx_analytics_audit_created
ON analytics_audit_log (created_at DESC);
