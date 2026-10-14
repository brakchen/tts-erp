"""Phase C orchestration for the monitor management API (docs §3.2/§5/§6.3).

The editable source of truth is still the generic runtime configuration:
``monitor.defaults`` holds the complete global default and ``monitor.shop.<shop_pk>``
holds one shop's overrides (both are ordinary ``config.runtime_config_items``
rows with an immutable revision history and ``secret://`` references). What
this module adds is the *only* publish exit for that namespace:

* composition of global + shop into one complete per-shop payload,
* the §4.3 release boundary — exactly the whitelisted Feishu fields may carry a
  ``secret://`` reference, they are decrypted exactly once per publish into an
  ordered, locked snapshot, and nothing else is ever decrypted,
* the delivery fan-out that freezes a new immutable snapshot per onboarded
  shop inside the same transaction as the source revision (§5.3).

Three invariants are load-bearing and each has a test:

1. **Plaintext is only ever released at publish time.** Drafts, the admin list,
   the generic runtime snapshot and audit rows carry references, never values.
2. **A global publish is all-or-nothing.** One shop that cannot compose fails
   the whole publish; no revision and no delivery pointer is left half-written.
3. **Lock order is global row → per-shop config rows (ascending ``shop_pk``) →
   per-shop state rows (ascending ``shop_pk``)**, for both the global and the
   single-shop publish, so concurrent publishes cannot deadlock or produce an
   impossible global/shop version combination (§5.3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.db.models.monitor_config import MonitorShopState
from tts_erp_v2.monitor_config import contracts
from tts_erp_v2.monitor_config.delivery import publish_delivery
from tts_erp_v2.monitor_config.etag import canonical_json_bytes
from tts_erp_v2.monitor_config.keys import (
    GLOBAL_DEFAULTS_KEY,
    SHOP_KEY_PREFIX,
    ReservedConfigKeyError,
    is_reserved_config_key,
    shop_config_key,
    shop_pk_from_config_key,
)
from tts_erp_v2.proxy import token_service
from tts_erp_v2.runtime_config.repository import (
    locked_item,
    publish,
    published_revision,
)
from tts_erp_v2.runtime_config.validation import (
    ConfigValidationError,
    is_secret_reference,
    iter_secret_references,
)

#: The only fields whose ``secret://`` reference may be resolved into a browser
#: delivery (§4.3). TikTok OAuth tokens, cookies, database and Fernet keys are
#: never reachable through this namespace at all.
RELEASABLE_SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "feishuWebhookUrl",
        "feishuSigningSecret",
        "feishuAppId",
        "feishuAppSecret",
    }
)

_ADMIN_HINT = "manage it through /v2/monitor/admin instead"

#: Placeholder shown in place of a plaintext secret found in a stored payload.
#: It is intentionally neither a valid ``secret://`` reference nor the empty
#: string, so a redacted value can never be mistaken for a managed or disabled
#: channel, and re-submitting it is refused by the write-time guard.
QUARANTINE_SENTINEL = "__quarantined_plaintext_secret__"


# ─── errors ──────────────────────────────────────────────────────


class DraftVersionConflict(RuntimeError):
    """``expectedDraftVersion`` does not match the stored draft version."""


class NothingToPublish(RuntimeError):
    """A publish was requested with no draft payload behind it."""


class MissingGlobalDefault(RuntimeError):
    """A shop publish ran before any global default was published."""


class ConfigItemRetired(RuntimeError):
    """The monitor config row was retired through some other path."""


class ForeignSecretReference(RuntimeError):
    """A ``secret://`` reference appeared outside the §4.3 release whitelist."""


class LiteralSecretValue(RuntimeError):
    """A released Feishu field carried a literal instead of a reference.

    See :func:`_reject_literal_released_secrets`. Distinct from
    :class:`ForeignSecretReference` on purpose: that one is a reference in the
    wrong place, this one is a secret typed straight into a draft field.
    """


class MissingSecret(RuntimeError):
    """A referenced secret is retired or absent at publish time."""


class UnknownShop(RuntimeError):
    """A shop row referenced by the monitor namespace no longer exists."""


