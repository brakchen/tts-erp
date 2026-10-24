"""PRD v3 comparison acceptance: real canonical SQL and availability evidence."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    AdDaily,
    AdRawLog,
    Case,
    CaseLine,
    ChannelAccount,
    ChannelProduct,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
    SyncJob,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
URL = "/v2/analytics/spu-profit-deterioration"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture()
def comparison_scope(db_engine):
    """Three sustained SPUs share orders; fourth has spend and no lifetime orders."""
    anchor = datetime.now(TZ).date() - timedelta(days=4)
    started = datetime.combine(anchor + timedelta(days=1), time(0, 5), TZ)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok", shop_id="TEST_COMPARE_SHOP", region="VN"
        )
        session.add(shop)
        session.flush()
        products = [
            ChannelProduct(
                shop_pk=shop.id, spu_id=f"TEST_COMPARE_{n}", status="ACTIVATE"
            )
            for n in range(5)
        ]
        session.add_all(products)
        session.flush()
        for n, day in enumerate(
            (anchor - timedelta(days=20), anchor - timedelta(days=8), anchor)
        ):
            order = SalesOrder(
                shop_pk=shop.id,
                order_id=f"TEST_COMPARE_ORDER_{n}",
                status="DELIVERED",
                currency="VND",
                order_time=datetime.combine(day, time(0, 15), TZ),
                paid_at=datetime.combine(day, time(0, 15), TZ),
            )
            session.add(order)
            session.flush()
            for product in products[:3]:
                session.add(
                    SalesOrderLine(
                        order_pk=order.id,
                        spu_pk=product.id,
                        external_line_id=f"TEST_LINE_{product.id}",
                        quantity=1,
                        unit_price=100000,
                        currency="VND",
                    )
                )
        # A lifetime-small, recently ordered SPU must not enter regular recent scope.
        small = SalesOrder(
            shop_pk=shop.id,
            order_id="TEST_COMPARE_SMALL",
            status="DELIVERED",
            currency="VND",
            order_time=datetime.combine(anchor, time(12), TZ),
        )
        session.add(small)
        session.flush()
        session.add(
            SalesOrderLine(
                order_pk=small.id,
                spu_pk=products[4].id,
                external_line_id="TEST_SMALL",
                quantity=1,
                unit_price=100000,
                currency="VND",
            )
        )
        for name in ("tiktok.orders", "tiktok.after_sales", "tiktok.finance"):
            session.add(
                SyncJob(
                    job_name=name,
                    started_at=started,
                    finished_at=started + timedelta(hours=2),
                    status="succeeded",
                    rows_failed=0,
                    extra={"shop_id": shop.shop_id},
                )
            )
        for ago in range(14):
            day = anchor - timedelta(days=ago)
            for product in products[:4]:
                session.add(
                    AdDaily(
                        seller_id=shop.shop_id,
                        advertiser_id="TEST_ADV",
                        campaign_id="TEST_CAMPAIGN",
                        endpoint=ENDPOINT,
                        day=day,
                        product_id=product.spu_id,
                        mixed_real_cost=1,
                        onsite_roi2_shopping_sku=0,
                        onsite_roi2_shopping_value=0,
                    )
                )
        sid = session.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots (base_code, upstream_last_update, next_update_at, fetched_at, rates_count) VALUES ('USD', '2098-01-01', '2098-01-02', now(), 3) RETURNING id"
            )
        ).scalar_one()
        for code, rate in [("USD", 1), ("CNY", 7), ("VND", 25000)]:
            session.execute(
                text(
                    "INSERT INTO fx.exchange_rates (snapshot_id, base_code, target_code, rate) VALUES (:sid, 'USD', :code, :rate)"
                ),
                {"sid": sid, "code": code, "rate": rate},
            )
        session.commit()
        scope = {
            "shop": shop.id,
            "products": [p.id for p in products],
            "anchor": anchor,
            "started": started,
            "fx": sid,
        }
    yield scope
    with Session(db_engine) as session:
        order_ids = select(SalesOrder.id).where(SalesOrder.shop_pk == scope["shop"])
        session.execute(delete(Case).where(Case.shop_pk == scope["shop"]))
        session.execute(
            delete(SalesOrderLine).where(SalesOrderLine.order_pk.in_(order_ids))
        )
        session.execute(delete(SalesOrder).where(SalesOrder.shop_pk == scope["shop"]))
        session.execute(delete(AdDaily).where(AdDaily.seller_id == "TEST_COMPARE_SHOP"))
        session.execute(
            delete(AdRawLog).where(AdRawLog.seller_id == "TEST_COMPARE_SHOP")
        )
        session.execute(
            delete(SyncJob).where(
                SyncJob.extra["shop_id"].astext == "TEST_COMPARE_SHOP"
            )
        )
        session.execute(
            delete(SyncIssue).where(SyncIssue.external_id.like("TEST_COMPARE%"))
        )
        session.execute(
            text("DELETE FROM fx.exchange_rate_snapshots WHERE id = :sid"), {"sid": sid}
        )
        session.execute(
            delete(ChannelProduct).where(ChannelProduct.shop_pk == scope["shop"])
        )
        session.execute(
            delete(ChannelAccount).where(ChannelAccount.id == scope["shop"])
        )
        session.commit()


def get(api_client, readonly_key, scope, **params):
    return api_client.get(
        URL,
        params={"shop_pk": scope["shop"], **params},
        headers={
            "Authorization": f"Bearer {readonly_key}",
            "x-request-id": "TEST_COMPARE_REQ",
        },
    )


def test_v3_canonical_values_scope_distinct_totals_and_drilldown(
    api_client, readonly_key, comparison_scope
):
    scope = comparison_scope
    response = get(api_client, readonly_key, scope, limit=1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4
    assert body["totals"] == {
        "analysisSpuCount": 4,
        "previousOrderCount": 1,
        "currentOrderCount": 1,
        "unavailableReason": None,
    }
    assert len(body["items"]) == 1
    assert body["meta"]["windowDays"] == 7
    assert body["meta"]["anchorDate"] == scope["anchor"].isoformat()
    assert body["meta"]["latestAvailableDate"] == scope["anchor"].isoformat()
    assert body["meta"]["stale"] is True
    assert body["meta"]["coverage"]["status"] == "limited"
    assert body["meta"]["requestId"] == "TEST_COMPARE_REQ"
    assert "effectiveConfig" not in body["meta"]
    last = get(api_client, readonly_key, scope, offset=3).json()
    assert last["totals"] == body["totals"]
    item = last["items"][0]
    assert item["spuPk"] == scope["products"][3]
    assert item["currentOrderCount"] == 0
    assert item["currentNoOrderAdSpend"] is True
    assert item["previousNoOrderAdSpend"] is True
    assert Decimal(item["currentSpendCny"]) == 49
    assert Decimal(item["currentNetProfitCny"]) == -49
    # Canonical ROI is net revenue after full-loss cost / spend, not profit/spend.
    assert Decimal(item["currentRoi"]) == 0
    assert item["currentRoiUnavailableReason"] is None
    assert item["basisCalculatedAt"] == last["meta"]["calculatedAt"]
    retired_keys = {
        "severity",
        "state",
        "sampleStatus",
        "layer",
        "warningCode",
        "warningText",
        "roiDecline",
        "netProfitDecline",
        "configVersion",
    }

    def wire_keys(value):
        if isinstance(value, dict):
            return set(value).union(*(wire_keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(wire_keys(child) for child in value))
        return set()

    assert not retired_keys & wire_keys(last)
    query = parse_qs(urlsplit(item["drilldown"]["pageUrl"]).query)
    assert query["shop_pk"] == [str(scope["shop"])]
    assert query["spu_ids"] == ["TEST_COMPARE_3"]
    assert query["w_start"] == [(scope["anchor"] - timedelta(days=6)).isoformat()]
    assert query["w_end"] == [scope["anchor"].isoformat()]
    short = get(api_client, readonly_key, scope, window_days=3).json()
    assert short["total"] == 4
    assert short["meta"]["latestAvailableDate"] == body["meta"]["latestAvailableDate"]
    assert short["totals"]["previousOrderCount"] == 0
    assert short["totals"]["currentOrderCount"] == 1


def test_exact_ids_bypass_small_sample_without_widening(
    api_client, readonly_key, comparison_scope
):
    scope = comparison_scope
    exact = get(api_client, readonly_key, scope, spu_ids=scope["products"][4]).json()
    assert exact["total"] == 1
    assert exact["items"][0]["spuPk"] == scope["products"][4]
    assert exact["meta"]["activityBypassed"] is True
    assert exact["items"][0]["currentSpendCny"] == "0"
    assert exact["items"][0]["currentRoi"] is None
    assert exact["items"][0]["currentRoiUnavailableReason"]
    assert get(api_client, readonly_key, scope, spu_ids=999999999).status_code == 422
    assert get(api_client, readonly_key, scope, activity="all").json()["total"] == 5


@pytest.mark.parametrize(
    "query",
    [
        {"window_days": 1},
        {"window_days": [3, 7]},
        {"window_days": "all"},
        {"severity": "all"},
        {"layer": "all"},
        {"sample": "all"},
        {"state": "stable"},
        {"spu_ids": ""},
        {"spu_ids": 0},
        {"spu_ids": [1] * 101},
        {"activity": "bad"},
    ],
)
def test_deprecated_and_invalid_inputs_rejected(
    api_client, readonly_key, comparison_scope, query
):
    response = get(api_client, readonly_key, comparison_scope, **query)
    assert response.status_code == 422, response.text


def test_date_cap_historical_reads_and_auth(api_client, readonly_key, comparison_scope):
    scope = comparison_scope
    assert api_client.get(URL, params={"shop_pk": scope["shop"]}).status_code == 401
    later = get(
        api_client,
        readonly_key,
        scope,
        anchor_date=(scope["anchor"] + timedelta(days=1)).isoformat(),
    )
    assert later.status_code == 422
    assert later.json()["detail"]["latestAvailableDate"] == scope["anchor"].isoformat()
    assert later.json()["detail"]["requestId"] == "TEST_COMPARE_REQ"
    older = get(
        api_client,
        readonly_key,
        scope,
        anchor_date=(scope["anchor"] - timedelta(days=1)).isoformat(),
        activity="all",
    )
    assert older.status_code == 200, older.text
    assert (
        older.json()["meta"]["anchorDate"]
        == (scope["anchor"] - timedelta(days=1)).isoformat()
    )


@pytest.mark.parametrize("anchor", ["0001-01-01", "0001-01-06"])
@pytest.mark.parametrize("window_days", [3, 7])
def test_calendar_date_without_supported_comparison_span_is_422(
    api_client, readonly_key, comparison_scope, anchor, window_days
):
    response = get(
        api_client,
        readonly_key,
        comparison_scope,
        anchor_date=anchor,
        window_days=window_days,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["requestId"] == "TEST_COMPARE_REQ"


def test_availability_history_reads_are_bounded_and_do_not_load_raw_payloads(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        for n in range(30):
            for name in ("tiktok.orders", "tiktok.after_sales", "tiktok.finance"):
                session.add(
                    SyncJob(
                        job_name=name,
                        started_at=scope["started"] - timedelta(days=n + 2),
                        finished_at=scope["started"]
                        - timedelta(days=n + 2)
                        + timedelta(hours=1),
                        status="succeeded",
                        rows_failed=0,
                        extra={"shop_id": "TEST_COMPARE_SHOP"},
                    )
                )
            session.add(
                AdRawLog(
                    seller_id="TEST_COMPARE_SHOP",
                    advertiser_id="TEST_ADV",
                    campaign_id="TEST_CAMPAIGN",
                    endpoint=ENDPOINT,
                    kind="daily",
                    day=scope["anchor"] - timedelta(days=n),
                    request_url="https://example.invalid",
                    request_method="POST",
                    response_status=500,
                    response_body={"body": {"code": 4, "payload": "TEST_" * 1000}},
                )
            )
        session.commit()
    loaded = []
    statements = []

    def record_load(session, instance):
        if isinstance(instance, (SyncJob, AdRawLog)):
            loaded.append(instance)

    def record_query(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, context.execution_options))

    event.listen(Session, "loaded_as_persistent", record_load)
    event.listen(db_engine, "before_cursor_execute", record_query)
    try:
        response = get(api_client, readonly_key, scope, limit=1)
    finally:
        event.remove(Session, "loaded_as_persistent", record_load)
        event.remove(db_engine, "before_cursor_execute", record_query)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["latestAvailableDate"] == scope["anchor"].isoformat()
    assert len([row for row in loaded if isinstance(row, SyncJob)]) <= 3
    assert not any(isinstance(row, AdRawLog) for row in loaded)
    source_reads = [sql for sql, _ in statements if "FROM integration.sync_jobs" in sql]
    assert len(source_reads) == 6
    assert all("LIMIT" in sql for sql in source_reads)
    raw_reads = [
        (sql, options) for sql, options in statements if "FROM plugin.ad_raw_log" in sql
    ]
    assert len(raw_reads) == 1
    sql, options = raw_reads[0]
    assert "response_body" not in sql.split("FROM", 1)[0]
    assert "response_status" in sql.split("WHERE", 1)[1]
    assert options.get("yield_per") == 500


def test_missing_source_is_unavailable_not_success_empty(
    api_client, readonly_key, comparison_scope, db_engine
):
    with Session(db_engine) as session:
        session.execute(
            delete(SyncJob).where(
                SyncJob.job_name == "tiktok.finance",
                SyncJob.extra["shop_id"].astext == "TEST_COMPARE_SHOP",
            )
        )
        session.commit()
    response = get(api_client, readonly_key, comparison_scope)
    assert response.status_code == 503
    assert response.json()["detail"]["requestId"] == "TEST_COMPARE_REQ"


def test_late_failure_retains_old_date_with_warning(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.add(
            SyncJob(
                job_name="tiktok.orders",
                started_at=scope["started"] + timedelta(days=1),
                finished_at=scope["started"] + timedelta(days=1, hours=1),
                status="failed",
                rows_failed=1,
                extra={"shop_id": "TEST_COMPARE_SHOP"},
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["latestAvailableDate"] == scope["anchor"].isoformat()
    assert any("tiktok.orders" in w for w in response.json()["meta"]["warnings"])


def test_known_missing_lines_do_not_become_zero_or_fact_flag(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.add(
            SalesOrder(
                shop_pk=scope["shop"],
                order_id="TEST_COMPARE_MISSING_LINES",
                status="DELIVERED",
                order_time=datetime.combine(scope["anchor"], time(12), TZ),
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope, spu_ids=scope["products"][3])
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["currentOrderCount"] is None
    assert item["currentNoOrderAdSpend"] is None
    assert item["currentRoi"] is None
    assert item["currentNetProfitCny"] is None
    assert Decimal(item["currentSpendCny"]) == 49
    assert response.json()["totals"]["currentOrderCount"] is None
    assert get(api_client, readonly_key, scope).status_code == 503


def test_ad_coverage_holes_are_unknown_not_zero(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.execute(
            delete(AdDaily).where(
                AdDaily.seller_id == "TEST_COMPARE_SHOP",
                AdDaily.day == scope["anchor"] - timedelta(days=1),
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope, activity="all")
    assert response.status_code == 200, response.text
    item = next(
        i for i in response.json()["items"] if i["spuPk"] == scope["products"][3]
    )
    assert item["currentSpendCny"] is None
    assert item["currentRoi"] is None
    assert item["currentNoOrderAdSpend"] is None
    assert item["previousSpendCny"] is not None


@pytest.mark.parametrize("days_old", [0, 3650])
def test_successful_empty_ads_and_local_start_cutoff(
    api_client, readonly_key, comparison_scope, db_engine, days_old
):
    scope = {
        **comparison_scope,
        "anchor": comparison_scope["anchor"] - timedelta(days=days_old),
    }
    with Session(db_engine) as session:
        session.execute(delete(AdDaily).where(AdDaily.seller_id == "TEST_COMPARE_SHOP"))
        for ago in range(14):
            session.add(
                AdRawLog(
                    seller_id="TEST_COMPARE_SHOP",
                    advertiser_id="TEST_ADV",
                    campaign_id="TEST_CAMPAIGN",
                    endpoint=ENDPOINT,
                    kind="daily",
                    day=scope["anchor"] - timedelta(days=ago),
                    request_url="https://example.invalid",
                    request_method="POST",
                    response_status=200,
                    response_body={
                        "status": 200,
                        "body": {"code": 0, "data": {"table": []}},
                    },
                )
            )
        session.commit()
    response = get(api_client, readonly_key, scope, activity="all")
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["latestAvailableDate"] == scope["anchor"].isoformat()
    assert all(i["currentSpendCny"] == "0" for i in response.json()["items"])
    assert all(i["currentNoOrderAdSpend"] is False for i in response.json()["items"])
    # Start is 00:05 shop time, previous UTC calendar day: do not use UTC date or finish date.
    assert scope["started"].astimezone(UTC).date() == comparison_scope["anchor"]
    assert (
        response.json()["meta"]["coverage"]["sources"]["tiktok.orders"][
            "operationalCutoff"
        ]
        == comparison_scope["anchor"].isoformat()
    )


def test_negative_canonical_roi_is_not_hidden(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        order = session.scalar(
            select(SalesOrder).where(SalesOrder.order_id == "TEST_COMPARE_ORDER_2")
        )
        assert order is not None
        line = session.scalar(
            select(SalesOrderLine).where(
                SalesOrderLine.order_pk == order.id,
                SalesOrderLine.spu_pk == scope["products"][0],
            )
        )
        assert line is not None
        case = Case(
            shop_pk=scope["shop"],
            order_pk=order.id,
            external_case_id="TEST_COMPARE_REFUND",
            case_type="RETURN_AND_REFUND",
            status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        )
        session.add(case)
        session.flush()
        session.add(
            CaseLine(
                case_id=case.id,
                sales_order_line_id=line.id,
                external_case_line_id="TEST_COMPARE_REFUND_LINE",
                quantity=1,
                refund_amount=100000,
                currency="VND",
                should_replenish_stock=False,
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope, spu_ids=scope["products"][0])
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert Decimal(item["currentRoi"]) < 0
    assert item["currentRoiUnavailableReason"] is None
    assert item["currentNoOrderAdSpend"] is False


def test_null_ad_cost_is_unknown_not_zero(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    with Session(db_engine) as session:
        row = session.scalar(
            select(AdDaily).where(
                AdDaily.product_id == "TEST_COMPARE_3", AdDaily.day == scope["anchor"]
            )
        )
        assert row is not None
        row.mixed_real_cost = None
        session.commit()
    response = get(api_client, readonly_key, scope, activity="all")
    assert response.status_code == 200, response.text
    item = next(
        i for i in response.json()["items"] if i["spuPk"] == scope["products"][3]
    )
    assert item["currentSpendCny"] is None
    assert item["currentNoOrderAdSpend"] is None
    assert item["currentNetProfitCny"] is None
    # Missing cost affects this product, not another SPU with known ad facts.
    known = next(
        i for i in response.json()["items"] if i["spuPk"] == scope["products"][0]
    )
    assert Decimal(known["currentSpendCny"]) == 49
    assert get(api_client, readonly_key, scope).status_code == 503


@pytest.mark.parametrize(
    "name", ["tiktok.orders", "tiktok.after_sales", "tiktok.finance"]
)
def test_unresolved_parse_failures_do_not_masquerade_as_complete_financial_facts(
    api_client, readonly_key, comparison_scope, db_engine, name
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.add(
            SyncIssue(
                job_name=name,
                issue_type="PARSE_ERROR",
                external_id="TEST_COMPARE_ISSUE",
                details={"shop_id": "TEST_COMPARE_SHOP"},
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope, spu_ids=scope["products"][3])
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["currentRoi"] is None
    assert item["currentNetProfitCny"] is None
    assert item["currentRoiUnavailableReason"]
    assert Decimal(item["currentSpendCny"]) == 49
    if name == "tiktok.orders":
        assert item["currentOrderCount"] is None
        assert item["currentNoOrderAdSpend"] is None
    else:
        assert item["currentOrderCount"] == 0
        assert item["currentNoOrderAdSpend"] is True


@pytest.mark.parametrize("missing_fact", ["date", "spu_pk", "quantity", "unit_price"])
def test_missing_header_or_line_facts_remain_unknown_with_exists_checks(
    api_client, readonly_key, comparison_scope, db_engine, missing_fact
):
    scope = comparison_scope
    with Session(db_engine) as session:
        order = session.scalar(
            select(SalesOrder).where(SalesOrder.order_id == "TEST_COMPARE_ORDER_2")
        )
        assert order is not None
        if missing_fact == "date":
            order.order_time = None
            order.paid_at = None
        else:
            line = session.scalar(
                select(SalesOrderLine)
                .where(SalesOrderLine.order_pk == order.id)
                .limit(1)
            )
            assert line is not None
            setattr(line, missing_fact, None)
        session.commit()
    response = get(api_client, readonly_key, scope, spu_ids=scope["products"][3])
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["currentOrderCount"] is None
    assert item["currentNoOrderAdSpend"] is None
    assert item["currentRoi"] is None
    assert get(api_client, readonly_key, scope).status_code == 503


@pytest.mark.parametrize("details", [{}, None, ["TEST_BAD"], "TEST_BAD"])
def test_unattributed_or_malformed_issue_details_are_unknown_not_server_error(
    api_client, readonly_key, comparison_scope, db_engine, details
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.add(
            SyncIssue(
                job_name="tiktok.orders",
                issue_type="PARSE_ERROR",
                external_id="TEST_COMPARE_BAD_DETAILS",
                details=details,
            )
        )
        session.commit()
    response = get(api_client, readonly_key, scope, spu_ids=scope["products"][3])
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["currentOrderCount"] is None
    assert item["currentNoOrderAdSpend"] is None
    assert item["warnings"]


def test_global_warnings_are_not_repeated_in_every_row(
    api_client, readonly_key, comparison_scope
):
    body = get(api_client, readonly_key, comparison_scope).json()
    assert body["meta"]["warnings"]
    assert all(item["warnings"] == [] for item in body["items"])


def test_order_and_issue_checks_do_not_scale_queries_or_load_lifetime_entities(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    queries = []
    loaded = []

    def record_query(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            queries.append(statement)

    def record_load(session, instance):
        if isinstance(instance, (SalesOrder, SalesOrderLine, SyncIssue)):
            loaded.append(instance)

    event.listen(db_engine, "before_cursor_execute", record_query)
    try:
        baseline = get(api_client, readonly_key, scope, limit=1)
        assert baseline.status_code == 200, baseline.text
        baseline_count = len(queries)
    finally:
        event.remove(db_engine, "before_cursor_execute", record_query)
    with Session(db_engine) as session:
        for n in range(30):
            order = SalesOrder(
                shop_pk=scope["shop"],
                order_id=f"TEST_COMPARE_HISTORY_{n}",
                status="DELIVERED",
                order_time=datetime.combine(
                    scope["anchor"] - timedelta(days=60 + n), time(12), TZ
                ),
            )
            session.add(order)
            session.flush()
            session.add(
                SalesOrderLine(
                    order_pk=order.id,
                    spu_pk=scope["products"][0],
                    external_line_id=f"TEST_COMPARE_HISTORY_LINE_{n}",
                    quantity=1,
                    unit_price=100000,
                    currency="VND",
                )
            )
            session.add(
                SyncIssue(
                    job_name="tiktok.orders",
                    issue_type="PARSE_ERROR",
                    external_id=f"TEST_COMPARE_OTHER_ISSUE_{n}",
                    details={"shop_id": "TEST_OTHER_SHOP", "payload": "TEST_" * 1000},
                )
            )
        session.add(
            SyncIssue(
                job_name="tiktok.orders",
                issue_type="STALE_OBSERVATION_SKIPPED",
                external_id="TEST_COMPARE_STALE_ISSUE",
                details={"shop_id": "TEST_COMPARE_SHOP"},
            )
        )
        session.add(
            SyncIssue(
                job_name="tiktok.orders",
                issue_type="PARSE_ERROR",
                external_id="TEST_COMPARE_RESOLVED_ISSUE",
                details={"shop_id": "TEST_COMPARE_SHOP"},
                resolved_at=datetime.now(UTC),
            )
        )
        session.commit()
    queries.clear()
    event.listen(db_engine, "before_cursor_execute", record_query)
    event.listen(Session, "loaded_as_persistent", record_load)
    try:
        response = get(api_client, readonly_key, scope, limit=1)
    finally:
        event.remove(db_engine, "before_cursor_execute", record_query)
        event.remove(Session, "loaded_as_persistent", record_load)
    assert response.status_code == 200, response.text
    assert response.json()["totals"] == baseline.json()["totals"]
    assert len(queries) <= baseline_count + 2
    assert loaded == []
    issue_reads = [sql for sql in queries if "FROM integration.sync_issues" in sql]
    assert issue_reads and all("LIMIT" in sql for sql in issue_reads)
    assert all("details" not in sql.split("FROM", 1)[0] for sql in issue_reads)


def test_distinct_order_total_across_canonical_batches(
    api_client, readonly_key, comparison_scope, db_engine
):
    scope = comparison_scope
    queries = []

    def record_query(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", record_query)
    try:
        baseline = get(api_client, readonly_key, scope, activity="all", limit=1)
        assert baseline.status_code == 200, baseline.text
        baseline_count = len(queries)
    finally:
        event.remove(db_engine, "before_cursor_execute", record_query)
    with Session(db_engine) as session:
        shared = session.scalar(
            select(SalesOrder).where(SalesOrder.order_id == "TEST_COMPARE_ORDER_2")
        )
        assert shared is not None
        for n in range(101):
            product = ChannelProduct(
                shop_pk=scope["shop"],
                spu_id=f"TEST_COMPARE_BATCH_{n}",
                status="DEACTIVATE",
            )
            session.add(product)
            session.flush()
            session.add(
                SalesOrderLine(
                    order_pk=shared.id,
                    spu_pk=product.id,
                    external_line_id=f"TEST_COMPARE_BATCH_LINE_{n}",
                    quantity=1,
                    unit_price=100000,
                    currency="VND",
                )
            )
        session.commit()
    queries.clear()
    event.listen(db_engine, "before_cursor_execute", record_query)
    try:
        response = get(
            api_client, readonly_key, scope, activity="all", offset=100, limit=1
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", record_query)
    assert response.status_code == 200, response.text
    # Full-scope SQL counts remain independent; expensive canonical reads only
    # calculate this one requested page, not every 100-SPU batch in the shop.
    assert len(queries) <= baseline_count + 2
    body = response.json()
    assert body["total"] == 106
    assert body["totals"]["analysisSpuCount"] == 106
    assert body["totals"]["currentOrderCount"] == 2  # shared + small
    assert body["totals"]["previousOrderCount"] == 1
    assert len(body["items"]) == 1


def test_all_reads_share_readonly_snapshot_and_calculation_time(
    comparison_scope, db_engine
):
    from tts_erp_v2.analytics.spu_deterioration_alert.availability import (
        resolve_availability,
    )
    from tts_erp_v2.analytics.spu_deterioration_alert.comparison import read_comparison

    observed = []

    def resolver(session, **kwargs):
        assert (
            session.execute(text("SHOW transaction_isolation")).scalar_one()
            == "repeatable read"
        )
        assert session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        observed.append(kwargs["now"].isoformat())
        return resolve_availability(session, **kwargs)

    with Session(db_engine) as session:
        body = read_comparison(
            session, shop_pk=comparison_scope["shop"], availability_resolver=resolver
        )
    assert observed == [body["meta"]["calculatedAt"].isoformat()]
    assert all(
        item["basisCalculatedAt"] == body["meta"]["calculatedAt"]
        for item in body["items"]
    )


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"status": 200, "body": {"code": 0, "data": {}}},
        {"status": 200, "body": {"code": 4, "data": {"table": []}}},
    ],
)
def test_malformed_or_failed_empty_ads_are_not_availability_evidence(
    api_client, readonly_key, comparison_scope, db_engine, body
):
    scope = comparison_scope
    with Session(db_engine) as session:
        session.execute(delete(AdDaily).where(AdDaily.seller_id == "TEST_COMPARE_SHOP"))
        session.add(
            AdRawLog(
                seller_id="TEST_COMPARE_SHOP",
                advertiser_id="TEST_ADV",
                campaign_id="TEST_CAMPAIGN",
                endpoint=ENDPOINT,
                kind="daily",
                day=scope["anchor"],
                request_url="https://example.invalid",
                request_method="POST",
                response_status=200,
                response_body=body,
            )
        )
        session.commit()
    assert get(api_client, readonly_key, scope, activity="all").status_code == 503
