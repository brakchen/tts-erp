"""Differential contracts and an opt-in synthetic benchmark for ROI SQL scope.

Only scripts/test_isolated.sh may execute this module. The frozen SQL fixture
records the pre-change commit; both versions read the same ephemeral database.
Timing is evidence, not a flaky pass/fail threshold. To include the benchmark:

    bash scripts/test_isolated.sh api tests/api/test_spu_roi_query_scope.py \
        -m 'domain_api and not domain_migration' -s
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from tests.api import test_spu_price_stats as prices
from tests.api import test_spu_roi_api as seeds
from tts_erp_v2.analytics.spu_profitability import _implementation as impl
from tts_erp_v2.analytics.spu_profitability import _price_stats as price
from tts_erp_v2.analytics.spu_profitability import _snapshot
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_START = datetime(2026, 8, 31, 17, tzinfo=UTC)
_END = datetime(2026, 9, 1, 17, tzinfo=UTC)


@pytest.fixture(scope="module")
def baseline_sql() -> dict[str, str]:
    path = Path(__file__).parents[1] / "fixtures" / "spu_roi_query_scope_baseline.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert set(value) == {"source_commit", "sales", "price_coverage", "price_buckets"}
    assert len(value["source_commit"]) == 40
    return value


def _wipe(db_engine) -> None:
    # Observations reference orders; settlements reference lines. Remove the
    # former before invoking the existing ROI and price fixture cleanup owners.
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM commerce.sales_order_line_price_observations "
                "WHERE shop_pk IN (SELECT id FROM commerce.shops "
                "WHERE shop_id LIKE 'TEST_%')"
            )
        )
    seeds._wipe(db_engine)
    prices._wipe(db_engine)


@pytest.fixture(autouse=True)
def _state(db_engine, _isolate_state, monkeypatch):
    _wipe(db_engine)
    seeds._seed_fx(db_engine)
    monkeypatch.setattr(_snapshot, "datetime", seeds._FrozenDateTime)
    try:
        yield
    finally:
        _wipe(db_engine)
        seeds._del_fx(db_engine)


def _params(shop: int, pks: list[int], start=_START, end=_END) -> dict[str, Any]:
    return {
        "shop_pk": shop,
        "selected_pks": pks,
        "ws": start,
        "we": end,
        "paid_statuses": list(PAID_SALES_ORDER_STATUSES),
        "st0": "CANCELLATION_REQUEST_COMPLETE",
        "st1": "RETURN_OR_REFUND_REQUEST_COMPLETE",
    }


def _old_price_rows(session, params, baseline):
    return (
        session.execute(text(baseline["price_coverage"]), params).mappings().all(),
        session.execute(text(baseline["price_buckets"]), params).mappings().all(),
    )


def _assert_same_rows(actual, expected) -> None:
    """Ignore SQL order and the union's discriminator/other-kind NULL columns."""
    assert len(actual) == len(expected)
    if not expected:
        return
    keys = tuple(expected[0].keys())
    assert Counter(tuple(row[key] for key in keys) for row in actual) == Counter(
        tuple(row[key] for key in keys) for row in expected
    )


