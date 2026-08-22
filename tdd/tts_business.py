"""Pure business functions for tts-erp.

Each function:
- Takes its dependencies as parameters (creds, http, repo) — no globals
- Returns a SyncResult
- Does NOT raise on business errors; uses SyncResult.error instead

HTTP framework code (FastAPI handler, BaseHTTPRequestHandler) and PG
connection management live elsewhere. This module is pure.
"""
from __future__ import annotations

from typing import Any

from domain import Creds, HttpClient, SyncResult


# Maximum pages to fetch in one sync call. Safety cap to avoid
# runaway pagination if TikTok keeps returning next_page_token.
_MAX_PAGES = 50


def sync_orders(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # OrderRepository — typed as Any to avoid circular import
) -> SyncResult:
    """Sync orders via TikTok /order/202309/orders/search.

    Behavior preserved from tts_erp.py:_sync_orders (Phase 0):
    - POST /order/202309/orders/search
    - shop_cipher + page_size + sort_field/order go in QUERY STRING
    - order_status + create_time_ge/lt go in BODY (raw int/string)
    - Paginates via next_page_token, up to 50 pages
    - Persists each order via repo.upsert()
    - Stops pagination if a page returns code != 0 (partial result OK)
    """
    page_size = int(body.get("page_size") or 50)
    order_status = body.get("order_status")
    create_time_ge = body.get("create_time_ge")
    create_time_lt = body.get("create_time_lt")

    extra_params: dict[str, str] = {
        "shop_cipher": creds.shop_cipher,
        "page_size": str(min(page_size, 100)),  # TikTok max is 100
        "sort_field": "create_time",
        "sort_order": "DESC",
    }
    search_body: dict[str, Any] = {}
    if order_status is not None:
        # TikTok expects string, not int (per 36009004 type validation)
        search_body["order_status"] = str(order_status)
    if create_time_ge is not None:
        search_body["create_time_ge"] = int(create_time_ge)
    if create_time_lt is not None:
        search_body["create_time_lt"] = int(create_time_lt)

    first = http.request(
        "POST", "/order/202309/orders/search",
        body=search_body if search_body else None,
        extra_params=extra_params,
    )
    if first.get("code") != 0:
        return SyncResult(saved=0, total=0, pages=0, error=str(first.get("message", "unknown")))

    data = first.get("data") or {}
    order_list = data.get("order_list") or data.get("orders") or data.get("list") or []
    saved = _persist_orders(repo, creds.shop_id, order_list)

    total = data.get("total") or len(order_list)
    next_token = data.get("next_page_token") or data.get("page_token")
    pages = 1
    while next_token and pages < _MAX_PAGES:
        extra_params["page_token"] = next_token
        nxt = http.request(
            "POST", "/order/202309/orders/search",
            body=search_body if search_body else None,
            extra_params=extra_params,
        )
        if nxt.get("code") != 0:
            # Sub-sequent page error: keep what we have, return success
            break
        d = nxt.get("data") or {}
        for o in d.get("order_list") or d.get("orders") or d.get("list") or []:
            if repo.upsert(creds.shop_id, o):
                saved += 1
        next_token = d.get("next_page_token") or d.get("page_token")
        pages += 1

    return SyncResult(saved=saved, total=total, pages=pages)


def _persist_orders(repo, shop_id: str, orders: list[dict[str, Any]]) -> int:
    """Persist a batch of orders, counting successful upserts."""
    saved = 0
    for o in orders:
        if repo.upsert(shop_id, o):
            saved += 1
    return saved


def sync_payments(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # PaymentRepository
) -> SyncResult:
    """Sync payments via TikTok /finance/202309/payments.

    GET endpoint. create_time_ge/lt go in QUERY STRING as string-encoded
    ints (finance endpoint is lenient on type validation, unlike
    return_refund which strictly rejects string).
    """
    page_size = int(body.get("page_size") or 50)
    create_time_ge = body.get("create_time_ge")
    create_time_lt = body.get("create_time_lt")

    extra_params: dict[str, str] = {
        "shop_cipher": creds.shop_cipher,
        "page_size": str(min(page_size, 100)),
        "sort_field": "create_time",
        "sort_order": "DESC",
    }
    if create_time_ge is not None:
        extra_params["create_time_ge"] = str(int(create_time_ge))
    if create_time_lt is not None:
        extra_params["create_time_lt"] = str(int(create_time_lt))

    first = http.request(
        "GET", "/finance/202309/payments",
        body=None, extra_params=extra_params,
    )
    if first.get("code") != 0:
        return SyncResult(saved=0, total=0, pages=0,
                          error=str(first.get("message", "unknown")))

    data = first.get("data") or {}
    pays = data.get("payments") or []
    saved = _persist_payments(repo, creds.shop_id, pays)

    total = data.get("total") or len(pays)
    next_token = data.get("next_page_token")
    pages = 1
    while next_token and pages < _MAX_PAGES:
        extra_params["page_token"] = next_token
        nxt = http.request(
            "GET", "/finance/202309/payments",
            body=None, extra_params=extra_params,
        )
        if nxt.get("code") != 0:
            break
        d = nxt.get("data") or {}
        for p in d.get("payments") or []:
            if repo.upsert(creds.shop_id, p):
                saved += 1
        next_token = d.get("next_page_token")
        pages += 1

    return SyncResult(saved=saved, total=total, pages=pages)


