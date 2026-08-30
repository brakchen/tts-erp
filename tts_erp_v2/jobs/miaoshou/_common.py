"""Shared helpers for the miaoshou sync jobs.

* :func:`ensure_procurement_account` — idempotent upsert into
  ``procurement.procurement_accounts`` so a licenseId always has a
  row before any product / order / move-collect row references it.
* :func:`resolve_credential` — load a ``CredentialsView`` for the
  miaoshou license; returns the decrypted plaintext so the job can
  spin up a ``MiaoshouErpClient`` (test mode: callers may inject a
  fake client instead and skip this).
* :func:`miaoshou_client_factory` — build a real ``MiaoshouErpClient``
  from decrypted credentials; tests inject a fake at this seam.

Design notes
------------
* We do NOT couple to a global ``Credentials`` cache. Each job opens
  its own session and looks up the row once. This keeps the job
  reentrant (parallel cron runs do not contend on a row).
* The ``ProcurementAccount`` row is keyed by ``(provider,
  external_account_id)``. The miaoshou-side external_account_id is
  the ``licenseId`` — stable across shop additions.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.db.models.procurement import ProcurementAccount
from tts_erp_v2.proxy.token_service import CredentialsView, load_credentials

log = logging.getLogger("tts_erp_v2.jobs.miaoshou")

MIAOSHOU_PROVIDER = "miaoshou"


# ---- typed credential + account handles ----------------------------


@dataclass
class MiaoshouContext:
    """Bundles the decrypted credential + the matching procurement account.

    A job's main loop dereferences ``ctx.credentials.access_token``
    (== licenseId) and ``ctx.account_id`` for upserts.
    """

    credentials: CredentialsView
    account_id: int


# ---- credential + account lookup -----------------------------------


def resolve_miaoshou_context(
    session: Session,
    *,
    license_id: str | None = None,
) -> MiaoshouContext | None:
    """Find the miaoshou Credentials row + the matching ProcurementAccount.

    Args:
        session: SQLAlchemy session.
        license_id: optional explicit license id. When omitted we fall
            back to ``MIAOSHOU_LICENSE_ID`` env var. Returns ``None``
            if no row exists or the Fernet key is unset.

    Returns:
        :class:`MiaoshouContext` with the decrypted view + account id,
        or ``None`` if not found.
    """
    ext_id = (license_id or os.environ.get("MIAOSHOU_LICENSE_ID") or "").strip()
    if not ext_id:
        return None
    view = load_credentials(session, MIAOSHOU_PROVIDER, ext_id)
    if view is None:
        return None
    acct = ensure_procurement_account(
        session,
        provider=MIAOSHOU_PROVIDER,
        external_account_id=ext_id,
        account_name=ext_id,
    )
    return MiaoshouContext(credentials=view, account_id=acct.id)


def ensure_procurement_account(
    session: Session,
    *,
    provider: str,
    external_account_id: str,
    account_name: str | None = None,
    status: str | None = None,
    credential_id: int | None = None,
) -> ProcurementAccount:
    """Idempotent upsert of a procurement account row.

    Returns the row (caller commits). Safe to call from inside an
    outer transaction; uses ``ON CONFLICT DO UPDATE`` so concurrent
    jobs do not create duplicates.
    """
    values: dict[str, Any] = {
        "provider": provider,
        "external_account_id": external_account_id,
        "account_name": account_name,
        "status": status,
        "credential_id": credential_id,
        "source_updated_at": datetime.utcnow(),
        "synced_at": datetime.utcnow(),
    }
    insert_stmt = pg_insert(ProcurementAccount).values(**values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["provider", "external_account_id"],
        set_={
            "account_name": values["account_name"],
            "status": values["status"],
            "credential_id": values["credential_id"],
            "source_updated_at": values["source_updated_at"],
            "synced_at": values["synced_at"],
        },
    )
    session.execute(upsert_stmt)
    row = session.execute(
        select(ProcurementAccount)
        .where(ProcurementAccount.provider == provider)
        .where(ProcurementAccount.external_account_id == external_account_id)
    ).scalar_one()
    return row


# ---- client factory ------------------------------------------------


def miaoshou_client_factory(ctx: MiaoshouContext, *, timeout: float = 30.0):
    """Build a real ``MiaoshouErpClient`` from a context.

    Imported lazily so tests that pass a fake client never need the
    proxy SDK module loaded.
    """
    from tts_erp_v2.proxy.miaoshou.client import MiaoshouErpClient

    return MiaoshouErpClient(
        app_id=ctx.credentials.access_token,
        app_secret=ctx.credentials.refresh_token or "",
        timeout=timeout,
    )


def maybe_load_credential_row(
    session: Session, *, provider: str, external_account_id: str
) -> Credentials | None:
    """Load the raw ``Credentials`` row (no decryption).

    Used by jobs that only need to attach a ``credential_id`` FK on a
    ``raw_records`` row, not the plaintext token.
    """
    return session.execute(
        select(Credentials)
        .where(Credentials.provider == provider)
        .where(Credentials.external_account_id == external_account_id)
    ).scalar_one_or_none()


__all__ = [
    "MIAOSHOU_PROVIDER",
    "MiaoshouContext",
    "ensure_procurement_account",
    "maybe_load_credential_row",
    "miaoshou_client_factory",
    "resolve_miaoshou_context",
]
