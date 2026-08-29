"""/v2/commerce/* — read-only queries for sales-domain tables.

Auth classification lives in ``tts_erp_v2.middleware.auth.required_role``:
all routes here resolve to ``readonly``.

Convention: in every handler, **path params come before the Depends'd
session** so Python's required-arg-after-default rule is satisfied.

SQL safety: all queries are module-level string constants passed to
``text()`` with a bind-params dict. There is no string interpolation;
values from request inputs flow only through the params dict.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.api.schemas import (
    ChannelAccountOut,
    ChannelProductOut,
    ChannelProductVariantOut,
    SalesOrderLineOut,
    SalesOrderOut,
)

router = APIRouter(prefix="/v2/commerce", tags=["commerce"])


# --- SQL constants (no interpolation) ------------------------------------
SQL_LIST_CHANNEL_ACCOUNTS = (
    "SELECT id, platform, external_account_id, account_name, region, "
    "seller_type, status, synced_at "
    "FROM commerce.channel_accounts "
    "WHERE (CAST(:platform AS text) IS NULL OR platform = CAST(:platform AS text)) "
    "ORDER BY id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_GET_CHANNEL_ACCOUNT = (
    "SELECT id, platform, external_account_id, account_name, region, "
    "seller_type, status, synced_at FROM commerce.channel_accounts "
    "WHERE id = :id"
)
SQL_LIST_CHANNEL_PRODUCTS = (
    "SELECT id, channel_account_id, external_product_id, title, status, "
    "source_created_at, source_updated_at FROM commerce.channel_products "
    "WHERE (CAST(:acct_id AS bigint) IS NULL OR channel_account_id = CAST(:acct_id AS bigint)) "
    "AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text)) "
    "ORDER BY id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_GET_CHANNEL_PRODUCT = (
    "SELECT id, channel_account_id, external_product_id, title, status, "
    "source_created_at, source_updated_at FROM commerce.channel_products "
    "WHERE id = :id"
)
SQL_LIST_CHANNEL_VARIANTS = (
    "SELECT id, channel_product_id, external_variant_id, seller_sku, "
    "variant_name FROM commerce.channel_product_variants "
    "WHERE channel_product_id = :id ORDER BY id"
)
SQL_LIST_SALES_ORDERS = (
    "SELECT id, channel_account_id, external_order_id, status, currency, "
    "payment_amount, total_amount, source_created_at, source_updated_at, "
    "paid_at FROM commerce.sales_orders "
    "WHERE (CAST(:acct_id AS bigint) IS NULL OR channel_account_id = CAST(:acct_id AS bigint)) "
    "AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text)) "
    "ORDER BY source_updated_at DESC NULLS LAST "
    "LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_GET_SALES_ORDER = (
    "SELECT id, channel_account_id, external_order_id, status, currency, "
    "payment_amount, total_amount, source_created_at, source_updated_at, "
    "paid_at FROM commerce.sales_orders WHERE id = :id"
)
SQL_LIST_ORDER_LINES = (
    "SELECT id, sales_order_id, external_line_id, channel_product_id, "
    "channel_product_variant_id, quantity, unit_price "
    "FROM commerce.sales_order_lines WHERE sales_order_id = :id ORDER BY id"
)
SQL_ACCOUNT_ORDER_STATS = (
    "SELECT COUNT(DISTINCT id) AS n, "
    "COALESCE(SUM(payment_amount), 0) AS total "
    "FROM commerce.sales_orders WHERE channel_account_id = :id"
)


def _q(compiled_stmt, params: dict, sess: Session):
    """Bound-parameter execute helper.

    Centralizes the execute pattern so the static analyzer sees a
    single allowlisted sink per query. ``compiled_stmt`` is a
    SQLAlchemy ``TextClause`` (built via ``text(SQL_CONST)``); runtime
    data flows only through the ``params`` dict — never into the SQL
    string itself.
    """
    return sess.execute(compiled_stmt, params)


_STMT_LIST_CHANNEL_ACCOUNTS = text(SQL_LIST_CHANNEL_ACCOUNTS)
_STMT_GET_CHANNEL_ACCOUNT = text(SQL_GET_CHANNEL_ACCOUNT)
_STMT_LIST_CHANNEL_PRODUCTS = text(SQL_LIST_CHANNEL_PRODUCTS)
_STMT_GET_CHANNEL_PRODUCT = text(SQL_GET_CHANNEL_PRODUCT)
_STMT_LIST_CHANNEL_VARIANTS = text(SQL_LIST_CHANNEL_VARIANTS)
_STMT_LIST_SALES_ORDERS = text(SQL_LIST_SALES_ORDERS)
_STMT_GET_SALES_ORDER = text(SQL_GET_SALES_ORDER)
_STMT_LIST_ORDER_LINES = text(SQL_LIST_ORDER_LINES)
_STMT_ACCOUNT_ORDER_STATS = text(SQL_ACCOUNT_ORDER_STATS)


def _row_to_channel_account(row: Any) -> ChannelAccountOut:
    return ChannelAccountOut(
        id=row.id,
        platform=row.platform,
        external_account_id=row.external_account_id,
        account_name=row.account_name,
        region=row.region,
        seller_type=row.seller_type,
        status=row.status,
        synced_at=row.synced_at,
    )


def _row_to_channel_product(row: Any) -> ChannelProductOut:
    return ChannelProductOut(
        id=row.id,
        channel_account_id=row.channel_account_id,
        external_product_id=row.external_product_id,
        title=row.title,
        status=row.status,
        source_created_at=row.source_created_at,
        source_updated_at=row.source_updated_at,
    )


def _row_to_sales_order(row: Any) -> SalesOrderOut:
    return SalesOrderOut(
        id=row.id,
        channel_account_id=row.channel_account_id,
        external_order_id=row.external_order_id,
        status=row.status,
        currency=row.currency,
        payment_amount=row.payment_amount,
        total_amount=row.total_amount,
        source_created_at=row.source_created_at,
        source_updated_at=row.source_updated_at,
        paid_at=row.paid_at,
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
def list_channel_accounts(
    sess: Session = Depends(get_session),
    platform: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ChannelAccountOut]:
    rows = _q(
        _STMT_LIST_CHANNEL_ACCOUNTS,
        {"platform": platform, "limit": limit, "offset": offset},
        sess,
    ).all()
    return [_row_to_channel_account(r) for r in rows]


@router.get("/channel-accounts/{account_id}", response_model=ChannelAccountOut)
def get_channel_account(
    account_id: int,
    sess: Session = Depends(get_session),
) -> ChannelAccountOut:
    row = _q(_STMT_GET_CHANNEL_ACCOUNT, {"id": account_id}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel account not found")
    return _row_to_channel_account(row)


@router.get("/channel-products", response_model=list[ChannelProductOut])
def list_channel_products(
    sess: Session = Depends(get_session),
    channel_account_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ChannelProductOut]:
    rows = _q(
        _STMT_LIST_CHANNEL_PRODUCTS,
        {
            "acct_id": channel_account_id,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        },
        sess,
    ).all()
    return [_row_to_channel_product(r) for r in rows]


@router.get("/channel-products/{product_id}", response_model=ChannelProductOut)
def get_channel_product(
    product_id: int,
    sess: Session = Depends(get_session),
) -> ChannelProductOut:
    row = _q(_STMT_GET_CHANNEL_PRODUCT, {"id": product_id}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel product not found")
    return _row_to_channel_product(row)


@router.get(
    "/channel-products/{product_id}/variants",
    response_model=list[ChannelProductVariantOut],
)
def list_channel_product_variants(
    product_id: int,
    sess: Session = Depends(get_session),
) -> list[ChannelProductVariantOut]:
    rows = _q(_STMT_LIST_CHANNEL_VARIANTS, {"id": product_id}, sess).all()
    return [
        ChannelProductVariantOut(
            id=r.id,
            channel_product_id=r.channel_product_id,
            external_variant_id=r.external_variant_id,
            seller_sku=r.seller_sku,
            variant_name=r.variant_name,
        )
        for r in rows
    ]


@router.get("/sales-orders", response_model=list[SalesOrderOut])
def list_sales_orders(
    sess: Session = Depends(get_session),
    channel_account_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[SalesOrderOut]:
    rows = _q(
        _STMT_LIST_SALES_ORDERS,
        {
            "acct_id": channel_account_id,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        },
        sess,
    ).all()
    return [_row_to_sales_order(r) for r in rows]


@router.get("/sales-orders/{order_id}", response_model=SalesOrderOut)
def get_sales_order(
    order_id: int,
    sess: Session = Depends(get_session),
) -> SalesOrderOut:
    row = _q(_STMT_GET_SALES_ORDER, {"id": order_id}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sales order not found")
    return _row_to_sales_order(row)


@router.get(
    "/sales-orders/{order_id}/lines",
    response_model=list[SalesOrderLineOut],
)
def list_sales_order_lines(
    order_id: int,
    sess: Session = Depends(get_session),
) -> list[SalesOrderLineOut]:
    rows = _q(_STMT_LIST_ORDER_LINES, {"id": order_id}, sess).all()
    return [
        SalesOrderLineOut(
            id=r.id,
            sales_order_id=r.sales_order_id,
            external_line_id=r.external_line_id,
            channel_product_id=r.channel_product_id,
            channel_product_variant_id=r.channel_product_variant_id,
            quantity=r.quantity,
            unit_price=r.unit_price,
        )
        for r in rows
    ]


@router.get(
    "/channel-accounts/{account_id}/order-stats",
    summary="Per-account order aggregate — distinct order count + sum of payment_amount",
)
def channel_account_order_stats(
    account_id: int,
    sess: Session = Depends(get_session),
) -> dict:
    """Lightweight aggregate: distinct order count + sum of payment_amount.

    Returns 0/0 when no orders exist (the COALESCE handles NULL from SUM).
    """
    row = _q(_STMT_ACCOUNT_ORDER_STATS, {"id": account_id}, sess).one()
    return {
        "channel_account_id": account_id,
        "distinct_orders": int(row.n or 0),
        "total_payment_amount": str(row.total),
    }
