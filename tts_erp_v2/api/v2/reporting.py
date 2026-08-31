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
    "SELECT id, channel_product_id, cost_method, unit_cost, currency, "
    "calculation_version, calculated_at "
    "FROM reporting.product_cost_snapshots "
    # (2026-08-31) The optional-filter pattern (:param IS NULL OR col = :param)
    # used to fire psycopg.errors.AmbiguousParameter — when :param is NULL,
    # PG cannot infer its type and refuses to plan the query. Explicit CASTs
    # pin the parameter types to the column types (channel_id bigint,
    # method text) so the WHERE evaluates cleanly with either bound value.
    "WHERE (CAST(:channel_id AS bigint) IS NULL "
    "OR channel_product_id = :channel_id) "
    "AND (CAST(:method AS text) IS NULL OR cost_method = :method) "
    "ORDER BY calculated_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
)
SQL_PROFIT_DAILY = (
    "SELECT id, channel_product_id, on_date, revenue, cost, profit, currency "
    "FROM reporting.product_profit_daily "
    "WHERE (:channel_id IS NULL OR channel_product_id = :channel_id) "
    "AND (:on_date IS NULL OR on_date = :on_date) "
    "ORDER BY on_date DESC NULLS LAST LIMIT :limit OFFSET :offset"
)
SQL_COVERAGE_REPORT = (
    "SELECT "
    "(SELECT COUNT(*) FROM commerce.channel_products) AS total_spus, "
    # (2026-09-01) status is free-text — TikTok sync stores 'ACTIVATE'
    # (uppercase) but tests / docs / earlier code expected lowercase
    # 'active'. Use ILIKE so the aggregate matches either case.
    "(SELECT COUNT(*) FROM commerce.channel_products "
    "WHERE status ILIKE 'activate') AS active_spus, "
    "(SELECT COUNT(DISTINCT channel_product_id) "
    "FROM linkage.effective_product_links "
    "WHERE effective_relation_type IS NOT NULL) AS linked_spus, "
    "(SELECT COUNT(*) FROM commerce.channel_products cp "
    "WHERE cp.status ILIKE 'activate' "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM procurement.manual_product_costs m "
    "  WHERE m.channel_product_id = cp.id AND m.valid_to IS NULL"
    ") AND NOT EXISTS ("
    "  SELECT 1 FROM linkage.effective_product_links epl "
    "  WHERE epl.channel_product_id = cp.id "
    "  AND epl.effective_relation_type IS NOT NULL"
    ")) AS missing_cost_spus, "
    "(SELECT COALESCE(MAX(calculation_version), 1) "
    "FROM reporting.product_cost_snapshots) AS calculation_version"
)
SQL_RESOLVE_CHANNEL_PRODUCT = "SELECT id FROM commerce.channel_products WHERE external_product_id = :ext_id LIMIT 1"
SQL_INSERT_MANUAL_COST = (
    "INSERT INTO procurement.manual_product_costs ("
    "channel_product_id, unit_cost, currency, valid_from, valid_to, "
    "note, created_by, created_at) "
    "VALUES (:cp_id, :unit_cost, :currency, :valid_from, NULL, "
    ":note, :created_by, now()) "
    "RETURNING id, channel_product_id, unit_cost, currency, valid_from, "
    "valid_to, note, created_by"
)
SQL_CLOSE_OLD_MANUAL_COSTS = (
    "UPDATE procurement.manual_product_costs SET valid_to = now() "
    "WHERE channel_product_id = :cp_id AND valid_to IS NULL "
    "AND id <> :keep_id"
)
SQL_LIST_MISSING_COST_PRODUCTS = (
    "SELECT cp.id, cp.external_product_id, cp.title, cp.channel_account_id, "
    "       (NOT EXISTS ("
    "         SELECT 1 FROM procurement.spu_images si "
    "         WHERE si.channel_product_id = cp.id "
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
    "FROM commerce.channel_products cp "
    "WHERE cp.status ILIKE 'activate' "
    "AND (CAST(:acct_id AS bigint) IS NULL OR cp.channel_account_id = CAST(:acct_id AS bigint)) "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM procurement.manual_product_costs m "
    "  WHERE m.channel_product_id = cp.id AND m.valid_to IS NULL"
    ") AND NOT EXISTS ("
    "  SELECT 1 FROM linkage.effective_product_links epl "
    "  WHERE epl.channel_product_id = cp.id "
    "  AND epl.effective_relation_type IS NOT NULL"
    ") ORDER BY cp.id LIMIT CAST(:limit AS integer) OFFSET CAST(:offset AS integer)"
)
SQL_TOTAL_MISSING_PHOTO = (
    "SELECT COUNT(*) AS n FROM ("
    "  SELECT cp.id "
    "  FROM commerce.channel_products cp "
    "  WHERE cp.status ILIKE 'activate' "
    "  AND (CAST(:acct_id AS bigint) IS NULL OR cp.channel_account_id = CAST(:acct_id AS bigint)) "
    "  AND NOT EXISTS ("
    "    SELECT 1 FROM procurement.manual_product_costs m "
    "    WHERE m.channel_product_id = cp.id AND m.valid_to IS NULL"
    "  ) AND NOT EXISTS ("
    "    SELECT 1 FROM linkage.effective_product_links epl "
    "    WHERE epl.channel_product_id = cp.id "
    "    AND epl.effective_relation_type IS NOT NULL"
    "  ) AND NOT EXISTS ("
    "    SELECT 1 FROM procurement.spu_images si "
    "    WHERE si.channel_product_id = cp.id "
    "    AND si.status = 'ready' AND si.deleted_at IS NULL"
    "  )"
    ") sub"
)


