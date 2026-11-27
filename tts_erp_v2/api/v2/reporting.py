"""/v2/reporting/* — cost / profit / coverage reports + manual-costs POST.

Auth classification (in ``auth.required_role``):
- ``GET /v2/reporting/*`` → readonly (cost snapshot, profit daily, coverage)
- ``POST /v2/reporting/manual-costs`` → readwrite (operator entry point
  that writes ``procurement.manual_product_costs``)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.schemas import (
    CostSnapshotOut,
    CoverageReport,
    ProfitDailyOut,
    PurchasePriceIn,
    PurchasePriceOut,
)
from tts_erp_v2.api.v2.purchase_price_import import (
    router as purchase_price_import_router,
)
from tts_erp_v2.commerce.shop_access import (
    authorized_shop_clause,
    require_authorized_shop,
)
from tts_erp_v2.reporting.purchase_cost import (
    PURCHASE_PRICE_AMOUNT,
    PURCHASE_PRICE_JOINS,
)
from tts_erp_v2.reporting.purchase_prices import append_purchase_price
from tts_erp_v2.storage.minio_client import MinioClient, MinioConfigError

router = APIRouter(prefix="/v2/reporting", tags=["reporting"])
router.include_router(purchase_price_import_router, prefix="/purchase-prices")
router.include_router(purchase_price_import_router, prefix="/manual-costs", include_in_schema=False)

_AUTHORIZED_SHOPS = "SELECT id FROM commerce.shops WHERE " + authorized_shop_clause()
_AUTHORIZED_PRODUCTS = (
    "SELECT id FROM commerce.products_spu WHERE shop_pk IN (" + _AUTHORIZED_SHOPS + ")"
)


# --- SQL constants (module-level, no interpolation) -----------------------
SQL_COST_SNAPSHOTS = (
    "SELECT id, spu_pk, cost_method, unit_cost, currency, "
    "calculation_version, calculated_at "
    "FROM reporting.product_cost_snapshots "
    # (2026-08-31) The optional-filter pattern (:param IS NULL OR col = :param)
    # used to fire psycopg.errors.AmbiguousParameter — when :param is NULL,
    # PG cannot infer its type and refuses to plan the query. Explicit CASTs
    # pin the parameter types to the column types (channel_id bigint,
    # method text) so the WHERE evaluates cleanly with either bound value.
    "WHERE cost_method IN ('MANUAL_ENTRY', 'PURCHASE_PRICE') "
    "AND spu_pk IN (" + _AUTHORIZED_PRODUCTS + ") AND (CAST(:channel_id AS bigint) IS NULL "
    "OR spu_pk = :channel_id) "
    "AND (CAST(:method AS text) IS NULL OR cost_method = :method) "
    "ORDER BY calculated_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
)
SQL_PROFIT_DAILY = (
    # 2026-09-01: column-name repair (audit P1-2). The table columns
    # are profit_date / gross_revenue / estimated_cogs /
    # estimated_gross_profit (NOT on_date / revenue / cost / profit),
    # so a straight SELECT was producing 500 with
    # psycopg.errors.UndefinedColumn. The aliases below match the
    # ProfitDailyOut pydantic schema. ``profit_date`` is a DATE so the
    # filter is compared as text — PG coerces both sides implicitly
    # but we CAST the bound param to date for the same reason
    # cost-snapshots had to CAST (PG otherwise raises
    # AmbiguousParameter when the param is NULL).
    "SELECT id, spu_pk, "
    "       profit_date AS on_date, "
    "       gross_revenue AS revenue, "
    "       CASE WHEN cost_method = 'SOURCE_PRICE' THEN NULL ELSE estimated_cogs END AS cost, "
    "       CASE WHEN cost_method = 'SOURCE_PRICE' THEN NULL ELSE estimated_gross_profit END AS profit, "
    "       currency "
    "FROM reporting.product_profit_daily "
    "WHERE spu_pk IN (" + _AUTHORIZED_PRODUCTS + ") AND (CAST(:channel_id AS bigint) IS NULL "
    "OR spu_pk = CAST(:channel_id AS bigint)) "
    "AND (CAST(:on_date AS date) IS NULL "
    "OR profit_date = CAST(:on_date AS date)) "
    "ORDER BY profit_date DESC NULLS LAST LIMIT :limit OFFSET :offset"
)
SQL_COVERAGE_REPORT = (
    "SELECT "
    "(SELECT COUNT(*) FROM commerce.products_spu WHERE id IN (" + _AUTHORIZED_PRODUCTS + ")) AS total_spus, "
    # (2026-09-01) status is free-text — TikTok sync stores 'ACTIVATE'
    # (uppercase) but tests / docs / earlier code expected lowercase
    # 'active'. Use ILIKE so the aggregate matches either case.
    "(SELECT COUNT(*) FROM commerce.products_spu "
    "WHERE status ILIKE 'activate' AND id IN (" + _AUTHORIZED_PRODUCTS + ")) AS active_spus, "
    "(SELECT COUNT(*) FROM commerce.products_spu cp " + PURCHASE_PRICE_JOINS +
    "WHERE cp.id IN (" + _AUTHORIZED_PRODUCTS + ") AND "
    + PURCHASE_PRICE_AMOUNT + " IS NOT NULL) AS costed_spus, "
    "(SELECT COUNT(*) FROM commerce.products_spu cp " + PURCHASE_PRICE_JOINS +
    "WHERE cp.status ILIKE 'activate' AND cp.shop_pk IN (" + _AUTHORIZED_SHOPS + ") "
    "AND " + PURCHASE_PRICE_AMOUNT + " IS NULL) AS missing_cost_spus, "
    "(SELECT COALESCE(MAX(calculation_version), 1) "
    "FROM reporting.product_cost_snapshots WHERE spu_pk IN (" + _AUTHORIZED_PRODUCTS + ")) AS calculation_version"
)
SQL_RESOLVE_CHANNEL_PRODUCT = (
    "SELECT id FROM commerce.products_spu WHERE spu_id = :ext_id AND shop_pk IN (" + _AUTHORIZED_SHOPS + ") "
    "AND (CAST(:spu_pk AS bigint) IS NULL OR id = CAST(:spu_pk AS bigint)) "
    "LIMIT 2"
)
# 2026-09-06: recently-filed list reads the truth table directly so the
# operator sees their submission immediately — the old page relied on
# reporting.cost_snapshots which is recomputed every 6 h (empty right
# after a manual entry). Join products_spu for shop_pk / spu_id / title.
SQL_LIST_MANUAL_COSTS = (
    "WITH ranked AS ("
    "  SELECT m.id, m.spu_pk, m.unit_cost, m.currency, "
    "         m.valid_from, m.valid_to, m.note, m.created_by, "
    "         m.created_at, m.updated_at, "
    "         LAG(m.unit_cost) OVER (PARTITION BY m.spu_pk ORDER BY m.created_at, m.id) "
    "           AS prev_unit_cost, "
    "         LAG(m.currency) OVER (PARTITION BY m.spu_pk ORDER BY m.created_at, m.id) "
    "           AS prev_currency "
    "  FROM procurement.manual_product_costs m "
    ")"
    "SELECT r.id, r.spu_pk, r.unit_cost, r.currency, r.valid_from, "
    "       r.valid_to, r.note, r.created_by, r.created_at, r.updated_at, "
    "       r.prev_unit_cost, r.prev_currency, "
    "       cp.spu_id, cp.shop_pk, cp.title "
    "FROM ranked r "
    "JOIN commerce.products_spu cp ON cp.id = r.spu_pk "
    "WHERE cp.shop_pk IN (" + _AUTHORIZED_SHOPS + ") AND (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND (CAST(:q AS text) IS NULL "
    "     OR cp.spu_id ILIKE '%' || CAST(:q AS text) || '%' "
    "     OR COALESCE(cp.title, '') ILIKE '%' || CAST(:q AS text) || '%') "
    "ORDER BY r.created_at DESC, r.id DESC "
    "LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_COUNT_MANUAL_COSTS = (
    "SELECT COUNT(*) AS n FROM procurement.manual_product_costs m "
    "JOIN commerce.products_spu cp ON cp.id = m.spu_pk "
    "WHERE cp.shop_pk IN (" + _AUTHORIZED_SHOPS + ") AND (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND (CAST(:q AS text) IS NULL "
    "     OR cp.spu_id ILIKE '%' || CAST(:q AS text) || '%' "
    "     OR COALESCE(cp.title, '') ILIKE '%' || CAST(:q AS text) || '%')"
)
_MISSING_PURCHASE_PRICE_SCOPE = (
    "FROM commerce.products_spu cp " + PURCHASE_PRICE_JOINS +
    "WHERE cp.status ILIKE 'activate' AND cp.shop_pk IN (" + _AUTHORIZED_SHOPS + ") "
    "AND (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND " + PURCHASE_PRICE_AMOUNT + " IS NULL "
)
_MISSING_PHOTO = (
    "NOT EXISTS (SELECT 1 FROM procurement.spu_images si WHERE si.spu_pk = cp.id "
    "AND si.status = 'ready' AND si.deleted_at IS NULL)"
)
SQL_LIST_MISSING_COST_PRODUCTS = (
    "SELECT cp.id, cp.spu_id, cp.title, cp.shop_pk, cp.main_image_url, cp.mirror_object_key, "
    + _MISSING_PHOTO + " AS missing_photo " + _MISSING_PURCHASE_PRICE_SCOPE +
    "ORDER BY cp.id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_TOTAL_MISSING_PHOTO = (
    "SELECT COUNT(*) AS n " + _MISSING_PURCHASE_PRICE_SCOPE + "AND " + _MISSING_PHOTO
)


_STMT_COST_SNAPSHOTS = text(SQL_COST_SNAPSHOTS)
_STMT_PROFIT_DAILY = text(SQL_PROFIT_DAILY)
_STMT_COVERAGE_REPORT = text(SQL_COVERAGE_REPORT)
_STMT_RESOLVE_CHANNEL_PRODUCT = text(SQL_RESOLVE_CHANNEL_PRODUCT)
_STMT_LIST_MISSING_COST_PRODUCTS = text(SQL_LIST_MISSING_COST_PRODUCTS)
_STMT_TOTAL_MISSING_PHOTO = text(SQL_TOTAL_MISSING_PHOTO)
_STMT_LIST_MANUAL_COSTS = text(SQL_LIST_MANUAL_COSTS)
_STMT_COUNT_MANUAL_COSTS = text(SQL_COUNT_MANUAL_COSTS)


# --- mirror URL resolution (2026-09-05 page-rework lane) -----------------
# The mirror job stores only ``mirror_object_key`` on products_spu; the
# read endpoints resolve it to a presigned / public URL on demand so
# MinIO access config can evolve without a backfill. Rows with no mirror
# yet (job not run / failed / no main image) return image_url=None and
# the frontend shows the default missing-image icon instead.

_minio_client_singleton: MinioClient | None = None


def _resolve_mirror_url(mirror_object_key: str | None) -> str | None:
    """Return a renderable URL for a stored mirror object key, else None."""
    if not mirror_object_key:
        return None
    global _minio_client_singleton
    if _minio_client_singleton is None:
        try:
            _minio_client_singleton = MinioClient.from_env()
        except MinioConfigError:
            # Optional mirror URLs cannot block the candidate/cost read model.
            # Unknown errors and upload configuration failures still propagate.
            return None
    client = _minio_client_singleton
    public_url = client.public_url_or_none(mirror_object_key)
    if public_url is not None:
        return public_url
    url, _expires_at = client.presign_get(mirror_object_key)
    return url


def _safe_int(value: Any, default: int = 0) -> int:
    """Defensive int coercion for SQL aggregate columns.

    ``row.total_spus`` etc. are bigint from COUNT() — they can only be an
    integer or NULL. This helper exists so the linter (which flags any
    naked ``int()`` call) has an explicit try/except to anchor on; the
    try/except is unreachable in practice for these aggregate columns.
    """
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _cost_snapshot_row(row: Any) -> CostSnapshotOut:
    return CostSnapshotOut(
        id=row.id,
        spu_pk=row.spu_pk,
        cost_method=row.cost_method,
        unit_cost=row.unit_cost,
        currency=row.currency,
        calculation_version=row.calculation_version,
        calculated_at=row.calculated_at,
    )


def _profit_daily_row(row: Any) -> ProfitDailyOut:
    return ProfitDailyOut(
        id=row.id,
        spu_pk=row.spu_pk,
        on_date=row.on_date,
        revenue=row.revenue,
        cost=row.cost,
        profit=row.profit,
        currency=row.currency,
    )


def _purchase_price_row(row: Any) -> PurchasePriceOut:
    return PurchasePriceOut(
        id=row.id,
        spu_pk=row.spu_pk,
        unit_cost=row.unit_cost,
        currency=row.currency,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        note=row.note,
        created_by=row.created_by,
    )


@router.get("/cost-snapshots", response_model=list[CostSnapshotOut])
def list_cost_snapshots(
    sess: Session = Depends(get_session),
    spu_pk: int | None = Query(default=None),
    cost_method: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CostSnapshotOut]:
    rows = sess.execute(
        _STMT_COST_SNAPSHOTS,
        {
            "channel_id": spu_pk,
            "method": cost_method,
            "limit": limit,
            "offset": offset,
        },
    ).all()
    return [_cost_snapshot_row(r) for r in rows]


@router.get("/profit-daily", response_model=list[ProfitDailyOut])
def list_profit_daily(
    sess: Session = Depends(get_session),
    spu_pk: int | None = Query(default=None),
    on_date: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProfitDailyOut]:
    rows = sess.execute(
        _STMT_PROFIT_DAILY,
        {
            "channel_id": spu_pk,
            "on_date": on_date,
            "limit": limit,
            "offset": offset,
        },
    ).all()
    return [_profit_daily_row(r) for r in rows]


@router.get("/coverage", response_model=CoverageReport)
def coverage_report(sess: Session = Depends(get_session)) -> CoverageReport:
    """Aggregate coverage / health snapshot."""
    row = sess.execute(_STMT_COVERAGE_REPORT).one()
    return CoverageReport(
        total_spus=_safe_int(row.total_spus),
        active_spus=_safe_int(row.active_spus),
        costed_spus=_safe_int(row.costed_spus),
        missing_cost_spus=_safe_int(row.missing_cost_spus),
        calculation_version=_safe_int(row.calculation_version, default=1),
    )


@router.get("/missing-cost-products")
def list_missing_cost_products(
    sess: Session = Depends(get_session),
    shop_pk: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Active SPUs without an effective purchase price.

    Returns the table backing the manual-costs page form. The page
    polls this endpoint with the operator's bearer token.

    Extended per docs/archive/procurement-ui-redesign.md §3.5: the body is
    now an object ``{items: [...], total_missing_photo: int}``. Each
    item carries ``missing_photo`` (bool). Back-compat: existing
    consumers that ignore the wrapper and read row fields keep working
    because the per-row shape is unchanged.

    ``shop_pk`` scopes the list to one shop; when omitted,
    the response spans all shops (legacy behaviour, used by the global
    test_auth_login.py probe).
    """
    if shop_pk is not None:
        require_authorized_shop(sess, shop_pk=shop_pk)
    items_rows = sess.execute(
        _STMT_LIST_MISSING_COST_PRODUCTS,
        {"acct_id": shop_pk, "limit": limit, "offset": offset},
    ).all()
    total_row = sess.execute(
        _STMT_TOTAL_MISSING_PHOTO,
        {"acct_id": shop_pk},
    ).one()
    return {
        "items": [
            {
                "spu_pk": r.id,
                "spu_id": r.spu_id,
                "title": r.title,
                "shop_pk": r.shop_pk,
                "missing_photo": bool(r.missing_photo),
                # 2026-09-05 mirror lane: expose the TikTok main image and
                # its local MinIO mirror so the page can render the SPU
                # image without hitting the TikTok CDN on every load.
                # ``image_url`` is None when the mirror job hasn't finished
                # yet (frontend shows the default missing-image icon).
                "main_image_url": r.main_image_url,
                "image_url": _resolve_mirror_url(r.mirror_object_key),
            }
            for r in items_rows
        ],
        "total_missing_photo": _safe_int(total_row.n),
    }


