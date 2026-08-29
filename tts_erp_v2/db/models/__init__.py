"""Per-schema ORM models.

Importing this package registers every tts_erp_v2 model on Base.metadata
via side-effect imports of the per-schema submodules.
"""
from __future__ import annotations

from sqlalchemy import MetaData

from tts_erp_v2.db.base import Base

# Side-effect: import every per-schema module so its tables register on
# Base.metadata. We also re-export the classes for convenience so tests
# can `from tts_erp_v2.db.models import ApiKey` etc.
from tts_erp_v2.db.models.integration import (  # noqa: F401
    Credentials, RawRecord, SyncJob, SyncCursor, SyncIssue,
)
from tts_erp_v2.db.models.commerce import (  # noqa: F401
    ChannelAccount, ChannelProduct, ChannelProductVariant,
    SalesOrder, SalesOrderLine,
)
from tts_erp_v2.db.models.procurement import (  # noqa: F401
    ProcurementAccount, ProcurementProduct, ProcurementProductVariant,
    PurchaseOrder, PurchaseOrderLine, ManualProductCost,
)
from tts_erp_v2.db.models.fulfillment import (  # noqa: F401
    Shipment, ShipmentLine, TrackingEvent,
)
from tts_erp_v2.db.models.after_sales import (  # noqa: F401
    Case, CaseLine,
)
from tts_erp_v2.db.models.finance import (  # noqa: F401
    Payout, SettlementStatement, SettlementTransaction, SettlementComponent,
)
from tts_erp_v2.db.models.linkage import (  # noqa: F401
    AccountLink, ProductLink, VariantLink, LinkEvidence,
    LinkOverride, LinkIssue,
)
from tts_erp_v2.db.models.reporting import (  # noqa: F401
    ProductCostSnapshot, ProductProfitDaily, ShipmentTrackingSummary,
)
from tts_erp_v2.db.models.security import ApiKey  # noqa: F401


__all__ = [
    "Base",
    "Credentials", "RawRecord", "SyncJob", "SyncCursor", "SyncIssue",
    "ChannelAccount", "ChannelProduct", "ChannelProductVariant",
    "SalesOrder", "SalesOrderLine",
    "ProcurementAccount", "ProcurementProduct", "ProcurementProductVariant",
    "PurchaseOrder", "PurchaseOrderLine", "ManualProductCost",
    "Shipment", "ShipmentLine", "TrackingEvent",
    "Case", "CaseLine",
    "Payout", "SettlementStatement", "SettlementTransaction", "SettlementComponent",
    "AccountLink", "ProductLink", "VariantLink", "LinkEvidence", "LinkOverride", "LinkIssue",
    "ProductCostSnapshot", "ProductProfitDaily", "ShipmentTrackingSummary",
    "ApiKey",
]


def load_all_metadata() -> MetaData:
    """Idempotently return Base.metadata (imports have already run above)."""
    return Base.metadata
