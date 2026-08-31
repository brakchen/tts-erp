"""Shared domain constants used across lanes.

Centralised here so that any code path filtering on a free-text status
column (``channel_products.status`` is the canonical example — TikTok
sync writes 'ACTIVATE', older docs / tests assumed 'active') can
reference the same source of truth.

Why a module and not class-level attributes on each model:
    Multiple unrelated domains (reporting, linkage, sync jobs) all need
    the same string. Importing this module keeps the constant visible
    in one place without creating cross-schema ORM import cycles.

Status spelling
---------------
TikTok returns ``status == "ACTIVATE"`` for an active listing and
``"DEACTIVATE"`` (or sometimes "DELETED") for delisted / removed
items. ``ChannelProduct.status`` is a free-text ``Text`` column — we
filter on the exact string the upstream writes.
"""

from __future__ import annotations

from typing import Final

# ─── Channel product status ──────────────────────────────────────────
# Verified live against the TikTok Shop Open API
# /product/202309/products/search on 2026-08-30: the only active
# status string we observe in production is 'ACTIVATE'. Downstream
# filtering MUST use this exact value.
ACTIVE_PRODUCT_STATUS: Final[str] = "ACTIVATE"

# Convenience set for callers that want ``Column.in_(...)`` semantics.
ACTIVE_PRODUCT_STATUSES: Final[frozenset[str]] = frozenset({ACTIVE_PRODUCT_STATUS})

# Statuses that mean the SPU is no longer buyable. Treated as
# "delisted" by every consumer (cost rebuild skips them, coverage
# queries exclude them, missing-cost inventory excludes them).
DELISTED_PRODUCT_STATUSES: Final[frozenset[str]] = frozenset(
    {"DEACTIVATE", "DELETED", "SUSPENDED", "ARCHIVED"}
)


# ─── SalesOrder status (paid-eligible whitelist) ────────────────────
# SalesOrder.status is also free-text. TikTok does NOT expose a
# literal 'PAID' status — paid orders flow into fulfilment and land
# in one of the lifecycle states below. Verified by SELECT status
# against commerce.sales_orders on 2026-08-30:
#
#     status               | count
#     ---------------------+------
#     COMPLETED            | 208
#     DELIVERED            | 196
#     CANCELLED            | 184
#     IN_TRANSIT           | 120
#     AWAITING_COLLECTION  |  38
#     AWAITING_SHIPMENT    |   3
#     UNPAID               |   1
#
# Revenue / cogs aggregation MUST include any order that is past the
# payment gate, regardless of fulfilment progress, because the
# merchant has earned the revenue on ``paid_at``. Explicitly
# excluding UNPAID / ON_HOLD / CANCELLED prevents counting refunded
# or voided orders.
PAID_SALES_ORDER_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "AWAITING_SHIPMENT",
        "PARTIAL_SHIPPING",
        "AWAITING_COLLECTION",
        "IN_TRANSIT",
        "DELIVERED",
        "COMPLETED",
    }
)

# Inverse — statuses that mean the order has NOT been paid and must be
# excluded from revenue aggregations.
UNPAID_SALES_ORDER_STATUSES: Final[frozenset[str]] = frozenset(
    {"UNPAID", "ON_HOLD", "CANCELLED"}
)


__all__ = [
    "ACTIVE_PRODUCT_STATUS",
    "ACTIVE_PRODUCT_STATUSES",
    "DELISTED_PRODUCT_STATUSES",
    "PAID_SALES_ORDER_STATUSES",
    "UNPAID_SALES_ORDER_STATUSES",
]
