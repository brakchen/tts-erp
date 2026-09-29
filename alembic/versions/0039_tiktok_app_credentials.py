"""Store TikTok Partner App credentials by service_id.

A TikTok OAuth token is issued by one Partner App.  The application pair
(app_key/app_secret) therefore cannot remain process-global once shops use
multiple service_id values.  This migration adds:

* ``integration.tiktok_app_credentials`` — one encrypted app-secret row per
  service_id;
* ``integration.credentials.service_id`` — the immutable issuing application
  binding for each TikTok access/refresh token.

Existing TikTok credential rows are backfilled from their linked shop when a
service_id is already known.  Rows still NULL keep the narrow legacy
TIKTOK_SERVICE_ID environment fallback until the seller reauthorizes.
"""

from alembic import op

revision: str = "0039_tiktok_app_credentials"
down_revision: str | None = "0038_statement_payout_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS integration.tiktok_app_credentials (
            service_id text PRIMARY KEY,
            app_key text NOT NULL,
            app_secret_ciphertext bytea NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE TRIGGER trg_integration_tiktok_app_credentials_touch
        BEFORE UPDATE ON integration.tiktok_app_credentials
        FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()
        """
    )
    op.execute(
        """
        ALTER TABLE integration.credentials
        ADD COLUMN IF NOT EXISTS service_id text
        """
    )
    op.execute(
        """
        UPDATE integration.credentials AS c
        SET service_id = s.service_id
        FROM commerce.shops AS s
        WHERE c.provider = 'tiktok'
          AND c.service_id IS NULL
          AND s.credential_id = c.id
          AND s.service_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_credentials_service_id
        ON integration.credentials (service_id)
        WHERE service_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS integration.ix_credentials_service_id
        """
    )
    op.execute(
        """
        ALTER TABLE integration.credentials
        DROP COLUMN IF EXISTS service_id
        """
    )
    op.execute(
        """
        DROP TABLE IF EXISTS integration.tiktok_app_credentials
        """
    )
