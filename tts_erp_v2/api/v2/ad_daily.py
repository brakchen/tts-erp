"""Readonly browser and API for ``plugin.ad_daily`` advertising facts.

The API deliberately exposes the source-shaped daily rows instead of folding them
into the SPU profitability model.  Operators use it to inspect what the browser
plugin captured for one seller/campaign/product/day and to audit the extra metrics
that are not promoted to first-class columns.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.api.v2.pages import _sidebar as _shared_sidebar
from tts_erp_v2.api.v2.pages import _sidebar_css as _shared_sidebar_css

router = APIRouter(tags=["ad-daily"])

_FILTER_SQL = """
WHERE (CAST(:seller_id AS text) IS NULL OR d.seller_id = :seller_id)
  AND (CAST(:advertiser_id AS text) IS NULL OR d.advertiser_id = :advertiser_id)
  AND (CAST(:endpoint AS text) IS NULL OR d.endpoint = :endpoint)
  AND (CAST(:day_from AS date) IS NULL OR d.day >= :day_from)
  AND (CAST(:day_to AS date) IS NULL OR d.day <= :day_to)
  AND (
    CAST(:q AS text) IS NULL
    OR d.campaign_id ILIKE :q ESCAPE '\\'
    OR d.product_id ILIKE :q ESCAPE '\\'
  )
"""

_STMT_LIST_SQL = """
SELECT
  d.id,
  d.seller_id,
  s.id AS shop_pk,
  s.account_name AS shop_name,
  d.advertiser_id,
  d.campaign_id,
  d.product_id,
  p.title AS product_title,
  d.endpoint,
  d.day,
  d.mixed_real_cost,
  d.onsite_roi2_shopping_sku,
  d.onsite_roi2_shopping_value,
  d.onsite_mixed_real_roi2_shopping,
  d.metrics_extra,
  d.created_at,
  d.updated_at
FROM plugin.ad_daily AS d
LEFT JOIN commerce.shops AS s
  ON s.platform = 'tiktok' AND s.shop_id = d.seller_id
LEFT JOIN commerce.products_spu AS p
  ON p.shop_pk = s.id AND p.spu_id = d.product_id
"""

# 表头排序白名单：key -> 固定 SQL 表达式。用户输入只用来查这张表，
# 拼进 ORDER BY 的永远是字面量表达式，不接受自由 SQL 片段。
_SORT_COLUMNS: dict[str, str] = {
    "day": "d.day",
    "shop": "COALESCE(s.account_name, d.seller_id)",
    "campaign": "d.campaign_id",
    "product": "COALESCE(p.title, d.product_id)",
    "spend": "d.mixed_real_cost",
    "orders": "d.onsite_roi2_shopping_sku",
    "gmv": "d.onsite_roi2_shopping_value",
    "roi": "d.onsite_mixed_real_roi2_shopping",
    "updated_at": "d.updated_at",
}
_DEFAULT_SORT = "day"
_DEFAULT_ORDER = "desc"


def _order_clause(sort: str, order: str) -> str:
    """Build a whitelisted ``ORDER BY`` clause for the detail listing.

    Sortable keys are fixed; anything else is a 422 instead of a silent
    fallback, so a typo'd column can't quietly return unsorted rows.
    ``NULLS LAST`` keeps rows missing a metric at the bottom in both
    directions, and the ``updated_at`` / ``id`` tiebreakers keep pagination
    stable for equal values.
    """
    expression = _SORT_COLUMNS.get(sort)
    if expression is None:
        raise HTTPException(
            status_code=422,
            detail=f"sort must be one of: {', '.join(sorted(_SORT_COLUMNS))}",
        )
    direction = order.strip().lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="order must be asc or desc")
    arrow = direction.upper()
    return (
        f"ORDER BY {expression} {arrow} NULLS LAST, "
        f"d.updated_at {arrow}, d.id {arrow}"
    )


def _list_statement(sort: str, order: str):
    return text(
        _STMT_LIST_SQL
        + _FILTER_SQL
        + "\n"
        + _order_clause(sort, order)
        + "\nLIMIT :limit OFFSET :offset\n"
    )

_STMT_SUMMARY = text(
    """