def ensure_generic_mutation_allowed(config_key: str) -> None:
    """Refuse a generic runtime-config mutation on a reserved monitor key (§5.1).

    The generic routes keep serving every other key unchanged; they simply
    refuse to become a second, unordered publish exit for ``monitor.*``.
    """

    if is_reserved_config_key(config_key):
        raise ReservedConfigKeyError(
            f"config key {config_key!r} is reserved for monitor configuration; "
            f"{_ADMIN_HINT}"
        )


# ─── fixed controlled schema (§5.1)───────────────────────────────
#
# The monitor namespace does not use ``infer_schema``: a first draft must not
# freeze a future field set, and it must not silently widen one either. The
# stored schema is fixed, closes the object (``additionalProperties: false``)
# and names every contract field; per-field type and range rules stay owned by
# :mod:`tts_erp_v2.monitor_config.contracts`, which is stricter than anything
# the supported JSON Schema subset can express (decimals travel as strings,
# two fields accept an explicit ``null``).

_FIELD_SET = contracts._ALLOWED_SETTINGS_FIELDS  # same package: one owner only


def defaults_settings_schema() -> dict[str, Any]:
    """Fixed schema for ``monitor.defaults``: every contract field required."""

    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "monitor global business defaults; validated by "
            "tts_erp_v2.monitor_config.contracts (docs/monitor-config-sync.md §3.1)"
        ),
        "properties": {field: {} for field in sorted(_FIELD_SET)},
        "required": sorted(_FIELD_SET),
    }


def override_settings_schema() -> dict[str, Any]:
    """Fixed schema for ``monitor.shop.<shop_pk>``: a subset of the fields."""

    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "monitor per-shop overrides; absent fields inherit the global default "
            "(docs/monitor-config-sync.md §3.2)"
        ),
        "properties": {field: {} for field in sorted(_FIELD_SET)},
    }


def _schema_for(config_key: str) -> dict[str, Any]:
    return (
        defaults_settings_schema()
        if config_key == GLOBAL_DEFAULTS_KEY
        else override_settings_schema()
    )


# ─── secret reference rules (§4.3)────────────────────────────────


def _reject_foreign_references(payload: Mapping[str, Any]) -> None:
    """Refuse a ``secret://`` reference on any field outside the whitelist.

    Runs before contract validation so the security boundary is the first
    thing a bad payload meets, and it walks nested values: a reference hidden
    inside ``campaignThresholds`` or a recipient list is just as releasable as
    a top-level one.
    """

    for field, value in payload.items():
        if field in RELEASABLE_SECRET_FIELDS:
            continue
        try:
            referenced = next(iter(iter_secret_references(value)), None)
        except ConfigValidationError as exc:
            raise ForeignSecretReference(f"settings.{field}: {exc}") from exc
        if referenced is not None:
            raise ForeignSecretReference(
                f"settings.{field} must not carry a secret:// reference; only "
                f"{', '.join(sorted(RELEASABLE_SECRET_FIELDS))} may be released"
            )


def _holds_literal_secret(value: Any) -> bool:
    """Whether one released-field value is a plaintext secret in disguise.

    Non-string values are *not* counted here: they are a type error the
    contract layer reports precisely, not stored plaintext.
    """

    return isinstance(value, str) and value != "" and not is_secret_reference(value)


def _reject_literal_released_secrets(payload: Mapping[str, Any]) -> None:
    """Refuse a literal value on any field that can be released to a browser.

    The four whitelisted Feishu fields accept exactly two forms:

    * ``secret://<name>`` — the stored value stays a reference; the plaintext
      lives only in ``config.runtime_config_secrets`` and is decrypted once, at
      publish time, into the frozen delivery.
    * ``""`` — §3.2's fixed meaning for "this channel is not configured".
      It is not a truthy-fallback slot, so an empty value stays empty.

    Anything else would put a webhook token, signing secret or app secret in
    clear text into ``config.runtime_config_items`` — where ``GET`` draft hands
    it straight back and where the generic ``/v2/config/runtime/snapshot``
    republishes it to any ``readwrite`` caller. §4.3's release exception is a
    *download* boundary for a value the server already holds encrypted; it is
    not permission to type a secret into a draft field.

    Non-string values fall through to the contract layer, which reports the
    precise type error instead of this one.
    """

    for field in _literal_released_fields(payload):
        raise LiteralSecretValue(
            f"settings.{field} must be a secret:// reference or an empty string "
            "(empty means the channel is not configured); plaintext secrets are "
            "never stored in a monitor draft"
        )


