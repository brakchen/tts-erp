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
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


class ForceRequiresPublishedDelivery(RuntimeError):
    """Force was requested for a shop with nothing published yet (§5.4).

    An unpublished draft must never be implicitly published by a force click.
    """


class ForceRequiresRequestId(RuntimeError):
    """Force was requested without a full idempotency key (§5.4).

    Without both ``request_id`` and ``request_digest`` a double-click or a
    retried request could not be replayed safely.
    """


class ForceVersionConflict(RuntimeError):
    """The caller's ``expectedDeliveryVersion`` no longer matches the pointer.

    The management UI must not be able to force-copy a snapshot the operator
    was not looking at (§5.4).
    """


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


def _frozen_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate then normalize one composed business payload.

    Raises :class:`~tts_erp_v2.monitor_config.contracts.ConfigContractError`
    for an incomplete, unknown, or forbidden field, before the caller encrypts
    anything or touches a pointer.
    """

    settings = contracts.validate_complete_business_settings(payload)
    return contracts.config_to_payload(settings)


def content_hash_of(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def _lock_state(session: Session, shop_pk: int) -> MonitorShopState:
    """Row-lock the per-shop state, creating it atomically on first onboarding (§5.2).

    ``SELECT ... FOR UPDATE`` cannot lock a row that does not exist yet, so a
    plain select-then-insert lets two concurrent first publishes both miss and
    both insert, and one dies on ``monitor_shop_state_pkey``. Inserting with
    ``ON CONFLICT DO NOTHING`` makes the create itself atomic: the loser of the
    race waits on the winner's uncommitted row and then proceeds as a no-op.
    Only after that does the ``FOR UPDATE`` take the per-shop publishing lock,
    so a concurrent retry cannot slip its idempotency recheck past a live
    publish.
    """

    session.execute(
        pg_insert(MonitorShopState)
        .values(shop_pk=shop_pk)
        .on_conflict_do_nothing(index_elements=["shop_pk"])
    )
    session.flush()
    return session.execute(
        select(MonitorShopState)
        .where(MonitorShopState.shop_pk == shop_pk)
        .with_for_update()
    ).scalar_one()


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

    Ordering is deliberate, and every step precedes any write:

    1. Validate and normalize the payload through the contract layer, so an
       incomplete or forbidden payload never reaches encryption, the delivery
       table, or the state pointers.
    2. Enforce the force preconditions (an already published delivery plus a
       full idempotency key) against a read-only peek, before creating state.
    3. Take the per-shop row lock **before** rechecking for a replay. Checking
       first would let two concurrent retries of the same ``request_id`` both
       miss and both write; under the lock the second one sees the committed
       row and replays that version instead.
    """

    frozen = _frozen_payload(payload)

    if is_force:
        if request_id is None or request_digest is None:
            raise ForceRequiresRequestId(
                "force publish requires both request_id and request_digest"
            )
        peek = session.get(MonitorShopState, shop_pk)
        if peek is None or peek.current_delivery_version is None:
            raise ForceRequiresPublishedDelivery(
                "force publish requires an already published delivery"
            )

    state = _lock_state(session, shop_pk)

    # Idempotency is checked before any version conflict so a client that lost a
    # response can safely retry the same request_id (§5.4).
    if request_id is not None:
        replayed = _replayed_delivery(
            session, shop_pk, request_id, request_digest or ""
        )
        if replayed is not None:
            return replayed.version

    version = (state.current_delivery_version or 0) + 1
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


def _delivery_row(
    session: Session, shop_pk: int, version: int
) -> MonitorConfigDelivery:
    return session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == shop_pk,
            MonitorConfigDelivery.version == version,
        )
    ).scalar_one()


def _replayed_delivery(
    session: Session, shop_pk: int, request_id: uuid.UUID, request_digest: str
) -> MonitorConfigDelivery | None:
    """Return the delivery a previous identical request already produced.

    Callers must hold the shop state row lock, otherwise two concurrent
    retries can both miss and both write.
    """

    existing = session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == shop_pk,
            MonitorConfigDelivery.request_id == request_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_digest != request_digest:
        raise PublishConflict("request_id replayed with different request metadata")
    return existing


def force_publish(
    session: Session,
    *,
    shop_pk: int,
    expected_delivery_version: int,
    request_id: uuid.UUID,
    request_digest: str,
    created_by: str,
) -> MonitorConfigDelivery:
    """Copy the current published snapshot as a new ``is_force`` delivery (§5.4).

    The copy happens even when the payload is byte-identical, so an offline
    instance can still tell "a force happened after my last check" from
    "nothing changed" (§11.1.3).

    Check order is deliberate and matches §5.4:

    1. Preconditions (a published delivery exists, a full idempotency key was
       supplied) are refused up front, before any state is created.
    2. The per-shop row lock is taken **before** the idempotency replay check,
       so a retried request cannot slip a second write past a live one.
    3. Only then is ``expected_delivery_version`` compared. Idempotency wins
       over the version check, which is what makes a lost-response retry
       recoverable instead of a permanent 409.
    """

    if request_id is None or request_digest is None:
        raise ForceRequiresRequestId(
            "force publish requires both request_id and request_digest"
        )
    peek = session.get(MonitorShopState, shop_pk)
    if peek is None or peek.current_delivery_version is None:
        raise ForceRequiresPublishedDelivery(
            "force publish requires an already published delivery"
        )

    state = _lock_state(session, shop_pk)

    replayed = _replayed_delivery(session, shop_pk, request_id, request_digest)
    if replayed is not None:
        return replayed

    current_version = state.current_delivery_version
    if current_version is None:
        # Re-checked under the lock: the pointer cannot be read as NULL here
        # because the peek above, but the lock must not be trusted to carry
        # the earlier non-NULL read across this reload.
        raise ForceRequiresPublishedDelivery(
            "force publish requires an already published delivery"
        )
    if current_version != expected_delivery_version:
        raise ForceVersionConflict(
            "expectedDeliveryVersion does not match the shop's current delivery"
        )

    target = _delivery_row(session, shop_pk, current_version)
    # The stored payload is the frozen settings document; decrypting it here
    # keeps a force copy from depending on the current global defaults.
    frozen = json.loads(token_service.decrypt(target.encrypted_payload))
    version = publish_delivery(
        session,
        shop_pk=shop_pk,
        payload=frozen,
        source_global_version=target.source_global_version,
        source_shop_version=target.source_shop_version,
        created_by=created_by,
        is_force=True,
        request_id=request_id,
        request_digest=request_digest,
    )
    row = _delivery_row(session, shop_pk, version)
    session.refresh(row)
    return row


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


def _block(row: MonitorConfigDelivery, seller_id: str) -> contracts.Current:
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
    return DeliveryView(envelope=envelope, etag=etag, body=wire_body(envelope))
