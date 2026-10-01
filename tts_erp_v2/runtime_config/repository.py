"""Persistence helpers for versioned runtime configuration."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.proxy.token_service import encrypt
from tts_erp_v2.runtime_config.validation import (
    ConfigValidationError,
    iter_secret_references,
)


class RetiredSecretError(ValueError):
    """A write attempted to use a secret that was deliberately retired."""


def locked_item(session: Session, config_key: str) -> RuntimeConfigItem | None:
    """Load one config row under a transaction-scoped row lock."""
    return session.execute(
        select(RuntimeConfigItem)
        .where(RuntimeConfigItem.config_key == config_key)
        .with_for_update()
    ).scalar_one_or_none()


def published_revision(session: Session, item: RuntimeConfigItem) -> RuntimeConfigRevision | None:
    if item.published_version is None:
        return None
    return session.execute(
        select(RuntimeConfigRevision).where(
            RuntimeConfigRevision.config_key == item.config_key,
            RuntimeConfigRevision.version == item.published_version,
        )
    ).scalar_one()


def publish(
    session: Session,
    *,
    item: RuntimeConfigItem,
    payload: dict[str, Any],
    rollout: list[dict[str, Any]],
    comment: str | None,
    actor: str,
) -> RuntimeConfigRevision:
    """Publish while the caller holds ``locked_item``'s row lock."""
    ensure_secret_references_exist(session, payload)
    for rule in rollout:
        ensure_secret_references_exist(session, rule["payload"])
    next_version = (item.published_version or 0) + 1
    revision = RuntimeConfigRevision(
        config_key=item.config_key,
        version=next_version,
        payload=payload,
        rollout=rollout,
        comment=comment,
        created_by=actor,
    )
    session.add(revision)
    item.published_version = next_version
    item.draft_payload = None
    item.draft_rollout = []
    item.draft_version += 1
    session.flush()
    return revision


def ensure_secret_references_exist(session: Session, value: Any) -> None:
    names = set(iter_secret_references(value))
    if not names:
        return
    rows = session.execute(
        select(RuntimeConfigSecret.name).where(
            RuntimeConfigSecret.name.in_(names),
            RuntimeConfigSecret.retired_at.is_(None),
        )
    ).scalars()
    missing = names - set(rows)
    if missing:
        raise KeyError(f"referenced active secrets do not exist: {', '.join(sorted(missing))}")


def secret_is_referenced_by_active_config(session: Session, name: str) -> bool:
    """Whether retiring ``name`` would break a current active config or draft."""
    items = session.execute(
        select(RuntimeConfigItem).where(RuntimeConfigItem.retired_at.is_(None))
    ).scalars()
    for item in items:
        values: list[Any] = [item.draft_payload]
        revision = published_revision(session, item)
        if revision is not None:
            values.append(revision.payload)
            values.extend(rule["payload"] for rule in revision.rollout)
        values.extend(rule["payload"] for rule in item.draft_rollout)
        for value in values:
            if value is None:
                continue
            try:
                if name in set(iter_secret_references(value)):
                    return True
            except ConfigValidationError:
                # Invalid historical data must never make secret retirement safer.
                return True
    return False


def upsert_secret(session: Session, *, name: str, value: str) -> RuntimeConfigSecret:
    """Atomically create/update an active secret without reviving retired names."""
    fingerprint = sha256(value.encode()).hexdigest()[:16]
    encrypted_value = encrypt(value)
    stmt = (
        insert(RuntimeConfigSecret)
        .values(
            name=name,
            encrypted_value=encrypted_value,
            fingerprint=fingerprint,
        )
        .on_conflict_do_update(
            index_elements=[RuntimeConfigSecret.name],
            set_={
                "encrypted_value": encrypted_value,
                "fingerprint": fingerprint,
            },
            where=RuntimeConfigSecret.retired_at.is_(None),
        )
        .returning(RuntimeConfigSecret)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise RetiredSecretError("secret is retired; restore it before updating")
    return row
