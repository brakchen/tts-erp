"""Pydantic v2 request/response schemas for /v2 endpoints.

Field naming follows DB columns exactly (snake_case). ``Decimal`` is
serialized as a string by default to avoid float drift on
``Numeric(20,4)`` money fields — the FastAPI default JSON encoder for
``Decimal`` uses ``str`` when configured via the app's encoder.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ChannelAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: str
    shop_id: str
    account_name: str | None = None
    region: str | None = None
    seller_type: str | None = None
    status: str | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChannelProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shop_pk: int
    spu_id: str
    title: str | None = None
    status: str | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None


class ChannelProductVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    spu_pk: int
    sku_id: str
    seller_sku: str | None = None
    variant_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SalesOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    shop_pk: int
    order_id: str
    status: str | None = None
    currency: str | None = None
    payment_amount: Decimal | None = None
    total_amount: Decimal | None = None
    order_time: datetime | None = None
    order_modify_time: datetime | None = None
    paid_at: datetime | None = None


class SalesOrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_pk: int
    external_line_id: str
    spu_pk: int | None = None
    sku_pk: int | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LinkEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_link_id: int | None = None
    variant_link_id: int | None = None
    evidence_type: str
    source_table: str | None = None
    source_external_id: str | None = None
    observed_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProductLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    procurement_product_id: int
    spu_pk: int
    relation_type: str
    status: str | None = None
    is_primary: bool | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LinkOverrideIn(BaseModel):
    """Body for POST /v2/linkage/overrides.

    decision ∈ {ALLOW, DENY, PRIMARY}. ``procurement_product_id`` is
    optional for DENY rows (operator denies even without a candidate).
    """

    spu_pk: int
    procurement_product_id: int | None = None
    decision: str = Field(pattern="^(ALLOW|DENY|PRIMARY)$")
    reason: str | None = Field(default=None, max_length=500)
    valid_from: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LinkOverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    procurement_product_id: int
    spu_pk: int
    decision: str
    reason: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LinkIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    issue_type: str
    procurement_product_id: int | None = None
    spu_pk: int | None = None
    candidate_count: int | None = None
    status: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    updated_at: datetime | None = None


class ManualCostIn(BaseModel):
    """Body for POST /v2/reporting/manual-costs.

    Caller passes the channel product's external id (the
    ``spu_id`` column on ``commerce.products_spu``).
    The handler resolves it to ``spu_pk`` and inserts into
    ``procurement.manual_product_costs``. New submissions auto-close the
    previous effective row for the same SPU by setting its ``valid_to``.
    """

    spu_id: str = Field(min_length=1, max_length=128)
    unit_cost: Decimal = Field(gt=Decimal("0"))
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$")
    valid_from: datetime | None = None
    note: str | None = Field(default=None, max_length=500)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ManualCostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    spu_pk: int
    unit_cost: Decimal
    currency: str
    valid_from: datetime
    valid_to: datetime | None = None
    note: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CostSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    spu_pk: int
    cost_method: str
    unit_cost: Decimal
    currency: str
    calculation_version: int
    calculated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProfitDailyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    spu_pk: int
    on_date: datetime | None = None
    revenue: Decimal | None = None
    cost: Decimal | None = None
    profit: Decimal | None = None
    currency: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CoverageReport(BaseModel):
    """Coverage / health snapshot for the operator dashboard.

    ``missing_cost_spus`` is the number of channel products that have
    NO row in ``procurement.manual_product_costs`` AND NO active row in
    ``linkage.effective_product_links`` — i.e. SPU candidates that the
    operator should fill in. Calculation version is bumped each rebuild
    so callers can detect a fresh run.
    """

    total_spus: int
    active_spus: int
    linked_spus: int
    missing_cost_spus: int
    calculation_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