def _literal_released_fields(payload: Mapping[str, Any]) -> list[str]:
    """Released fields currently holding a literal, in stable field order.

    Single source of truth for the rule: the write-time rejection and the
    read-time quarantine below must never drift apart.
    """

    return [
        field
        for field in sorted(RELEASABLE_SECRET_FIELDS)
        if _holds_literal_secret(payload.get(field))
    ]


def quarantined_fields(payload: Mapping[str, Any] | None) -> list[str]:
    """Which released fields of an existing payload hold plaintext.

    Used by the read paths to decide that a stored record predates (or bypassed)
    the no-plaintext rule and must therefore be redacted rather than served.
    """

    if not payload:
        return []
    return _literal_released_fields(payload)


def redact_quarantined(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with every plaintext released field replaced by the sentinel.

    Operator-facing reads redact rather than fail: an operator still needs to
    see and repair the rest of a legacy record. The sentinel is deliberately
    not a valid ``secret://`` reference and not the empty string, so a
    redacted value can never be mistaken for a managed or disabled channel —
    and re-saving it is rejected by the write-time guard.
    """

    redacted = dict(payload)
    for field in quarantined_fields(payload):
        redacted[field] = QUARANTINE_SENTINEL
    return redacted


@dataclass(frozen=True, slots=True)
class SecretSnapshot:
    """One ordered, row-locked, decrypted read of every secret a publish needs.

    §5.3 requires a single batched read so that generating five shops cannot
    interleave with a concurrent secret update and produce a global publish
    that mixes two different generations of one webhook URL.
    """

    values: Mapping[str, str]

    def resolve(self, name: str) -> str:
        try:
            return self.values[name]
        except KeyError as exc:
            raise MissingSecret(f"secret {name!r} is missing or retired") from exc


def load_secret_snapshot(session: Session, names: Iterable[str]) -> SecretSnapshot:
    wanted = sorted({name for name in names if name})
    if not wanted:
        return SecretSnapshot({})
    rows = session.execute(
        select(RuntimeConfigSecret)
        .where(
            RuntimeConfigSecret.name.in_(wanted),
            RuntimeConfigSecret.retired_at.is_(None),
        )
        .order_by(RuntimeConfigSecret.name)
        .with_for_update()
    ).scalars()
    values = {row.name: token_service.decrypt(row.encrypted_value) for row in rows}
    missing = sorted(set(wanted) - set(values))
    if missing:
        raise MissingSecret(
            f"referenced secret(s) are missing or retired: {', '.join(missing)}"
        )
    return SecretSnapshot(values)


def _secret_reference_name(value: Any) -> str | None:
    """The referenced secret name, or ``None`` when the value is not a reference."""

    if not isinstance(value, str) or not is_secret_reference(value):
        return None
    return value.removeprefix("secret://")


def _referenced_secret_names(payload: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for field in RELEASABLE_SECRET_FIELDS:
        name = _secret_reference_name(payload.get(field))
        if name is not None:
            names.add(name)
    return names


def release_secret_references(
    payload: Mapping[str, Any], snapshot: SecretSnapshot
) -> dict[str, Any]:
    """Substitute whitelisted references with their decrypted values.

    A value that is not a reference (an explicitly empty or literal field)
    passes through untouched — §3.2 fixes the meaning of empty and ``null`` and
    forbids a truthy fallback, so this is a mechanical substitution and nothing
    more.
    """

    _reject_foreign_references(payload)
    released = dict(payload)
    for field in RELEASABLE_SECRET_FIELDS:
        name = _secret_reference_name(released.get(field))
        if name is not None:
            released[field] = snapshot.resolve(name)
    return released


def finalize_composed_payload(
    payload: Mapping[str, Any], snapshot: SecretSnapshot
) -> contracts.MonitorBusinessSettings:
    """Release whitelisted references, then re-validate the complete payload.

    The re-validation is not redundant: it is what stops a decrypted value from
    slipping past the type, finiteness and range checks that the operator's
    draft passed while it was still a reference.
    """

    return contracts.validate_complete_business_settings(
        release_secret_references(payload, snapshot)
    )


def validate_draft_payload(config_key: str, payload: Mapping[str, Any]) -> None:
    """Validate one monitor draft against the contract and the release rules.

    ``monitor.defaults`` must be a complete payload; a shop override may be a
    subset (an empty object is the canonical "inherit everything"), and an
    explicit ``null`` is only accepted on the two documented threshold-disable
    fields.
    """

    _reject_foreign_references(payload)
    _reject_literal_released_secrets(payload)
    if config_key == GLOBAL_DEFAULTS_KEY:
        contracts.validate_complete_business_settings(payload)
    else:
        contracts.validate_settings_override(payload)


# ─── drafts ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DraftView:
    """One monitor config key's draft/published state for the admin UI."""

    config_key: str
    draft_payload: dict[str, Any] | None
    draft_version: int
    published_version: int | None
    published_payload: dict[str, Any] | None
    published_at: datetime | None
    updated_at: datetime | None


def _draft_view(
    session: Session, config_key: str, item: RuntimeConfigItem | None
) -> DraftView:
    if item is None:
        return DraftView(config_key, None, 0, None, None, None, None)
    revision = published_revision(session, item)
    return DraftView(
        config_key=config_key,
        draft_payload=item.draft_payload,
        draft_version=item.draft_version,
        published_version=item.published_version,
        published_payload=revision.payload if revision is not None else None,
        published_at=revision.created_at if revision is not None else None,
        updated_at=item.updated_at,
    )


def read_draft(session: Session, config_key: str) -> DraftView:
    """Read one key's draft and published state.

    A key that does not exist yet reads as the zero state rather than a 404:
    ``draftVersion: 0`` is exactly the version a first ``PUT`` must send, so
    creating the key needs no separate endpoint and no special case.
    """

    return _draft_view(session, config_key, session.get(RuntimeConfigItem, config_key))


def save_draft(
    session: Session,
    *,
    config_key: str,
    display_name: str,
    payload: Mapping[str, Any],
    expected_draft_version: int,
) -> DraftView:
    """Save a draft under an optimistic ``expectedDraftVersion`` check."""

    validate_draft_payload(config_key, payload)
    item = locked_item(session, config_key)
    if item is None:
        if expected_draft_version != 0:
            raise DraftVersionConflict(
                "expectedDraftVersion does not match a key that does not exist yet"
            )
        # ``SELECT ... FOR UPDATE`` does not lock a row that does not exist yet,
        # so two concurrent first saves both miss and both INSERT — and one dies
        # on ``runtime_config_items_pkey``. A bare IntegrityError would surface
        # as a 500 for what is really a stale-UI retry. Creating the row with
        # ``ON CONFLICT DO NOTHING`` makes the create itself atomic: the loser
        # waits on the winner's uncommitted row, then skips it. A skip means the
        # key now exists at a version the caller did not expect, which is
        # exactly DraftVersionConflict.
        # ``RETURNING`` reports the outcome without touching the driver-specific
        # ``rowcount``: a skipped insert returns no row at all.
        created = session.execute(
            pg_insert(RuntimeConfigItem)
            .values(
                config_key=config_key,
                display_name=display_name,
                json_schema=_schema_for(config_key),
                draft_payload=dict(payload),
                draft_version=1,
            )
            .on_conflict_do_nothing(index_elements=["config_key"])
            .returning(RuntimeConfigItem.config_key)
        )
        if created.scalar_one_or_none() is None:
            raise DraftVersionConflict(
                "draft version conflict; another request created this key first, "
                "reload before saving"
            )
        session.flush()
        return _draft_view(
            session, config_key, session.get(RuntimeConfigItem, config_key)
        )
    if item.retired_at is not None:
        raise ConfigItemRetired("this monitor config key is retired")
    if item.draft_version != expected_draft_version:
        raise DraftVersionConflict("draft version conflict; reload before saving")
    item.draft_payload = dict(payload)
    item.draft_version += 1
    session.flush()
    return _draft_view(session, config_key, item)


# ─── publishing (§5.3 / §5.4)──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """One shop's new immutable delivery after a publish."""

    shop_pk: int
    seller_id: str
    delivery_version: int
    is_force: bool


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """What one publish produced, for the admin response."""

    config_key: str
    published_version: int | None
    deliveries: list[DeliveryOutcome]


def _published_payload(
    session: Session, config_key: str
) -> tuple[int | None, dict[str, Any] | None]:
    item = session.get(RuntimeConfigItem, config_key)
    if item is None:
        return None, None
    revision = published_revision(session, item)
    if revision is None:
        return None, None
    return revision.version, revision.payload


def quarantined_source_keys(
    session: Session,
    shop_pk: int,
    source_versions: Mapping[str, str | None],
) -> list[str]:
    """Which of a delivery's source revisions hold plaintext released fields.

    A frozen delivery legitimately carries plaintext (§4.3). What it must not
    do is serve material that never came through ``config.runtime_config_secrets``.
    The delivery row itself cannot tell the two apart — the plaintext looks the
    same either way — so provenance is re-established from the source revisions
    the delivery was frozen from.

    Only non-sensitive config keys are returned; they are safe to log and to
    show an operator, and they name exactly which record needs remediation.
    """

    dirty: list[str] = []
    for layer, config_key in (
        ("global", GLOBAL_DEFAULTS_KEY),
        ("shop", shop_config_key(shop_pk)),
    ):
        version = source_versions.get(layer)
        if version is None:
            continue
        # Source versions cross the delivery wire as canonical decimal strings
        # (§5.2), while runtime_config_revisions.version is an integer column.
        # Coerce explicitly: comparing the two directly raises
        # "operator does not exist: integer = character varying".
        revision = session.execute(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == config_key,
                RuntimeConfigRevision.version == int(version),
            )
        ).scalar_one_or_none()
        if revision is not None and quarantined_fields(revision.payload):
            dirty.append(config_key)
    return dirty


