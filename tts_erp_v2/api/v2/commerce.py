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

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
from tts_erp_v2.storage.minio_client import MinioClient

router = APIRouter(prefix="/v2/commerce", tags=["commerce"])


# --- mirror URL resolution (2026-09-06 all-SPU tab) -----------------------
# Same lazy-singleton pattern as reporting.list_missing_cost_products: the
# mirror job stores only mirror_object_key on products_spu; read endpoints
# resolve it to a presigned / public URL on demand. Rows without a mirror
# yet return image_url=None (frontend shows the fallback icon).
_minio_client_singleton: MinioClient | None = None


def _resolve_mirror_url(mirror_object_key: str | None) -> str | None:
    """Return a renderable URL for a stored mirror object key, else None."""
    if not mirror_object_key:
        return None
    global _minio_client_singleton
    if _minio_client_singleton is None:
        _minio_client_singleton = MinioClient.from_env()
    client = _minio_client_singleton
    public_url = client.public_url_or_none(mirror_object_key)
    if public_url is not None:
        return public_url
    url, _expires_at = client.presign_get(mirror_object_key)
    return url


# --- SQL constants (no interpolation) ------------------------------------
SQL_LIST_CHANNEL_ACCOUNTS = (
    "SELECT id, platform, shop_id, account_name, region, "
    "seller_type, status, synced_at, opened_date "
    "FROM commerce.shops "
    "WHERE (CAST(:platform AS text) IS NULL OR platform = CAST(:platform AS text)) "
    "ORDER BY id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_GET_CHANNEL_ACCOUNT = (
    "SELECT id, platform, shop_id, account_name, region, "
    "seller_type, status, synced_at, opened_date FROM commerce.shops "
    "WHERE id = :id"
)
SQL_GET_CHANNEL_ACCOUNT_BY_EXTERNAL = (
    "SELECT id, platform, shop_id, account_name, region, "
    "seller_type, status, synced_at, opened_date FROM commerce.shops "
    "WHERE platform = :platform AND shop_id = :ext"
)
SQL_LIST_CHANNEL_PRODUCTS = (
    "SELECT cp.id, cp.shop_pk, cp.spu_id, cp.title, cp.status, "
    "       cp.source_created_at, cp.source_updated_at, "
    "       cp.main_image_url, cp.mirror_object_key, "
    "       m.unit_cost, m.currency, "
    "       CASE WHEN m.id IS NULL THEN NULL ELSE 'MANUAL_ENTRY' END AS cost_method "
    "FROM commerce.products_spu cp "
    "LEFT JOIN procurement.manual_product_costs m "
    "  ON m.spu_pk = cp.id AND m.valid_to IS NULL "
    "WHERE (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND (CAST(:status AS text) IS NULL OR cp.status = CAST(:status AS text)) "
    "AND (NOT CAST(:has_orders AS boolean) OR EXISTS ("
    "  SELECT 1 FROM commerce.sales_order_lines sol "
    "  WHERE sol.spu_pk = cp.id AND sol.spu_pk IS NOT NULL"
    "))"
)
# Total row count for the SAME filter (no sort / page suffix) — surfaced as
# the X-Total-Count header so the page can render 共 N 行 / paging without
# switching the JSON body to an envelope (the bare-array shape is a stable
# external contract). The LEFT JOIN cannot inflate the count: at most one
# open manual_product_costs row per SPU (partial unique index); the
# has-orders EXISTS also cannot inflate it.
SQL_COUNT_CHANNEL_PRODUCTS = (
    "SELECT COUNT(*) AS n FROM commerce.products_spu cp "
    "LEFT JOIN procurement.manual_product_costs m "
    "  ON m.spu_pk = cp.id AND m.valid_to IS NULL "
    "WHERE (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND (CAST(:status AS text) IS NULL OR cp.status = CAST(:status AS text)) "
    "AND (NOT CAST(:has_orders AS boolean) OR EXISTS ("
    "  SELECT 1 FROM commerce.sales_order_lines sol "
    "  WHERE sol.spu_pk = cp.id AND sol.spu_pk IS NOT NULL"
    "))"
)
# All-SPU catalogue sorters (2026-09-06): the tail of SQL_LIST_CHANNEL_PRODUCTS.
# Values come from the allowlist below — no request input ever reaches the
# SQL text, so each ORDER BY tail stays a module-level constant.
# NULL manual costs sort to the tail in BOTH directions (cost-less rows are
# not "lowest cost" — they are "no cost yet", so they must not jump to the
# top of an asc sort and masquerade as cheapest).
_SORT_TAILS_CHANNEL_PRODUCTS = {
    "created_at-asc": "ORDER BY cp.source_created_at ASC NULLS LAST, cp.id",
    "created_at-desc": "ORDER BY cp.source_created_at DESC NULLS LAST, cp.id",
    "updated_at-asc": "ORDER BY cp.source_updated_at ASC NULLS LAST, cp.id",
    "updated_at-desc": "ORDER BY cp.source_updated_at DESC NULLS LAST, cp.id",
    "unit_cost-asc": ("ORDER BY m.unit_cost ASC NULLS LAST, cp.id"),
    "unit_cost-desc": ("ORDER BY m.unit_cost DESC NULLS LAST, cp.id"),
    # Status sort (2026-09-06): stable by a fixed weight so the list
    # doesn't depend on upstream enum spelling. Weights mirror the page's
    # Chinese labels: 商家(ACTIVATE)=0 < 下架(DELETED)=1 < 停售
    # (SELLER_DEACTIVATED)=2.
    "status-asc": (
        "ORDER BY CASE cp.status WHEN 'ACTIVATE' THEN 0 "
        "WHEN 'DELETED' THEN 1 WHEN 'SELLER_DEACTIVATED' THEN 2 "
        "ELSE 3 END ASC, cp.id"
    ),
    "status-desc": (
        "ORDER BY CASE cp.status WHEN 'ACTIVATE' THEN 0 "
        "WHEN 'DELETED' THEN 1 WHEN 'SELLER_DEACTIVATED' THEN 2 "
        "ELSE 3 END DESC, cp.id"
    ),
    # Back-compat default: stable insertion order.
    "id-asc": "ORDER BY cp.id",
}
_PAGE_SUFFIX = "LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
_SQL_LIST_CHANNEL_PRODUCTS = {
    # Each precompiled statement = the shared SELECT + an allowlisted
    # ORDER BY tail + the LIMIT/OFFSET page suffix. No request value
    # ever enters the SQL string (sort/order map onto dict keys).
    key: text(SQL_LIST_CHANNEL_PRODUCTS + " " + tail + " " + _PAGE_SUFFIX)
    for key, tail in _SORT_TAILS_CHANNEL_PRODUCTS.items()
}
SQL_GET_CHANNEL_PRODUCT = (
    "SELECT cp.id, cp.shop_pk, cp.spu_id, cp.title, cp.status, "
    "       cp.source_created_at, cp.source_updated_at, "
    "       cp.main_image_url, cp.mirror_object_key, "
    "       m.unit_cost, m.currency, "
    "       CASE WHEN m.id IS NULL THEN NULL ELSE 'MANUAL_ENTRY' END AS cost_method "
    "FROM commerce.products_spu cp "
    "LEFT JOIN procurement.manual_product_costs m "
    "  ON m.spu_pk = cp.id AND m.valid_to IS NULL "
    "WHERE cp.id = :id"
)
SQL_LIST_CHANNEL_VARIANTS = (
    "SELECT id, spu_pk, sku_id, seller_sku, "
    "variant_name FROM commerce.products_sku "
    "WHERE spu_pk = :id ORDER BY id"
)
SQL_LIST_SALES_ORDERS = (
    "SELECT id, shop_pk, order_id, status, currency, "
    "payment_amount, total_amount, order_time, order_modify_time, "
    "paid_at FROM commerce.sales_orders "
    "WHERE (CAST(:acct_id AS bigint) IS NULL OR shop_pk = CAST(:acct_id AS bigint)) "
    "AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text)) "
    "ORDER BY order_modify_time DESC NULLS LAST "
    "LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_GET_SALES_ORDER = (
    "SELECT id, shop_pk, order_id, status, currency, "
    "payment_amount, total_amount, order_time, order_modify_time, "
    "paid_at FROM commerce.sales_orders WHERE id = :id"
)
SQL_LIST_ORDER_LINES = (
    "SELECT id, order_pk, external_line_id, spu_pk, "
    "sku_pk, quantity, unit_price "
    "FROM commerce.sales_order_lines WHERE order_pk = :id ORDER BY id"
)
SQL_ACCOUNT_ORDER_STATS = (
    "SELECT COUNT(DISTINCT id) AS n, "
    "COALESCE(SUM(payment_amount), 0) AS total "
    "FROM commerce.sales_orders WHERE shop_pk = :id"
)


