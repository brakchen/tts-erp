"""Query budgets and unchanged financial/detail contracts for SPU ROI reads.

These integration tests must run through scripts/test_isolated.sh. SQL listeners
surround HTTP reads only; all TEST_ fixtures are written to the ephemeral clone.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.api import test_spu_roi_api as seeds
from tts_erp_v2.analytics.spu_profitability import _implementation as impl
from tts_erp_v2.analytics.spu_profitability import _snapshot

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_FACT_QUERIES = (
    "_SQL_ROI_AD",
    "_SQL_ROI_SALES",
    "_SQL_ROI_FULL_LOSS",
    "_SQL_ROI_ROW_STATUS",
    "_SQL_ROI_REFUNDS",
    "_SQL_ROI_PROJECTION",
    "_SQL_ROI_PROJECTION_SCOPE_COUNTS",
    "_SQL_ROI_ORDER_SCOPE",
    "_SQL_ROI_REFUND_SCOPE",
)


@pytest.fixture(autouse=True)
def _roi_state(db_engine, _isolate_state, monkeypatch):
    seeds._wipe(db_engine)
    seeds._seed_fx(db_engine)
    monkeypatch.setattr(_snapshot, "datetime", seeds._FrozenDateTime)
    try:
        yield
    finally:
        seeds._wipe(db_engine)
        seeds._del_fx(db_engine)


@contextmanager
def _queries(engine: Engine) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    names = {getattr(impl, name).text: name for name in _FACT_QUERIES}
    calls: list[tuple[str, dict[str, Any]]] = []

    def record(_conn, _cursor, statement, _params, context, _executemany):
        compiled = context.compiled
        sql = getattr(compiled.statement, "text", None) if compiled else None
        parameters = getattr(context, "compiled_parameters", None)
        calls.append(
            (names.get(sql, statement), dict(parameters[0]) if parameters else {})
        )

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield calls
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
def selected_scope(db_engine):
    with Session(db_engine) as session:
        x, y = seeds._seed_cross_spu_orders(session)
        shop_pk = session.execute(
            text("SELECT shop_pk FROM commerce.products_spu WHERE id = :pk"),
            {"pk": x},
        ).scalar_one()
        historical = seeds._seed_spu(session, shop_pk, "TEST_ROI_SPU_HISTORICAL")
        seeds._seed_order_line(
            session,
            shop_pk=shop_pk,
            spu_pk=historical,
            order_id="TEST_ORDER_EFFICIENCY_HISTORICAL",
            status="DELIVERED",
            line_ext="TEST_LINE_EFFICIENCY_HISTORICAL",
            qty="1",
            unit_price="100000",
            paid=True,
            order_iso="2026-08-01T08:00:00+00:00",
        )
        session.commit()
    return shop_pk, (x, y, historical)


def _overview(api_client, key: str, shop_pk: int, **view: Any) -> dict[str, Any]:
    response = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {key}"},
        params={
            "shop_pk": shop_pk,
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
            "projection_lookback_days": 90,
            **view,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_scope(data: dict[str, Any], selected_pks: tuple[int, ...]) -> None:
    assert {item["spu_pk"] for item in data["items"]} == set(selected_pks[:2])
    assert sum(item["order_count"] for item in data["items"]) == 2
    totals = data["totals"]
    assert totals["order_count"] == 1
    assert totals["cancelled_order_count"] == 1
    assert totals["sales"] == seeds.cny4_from_usd("20")
    assert totals["gmv"] == seeds.cny4_from_usd("30")
    assert totals["projection_completed_basis_order_count"] == 3
    assert data["meta"]["projection"]["basis_order_count"] == 3


def test_overview_reads_each_fact_once_without_losing_historical_sample(
    api_client, readonly_key, db_engine, selected_scope
):
    shop_pk, selected_pks = selected_scope
    with _queries(db_engine) as calls:
        data = _overview(api_client, readonly_key, shop_pk)
    _assert_scope(data, selected_pks)
    counts = Counter(name for name, _ in calls if name in _FACT_QUERIES)
    assert counts == Counter({name: 1 for name in _FACT_QUERIES}), counts
    params = next(
        params for name, params in calls if name == "_SQL_ROI_PROJECTION_SCOPE_COUNTS"
    )
    assert set(params["selected_pks"]) == set(selected_pks)


def test_overview_calculates_current_once_per_selected_spu_and_one_dashboard(
    api_client, readonly_key, selected_scope, monkeypatch
):
    shop_pk, selected_pks = selected_scope
    current_inputs = []
    projection_inputs = []
    current = impl.calculate
    projection = impl.calculate_projection

    def record_current(inputs):
        current_inputs.append(inputs)
        return current(inputs)

    def record_projection(inputs):
        projection_inputs.append(inputs)
        return projection(inputs)

    monkeypatch.setattr(impl, "calculate", record_current)
    monkeypatch.setattr(impl, "calculate_projection", record_projection)
    data = _overview(api_client, readonly_key, shop_pk)
    _assert_scope(data, selected_pks)
    assert len(current_inputs) == len(selected_pks)
    # Two stable compatibility rows still need their forecasts, plus one dashboard.
    assert len(projection_inputs) == len(data["items"]) + 1


def test_overview_paging_and_search_keep_the_complete_totals(
    api_client, readonly_key, selected_scope
):
    shop_pk, selected_pks = selected_scope
    complete = _overview(api_client, readonly_key, shop_pk)
    _assert_scope(complete, selected_pks)
    for view in ({"limit": 1, "offset": 1}, {"q": "TEST_ROI_SPU_X"}):
        partial = _overview(api_client, readonly_key, shop_pk, **view)
        assert len(partial["items"]) == 1
        assert partial["totals"] == complete["totals"]
        assert partial["meta"] == complete["meta"]


def _seed_detail_order(session: Session, shop: int, x: int, y: int, index: int) -> None:
    order_id = f"TEST_ORDER_BATCH_{index}"
    order_pk = seeds._seed_order_line(
        session,
        shop_pk=shop,
        spu_pk=x,
        order_id=order_id,
        status="DELIVERED",
        line_ext=f"TEST_LINE_BATCH_{index}_X1",
        qty="1",
        unit_price="26330",
        paid=True,
    )
    for spu_pk, suffix, qty, price in ((x, "X2", "2", "26330"), (y, "Y", "1", "78990")):
        seeds._seed_extra_order_line(
            session,
            order_id=order_id,
            spu_pk=spu_pk,
            line_ext=f"TEST_LINE_BATCH_{index}_{suffix}",
            qty=qty,
            unit_price=price,
        )
    session.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "VALUES (:order_pk, :line_id, NULL, 1, 105320, 'VND')"
        ),
        {"order_pk": order_pk, "line_id": f"TEST_LINE_BATCH_{index}_UNKNOWN"},
    )
    for suffix, amount, refund in (
        ("FIRST", "131650", "-26330"),
        ("SECOND", "-26330", "0"),
    ):
        seeds._seed_settlement(
            session,
            order_pk=order_pk,
            external_id=f"TEST_TXN_BATCH_{index}_{suffix}",
            amount_vnd=amount,
            customer_refund_vnd=refund,
        )
    for package in range(2):
        shipment_pk = session.execute(
            text(
                "INSERT INTO fulfillment.shipments "
                "(order_pk, external_package_id, tracking_number, provider_name, status) "
                "VALUES (:order_pk, :package, :tracking, 'TEST', 'in_transit') RETURNING id"
            ),
            {
                "order_pk": order_pk,
                "package": f"TEST_PACKAGE_BATCH_{index}_{package}",
                "tracking": f"TEST_TRACKING_BATCH_{index}_{package}",
            },
        ).scalar_one()
        events = (
            [
                ("late", "2026-09-02T10:00:00+00:00", 38301),
                ("early_first", "2026-09-02T09:00:00+00:00", 10001),
                ("early_second", "2026-09-02T09:00:00+00:00", 10002),
                ("undated", None, 10003),
            ]
            if package == 0
            else [("other_package", "2026-09-02T08:00:00+00:00", 10001)]
        )
        for label, occurred_at, code in events:
            session.execute(
                text(
                    "INSERT INTO fulfillment.tracking_events "
                    "(shipment_id, external_event_key, action_code, event_at, description) "
                    "VALUES (:shipment, :key, :code, CAST(:at AS timestamptz), :description)"
                ),
                {
                    "shipment": shipment_pk,
                    "key": f"TEST_EVENT_BATCH_{index}_{label}",
                    "code": code,
                    "at": occurred_at,
                    "description": f"TEST_{label}",
                },
            )


def _assert_orders(data: dict[str, Any], count: int) -> None:
    rows = data["orders"]
    assert [row["order_id"] for row in rows] == [
        f"TEST_ORDER_BATCH_{index}"
        for index in reversed(range(count))
        for _ in range(4)
    ]
    assert data["meta"]["orders_truncated"] is False
    for index in range(count):
        order_rows = [
            row for row in rows if row["order_id"] == f"TEST_ORDER_BATCH_{index}"
        ]
        assert Counter(
            (row["qty"], row["shipment"]["tracking_number"]) for row in order_rows
        ) == Counter(
            (qty, f"TEST_TRACKING_BATCH_{index}_{package}")
            for qty in (1, 2)
            for package in (0, 1)
        )
    for row in rows:
        assert row["line_gmv"] == seeds.cny4_from_usd(str(row["qty"]))
        assert row["settled_net_share"] == seeds.cny4_from_usd(
            "0.4" if row["qty"] == 1 else "0.8"
        )
        assert row["is_settled"] is True
        assert row["arrived_overseas"] is True
        assert row["full_loss"] is False
        first_package = row["shipment"]["tracking_number"].endswith("_0")
        expected = (
            ["early_first", "early_second", "late", "undated"]
            if first_package
            else ["other_package"]
        )
        assert [item["desc"] for item in row["tracking"]] == [
            f"TEST_{label}" for label in expected
        ]
        if first_package:
            assert row["tracking"][-1]["event_at"] is None


def _assert_settlements(data: dict[str, Any], count: int) -> None:
    rows = data["settlements"]
    assert [row["order_id"] for row in rows] == [
        f"TEST_ORDER_BATCH_{index}"
        for index in reversed(range(count))
        for _ in range(2)
    ]
    for position, row in enumerate(rows):
        assert row["share_ratio"] == "0.3000"
        second = position % 2 == 0
        assert row["components"] == [
            {
                "code": "CUSTOMER_REFUND",
                "amount_vnd": "0.0000" if second else "-26330.0000",
                "amount": seeds.cny4_from_usd("0" if second else "-1"),
            },
            {
                "code": "SETTLEMENT",
                "amount_vnd": "-26330.0000" if second else "131650.0000",
                "amount": seeds.cny4_from_usd("-1" if second else "5"),
            },
        ]


@pytest.mark.parametrize("kind", ["orders", "settlements"])
def test_detail_queries_do_not_grow_with_order_or_transaction_count(
    kind, api_client, readonly_key, db_engine
):
    with Session(db_engine) as session:
        shop = seeds._seed_shop(session, "TEST_SELLER_BATCH")
        x = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_X")
        y = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_Y")
        _seed_detail_order(session, shop, x, y, 0)
        session.commit()

    def read():
        with _queries(db_engine) as calls:
            response = api_client.get(
                f"/v2/analytics/spu-roi/{x}/{kind}",
                headers={"Authorization": f"Bearer {readonly_key}"},
                params={"w_start": "2026-09-01", "w_end": "2026-09-01"},
            )
        assert response.status_code == 200, response.text
        return response.json(), len(calls)

    small, small_count = read()
    with Session(db_engine) as session:
        for index in range(1, 8):
            _seed_detail_order(session, shop, x, y, index)
        session.commit()
    large, large_count = read()
    assert_rows = _assert_orders if kind == "orders" else _assert_settlements
    assert_rows(small, 1)
    assert_rows(large, 8)
    assert large_count <= small_count, (small_count, large_count)


def test_orders_keep_500_row_limit_and_missing_evidence_values(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as session:
        shop = seeds._seed_shop(session, "TEST_SELLER_BATCH_LIMIT")
        spu = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_LIMIT")
        orders = (
            session.execute(
                text(
                    "INSERT INTO commerce.sales_orders "
                    "(shop_pk, order_id, status, currency, paid_at, order_time) "
                    "SELECT :shop, 'TEST_ORDER_BATCH_LIMIT_' || n, 'DELIVERED', 'VND', "
                    "'2026-09-01T08:00:00+00:00'::timestamptz, "
                    "'2026-09-01T08:00:00+00:00'::timestamptz "
                    "FROM generate_series(1, 501) AS n RETURNING id, order_id"
                ),
                {"shop": shop},
            )
            .mappings()
            .all()
        )
        session.execute(
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
                "VALUES (:order, :line, :spu, 1, 26330, 'VND')"
            ),
            [
                {"order": row["id"], "line": f"TEST_LINE_{row['order_id']}", "spu": spu}
                for row in orders
            ],
        )
        session.commit()
    response = api_client.get(
        f"/v2/analytics/spu-roi/{spu}/orders",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["orders"]) == 500
    assert data["meta"]["orders_truncated"] is True
    assert [row["order_id"] for row in data["orders"]] == [
        row["order_id"]
        for row in sorted(orders, key=lambda row: row["id"], reverse=True)[:500]
    ]
    for row in data["orders"]:
        assert row["shipment"] is None
        assert row["tracking"] == []
        assert row["is_settled"] is False
        assert row["settled_net_share"] is None


@pytest.mark.parametrize(
    ("target_price", "other_price", "expected_share"),
    [("0", "0", None), ("-26330", "0", None), ("0", "26330", "0.0000")],
)
def test_detail_shares_preserve_nonpositive_and_zero_numerators(
    api_client, readonly_key, db_engine, target_price, other_price, expected_share
):
    with Session(db_engine) as session:
        shop = seeds._seed_shop(session, "TEST_SELLER_BATCH_ZERO")
        spu = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_ZERO")
        other = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_OTHER")
        order = seeds._seed_order_line(
            session,
            shop_pk=shop,
            spu_pk=spu,
            order_id="TEST_ORDER_BATCH_ZERO",
            status="DELIVERED",
            line_ext="TEST_LINE_BATCH_ZERO",
            qty="1",
            unit_price=target_price,
            paid=True,
        )
        seeds._seed_extra_order_line(
            session,
            order_id="TEST_ORDER_BATCH_ZERO",
            spu_pk=other,
            line_ext="TEST_LINE_BATCH_OTHER",
            qty="1",
            unit_price=other_price,
        )
        seeds._seed_settlement(
            session,
            order_pk=order,
            external_id="TEST_TXN_BATCH_ZERO",
            amount_vnd="0",
        )
        session.commit()
    for kind, field in (
        ("orders", "settled_net_share"),
        ("settlements", "share_ratio"),
    ):
        response = api_client.get(
            f"/v2/analytics/spu-roi/{spu}/{kind}",
            headers={"Authorization": f"Bearer {readonly_key}"},
        )
        assert response.status_code == 200, response.text
        rows = response.json()[kind]
        assert len(rows) == 1
        assert rows[0][field] == expected_share
        if kind == "orders":
            assert rows[0]["is_settled"] is True


def test_details_preserve_empty_evidence(api_client, readonly_key, db_engine):
    with Session(db_engine) as session:
        shop = seeds._seed_shop(session, "TEST_SELLER_BATCH_EMPTY")
        spu = seeds._seed_spu(session, shop, "TEST_ROI_SPU_BATCH_EMPTY")
        session.commit()
    for kind in ("orders", "settlements"):
        response = api_client.get(
            f"/v2/analytics/spu-roi/{spu}/{kind}",
            headers={"Authorization": f"Bearer {readonly_key}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()[kind] == []

    with Session(db_engine) as session:
        order = seeds._seed_order_line(
            session,
            shop_pk=shop,
            spu_pk=spu,
            order_id="TEST_ORDER_BATCH_EMPTY",
            status="DELIVERED",
            line_ext="TEST_LINE_BATCH_EMPTY",
            qty="1",
            unit_price="26330",
            paid=True,
        )
        session.execute(
            text(
                "WITH statement AS (INSERT INTO finance.settlement_statements "
                "(external_statement_id, statement_time, currency) "
                "VALUES ('TEST_STATEMENT_BATCH_EMPTY', "
                "'2026-09-01T08:00:00+00:00', 'VND') RETURNING id) "
                "INSERT INTO finance.settlement_transactions "
                "(settlement_statement_id, order_pk, external_transaction_id, transaction_time) "
                "SELECT id, :order, 'TEST_TXN_BATCH_EMPTY', "
                "'2026-09-01T08:00:00+00:00' FROM statement"
            ),
            {"order": order},
        )
        session.commit()
    response = api_client.get(
        f"/v2/analytics/spu-roi/{spu}/settlements",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["settlements"]) == 1
    assert response.json()["settlements"][0]["components"] == []
