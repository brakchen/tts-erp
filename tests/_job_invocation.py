"""Finish test arrangement before entering a job that owns its transactions.

The canonical db_session fixture joins an outer rollback transaction through
SAVEPOINTs. Committing arrangement releases only that fixture SAVEPOINT; it
does not escape test isolation. Evaluate arguments before this boundary so
expired seed objects cannot autobegin a transaction inside the job invocation.
New transaction-boundary tests use fresh Sessions directly instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.orm import Session

Result = TypeVar("Result")


def invoke_seeded_job(
    function: Callable[..., Result], session: Session, *args: Any, **kwargs: Any
) -> Result:
    session.commit()
    return function(session, *args, **kwargs)


def credential_snapshot(provider: str, external_account_id: str):
    """Immutable dummy view for callback-contract tests without persistence."""
    from tts_erp_v2.proxy.token_service import CredentialsView

    return CredentialsView(
        id=0,
        provider=provider,
        external_account_id=external_account_id,
        account_label=None,
        access_token="TEST_ACCESS",
        refresh_token="TEST_REFRESH",
        shop_cipher="TEST_CIPHER",
        expires_at=None,
        granted_scopes=None,
        service_id="TEST_SERVICE",
        extra=None,
    )


def authorize_test_shop(session: Session, shop_pk: int, shop_id: str) -> None:
    """Arrange the current shop-owned App/credential authorization contract."""
    from tts_erp_v2.db.models import ChannelAccount
    from tts_erp_v2.proxy.token_service import (
        upsert_credentials,
        upsert_tiktok_app_credentials,
    )

    credential = upsert_credentials(
        session, provider="tiktok", external_account_id=shop_id,
        plaintext_access_token="TEST_AUTH_ACCESS", plaintext_refresh_token="TEST_AUTH_REFRESH",
        service_id="TEST_AUTH_SERVICE",
    )
    shop = session.get(ChannelAccount, shop_pk)
    shop.credential_id = credential.id
    shop.service_id = "TEST_AUTH_SERVICE"
    upsert_tiktok_app_credentials(
        session, shop_pk=shop_pk, service_id="TEST_AUTH_SERVICE",
        app_key="TEST_APP_KEY", plaintext_app_secret="TEST_APP_SECRET",
    )
    session.flush()
