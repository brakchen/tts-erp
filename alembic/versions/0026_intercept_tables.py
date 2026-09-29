"""intercept tables: configs + requests + sessions + cursors

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-12 10:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision: str = '0026_intercept_tables'
down_revision: str | None = '0025_drop_shops_data_source'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plugin.intercept_configs
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.intercept_configs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            domain text NOT NULL,
            endpoint text NOT NULL,
            capture_headers boolean DEFAULT true,
            capture_body boolean DEFAULT true,
            description text,
            tags jsonb DEFAULT '[]',
            enabled boolean DEFAULT true,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT intercept_configs_domain_endpoint_unique UNIQUE (domain, endpoint)
        );
    """)
    op.execute("""
        CREATE INDEX idx_intercept_configs_domain 
        ON plugin.intercept_configs (domain) 
        WHERE enabled = true;
    """)
    op.execute("""
        CREATE UNIQUE INDEX idx_intercept_configs_domain_endpoint 
        ON plugin.intercept_configs (domain, endpoint);
    """)

    # plugin.intercepted_requests
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.intercepted_requests (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            request_id text NOT NULL UNIQUE,
            trace_id text,
            session_id text NOT NULL,
            method text NOT NULL,
            url text NOT NULL,
            url_hash text NOT NULL,
            endpoint_path text NOT NULL,
            endpoint_host text NOT NULL,
            is_whitelisted boolean NOT NULL,
            matched_config_id bigint,
            request_headers jsonb,
            request_body jsonb,
            response_status integer,
            response_status_text text,
            response_headers jsonb,
            response_body jsonb,
            duration_ms integer,
            error_type text,
            error_message text,
            seller_id text,
            advertiser_id text,
            business_context jsonb,
            pagination jsonb,
            captured_at timestamp with time zone NOT NULL,
            received_at timestamp with time zone DEFAULT now() NOT NULL
        );
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_captured_at 
        ON plugin.intercepted_requests (captured_at);
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_url_hash 
        ON plugin.intercepted_requests (url_hash);
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_endpoint 
        ON plugin.intercepted_requests (endpoint_host, endpoint_path);
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_session 
        ON plugin.intercepted_requests (session_id, captured_at);
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_seller 
        ON plugin.intercepted_requests (seller_id) 
        WHERE seller_id IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_whitelisted 
        ON plugin.intercepted_requests (is_whitelisted, captured_at);
    """)
    op.execute("""
        CREATE INDEX idx_intercepted_requests_config 
        ON plugin.intercepted_requests (matched_config_id) 
        WHERE matched_config_id IS NOT NULL;
    """)

    # plugin.intercept_sessions
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.intercept_sessions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            session_id text NOT NULL UNIQUE,
            tab_id integer,
            tab_url text,
            user_agent text,
            total_requests integer DEFAULT 0,
            whitelisted_requests integer DEFAULT 0,
            metadata_only_requests integer DEFAULT 0,
            started_at timestamp with time zone NOT NULL,
            last_request_at timestamp with time zone,
            ended_at timestamp with time zone,
            created_at timestamp with time zone DEFAULT now() NOT NULL
        );
    """)

    # plugin.intercept_sync_cursors
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.intercept_sync_cursors (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            cursor_key text NOT NULL UNIQUE,
            last_synced_id bigint,
            last_synced_at timestamp with time zone,
            total_synced integer DEFAULT 0,
            last_error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL
        );
    """)

    # updated_at trigger for intercept_configs
    op.execute("""
        CREATE OR REPLACE FUNCTION public.fn_touch_intercept_configs_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_intercept_configs_updated_at
        BEFORE UPDATE ON plugin.intercept_configs
        FOR EACH ROW EXECUTE FUNCTION public.fn_touch_intercept_configs_updated_at();
    """)

    # updated_at trigger for intercept_sync_cursors
    op.execute("""
        CREATE OR REPLACE FUNCTION public.fn_touch_intercept_sync_cursors_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_intercept_sync_cursors_updated_at
        BEFORE UPDATE ON plugin.intercept_sync_cursors
        FOR EACH ROW EXECUTE FUNCTION public.fn_touch_intercept_sync_cursors_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_intercept_sync_cursors_updated_at ON plugin.intercept_sync_cursors;")
    op.execute("DROP FUNCTION IF EXISTS public.fn_touch_intercept_sync_cursors_updated_at();")
    op.execute("DROP TRIGGER IF EXISTS trg_intercept_configs_updated_at ON plugin.intercept_configs;")
    op.execute("DROP FUNCTION IF EXISTS public.fn_touch_intercept_configs_updated_at();")
    op.execute("DROP TABLE IF EXISTS plugin.intercept_sync_cursors;")
    op.execute("DROP TABLE IF EXISTS plugin.intercept_sessions;")
    op.execute("DROP TABLE IF EXISTS plugin.intercepted_requests;")
    op.execute("DROP TABLE IF EXISTS plugin.intercept_configs;")