@pytest.fixture
def sales_scope(db_engine) -> tuple[int, int, int]:
    with Session(db_engine) as session:
        shop = seeds._seed_shop(session, "TEST_SCOPE_SALES_SHOP")
        x = seeds._seed_spu(session, shop, "TEST_SCOPE_SALES_X")
        y = seeds._seed_spu(session, shop, "TEST_SCOPE_SALES_Y")

        def order(
            label, amount="100", status="DELIVERED", at="2026-09-01T08:00:00+00:00"
        ):
            return seeds._seed_order_line(
                session,
                shop_pk=shop,
                spu_pk=x,
                order_id=f"TEST_SCOPE_SALES_{label}",
                status=status,
                line_ext=f"TEST_SCOPE_SALES_LINE_{label}",
                qty="1",
                unit_price=amount,
                paid=status not in {"UNPAID", "ON_HOLD"},
                order_iso=at,
            )

        shared = order("SHARED")
        for pk, suffix, qty, amount in ((x, "X2", "2", "50"), (y, "Y", "1", "200")):
            seeds._seed_extra_order_line(
                session,
                order_id="TEST_SCOPE_SALES_SHARED",
                spu_pk=pk,
                line_ext=f"TEST_SCOPE_SALES_{suffix}",
                qty=qty,
                unit_price=amount,
            )
        session.execute(
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
                "VALUES (:order, 'TEST_SCOPE_SALES_UNMAPPED', NULL, 1, 100, 'VND')"
            ),
            {"order": shared},
        )
        for suffix, amount in (("FIRST", "150"), ("SECOND", "100")):
            seeds._seed_settlement(
                session,
                order_pk=shared,
                external_id=f"TEST_SCOPE_SALES_TX_{suffix}",
                amount_vnd=amount,
            )
        zero = order("ZERO", "60")
        seeds._seed_settlement(
            session,
            order_pk=zero,
            external_id="TEST_SCOPE_SALES_TX_ZERO",
            amount_vnd="0",
        )
        unpaid_settlement = order("UNPAID", status="UNPAID")
        seeds._seed_settlement(
            session,
            order_pk=unpaid_settlement,
            external_id="TEST_SCOPE_SALES_TX_UNPAID",
            amount_vnd="999",
        )
        pending = order("REFUND")
        line = seeds._fetch_spu_line_id(session, "TEST_SCOPE_SALES_REFUND")
        seeds._seed_case(
            session,
            shop_pk=shop,
            order_pk=pending,
            ext_case="TEST_SCOPE_SALES_CASE",
            case_type="REFUND_ONLY",
            status="RETURN_OR_REFUND_REQUEST_COMPLETE",
            lines=[(line, "TEST_SCOPE_SALES_CASE_LINE", "1", "30")],
            updated_iso="2026-10-01T00:00:00+00:00",
        )
        fallback = order("FALLBACK", "70")
        undated = order("UNDATED", "90")
        session.execute(
            text(
                "UPDATE commerce.sales_orders SET order_time=NULL "
                "WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"ids": [fallback, undated]},
        )
        session.execute(
            text("UPDATE commerce.sales_orders SET paid_at=NULL WHERE id=:id"),
            {"id": undated},
        )
        order("BEFORE", "40", at="2026-08-31T16:59:59+00:00")
        order("AT_END", "50", at="2026-09-01T17:00:00+00:00")
        order("CANCELLED", status="CANCELLED")
        order("ON_HOLD", status="ON_HOLD")
        session.commit()
    return shop, x, y


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (None, None),
        (_START, None),
        (None, _END),
        (_START, _END),
        (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)),
    ],
    ids=["unbounded", "start-only", "end-only", "one-day", "empty"],
)
def test_sales_scope_matches_original_sql(
    db_engine, baseline_sql, sales_scope, start, end
):
    shop, x, _ = sales_scope
    params = _params(shop, [x], start, end)
    with Session(db_engine) as session:
        before = session.execute(text(baseline_sql["sales"]), params).mappings().all()
        after = session.execute(impl._SQL_ROI_SALES, params).mappings().all()
    _assert_same_rows(after, before)
    if (start, end) == (_START, _END):
        assert len(after) == 1
        row = after[0]
        assert row["order_count"] == 4
        assert row["units_sold"] == 6
        assert row["sales_vnd"] == Decimal(430)
        # X=200 / entire order=500, settlement=250. Zero is still settled.
        assert row["settled_net_vnd"] == Decimal(100)
        assert row["settled_order_count"] == 2
        assert row["confirmed_unsettled_refund_vnd"] == Decimal(30)