_STMT_COST_SNAPSHOTS = text(SQL_COST_SNAPSHOTS)
_STMT_PROFIT_DAILY = text(SQL_PROFIT_DAILY)
_STMT_COVERAGE_REPORT = text(SQL_COVERAGE_REPORT)
_STMT_RESOLVE_CHANNEL_PRODUCT = text(SQL_RESOLVE_CHANNEL_PRODUCT)
_STMT_INSERT_MANUAL_COST = text(SQL_INSERT_MANUAL_COST)
_STMT_CLOSE_OLD_MANUAL_COSTS = text(SQL_CLOSE_OLD_MANUAL_COSTS)
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
        channel_product_id=row.channel_product_id,
        cost_method=row.cost_method,
        unit_cost=row.unit_cost,
        currency=row.currency,
        calculation_version=row.calculation_version,
        calculated_at=row.calculated_at,
    )


def _profit_daily_row(row: Any) -> ProfitDailyOut:
    return ProfitDailyOut(
        id=row.id,
        channel_product_id=row.channel_product_id,
        on_date=row.on_date,
        revenue=row.revenue,
        cost=row.cost,
        profit=row.profit,
        currency=row.currency,
    )


def _manual_cost_row(row: Any) -> ManualCostOut:
    return ManualCostOut(
        id=row.id,
        channel_product_id=row.channel_product_id,
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
    channel_product_id: int | None = Query(default=None),
    cost_method: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CostSnapshotOut]:
    rows = sess.execute(
        _STMT_COST_SNAPSHOTS,
        {
            "channel_id": channel_product_id,
            "method": cost_method,
            "limit": limit,
            "offset": offset,
        },
    ).all()
    return [_cost_snapshot_row(r) for r in rows]


@router.get("/profit-daily", response_model=list[ProfitDailyOut])
def list_profit_daily(
    sess: Session = Depends(get_session),
    channel_product_id: int | None = Query(default=None),
    on_date: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProfitDailyOut]:
    rows = sess.execute(
        _STMT_PROFIT_DAILY,
        {
            "channel_id": channel_product_id,
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
    channel_account_id: int | None = Query(default=None, ge=1),
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

    ``channel_account_id`` scopes the list to one shop; when omitted,
    the response spans all shops (legacy behaviour, used by the global
    test_auth_login.py probe).
    """
    items_rows = sess.execute(
        _STMT_LIST_MISSING_COST_PRODUCTS,
        {"acct_id": channel_account_id, "limit": limit, "offset": offset},
    ).all()
    total_row = sess.execute(
        _STMT_TOTAL_MISSING_PHOTO,
        {"acct_id": channel_account_id},
    ).one()
    return {
        "items": [
            {
                "channel_product_id": r.id,
                "external_product_id": r.external_product_id,
                "title": r.title,
                "channel_account_id": r.channel_account_id,
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

    Resolves the channel product by ``external_product_id``, closes
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
        {"ext_id": body.channel_product_external_id},
    ).first()
    if cp_row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel product not found: {body.channel_product_external_id}",
        )
    cp_id = cp_row.id
    role = request.scope.get("api_key_role") or "unknown"
    created_by = f"api_key:{role}"
    valid_from = body.valid_from or datetime.now()

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
    sess.commit()
    if new_row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to insert manual cost",
        )
    # Close old rows on the same SPU so only the new one is effective.
    try:
        sess.execute(
            _STMT_CLOSE_OLD_MANUAL_COSTS,
            {"cp_id": cp_id, "keep_id": new_row.id},
        )
        sess.commit()
    except Exception:
        sess.rollback()
    return _manual_cost_row(new_row)


def _q(compiled_stmt, params, db):
    """Allowlisted execute helper; SQL is module-level, params are bound."""
    return db.execute(compiled_stmt, params)