def _persist_payments(repo, shop_id: str, payments: list[dict[str, Any]]) -> int:
    saved = 0
    for p in payments:
        if repo.upsert(shop_id, p):
            saved += 1
    return saved


def sync_statements(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # StatementRepository
) -> SyncResult:
    """Sync settlement statements via TikTok /finance/202309/statements.

    GET endpoint. Uses statement_time_ge/lt (not create_time) as the
    time filter. Same shape as sync_payments.
    """
    page_size = int(body.get("page_size") or 50)
    statement_time_ge = body.get("statement_time_ge")
    statement_time_lt = body.get("statement_time_lt")

    extra_params: dict[str, str] = {
        "shop_cipher": creds.shop_cipher,
        "page_size": str(min(page_size, 100)),
        "sort_field": "statement_time",
        "sort_order": "DESC",
    }
    if statement_time_ge is not None:
        extra_params["statement_time_ge"] = str(int(statement_time_ge))
    if statement_time_lt is not None:
        extra_params["statement_time_lt"] = str(int(statement_time_lt))

    first = http.request(
        "GET", "/finance/202309/statements",
        body=None, extra_params=extra_params,
    )
    if first.get("code") != 0:
        return SyncResult(saved=0, total=0, pages=0,
                          error=str(first.get("message", "unknown")))

    data = first.get("data") or {}
    stmts = data.get("statements") or []
    saved = _persist_simple(repo, creds.shop_id, stmts)

    total = data.get("total") or len(stmts)
    next_token = data.get("next_page_token")
    pages = 1
    while next_token and pages < _MAX_PAGES:
        extra_params["page_token"] = next_token
        nxt = http.request(
            "GET", "/finance/202309/statements",
            body=None, extra_params=extra_params,
        )
        if nxt.get("code") != 0:
            break
        d = nxt.get("data") or {}
        for s in d.get("statements") or []:
            if repo.upsert(creds.shop_id, s):
                saved += 1
        next_token = d.get("next_page_token")
        pages += 1

    return SyncResult(saved=saved, total=total, pages=pages)


def _persist_simple(repo, shop_id: str, items: list[dict[str, Any]]) -> int:
    """Generic persist helper for sync_* endpoints that return id-keyed items."""
    saved = 0
    for item in items:
        if repo.upsert(shop_id, item):
            saved += 1
    return saved


def sync_statement_transactions(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # StatementTransactionRepository
) -> SyncResult:
    """Sync 账单内逐交易明细 via /finance/202309/statements/{sid}/statement_transactions。

    替代 Excel financial_lines + fee_lines 的接口数据源（2026-08-18 probe 确认，
    58 字段/条含 order_id，sort_field 只接受 order_create_time）。

    body:
      {shop_id, statement_ids?: [...], statement_time_ge?, statement_time_lt?, page_size?}
    显式给 statement_ids 就逐个拉；否则按 statement_time 窗口从本地 statements 表选。
    单个 statement 拉取失败不中断其他 statement（部分失败记 error，全败才 error）。
    """
    page_size = int(body.get("page_size") or 100)

    statement_ids: list[str] = []
    if body.get("statement_ids"):
        statement_ids = [str(s) for s in body["statement_ids"] if s]
    else:
        statement_ids = repo.list_statement_ids(
            creds.shop_id,
            statement_time_ge=body.get("statement_time_ge"),
            statement_time_lt=body.get("statement_time_lt"),
        )

    if not statement_ids:
        return SyncResult(saved=0, total=0, pages=0)

    saved = 0
    total = 0
    pages = 0
    errors: list[str] = []
    for sid in statement_ids:
        extra_params: dict[str, str] = {
            "shop_cipher": creds.shop_cipher,
            "page_size": str(min(page_size, 100)),
            "sort_field": "order_create_time",
            "sort_order": "DESC",
        }
        next_token: str | None = None
        stmt_pages = 0
        while stmt_pages < _MAX_PAGES:
            if next_token:
                extra_params["page_token"] = next_token
            r = http.request(
                "GET", f"/finance/202309/statements/{sid}/statement_transactions",
                body=None, extra_params=extra_params,
            )
            if r.get("code") != 0:
                errors.append(f"{sid}: {r.get('message', 'unknown')}")
                break
            data = r.get("data") or {}
            txns = data.get("statement_transactions") or []
            total += len(txns)
            for txn in txns:
                if repo.upsert(creds.shop_id, sid, txn):
                    saved += 1
            stmt_pages += 1
            pages += 1
            next_token = data.get("next_page_token")
            if not next_token:
                break

    if errors and saved == 0:
        return SyncResult(saved=0, total=total, pages=pages,
                          error="; ".join(errors)[:400])
    return SyncResult(saved=saved, total=total, pages=pages,
                      error=("; ".join(errors)[:400] if errors else None))