@pytest.fixture
def price_scope(db_engine) -> tuple[int, int, int]:
    with Session(db_engine) as session:
        shop = prices._seed_shop(session, "TEST_SCOPE_PRICE_SHOP")
        x = prices._seed_spu(session, shop, "TEST_SCOPE_PRICE_X")
        y = prices._seed_spu(session, shop, "TEST_SCOPE_PRICE_Y")
        prices._seed_manual_cost(session, x, "10")

        def line(label, *, status="DELIVERED", at=prices.ORDER_TS, spu=None, ext=None):
            order = prices._seed_order(
                session,
                shop,
                order_id=f"TEST_SCOPE_PRICE_{label}",
                status=status,
                order_iso=at,
            )
            item = prices._seed_line(
                session,
                order,
                x if spu is None else spu,
                line_ext=ext or f"TEST_SCOPE_PRICE_LINE_{label}",
                qty="2",
                unit_price="10",
            )
            return order, item

        def observation(order, item, label, **kwargs):
            return prices._seed_observation(
                session,
                shop=shop,
                order=order,
                line_ext=item.external_line_id,
                semantic=f"TEST_SCOPE_PRICE_SEM_{label}",
                qty="2",
                **kwargs,
            )

        order, item = line("CANONICAL")
        # The current line supplies SPU attribution. Its historical observation
        # can retain an old/NULL mapping, and capture dates are not order dates.
        observation(order, item, "OLD", spu=x, paid="99")
        observation(
            order,
            item,
            "LATE",
            spu=None,
            paid=None,
            paid_status="MISSING",
            version="2026-10-01T00:00:00+00:00",
            captured="2026-10-01T01:00:00+00:00",
        )
        undated = observation(
            order,
            item,
            "NULL_VERSION",
            spu=x,
            paid="123",
            captured="2026-11-01T01:00:00+00:00",
        )
        undated.source_order_version_at = None
        other = prices._seed_line(
            session, order, y, line_ext="TEST_SCOPE_PRICE_OTHER", qty="8"
        )
        observation(order, other, "OTHER", spu=y, paid="999")
        order, item = line("TIE")
        # Equal source version: latest capture wins, then smallest semantic hash.
        observation(order, item, "TIE_OLD_CAPTURE", spu=x, paid="77")
        observation(
            order, item, "TIE_Z", spu=x, paid="88", captured="2026-09-02T01:00:00+00:00"
        )
        observation(
            order, item, "TIE_A", spu=y, paid="7", captured="2026-09-02T01:00:00+00:00"
        )
        order, item = line("REUSED", ext="TEST_SCOPE_PRICE_LINE_TIE")
        observation(order, item, "REUSED", spu=None, paid="3")
        line("MISSING_OBSERVATION")
        order, item = line("NULL_PRICE")
        observation(order, item, "NULL_PRICE", spu=x, paid=None, paid_status="OBSERVED")
        for label, status in (
            ("UNPAID", "UNPAID"),
            ("HOLD", "ON_HOLD"),
            ("CANCELLED", "CANCELLED"),
            ("UNKNOWN", "FUTURE_STATUS"),
        ):
            order, item = line(label, status=status)
            observation(order, item, label, spu=x, parent_payment_status=status)
        order, item = line("HISTORY", at="2026-08-01T08:00:00+00:00")
        observation(order, item, "HISTORY", spu=x, paid="1000")
        order, item = line("UNSELECTED", spu=y)
        observation(order, item, "UNSELECTED", spu=y, paid="2000")
        foreign = prices._seed_shop(session, "TEST_SCOPE_PRICE_FOREIGN")
        foreign_spu = prices._seed_spu(session, foreign, "TEST_SCOPE_PRICE_FOREIGN_SPU")
        foreign_order = prices._seed_order(
            session,
            foreign,
            order_id="TEST_SCOPE_PRICE_FOREIGN_ORDER",
            status="DELIVERED",
        )
        foreign_line = prices._seed_line(
            session,
            foreign_order,
            foreign_spu,
            line_ext="TEST_SCOPE_PRICE_LINE_TIE",
        )
        prices._seed_observation(
            session,
            shop=foreign,
            order=foreign_order,
            line_ext=foreign_line.external_line_id,
            spu=foreign_spu,
            paid="9000",
        )
        session.commit()
        return shop.id, x.id, y.id


@pytest.mark.parametrize("selection", ["one", "both", "empty"])
def test_price_scope_matches_original_rows(
    db_engine, baseline_sql, price_scope, selection
):
    shop, x, y = price_scope
    pks = {"one": [x], "both": [x, y], "empty": []}[selection]
    params = _params(shop, pks)
    with Session(db_engine) as session:
        expected = _old_price_rows(session, params, baseline_sql)
        actual = price._read_price_rows(session, params)
    for after, before in zip(actual, expected, strict=True):
        _assert_same_rows(after, before)
    if selection == "one":
        coverage = next(row for row in actual[0] if row["spu_pk"] == x)
        assert coverage["eligible_quantity"] == 8
        assert coverage["missing_observation_line_count"] == 1
        assert coverage["excluded_unpaid_quantity"] == 2
        assert coverage["excluded_on_hold_quantity"] == 2
        assert coverage["excluded_cancelled_quantity"] == 2
        assert coverage["unknown_status_quantity"] == 2
        paid = [
            row for row in actual[1] if row["spu_pk"] == x and row["metric"] == "paid"
        ]
        assert {
            (row["klass"], row["native_price"], row["quantity"]) for row in paid
        } == {
            ("OBSERVED", Decimal(7), 2),
            ("OBSERVED", Decimal(3), 2),
            ("MISSING", None, 2),
            ("INVALID", None, 2),
        }


