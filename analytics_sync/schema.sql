-- analytics_sync schema
-- Run as: docker exec -i postgres psql -U postgres -d tts_erp < analytics_sync/schema.sql
--
-- This service lives inside the tts-erp PostgreSQL database (same pattern
-- as miaoshou_* and api_keys tables). All tables are namespaced with
-- `analytics_*` to keep them isolated from tts-erp's own data.
--
-- Idempotent: every CREATE uses IF NOT EXISTS. Safe to re-apply on a
-- populated database.

-- ─── Records ─────────────────────────────────────────────────────────
-- Raw response JSON + normalized scope columns. The unique index on
-- idempotency_key makes inserts idempotent: a duplicate insert is
-- treated as a duplicate, not an error.
CREATE TABLE IF NOT EXISTS analytics_records (
    id                  BIGSERIAL PRIMARY KEY,
    idempotency_key     TEXT        NOT NULL,
    source_record_id    TEXT,
    seller_id           TEXT        NOT NULL,
    advertiser_id       TEXT        NOT NULL,
    storage_key         TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL,
    day                 DATE        NOT NULL,
    page                INT         NOT NULL,
    shop_name           TEXT,
    endpoint            TEXT        NOT NULL,
    method              TEXT        NOT NULL,
    request_body        JSONB,
    response_data       JSONB       NOT NULL,
    source              TEXT        NOT NULL,
    captured_at         TIMESTAMPTZ NOT NULL,
    schema_version      INT         NOT NULL DEFAULT 1,
    protocol_version    INT         NOT NULL DEFAULT 1,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id          TEXT,
    CONSTRAINT uq_analytics_records_idem        UNIQUE (idempotency_key),
    CONSTRAINT ck_analytics_records_storage    CHECK (storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')),
    CONSTRAINT ck_analytics_records_page       CHECK (page > 0),
    CONSTRAINT ck_analytics_records_schema     CHECK (schema_version > 0),
    CONSTRAINT ck_analytics_records_protocol   CHECK (protocol_version > 0)
);

-- Lookup by scope (cursor queries).
CREATE INDEX IF NOT EXISTS idx_analytics_records_scope
    ON analytics_records (seller_id, advertiser_id, storage_key, campaign_id, day);

-- Diagnostics by request_id (audit lookup).
CREATE INDEX IF NOT EXISTS idx_analytics_records_request
    ON analytics_records (request_id);

-- Recent ingest for ops monitoring.
CREATE INDEX IF NOT EXISTS idx_analytics_records_received
    ON analytics_records (received_at DESC);


-- ─── Cursors ─────────────────────────────────────────────────────────
-- One row per (sellerId, advertiserId, storageKey, campaignId).
-- latest_completed_day is the most recent calendar day (in shop TZ) for
-- which at least one record has been durably stored. Atomic upsert
-- (see analytics_sync/app.py::_advance_cursors) only ever advances it,
-- never regresses.
CREATE TABLE IF NOT EXISTS analytics_cursors (
    seller_id            TEXT        NOT NULL,
    advertiser_id        TEXT        NOT NULL,
    storage_key          TEXT        NOT NULL,
    campaign_id          TEXT        NOT NULL,
    latest_completed_day DATE,
    last_updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id           TEXT,
    PRIMARY KEY (seller_id, advertiser_id, storage_key, campaign_id),
    CONSTRAINT ck_analytics_cursors_storage CHECK (storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs'))
);


-- ─── Per-shop timezone ───────────────────────────────────────────────
-- Plugin's request body may not carry a timezone. The server keeps the
-- canonical IANA timezone per seller (1 seller typically = 1 advertiser,
-- but we don't enforce that here — a seller may operate multiple
-- advertiser accounts). The cursor bootstrap date and `nextRequiredDay`
-- are computed in this timezone. Default Asia/Shanghai matches the
-- current extension deployment.
CREATE TABLE IF NOT EXISTS analytics_shop_timezones (
    seller_id     TEXT PRIMARY KEY,
    advertiser_id TEXT        NOT NULL,
    timezone      TEXT        NOT NULL DEFAULT 'Asia/Shanghai',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ─── Sync tokens ─────────────────────────────────────────────────────
-- Bearer tokens used by the Chrome extension. Plaintext is shown ONCE
-- at create/rotate time and never stored. SHA-256 hex digest lives in
-- key_hash; key_prefix (first 16 chars) is the operator-facing id.
CREATE TABLE IF NOT EXISTS analytics_sync_tokens (
    id           BIGSERIAL    PRIMARY KEY,
    key_prefix   TEXT         NOT NULL,
    key_hash     TEXT         NOT NULL,
    name         TEXT,
    scopes       TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    CONSTRAINT uq_analytics_sync_tokens_prefix UNIQUE (key_prefix)
);

CREATE INDEX IF NOT EXISTS idx_analytics_sync_tokens_enabled
    ON analytics_sync_tokens (enabled) WHERE enabled;


-- ─── Audit log ───────────────────────────────────────────────────────
-- requestId-keyed audit trail for ops diagnostics. No secrets: only
-- status codes, record counts, key prefix. Retention is operator's
-- responsibility (cron job).
CREATE TABLE IF NOT EXISTS analytics_audit_log (
    id           BIGSERIAL    PRIMARY KEY,
    request_id   TEXT,
    endpoint     TEXT         NOT NULL,
    method       TEXT         NOT NULL,
    path         TEXT         NOT NULL,
    status       INT          NOT NULL,
    key_prefix   TEXT,
    records_in   INT,
    records_ok   INT,
    records_rej  INT,
    error_code   TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_audit_request
    ON analytics_audit_log (request_id);

CREATE INDEX IF NOT EXISTS idx_analytics_audit_created
    ON analytics_audit_log (created_at DESC);
