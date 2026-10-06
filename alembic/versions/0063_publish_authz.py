"""withhold video-publish permission until manual operator rollout

Revision ID: 0063_publish_authz
Revises: 0062_publish_attempt_identity
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0063_publish_authz"
down_revision: str | None = "0062_publish_attempt_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the page admin-only until the documented manual operator grant."""
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DELETE FROM security.role_permissions
        WHERE role_code = 'operator'
          AND permission_code = 'page:video-publish'
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        INSERT INTO security.role_permissions (role_code, permission_code)
        SELECT 'admin', 'page:video-publish'
        WHERE EXISTS (SELECT 1 FROM security.roles WHERE code = 'admin')
        ON CONFLICT DO NOTHING
        """)
    )


def downgrade() -> None:
    # A downgrade must not silently broaden operator authorization.
    pass
