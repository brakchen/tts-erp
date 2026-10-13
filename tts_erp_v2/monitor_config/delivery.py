"""Publish and read the frozen per-shop monitor delivery snapshot.

Owns the write path (docs/monitor-config-sync.md §5.3/§5.4) and the read path
used by the delivery GET (§6.1). Two invariants are load-bearing here:

* Plaintext is encrypted and decrypted **only** through
  :mod:`tts_erp_v2.proxy.token_service`; it never lands in a source revision,
  a log line, or an audit record.
* A delivery row is immutable. Publishing appends a new row and advances the
  pointer in ``config.monitor_shop_state`` in the same transaction, so a partial
  failure can never leave a pointer aimed at a missing snapshot.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.monitor_config import (
    MonitorConfigDelivery,
    MonitorShopState,
)
from tts_erp_v2.monitor_config import contracts
from tts_erp_v2.monitor_config.etag import canonical_json_bytes, weak_etag
from tts_erp_v2.proxy import token_service

PLATFORM = "tiktok"


class PublishConflict(RuntimeError):
    """A force directive was replayed with different request metadata (§5.4)."""


class ShopUnconfigured(RuntimeError):
    """The shop is onboarded but has no published delivery yet."""


def resolve_shop(session: Session, seller_id: str) -> ChannelAccount | None:
    """Resolve the single identity field ``sellerId`` to a registered shop.

    Platform is fixed to TikTok; an arbitrary ``sellerId`` never resolves to a
    global default (§4.1).
    """

    return session.execute(
        select(ChannelAccount).where(
            ChannelAccount.platform == PLATFORM,
            ChannelAccount.shop_id == seller_id,
        )
    ).scalar_one_or_none()


def content_hash_of(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def _lock_state(session: Session, shop_pk: int) -> MonitorShopState:
    """Row-lock the per-shop state, creating it on first onboarding (§5.2)."""

    state = session.execute(
        select(MonitorShopState)
        .where(MonitorShopState.shop_pk == shop_pk)
        .with_for_update()
    ).scalar_one_or_none()
    if state is None:
        state = MonitorShopState(shop_pk=shop_pk)
        session.add(state)
        session.flush()
    return state


def publish_delivery(
    session: Session,
    *,
    shop_pk: int,
    payload: Mapping[str, Any],
    source_global_version: int | None,
    source_shop_version: int | None,
    created_by: str,
    is_force: bool = False,
    request_id: uuid.UUID | None = None,
    request_digest: str | None = None,
) -> int:
    """Freeze one composed payload as a new immutable delivery.

    Returns the new delivery version, which doubles as the delivery identifier.
    Idempotency is checked before any version conflict so a client that lost a
    response can safely retry the same ``request_id`` (§5.4).
    """

    if request_id is not None:
        existing = session.execute(
            select(MonitorConfigDelivery).where(
                MonitorConfigDelivery.shop_pk == shop_pk,
                MonitorConfigDelivery.request_id == request_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if request_digest is not None and existing.request_digest != request_digest:
                raise PublishConflict(
                    "request_id replayed with different request metadata"
                )
            return existing.version

    state = _lock_state(session, shop_pk)
    version = (state.current_delivery_version or 0) + 1
    frozen = dict(payload)
    session.add(
        MonitorConfigDelivery(
            shop_pk=shop_pk,
            version=version,
            source_global_version=source_global_version,
            source_shop_version=source_shop_version,
            schema_version=contracts.WIRE_SCHEMA_VERSION,
            content_hash=content_hash_of(frozen),
            # The only exit for plaintext on the way to disk.
            encrypted_payload=token_service.encrypt(
                canonical_json_bytes(frozen).decode("utf-8")
            ),
            is_force=is_force,
            created_by=created_by,
            request_id=request_id,
            request_digest=request_digest,
        )
    )
    state.current_delivery_version = version
    if is_force:
        state.last_force_version = version
    session.flush()
    return version


class DeliveryView(NamedTuple):
    envelope: contracts.DeliveryEnvelope
    etag: str
    body: dict[str, Any]


def _block_wire(block: contracts.Current) -> dict[str, Any]:
    """One delivery block in the §6.1 wire shape."""

    return {
        "version": block.version,
        "sourceVersions": dict(block.source_versions),
        "contentHash": block.content_hash,
        "payload": {
            "sellerId": block.payload.seller_id or "",
            "schemaVersion": contracts.WIRE_SCHEMA_VERSION,
            "settings": contracts.config_to_payload(block.payload),
        },
    }


def wire_body(envelope: contracts.DeliveryEnvelope) -> dict[str, Any]:
    """The complete §6.1 envelope body served to the plugin.

    Composed from the contract layer's validated accessors so the wire shape has
    exactly one construction site; ``force.target`` mirrors ``current``.
    """

    force = envelope.force
    return {
        "schemaVersion": envelope.schema_version,
        "sellerId": envelope.seller_id,
        "shopPk": envelope.shop_pk,
        "current": _block_wire(envelope.current),
        "force": (
            None
            if force is None
            else {"version": force.version, "target": _block_wire(force.target)}
        ),
    }


def _settings_of(
    row: MonitorConfigDelivery, seller_id: str
) -> contracts.MonitorBusinessSettings:
    plaintext = token_service.decrypt(row.encrypted_payload)
    settings = contracts.validate_complete_business_settings(json.loads(plaintext))
    if not isinstance(settings, contracts.MonitorBusinessSettings):
        # Not an assert: this guards a decrypted-payload contract that must
        # hold under -O too, and the value came from our own encrypted column.
        raise contracts.ConfigContractError(
            f"delivery {row.shop_pk}/{row.version} is not a complete settings payload"
        )
    # The frozen payload carries settings only; ``sellerId`` is identity, not
    # configuration, so it is re-attached from the resolved shop rather than
    # trusted from the stored bytes.
    return replace(settings, seller_id=seller_id)


def _block(
    row: MonitorConfigDelivery, seller_id: str
) -> contracts.Current:
    return contracts.Current(
        version=str(row.version),
        source_versions={
            "global": (
                str(row.source_global_version)
                if row.source_global_version is not None
                else None
            ),
            "shop": (
                str(row.source_shop_version)
                if row.source_shop_version is not None
                else None
            ),
        },
        content_hash=row.content_hash,
        payload=_settings_of(row, seller_id),
    )


def load_delivery(
    session: Session, shop_pk: int, seller_id: str
) -> DeliveryView | None:
    """Read the current (and latest force) delivery for one shop.

    Returns ``None`` when the shop has no published configuration; the route
    turns that into 404 rather than inventing a snapshot (§5.2).
    """

    state = session.get(MonitorShopState, shop_pk)
    if state is None or state.current_delivery_version is None:
        return None
    current = session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == shop_pk,
            MonitorConfigDelivery.version == state.current_delivery_version,
        )
    ).scalar_one()
    force_block: contracts.DeliveryEnvelope.Force | None = None
    if state.last_force_version is not None:
        force_row = session.execute(
            select(MonitorConfigDelivery).where(
                MonitorConfigDelivery.shop_pk == shop_pk,
                MonitorConfigDelivery.version == state.last_force_version,
            )
        ).scalar_one()
        force_target = _block(force_row, seller_id)
        force_block = contracts.DeliveryEnvelope.Force(
            version=str(force_row.version), target=force_target
        )

    envelope = contracts.parse_envelope(
        _settings_of(current, seller_id),
        current_version=str(current.version),
        source_versions={
            "global": (
                str(current.source_global_version)
                if current.source_global_version is not None
                else None
            ),
            "shop": (
                str(current.source_shop_version)
                if current.source_shop_version is not None
                else None
            ),
        },
        content_hash=current.content_hash,
        force=force_block,
        shop_pk=str(shop_pk),
    )
    etag = weak_etag(
        {
            "sellerId": envelope.seller_id,
            "shopPk": str(shop_pk),
            "schemaVersion": envelope.schema_version,
            "current": {
                "version": str(current.version),
                "contentHash": current.content_hash,
            },
            "force": (
                None
                if force_block is None
                else {
                    "version": force_block.version,
                    "contentHash": force_block.target.content_hash,
                }
            ),
        }
    )
    return DeliveryView(
        envelope=envelope, etag=etag, body=wire_body(envelope)
    )