def _compose(
    *, seller_id: str, global_default: Mapping[str, Any], shop_override: Any
) -> dict[str, Any]:
    """Merge global + override into one complete payload that still holds refs."""

    composed = contracts.merge_business_settings(
        seller_id=seller_id,
        global_default=global_default,
        shop_override=shop_override,
    )
    payload = contracts.config_to_payload(composed)
    _reject_foreign_references(payload)
    # Re-checked at the publish entrance, not only at draft-save: a literal
    # could reach here through a row written by an older build or direct DB
    # access, and freezing it would put plaintext into the delivery.
    _reject_literal_released_secrets(payload)
    return payload


def _onboarded_shop_pks(session: Session) -> list[int]:
    """Shops that already have a delivery pointer, in the documented lock order."""

    return list(
        session.execute(
            select(MonitorShopState.shop_pk).order_by(MonitorShopState.shop_pk)
        ).scalars()
    )


def _freeze_delivery(
    session: Session,
    *,
    shop_pk: int,
    seller_id: str,
    composed: Mapping[str, Any],
    snapshot: SecretSnapshot,
    source_global_version: int | None,
    source_shop_version: int | None,
    actor: str,
) -> DeliveryOutcome:
    settings = finalize_composed_payload(composed, snapshot)
    version = publish_delivery(
        session,
        shop_pk=shop_pk,
        payload=contracts.config_to_payload(settings),
        source_global_version=source_global_version,
        source_shop_version=source_shop_version,
        created_by=actor,
    )
    return DeliveryOutcome(
        shop_pk=shop_pk, seller_id=seller_id, delivery_version=version, is_force=False
    )


