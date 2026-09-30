"""Add versioned runtime configuration and encrypted secret references.

Revision ID: 0049_runtime_config_management
Revises: 0048_retire_monthly_ad_sync
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0049_runtime_config_management"
down_revision: str | None = "0048_retire_monthly_ad_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE config.runtime_config_items (
                config_key       VARCHAR(128) PRIMARY KEY,
                display_name     VARCHAR(256) NOT NULL,
                json_schema      JSONB NOT NULL,
                draft_payload    JSONB,
                draft_rollout    JSONB NOT NULL DEFAULT '[]'::jsonb,
                draft_version    INTEGER NOT NULL DEFAULT 0,
                published_version INTEGER,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_runtime_config_draft_version_nonnegative
                    CHECK (draft_version >= 0),
                CONSTRAINT ck_runtime_config_published_version_positive
                    CHECK (published_version IS NULL OR published_version > 0)
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE config.runtime_config_revisions (
                id          BIGSERIAL PRIMARY KEY,
                config_key  VARCHAR(128) NOT NULL
                    REFERENCES config.runtime_config_items(config_key) ON DELETE CASCADE,
                version     INTEGER NOT NULL,
                payload     JSONB NOT NULL,
                rollout     JSONB NOT NULL DEFAULT '[]'::jsonb,
                comment     TEXT,
                created_by  VARCHAR(128) NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_runtime_config_revision UNIQUE (config_key, version),
                CONSTRAINT ck_runtime_config_revision_positive CHECK (version > 0)
            )
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX ix_runtime_config_revisions_key_version
            ON config.runtime_config_revisions (config_key, version DESC)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE config.runtime_config_secrets (
                name            VARCHAR(128) PRIMARY KEY,
                encrypted_value BYTEA NOT NULL,
                fingerprint     VARCHAR(32) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    for table in ("runtime_config_items", "runtime_config_secrets"):
        op.execute(
            text(
                f"""
                CREATE TRIGGER trg_{table}_updated_at
                BEFORE UPDATE ON config.{table}
                FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
                """
            )
        )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS config.runtime_config_secrets"))
    op.execute(text("DROP TABLE IF EXISTS config.runtime_config_revisions"))
    op.execute(text("DROP TABLE IF EXISTS config.runtime_config_items"))