def sync_returns(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # ReturnRepository
) -> SyncResult:
    """Sync returns via TikTok /return_refund/202309/returns/search.

    POST endpoint. Time filter goes in BODY as int (TikTok strictly
    type-checks query string here, unlike finance endpoints).
    page_size must be in [10, 50] or TikTok returns 98001004.
    """
    page_size = int(body.get("page_size") or 50)
    create_time_ge = body.get("create_time_ge")
    create_time_lt = body.get("create_time_lt")

    extra_params: dict[str, str] = {
        "shop_cipher": creds.shop_cipher,
        "page_size": str(min(max(page_size, 10), 50)),  # clamp to [10, 50]
        "sort_field": "create_time",
        "sort_order": "DESC",
    }
    filter_body: dict[str, Any] = {}
    if create_time_ge is not None:
        filter_body["create_time_ge"] = int(create_time_ge)
    if create_time_lt is not None:
        filter_body["create_time_lt"] = int(create_time_lt)

    first = http.request(
        "POST", "/return_refund/202309/returns/search",
        body=filter_body if filter_body else None,
        extra_params=extra_params,
    )
    if first.get("code") != 0:
        return SyncResult(saved=0, total=0, pages=0,
                          error=str(first.get("message", "unknown")))

    data = first.get("data") or {}
    items = data.get("return_orders") or data.get("returns") or data.get("list") or []
    saved = _persist_simple(repo, creds.shop_id, items)

    total = data.get("total_count") or data.get("total") or len(items)
    next_token = data.get("next_page_token")
    pages = 1
    while next_token and pages < _MAX_PAGES:
        extra_params["page_token"] = next_token
        nxt = http.request(
            "POST", "/return_refund/202309/returns/search",
            body=filter_body if filter_body else None,
            extra_params=extra_params,
        )
        if nxt.get("code") != 0:
            break
        d = nxt.get("data") or {}
        for r in d.get("return_orders") or d.get("returns") or d.get("list") or []:
            if repo.upsert(creds.shop_id, r):
                saved += 1
        next_token = d.get("next_page_token")
        pages += 1

    return SyncResult(saved=saved, total=total, pages=pages)


def sync_cancellations(
    creds: Creds,
    body: dict[str, Any],
    *,
    http: HttpClient,
    repo,  # CancellationRepository
) -> SyncResult:
    """Sync cancellations via TikTok /return_refund/202309/cancellations/search.

    Same shape as sync_returns but different endpoint and response key.
    """
    page_size = int(body.get("page_size") or 50)
    create_time_ge = body.get("create_time_ge")
    create_time_lt = body.get("create_time_lt")

    extra_params: dict[str, str] = {
        "shop_cipher": creds.shop_cipher,
        "page_size": str(min(max(page_size, 10), 50)),
        "sort_field": "create_time",
        "sort_order": "DESC",
    }
    filter_body: dict[str, Any] = {}
    if create_time_ge is not None:
        filter_body["create_time_ge"] = int(create_time_ge)
    if create_time_lt is not None:
        filter_body["create_time_lt"] = int(create_time_lt)

    first = http.request(
        "POST", "/return_refund/202309/cancellations/search",
        body=filter_body if filter_body else None,
        extra_params=extra_params,
    )
    if first.get("code") != 0:
        return SyncResult(saved=0, total=0, pages=0,
                          error=str(first.get("message", "unknown")))

    data = first.get("data") or {}
    items = data.get("cancellations") or data.get("list") or []
    saved = _persist_simple(repo, creds.shop_id, items)

    total = data.get("total_count") or data.get("total") or len(items)
    next_token = data.get("next_page_token")
    pages = 1
    while next_token and pages < _MAX_PAGES:
        extra_params["page_token"] = next_token
        nxt = http.request(
            "POST", "/return_refund/202309/cancellations/search",
            body=filter_body if filter_body else None,
            extra_params=extra_params,
        )
        if nxt.get("code") != 0:
            break
        d = nxt.get("data") or {}
        for c in d.get("cancellations") or d.get("list") or []:
            if repo.upsert(creds.shop_id, c):
                saved += 1
        next_token = d.get("next_page_token")
        pages += 1

    return SyncResult(saved=saved, total=total, pages=pages)