def publish_shop_deliveries(session: Session, *, actor: str) -> list[DeliveryOutcome]:
    """Freeze a fresh delivery for every onboarded shop, all-or-nothing.

    The caller must already hold the ``monitor.defaults`` row lock (both
    publish paths take it first). Composition happens for all shops *before*
    any delivery is written, so the single secret snapshot below provably
    covers every payload this publish is about to create.
    """

    global_version, global_payload = _published_payload(session, GLOBAL_DEFAULTS_KEY)
    if global_payload is None:
        raise MissingGlobalDefault("the global monitor defaults are not published")
    shop_pks = _onboarded_shop_pks(session)
    if not shop_pks:
        return []

    # §5.3: per-shop config rows in ascending shop_pk, before any state row
    # lock, matching the single-shop publish order.
    for shop_pk in shop_pks:
        locked_item(session, shop_config_key(shop_pk))

    composed_by_pk: dict[int, dict[str, Any]] = {}
    sellers: dict[int, str] = {}
    names: set[str] = set()
    for shop_pk in shop_pks:
        shop = session.get(ChannelAccount, shop_pk)
        if shop is None:
            raise UnknownShop(f"shop_pk {shop_pk} is no longer registered")
        _, shop_payload = _published_payload(session, shop_config_key(shop_pk))
        composed = _compose(
            seller_id=shop.shop_id,
            global_default=global_payload,
            shop_override=shop_payload,
        )
        composed_by_pk[shop_pk] = composed
        sellers[shop_pk] = shop.shop_id
        names |= _referenced_secret_names(composed)

    snapshot = load_secret_snapshot(session, names)
    return [
        _freeze_delivery(
            session,
            shop_pk=shop_pk,
            seller_id=sellers[shop_pk],
            composed=composed_by_pk[shop_pk],
            snapshot=snapshot,
            source_global_version=global_version,
            source_shop_version=_published_payload(session, shop_config_key(shop_pk))[
                0
            ],
            actor=actor,
        )
        for shop_pk in shop_pks
    ]