SELECT
  COUNT(*) AS row_count,
  COALESCE(SUM(d.mixed_real_cost), 0::numeric(20,4)) AS spend,
  COALESCE(SUM(d.onsite_roi2_shopping_sku), 0) AS attributed_orders,
  COALESCE(SUM(d.onsite_roi2_shopping_value), 0::numeric(20,4)) AS attributed_gmv,
  CASE
    WHEN COALESCE(SUM(d.mixed_real_cost), 0) > 0
    THEN ROUND(
      COALESCE(SUM(d.onsite_roi2_shopping_value), 0)
      / SUM(d.mixed_real_cost),
      4
    )
    ELSE NULL
  END AS weighted_roi
FROM plugin.ad_daily AS d
"""
    + _FILTER_SQL
)

_STMT_OPTION_SELLERS = text(
    """
SELECT
  d.seller_id,
  MAX(s.account_name) AS shop_name,
  COUNT(*) AS row_count
FROM plugin.ad_daily AS d
LEFT JOIN commerce.shops AS s
  ON s.platform = 'tiktok' AND s.shop_id = d.seller_id
GROUP BY d.seller_id
ORDER BY COALESCE(MAX(s.account_name), d.seller_id), d.seller_id
"""
)

_STMT_OPTION_ADVERTISERS = text(
    """
SELECT d.seller_id, d.advertiser_id, COUNT(*) AS row_count
FROM plugin.ad_daily AS d
GROUP BY d.seller_id, d.advertiser_id
ORDER BY d.seller_id, d.advertiser_id
"""
)

_STMT_OPTION_ENDPOINTS = text(
    """
SELECT d.endpoint, COUNT(*) AS row_count
FROM plugin.ad_daily AS d
GROUP BY d.endpoint
ORDER BY d.endpoint
"""
)

_STMT_OPTION_RANGE = text(
    """