def _q(compiled_stmt, params: dict, sess: Session):
    """Bound-parameter execute helper.

    Centralizes the execute pattern so the static analyzer sees a
    single allowlisted sink per query. ``compiled_stmt`` is a
    SQLAlchemy ``TextClause`` (built via ``text(SQL_CONST)``); runtime
    data flows only through the ``params`` dict — never into the SQL
    string itself.
    """
    return sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: module-level text() constants + bound params only
        compiled_stmt, params
    )


def _safe_int(value: Any, default: int = 0) -> int:
    """Defensive int coercion for aggregate columns.

    COUNT() results are never NULL but the linter wants an explicit
    try/except anchor — same pattern as reporting._safe_int.
    """
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


_STMT_LIST_CHANNEL_ACCOUNTS = text(SQL_LIST_CHANNEL_ACCOUNTS)
_STMT_GET_CHANNEL_ACCOUNT = text(SQL_GET_CHANNEL_ACCOUNT)
_STMT_GET_CHANNEL_ACCOUNT_BY_EXTERNAL = text(SQL_GET_CHANNEL_ACCOUNT_BY_EXTERNAL)
_STMT_COUNT_CHANNEL_PRODUCTS = text(SQL_COUNT_CHANNEL_PRODUCTS)
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
        shop_id=row.shop_id,
        account_name=row.account_name,
        region=row.region,
        seller_type=row.seller_type,
        status=row.status,
        synced_at=row.synced_at,
        opened_date=row.opened_date,
    )