def _locked_draft(
    session: Session, config_key: str, expected: int
) -> RuntimeConfigItem:
    item = locked_item(session, config_key)
    if item is not None and item.retired_at is not None:
        raise ConfigItemRetired("this monitor config key is retired")
    if item is not None and item.draft_version != expected:
        raise DraftVersionConflict("draft version conflict; reload before publishing")
    if item is None or item.draft_payload is None:
        raise NothingToPublish("a draft payload is required before publishing")
    return item


def publish_defaults(
    session: Session, *, expected_draft_version: int, comment: str | None, actor: str
) -> PublishOutcome:
    """Publish the global defaults and re-freeze every onboarded shop (§5.3)."""

    item = _locked_draft(session, GLOBAL_DEFAULTS_KEY, expected_draft_version)
    # Re-validated at the publish entrance: the draft may predate the
    # no-plaintext rule or may have been written straight to the DB, and a
    # literal must never become an immutable revision. This also covers the
    # no-shop-yet case, where per-shop composition never runs.
    validate_draft_payload(GLOBAL_DEFAULTS_KEY, item.draft_payload or {})
    revision = publish(
        session,
        item=item,
        payload=item.draft_payload or {},
        comment=comment,
        actor=actor,
    )
    deliveries = publish_shop_deliveries(session, actor=actor)
    return PublishOutcome(GLOBAL_DEFAULTS_KEY, revision.version, deliveries)


def publish_shop(
    session: Session,
    *,
    shop_pk: int,
    seller_id: str,
    expected_draft_version: int,
    comment: str | None,
    actor: str,
) -> PublishOutcome:
    """Publish one shop's override (if any) and freeze its delivery.

    A shop with no override draft is still onboarded here: it receives the
    global default as its initial delivery with ``sourceVersions.shop = null``
    rather than a fabricated empty revision (§5.2).
    """

    config_key = shop_config_key(shop_pk)
    # §5.3 lock order: global row, then this shop's config row, then its state
    # (taken inside publish_delivery).
    locked_item(session, GLOBAL_DEFAULTS_KEY)
    item = locked_item(session, config_key)
    if item is not None and item.retired_at is not None:
        raise ConfigItemRetired("this monitor config key is retired")
    if item is not None and item.draft_version != expected_draft_version:
        raise DraftVersionConflict("draft version conflict; reload before publishing")

    global_version, global_payload = _published_payload(session, GLOBAL_DEFAULTS_KEY)
    if global_payload is None:
        raise MissingGlobalDefault(
            "publish the global monitor defaults before publishing a shop"
        )

    shop_source: int | None = None
    shop_payload: dict[str, Any] | None = None
    if item is not None and item.draft_payload is not None:
        # Same re-validation rationale as publish_defaults: the revision is
        # permanent storage, so the no-plaintext rule is enforced here too.
        validate_draft_payload(config_key, item.draft_payload)
        revision = publish(
            session,
            item=item,
            payload=item.draft_payload,
            comment=comment,
            actor=actor,
        )
        shop_source = revision.version
        shop_payload = revision.payload

    composed = _compose(
        seller_id=seller_id, global_default=global_payload, shop_override=shop_payload
    )
    snapshot = load_secret_snapshot(session, _referenced_secret_names(composed))
    outcome = _freeze_delivery(
        session,
        shop_pk=shop_pk,
        seller_id=seller_id,
        composed=composed,
        snapshot=snapshot,
        source_global_version=global_version,
        source_shop_version=shop_source,
        actor=actor,
    )
    return PublishOutcome(config_key, shop_source, [outcome])


# ─── admin list (§6.3)─────────────────────────────────────────────


