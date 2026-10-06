from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    ALERT_CONFIG_KEY,
    SEED_FALLBACK_CONFIG,
    config_to_payload,
)
from tts_erp_v2.analytics.spu_deterioration_alert.materialize import replace_anchor
from tts_erp_v2.analytics.spu_profitability import _implementation
from tts_erp_v2.db.models import (
    ChannelAccount,
    ChannelProduct,
    RuntimeConfigItem,
    RuntimeConfigRevision,
    SpuDeteriorationAlert,
    SyncJob,
)
from tts_erp_v2.jobs.spu_deterioration_alert import (
    _record_failure,
    materialize_alerts,
    run_scheduled,
)

pytestmark = pytest.mark.domain_sync


def _ensure_alert_migration(db_engine) -> None:
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
        migration_path = (
            Path(__file__).parents[2]
            / "alembic/versions/0053_spu_deterioration_alert.py"
        )
        spec = importlib.util.spec_from_file_location(
            "alert_migration_0053_sync_test", migration_path
        )
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        migration.upgrade()


def _seed_bounded_fx(db_engine) -> None:
    with db_engine.begin() as connection:
        # pi-lens-ignore: python-sql-injection
        snapshot_id = connection.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
                "VALUES ('USD', '2099-10-05T00:00:00+00:00', "
                "'2099-10-06T00:00:00+00:00', now(), 3) RETURNING id"
            )
        ).scalar_one()
        for target_code, rate in (("USD", "1"), ("VND", "26330"), ("CNY", "6.7686473")):
            # pi-lens-ignore: python-sql-injection
            connection.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:snapshot_id, 'USD', :target_code, :rate)"
                ),
                {
                    "snapshot_id": snapshot_id,
                    "target_code": target_code,
                    "rate": rate,
                },
            )


def _delete_bounded_fx(db_engine) -> None:
    with db_engine.begin() as connection:
        # pi-lens-ignore: python-sql-injection
        connection.execute(
            text(
                "DELETE FROM fx.exchange_rate_snapshots "
                "WHERE upstream_last_update = '2099-10-05T00:00:00+00:00'"
            )
        )


def _insert_bounded_order(
    session: Session,
    *,
    shop_pk: int,
    spu_pk: int,
    key: str,
    anchor: date,
    status: str,
) -> int:
    paid_at = f"{anchor.isoformat()}T08:00:00+00:00"
    order_pk = session.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, paid_at, order_time) "
            "VALUES (:shop, :order_id, :status, 'VND', "
            "CAST(:paid_at AS timestamptz), CAST(:paid_at AS timestamptz)) "
            "RETURNING id"
        ),
        {
            "shop": shop_pk,
            "order_id": f"TEST_BULK_ORDER_{key}",
            "status": status,
            "paid_at": paid_at,
        },
    ).scalar_one()
    session.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "VALUES (:order_pk, :line_id, :spu_pk, 1, 526600, 'VND')"
        ),
        {
            "order_pk": order_pk,
            "line_id": f"TEST_BULK_LINE_{key}",
            "spu_pk": spu_pk,
        },
    )
    return order_pk


def _insert_bounded_ad(
    session: Session, *, seller: str, spu_id: str, anchor: date
) -> None:
    session.execute(
        text(
            "INSERT INTO plugin.ad_daily "
            "(seller_id, advertiser_id, campaign_id, product_id, endpoint, day, "
            "mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value, "
            "onsite_mixed_real_roi2_shopping, metrics_extra, created_at) "
            "VALUES (:seller, 'TEST_BULK_ADV', :campaign, :product_id, "
            "'/oec_ads/shopping/v1/oec/stat/post_product_list', :day, "
            "10, 1, 20, NULL, '{}'::jsonb, now())"
        ),
        {
            "seller": seller,
            "campaign": f"TEST_BULK_CAMPAIGN_{spu_id}",
            "product_id": spu_id,
            "day": anchor,
        },
    )


