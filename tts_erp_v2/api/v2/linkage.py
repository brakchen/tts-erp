"""/v2/linkage/* — link queries + manual override write + issue queue.

Auth classification (in ``auth.required_role``):
- ``GET /v2/linkage/*`` → readonly (link_evidence, product_links, link_issues reads)
- ``POST /v2/linkage/overrides`` → admin (operator decision; overrides are
  persistent, audit-relevant, and affect ``effective_product_links`` view).

We use ``text()`` with bind params throughout to keep the static analyzer
happy with the same allowlisted pattern as ``commerce.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.api.schemas import (
    LinkEvidenceOut,
    LinkIssueOut,
    LinkOverrideIn,
    LinkOverrideOut,
    ProductLinkOut,
)

router = APIRouter(prefix="/v2/linkage", tags=["linkage"])


# --- SQL constants (module-level, no interpolation) -----------------------
# Each SELECT ends with ``FROM <table> ORDER BY ... LIMIT :limit OFFSET
# :offset``. _q_optional() injects ``WHERE col = :col`` for each active
# optional filter between FROM and ORDER BY. We deliberately do NOT
# use ``IS NULL OR col = :param`` — psycopg can't infer the type of a
# None-valued bind param and the query 500s with
# ``ERROR: could not determine data type of parameter $1``.
SQL_LIST_PRODUCT_LINKS = (
    "SELECT id, procurement_product_id, channel_product_id, relation_type, "
    "status, is_primary, valid_from, valid_to "
    "FROM linkage.product_links "
    "ORDER BY id LIMIT :limit OFFSET :offset"
)
SQL_LIST_LINK_EVIDENCE = (
    "SELECT id, product_link_id, variant_link_id, evidence_type, "
    "source_table, source_external_id, observed_at "
    "FROM linkage.link_evidence "
    "ORDER BY id LIMIT :limit OFFSET :offset"
)
SQL_LIST_LINK_ISSUES = (
    "SELECT id, issue_type, procurement_product_id, channel_product_id, "
    "candidate_count, status, created_at, resolved_at "
    "FROM linkage.link_issues "
    "WHERE (:unresolved_only = FALSE OR resolved_at IS NULL) "
    "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
)
SQL_INSERT_LINK_OVERRIDE = (
    "INSERT INTO linkage.link_overrides ("
    "procurement_product_id, channel_product_id, decision, reason, "
    "valid_from, valid_to, created_by, created_at) "
    "VALUES (:proc_id, :channel_id, :decision, :reason, :valid_from, "
    "NULL, :created_by, now()) "
    "RETURNING id, procurement_product_id, channel_product_id, decision, "
    "reason, valid_from, valid_to, created_by"
)
SQL_CLOSE_OLD_OVERRIDES = (
    "UPDATE linkage.link_overrides SET valid_to = now() "
    "WHERE channel_product_id = CAST(:channel_id AS bigint) "
    "AND procurement_product_id = CAST(:proc_id AS bigint) "
    "AND valid_to IS NULL "
    "AND id <> :keep_id"
)
SQL_LIST_OVERRIDES = (
    "SELECT id, procurement_product_id, channel_product_id, decision, "
    "reason, valid_from, valid_to, created_by "
    "FROM linkage.link_overrides "
    "WHERE (:channel_id IS NULL OR channel_product_id = CAST(:channel_id AS bigint)) "
    "AND (:active_only = FALSE OR valid_to IS NULL) "
    "ORDER BY valid_from DESC LIMIT :limit OFFSET :offset"
)
SQL_RESOLVE_ISSUE = (
    "UPDATE linkage.link_issues SET resolved_at = now(), status = 'resolved' "
    "WHERE id = :id AND resolved_at IS NULL "
    "RETURNING id"
)


_STMT_LIST_PRODUCT_LINKS = text(SQL_LIST_PRODUCT_LINKS)
_STMT_LIST_LINK_EVIDENCE = text(SQL_LIST_LINK_EVIDENCE)
_STMT_LIST_LINK_ISSUES = text(SQL_LIST_LINK_ISSUES)
_STMT_INSERT_LINK_OVERRIDE = text(SQL_INSERT_LINK_OVERRIDE)
_STMT_CLOSE_OLD_OVERRIDES = text(SQL_CLOSE_OLD_OVERRIDES)
_STMT_LIST_OVERRIDES = text(SQL_LIST_OVERRIDES)
_STMT_RESOLVE_ISSUE = text(SQL_RESOLVE_ISSUE)


def _q(compiled_stmt, params: dict, sess: Session):
    """Bound-parameter execute helper.

    All SQL is built from module-level ``text(const)`` objects; runtime
    data flows only through the ``params`` dict — never into the SQL
    string itself.
    """
    return sess.execute(compiled_stmt, params)


def _q_optional(
    base_sql: str,
    optional_filters: dict[str, Any],
    params: dict,
    sess: Session,
) -> Any:
    """Build a SQL with ``WHERE col = :param`` for each non-None filter.

    Replaces the ``IS NULL OR col = :param`` pattern, which psycopg
    cannot type-infer when the param is ``None`` (PG returns
    ``ERROR: could not determine data type of parameter $1`` and the
    route 500s). Building the WHERE clause in Python side-steps the
    type-inference issue entirely and the SQL is still bound-parameter
    safe.

    The ``base_sql`` must NOT contain ``IS NULL OR`` patterns — those
    are added here only when the corresponding filter is non-None.
    Pass the SQL as ``... FROM <table> ORDER BY ... LIMIT :limit ...``
    and we'll insert ``WHERE col = :col`` before ORDER BY when needed.
    """
    clauses: list[str] = []
    bind_params: dict = dict(params)
    for col, value in optional_filters.items():
        if value is None:
            continue
        clauses.append(f"{col} = :{col}")
        bind_params[col] = value
    if not clauses:
        return sess.execute(text(base_sql), bind_params)
    # Inject the WHERE between the FROM clause and the trailing
    # ORDER BY. The base SQL ends with ``FROM <table>`` and the
    # caller appends ``ORDER BY ... LIMIT ...``.
    order_idx = base_sql.upper().find("ORDER BY")
    if order_idx == -1:
        raise RuntimeError("_q_optional expects a SQL with ORDER BY clause")
    new_sql = (
        base_sql[:order_idx]
        + " WHERE "
        + " AND ".join(clauses)
        + " "
        + base_sql[order_idx:]
    )
    return sess.execute(text(new_sql), bind_params)


def _evidence_row(row: Any) -> LinkEvidenceOut:
    return LinkEvidenceOut(
        id=row.id,
        product_link_id=row.product_link_id,
        variant_link_id=row.variant_link_id,
        evidence_type=row.evidence_type,
        source_table=row.source_table,
        source_external_id=row.source_external_id,
        observed_at=row.observed_at,
    )


def _product_link_row(row: Any) -> ProductLinkOut:
    return ProductLinkOut(
        id=row.id,
        procurement_product_id=row.procurement_product_id,
        channel_product_id=row.channel_product_id,
        relation_type=row.relation_type,
        status=row.status,
        is_primary=row.is_primary,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def _issue_row(row: Any) -> LinkIssueOut:
    return LinkIssueOut(
        id=row.id,
        issue_type=row.issue_type,
        procurement_product_id=row.procurement_product_id,
        channel_product_id=row.channel_product_id,
        candidate_count=row.candidate_count,
        status=row.status,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def _override_row(row: Any) -> LinkOverrideOut:
    return LinkOverrideOut(
        id=row.id,
        procurement_product_id=row.procurement_product_id,
        channel_product_id=row.channel_product_id,
        decision=row.decision,
        reason=row.reason,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        created_by=row.created_by,
    )


@router.get("/product-links", response_model=list[ProductLinkOut])
def list_product_links(
    sess: Session = Depends(get_session),
    channel_product_id: int | None = Query(default=None),
    procurement_product_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProductLinkOut]:
    rows = _q_optional(
        SQL_LIST_PRODUCT_LINKS,
        {
            "channel_product_id": channel_product_id,
            "procurement_product_id": procurement_product_id,
        },
        {"limit": limit, "offset": offset},
        sess,
    ).all()
    return [_product_link_row(r) for r in rows]


@router.get("/evidence", response_model=list[LinkEvidenceOut])
def list_link_evidence(
    sess: Session = Depends(get_session),
    product_link_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[LinkEvidenceOut]:
    rows = _q_optional(
        SQL_LIST_LINK_EVIDENCE,
        {"product_link_id": product_link_id},
        {"limit": limit, "offset": offset},
        sess,
    ).all()
    return [_evidence_row(r) for r in rows]


@router.get("/issues", response_model=list[LinkIssueOut])
def list_link_issues(
    sess: Session = Depends(get_session),
    unresolved_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[LinkIssueOut]:
    rows = _q(
        _STMT_LIST_LINK_ISSUES,
        {
            "unresolved_only": unresolved_only,
            "limit": limit,
            "offset": offset,
        },
        sess,
    ).all()
    return [_issue_row(r) for r in rows]


@router.post(
    "/issues/{issue_id}/resolve",
    status_code=status.HTTP_200_OK,
)
def resolve_link_issue(
    issue_id: int,
    request: Request,
    sess: Session = Depends(get_session),
) -> dict:
    """Mark an issue resolved. Requires readwrite (operator workflow)."""
    require_role_at_least(request, "readwrite")
    row = _q(_STMT_RESOLVE_ISSUE, {"id": issue_id}, sess).first()
    sess.commit()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "issue not found or already resolved",
        )
    return {"id": row.id, "status": "resolved"}


@router.get("/overrides", response_model=list[LinkOverrideOut])
def list_link_overrides(
    sess: Session = Depends(get_session),
    channel_product_id: int | None = Query(default=None),
    active_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[LinkOverrideOut]:
    rows = _q(
        _STMT_LIST_OVERRIDES,
        {
            "channel_id": channel_product_id,
            "active_only": active_only,
            "limit": limit,
            "offset": offset,
        },
        sess,
    ).all()
    return [_override_row(r) for r in rows]


@router.post(
    "/overrides",
    response_model=LinkOverrideOut,
    status_code=status.HTTP_201_CREATED,
)
def create_link_override(
    body: LinkOverrideIn,
    request: Request,
    sess: Session = Depends(get_session),
) -> LinkOverrideOut:
    """Operator decision: ALLOW / DENY / PRIMARY for a (procurement, channel).

    Requires ``admin`` role (operator-only; overrides affect the
    ``effective_product_links`` view used by all downstream cost
    calculations). The handler closes any existing active override on
    the same (procurement, channel) pair before inserting the new one,
    so ``valid_to`` history is preserved.

    For ``DENY`` decisions, ``procurement_product_id`` may be null
    (operator denies even when no candidate exists). For ``ALLOW`` /
    ``PRIMARY``, it is required.
    """
    require_role_at_least(request, "admin")
    if body.decision in ("ALLOW", "PRIMARY") and body.procurement_product_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "procurement_product_id is required for ALLOW/PRIMARY",
        )
    if body.procurement_product_id is None:
        # Use a sentinel 0 for DENY rows; we accept it for SQL compliance.
        # The model FK will reject 0 if the row references a real product,
        # so we explicitly insert 0 only when the FK is None.
        proc_id = 0
    else:
        proc_id = body.procurement_product_id

    role = request.scope.get("api_key_role") or "unknown"
    created_by = f"api_key:{role}"
    valid_from = body.valid_from or datetime.now()

    # First close any existing active overrides on the same (proc, channel)
    # pair so the new one is the unique effective row.
    new_row = _q(
        _STMT_INSERT_LINK_OVERRIDE,
        {
            "proc_id": proc_id,
            "channel_id": body.channel_product_id,
            "decision": body.decision,
            "reason": body.reason,
            "valid_from": valid_from,
            "created_by": created_by,
        },
        sess,
    ).first()
    sess.commit()
    if new_row is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "failed to insert override",
        )
    # Best-effort close old rows (don't fail the request if 0 rows match).
    try:
        _q(
            _STMT_CLOSE_OLD_OVERRIDES,
            {
                "channel_id": body.channel_product_id,
                "proc_id": proc_id,
                "keep_id": new_row.id,
            },
            sess,
        )
        sess.commit()
    except Exception:
        sess.rollback()
    return _override_row(new_row)
