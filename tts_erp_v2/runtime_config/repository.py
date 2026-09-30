"""Persistence helpers for versioned runtime configuration."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.proxy.token_service import encrypt
from tts_erp_v2.runtime_config.validation import iter_secret_references


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
        select(RuntimeConfigSecret.name).where(RuntimeConfigSecret.name.in_(names))
    ).scalars()
    missing = names - set(rows)
    if missing:
        raise KeyError(f"referenced secrets do not exist: {', '.join(sorted(missing))}")


def upsert_secret(session: Session, *, name: str, value: str) -> RuntimeConfigSecret:
    fingerprint = sha256(value.encode()).hexdigest()[:16]
    row = session.get(RuntimeConfigSecret, name)
    if row is None:
        row = RuntimeConfigSecret(
            name=name,
            encrypted_value=encrypt(value),
            fingerprint=fingerprint,
        )
        session.add(row)
    else:
        row.encrypted_value = encrypt(value)
        row.fingerprint = fingerprint
    session.flush()
    return row


def max_revision_version(session: Session, config_key: str) -> int:
    return session.execute(
        select(func.coalesce(func.max(RuntimeConfigRevision.version), 0)).where(
            RuntimeConfigRevision.config_key == config_key
        )
    ).scalar_one()
