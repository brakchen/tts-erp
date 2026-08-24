-- OAuth Receiver token storage schema
-- Run as: psql -U postgres -f schema.sql
-- Or inside the postgres container: docker exec -i postgres psql -U postgres < schema.sql

-- Create the database (idempotent)
SELECT 'CREATE DATABASE oauth_receiver ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oauth_receiver')\gexec

\c oauth_receiver

-- Drop existing table (be careful: drops all token data)
-- DROP TABLE IF EXISTS oauth_tokens;

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id                       BIGSERIAL PRIMARY KEY,
    shop_id                  TEXT        NOT NULL,
    provider                 TEXT        NOT NULL DEFAULT 'tiktok',
    -- Encrypted columns (Fernet / AES-128-CBC + HMAC-SHA256, key from env)
    access_token_encrypted   BYTEA       NOT NULL,
    refresh_token_encrypted  BYTEA       NOT NULL,
    shop_cipher_encrypted    BYTEA,
    -- Plaintext metadata (not sensitive)
    shop_name                TEXT,
    shop_region              TEXT,
    seller_type              TEXT,
    access_token_expires_at  BIGINT,         -- unix timestamp
    refresh_token_expires_at BIGINT,         -- unix timestamp
    granted_scopes           TEXT[],
    -- Audit
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at             TIMESTAMPTZ,
    last_refresh_at          TIMESTAMPTZ,
    -- One row per (shop_id, provider)
    CONSTRAINT oauth_tokens_shop_provider_unique UNIQUE (shop_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens (provider);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_expires ON oauth_tokens (access_token_expires_at);

-- updated_at auto-touch trigger
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_oauth_tokens_touch ON oauth_tokens;
CREATE TRIGGER trg_oauth_tokens_touch
    BEFORE UPDATE ON oauth_tokens
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE oauth_tokens IS
    'OAuth token storage for the oauth-receiver service. Sensitive columns (access_token, refresh_token, shop_cipher) are encrypted with Fernet; key loaded from OAUTH_DB_ENCRYPTION_KEY env var.';

COMMENT ON COLUMN oauth_tokens.shop_id IS
    'TikTok Shop id (or other provider-specific shop identifier). Used for isolation — each shop has its own row.';

COMMENT ON COLUMN oauth_tokens.access_token_encrypted IS
    'Fernet-encrypted OAuth access_token. Decrypt with the same key to use.';

COMMENT ON COLUMN oauth_tokens.refresh_token_encrypted IS
    'Fernet-encrypted OAuth refresh_token. Use to mint new access_token when current one expires.';
