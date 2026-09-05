-- schema_storage.sql — procurement.spu_images + indexes
-- Idempotent: safe to re-apply on an existing DB.
-- Ref: tech-doc/procurement-ui-redesign.md §4

CREATE SCHEMA IF NOT EXISTS procurement;

CREATE TABLE IF NOT EXISTS procurement.spu_images (
    id                  BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    shop_pk  BIGINT NOT NULL
        REFERENCES commerce.shops(id) ON DELETE RESTRICT,
    spu_pk  BIGINT NOT NULL
        REFERENCES commerce.products_spu(id) ON DELETE RESTRICT,
    object_key          TEXT NOT NULL UNIQUE,
    filename            TEXT NOT NULL,
    content_type        TEXT NOT NULL,
    size_bytes          BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 8388608),
    status              TEXT NOT NULL DEFAULT 'awaiting_upload'
        CHECK (status IN ('awaiting_upload','ready','failed')),
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by_key_id  BIGINT REFERENCES security.api_keys(id) ON DELETE SET NULL,
    uploaded_by_prefix  TEXT,
    deleted_at          TIMESTAMPTZ,
    failure_reason      TEXT,
    raw_metadata        JSONB
);

CREATE INDEX IF NOT EXISTS ix_spu_images_product_status
    ON procurement.spu_images (spu_pk, status)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_spu_images_account_uploaded
    ON procurement.spu_images (shop_pk, uploaded_at DESC)
    WHERE deleted_at IS NULL;
