"""Behavioral regressions for job-owned short SQLAlchemy transactions."""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ChannelAccount, RawRecord, SalesOrder, SyncJob
from tts_erp_v2.proxy.token_service import refresh_if_needed
from tts_erp_v2.sync_worker.job_runner import JobResult, run_with_sync_job

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]


def test_running_audit_is_durable_without_holding_business_transaction(db_engine):
    name = "TEST_SHORT_TRANSACTION_VISIBLE"
    with Session(db_engine) as session:
        def inner(business_session):
            assert not business_session.in_transaction()
            with Session(db_engine) as observer:
                running = observer.scalar(select(SyncJob).where(SyncJob.job_name == name))
                assert running is not None
                assert running.status == "running"
            return JobResult(rows_total=1)

        row, _ = run_with_sync_job(session, job_name=name, inner=inner)
        assert not session.in_transaction()
        assert row.status == "succeeded"


def test_failed_business_unit_rolls_back_without_committing_partial_facts(db_engine):
    name = "TEST_SHORT_TRANSACTION_ROLLBACK"
    with Session(db_engine) as session:
        def inner(business_session):
            with business_session.begin():
                business_session.add(RawRecord(endpoint=name, payload={"test": True}))
                business_session.flush()
                raise ValueError("TEST_INCOMPLETE_UNIT")

        with pytest.raises(ValueError, match="TEST_INCOMPLETE_UNIT"):
            run_with_sync_job(session, job_name=name, inner=inner)
        assert not session.in_transaction()
        with session.begin():
            assert session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name)) is None
            assert session.scalar(select(SyncJob.status).where(SyncJob.job_name == name)) == "failed"


def test_previously_completed_unit_survives_failure_and_is_reported_failed(db_engine):
    name = "TEST_SHORT_TRANSACTION_REPLAY"
    with Session(db_engine) as session:
        def inner(business_session):
            with business_session.begin():
                business_session.add(RawRecord(endpoint=name, payload={"complete": True}))
            assert not business_session.in_transaction()
            raise RuntimeError("TEST_NEXT_PAGE_UNAVAILABLE")

        with pytest.raises(RuntimeError, match="TEST_NEXT_PAGE_UNAVAILABLE"):
            run_with_sync_job(session, job_name=name, inner=inner)
        with session.begin():
            assert session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name)) is not None
            assert session.scalar(select(SyncJob.status).where(SyncJob.job_name == name)) == "failed"


def test_token_refresh_cannot_commit_an_active_business_transaction(db_engine):
    name = "TEST_TOKEN_UNRELATED_TRANSACTION"
    calls = []
    with Session(db_engine) as session:
        with pytest.raises(InvalidRequestError):
            with session.begin():
                session.add(RawRecord(endpoint=name, payload={"incomplete": True}))
                refresh_if_needed(
                    session, provider="tiktok", external_account_id=name,
                    refresher=lambda *args: calls.append(args), force=True,
                )
        assert calls == []
        with session.begin():
            assert session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name)) is None


@pytest.mark.parametrize("module_name", ["orders", "order_detail", "products", "logistics", "after_sales", "finance"])
def test_tiktok_upstream_is_called_without_active_transaction(db_engine, monkeypatch, module_name):
    module = importlib.import_module(f"tts_erp_v2.jobs.tiktok.{module_name}")
    name = f"TEST_NO_NETWORK_TRANSACTION_{module_name}"
    with Session(db_engine, expire_on_commit=False) as session:
        with session.begin():
            account = ChannelAccount(platform="tiktok", shop_id=name)
            session.add(account)
            session.flush()
            order = SalesOrder(shop_pk=account.id, order_id=name)
            session.add(order)
        if module_name == "logistics":
            monkeypatch.setattr(module, "_select_tracking_targets", lambda *a, **k: [{
                "order_id": name, "external_package_id": name,
                "tracking_number": name, "provider_id": None, "provider_name": None,
            }])
        calls = []

        def proxy(*args, **kwargs):
            assert not session.in_transaction()
            calls.append(args)
            return {"code": 0, "data": {}}

        kwargs = {"order_ids": [name]} if module_name == "order_detail" else {}
        module.run(session, proxy_call=proxy, shop_id=name, **kwargs)
        assert calls
        assert not session.in_transaction()