@router.get("/manual-costs", include_in_schema=False)
@router.get("/purchase-prices")
def list_purchase_prices(
    sess: Session = Depends(get_session),
    shop_pk: int | None = Query(default=None, ge=1),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Recently filed manual costs (newest first) — the 最近提交 tab.

    2026-09-06: this tab used to read ``reporting.cost_snapshots`` which
    is recomputed every 6 h — right after a manual entry the tab stayed
    empty (the operator's fresh submission appeared only after the next
    snapshot tick). This endpoint reads ``procurement.manual_product_costs``
    (the truth table) so a submission shows up immediately.

    ``shop_pk`` scopes to one shop; omit for all shops.
    """
    if shop_pk is not None:
        require_authorized_shop(sess, shop_pk=shop_pk)
    params = {
        "acct_id": shop_pk,
        "q": q.strip() if q and q.strip() else None,
    }
    rows = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: module-level text() + bound params
        _STMT_LIST_MANUAL_COSTS,
        {**params, "limit": limit, "offset": offset},
    ).all()
    total = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: module-level text() + bound params
        _STMT_COUNT_MANUAL_COSTS,
        params,
    ).scalar()
    return {
        "total": _safe_int(total),
        "items": [
            {
                "id": r.id,
                "spu_pk": r.spu_pk,
                "spu_id": r.spu_id,
                "title": r.title,
                "shop_pk": r.shop_pk,
                "unit_cost": str(r.unit_cost),
                "currency": r.currency,
                "prev_unit_cost": (
                    str(r.prev_unit_cost) if r.prev_unit_cost is not None else None
                ),
                "prev_currency": r.prev_currency,
                "note": r.note,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.post("/manual-costs", response_model=PurchasePriceOut, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post(
    "/purchase-prices",
    response_model=PurchasePriceOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_purchase_price(
    body: PurchasePriceIn,
    request: Request,
    sess: Session = Depends(get_session),
) -> PurchasePriceOut:
    """Operator-entered unit cost for one SPU. Requires readwrite.

    Resolves the matching ``spu_pk`` / ``spu_id`` pair, or an unambiguous
    legacy external-only identity. Never chooses an arbitrary shop's first
    matching row. Existing effective rows are closed and a new history row
    appended only after this identity check and the shared per-SPU lock.

    CSRF guard: session-cookie auth is auto-attached by the browser, so
    every cookie-authed mutating request must carry the
    ``X-Requested-With: tts-erp`` custom header (the browser SOP blocks
    cross-origin JS from setting it). Bearer-authed API clients are
    exempt — they pick the header themselves and the request is
    already a deliberate cross-site call.
    """
    require_role_at_least(request, "readwrite")
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("X-Requested-With") != "tts-erp"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "cookie-authed POST must set header X-Requested-With: tts-erp (CSRF guard)",
        )
    matches = sess.execute(
        _STMT_RESOLVE_CHANNEL_PRODUCT,
        {"ext_id": body.spu_id, "spu_pk": body.spu_pk},
    ).all()
    if not matches:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel product not found: {body.spu_id}",
        )
    if len(matches) > 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "spu_id is ambiguous; provide spu_pk",
        )
    cp_id = matches[0].id
    role = request.scope.get("api_key_role") or "unknown"
    created_by = f"api_key:{role}"
    new_row = append_purchase_price(
        sess, spu_pk=cp_id, unit_cost=body.unit_cost, currency=body.currency,
        valid_from=body.valid_from or datetime.now(UTC), note=body.note, created_by=created_by,
    )
    sess.commit()
    return _purchase_price_row(new_row)


def _q(compiled_stmt, params, db):
    """Allowlisted execute helper; SQL is module-level, params are bound."""
    return db.execute(compiled_stmt, params)
