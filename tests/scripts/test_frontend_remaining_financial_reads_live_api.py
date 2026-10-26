"""Ad-daily and SPU profitability reads on the owned isolated child.

Real login, real SQL and real HTTP against the maintained fixed collaborator
profile. The original ``clone``/``manual_cost_facts`` fixtures, the original
``checked_session`` owner, the original builtin accounts and the original
``session_client`` helper are reused exactly as they are; this file owns one
new set of test facts, recovers them by ownership intent and removes them
again. It changes no production code, role, formula, runner or fixture owner,
and it copies no financial formula: the backend owns every decision value.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select, text

from tests.scripts.test_frontend_remaining_catalogue_queries_live import session_client
from tests.scripts.test_frontend_remaining_live_api import OwnedClone, owned
from tests.scripts.test_frontend_remaining_live_api import clone as owned_clone
from tests.scripts.test_frontend_remaining_live_api import (
    manual_cost_facts as owned_facts,
)
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_remaining_financial_facts import (
    AD_ENDPOINT,
    Facts,
    _intents,
    _owned_candidates,
    _seeded_facts,
    _set_region,
)
from tests.support.frontend_remaining_financial_facts import (
    financial_facts as facts_owner,
)
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.db.models import (
    AdDaily,
    ChannelAccount,
    ChannelProduct,
    SalesOrder,
    SalesOrderLine,
)
from tts_erp_v2.db.models.fx import ExchangeRateSnapshot

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]

# Expose the original decorated fixtures without wrapping or changing scope.
clone = owned_clone
manual_cost_facts = owned_facts
financial_facts = facts_owner

AD_DAILY = "/v2/reporting/ad-daily"
AD_OPTIONS = "/v2/reporting/ad-daily/options"
PROFIT = "/v2/analytics/spu-profit-deterioration"
SORT_KEYS = (
    "day",
    "shop",
    "campaign",
    "product",
    "spend",
    "orders",
    "gmv",
    "roi",
    "updated_at",
)
RETIRED_KEYS = {
    "severity",
    "state",
    "sample",
    "layer",
    "sampleStatus",
    "sample_status",
    "confirmationStatus",
    "confirmation_status",
    "thresholds",
}


# --- independent SQL (fresh session, not the handler's rows) --------------
def _ad_summary(clone: OwnedClone, seller_id: str) -> dict[str, Any]:
    """Fresh SQL over one seller's advertising rows."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        row = session.execute(
            text(
                "SELECT COUNT(*) AS row_count, "
                "COALESCE(SUM(mixed_real_cost), 0::numeric(20,4)) AS spend, "
                "COALESCE(SUM(onsite_roi2_shopping_sku), 0) AS orders, "
                "COALESCE(SUM(onsite_roi2_shopping_value), 0::numeric(20,4)) AS gmv, "
                "CASE WHEN COALESCE(SUM(mixed_real_cost), 0) > 0 THEN "
                "ROUND(COALESCE(SUM(onsite_roi2_shopping_value), 0) / "
                "SUM(mixed_real_cost), 4) ELSE NULL END AS weighted_roi "
                "FROM plugin.ad_daily WHERE seller_id = :seller_id"
            ),
            {"seller_id": seller_id},
        ).one()
        return {
            "row_count": int(row.row_count),
            "spend": Decimal(row.spend),
            "orders": int(row.orders),
            "gmv": Decimal(row.gmv),
            "weighted_roi": None
            if row.weighted_roi is None
            else Decimal(row.weighted_roi),
        }


def _ad_range(clone: OwnedClone) -> tuple[date | None, date | None]:
    """Fresh SQL over the global observed day range."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        row = session.execute(
            text("SELECT MIN(day) AS min_day, MAX(day) AS max_day FROM plugin.ad_daily")
        ).one()
        return row.min_day, row.max_day


def _selected_fx(clone: OwnedClone) -> int:
    """The snapshot id the rate loader selects: the newest USD snapshot."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        selected = session.scalar(
            select(ExchangeRateSnapshot.id)
            .where(ExchangeRateSnapshot.base_code == "USD")
            .order_by(ExchangeRateSnapshot.id.desc())
            .limit(1)
        )
        return int(selected or 0)


