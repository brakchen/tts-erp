from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    ALERT_CONFIG_KEY,
    SEED_FALLBACK_CONFIG,
    config_to_payload,
)
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    RuntimeConfigItem,
    RuntimeConfigRevision,
    SpuDeteriorationAlert,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _ensure_alert_migration(db_engine) -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic/versions/0053_spu_deterioration_alert.py"
    )
    required = {
        "commerce.shops",
        "commerce.products_spu",
        "commerce.sales_orders",
        "commerce.sales_order_lines",
        "after_sales.cases",
        "after_sales.case_lines",
        "plugin.ad_daily",
        "finance.settlement_statements",
        "finance.settlement_transactions",
        "finance.settlement_components",
        "config.runtime_config_items",
        "config.runtime_config_revisions",
        "security.api_keys",
    }
    with db_engine.connect() as connection:
        # pi-lens-ignore: python-sql-injection
        database = connection.execute(text("SELECT current_database()")).scalar_one()
        assert database.startswith("tts_erp_test_")
        assert database != "tts_erp_test_template"
        inspector = inspect(connection)
        missing = sorted(
            schema_table
            for schema_table in required
            if not inspector.has_table(*schema_table.split(".", 1)[::-1])
        )
        if missing:
            pytest.skip(f"prerequisite schema unavailable: {missing}")
        if inspector.has_table("spu_deterioration_alerts", schema="analytics"):
            return
    with db_engine.begin() as connection:
        # pi-lens-ignore: python-sql-injection
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
        spec = importlib.util.spec_from_file_location(
            "alert_migration_0053_test", migration_path
        )
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        migration.upgrade()


