"""Resolve published runtime configuration deterministically for service code."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.proxy.token_service import decrypt


def rollout_bucket(*, rollout_salt: str, config_key: str, subject: str) -> int:
    """Return a stable 0..9999 rollout bucket; revisions never influence it."""
    material = f"{rollout_salt}:{config_key}:{subject}".encode()
    return int.from_bytes(sha256(material).digest()[:8], "big") % 10_000


def select_payload(
    payload: dict[str, Any],
    rollout: list[dict[str, Any]],
    *,
    rollout_salt: str,
    config_key: str,
    subject: str | None,
) -> tuple[dict[str, Any], int | None]:
    """Apply ordered, first-match rollout rules to a published payload."""
    if subject is None:
        return deepcopy(payload), None
    bucket = rollout_bucket(
        rollout_salt=rollout_salt,
        config_key=config_key,
        subject=subject,
    )
    for index, rule in enumerate(rollout):
        if bucket < rule["basisPoints"]:
            return deepcopy(rule["payload"]), index
    return deepcopy(payload), None


def resolve_runtime_config(
    session: Session,
    *,
    config_key: str,
    rollout_salt: str,
    subject: str | None = None,
) -> dict[str, Any]:
    """Resolve a published config including ``secret://`` references for server use.

    This function is deliberately not exposed by HTTP routes: callers in the
    process may receive decrypted values, while browser/API snapshots never do.
    """
    item = session.get(RuntimeConfigItem, config_key)
    if item is None or item.published_version is None:
        raise KeyError(f"published runtime config not found: {config_key}")
    revision = session.execute(
        select(RuntimeConfigRevision).where(
            RuntimeConfigRevision.config_key == config_key,
            RuntimeConfigRevision.version == item.published_version,
        )
    ).scalar_one()
    selected, rule_index = select_payload(
        revision.payload,
        revision.rollout,
        rollout_salt=rollout_salt,
        config_key=config_key,
        subject=subject,
    )
    return {
        "config_key": config_key,
        "version": revision.version,
        "payload": _resolve_secrets(session, selected),
        "rollout_rule_index": rule_index,
    }


def _resolve_secrets(session: Session, value: Any) -> Any:
    if isinstance(value, str) and value.startswith("secret://"):
        name = value.removeprefix("secret://")
        secret = session.get(RuntimeConfigSecret, name)
        if secret is None or secret.retired_at is not None:
            raise KeyError(f"active runtime config secret not found: {name}")
        return decrypt(secret.encrypted_value)
    if isinstance(value, dict):
        return {key: _resolve_secrets(session, child) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_secrets(session, child) for child in value]
    return value