def _order_counts(
    clone: OwnedClone,
    shop_pk: int,
    spu_pks: tuple[int, ...],
    window: dict[str, str],
    zone: str,
) -> tuple[dict[int, int], int]:
    """Distinct paid orders per SPU and for the whole scope, in one window."""
    start = datetime.combine(
        date.fromisoformat(window["startDate"]), time.min, ZoneInfo(zone)
    ).astimezone(UTC)
    end = datetime.combine(
        date.fromisoformat(window["endDate"]) + timedelta(days=1),
        time.min,
        ZoneInfo(zone),
    ).astimezone(UTC)
    scope = (
        SalesOrder.shop_pk == shop_pk,
        SalesOrder.status.in_(PAID_SALES_ORDER_STATUSES),
        SalesOrderLine.spu_pk.in_(spu_pks),
        func.coalesce(SalesOrder.order_time, SalesOrder.paid_at) >= start,
        func.coalesce(SalesOrder.order_time, SalesOrder.paid_at) < end,
    )
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        rows = session.execute(
            select(SalesOrderLine.spu_pk, func.count(func.distinct(SalesOrder.id)))
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_pk)
            .where(*scope)
            .group_by(SalesOrderLine.spu_pk)
        ).all()
        total = session.scalar(
            select(func.count(func.distinct(SalesOrder.id)))
            .select_from(SalesOrder)
            .join(SalesOrderLine, SalesOrderLine.order_pk == SalesOrder.id)
            .where(*scope)
        )
        counts: dict[int, int] = {}
        for row in rows:
            counts[int(row[0] or 0)] = int(row[1] or 0)
        return counts, int(total or 0)


# --- HTTP helpers (real requests through the owned child) ----------------
def _ad(clone: OwnedClone, client: httpx.Client, **params: Any) -> dict:
    """One real advertising read through the owned child."""
    query = {key: value for key, value in params.items() if value is not None}
    return owned(clone, client.get(clone.prefix + AD_DAILY, params=query), 200).json()


def _profit(clone: OwnedClone, client: httpx.Client, **params: Any) -> dict:
    """One real profitability read through the owned child."""
    query = {key: value for key, value in params.items() if value is not None}
    return owned(clone, client.get(clone.prefix + PROFIT, params=query), 200).json()


def _status(
    clone: OwnedClone,
    client: httpx.Client,
    path: str,
    params: dict[str, Any],
    status: int,
) -> httpx.Response:
    """One real request with an expected status."""
    return owned(clone, client.get(clone.prefix + path, params=params), status)


def _assert_server_order(
    clone: OwnedClone,
    rows: list[dict],
    key: str,
    order: str,
    *,
    seller_id: str,
) -> None:
    """Compare IDs to fresh SQL using the database's collation, not Python order."""
    # Published contract: ad_daily._SORT_COLUMNS and its updated_at/id tiebreaks.
    columns = {
        "day": AdDaily.day,
        "shop": func.coalesce(ChannelAccount.account_name, AdDaily.seller_id),
        "campaign": AdDaily.campaign_id,
        "product": func.coalesce(ChannelProduct.title, AdDaily.product_id),
        "spend": AdDaily.mixed_real_cost,
        "orders": AdDaily.onsite_roi2_shopping_sku,
        "gmv": AdDaily.onsite_roi2_shopping_value,
        "roi": AdDaily.onsite_mixed_real_roi2_shopping,
        "updated_at": AdDaily.updated_at,
    }
    statement = (
        select(AdDaily.id)
        .select_from(AdDaily)
        .outerjoin(
            ChannelAccount,
            (ChannelAccount.platform == "tiktok")
            & (ChannelAccount.shop_id == AdDaily.seller_id),
        )
        .outerjoin(
            ChannelProduct,
            (ChannelProduct.shop_pk == ChannelAccount.id)
            & (ChannelProduct.spu_id == AdDaily.product_id),
        )
        .where(AdDaily.seller_id == seller_id)
    )
    column = columns[key]
    if order == "asc":
        statement = statement.order_by(
            column.asc().nulls_last(), AdDaily.updated_at.asc(), AdDaily.id.asc()
        )
    else:
        statement = statement.order_by(
            column.desc().nulls_last(), AdDaily.updated_at.desc(), AdDaily.id.desc()
        )
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        expected_ids = session.scalars(statement).all()
        clone.child.require_live()
    assert [row["id"] for row in rows] == expected_ids