@pytest.fixture()
def materialized_alert_scope(db_engine) -> Iterator[tuple[int, int]]:
    _ensure_alert_migration(db_engine)
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
    config_hash = sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    calculated_at = datetime.now(UTC)
    anchor_date = calculated_at.date() - timedelta(days=2)
    with Session(db_engine) as session:
        # The 0053 migration seeds a strict draft; this fixture intentionally
        # replaces it with the published fixture required by API-contract tests.
        session.execute(
            delete(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(
            delete(RuntimeConfigItem).where(
                RuntimeConfigItem.config_key == ALERT_CONFIG_KEY
            )
        )
        session.add(
            RuntimeConfigItem(
                config_key=ALERT_CONFIG_KEY,
                display_name="TEST published alert",
                json_schema={"type": "object", "additionalProperties": True},
                draft_payload=payload,
                draft_rollout=[],
                draft_version=1,
                published_version=3,
            )
        )
        session.add(
            RuntimeConfigRevision(
                config_key=ALERT_CONFIG_KEY,
                version=3,
                payload=payload,
                rollout=[],
                created_by="TEST_published_alert",
            )
        )
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
                anchor_date=anchor_date,
                window_days=1,
                layer="fast",
                severity="warning",
                state="roi_deterioration",
                sample_status="sufficient",
                prior_roi=Decimal(1),
                current_roi=Decimal(".7"),
                roi_decline=Decimal(".3"),
                prior_net_profit_cny=Decimal(100),
                current_net_profit_cny=Decimal(70),
                net_profit_decline=Decimal(".3"),
                previous_spend_cny=Decimal(100),
                current_spend_cny=Decimal(100),
                previous_order_count=5,
                current_order_count=5,
                previous_ad_orders=1,
                current_ad_orders=1,
                effective_config_source="runtime_config",
                effective_config_version=3,
                config_payload_hash=config_hash,
                basis_calculated_at=calculated_at,
            )
        )
        session.add(
            SpuDeteriorationAlert(
                shop_pk=shop.id,
                spu_pk=spu.id,
                anchor_date=anchor_date,
                window_days=3,
                layer="confirmation",
                severity="critical",
                state="unavailable",
                sample_status="unavailable",
                effective_config_source="runtime_config",
                effective_config_version=3,
                config_payload_hash=config_hash,
                basis_calculated_at=calculated_at,
            )
        )
        session.commit()
        ids = (shop.id, spu.id)
    yield ids
    with Session(db_engine) as session:
        session.execute(
            delete(SpuDeteriorationAlert).where(SpuDeteriorationAlert.shop_pk == ids[0])
        )
        session.execute(
            delete(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(
            delete(RuntimeConfigItem).where(
                RuntimeConfigItem.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(delete(ChannelProduct).where(ChannelProduct.id == ids[1]))
        session.execute(delete(ChannelAccount).where(ChannelAccount.id == ids[0]))
        session.commit()


def test_published_alert_rollout_fails_closed_for_runtime_read(
    api_client, db_engine, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, _spu_pk = materialized_alert_scope
    headers = {"Authorization": f"Bearer {readonly_key}"}
    with Session(db_engine) as session:
        revision = session.scalar(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                RuntimeConfigRevision.version == 3,
            )
        )
        assert revision is not None
        revision.rollout = [
            {
                "name": "canary",
                "basisPoints": 10000,
                "payload": config_to_payload(SEED_FALLBACK_CONFIG),
            }
        ]
        session.commit()
    try:
        response = api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
            headers=headers,
        )
        assert response.status_code == 503
        with Session(db_engine) as session:
            revision = session.scalar(
                select(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                    RuntimeConfigRevision.version == 3,
                )
            )
            assert revision is not None
            revision.rollout = {}  # type: ignore[assignment]
            session.commit()
        malformed = api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
            headers=headers,
        )
        assert malformed.status_code == 503
    finally:
        with Session(db_engine) as session:
            revision = session.scalar(
                select(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                    RuntimeConfigRevision.version == 3,
                )
            )
            assert revision is not None
            revision.rollout = []
            session.commit()


def test_authenticated_get_requires_shop_and_returns_complete_scope(
    api_client, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, spu_pk = materialized_alert_scope
    headers = {
        "Authorization": f"Bearer {readonly_key}",
        "X-Request-Id": "TEST_REQ_ALERT",
    }
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
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["spuPk"] == spu_pk
    assert body["items"][0]["configSource"] == "runtime_config"
    assert body["items"][0]["configVersion"] == 3
    assert body["totals"]["shopSpuCount"] == 1
    item = body["items"][0]
    assert isinstance(item["previousRoi"], str)
    assert item["drilldown"]["profitabilityUrl"].startswith("/v2/analytics/spu-roi?")
    assert body["meta"]["requestId"] == "TEST_REQ_ALERT"
    assert "rollout" not in body["meta"]["effectiveConfig"]
    assert "draftPayload" not in body["meta"]["effectiveConfig"]
    assert "secrets" not in body["meta"]["effectiveConfig"]
    assert body["meta"]["effectiveConfig"]["source"] == "runtime_config"
    assert body["meta"]["effectiveConfig"]["version"] == 3
    expected_hash = sha256(
        json.dumps(
            config_to_payload(SEED_FALLBACK_CONFIG),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert body["meta"]["effectiveConfig"]["payloadHash"] == expected_hash


def test_totals_count_distinct_shop_spu_across_windows_filters_and_pages(
    api_client, db_engine, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, spu_pk = materialized_alert_scope
    headers = {"Authorization": f"Bearer {readonly_key}"}
    with Session(db_engine) as session:
        existing = session.scalars(
            select(SpuDeteriorationAlert).where(
                SpuDeteriorationAlert.shop_pk == shop_pk
            )
        ).all()
        assert len(existing) == 2
        template = existing[0]
        second = ChannelProduct(
            shop_pk=shop_pk, spu_id="TEST_alert_second_spu", status="active"
        )
        session.add(second)
        session.flush()
        existing_keys = {(row.spu_pk, row.window_days, row.layer) for row in existing}
        for target_pk in (spu_pk, second.id):
            for window_days in (1, 3, 7):
                for layer in ("fast", "confirmation"):
                    if (target_pk, window_days, layer) in existing_keys:
                        continue
                    session.add(
                        SpuDeteriorationAlert(
                            shop_pk=shop_pk,
                            spu_pk=target_pk,
                            anchor_date=template.anchor_date,
                            window_days=window_days,
                            layer=layer,
                            severity="none",
                            state="stable",
                            sample_status="unavailable",
                            effective_config_source=template.effective_config_source,
                            effective_config_version=template.effective_config_version,
                            config_payload_hash=template.config_payload_hash,
                            basis_calculated_at=template.basis_calculated_at,
                        )
                    )
        session.commit()
    try:
        all_rows = api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&limit=1",
            headers=headers,
        )
        assert all_rows.status_code == 200, all_rows.text
        assert all_rows.json()["total"] == 12
        assert all_rows.json()["totals"]["shopSpuCount"] == 2
        filtered = api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}"
            "&window_days=1&layer=fast&limit=1",
            headers=headers,
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 12
        assert filtered.json()["totals"]["shopSpuCount"] == 2
        scoped = api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}"
            f"&spu_ids={spu_pk}&limit=1",
            headers=headers,
        )
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["total"] == 12
        assert scoped.json()["totals"]["shopSpuCount"] == 2
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(
                    ChannelProduct.shop_pk == shop_pk,
                    ChannelProduct.spu_id == "TEST_alert_second_spu",
                )
            )
            session.commit()


def test_repeatable_filters_bounds_and_explicit_latest_anchor(
    api_client, readonly_key, materialized_alert_scope
) -> None:
    shop_pk, spu_pk = materialized_alert_scope
    headers = {"Authorization": f"Bearer {readonly_key}"}
    filtered = api_client.get(
        "/v2/analytics/spu-profit-deterioration",
        params=[
            ("shop_pk", shop_pk),
            ("window_days", 3),
            ("window_days", 1),
            ("state", "unavailable"),
            ("sample", "unavailable"),
            ("anchor_date", (datetime.now(UTC).date() - timedelta(days=2)).isoformat()),
        ],
        headers=headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 2
    assert all(
        item["sampleStatus"] == "unavailable" for item in filtered.json()["items"]
    )
    assert all(item["previousRoi"] is None for item in filtered.json()["items"])
    assert (
        api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&severity=bad",
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&window_days=2",
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&limit=0",
            headers=headers,
        ).status_code
        == 422
    )
    repeated = "&".join(f"spu_ids={spu_pk}" for _ in range(101))
    assert (
        api_client.get(
            f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&{repeated}",
            headers=headers,
        ).status_code
        == 422
    )


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
        item = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
        assert item is not None
        draft_payload = config_to_payload(SEED_FALLBACK_CONFIG)
        draft_payload["enabled"] = False
        item.draft_payload = draft_payload
        session.commit()
    draft_only = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
        headers=headers,
    )
    assert draft_only.status_code == 200
    assert draft_only.json()["meta"]["enabled"] is True
    with Session(db_engine) as session:
        item = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
        assert item is not None
        session.add(
            RuntimeConfigRevision(
                config_key=ALERT_CONFIG_KEY,
                version=4,
                payload=config_to_payload(SEED_FALLBACK_CONFIG),
                rollout=[],
                created_by="TEST_new_publication",
            )
        )
        item.published_version = 4
        session.commit()
    newer_publication = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&severity=none",
        headers=headers,
    )
    assert newer_publication.status_code == 503
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


