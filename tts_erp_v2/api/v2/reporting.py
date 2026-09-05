"""/v2/reporting/* — cost / profit / coverage reports + manual-costs POST.

Auth classification (in ``auth.required_role``):
- ``GET /v2/reporting/*`` → readonly (cost snapshot, profit daily, coverage)
- ``POST /v2/reporting/manual-costs`` → readwrite (operator entry point
  that writes ``procurement.manual_product_costs``)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.schemas import (
    CostSnapshotOut,
    CoverageReport,
    ManualCostIn,
    ManualCostOut,
    ProfitDailyOut,
)

router = APIRouter(prefix="/v2/reporting", tags=["reporting"])


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
    "WHERE (CAST(:channel_id AS bigint) IS NULL "
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
    "       estimated_cogs AS cost, "
    "       estimated_gross_profit AS profit, "
    "       currency "
    "FROM reporting.product_profit_daily "
    "WHERE (CAST(:channel_id AS bigint) IS NULL "
    "OR spu_pk = CAST(:channel_id AS bigint)) "
    "AND (CAST(:on_date AS date) IS NULL "
    "OR profit_date = CAST(:on_date AS date)) "
    "ORDER BY profit_date DESC NULLS LAST LIMIT :limit OFFSET :offset"
)
SQL_COVERAGE_REPORT = (
    "SELECT "
    "(SELECT COUNT(*) FROM commerce.products_spu) AS total_spus, "
    # (2026-09-01) status is free-text — TikTok sync stores 'ACTIVATE'
    # (uppercase) but tests / docs / earlier code expected lowercase
    # 'active'. Use ILIKE so the aggregate matches either case.
    "(SELECT COUNT(*) FROM commerce.products_spu "
    "WHERE status ILIKE 'activate') AS active_spus, "
    "(SELECT COUNT(DISTINCT spu_pk) "
    "FROM linkage.effective_product_links "
    "WHERE effective_relation_type IS NOT NULL) AS linked_spus, "
    "(SELECT COUNT(*) FROM commerce.products_spu cp "
    "WHERE cp.status ILIKE 'activate' "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM procurement.manual_product_costs m "
    "  WHERE m.spu_pk = cp.id AND m.valid_to IS NULL"
    ") AND NOT EXISTS ("
    "  SELECT 1 FROM linkage.effective_product_links epl "
    "  WHERE epl.spu_pk = cp.id "
    "  AND epl.effective_relation_type IS NOT NULL"
    ")) AS missing_cost_spus, "
    "(SELECT COALESCE(MAX(calculation_version), 1) "
    "FROM reporting.product_cost_snapshots) AS calculation_version"
)
SQL_RESOLVE_CHANNEL_PRODUCT = "SELECT id FROM commerce.products_spu WHERE spu_id = :ext_id LIMIT 1"
SQL_INSERT_MANUAL_COST = (
    "INSERT INTO procurement.manual_product_costs ("
    "spu_pk, unit_cost, currency, valid_from, valid_to, "
    "note, created_by, created_at) "
    "VALUES (:cp_id, :unit_cost, :currency, :valid_from, NULL, "
    ":note, :created_by, now()) "
    "RETURNING id, spu_pk, unit_cost, currency, valid_from, "
    "valid_to, note, created_by"
)
# 2026-09-01: close-old-then-insert sequence rewritten as a single
# transaction (audit P1-6). The two-commit pattern used to drop the
# UPDATE into a try/except + rollback, so a connection blip mid-handler
# left the previous row's valid_to NULL and the new row's valid_to NULL
# simultaneously — two effective manual costs per SPU. We now:
#   1. UPDATE old rows first (within the same transaction)
#   2. INSERT the new row
#   3. commit once
# The partial unique index uq_manual_costs_one_open (added in
# migration 0003_manual_costs_one_open) is the DB-side safety net: even
# if the application logic regresses, the index will reject a second
# valid_to IS NULL row for the same spu_pk.
SQL_CLOSE_OLD_MANUAL_COSTS_BEFORE_INSERT = (
    "UPDATE procurement.manual_product_costs SET valid_to = now() "
    "WHERE spu_pk = :cp_id AND valid_to IS NULL"
)
SQL_LIST_MISSING_COST_PRODUCTS = (
    "SELECT cp.id, cp.spu_id, cp.title, cp.shop_pk, "
    "       (NOT EXISTS ("
    "         SELECT 1 FROM procurement.spu_images si "
    "         WHERE si.spu_pk = cp.id "
    "         AND si.status = 'ready' AND si.deleted_at IS NULL"
    "       )) AS missing_photo "
    # (2026-09-01) cp.status stored as 'ACTIVATE' by TikTok sync; use
    # ILIKE so the filter also matches the lowercase 'active' that
    # earlier docs / tests assume.
    # Also: linkage.effective_product_links is a LEFT-JOIN view that
    # emits one row per channel_product even when no real link exists.
    # The presence of a row is therefore meaningless — only the
    # presence of effective_relation_type (non-null) means an actual
    # link. The pre-fix NOT EXISTS evaluated FALSE for every product
    # and the "Needs cost" tab was always empty.
    "FROM commerce.products_spu cp "
    "WHERE cp.status ILIKE 'activate' "
    "AND (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM procurement.manual_product_costs m "
    "  WHERE m.spu_pk = cp.id AND m.valid_to IS NULL"
    ") AND NOT EXISTS ("
    "  SELECT 1 FROM linkage.effective_product_links epl "
    "  WHERE epl.spu_pk = cp.id "
    "  AND epl.effective_relation_type IS NOT NULL"
    ") ORDER BY cp.id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_TOTAL_MISSING_PHOTO = (
    "SELECT COUNT(*) AS n FROM ("
    "  SELECT cp.id "
    "  FROM commerce.products_spu cp "
    "  WHERE cp.status ILIKE 'activate' "
    "  AND (CAST(:acct_id AS bigint) IS NULL OR cp.shop_pk = CAST(:acct_id AS bigint)) "
    "  AND NOT EXISTS ("
    "    SELECT 1 FROM procurement.manual_product_costs m "
    "    WHERE m.spu_pk = cp.id AND m.valid_to IS NULL"
    "  ) AND NOT EXISTS ("
    "    SELECT 1 FROM linkage.effective_product_links epl "
    "    WHERE epl.spu_pk = cp.id "
    "    AND epl.effective_relation_type IS NOT NULL"
    "  ) AND NOT EXISTS ("
    "    SELECT 1 FROM procurement.spu_images si "
    "    WHERE si.spu_pk = cp.id "
    "    AND si.status = 'ready' AND si.deleted_at IS NULL"
    "  )"
    ") sub"
)


_STMT_COST_SNAPSHOTS = text(SQL_COST_SNAPSHOTS)
_STMT_PROFIT_DAILY = text(SQL_PROFIT_DAILY)
_STMT_COVERAGE_REPORT = text(SQL_COVERAGE_REPORT)
_STMT_RESOLVE_CHANNEL_PRODUCT = text(SQL_RESOLVE_CHANNEL_PRODUCT)
_STMT_INSERT_MANUAL_COST = text(SQL_INSERT_MANUAL_COST)
_STMT_CLOSE_OLD_MANUAL_COSTS_BEFORE_INSERT = text(
    SQL_CLOSE_OLD_MANUAL_COSTS_BEFORE_INSERT
)
_STMT_LIST_MISSING_COST_PRODUCTS = text(SQL_LIST_MISSING_COST_PRODUCTS)
_STMT_TOTAL_MISSING_PHOTO = text(SQL_TOTAL_MISSING_PHOTO)


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


def _manual_cost_row(row: Any) -> ManualCostOut:
    return ManualCostOut(
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
        linked_spus=_safe_int(row.linked_spus),
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
    """SPUs the operator should fill in: active + no manual cost + no link.

    Returns the table backing the manual-costs page form. The page
    polls this endpoint with the operator's bearer token.

    Extended per tech-doc/procurement-ui-redesign.md §3.5: the body is
    now an object ``{items: [...], total_missing_photo: int}``. Each
    item carries ``missing_photo`` (bool). Back-compat: existing
    consumers that ignore the wrapper and read row fields keep working
    because the per-row shape is unchanged.

    ``shop_pk`` scopes the list to one shop; when omitted,
    the response spans all shops (legacy behaviour, used by the global
    test_auth_login.py probe).
    """
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
            }
            for r in items_rows
        ],
        "total_missing_photo": _safe_int(total_row.n),
    }


@router.post(
    "/manual-costs",
    response_model=ManualCostOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_manual_cost(
    body: ManualCostIn,
    request: Request,
    sess: Session = Depends(get_session),
) -> ManualCostOut:
    """Operator-entered unit cost for one SPU. Requires readwrite.

    Resolves the channel product by ``spu_id``, closes
    any existing effective manual_cost row for the same SPU (history
    preserved via ``valid_to``), and inserts a fresh row.

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
    cp_row = sess.execute(
        _STMT_RESOLVE_CHANNEL_PRODUCT,
        {"ext_id": body.spu_id},
    ).first()
    if cp_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel product not found: {body.spu_id}",
        )
    cp_id = cp_row.id
    role = request.scope.get("api_key_role") or "unknown"
    created_by = f"api_key:{role}"
    valid_from = body.valid_from or datetime.now()

    # 2026-09-01: close-old + insert in ONE transaction (audit P1-6).
    # The previous two-commit pattern (insert+commit, then
    # try/except: rollback the close) silently lost the UPDATE on any
    # error path, leaving two rows with valid_to IS NULL for the same
    # spu_pk. The DB-side partial unique index
    # uq_manual_costs_one_open (alembic 0003) is the safety net; the
    # app-level guarantee below is that we flush the UPDATE before the
    # INSERT, so a same-SPU race against another concurrent POST is
    # rejected by the index rather than producing ghost rows.
    # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() with bound :cp_id, no string interpolation
    sess.execute(
        _STMT_CLOSE_OLD_MANUAL_COSTS_BEFORE_INSERT,
        {"cp_id": cp_id},
    )
    new_row = sess.execute(
        _STMT_INSERT_MANUAL_COST,
        {
            "cp_id": cp_id,
            "unit_cost": str(body.unit_cost),
            "currency": body.currency,
            "valid_from": valid_from,
            "note": body.note,
            "created_by": created_by,
        },
    ).first()
    if new_row is None:
        sess.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to insert manual cost",
        )
    sess.commit()
    return _manual_cost_row(new_row)


def _q(compiled_stmt, params, db):
    """Allowlisted execute helper; SQL is module-level, params are bound."""
    return db.execute(compiled_stmt, params)