def _insert_bounded_refund_case(
    session: Session, *, order_pk: int, key: str, case_type: str
) -> None:
    line_pk = session.execute(
        text(
            "SELECT id FROM commerce.sales_order_lines "
            "WHERE order_pk = :order_pk LIMIT 1"
        ),
        {"order_pk": order_pk},
    ).scalar_one()
    case_pk = session.execute(
        text(
            "INSERT INTO after_sales.cases "
            "(shop_pk, order_pk, external_case_id, case_type, status, "
            "created_at_source, updated_at_source, currency) "
            "SELECT shop_pk, id, :case_id, :case_type, "
            "'RETURN_OR_REFUND_REQUEST_COMPLETE', "
            "CAST(:updated AS timestamptz), CAST(:updated AS timestamptz), 'VND' "
            "FROM commerce.sales_orders WHERE id = :order_pk RETURNING id"
        ),
        {
            "order_pk": order_pk,
            "case_id": f"TEST_BULK_CASE_{key}",
            "case_type": case_type,
            "updated": "2026-10-03T00:00:00+00:00",
        },
    ).scalar_one()
    session.execute(
        text(
            "INSERT INTO after_sales.case_lines "
            "(case_id, sales_order_line_id, external_case_line_id, quantity, "
            "refund_amount, currency) VALUES (:case_pk, :line_pk, :line_id, "
            "1, 526600, 'VND')"
        ),
        {
            "case_pk": case_pk,
            "line_pk": line_pk,
            "line_id": f"TEST_BULK_CASE_LINE_{key}",
        },
    )


def _insert_bounded_settlement(session: Session, *, order_pk: int, key: str) -> None:
    statement_pk = session.execute(
        text(
            "INSERT INTO finance.settlement_statements "
            "(external_statement_id, statement_time, currency) "
            "VALUES (:statement_id, '2026-10-03T00:00:00+00:00', 'VND') "
            "RETURNING id"
        ),
        {"statement_id": f"TEST_BULK_STATEMENT_{key}"},
    ).scalar_one()
    transaction_pk = session.execute(
        text(
            "INSERT INTO finance.settlement_transactions "
            "(settlement_statement_id, external_transaction_id, order_pk, "
            "transaction_time) VALUES (:statement_pk, :external_id, :order_pk, "
            "'2026-10-03T00:00:00+00:00') RETURNING id"
        ),
        {
            "statement_pk": statement_pk,
            "external_id": f"TEST_BULK_TRANSACTION_{key}",
            "order_pk": order_pk,
        },
    ).scalar_one()
    session.execute(
        text(
            "INSERT INTO finance.settlement_components "
            "(transaction_id, component_code, amount, currency) "
            "VALUES (:transaction_pk, 'SETTLEMENT', 526600, 'VND')"
        ),
        {"transaction_pk": transaction_pk},
    )


def _parameter_value(parameters: object, name: str, context: object) -> object:
    if isinstance(parameters, Mapping):
        return parameters.get(name)
    compiled = getattr(context, "compiled", None)
    positiontup = getattr(compiled, "positiontup", None)
    if positiontup and name in positiontup and isinstance(parameters, (list, tuple)):
        return parameters[positiontup.index(name)]
    return None


def _numeric_parameter(
    parameters: object, name: str, context: object
) -> set[int] | None:
    value = _parameter_value(parameters, name, context)
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return None
    return set(value)


def _fact_family(statement: str, parameters: object, context: object) -> str | None:
    if "procurement.manual_product_costs" in statement:
        return "cost"
    if "projection_sample_start" in statement:
        return "projection"
    if "FROM plugin.ad_daily d" in statement:
        return "ad"
    if "SELECT count(DISTINCT so.id)::int AS refund_order_count" in statement:
        return "refund_scope"
    if "AS gmv" in statement and "cancelled_order_count" in statement:
        return "order_scope"
    if "confirmed_unsettled_refund_vnd" in statement:
        return "sales"
    if "full_loss_qty" in statement and "FROM after_sales.cases c" in statement:
        return "full_loss"
    if "cancelled_order_count" in statement:
        return "row_status"
    if "refund_only_amount" in statement:
        return "refund"
    return None


def _assert_fact_keys(
    family: str, selected_pks: set[int], active_batch_pks: set[int]
) -> None:
    assert family in {
        "ad",
        "sales",
        "full_loss",
        "row_status",
        "refund",
        "projection",
        "cost",
        "refund_scope",
        "order_scope",
    }
    assert selected_pks
    assert len(selected_pks) <= 100
    assert selected_pks <= active_batch_pks


def test_run_scheduled_replaces_all_anchors_and_records_later_failure(
    monkeypatch,
) -> None:
    session = MagicMock()
    anchors = {date(2026, 10, 9): [1], date(2026, 10, 10): [2]}
    replacement = MagicMock(side_effect=[1, RuntimeError("batch failed")])
    failure = MagicMock(return_value={"status": "failed"})
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.build_materialized_rows",
        lambda _session: anchors,
    )
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.replace_anchor", replacement
    )
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert._record_failure", failure
    )
    assert run_scheduled(session) == {"status": "failed"}
    assert replacement.call_count == 2
    failure.assert_called_once()


