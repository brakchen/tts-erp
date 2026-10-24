"""An external SPU ID cannot select a shop's authoritative cost write target."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelAccount, ChannelProduct, ManualProductCost

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture
def products(db_engine):
    with Session(db_engine) as session:
        shops = [
            ChannelAccount(
                platform="tiktok",
                shop_id=f"TEST_remaining_cost_shop_{n}",
                account_name=f"TEST remaining shop {n}",
                status="active",
            )
            for n in range(2)
        ]
        session.add_all(shops)
        session.flush()
        rows = [
            ChannelProduct(
                shop_pk=shop.id,
                spu_id="TEST_remaining_shared_spu",
                title="TEST duplicate external identity",
                status="ACTIVATE",
            )
            for shop in shops
        ]
        rows.append(
            ChannelProduct(
                shop_pk=shops[0].id,
                spu_id="TEST_remaining_unique_spu",
                title="TEST unique external identity",
                status="ACTIVATE",
            )
        )
        session.add_all(rows)
        session.flush()
        ids = tuple(row.id for row in rows)
        session.add_all(
            [
                ManualProductCost(
                    spu_pk=pk,
                    unit_cost=Decimal("1.2500"),
                    currency="USD",
                    note="TEST original fact",
                    created_by="TEST seed",
                )
                for pk in ids[:2]
            ]
        )
        session.commit()
    return ids


def _facts(db_engine, ids):
    with Session(db_engine) as session:
        return session.execute(
            select(
                ManualProductCost.id,
                ManualProductCost.spu_pk,
                ManualProductCost.unit_cost,
                ManualProductCost.currency,
                ManualProductCost.valid_from,
                ManualProductCost.valid_to,
                ManualProductCost.note,
                ManualProductCost.created_by,
                ManualProductCost.created_at,
                ManualProductCost.updated_at,
            )
            .where(ManualProductCost.spu_pk.in_(ids))
            .order_by(ManualProductCost.id)
        ).all()


def _post(api_client, key, **identity):
    return api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "spu_id": "TEST_remaining_shared_spu",
            "unit_cost": "2.3456",
            "currency": "USD",
            "note": "TEST exact target",
            **identity,
        },
    )


def test_legacy_ambiguous_external_id_cannot_close_or_insert_any_fact(
    api_client, readwrite_key, db_engine, products
):
    before = _facts(db_engine, products)
    response = _post(api_client, readwrite_key)
    assert response.status_code == 409, response.text
    assert _facts(db_engine, products) == before


@pytest.mark.parametrize("target_index", [0, 1])
def test_internal_spu_pk_selects_only_the_requested_shop_and_preserves_history(
    api_client, readwrite_key, db_engine, products, target_index
):
    target = products[target_index]
    other = products[1 - target_index]
    before_other = [row for row in _facts(db_engine, products) if row.spu_pk == other]
    response = _post(api_client, readwrite_key, spu_pk=target)
    assert response.status_code == 201, response.text
    assert response.json()["spu_pk"] == target
    assert response.json()["unit_cost"] == "2.3456"
    rows = _facts(db_engine, products)
    assert [row for row in rows if row.spu_pk == other] == before_other
    own = [row for row in rows if row.spu_pk == target]
    assert len(own) == 2 and own[0].valid_to is not None
    assert own[1].valid_to is None and own[1].unit_cost == Decimal("2.3456")


@pytest.mark.parametrize("bad_pk", [0, -1, 9223372036854775808])
def test_invalid_internal_identity_is_rejected_before_fact_changes(
    api_client, readwrite_key, db_engine, products, bad_pk
):
    before = _facts(db_engine, products)
    response = _post(api_client, readwrite_key, spu_pk=bad_pk)
    assert response.status_code == 422, response.text
    assert _facts(db_engine, products) == before


@pytest.mark.parametrize("missing", [False, True])
def test_mismatched_or_missing_internal_identity_never_falls_back_to_external_first_row(
    api_client, readwrite_key, db_engine, products, missing
):
    before = _facts(db_engine, products)
    response = _post(
        api_client,
        readwrite_key,
        spu_pk=9223372036854775806 if missing else products[2],
    )
    assert response.status_code == 404, response.text
    assert _facts(db_engine, products) == before


def test_legacy_unique_external_identity_remains_compatible(
    api_client, readwrite_key, db_engine, products
):
    response = _post(api_client, readwrite_key, spu_id="TEST_remaining_unique_spu")
    assert response.status_code == 201, response.text
    assert response.json()["spu_pk"] == products[2]


def test_internal_identity_does_not_grant_readonly_writers_permission(
    api_client, readonly_key, db_engine, products
):
    before = _facts(db_engine, products)
    response = _post(api_client, readonly_key, spu_pk=products[1])
    assert response.status_code == 403, response.text
    assert _facts(db_engine, products) == before


def test_default_effective_timestamp_uses_aware_utc_not_host_wall_clock(
    api_client, readwrite_key, products, monkeypatch
):
    from tts_erp_v2.api.v2 import reporting

    # Materialize the lazy test app's real annotations before replacing only
    # this handler's clock, not the API's datetime field types.
    warmup = api_client.get(
        "/v2/reporting/coverage", headers={"Authorization": f"Bearer {readwrite_key}"}
    )
    assert warmup.status_code == 200, warmup.text
    instant = datetime(2026, 10, 24, 19, tzinfo=UTC)

    class HostClock:
        @staticmethod
        def now(tz=None):
            # A Shanghai host wall clock is not a UTC database timestamp.
            return (
                instant.astimezone(tz)
                if tz is not None
                else instant.replace(tzinfo=None) + timedelta(hours=8)
            )

    monkeypatch.setattr(reporting, "datetime", HostClock)
    response = _post(api_client, readwrite_key, spu_id="TEST_remaining_unique_spu")
    assert response.status_code == 201, response.text
    assert datetime.fromisoformat(response.json()["valid_from"]) == instant