SELECT MIN(day) AS min_day, MAX(day) AS max_day
FROM plugin.ad_daily
"""
)

_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def _asset_version(relative_path: str) -> str:
    """Return a short content hash for browser cache busting."""
    payload = (_STATIC_DIR / relative_path).read_bytes()
    return hashlib.sha256(payload).hexdigest()[:12]


def _contains_pattern(value: str | None) -> str | None:
    """Build a literal ILIKE substring pattern (``%``/``_`` are data)."""
    if value is None or not value.strip():
        return None
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%")
    return f"%{escaped.replace('_', '\\_')}%"


def _filters(
    *,
    seller_id: str | None,
    advertiser_id: str | None,
    endpoint: str | None,
    day_from: date | None,
    day_to: date | None,
    q: str | None,
) -> dict[str, Any]:
    return {
        "seller_id": seller_id.strip() if seller_id and seller_id.strip() else None,
        "advertiser_id": (
            advertiser_id.strip()
            if advertiser_id and advertiser_id.strip()
            else None
        ),
        "endpoint": endpoint.strip() if endpoint and endpoint.strip() else None,
        "day_from": day_from,
        "day_to": day_to,
        "q": _contains_pattern(q),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal(value: Any) -> str | None:
    return str(value) if value is not None else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _item(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "seller_id": row.seller_id,
        "shop_pk": row.shop_pk,
        "shop_name": row.shop_name,
        "advertiser_id": row.advertiser_id,
        "campaign_id": row.campaign_id,
        "product_id": row.product_id,
        "product_title": row.product_title,
        "endpoint": row.endpoint,
        "day": _iso(row.day),
        "mixed_real_cost": _decimal(row.mixed_real_cost),
        "onsite_roi2_shopping_sku": row.onsite_roi2_shopping_sku,
        "onsite_roi2_shopping_value": _decimal(row.onsite_roi2_shopping_value),
        "onsite_mixed_real_roi2_shopping": _decimal(
            row.onsite_mixed_real_roi2_shopping
        ),
        "metrics_extra": row.metrics_extra or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


@router.get("/v2/reporting/ad-daily/options")
def ad_daily_options(
    sess: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    """Return bounded filter choices and the observed day range."""
    sellers = sess.execute(_STMT_OPTION_SELLERS).all()
    advertisers = sess.execute(_STMT_OPTION_ADVERTISERS).all()
    endpoints = sess.execute(_STMT_OPTION_ENDPOINTS).all()
    observed = sess.execute(_STMT_OPTION_RANGE).one()
    return {
        "sellers": [
            {
                "seller_id": row.seller_id,
                "shop_name": row.shop_name,
                "row_count": _safe_int(row.row_count),
            }
            for row in sellers
        ],
        "advertisers": [
            {
                "seller_id": row.seller_id,
                "advertiser_id": row.advertiser_id,
                "row_count": _safe_int(row.row_count),
            }
            for row in advertisers
        ],
        "endpoints": [
            {"endpoint": row.endpoint, "row_count": _safe_int(row.row_count)}
            for row in endpoints
        ],
        "min_day": _iso(observed.min_day),
        "max_day": _iso(observed.max_day),
    }


@router.get("/v2/reporting/ad-daily")
def list_ad_daily(
    sess: Annotated[Session, Depends(get_session)],
    seller_id: Annotated[str | None, Query(max_length=128)] = None,
    advertiser_id: Annotated[str | None, Query(max_length=128)] = None,
    endpoint: Annotated[str | None, Query(max_length=512)] = None,
    day_from: date | None = None,
    day_to: date | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[str, Query(max_length=32)] = _DEFAULT_SORT,
    order: Annotated[str, Query(max_length=8)] = _DEFAULT_ORDER,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List source-shaped daily ad rows, sortable by any table header.

    ``q`` is a literal substring search across campaign and product IDs.  Summary
    values cover the full filtered set, not only the current page.

    ``sort`` must be one of :data:`_SORT_COLUMNS` and ``order`` must be ``asc``
    or ``desc``; anything else is a 422 (see :func:`_order_clause`).
    """
    if day_from is not None and day_to is not None and day_from > day_to:
        raise HTTPException(status_code=422, detail="day_from must be <= day_to")

    params = _filters(
        seller_id=seller_id,
        advertiser_id=advertiser_id,
        endpoint=endpoint,
        day_from=day_from,
        day_to=day_to,
        q=q,
    )
    summary = sess.execute(_STMT_SUMMARY, params).one()
    rows = sess.execute(
        _list_statement(sort, order),
        {**params, "limit": limit, "offset": offset},
    ).all()
    total = _safe_int(summary.row_count)
    return {
        "items": [_item(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": {
            "row_count": total,
            "spend": _decimal(summary.spend),
            "attributed_orders": _safe_int(summary.attributed_orders),
            "attributed_gmv": _decimal(summary.attributed_gmv),
            "weighted_roi": _decimal(summary.weighted_roi),
            "currency": "USD",
        },
    }


_PAGE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>广告日明细 · tts-erp</title>
  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">
  <link rel="stylesheet" href="../../static/css/tokens.css?v=__TOKENS_VERSION__">
  <link rel="stylesheet" href="../../static/css/common.css?v=__COMMON_VERSION__">
  <link rel="stylesheet" href="../../static/css/ad-daily.css?v=__CSS_VERSION__">
  <style>__SIDEBAR_CSS__</style>
</head>
<body>
  __SIDEBAR_NAV__
  <header class="mld-masthead">
    <div class="mld-masthead__bar">
      <a class="mld-back" href="../../v2/pages/dashboard">← 控制台</a>
      <span class="mld-system-mark">TTS / MEDIA LEDGER</span>
      <span class="mld-source-chip">plugin.ad_daily</span>
    </div>
    <div class="mld-title-grid">
      <div>
        <p class="mld-kicker">逐日 · 计划 · 商品</p>
        <h1>广告日明细</h1>
        <p class="mld-deck">检查插件采集的原始广告事实。金额保持 TikTok 广告源币种 USD，不套用盈利看板汇率。</p>
      </div>
      <div class="mld-range-board" aria-label="数据日期范围">
        <span>OBSERVED WINDOW</span>
        <strong id="observed-range">读取中…</strong>
        <i aria-hidden="true"></i>
      </div>
    </div>
  </header>

  <main class="mld-shell">
    <form id="filters" class="mld-filter-panel" autocomplete="off">
      <div class="mld-filter-heading">
        <div><span>FILTER STRIP</span><strong>缩小核查范围</strong></div>
        <button type="button" id="reset-filters" class="mld-text-button">清空条件</button>
      </div>
      <div class="mld-filter-grid">
        <label>店铺
          <select id="seller-filter" name="seller_id"><option value="">全部店铺</option></select>
        </label>
        <label>广告账户
          <select id="advertiser-filter" name="advertiser_id"><option value="">全部账户</option></select>
        </label>
        <label>采集接口
          <select id="endpoint-filter" name="endpoint"><option value="">全部接口</option></select>
        </label>
        <label>开始日期<input id="day-from" name="day_from" type="date"></label>
        <label>结束日期<input id="day-to" name="day_to" type="date"></label>
        <label class="mld-query">计划 / 商品 ID
          <input id="query-filter" name="q" type="search" maxlength="200" placeholder="输入完整 ID 或片段">
        </label>
        <label>每页
          <select id="page-size" name="limit">
            <option value="25">25</option><option value="50" selected>50</option><option value="100">100</option><option value="200">200</option>
          </select>
        </label>
        <button class="mld-apply" type="submit">查询明细</button>
      </div>
    </form>

    <section class="mld-summary" aria-label="当前筛选汇总">
      <article><span>ROWS</span><strong id="sum-rows">—</strong><small>明细行</small></article>
      <article><span>SPEND · USD</span><strong id="sum-spend">—</strong><small>实际消耗</small></article>
      <article><span>ATTR. GMV · USD</span><strong id="sum-gmv">—</strong><small>广告归因 GMV</small></article>
      <article><span>ATTR. ORDERS</span><strong id="sum-orders">—</strong><small>广告归因订单</small></article>
      <article class="mld-summary__signal"><span>WEIGHTED ROI</span><strong id="sum-roi">—</strong><small>GMV ÷ 消耗</small></article>
    </section>

    <section class="mld-ledger" aria-labelledby="ledger-title">
      <div class="mld-ledger__heading">
        <div><span>DAILY FACTS</span><h2 id="ledger-title">采集明细账</h2></div>
        <p id="load-status" role="status" aria-live="polite">准备读取</p>
      </div>
      <div class="mld-table-wrap">
        <table class="mld-table">
          <thead><tr>
            <th data-sort="day"><button type="button" class="mld-sort-button" data-sort="day">日期<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th data-sort="shop"><button type="button" class="mld-sort-button" data-sort="shop">店铺 / 广告账户<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th data-sort="campaign"><button type="button" class="mld-sort-button" data-sort="campaign">计划 ID<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th data-sort="product"><button type="button" class="mld-sort-button" data-sort="product">商品<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th class="mld-num" data-sort="spend"><button type="button" class="mld-sort-button" data-sort="spend">消耗 USD<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th class="mld-num" data-sort="orders"><button type="button" class="mld-sort-button" data-sort="orders">归因订单<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th class="mld-num" data-sort="gmv"><button type="button" class="mld-sort-button" data-sort="gmv">归因 GMV<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th class="mld-num" data-sort="roi"><button type="button" class="mld-sort-button" data-sort="roi">实际 ROI<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th data-sort="updated_at"><button type="button" class="mld-sort-button" data-sort="updated_at">更新时间<span class="mld-sort-arrow" aria-hidden="true"></span></button></th>
            <th><span class="visually-hidden">更多指标</span></th>
          </tr></thead>
          <tbody id="ledger-body"></tbody>
        </table>
        <div id="empty-state" class="mld-empty" hidden>
          <strong>当前条件没有明细</strong><span>调整日期、店铺或 ID 后重新查询。</span>
        </div>
      </div>
      <footer class="mld-pagination">
        <span id="page-range">—</span>
        <div><button id="prev-page" type="button">上一页</button><button id="next-page" type="button">下一页</button></div>
      </footer>
    </section>
  </main>
  <noscript><p class="mld-noscript">此页面需要 JavaScript 才能加载广告明细。</p></noscript>
  <script src="../../static/js/ad-daily.js?v=__JS_VERSION__" defer></script>
</body>
</html>
"""


@router.get("/v2/pages/ad-daily", response_class=HTMLResponse)
def ad_daily_page() -> HTMLResponse:
    """Render the authenticated advertising-detail browser shell."""
    html = (
        _PAGE_HTML.replace("__CSS_VERSION__", _asset_version("css/ad-daily.css"))
        .replace("__TOKENS_VERSION__", _asset_version("css/tokens.css"))
        .replace("__COMMON_VERSION__", _asset_version("css/common.css"))
        .replace("__JS_VERSION__", _asset_version("js/ad-daily.js"))
        .replace("__SIDEBAR_CSS__", str(_shared_sidebar_css("ad-daily")))
        .replace("__SIDEBAR_NAV__", str(_shared_sidebar("ad-daily")))
    )
    return HTMLResponse(html)
