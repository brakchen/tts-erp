"""The reserved ``monitor.*`` runtime-config namespace (docs §3.2 / §5.1).

The monitor namespace is owned by :mod:`tts_erp_v2.monitor_config.admin`; the
generic ``/v2/config/runtime/*`` mutations refuse these keys so the dedicated
publish orchestration stays the only exit (§5.1).

Kept dependency-free on purpose: the generic runtime-config API imports
:func:`is_reserved_config_key` without dragging in the delivery layer.
"""

from __future__ import annotations

#: Every key in this namespace is managed through ``/v2/monitor/admin``.
RESERVED_NAMESPACE = "monitor."

#: Global defaults: one complete business payload (§3.2).
GLOBAL_DEFAULTS_KEY = "monitor.defaults"

#: Per-shop overrides: only the fields this shop overrides (§3.2).
SHOP_KEY_PREFIX = "monitor.shop."


class ReservedConfigKeyError(ValueError):
    """A generic runtime-config mutation tried to touch a reserved key."""


def is_reserved_config_key(config_key: str) -> bool:
    """Whether ``config_key`` belongs to the monitor-only namespace."""

    return config_key.startswith(RESERVED_NAMESPACE)


def ensure_generic_mutation_allowed(config_key: str) -> None:
    """Refuse a generic runtime-config mutation on a reserved monitor key (§5.1).

    The dedicated orchestration in :mod:`tts_erp_v2.monitor_config.admin` is the
    only publish exit for this namespace: it composes global + shop and freezes
    a delivery per shop in one transaction. A generic draft/publish/rollback/
    retire that slipped past would publish a revision no shop ever receives.
    """

    if is_reserved_config_key(config_key):
        raise ReservedConfigKeyError(
            f"config key {config_key!r} is reserved for monitor configuration; "
            "manage it through /v2/monitor/admin instead"
        )


def shop_config_key(shop_pk: int) -> str:
    """The runtime-config key holding one shop's override draft."""

    if shop_pk <= 0:
        raise ValueError(f"shop_pk must be a positive internal primary key: {shop_pk}")
    return f"{SHOP_KEY_PREFIX}{shop_pk}"


def shop_pk_from_config_key(config_key: str) -> int | None:
    """Reverse :func:`shop_config_key`; ``None`` when the key is not a shop override.

    Only canonical ASCII digits parse, so ``monitor.shop.007`` and
    ``monitor.shop. 7`` never silently resolve to a shop.
    """

    if not config_key.startswith(SHOP_KEY_PREFIX):
        return None
    suffix = config_key[len(SHOP_KEY_PREFIX) :]
    if not suffix.isascii() or not suffix.isdigit():
        return None
    return int(suffix)