def list_managed_shops(session: Session) -> list[dict[str, Any]]:
    """Every shop the monitor namespace knows about, with its delivery pointers.

    "Managed" means either already onboarded (a delivery pointer exists) or
    holding a shop draft/revision. Neither carries any secret: the version and
    hash facts come from ``config.monitor_config_deliveries`` and never from
    the decrypted payload.
    """

    states = {
        row.shop_pk: row
        for row in session.execute(
            select(MonitorShopState).order_by(MonitorShopState.shop_pk)
        ).scalars()
    }
    items = {
        row.config_key: row
        for row in session.execute(
            select(RuntimeConfigItem)
            .where(
                RuntimeConfigItem.config_key.startswith(
                    SHOP_KEY_PREFIX, autoescape=True
                )
            )
            .order_by(RuntimeConfigItem.config_key)
        ).scalars()
    }
    draft_pks = {
        pk for key in items if (pk := shop_pk_from_config_key(key)) is not None
    }
    rows: list[dict[str, Any]] = []
    for shop_pk in sorted(set(states) | draft_pks):
        shop = session.get(ChannelAccount, shop_pk)
        if shop is None:
            continue
        state = states.get(shop_pk)
        item = items.get(shop_config_key(shop_pk))
        current = None
        if state is not None and state.current_delivery_version is not None:
            current = (
                session.execute(
                    select(_delivery_table()).where(
                        _delivery_table().c.shop_pk == shop_pk,
                        _delivery_table().c.version == state.current_delivery_version,
                    )
                )
                .mappings()
                .one_or_none()
            )
        rows.append(
            {
                "shopPk": str(shop_pk),
                "sellerId": shop.shop_id,
                "shopName": shop.account_name,
                "onboarded": state is not None,
                "publishedVersion": (
                    str(state.current_delivery_version)
                    if state is not None and state.current_delivery_version is not None
                    else None
                ),
                "lastForceVersion": (
                    str(state.last_force_version)
                    if state is not None and state.last_force_version is not None
                    else None
                ),
                "contentHash": current["content_hash"] if current else None,
                "sourceVersions": (
                    {
                        "global": (
                            str(current["source_global_version"])
                            if current["source_global_version"] is not None
                            else None
                        ),
                        "shop": (
                            str(current["source_shop_version"])
                            if current["source_shop_version"] is not None
                            else None
                        ),
                    }
                    if current
                    else None
                ),
                "isForce": bool(current["is_force"]) if current else False,
                "shopDraftVersion": item.draft_version if item is not None else 0,
                "shopPublishedVersion": (
                    item.published_version if item is not None else None
                ),
                "updatedAt": state.updated_at.isoformat()
                if state is not None
                else None,
            }
        )
    return rows


def _delivery_table() -> Any:
    from tts_erp_v2.db.models.monitor_config import MonitorConfigDelivery

    return MonitorConfigDelivery.__table__


def force_request_digest(shop_pk: int, expected_delivery_version: int) -> str:
    """Digest over a force request's non-sensitive metadata only (§5.4).

    Never covers a token, a secret or the payload: two identical clicks must
    produce one digest so the replay is recognised, and any genuinely different
    force must produce a different one so it is rejected.
    """

    material = {
        "operation": "monitor.force",
        "shopPk": str(shop_pk),
        "expectedDeliveryVersion": str(expected_delivery_version),
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(material)).hexdigest()


__all__ = [
    "QUARANTINE_SENTINEL",
    "RELEASABLE_SECRET_FIELDS",
    "ConfigItemRetired",
    "DeliveryOutcome",
    "DraftVersionConflict",
    "DraftView",
    "ForeignSecretReference",
    "LiteralSecretValue",
    "MissingGlobalDefault",
    "MissingSecret",
    "NothingToPublish",
    "PublishOutcome",
    "ReservedConfigKeyError",
    "SecretSnapshot",
    "UnknownShop",
    "defaults_settings_schema",
    "ensure_generic_mutation_allowed",
    "finalize_composed_payload",
    "force_request_digest",
    "list_managed_shops",
    "load_secret_snapshot",
    "override_settings_schema",
    "publish_defaults",
    "publish_shop",
    "publish_shop_deliveries",
    "quarantined_fields",
    "quarantined_source_keys",
    "read_draft",
    "redact_quarantined",
    "release_secret_references",
    "save_draft",
    "validate_draft_payload",
]
