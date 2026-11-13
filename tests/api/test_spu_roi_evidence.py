"""Evidence HTTP reads preserve the explanation wire contract without ROI work."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api import test_spu_roi_api as seeds
from tests.api import test_spu_roi_query_efficiency as budgets
from tts_erp_v2.analytics import spu_roi
from tts_erp_v2.analytics.spu_profitability import (
    EvidenceKind,
    EvidenceRequest,
    ProfitScope,
    SnapshotIsolationUnavailable,
    _snapshot,
    explain_spu,
    read_evidence,
)
from tts_erp_v2.analytics.spu_profitability import _implementation as impl

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture(autouse=True)
def _evidence_state(db_engine, _isolate_state, monkeypatch):
    seeds._wipe(db_engine)
    seeds._seed_fx(db_engine)
    monkeypatch.setattr(_snapshot, "datetime", seeds._FrozenDateTime)
    try:
        yield
    finally:
        seeds._wipe(db_engine)
        seeds._del_fx(db_engine)


@pytest.fixture
def evidence_spu(db_engine):
    with Session(db_engine) as session:
        spu_pk = seeds._seed_scenario_a(session)
        order_pk = session.execute(
            text("SELECT id FROM commerce.sales_orders WHERE order_id = 'TEST_ORDER_A1'")
        ).scalar_one()
        seeds._seed_settlement(
            session,
            order_pk=order_pk,
            external_id="TEST_EVIDENCE_SETTLEMENT",
            amount_vnd="1500000",
            customer_refund_vnd="-526600",
        )
        session.commit()
    return spu_pk


def _get(api_client, key, spu_pk, kind, **params):
    return api_client.get(
        f"/v2/analytics/spu-roi/{spu_pk}/{kind.value}",
        headers={"Authorization": f"Bearer {key}"},
        params=params,
    )


def _explanation_wire(db_engine, spu_pk, kind, params):
    """The full financial explanation remains the independent compatibility oracle."""
    start = date.fromisoformat(params["w_start"]) if "w_start" in params else None
    end = date.fromisoformat(params["w_end"]) if "w_end" in params else None
    with Session(db_engine) as session:
        explanation = explain_spu(
            session,
            spu_pk=spu_pk,
            scope=ProfitScope(
                shop_pk=None, include_inactive=True, start_date=start, end_date=end
            ),
            evidence=EvidenceRequest(frozenset({kind})),
        )
    rows = explanation.evidence.rows[kind]
    expected = {
        "spu_pk": explanation.result.spu_pk,
        kind.value: [spu_roi._wire_value(kind.value, dict(row)) for row in rows],
        "meta": {
            "rubric_version": explanation.basis.rubric_version,
            "computed_at": explanation.basis.calculated_at.isoformat(),
            "currency": {
                "display": explanation.basis.display_currency,
                "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
            },
        },
    }
    if kind is EvidenceKind.ORDERS:
        expected.update(
            spu_id=explanation.result.spu_id,
            window={"w_start": params.get("w_start"), "w_end": params.get("w_end")},
        )
        expected["meta"]["orders_truncated"] = len(rows) >= 500
    if kind is EvidenceKind.ADS:
        expected["meta"]["note"] = "广告域与日期窗口同语义裁剪"
    return expected


@pytest.mark.parametrize("kind", list(EvidenceKind))
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"w_start": "2026-09-01"},
        {"w_end": "2026-09-30"},
        {"w_start": "2026-09-01", "w_end": "2026-09-30"},
        {"w_start": "2026-08-01", "w_end": "2026-08-31"},
    ],
    ids=["all", "start-only", "end-only", "window", "empty-window"],
)
def test_evidence_matches_full_explanation_without_aggregating_roi(
    api_client, readonly_key, db_engine, evidence_spu, monkeypatch, kind, params
):
    expected = _explanation_wire(db_engine, evidence_spu, kind, params)
    overview_calls = []
    original = impl._query_spu_roi

    def record_overview(*args, **kwargs):
        overview_calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(impl, "_query_spu_roi", record_overview)

    def unexpected_financial_work(*args, **kwargs):
        pytest.fail("Evidence-only requests must not load or calculate profitability")

    for name in (
        "calculate", "calculate_projection", "read_price_stats",
        "_resolve_costs_batch", "_load_shop_fee_estimates",
    ):
        monkeypatch.setattr(impl, name, unexpected_financial_work)
    with budgets._queries(db_engine) as calls:
        response = _get(api_client, readonly_key, evidence_spu, kind, **params)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert not overview_calls, "Evidence-only requests must not recompute ROI"
    assert not [sql for sql, _ in calls if sql in budgets._FACT_QUERIES]
    assert not any(
        table in sql
        for sql, _ in calls
        for table in (
            "manual_product_costs", "shop_fee", "sales_order_line_price_observations"
        )
    )
    assert any(sql == "SET TRANSACTION READ ONLY" for sql, _ in calls)
    catalogs = [(sql, bound) for sql, bound in calls if "AS spu_pk" in sql]
    assert len(catalogs) == 1
    assert catalogs[0][1]["only_spu_pk"] == evidence_spu
    assert "cp.id = CAST(" in catalogs[0][0]


def test_evidence_domain_read_keeps_typed_immutable_rows_in_readonly_snapshot(
    db_engine, evidence_spu
):
    with Session(db_engine) as session:
        result = read_evidence(
            session, spu_pk=evidence_spu, evidence=EvidenceRequest()
        )
        assert session.execute(text("SHOW transaction_isolation")).scalar_one() == (
            "repeatable read"
        )
        assert session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
    assert set(result.evidence.rows) == set(EvidenceKind)
    assert isinstance(result.fx.usd_cny, Decimal)
    order = result.evidence.rows[EvidenceKind.ORDERS][0]
    assert isinstance(order["line_gmv"], Decimal)
    with pytest.raises(TypeError):
        order["line_gmv"] = Decimal(0)


def test_evidence_domain_read_rejects_existing_write_transaction(db_engine, evidence_spu):
    with Session(db_engine) as session:
        session.execute(text("SELECT 1"))
        with pytest.raises(SnapshotIsolationUnavailable, match="read-only repeatable-read"):
            read_evidence(session, spu_pk=evidence_spu, evidence=EvidenceRequest())


@pytest.mark.parametrize("kind", list(EvidenceKind))
@pytest.mark.parametrize("state", ["missing", "unauthorized", "inactive", "empty"])
def test_evidence_preserves_catalog_visibility(
    api_client, readonly_key, db_engine, kind, state
):
    with Session(db_engine) as session:
        shop_pk = seeds._seed_shop(session, "TEST_SELLER_EVIDENCE")
        spu_pk = seeds._seed_spu(
            session,
            shop_pk,
            "TEST_ROI_SPU_EVIDENCE",
            status="DEACTIVATE" if state == "inactive" else "ACTIVATE",
        )
        if state == "unauthorized":
            session.execute(
                text("UPDATE commerce.shops SET credential_id = NULL WHERE id = :pk"),
                {"pk": shop_pk},
            )
        session.commit()
    if state == "missing":
        spu_pk += 1000000
    response = _get(api_client, readonly_key, spu_pk, kind)
    if state in {"missing", "unauthorized"}:
        assert response.status_code == 404
        assert response.json()["detail"] == (
            f"spu_pk {spu_pk} not found in profitability scope"
        )
    else:
        assert response.status_code == 200, response.text
        assert response.json() == _explanation_wire(db_engine, spu_pk, kind, {})
        assert response.json()[kind.value] == []


@pytest.mark.parametrize("kind", list(EvidenceKind))
@pytest.mark.parametrize("params", [{}, {"w_start": "2026-09-01"}, {"w_end": "2026-09-30"}])
def test_evidence_preserves_unknown_region_date_validation(
    api_client, readonly_key, db_engine, kind, params
):
    with Session(db_engine) as session:
        shop_pk = seeds._seed_shop(session, "TEST_SELLER_EVIDENCE", region=None)
        spu_pk = seeds._seed_spu(session, shop_pk, "TEST_ROI_SPU_EVIDENCE")
        session.commit()
    response = _get(api_client, readonly_key, spu_pk, kind, **params)
    if params:
        assert response.status_code == 422, response.text
        assert "reporting timezone" in response.json()["detail"]
    else:
        assert response.status_code == 200, response.text
        assert response.json() == _explanation_wire(db_engine, spu_pk, kind, {})


@pytest.mark.parametrize("kind", list(EvidenceKind))
@pytest.mark.parametrize("state", ["missing", "unauthorized", "unknown-region"])
def test_evidence_fx_error_precedes_catalog_and_timezone_errors(
    api_client, readonly_key, db_engine, monkeypatch, kind, state
):
    with Session(db_engine) as session:
        shop_pk = seeds._seed_shop(session, "TEST_SELLER_EVIDENCE", region=None)
        spu_pk = seeds._seed_spu(session, shop_pk, "TEST_ROI_SPU_EVIDENCE")
        if state == "unauthorized":
            session.execute(
                text("UPDATE commerce.shops SET credential_id = NULL WHERE id = :pk"),
                {"pk": shop_pk},
            )
        session.commit()
    if state == "missing":
        spu_pk += 1000000
    monkeypatch.setattr(impl, "load_rate_map", lambda *args, **kwargs: None)
    response = _get(api_client, readonly_key, spu_pk, kind, w_start="2026-09-01")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "FX_RATE_UNAVAILABLE"
    invalid_dates = _get(
        api_client, readonly_key, spu_pk, kind,
        w_start="2026-09-30", w_end="2026-09-01",
    )
    assert invalid_dates.status_code == 422, invalid_dates.text