@contextmanager
def _statements(engine) -> Iterator[list[str]]:
    calls: list[str] = []

    def record(_conn, _cursor, statement, _params, context, _many):
        compiled = context.compiled
        calls.append(
            getattr(compiled.statement, "text", statement) if compiled else statement
        )

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield calls
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_full_api_payload_unchanged_and_price_population_read_once(
    api_client,
    readonly_key,
    db_engine,
    monkeypatch,
    baseline_sql,
    price_scope,
    sales_scope,
):
    def fetch(shop, spu_id):
        response = api_client.get(
            "/v2/analytics/spu-roi",
            headers={"Authorization": f"Bearer {readonly_key}"},
            params={
                "shop_pk": shop,
                "spu_ids": spu_id,
                "w_start": "2026-09-01",
                "w_end": "2026-09-01",
                "include_all": "true",
                "sort": "paidPriceMean",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    for shop, spu_id in (
        (price_scope[0], "TEST_SCOPE_PRICE_X"),
        (sales_scope[0], "TEST_SCOPE_SALES_X"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(impl, "_SQL_ROI_SALES", text(baseline_sql["sales"]))
            patch.setattr(
                price,
                "_read_price_rows",
                lambda session, params: _old_price_rows(session, params, baseline_sql),
            )
            before = fetch(shop, spu_id)
        with _statements(db_engine) as calls:
            after = fetch(shop, spu_id)
        assert after == before
        assert calls.count(price._SQL_PRICE_STATS.text) == 1
        assert calls.count(price._SQL_PRICE_OBSERVATION_TABLE_EXISTS.text) == 1
        assert calls.count(impl._SQL_ROI_SALES.text) == 1
        assert baseline_sql["price_coverage"] not in calls
        assert baseline_sql["price_buckets"] not in calls


def _seed_benchmark(session: Session) -> tuple[int, list[int]]:
    """10k historical + 200 current orders, 3 versions/line, no production copy."""
    shop = prices._seed_shop(session, "TEST_SCOPE_BENCH_SHOP")
    pks = [
        prices._seed_spu(session, shop, f"TEST_SCOPE_BENCH_SPU_{n}").id
        for n in range(20)
    ]
    raw = prices._seed_raw_record(session, external_id="TEST_SCOPE_BENCH_RAW")
    session.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, order_time, paid_at) "
            "SELECT :shop, 'TEST_SCOPE_BENCH_ORDER_' || n, "
            "CASE WHEN n % 17 = 0 THEN 'CANCELLED' ELSE 'DELIVERED' END, 'VND', "
            "CASE WHEN n <= 10000 THEN '2025-01-01T08:00:00Z'::timestamptz "
            "ELSE '2026-09-01T08:00:00Z'::timestamptz END, "
            "'2026-09-01T08:00:00Z'::timestamptz FROM generate_series(1,10200) n"
        ),
        {"shop": shop.id},
    )
    session.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "SELECT o.id, 'TEST_SCOPE_BENCH_LINE_' || o.id, "
            "(CAST(:pks AS bigint[]))[1 + (substring(o.order_id from '[0-9]+$')::int % 20)], "
            "1 + (o.id % 3), 100000, 'VND' FROM commerce.sales_orders o WHERE o.shop_pk=:shop"
        ),
        {"shop": shop.id, "pks": pks},
    )
    session.execute(
        text(
            "INSERT INTO commerce.sales_order_line_price_observations "
            "(shop_pk, order_pk, external_line_id, raw_record_id, source_endpoint, "
            "source_payload_hash, semantic_observation_hash, source_order_version_at, "
            "source_captured_at, spu_pk, raw_quantity, effective_quantity, quantity_status, "
            "parent_payment_status, gift_status, original_price_native, paid_price_native, "
            "currency, original_price_status, paid_price_status) "
            "SELECT :shop, l.order_pk, l.external_line_id, :raw, 'ORDER_SEARCH', "
            "'TEST_SCOPE_PAYLOAD_' || l.id || '_' || version, "
            "'TEST_SCOPE_SEMANTIC_' || l.id || '_' || version, "
            "'2026-10-01T00:00:00Z'::timestamptz + version * interval '1 day', "
            "'2026-10-01T01:00:00Z'::timestamptz + version * interval '1 day', "
            "l.spu_pk, l.quantity, l.quantity, 'OBSERVED', o.status, 'NOT_GIFT', "
            "120000, 90000 + 1000 * version, 'VND', 'OBSERVED', 'OBSERVED' "
            "FROM commerce.sales_order_lines l JOIN commerce.sales_orders o ON o.id=l.order_pk "
            "CROSS JOIN generate_series(1,3) version WHERE o.shop_pk=:shop"
        ),
        {"shop": shop.id, "raw": raw.id},
    )
    statement = session.execute(
        text(
            "INSERT INTO finance.settlement_statements "
            "(external_statement_id, statement_time, currency) "
            "VALUES ('TEST_SCOPE_BENCH_STATEMENT','2026-10-01T00:00:00Z','VND') RETURNING id"
        )
    ).scalar_one()
    session.execute(
        text(
            "INSERT INTO finance.settlement_transactions "
            "(settlement_statement_id, external_transaction_id, order_pk, transaction_time) "
            "SELECT :statement, 'TEST_SCOPE_BENCH_TX_' || id, id, '2026-10-01T00:00:00Z' "
            "FROM commerce.sales_orders WHERE shop_pk=:shop AND id % 2=0"
        ),
        {"shop": shop.id, "statement": statement},
    )
    session.execute(
        text(
            "INSERT INTO finance.settlement_components (transaction_id, component_code, amount, currency) "
            "SELECT id, 'SETTLEMENT', 80000, 'VND' FROM finance.settlement_transactions "
            "WHERE settlement_statement_id=:statement"
        ),
        {"statement": statement},
    )
    session.commit()
    # Statistics cover only this run's isolated clone. Planner settings remain defaults.
    for relation in (
        "commerce.sales_orders",
        "commerce.sales_order_lines",
        "commerce.sales_order_line_price_observations",
        "finance.settlement_transactions",
        "finance.settlement_components",
        "after_sales.cases",
        "after_sales.case_lines",
    ):
        session.execute(text(f"ANALYZE {relation}"))
    session.commit()
    return shop.id, pks