def _row_to_channel_product(row: Any) -> ChannelProductOut:
    return ChannelProductOut(
        id=row.id,
        shop_pk=row.shop_pk,
        spu_id=row.spu_id,
        title=row.title,
        status=row.status,
        source_created_at=row.source_created_at,
        source_updated_at=row.source_updated_at,
        # 2026-09-06 all-SPU tab: current effective manual cost + the
        # local MinIO mirror URL (resolved from mirror_object_key).
        unit_cost=row.unit_cost,
        currency=row.currency,
        cost_method=row.cost_method,
        image_url=_resolve_mirror_url(row.mirror_object_key),
        main_image_url=row.main_image_url,
    )


def _row_to_sales_order(row: Any) -> SalesOrderOut:
    return SalesOrderOut(
        id=row.id,
        shop_pk=row.shop_pk,
        order_id=row.order_id,
        status=row.status,
        currency=row.currency,
        payment_amount=row.payment_amount,
        total_amount=row.total_amount,
        order_time=row.order_time,
        order_modify_time=row.order_modify_time,
        paid_at=row.paid_at,
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
def list_shops(
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


@router.get(
    "/channel-accounts/by-external/{shop_id}",
    response_model=ChannelAccountOut,
    summary="Look up a channel account by its upstream (external) account id",
    description=(
        "**Single-source spec:** `tech-doc/api/channel-accounts-by-external.md`.\n\n"
        "Reverse-lookup endpoint: given the upstream shop_id "
        "(`shop_id`) and a `platform` filter, return the internal "
        "`commerce.shops` row. Replaces the list-and-filter "
        "pattern (`GET /channel-accounts?platform=...` + client-side search) "
        "with one round-trip and a clean 404.\n\n"
        "**Auth.** `Authorization: Bearer <key>` or `X-API-Key: <key>`; "
        "role = `readonly` (whole `/v2/commerce/*` prefix is readonly).\n\n"
        "**Path.** `{shop_id}` — upstream shop_id (string, e.g. "
        "`7494763368967603447`).\n\n"
        '**Query.** `platform` (string, default `"tiktok"`, ≤ 32 chars). '
        "**Required for uniqueness:** `shop_id` is only unique "
        "within a platform — once we onboard miaoshou accounts, the same "
        "external id may exist under `tiktok` and `miaoshou` separately.\n\n"
        "**Response.** 200 with `ChannelAccountOut`; 404 when no row matches; "
        "401 without key; 403 for role < readonly."
    ),
    responses={
        200: {
            "description": "Matching channel account.",
            "content": {
                "application/json": {
                    "example": {
                        "id": 314,
                        "platform": "tiktok",
                        "shop_id": "7494763368967603447",
                        "account_name": "Bridge nook",
                        "region": "VN",
                        "seller_type": "CROSS_BORDER",
                        "status": "active",
                    }
                }
            },
        },
        401: {"description": "Missing / invalid / disabled API key."},
        403: {"description": "API key role < readonly."},
        404: {"description": "No `commerce.shops` row matches `(platform, shop_id)`."},
    },
)
def get_channel_account_by_external(
    shop_id: str,
    sess: Session = Depends(get_session),
    platform: str = Query(
        default="tiktok",
        max_length=32,
        description=(
            "Platform filter. Required for uniqueness — "
            "`shop_id` is only unique within a platform. "
            "Default: `tiktok`."
        ),
    ),
) -> ChannelAccountOut:
    """Look up a channel account by its upstream (external) account id.

    Full contract: see `tech-doc/api/channel-accounts-by-external.md`.
    """
    row = _q(
        _STMT_GET_CHANNEL_ACCOUNT_BY_EXTERNAL,
        {"platform": platform, "ext": shop_id},
        sess,
    ).first()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel account not found for platform={platform!r} shop_id={shop_id!r}",
        )
    return _row_to_channel_account(row)


@router.get("/channel-accounts/{shop_pk}", response_model=ChannelAccountOut)
def get_channel_account(
    shop_pk: int,
    sess: Session = Depends(get_session),
) -> ChannelAccountOut:
    row = _q(_STMT_GET_CHANNEL_ACCOUNT, {"id": shop_pk}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel account not found")
    return _row_to_channel_account(row)


def _sort_key(sort: str, order: str) -> str:
    """Map (sort, order) onto the allowlist; default = id asc."""
    if sort not in ("created_at", "updated_at", "unit_cost", "status", "id"):
        return "id-asc"
    return f"{sort}-{order}"


@router.get("/channel-products", response_model=list[ChannelProductOut])
def list_products_spu(
    sess: Session = Depends(get_session),
    shop_pk: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    has_orders: bool = Query(
        default=False,
        description=(
            "Only return SPUs that appear on at least one sales order "
            "line (spu_pk bound). False (default) returns the whole "
            "catalogue."
        ),
    ),
    sort: str = Query(
        default="id",
        pattern="^(id|created_at|updated_at|unit_cost|status)$",
        description=(
            "Catalogue column to sort on: id (insertion order), "
            "created_at / updated_at (source timestamps), unit_cost "
            "(current effective manual cost; cost-less rows always tail), "
            "or status (ACTIVATE=在售 first, then DELETED, then "
            "SELLER_DEACTIVATED)."
        ),
    ),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    response: Response = None,  # type: ignore[assignment]  # injected by FastAPI
) -> list[ChannelProductOut]:
    key = _sort_key(sort, order)
    params = {
        "acct_id": shop_pk,
        "status": status_filter,
        "has_orders": has_orders,
    }
    # Total matching rows (same filter, ignoring page bounds) — exposed as
    # X-Total-Count so the UI can render 共 N 行 without switching the
    # bare-array body to an envelope. Missing response (unit test without
    # FastAPI injection) simply skips the header.
    if response is not None:
        total = _q(
            _STMT_COUNT_CHANNEL_PRODUCTS,
            params,
            sess,
        ).scalar()
        response.headers["X-Total-Count"] = str(total or 0)
    page_params = dict(params)
    page_params["limit"] = limit
    page_params["offset"] = offset
    rows = _q(
        _SQL_LIST_CHANNEL_PRODUCTS[key],
        page_params,
        sess,
    ).all()
    return [_row_to_channel_product(r) for r in rows]


@router.get("/channel-products/{spu_pk}", response_model=ChannelProductOut)
def get_channel_product(
    spu_pk: int,
    sess: Session = Depends(get_session),
) -> ChannelProductOut:
    row = _q(_STMT_GET_CHANNEL_PRODUCT, {"id": spu_pk}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel product not found")
    return _row_to_channel_product(row)


@router.get(
    "/channel-products/{spu_pk}/variants",
    response_model=list[ChannelProductVariantOut],
)
def list_products_sku(
    spu_pk: int,
    sess: Session = Depends(get_session),
) -> list[ChannelProductVariantOut]:
    rows = _q(_STMT_LIST_CHANNEL_VARIANTS, {"id": spu_pk}, sess).all()
    return [
        ChannelProductVariantOut(
            id=r.id,
            spu_pk=r.spu_pk,
            sku_id=r.sku_id,
            seller_sku=r.seller_sku,
            variant_name=r.variant_name,
        )
        for r in rows
    ]


@router.get("/sales-orders", response_model=list[SalesOrderOut])
def list_sales_orders(
    sess: Session = Depends(get_session),
    shop_pk: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[SalesOrderOut]:
    rows = _q(
        _STMT_LIST_SALES_ORDERS,
        {
            "acct_id": shop_pk,
            "status": status_filter,
            "limit": limit,
            "offset": offset,
        },
        sess,
    ).all()
    return [_row_to_sales_order(r) for r in rows]


@router.get("/sales-orders/{order_pk}", response_model=SalesOrderOut)
def get_sales_order(
    order_pk: int,
    sess: Session = Depends(get_session),
) -> SalesOrderOut:
    row = _q(_STMT_GET_SALES_ORDER, {"id": order_pk}, sess).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sales order not found")
    return _row_to_sales_order(row)


@router.get(
    "/sales-orders/{order_pk}/lines",
    response_model=list[SalesOrderLineOut],
)
def list_sales_order_lines(
    order_pk: int,
    sess: Session = Depends(get_session),
) -> list[SalesOrderLineOut]:
    rows = _q(_STMT_LIST_ORDER_LINES, {"id": order_pk}, sess).all()
    return [
        SalesOrderLineOut(
            id=r.id,
            order_pk=r.order_pk,
            external_line_id=r.external_line_id,
            spu_pk=r.spu_pk,
            sku_pk=r.sku_pk,
            quantity=r.quantity,
            unit_price=r.unit_price,
        )
        for r in rows
    ]


@router.get(
    "/channel-accounts/{shop_pk}/order-stats",
    summary="Per-account order aggregate — distinct order count + sum of payment_amount",
)
def channel_account_order_stats(
    shop_pk: int,
    sess: Session = Depends(get_session),
) -> dict:
    """Lightweight aggregate: distinct order count + sum of payment_amount.

    Returns 0/0 when no orders exist (the COALESCE handles NULL from SUM).
    """
    row = _q(_STMT_ACCOUNT_ORDER_STATS, {"id": shop_pk}, sess).one()
    return {
        "shop_pk": shop_pk,
        "distinct_orders": _safe_int(row.n),
        "total_payment_amount": str(row.total),
    }
