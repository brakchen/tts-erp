-- analytics_sync protocolVersion 2 migration
-- Run as: docker exec -i postgres psql -U postgres -d tts_erp < analytics_sync/migration_v2.sql
--
-- This migration is idempotent and backward-compatible:
--   - Adds expected_page_count to analytics_records (nullable; v1 rows stay valid).
--   - Creates analytics_daily_pages and analytics_daily_completeness.
--   - Backfills daily completeness from existing analytics_records rows
--     using v1 semantics (every existing day is implicitly expected=1 and complete).
--   - Adds the required six-column lookup index on analytics_records.

BEGIN;

-- ─── 1. Add expected_page_count to raw records ────────────────────────
ALTER TABLE analytics_records
    ADD COLUMN IF NOT EXISTS expected_page_count INT;

-- Existing v1 rows implicitly represent a single page; backfill in step 4.

-- ─── 2. Track which pages have been durably stored per daily unit ───────
CREATE TABLE IF NOT EXISTS analytics_daily_pages (
    seller_id      TEXT        NOT NULL,
    advertiser_id  TEXT        NOT NULL,
    storage_key    TEXT        NOT NULL,
    campaign_id    TEXT        NOT NULL,
    day            DATE        NOT NULL,
    page           INT         NOT NULL,
    inserted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_analytics_daily_pages PRIMARY KEY (
        seller_id, advertiser_id, storage_key, campaign_id, day, page
    ),
    CONSTRAINT ck_analytics_daily_pages_storage CHECK (
        storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')
    ),
    CONSTRAINT ck_analytics_daily_pages_page CHECK (page > 0)
);

-- Fast lookup of all pages for a daily unit during completeness checks.
CREATE INDEX IF NOT EXISTS idx_analytics_daily_pages_unit
    ON analytics_daily_pages (seller_id, advertiser_id, storage_key, campaign_id, day);

-- ─── 3. Aggregate daily completeness state ────────────────────────────
-- This table is the source of truth for "is this day complete?".
-- It is kept in sync with analytics_daily_pages inside the batch transaction.
CREATE TABLE IF NOT EXISTS analytics_daily_completeness (
    seller_id           TEXT        NOT NULL,
    advertiser_id       TEXT        NOT NULL,
    storage_key         TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL,
    day                 DATE        NOT NULL,
    expected_page_count INT         NOT NULL,
    is_complete         BOOLEAN     NOT NULL DEFAULT FALSE,
    completed_at        TIMESTAMPTZ,
    last_recomputed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_analytics_daily_completeness PRIMARY KEY (
        seller_id, advertiser_id, storage_key, campaign_id, day
    ),
    CONSTRAINT ck_analytics_daily_completeness_storage CHECK (
        storage_key IN ('productAnalyses', 'sessionAnalyses', 'campaignChangeLogs')
    ),
    CONSTRAINT ck_analytics_daily_completeness_expected CHECK (expected_page_count > 0)
);

-- Cursor recompute reads complete days for a unit in a range.
CREATE INDEX IF NOT EXISTS idx_analytics_daily_completeness_unit_complete
    ON analytics_daily_completeness (
        seller_id, advertiser_id, storage_key, campaign_id, day, is_complete
    );

-- ─── 4. Backfill from existing analytics_records ──────────────────────
-- Treat every existing record as a single-page day (v1 semantics).
INSERT INTO analytics_daily_pages (
    seller_id, advertiser_id, storage_key, campaign_id, day, page
)
SELECT DISTINCT
    seller_id, advertiser_id, storage_key, campaign_id, day, page
FROM analytics_records
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day, page)
DO NOTHING;

INSERT INTO analytics_daily_completeness (
    seller_id, advertiser_id, storage_key, campaign_id, day,
    expected_page_count, is_complete, completed_at, last_recomputed_at
)
SELECT
    seller_id, advertiser_id, storage_key, campaign_id, day,
    1, TRUE, now(), now()
FROM (
    SELECT DISTINCT seller_id, advertiser_id, storage_key, campaign_id, day
    FROM analytics_records
) AS existing_days
ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id, day)
DO UPDATE SET
    expected_page_count = 1,
    is_complete = TRUE,
    completed_at = now(),
    last_recomputed_at = now()
WHERE analytics_daily_completeness.is_complete = FALSE;

-- Backfill expected_page_count on the raw records themselves.
UPDATE analytics_records
SET expected_page_count = 1
WHERE expected_page_count IS NULL;

-- ─── 5. Add the six-column lookup index required by v2 ────────────────
CREATE INDEX IF NOT EXISTS idx_analytics_records_scope_page
    ON analytics_records (seller_id, advertiser_id, storage_key, campaign_id, day, page);

-- ─── 6. Anchor day for cursor contiguity (v2) ────────────────────────
-- first_seen_day is the earliest day the unit has any completeness row
-- for. The cursor chain walks forward from this anchor (not from the
-- bootstrap lookback day), so a unit whose first-ever sync is recent
-- does not need to backfill empty earlier days before its cursor can
-- advance. Interior gaps (a known day that is incomplete or absent
-- between anchor and today) still block advancement.
ALTER TABLE analytics_cursors
    ADD COLUMN IF NOT EXISTS first_seen_day DATE;

COMMIT;