def test_materialize_guard_denies_prod_shape_before_delete(
    db_engine, monkeypatch
) -> None:
    _ensure_alert_migration(db_engine)
    anchor = date(2026, 10, 10)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_guard_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(shop_pk=shop.id, spu_id="TEST_guard_spu", status="active")
        session.add(spu)
        session.flush()
        old_row = SpuDeteriorationAlert(
            shop_pk=shop.id,
            spu_pk=spu.id,
            anchor_date=anchor,
            window_days=1,
            layer="fast",
            severity="none",
            state="stable",
            sample_status="sufficient",
            effective_config_source="seed_fallback",
            config_payload_hash="0" * 64,
            basis_calculated_at=datetime.now(UTC),
        )
        session.add(old_row)
        session.commit()
        shop_pk, old_id = shop.id, old_row.id
    monkeypatch.setattr("tts_erp_v2.api.deps.is_prod_shaped_db", lambda: True)
    deleted: list[str] = []

    def observe_delete(_connection, _cursor, statement, *_args):
        if "DELETE FROM analytics.spu_deterioration_alerts" in statement:
            deleted.append(statement)

    event.listen(db_engine, "before_cursor_execute", observe_delete)
    try:
        with Session(db_engine) as session, pytest.raises(SystemExit):
            materialize_alerts(session, anchor_date=anchor, rows=[])
        assert deleted == []
        with Session(db_engine) as session:
            assert session.get(SpuDeteriorationAlert, old_id) is not None
    finally:
        event.remove(db_engine, "before_cursor_execute", observe_delete)
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()


def test_failure_audit_redacts_read_and_write_exception_details(
    db_engine, monkeypatch, caplog
) -> None:
    _ensure_alert_migration(db_engine)
    sentinel = "SENSITIVE_SQL_BIND_TEST_SECRET"
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.build_materialized_rows",
        lambda _session: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )
    with Session(db_engine) as session:
        read_result = run_scheduled(session)
    assert read_result["status"] == "failed"
    assert sentinel not in str(read_result)
    assert sentinel not in caplog.text
    with Session(db_engine) as session:
        read_job = session.scalar(
            select(SyncJob)
            .where(SyncJob.job_name == "analytics.spu_profit_deterioration_alert")
            .order_by(SyncJob.id.desc())
        )
        assert read_job is not None
        assert sentinel not in str(read_job.extra)

    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.build_materialized_rows",
        lambda _session: {date(2026, 10, 10): []},
    )
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.replace_anchor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )
    with Session(db_engine) as session:
        write_result = run_scheduled(session)
    assert write_result["status"] == "failed"
    assert sentinel not in str(write_result)
    assert sentinel not in caplog.text
    with Session(db_engine) as session:
        write_job = session.scalar(
            select(SyncJob)
            .where(SyncJob.job_name == "analytics.spu_profit_deterioration_alert")
            .order_by(SyncJob.id.desc())
        )
        assert write_job is not None
        assert sentinel not in str(write_job.extra)


def test_failure_audit_commit_error_is_sanitized() -> None:
    session = MagicMock()
    sentinel = "SENSITIVE_FAILURE_AUDIT_SQL_BIND_TEST_SECRET"
    session.commit.side_effect = SQLAlchemyError(sentinel)

    with pytest.raises(RuntimeError) as error:
        _record_failure(session, phase="write", error_type="SQLAlchemyError")

    assert str(error.value) == "write:audit:SQLAlchemyError"
    assert sentinel not in str(error.value)
    assert session.rollback.call_count >= 2