def test_disabled_published_config_returns_safe_empty_scope(
    api_client, readonly_key, db_engine, materialized_alert_scope
) -> None:
    shop_pk, _ = materialized_alert_scope
    with Session(db_engine) as session:
        revision = session.execute(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                RuntimeConfigRevision.version == 3,
            )
        ).scalar_one()
        assert revision is not None
        disabled = config_to_payload(SEED_FALLBACK_CONFIG)
        disabled["enabled"] = False
        revision.payload = disabled
        session.commit()
    response = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["meta"]["enabled"] is False


def test_missing_published_config_uses_visible_seed_fallback(
    api_client, readonly_key, db_engine, materialized_alert_scope
) -> None:
    shop_pk, _ = materialized_alert_scope
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
    config_hash = sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    with Session(db_engine) as session:
        session.execute(
            delete(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(
            delete(RuntimeConfigItem).where(
                RuntimeConfigItem.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(
            update(SpuDeteriorationAlert)
            .where(SpuDeteriorationAlert.shop_pk == shop_pk)
            .values(
                effective_config_source="seed_fallback",
                effective_config_version=None,
                config_payload_hash=config_hash,
            )
        )
        session.commit()
    response = api_client.get(
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["config"]["source"] == "seed_fallback"
    assert response.json()["meta"]["effectiveConfig"]["provisionalLabel"] == "回测暂定"


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
        f"/v2/analytics/spu-profit-deterioration?shop_pk={shop_pk}&severity=none",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 2
    assert response.json()["totals"]["shopSpuCount"] == 1
