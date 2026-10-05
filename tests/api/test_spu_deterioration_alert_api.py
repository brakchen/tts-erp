from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import delete, inspect, update
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    SEED_FALLBACK_CONFIG,
    config_to_payload,
)
from tts_erp_v2.db.models import ChannelAccount, ChannelProduct, SpuDeteriorationAlert

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture()
def materialized_alert_scope(db_engine) -> Iterator[tuple[int, int]]:
    if not inspect(db_engine).has_table(
        "spu_deterioration_alerts", schema="analytics"
    ):
        pytest.skip("alert migration is not applied in the isolated template")
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
    config_hash = sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok", shop_id="TEST_alert_shop", status="active"
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(shop_pk=shop.id, spu_id="TEST_alert_spu", status="active")
        session.add(spu)
        session.flush()
        session.add(
            SpuDeteriorationAlert(
                shop_pk=shop.id,
                spu_pk=spu.id,
                anchor_date=datetime.now(UTC).date() - timedelta(days=2),
                window_days=1,
                layer="fast",
                severity="warning",
                state="roi_deterioration",
                sample_status="sufficient",
                prior_roi=Decimal("1"),
                current_roi=Decimal(".7"),
                roi_decline=Decimal(".3"),
                prior_net_profit_cny=Decimal("100"),
                current_net_profit_cny=Decimal("70"),
                net_profit_decline=Decimal(".3"),
                previous_spend_cny=Decimal("100"),
                current_spend_cny=Decimal("100"),
                previous_order_count=5,
                current_order_count=5,
                previous_ad_orders=1,
                current_ad_orders=1,
                effective_config_source="seed_fallback",
                effective_config_version=None,
                config_payload_hash=config_hash,
                basis_calculated_at=datetime.now(UTC),
            )
        )
        session.commit()
        ids = (shop.id, spu.id)
    yield ids
    with Session(db_engine) as session:
        session.execute(
            delete(SpuDeteriorationAlert).where(SpuDeteriorationAlert.shop_pk == ids[0])
        )
        session.execute(delete(ChannelProduct).where(ChannelProduct.id == ids[1]))
        session.execute(delete(ChannelAccount).where(ChannelAccount.id == ids[0]))
        session.commit()


def test_authenticated_get_requires_shop_and_returns_complete_scope(
    api_client, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, spu_pk = materialized_alert_scope
    headers = {"Authorization": f"Bearer {readonly_key}"}
    assert api_client.get("/v2/analytics/spu-profit-deterioration").status_code == 401
    assert (
        api_client.get(
            "/v2/analytics/spu-profit-deterioration", headers=headers
        ).status_code
        == 422
    )
    response = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&limit=1",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["spuPk"] == spu_pk
    assert body["totals"]["shopSpuCount"] == 1
    assert "rollout" not in body["meta"]["effectiveConfig"]
    assert body["meta"]["effectiveConfig"]["source"] == "seed_fallback"


def test_missing_stale_and_mismatched_snapshots_fail_closed(
    api_client, readonly_key, db_engine, materialized_alert_scope
) -> None:
    shop_pk, _ = materialized_alert_scope
    headers = {"Authorization": f"Bearer {readonly_key}"}
    missing = api_client.get(
        "/v2/analytics/spu-profit-deterioration?shop_pk=999999999",
        headers=headers,
    )
    assert missing.status_code == 503
    with Session(db_engine) as session:
        session.execute(
            update(SpuDeteriorationAlert)
            .where(SpuDeteriorationAlert.shop_pk == shop_pk)
            .values(config_payload_hash="0" * 64)
        )
        session.commit()
    mismatch = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
        headers=headers,
    )
    assert mismatch.status_code == 503


def test_stale_snapshot_fails_closed(
    api_client, readonly_key, db_engine, materialized_alert_scope
) -> None:
    shop_pk, _ = materialized_alert_scope
    with Session(db_engine) as session:
        session.execute(
            update(SpuDeteriorationAlert)
            .where(SpuDeteriorationAlert.shop_pk == shop_pk)
            .values(anchor_date=datetime.now(UTC).date() - timedelta(days=4))
        )
        session.commit()
    response = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 503


def test_valid_empty_filter_preserves_snapshot_totals(
    api_client, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, _ = materialized_alert_scope
    response = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&severity=critical",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 1
    assert response.json()["totals"]["shopSpuCount"] == 1