def test_commit_failure_rolls_back_snapshot_and_sanitizes_audit(
    db_engine, monkeypatch, caplog
) -> None:
    _ensure_alert_migration(db_engine)
    _seed_bounded_fx(db_engine)
    sentinel = "SENSITIVE_COMMIT_SQL_BIND_TEST_SECRET"
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_commit_failure_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(
            shop_pk=shop.id, spu_id="TEST_commit_failure_spu", status="active"
        )
        session.add(spu)
        session.flush()
        old_rows = []
        for offset in (1, 2, 3):
            old_row = SpuDeteriorationAlert(
                shop_pk=shop.id,
                spu_pk=spu.id,
                anchor_date=datetime.now(UTC).date() - timedelta(days=offset),
                window_days=1,
                layer="fast",
                severity="none",
                state="stable",
                sample_status="sufficient",
                effective_config_source="seed_fallback",
                config_payload_hash="0" * 64,
                basis_calculated_at=datetime.now(UTC),
            )
            old_rows.append(old_row)
        session.add_all(old_rows)
        session.flush()
        shop_pk = shop.id
        old_ids = {row.id for row in old_rows}
        session.commit()
        session.rollback()

        original_commit = session.commit
        commit_calls = 0

        def fail_first_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise SQLAlchemyError(sentinel)
            original_commit()

        monkeypatch.setattr(session, "commit", fail_first_commit)
        with caplog.at_level("ERROR"):
            result = run_scheduled(session)

    assert result["status"] == "failed"
    assert result["phase"] == "finalize"
    assert sentinel not in str(result)
    assert sentinel not in caplog.text
    with Session(db_engine) as session:
        retained_ids = set(
            session.scalars(
                select(SpuDeteriorationAlert.id).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
        )
        assert old_ids <= retained_ids
        failure_job = session.scalar(
            select(SyncJob)
            .where(SyncJob.job_name == "analytics.spu_profit_deterioration_alert")
            .order_by(SyncJob.id.desc())
        )
        assert failure_job is not None
        assert failure_job.status == "failed"
        assert sentinel not in str(failure_job.extra)
    try:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()
    finally:
        _delete_bounded_fx(db_engine)


def test_read_phase_rollback_failure_is_sanitized(
    db_engine, monkeypatch, caplog
) -> None:
    """The transition rollback after a successful read must stay sanitized.

    It sits between the sanitized read path and the write path; an escaping
    exception would carry statement/bind text into the scheduler audit row.
    """
    _ensure_alert_migration(db_engine)
    _seed_bounded_fx(db_engine)
    sentinel = "SENSITIVE_ROLLBACK_SQL_BIND_TEST_SECRET"
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_rollback_failure_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(
            shop_pk=shop.id, spu_id="TEST_rollback_failure_spu", status="active"
        )
        session.add(spu)
        session.flush()
        old_row = SpuDeteriorationAlert(
            shop_pk=shop.id,
            spu_pk=spu.id,
            anchor_date=date(2026, 10, 10),
            window_days=1,
            layer="fast",
            severity="none",
            state="stable",
            sample_status="sufficient",
            effective_config_source="seed_fallback",
            config_payload_hash="0" * 64,
            basis_calculated_at=datetime.now(UTC),
        )
        session.add(old_row)
        session.flush()
        shop_pk, old_id = shop.id, old_row.id
        session.commit()
        session.rollback()

        original_rollback = session.rollback
        rollback_calls = 0

        def fail_transition_rollback() -> None:
            nonlocal rollback_calls
            rollback_calls += 1
            if rollback_calls == 1:
                raise OperationalError(
                    "ROLLBACK",
                    {"bind:shop_pk": sentinel},
                    Exception(sentinel),
                )
            original_rollback()

        monkeypatch.setattr(session, "rollback", fail_transition_rollback)
        with caplog.at_level("ERROR"):
            result = run_scheduled(session)
    try:
        assert rollback_calls >= 2
        assert result["status"] == "failed"
        assert result["phase"] == "rollback"
        assert result["error"] == "rollback:OperationalError"
        assert sentinel not in str(result)
        assert sentinel not in caplog.text
        with Session(db_engine) as session:
            retained = session.get(SpuDeteriorationAlert, old_id)
            assert retained is not None
            assert retained.shop_pk == shop_pk
            assert retained.anchor_date == date(2026, 10, 10)
            failure_job = session.scalar(
                select(SyncJob)
                .where(SyncJob.job_name == "analytics.spu_profit_deterioration_alert")
                .order_by(SyncJob.id.desc())
            )
            assert failure_job is not None
            assert failure_job.status == "failed"
            assert sentinel not in str(failure_job.extra)
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()
        _delete_bounded_fx(db_engine)


def test_run_scheduled_materializes_and_replaces_idempotently(db_engine) -> None:
    _ensure_alert_migration(db_engine)
    _seed_bounded_fx(db_engine)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_alert_job_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        session.add(
            ChannelProduct(
                shop_pk=shop.id, spu_id="TEST_alert_job_spu", status="active"
            )
        )
        session.commit()
        shop_pk = shop.id
    try:
        with Session(db_engine) as session:
            first = run_scheduled(session)
            assert first["status"] == "success"
            first_count = session.scalar(
                select(SpuDeteriorationAlert.id)
                .where(SpuDeteriorationAlert.shop_pk == shop_pk)
                .order_by(SpuDeteriorationAlert.id.desc())
                .limit(1)
            )
            assert first_count is not None
        with Session(db_engine) as session:
            second = run_scheduled(session)
            assert second["status"] == "success"
            rows = session.scalars(
                select(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            ).all()
            assert len(rows) > 0
            assert second["rows"] == first["rows"]
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()
        _delete_bounded_fx(db_engine)


def test_published_rollout_fails_closed_without_replacing_rows(db_engine) -> None:
    _ensure_alert_migration(db_engine)
    _seed_bounded_fx(db_engine)
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
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
        session.add(
            RuntimeConfigItem(
                config_key=ALERT_CONFIG_KEY,
                display_name="TEST published rollout alert",
                json_schema={"type": "object"},
                draft_payload=payload,
                draft_rollout=[],
                draft_version=1,
                published_version=1,
            )
        )
        session.add(
            RuntimeConfigRevision(
                config_key=ALERT_CONFIG_KEY,
                version=1,
                payload=payload,
                rollout=[
                    {
                        "name": "canary",
                        "basisPoints": 10000,
                        "payload": payload,
                    }
                ],
                created_by="TEST_legacy_rollout",
            )
        )
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_rollout_job_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(
            shop_pk=shop.id, spu_id="TEST_rollout_job_spu", status="active"
        )
        session.add(spu)
        session.flush()
        old_row = SpuDeteriorationAlert(
            shop_pk=shop.id,
            spu_pk=spu.id,
            anchor_date=date(2026, 10, 10),
            window_days=1,
            layer="fast",
            severity="none",
            state="stable",
            sample_status="sufficient",
            effective_config_source="runtime_config",
            effective_config_version=1,
            config_payload_hash="0" * 64,
            basis_calculated_at=datetime.now(UTC),
        )
        session.add(old_row)
        session.commit()
        shop_pk, spu_pk, old_id = shop.id, spu.id, old_row.id
    try:
        with Session(db_engine) as session:
            assert run_scheduled(session)["status"] == "failed"
        with Session(db_engine) as session:
            retained = session.get(SpuDeteriorationAlert, old_id)
            assert retained is not None
            assert retained.shop_pk == shop_pk
            assert retained.anchor_date == date(2026, 10, 10)
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
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
            session.execute(delete(ChannelProduct).where(ChannelProduct.id == spu_pk))
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()
        _delete_bounded_fx(db_engine)


def test_real_constraint_failure_rolls_back_replaced_snapshot(db_engine) -> None:
    _ensure_alert_migration(db_engine)
    anchor = date(2026, 10, 10)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok", shop_id="TEST_alert_rollback_shop", status="active"
        )
        session.add(shop)
        session.flush()
        spu = ChannelProduct(
            shop_pk=shop.id, spu_id="TEST_alert_rollback_spu", status="active"
        )
        session.add(spu)
        session.flush()
        old_row = SpuDeteriorationAlert(
            shop_pk=shop.id,
            spu_pk=spu.id,
            anchor_date=anchor,
            window_days=1,
            layer="fast",
            severity="none",
            state="stable",
            sample_status="sufficient",
            effective_config_source="seed_fallback",
            config_payload_hash="0" * 64,
            basis_calculated_at=datetime.now(UTC),
        )
        session.add(old_row)
        session.commit()
        shop_pk, old_id = shop.id, old_row.id
    try:
        with Session(db_engine) as session:
            invalid_row = SpuDeteriorationAlert(
                shop_pk=shop_pk,
                spu_pk=999999999999,
                anchor_date=anchor,
                window_days=1,
                layer="fast",
                severity="none",
                state="stable",
                sample_status="sufficient",
                effective_config_source="seed_fallback",
                config_payload_hash="0" * 64,
                basis_calculated_at=datetime.now(UTC),
            )
            with pytest.raises(IntegrityError):
                replace_anchor(session, rows=[invalid_row], anchor_date=anchor)
            session.rollback()
        with Session(db_engine) as session:
            retained = session.get(SpuDeteriorationAlert, old_id)
            assert retained is not None
            assert retained.shop_pk == shop_pk
            assert retained.anchor_date == anchor
    finally:
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()


def test_real_canonical_queries_batch_more_than_500_scope(
    db_engine, monkeypatch
) -> None:
    _ensure_alert_migration(db_engine)
    _seed_bounded_fx(db_engine)
    seen: list[tuple[set[str], str, str]] = []
    sql_key_observations: list[tuple[str, set[int], set[int]]] = []
    formula_observations: dict[int, list[tuple[Any, Any]]] = {}
    active_pk_by_external: dict[str, int] = {}
    fact_product_pks: set[int] = set()
    current_batch_pks: set[int] | None = None
    current_families: set[str] | None = None
    current_formula_pks: list[int] = []
    current_formula_index = 0
    original_query = _implementation._query_spu_roi
    original_calculate = _implementation.calculate

    def capture_sql(_connection, _cursor, statement, parameters, context, _executemany):
        family = _fact_family(statement, parameters, context)
        if family is None or current_batch_pks is None:
            return
        parameter_name = (
            "pks"
            if family in {"cost", "refund_scope", "order_scope"}
            else "selected_pks"
        )
        selected_pks = _numeric_parameter(parameters, parameter_name, context)
        assert selected_pks is not None, (
            f"missing numeric {parameter_name} for {family} fact statement"
        )
        _assert_fact_keys(family, selected_pks, current_batch_pks)
        if current_families is not None:
            current_families.add(family)
        sql_key_observations.append((family, selected_pks, set(current_batch_pks)))

    def capture_calculate(inputs):
        nonlocal current_formula_index
        output = original_calculate(inputs)
        if current_batch_pks is not None and current_formula_pks:
            pk = current_formula_pks[current_formula_index % len(current_formula_pks)]
            formula_observations.setdefault(pk, []).append((inputs, output))
            current_formula_index += 1
        return output

    def observed_query(session, **kwargs):
        nonlocal \
            current_batch_pks, \
            current_families, \
            current_formula_pks, \
            current_formula_index
        selected = set(kwargs["selection"].spu_ids)
        current_batch_pks = {
            active_pk_by_external[external_id] for external_id in selected
        }
        current_families = set()
        current_formula_pks = sorted(current_batch_pks & fact_product_pks)
        current_formula_index = 0
        isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
        read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
        seen.append((selected, isolation, read_only))
        try:
            result = original_query(session, **kwargs)
            assert current_formula_index >= len(current_formula_pks)
            assert {
                "ad",
                "sales",
                "full_loss",
                "row_status",
                "refund",
                "projection",
                "cost",
            } <= current_families
            return result
        finally:
            current_batch_pks = None
            current_families = None
            current_formula_pks = []
            current_formula_index = 0

    event.listen(db_engine, "before_cursor_execute", capture_sql)
    monkeypatch.setattr(_implementation, "_query_spu_roi", observed_query)
    monkeypatch.setattr(_implementation, "calculate", capture_calculate)
    anchor = datetime.now(UTC).astimezone(
        ZoneInfo("Asia/Ho_Chi_Minh")
    ).date() - timedelta(days=2)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id="TEST_alert_batch_shop",
            region="VN",
            status="active",
        )
        session.add(shop)
        session.flush()
        products = [
            ChannelProduct(
                shop_pk=shop.id,
                spu_id=f"TEST_BULK_SPU_{index}",
                status="ACTIVATE",
            )
            for index in range(501)
        ]
        session.add_all(products)
        session.flush()
        # Index 3 is deliberately unseeded; the other seven products provide
        # paired baseline, unsettled, settled, return/refund, and cancellation
        # cases while retaining a >500 active catalog.
        fact_indexes = (0, 1, 2, 99, 100, 499, 500)
        for index in fact_indexes:
            spu_id = f"TEST_BULK_SPU_{index}"
            _insert_bounded_ad(
                session, seller=shop.shop_id, spu_id=spu_id, anchor=anchor
            )
            order_pk = _insert_bounded_order(
                session,
                shop_pk=shop.id,
                spu_pk=products[index].id,
                key=str(index),
                anchor=anchor,
                status="CANCELLED"
                if index == 500
                else "IN_TRANSIT"
                if index in (1, 2, 100, 499)
                else "DELIVERED",
            )
            if index == 100:
                _insert_bounded_refund_case(
                    session, order_pk=order_pk, key=str(index), case_type="REFUND_ONLY"
                )
            elif index == 499:
                _insert_bounded_refund_case(
                    session,
                    order_pk=order_pk,
                    key=str(index),
                    case_type="RETURN_AND_REFUND",
                )
            elif index == 99:
                _insert_bounded_settlement(session, order_pk=order_pk, key=str(index))
                _insert_bounded_refund_case(
                    session, order_pk=order_pk, key=str(index), case_type="REFUND_ONLY"
                )
            elif index == 500:
                _insert_bounded_refund_case(
                    session, order_pk=order_pk, key=str(index), case_type="CANCELLATION"
                )
            elif index in (0, 1, 2):
                if index == 0:
                    _insert_bounded_settlement(
                        session, order_pk=order_pk, key=str(index)
                    )
            else:
                raise AssertionError(f"unexpected fact index {index}")
        session.commit()
        shop_pk = shop.id
        product_ids = {product.id for product in products}
        active_pk_by_external = {product.spu_id: product.id for product in products}
        fact_product_pks = {products[index].id for index in fact_indexes}
    try:
        with Session(db_engine) as session:
            result = run_scheduled(session)
            assert result["status"] == "success"
        assert seen
        assert max(len(selected) for selected, _, _ in seen) <= 100
        assert len([selected for selected, _, _ in seen if len(selected) == 100]) >= 5
        assert all(isolation == "repeatable read" for _, isolation, _ in seen)
        assert all(read_only == "on" for _, _, read_only in seen)
        expected_families = {
            "ad",
            "sales",
            "full_loss",
            "row_status",
            "refund",
            "projection",
            "cost",
            "refund_scope",
            "order_scope",
        }
        assert sql_key_observations
        assert {family for family, _, _ in sql_key_observations} == expected_families
        core_families = {
            "ad",
            "sales",
            "full_loss",
            "row_status",
            "refund",
            "projection",
            "cost",
        }
        for family in expected_families:
            family_observations = [
                observation
                for observation in sql_key_observations
                if observation[0] == family
            ]
            assert family_observations
            if family in core_families:
                assert len(family_observations) >= len(seen)
            assert all(
                selected_pks <= active_batch
                for _, selected_pks, active_batch in family_observations
            )
        with pytest.raises(AssertionError):
            _assert_fact_keys("sales", {999999999}, {1, 2, 3})
        with pytest.raises(AssertionError):
            _assert_fact_keys("sales", set(range(101)), set(range(101)))

        with Session(db_engine) as session:
            rows = session.scalars(
                select(SpuDeteriorationAlert)
                .where(SpuDeteriorationAlert.shop_pk == shop_pk)
                .order_by(
                    SpuDeteriorationAlert.spu_pk,
                    SpuDeteriorationAlert.window_days,
                    SpuDeteriorationAlert.layer,
                )
            ).all()
            assert len(rows) == 501 * 6
            assert {row.spu_pk for row in rows} == product_ids
            assert len({row.basis_calculated_at for row in rows}) == 1
            assert len({row.effective_config_source for row in rows}) == 1
            assert len({row.effective_config_version for row in rows}) == 1
            assert len({row.config_payload_hash for row in rows}) == 1
            keys = [(row.spu_pk, row.window_days, row.layer) for row in rows]
            assert keys == sorted(keys)
            unseeded = [row for row in rows if row.spu_pk == products[3].id]
            assert len(unseeded) == 6
            assert all(row.sample_status == "unavailable" for row in unseeded)
            persisted_current = {}
            for index in (1, 100, 499):
                current_rows = [
                    row
                    for row in rows
                    if row.spu_pk == products[index].id
                    and row.window_days == 1
                    and row.layer == "fast"
                ]
                assert len(current_rows) == 1
                persisted_current[index] = current_rows[0]
            assert len({row.anchor_date for row in persisted_current.values()}) == 1

        expected_formula_fields = {
            products[0].id: (Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0),
            products[1].id: (Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0),
            products[2].id: (Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0),
            products[99].id: (Decimal(0), Decimal(526600), Decimal(0), Decimal(0), 1),
            products[100].id: (
                Decimal(526600),
                Decimal(526600),
                Decimal(0),
                Decimal(0),
                1,
            ),
            products[499].id: (
                Decimal(526600),
                Decimal(0),
                Decimal(526600),
                Decimal(0),
                1,
            ),
            products[500].id: (Decimal(0), Decimal(0), Decimal(0), Decimal(526600), 0),
        }

        def matching_formula(
            pk: int, expected: tuple[Decimal, Decimal, Decimal, Decimal, int]
        ) -> tuple[Any, Any] | None:
            observations = formula_observations.get(pk)
            assert observations
            return next(
                (
                    (inputs, output)
                    for inputs, output in observations
                    if (
                        inputs.confirmed_unsettled_refund_vnd,
                        inputs.refund_only_vnd,
                        inputs.refund_return_vnd,
                        inputs.refund_cancelled_vnd,
                        inputs.full_loss_qty,
                    )
                    == expected
                ),
                None,
            )

        matched_optional = {
            pk: matching_formula(pk, expected)
            for pk, expected in expected_formula_fields.items()
        }
        assert all(value is not None for value in matched_optional.values())
        matched = {
            pk: value for pk, value in matched_optional.items() if value is not None
        }
        for pk, expected in expected_formula_fields.items():
            inputs, _output = matched[pk]
            assert (
                inputs.confirmed_unsettled_refund_vnd,
                inputs.refund_only_vnd,
                inputs.refund_return_vnd,
                inputs.refund_cancelled_vnd,
                inputs.full_loss_qty,
            ) == expected

        baseline_inputs, _baseline_output = matched[products[1].id]
        expected_order_vnd = Decimal(526600)
        expected_usd_vnd = Decimal(26330)
        expected_usd_cny = Decimal("6.7686473")
        expected_ad_spend_usd = Decimal(10)
        expected_fee_rate = Decimal("0.308")
        expected_unit_cost_cny = Decimal(40)
        assert baseline_inputs.usd_vnd == expected_usd_vnd
        assert baseline_inputs.usd_cny == expected_usd_cny
        assert baseline_inputs.unsettled_fee_rate == expected_fee_rate

        vnd_per_cny = expected_usd_vnd / expected_usd_cny
        spend_cny = expected_ad_spend_usd * expected_usd_cny
        baseline_net = (
            expected_order_vnd * (Decimal(1) - expected_fee_rate) / vnd_per_cny
            - expected_unit_cost_cny
            - spend_cny
        )
        refund_net = (
            (expected_order_vnd - expected_order_vnd)
            * (Decimal(1) - expected_fee_rate)
            / vnd_per_cny
            - expected_unit_cost_cny
            - spend_cny
        )
        baseline_roi = (
            expected_order_vnd * (Decimal(1) - expected_fee_rate) / vnd_per_cny
        ) / spend_cny
        refund_roi = -expected_unit_cost_cny / spend_cny

        def persisted_money(value: Decimal) -> Decimal:
            return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        def persisted_ratio(value: Decimal) -> Decimal:
            return value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)

        assert persisted_current[1].current_net_profit_cny == persisted_money(
            baseline_net
        )
        assert persisted_current[100].current_net_profit_cny == persisted_money(
            refund_net
        )
        assert persisted_current[499].current_net_profit_cny == persisted_money(
            refund_net
        )
        assert persisted_current[1].current_roi == persisted_ratio(baseline_roi)
        assert persisted_current[100].current_roi == persisted_ratio(refund_roi)
        assert persisted_current[499].current_roi == persisted_ratio(refund_roi)
    finally:
        event.remove(db_engine, "before_cursor_execute", capture_sql)
        with Session(db_engine) as session:
            session.execute(
                delete(SpuDeteriorationAlert).where(
                    SpuDeteriorationAlert.shop_pk == shop_pk
                )
            )
            session.execute(
                text(
                    "DELETE FROM after_sales.case_lines WHERE case_id IN "
                    "(SELECT id FROM after_sales.cases WHERE shop_pk = :shop)"
                ),
                {"shop": shop_pk},
            )
            session.execute(
                text("DELETE FROM after_sales.cases WHERE shop_pk = :shop"),
                {"shop": shop_pk},
            )
            session.execute(
                text(
                    "DELETE FROM finance.settlement_components WHERE transaction_id IN "
                    "(SELECT id FROM finance.settlement_transactions WHERE order_pk IN "
                    "(SELECT id FROM commerce.sales_orders WHERE shop_pk = :shop))"
                ),
                {"shop": shop_pk},
            )
            session.execute(
                text(
                    "DELETE FROM finance.settlement_transactions WHERE order_pk IN "
                    "(SELECT id FROM commerce.sales_orders WHERE shop_pk = :shop)"
                ),
                {"shop": shop_pk},
            )
            session.execute(
                text(
                    "DELETE FROM finance.settlement_statements WHERE external_statement_id LIKE 'TEST_BULK_%'"
                )
            )
            session.execute(
                text(
                    "DELETE FROM commerce.sales_order_lines WHERE order_pk IN "
                    "(SELECT id FROM commerce.sales_orders WHERE shop_pk = :shop)"
                ),
                {"shop": shop_pk},
            )
            session.execute(
                text("DELETE FROM commerce.sales_orders WHERE shop_pk = :shop"),
                {"shop": shop_pk},
            )
            session.execute(
                text("DELETE FROM plugin.ad_daily WHERE seller_id = :seller"),
                {"seller": "TEST_alert_batch_shop"},
            )
            session.execute(
                delete(ChannelProduct).where(ChannelProduct.shop_pk == shop_pk)
            )
            session.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            session.commit()
        _delete_bounded_fx(db_engine)


def test_materialize_alerts_uses_anchor_replacement_seam(monkeypatch) -> None:
    session = MagicMock()
    replacement = MagicMock(return_value=2)
    monkeypatch.setattr(
        "tts_erp_v2.jobs.spu_deterioration_alert.replace_anchor", replacement
    )
    result = materialize_alerts(session, anchor_date=date(2026, 10, 10), rows=[])
    replacement.assert_called_once_with(
        session, rows=[], anchor_date=date(2026, 10, 10)
    )
    assert result == {"status": "success", "anchorDate": "2026-10-10", "rows": 2}