def _keys(payload: Any) -> set[str]:
    """Every key name in a payload."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _keys(value)
    return found


def _is_wire_decimal(value: Any) -> bool:
    """A wire decimal is a string with at most two decimals."""
    if not isinstance(value, str):
        return False
    whole, _, fraction = value.partition(".")
    if not whole.lstrip("-").isdigit():
        return False
    return fraction == "" or (fraction.isdigit() and len(fraction) <= 2)


# --- cases ----------------------------------------------------------------
def test_ad_daily_rows_summary_and_total_match_fresh_sql(
    clone: OwnedClone,
    financial_facts: Facts,
) -> None:
    """The filtered rows, the whole-set summary and the total agree with SQL."""
    facts = financial_facts
    expected = _ad_summary(clone, facts.original_shop_id)
    with session_client(clone, "viewer") as client:
        response = owned(
            clone,
            client.get(
                clone.prefix + AD_DAILY,
                params={"seller_id": facts.original_shop_id, "limit": 10},
            ),
            200,
        )
        body = response.json()
        assert body["total"] == expected["row_count"]
        assert body["limit"] == 10 and body["offset"] == 0
        assert len(body["items"]) == 10
        summary = body["summary"]
        assert summary["row_count"] == expected["row_count"]
        assert summary["currency"] == "USD"
        assert Decimal(summary["spend"]) == expected["spend"]
        assert summary["attributed_orders"] == expected["orders"]
        assert Decimal(summary["attributed_gmv"]) == expected["gmv"]
        assert summary["weighted_roi"] is not None
        assert Decimal(summary["weighted_roi"]) == expected["weighted_roi"]
        # The count travels in the body, not in an invented response header.
        assert "x-total-count" not in response.headers
        rows = _ad(clone, client, seller_id=facts.original_shop_id, limit=500)["items"]
        assert len(rows) == expected["row_count"]
        for row in rows:
            assert row["shop_pk"] == facts.original_shop_pk
            assert row["shop_name"] == facts.original_marker
            assert row["endpoint"] == AD_ENDPOINT
            assert row["seller_id"] == facts.original_shop_id
        joined = [row for row in rows if row["product_id"] in facts.original_spu_ids]
        assert len(joined) == 28
        assert all(row["product_title"] == facts.original_marker for row in joined)
        # A row without a catalogue product keeps its null metrics null.
        empty = [row for row in rows if row["product_id"] == facts.owned.zero_spu_id]
        assert len(empty) == 1
        assert empty[0]["product_title"] is None
        assert empty[0]["mixed_real_cost"] == "0.0000"
        assert empty[0]["onsite_roi2_shopping_value"] is None
        assert empty[0]["onsite_mixed_real_roi2_shopping"] is None
        zero = _ad(
            clone, client, seller_id=facts.original_shop_id, q=facts.owned.zero_spu_id
        )
        assert zero["summary"]["weighted_roi"] is None
        assert Decimal(zero["summary"]["spend"]) == 0


def test_ad_daily_filters_options_and_server_sorts(
    clone: OwnedClone,
    financial_facts: Facts,
) -> None:
    """Filters, options and every documented sort come from the server."""
    facts = financial_facts
    marker = facts.owned.marker
    with session_client(clone, "viewer") as client:
        assert _ad(clone, client, seller_id=facts.original_shop_id)["total"] == 29
        assert _ad(clone, client, seller_id=facts.owned.shop_ids[0])["total"] == 56
        assert _ad(clone, client, advertiser_id=marker + "_adv")["total"] == 28
        assert (
            _ad(
                clone,
                client,
                seller_id=facts.original_shop_id,
                endpoint="/not/an/endpoint",
            )["total"]
            == 0
        )
        assert (
            _ad(
                clone,
                client,
                seller_id=facts.original_shop_id,
                day_from=facts.anchor.isoformat(),
            )["total"]
            == 3
        )
        assert (
            _ad(
                clone,
                client,
                seller_id=facts.original_shop_id,
                day_from=(facts.anchor - timedelta(days=13)).isoformat(),
                day_to=facts.anchor.isoformat(),
            )["total"]
            == 29
        )
        # Search covers campaign and product IDs; the zero campaign has an underscore.
        assert (
            _ad(clone, client, seller_id=facts.original_shop_id, q="%")["total"] == 0
        )
        assert (
            _ad(clone, client, seller_id=facts.original_shop_id, q="_")["total"] == 29
        )
        # A wildcard underscore would match the final hex character; a literal must not.
        assert (
            _ad(
                clone,
                client,
                seller_id=facts.original_shop_id,
                q=facts.owned.zero_spu_id[:-1] + "_",
            )["total"]
            == 0
        )
        assert (
            _ad(
                clone,
                client,
                seller_id=facts.original_shop_id,
                q=facts.owned.zero_spu_id,
            )["total"]
            == 1
        )
        options = owned(clone, client.get(clone.prefix + AD_OPTIONS), 200).json()
        sellers = {row["seller_id"]: row for row in options["sellers"]}
        assert sellers[facts.original_shop_id]["row_count"] == 29
        assert sellers[facts.original_shop_id]["shop_name"] == facts.original_marker
        advertisers = {
            (row["seller_id"], row["advertiser_id"]): row["row_count"]
            for row in options["advertisers"]
        }
        assert advertisers[(facts.original_shop_id, marker + "_adv")] == 28
        endpoints = {row["endpoint"]: row["row_count"] for row in options["endpoints"]}
        assert endpoints[AD_ENDPOINT] >= 28
        min_day, max_day = _ad_range(clone)
        assert min_day is not None and max_day is not None
        assert options["min_day"] == min_day.isoformat()
        assert options["max_day"] == max_day.isoformat()
        for key in SORT_KEYS:
            for order in ("asc", "desc"):
                rows = _ad(
                    clone,
                    client,
                    seller_id=facts.original_shop_id,
                    sort=key,
                    order=order,
                    limit=500,
                )["items"]
                assert len(rows) == 29
                _assert_server_order(
                    clone, rows, key, order, seller_id=facts.original_shop_id
                )


def test_ad_daily_rejects_invalid_filters_and_anonymous(
    clone: OwnedClone,
    financial_facts: Facts,
) -> None:
    """Invalid filters are refused for a readonly session, anonymous is 401."""
    invalid = (
        {"sort": "not_a_column"},
        {"order": "sideways"},
        {"day_from": "2026-05-05", "day_to": "2026-05-01"},
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
        {"q": "x" * 201},
    )
    with session_client(clone, "viewer") as client:
        for params in invalid:
            _status(clone, client, AD_DAILY, params, 422)
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as anon:
        owned(clone, anon.get(clone.prefix + AD_DAILY), 401)


def test_profit_scope_windows_and_totals_match_fresh_sql(
    clone: OwnedClone,
    financial_facts: Facts,
) -> None:
    """The profitability read keeps its scope, windows and server decisions."""
    facts = financial_facts
    with session_client(clone, "viewer") as client:
        body = _profit(clone, client, shop_pk=facts.own_shop_pk)
        meta = body["meta"]
        assert meta["shopPk"] == facts.own_shop_pk
        assert meta["anchorDate"] == facts.anchor.isoformat()
        assert meta["latestAvailableDate"] == facts.anchor.isoformat()
        assert meta["dataAsOf"] == facts.anchor.isoformat()
        assert meta["windowDays"] == 7
        assert meta["reportingTimezone"] == "Asia/Ho_Chi_Minh"
        assert meta["activityBypassed"] is False
        assert meta["stale"] is True
        assert meta["requestId"]
        assert meta["coverage"]["status"] == "limited"
        assert body["total"] == 4
        assert body["totals"]["analysisSpuCount"] == 4
        # The lifetime paid-order gate excludes the small-count SPU.
        assert tuple(item["spuPk"] for item in body["items"]) == facts.scope_pks
        # The rate loader must select this file's own complete USD snapshot.
        assert _selected_fx(clone) == facts.fx_snapshot_id
        zone = meta["reportingTimezone"]
        for label in ("previous", "current"):
            window = meta["periods"][label]
            per_spu, scope = _order_counts(
                clone, facts.own_shop_pk, facts.scope_pks, window, zone
            )
            assert body["totals"][label + "OrderCount"] == scope
            for item in body["items"]:
                assert item[label + "OrderCount"] == per_spu.get(item["spuPk"], 0)
        # The zero-order SPU keeps the server's no-order-spend fact.
        zero_item = next(
            item for item in body["items"] if item["spuPk"] == facts.scope_pks[3]
        )
        assert zero_item["currentOrderCount"] == 0
        assert zero_item["currentNoOrderAdSpend"] is True
        for item in body["items"]:
            for label in ("previous", "current"):
                for field in ("SpendCny", "NetProfitCny", "Roi"):
                    value = item[label + field]
                    assert value is None or _is_wire_decimal(value)
                    if value is not None and Decimal(value) == 0:
                        assert value == "0"
                assert item[label + "OrderCount"] is None or isinstance(
                    item[label + "OrderCount"], int
                )
                assert item[label + "NoOrderAdSpend"] in (True, False, None)
        assert not _keys(body) & RETIRED_KEYS
        # A three-day window changes the periods, not the observed coverage.
        three = _profit(clone, client, shop_pk=facts.own_shop_pk, window_days=3)
        assert three["meta"]["windowDays"] == 3
        assert three["total"] == 4
        assert tuple(item["spuPk"] for item in three["items"]) == facts.scope_pks
        assert three["meta"]["periods"] != meta["periods"]
        assert (
            three["meta"]["periods"]["current"]["startDate"]
            != meta["periods"]["current"]["startDate"]
        )
        assert three["meta"]["latestAvailableDate"] == meta["latestAvailableDate"]
        assert (
            three["meta"]["coverage"]["advertising"] == meta["coverage"]["advertising"]
        )
        assert {item["windowDays"] for item in three["items"]} == {3}


def test_profit_scope_selection_and_rejections(
    clone: OwnedClone,
    financial_facts: Facts,
) -> None:
    """Exact ids, activity, invalid input and a shop without a region."""
    facts = financial_facts
    with session_client(clone, "viewer") as client:
        for invalid in ({"window_days": 5}, {"activity": "bad"}):
            _status(
                clone, client, PROFIT, {"shop_pk": facts.own_shop_pk, **invalid}, 422
            )
        exact = _profit(
            clone, client, shop_pk=facts.own_shop_pk, spu_ids=facts.scope_pks[0]
        )
        assert exact["total"] == 1
        assert exact["meta"]["activityBypassed"] is True
        assert exact["items"][0]["spuPk"] == facts.scope_pks[0]
        every = _profit(clone, client, shop_pk=facts.own_shop_pk, activity="all")
        assert every["total"] == 5
        _status(
            clone,
            client,
            PROFIT,
            {"shop_pk": facts.own_shop_pk, "spu_ids": 99999999},
            422,
        )
        future = _status(
            clone,
            client,
            PROFIT,
            {
                "shop_pk": facts.own_shop_pk,
                "anchor_date": (facts.anchor + timedelta(days=5)).isoformat(),
            },
            422,
        ).json()
        assert future["detail"]["latestAvailableDate"] == facts.anchor.isoformat()
        assert future["detail"]["requestId"]
        _status(
            clone,
            client,
            PROFIT,
            {"shop_pk": facts.own_shop_pk, "anchor_date": "0001-01-01"},
            422,
        )
        historical = _profit(
            clone,
            client,
            shop_pk=facts.own_shop_pk,
            activity="all",
            anchor_date=(facts.anchor - timedelta(days=30)).isoformat(),
        )
        assert (
            historical["meta"]["anchorDate"]
            == (facts.anchor - timedelta(days=30)).isoformat()
        )
        _status(
            clone,
            client,
            PROFIT,
            {"shop_pk": facts.own_shop_pk, "severity": "high"},
            422,
        )
        # A shop without a reporting region cannot be read.
        _status(clone, client, PROFIT, {"shop_pk": facts.noreg_shop_pk}, 503)
        _set_region(clone, facts.noreg_shop_pk, facts.owned.marker, "VN")
        readable = _profit(clone, client, shop_pk=facts.noreg_shop_pk)
        assert readable["total"] == 4
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as anon:
        owned(
            clone,
            anon.get(clone.prefix + PROFIT, params={"shop_pk": facts.own_shop_pk}),
            401,
        )


def test_setup_failure_cleanup_recovery(
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
) -> None:
    """A setup failure must still remove every owned row it committed."""
    owned_intents = _intents(
        "TEST_financial_cleanup_" + uuid4().hex, manual_cost_facts[0]
    )
    with (
        pytest.raises(AssertionError, match="EXPECTED_SETUP_FAILURE"),
        _seeded_facts(
            clone, owned_intents, manual_cost_facts, fail_before_capture=True
        ),
    ):
        pytest.fail("the committed setup fault must happen before Facts is returned")
    leftover = sorted(
        name for name, rows in _owned_candidates(clone, owned_intents).items() if rows
    )
    assert not leftover, f"owned rows survived the setup failure: {leftover}"
