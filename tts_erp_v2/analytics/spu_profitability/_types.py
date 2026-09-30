"""Domain types for the SPU profitability module.

These types deliberately contain no HTTP concerns: monetary values and ratios stay
as :class:`~decimal.Decimal`, dates stay as dates, and status is represented by
enums.  The legacy ``/v2/analytics/spu-roi`` adapter owns wire formatting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import cast


class ProfitabilityError(RuntimeError):
    """Base class for failures that make a profitability result unavailable."""


class FxRateUnavailable(ProfitabilityError):
    """Raised when the database has no complete USD/CNY/VND FX snapshot."""


class SpuNotFound(ProfitabilityError):
    """Raised when an explanation is requested for an unknown SPU."""


class SnapshotIsolationUnavailable(ProfitabilityError):
    """Raised when a caller-owned transaction cannot satisfy snapshot invariants."""


class SortField(StrEnum):
    ROI_REAL = "roi_real"
    SPEND = "spend"
    AD_SYSTEM_ACTUAL_ROI = "ad_system_actual_roi"
    AD_SYSTEM_BREAKEVEN_ROI = "ad_system_breakeven_roi"
    REFUND_RATE = "refund_rate"
    REFUND_RATE_QTY = "refund_rate_qty"
    CANCEL_RATE = "cancel_rate"
    NET_PROFIT = "net_profit"
    SALES = "sales"
    EFFECTIVE_SALES = "effective_sales"
    GMV_SALES = "gmv_sales"
    AD_COUNT = "ad_count"
    GMV_AD = "gmv_ad"
    ORDER_COUNT = "order_count"
    TOTAL_ORDERS = "total_orders"
    EFFECTIVE_ORDER_COUNT = "effective_order_count"
    CANCELLED_ORDER_COUNT = "cancelled_order_count"
    UNITS_SOLD = "units_sold"
    REFUND_NET_AMOUNT = "refund_net_amount"
    RETURN_LOSS = "return_loss"
    ROI_BREAKEVEN = "roi_breakeven"
    FULL_LOSS_RATE = "full_loss_rate"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class EvidenceKind(StrEnum):
    ORDERS = "orders"
    SETTLEMENTS = "settlements"
    CASES = "cases"
    ADS = "ads"


class FormulaStatus(StrEnum):
    CALCULATED = "calculated"
    ESTIMATED_KNOWN_COSTS = "estimated_known_costs"


class ProjectionStatus(StrEnum):
    AVAILABLE = "available"
    NO_UNSETTLED_ORDERS = "no_unsettled_orders"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


REFUND_RATE_ALERT_THRESHOLD = Decimal("0.30")


def _profit_status(net_profit: Decimal) -> str:
    if net_profit < 0:
        return "loss"
    if net_profit > 0:
        return "profit"
    return "break_even"


def _roi_status(roi_real: Decimal | None) -> str:
    if roi_real is None:
        return "unavailable"
    return "negative" if roi_real < 0 else "non_negative"


@dataclass(frozen=True, slots=True)
class ActivitySelection:
    """The ordinary catalog/activity scope."""


@dataclass(frozen=True, slots=True)
class ExactIdsSelection:
    """A bounded, temporary exact-ID scope."""

    spu_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.spu_ids:
            raise ValueError("spu_ids must not be empty")
        if len(self.spu_ids) > 100:
            raise ValueError("spu_ids must contain at most 100 unique ids")
        if len(set(self.spu_ids)) != len(self.spu_ids):
            raise ValueError("spu_ids must contain unique ids")
        if any(not value or len(value) > 128 for value in self.spu_ids):
            raise ValueError("each spu_id must contain between 1 and 128 characters")


@dataclass(frozen=True, slots=True)
class FocusedSelection:
    """The unbounded active membership stored for one shop."""


SpuSelection = ActivitySelection | ExactIdsSelection | FocusedSelection


@dataclass(frozen=True, slots=True)
class ProfitScope:
    """Facts that define both rows and the global overview.

    ``spu_ids`` remains as a compatibility constructor field for existing typed
    callers. New callers should pass one explicit ``selection``.
    """

    shop_pk: int | None
    start_date: date | None = None
    end_date: date | None = None
    include_inactive: bool = False
    spu_ids: tuple[str, ...] | None = None
    selection: SpuSelection | None = None

    def __post_init__(self) -> None:
        if self.shop_pk is not None and self.shop_pk < 1:
            raise ValueError("shop_pk must be >= 1")
        if self.spu_ids is not None and self.selection is not None:
            raise ValueError("spu_ids and selection cannot be combined")
        if self.spu_ids is not None:
            ExactIdsSelection(self.spu_ids)
        if isinstance(self.selection, FocusedSelection) and self.shop_pk is None:
            raise ValueError("shop_pk is required with focused selection")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date must be <= end_date")

    @property
    def effective_selection(self) -> SpuSelection:
        if self.selection is not None:
            return self.selection
        if self.spu_ids is not None:
            return ExactIdsSelection(self.spu_ids)
        return ActivitySelection()


@dataclass(frozen=True, slots=True)
class RowView:
    """Presentation-only choices for the SPU row collection."""

    search: str | None = None
    sort: SortField = SortField.ROI_REAL
    direction: SortDirection = SortDirection.ASC
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")
        if self.search is not None and len(self.search) > 200:
            raise ValueError("search must contain at most 200 characters")


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    kinds: frozenset[EvidenceKind] = frozenset(EvidenceKind)


@dataclass(frozen=True, slots=True)
class FxBasis:
    snapshot_id: int
    usd_cny: Decimal
    cny_usd: Decimal
    usd_vnd: Decimal
    as_of: datetime


@dataclass(frozen=True, slots=True)
class ShopFeeRateEstimate:
    """一次店铺费率实测快照（``reporting.shop_fee_rate_estimates`` 的一行）。

    ``fee_rate = total_fee / kept_line_gmv``（GMV 加权，口径见
    ``jobs/finance_fee_rate.py``）—— **只统计未退款(kept)订单**，因为
    页面公式的 ``(1−退款率)`` 已单独扣过退款，再混入退款订单会把退款
    效应算两遍。``line_gmv`` = 订单行 ``quantity × unit_price`` = 客户实付。

    ``kept_share`` = ``kept_line_gmv / window_line_gmv``，余量 = 退款订单 +
    缺 ``FEE`` 分项的订单；仅供观测，不作门槛。
    """

    calculated_on: date
    calculated_at: datetime
    lookback_days: int
    fee_rate: Decimal
    kept_order_count: int
    kept_line_gmv: Decimal
    window_line_gmv: Decimal
    kept_share: Decimal
    total_fee: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class ShopFeeRateEntry:
    """一个店铺在本次计算中实际使用的平台抽成费率。

    ``source`` ∈ {``user_override``（页面覆写）, ``shop_estimate``（店铺
    实测快照）, ``baseline``（无可用或已过期的实测样本）}。

    ``estimate`` 仅 ``shop_estimate`` 有值；``fallback_reason`` 仅
    ``baseline`` 有值（``no_estimate`` / ``stale_estimate``）。
    """

    shop_pk: int
    shop_name: str | None
    fee_rate: Decimal
    source: str
    estimate: ShopFeeRateEstimate | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProfitabilityBasis:
    calculated_at: datetime
    fx: FxBasis
    fee_rate: Decimal
    fee_source: str
    rubric_version: str
    coverage_first_day: date | None
    coverage_last_day: date | None
    ad_first_day: date | None
    ad_last_day: date | None
    unattributed_refund_lines: int
    display_currency: str = "CNY"
    warnings: tuple[str, ...] = ()
    fee_per_shop: tuple[ShopFeeRateEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class SpuProfitability:
    spu_pk: int
    spu_id: str | None
    title: str | None
    status: str | None
    main_image_url: str | None
    shop_id: str | None
    shop_name: str | None
    ad_count: int
    ad_orders: int
    spend: Decimal
    gmv_ad: Decimal
    roi_l0: Decimal | None
    ad_first_day: date | None
    ad_last_day: date | None
    order_count: int
    cancelled_order_count: int
    total_orders: int
    effective_order_count: int
    refund_order_count: int
    full_loss_order_count: int
    domestic_cancelled_order_count: int
    overseas_cancelled_order_count: int
    units_sold: int
    sales: Decimal
    effective_sales: Decimal
    gmv_sales: Decimal
    cancel_rate: Decimal | None
    refund_rate_qty: Decimal | None
    refund_only_qty: int
    refund_only_amount: Decimal
    refund_return_qty: int
    refund_return_amount: Decimal
    refund_net_qty: int
    refund_net_amount: Decimal
    refund_rate: Decimal | None
    refund_amount_rate: Decimal | None
    refund_cancelled_qty: int
    refund_cancelled_amount: Decimal
    refund_cancelled_missing_lines: int
    return_loss: Decimal
    net_profit: Decimal
    platform_fee: Decimal
    roi_real: Decimal | None
    roi_breakeven: Decimal | None
    cpa: Decimal | None
    unit_cost_used: Decimal
    cost_source: str
    net_revenue: Decimal
    settled_net: Decimal
    unsettled_net: Decimal
    settled_sales: Decimal
    unsettled_sales: Decimal
    cogs_sold: Decimal
    cogs_full_loss_cancelled: Decimal
    cogs_total: Decimal
    settled_order_count: int
    full_loss_qty: int
    full_loss_cancelled_qty: int
    full_loss_rate: Decimal | None
    full_loss_qty_rate: Decimal | None
    ad_system_actual_roi: Decimal | None
    ad_system_breakeven_roi: Decimal | None
    ad_system_max_ad_spend: Decimal
    ad_system_remaining_ad_spend_capacity: Decimal
    ad_system_breakeven_roi_status: FormulaStatus
    fee_rate_used: Decimal
    fee_source: str
    projection_status: ProjectionStatus
    projection_basis_order_count: int
    projection_basis_qty: int
    projection_basis_sales: Decimal
    projection_basis_refund_amount: Decimal
    projection_basis_full_loss_order_count: int
    projection_basis_full_loss_qty: int
    projection_refund_amount_rate: Decimal | None
    settled_full_loss_rate: Decimal | None
    projection_full_loss_qty_rate: Decimal | None
    unsettled_order_count: int
    unresolved_unsettled_order_count: int
    unresolved_unsettled_qty: int
    unresolved_unsettled_sales: Decimal
    confirmed_unsettled_refund_amount: Decimal
    confirmed_unsettled_full_loss_order_count: int
    confirmed_unsettled_full_loss_qty: int
    projected_future_refund_amount: Decimal | None
    projected_terminal_refund_amount: Decimal | None
    projected_future_full_loss_order_count: Decimal | None
    projected_future_full_loss_qty: Decimal | None
    projected_terminal_full_loss_qty: Decimal | None
    projected_full_loss_cost: Decimal | None
    projected_unsettled_net: Decimal | None
    projected_net_revenue: Decimal | None
    projected_net_profit: Decimal | None
    projected_roi_real: Decimal | None
    projected_roi_breakeven: Decimal | None
    projected_nc_prime: Decimal | None
    projected_cogs_kept: Decimal | None
    projected_ad_gmv: Decimal | None
    projected_ad_system_actual_roi: Decimal | None
    projected_ad_system_max_ad_spend: Decimal | None
    projected_ad_system_breakeven_roi: Decimal | None

    @property
    def profit_status(self) -> str:
        return _profit_status(self.net_profit)

    @property
    def roi_status(self) -> str:
        return _roi_status(self.roi_real)

    @property
    def has_unsettled_orders(self) -> bool:
        return self.settled_order_count < self.order_count

    @property
    def uses_default_unit_cost(self) -> bool:
        return self.cost_source == "DEFAULT_K1"

    @property
    def refund_rate_alert(self) -> bool:
        return (
            self.refund_rate is not None
            and self.refund_rate > REFUND_RATE_ALERT_THRESHOLD
        )


@dataclass(frozen=True, slots=True)
class ProfitabilityTotals:
    row_count: int
    order_count: int
    cancelled_order_count: int
    total_orders: int
    spend: Decimal
    sales: Decimal
    gmv: Decimal
    refund_net_amount: Decimal
    return_loss: Decimal
    net_profit: Decimal
    roi_real: Decimal | None
    refund_order_count: int
    full_loss_qty: int
    full_loss_cancelled_qty: int
    domestic_cancelled_order_count: int
    overseas_cancelled_order_count: int
    roi_breakeven: Decimal | None
    effective_sales: Decimal
    effective_order_count: int
    full_loss_order_count: int
    refund_rate: Decimal | None
    full_loss_rate: Decimal | None
    cancel_rate: Decimal | None
    ad_system_actual_roi: Decimal | None
    ad_system_breakeven_roi: Decimal | None
    ad_system_max_ad_spend: Decimal
    ad_system_remaining_ad_spend_capacity: Decimal
    ad_system_breakeven_roi_status: FormulaStatus
    projection_status: ProjectionStatus
    projection_basis_order_count: int
    projection_basis_qty: int
    projection_basis_sales: Decimal
    projection_basis_refund_amount: Decimal
    projection_basis_full_loss_order_count: int
    projection_basis_full_loss_qty: int
    projection_refund_amount_rate: Decimal | None
    settled_full_loss_rate: Decimal | None
    projection_full_loss_qty_rate: Decimal | None
    unsettled_order_count: int
    unresolved_unsettled_order_count: int
    unresolved_unsettled_qty: int
    unresolved_unsettled_sales: Decimal
    confirmed_unsettled_refund_amount: Decimal
    confirmed_unsettled_full_loss_order_count: int
    confirmed_unsettled_full_loss_qty: int
    projected_future_refund_amount: Decimal | None
    projected_terminal_refund_amount: Decimal | None
    projected_future_full_loss_order_count: Decimal | None
    projected_future_full_loss_qty: Decimal | None
    projected_terminal_full_loss_qty: Decimal | None
    projected_full_loss_cost: Decimal | None
    projected_unsettled_net: Decimal | None
    projected_net_revenue: Decimal | None
    projected_net_profit: Decimal | None
    projected_roi_real: Decimal | None
    projected_roi_breakeven: Decimal | None
    projected_nc_prime: Decimal | None
    projected_cogs_kept: Decimal | None
    projected_ad_gmv: Decimal | None
    projected_ad_system_actual_roi: Decimal | None
    projected_ad_system_max_ad_spend: Decimal | None
    projected_ad_system_breakeven_roi: Decimal | None

    @property
    def profit_status(self) -> str:
        return _profit_status(self.net_profit)

    @property
    def roi_status(self) -> str:
        return _roi_status(self.roi_real)


@dataclass(frozen=True, slots=True)
class ProfitabilityOverview:
    items: tuple[SpuProfitability, ...]
    total: int
    totals: ProfitabilityTotals
    basis: ProfitabilityBasis


EvidenceValue = object
EvidenceRow = Mapping[str, EvidenceValue]


def _freeze_evidence_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_evidence_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_evidence_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ProfitabilityEvidence:
    """Normalized evidence grouped by source kind.

    Rows are immutable mappings because the four evidence sources have distinct,
    source-specific fields.  Their values nevertheless remain typed domain values;
    the HTTP adapter performs all string conversion.
    """

    rows: Mapping[EvidenceKind, tuple[EvidenceRow, ...]]

    @classmethod
    def from_rows(
        cls, rows: Mapping[EvidenceKind, tuple[EvidenceRow, ...]]
    ) -> ProfitabilityEvidence:
        frozen = {
            kind: tuple(
                cast(EvidenceRow, _freeze_evidence_value(dict(row)))
                for row in source_rows
            )
            for kind, source_rows in rows.items()
        }
        return cls(rows=MappingProxyType(frozen))


@dataclass(frozen=True, slots=True)
class SpuProfitExplanation:
    result: SpuProfitability
    evidence: ProfitabilityEvidence
    basis: ProfitabilityBasis