def _plan(session, sql: str, params) -> dict[str, Any]:
    # SQL comes solely from reviewed static query constants/the committed fixture.
    result = session.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, TIMING OFF) " + sql), params
    ).scalar_one()
    return result[0]


@pytest.mark.slow
def test_synthetic_sql_scope_benchmark(db_engine, baseline_sql, tmp_path):
    report: dict[str, Any] = {
        "baseline_commit": baseline_sql["source_commit"],
        "population": {
            "historical_orders": 10000,
            "current_orders": 200,
            "spus": 20,
            "observation_versions_per_line": 3,
        },
        "scenarios": {},
    }
    with Session(db_engine) as session:
        shop, pks = _seed_benchmark(session)
        for name, selected, start, end in (
            ("one_spu_7_days", pks[:1], datetime(2026, 8, 25, 17, tzinfo=UTC), _END),
            ("whole_shop_30_days", pks, datetime(2026, 8, 2, 17, tzinfo=UTC), _END),
            ("whole_shop_unbounded", pks, None, None),
        ):
            params = _params(shop, selected, start, end)

            def sales(old, query_params=params):
                statement = text(baseline_sql["sales"]) if old else impl._SQL_ROI_SALES
                return session.execute(statement, query_params).mappings().all()

            def price_rows(old, query_params=params):
                return (
                    _old_price_rows(session, query_params, baseline_sql)
                    if old
                    else price._read_price_rows(session, query_params)
                )

            before_sales, after_sales = sales(True), sales(False)
            _assert_same_rows(after_sales, before_sales)
            before_prices, after_prices = price_rows(True), price_rows(False)
            for after, before in zip(after_prices, before_prices, strict=True):
                _assert_same_rows(after, before)
            timings: dict[str, list[float]] = {
                "sales_before": [],
                "sales_after": [],
                "prices_before": [],
                "prices_after": [],
            }
            # Equality calls above warm each query once. Reverse order every round.
            for repetition in range(5):
                for old in (True, False) if repetition % 2 == 0 else (False, True):
                    for label, run in (("sales", sales), ("prices", price_rows)):
                        started = perf_counter()
                        run(old)
                        key = label + ("_before" if old else "_after")
                        timings[key].append((perf_counter() - started) * 1000)
            plans = {
                "sales_before": _plan(session, baseline_sql["sales"], params),
                "sales_after": _plan(session, impl._SQL_ROI_SALES.text, params),
                "price_coverage_before": _plan(
                    session, baseline_sql["price_coverage"], params
                ),
                "price_buckets_before": _plan(
                    session, baseline_sql["price_buckets"], params
                ),
                "prices_after": _plan(session, price._SQL_PRICE_STATS.text, params),
            }
            medians = {key: round(median(values), 3) for key, values in timings.items()}
            report["scenarios"][name] = {
                "median_ms": medians,
                "samples_ms": timings,
                "plans": plans,
                "sales_rows": len(after_sales),
                "coverage_rows": len(after_prices[0]),
                "bucket_rows": len(after_prices[1]),
                "results_equal": True,
            }
            print(f"[roi-sql-benchmark] {name}: {json.dumps(medians, sort_keys=True)}")
    output = tmp_path / "spu-roi-query-scope-benchmark.json"
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[roi-sql-benchmark] report={output}")
