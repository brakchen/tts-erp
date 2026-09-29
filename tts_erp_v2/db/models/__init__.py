"""Per-schema ORM models.

Importing this package registers every tts_erp_v2 model on Base.metadata
via side-effect imports of the per-schema submodules.
"""

from __future__ import annotations

from sqlalchemy import MetaData

from tts_erp_v2.db.base import Base
from tts_erp_v2.db.models.after_sales import (
    Case,
    CaseLine,
)
from tts_erp_v2.db.models.commerce import (
    ChannelAccount,
    ChannelProduct,
    ChannelProductVariant,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.db.models.config import EnumMap
from tts_erp_v2.db.models.finance import (
    Payout,
    SettlementComponent,
    SettlementStatement,
    SettlementTransaction,
)
from tts_erp_v2.db.models.fulfillment import (
    Shipment,
    ShipmentLine,
    TrackingEvent,
)
from tts_erp_v2.db.models.fx import (
    ExchangeRate,
    ExchangeRateSnapshot,
)

# Side-effect: import every per-schema module so its tables register on
# Base.metadata. We also re-export the classes for convenience so tests
# can `from tts_erp_v2.db.models import ApiKey` etc.
from tts_erp_v2.db.models.integration import (
    Credentials,
    RawRecord,
    SyncCursor,
    SyncIssue,
    SyncJob,
    TikTokAppCredential,
)
from tts_erp_v2.db.models.intercept import (
    InterceptConfig,
    InterceptedRequest,
    InterceptSession,
    InterceptSyncCursor,
)
from tts_erp_v2.db.models.linkage import (
    AccountLink,
    LinkEvidence,
    LinkIssue,
    LinkOverride,
    ProductLink,
    VariantLink,
)
from tts_erp_v2.db.models.plugin import (
    AdDaily,
    AdMonthly,
    AdRawLog,
    AdToday,
    ChromeAfterSale,
    ChromeAfterSaleItem,
    ChromeOrder,
    ChromeOrderLine,
    ChromeSettlement,
    ChromeSettlementDetail,
    ChromeShipment,
    ChromeTrackingEvent,
    PluginLog,
)
from tts_erp_v2.db.models.procurement import (
    ManualProductCost,
    ProcurementAccount,
    ProcurementProduct,
    ProcurementProductVariant,
    PurchaseOrder,
    PurchaseOrderLine,
)
from tts_erp_v2.db.models.reporting import (
    ProductCostSnapshot,
    ProductProfitDaily,
    ShipmentTrackingSummary,
)
from tts_erp_v2.db.models.security import ApiKey

__all__ = [
    "AccountLink",
    "AdDaily",
    "AdMonthly",
    "AdRawLog",
    "AdToday",
    "ApiKey",
    "Base",
    "Case",
    "CaseLine",
    "ChannelAccount",
    "ChannelProduct",
    "ChannelProductVariant",
    "ChromeAfterSale",
    "ChromeAfterSaleItem",
    "ChromeOrder",
    "ChromeOrderLine",
    "ChromeSettlement",
    "ChromeSettlementDetail",
    "ChromeShipment",
    "ChromeTrackingEvent",
    "Credentials",
    "EnumMap",
    "ExchangeRate",
    "ExchangeRateSnapshot",
    "InterceptConfig",
    "InterceptSession",
    "InterceptSyncCursor",
    "InterceptedRequest",
    "LinkEvidence",
    "LinkIssue",
    "LinkOverride",
    "ManualProductCost",
    "Payout",
    "PluginLog",
    "ProcurementAccount",
    "ProcurementProduct",
    "ProcurementProductVariant",
    "ProductCostSnapshot",
    "ProductLink",
    "ProductProfitDaily",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "RawRecord",
    "SalesOrder",
    "SalesOrderLine",
    "SettlementComponent",
    "SettlementStatement",
    "SettlementTransaction",
    "Shipment",
    "ShipmentLine",
    "ShipmentTrackingSummary",
    "SyncCursor",
    "SyncIssue",
    "SyncJob",
    "TikTokAppCredential",
    "TrackingEvent",
    "VariantLink",
]


def load_all_metadata() -> MetaData:
    """Idempotently return Base.metadata (imports have already run above)."""
    return Base.metadata
