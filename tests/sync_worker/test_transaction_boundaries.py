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
                running = observer.scalar(
                    select(SyncJob).where(SyncJob.job_name == name)
                )
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
            assert (
                session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name))
                is None
            )
            assert (
                session.scalar(select(SyncJob.status).where(SyncJob.job_name == name))
                == "failed"
            )


def test_previously_completed_unit_survives_failure_and_is_reported_failed(db_engine):
    name = "TEST_SHORT_TRANSACTION_REPLAY"
    with Session(db_engine) as session:

        def inner(business_session):
            with business_session.begin():
                business_session.add(
                    RawRecord(endpoint=name, payload={"complete": True})
                )
            assert not business_session.in_transaction()
            raise RuntimeError("TEST_NEXT_PAGE_UNAVAILABLE")

        with pytest.raises(RuntimeError, match="TEST_NEXT_PAGE_UNAVAILABLE"):
            run_with_sync_job(session, job_name=name, inner=inner)
        with session.begin():
            assert (
                session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name))
                is not None
            )
            assert (
                session.scalar(select(SyncJob.status).where(SyncJob.job_name == name))
                == "failed"
            )


def test_token_refresh_cannot_commit_an_active_business_transaction(db_engine):
    name = "TEST_TOKEN_UNRELATED_TRANSACTION"
    calls = []
    with Session(db_engine) as session:
        with pytest.raises(InvalidRequestError):
            with session.begin():
                session.add(RawRecord(endpoint=name, payload={"incomplete": True}))
                refresh_if_needed(
                    session,
                    provider="tiktok",
                    external_account_id=name,
                    refresher=lambda *args: calls.append(args),
                    force=True,
                )
        assert calls == []
        with session.begin():
            assert (
                session.scalar(select(RawRecord.id).where(RawRecord.endpoint == name))
                is None
            )


@pytest.mark.parametrize(
    "module_name",
    ["orders", "order_detail", "products", "logistics", "after_sales", "finance"],
)
def test_tiktok_upstream_is_called_without_active_transaction(
    db_engine, monkeypatch, module_name
):
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
            monkeypatch.setattr(
                module,
                "_select_tracking_targets",
                lambda *a, **k: [
                    {
                        "order_id": name,
                        "external_package_id": name,
                        "tracking_number": name,
                        "provider_id": None,
                        "provider_name": None,
                    }
                ],
            )
        calls = []

        def proxy(*args, **kwargs):
            assert not session.in_transaction()
            calls.append(args)
            return {"code": 0, "data": {}}

        kwargs = {"order_ids": [name]} if module_name == "order_detail" else {}
        module.run(session, proxy_call=proxy, shop_id=name, **kwargs)
        assert calls
        assert not session.in_transaction()


def test_older_failed_order_cannot_be_skipped_by_newer_success(db_engine):
    from tts_erp_v2.jobs.tiktok.orders import run
    from tts_erp_v2.sync_worker import watermarks

    name = "TEST_CURSOR_MIXED_FAILURE"
    with Session(db_engine) as session:
        with session.begin():
            session.add(ChannelAccount(platform="tiktok", shop_id=name))
            watermarks.set_cursor(
                session, job_name="tiktok.orders", scope=name, cursor_epoch_ms=1000
            )

        def proxy(*args, **kwargs):
            assert not session.in_transaction()
            return {
                "code": 0,
                "data": {
                    "orders": [
                        {"update_time": 2},
                        {
                            "id": name,
                            "order_status": "COMPLETED",
                            "currency": "VND",
                            "update_time": 3,
                        },
                    ]
                },
            }

        result = run(session, proxy_call=proxy, shop_id=name)
        assert result.rows_failed > 0
        assert result.rows_inserted == 1
        assert result.cursor is None
        with session.begin():
            assert (
                watermarks.get_cursor(session, job_name="tiktok.orders", scope=name)
                == 1000
            )


def test_refresh_uses_exact_snapshot_and_cannot_overwrite_new_authorization(db_engine):
    from tts_erp_v2.proxy.token_service import load_credentials, upsert_credentials

    name = "TEST_REFRESH_SNAPSHOT_CAS"
    with Session(db_engine) as session:
        with session.begin():
            upsert_credentials(
                session,
                provider="tiktok",
                external_account_id=name,
                plaintext_access_token="TEST_OLD_ACCESS",
                plaintext_refresh_token="TEST_OLD_REFRESH",
            )

        def refresh(view):
            assert not session.in_transaction()
            assert view.refresh_token == "TEST_OLD_REFRESH"
            with Session(db_engine) as callback, callback.begin():
                upsert_credentials(
                    callback,
                    provider="tiktok",
                    external_account_id=name,
                    plaintext_access_token="TEST_NEW_AUTH",
                    plaintext_refresh_token="TEST_NEW_AUTH_REFRESH",
                )
            return {
                "access_token": "TEST_OLD_ROTATION",
                "refresh_token": "TEST_OLD_ROTATION_REFRESH",
            }

        view = refresh_if_needed(
            session,
            provider="tiktok",
            external_account_id=name,
            refresher=refresh,
            force=True,
        )
        assert not session.in_transaction()
        assert view.access_token == "TEST_NEW_AUTH"
        with session.begin():
            assert (
                load_credentials(session, "tiktok", name).refresh_token
                == "TEST_NEW_AUTH_REFRESH"
            )


def test_token_commit_failure_reaches_one_shot_exit_code(db_engine, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import sessionmaker

    from tts_erp_v2.proxy.token_service import upsert_credentials
    from tts_erp_v2.sync_worker.main import _run_one_job

    name = "TEST_TOKEN_COMMIT_FAILURE"
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    with factory() as seed, seed.begin():
        upsert_credentials(
            seed,
            provider="tiktok",
            external_account_id=name,
            plaintext_access_token="TEST_OLD",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    state = {"fail_next_commit": False}

    def refresh(view):
        state["fail_next_commit"] = True
        return {"access_token": "TEST_ROTATED"}

    def before_commit(session):
        if state["fail_next_commit"]:
            state["fail_next_commit"] = False
            raise SQLAlchemyError("TEST_CREDENTIAL_COMMIT_FAILED")

    event.listen(Session, "before_commit", before_commit)
    monkeypatch.setattr("tts_erp_v2.db.base.get_session_factory", lambda: factory)
    monkeypatch.setattr(
        "tts_erp_v2.proxy.tiktok_auth.build_token_registry",
        lambda **kwargs: lambda *args: refresh,
    )
    try:
        assert _run_one_job("token.refresh") == 1
    finally:
        event.remove(Session, "before_commit", before_commit)
    with factory() as observer:
        latest = observer.scalar(
            select(SyncJob)
            .where(SyncJob.job_name == "token.refresh")
            .order_by(SyncJob.id.desc())
        )
        assert latest.status == "failed"


@pytest.mark.parametrize(
    "module_name,entrypoint",
    [
        ("shops", "sync_shops"),
        ("collect_box", "sync_collect_box"),
        ("common_collect_box", "sync_common_collect_box"),
        ("move_collect", "sync_move_collect"),
        ("packages", "sync_packages"),
    ],
)
def test_miaoshou_network_releases_shared_account_lock(
    db_engine, monkeypatch, module_name, entrypoint
):
    from sqlalchemy import text

    from tts_erp_v2.proxy.token_service import upsert_credentials

    module = importlib.import_module(f"tts_erp_v2.jobs.miaoshou.{module_name}")
    name = f"TEST_MIAOSHOU_SHORT_{module_name}"
    calls = []
    with Session(db_engine) as session:
        with session.begin():
            upsert_credentials(
                session,
                provider="miaoshou",
                external_account_id=name,
                plaintext_access_token="TEST_LICENSE",
                plaintext_refresh_token="TEST_SECRET",
            )

        class Client:
            def _call_erp(self, **kwargs):
                assert not session.in_transaction()
                # Another connection can acquire the SAME account lock while
                # the simulated network request is in progress.
                with Session(db_engine) as observer, observer.begin():
                    assert (
                        observer.scalar(
                            text(
                                "SELECT id FROM procurement.procurement_accounts "
                                "WHERE external_account_id=:name FOR UPDATE NOWAIT"
                            ),
                            {"name": name},
                        )
                        is not None
                    )
                calls.append(kwargs)
                return {"result": "success", "data": {}}

        if module_name == "shops":
            monkeypatch.setattr(
                "tts_erp_v2.jobs.miaoshou._common.miaoshou_client_factory",
                lambda ctx: Client(),
            )
            getattr(module, entrypoint)(session, license_id=name)
        else:
            getattr(module, entrypoint)(session, client=Client(), license_id=name)
        assert calls
        assert not session.in_transaction()


def test_miaoshou_purchase_http_has_no_active_transaction(db_engine):
    from tts_erp_v2.jobs.miaoshou.purchase_price_clean import sync_purchase_prices
    from tts_erp_v2.proxy.token_service import upsert_credentials

    name = "TEST_PURCHASE_HTTP_TRANSACTION"
    with Session(db_engine) as session:
        with session.begin():
            upsert_credentials(
                session,
                provider="miaoshou_web",
                external_account_id=name,
                plaintext_access_token="TEST_COOKIE",
                plaintext_refresh_token="TEST_HEADER",
            )

        class Client:
            def search_page(self, *, page, page_size):
                assert not session.in_transaction()
                return {"list": [], "total": 0, "page": page, "pageSize": page_size}

        result = sync_purchase_prices(
            session, client=Client(), external_account_id=name
        )
        assert not result["skipped"]
        assert not session.in_transaction()


def test_image_download_and_upload_run_outside_transactions(db_engine):
    from tts_erp_v2.db.models import ChannelProduct
    from tts_erp_v2.jobs.tiktok.spu_image_mirror import run

    name = "TEST_MIRROR_HTTP_TRANSACTION"
    calls = []
    with Session(db_engine, expire_on_commit=False) as session:
        with session.begin():
            account = ChannelAccount(platform="tiktok", shop_id=name)
            session.add(account)
            session.flush()
            product = ChannelProduct(
                shop_pk=account.id,
                spu_id=name,
                main_image_url="https://example.test/TEST_image.jpg",
            )
            session.add(product)
            session.flush()
            spu_pk = product.id

        def download(url):
            assert not session.in_transaction()
            calls.append("download")
            return b"TEST_IMAGE", "image/jpeg"

        class Store:
            def ensure_bucket(self):
                assert not session.in_transaction()

            def put_bytes(self, *args):
                assert not session.in_transaction()
                calls.append("upload")

        run(session, spu_pks=[spu_pk], downloader=download, minio=Store())
        assert calls == ["download", "upload"]
        assert not session.in_transaction()


@pytest.mark.parametrize(
    "kind", ["products", "after_sales", "logistics", "logistics_nonzero", "finance"]
)
def test_failed_required_child_cannot_be_skipped_by_later_success(
    db_engine, monkeypatch, kind
):
    from tts_erp_v2.sync_worker import watermarks

    module_name = "logistics" if kind == "logistics_nonzero" else kind
    module = importlib.import_module(f"tts_erp_v2.jobs.tiktok.{module_name}")
    name = f"TEST_CHILD_CURSOR_{kind}"
    cursor_job = "tiktok.finance.statements" if kind == "finance" else module.JOB_NAME
    with Session(db_engine, expire_on_commit=False) as session:
        with session.begin():
            account = ChannelAccount(platform="tiktok", shop_id=name)
            session.add(account)
            session.flush()
            for suffix in ("bad", "good"):
                session.add(SalesOrder(shop_pk=account.id, order_id=f"{name}_{suffix}"))
            watermarks.set_cursor(
                session, job_name=cursor_job, scope=name, cursor_epoch_ms=1000
            )
        if module_name == "logistics":
            monkeypatch.setattr(
                module,
                "_select_tracking_targets",
                lambda *a, **k: [
                    {
                        "order_id": f"{name}_{suffix}",
                        "external_package_id": f"{name}_{suffix}",
                        "tracking_number": name,
                        "provider_id": None,
                        "provider_name": None,
                    }
                    for suffix in ("bad", "good")
                ],
            )

        def proxy(method, path, **kwargs):
            assert not session.in_transaction()
            if kind == "products":
                return {
                    "code": 0,
                    "data": {
                        "products": [
                            {"id": f"{name}_bad", "update_time": 2, "skus": [{}]},
                            {"id": f"{name}_good", "update_time": 3, "skus": []},
                        ]
                    },
                }
            if kind == "after_sales":
                return (
                    {
                        "code": 0,
                        "data": {
                            "return_orders": [
                                {
                                    "id": f"{name}_bad",
                                    "order_id": f"{name}_bad",
                                    "update_time": 2,
                                    "return_line_items": [{}],
                                },
                                {
                                    "id": f"{name}_good",
                                    "order_id": f"{name}_good",
                                    "update_time": 3,
                                },
                            ]
                        },
                    }
                    if path == module.RETURNS_ENDPOINT
                    else {"code": 0, "data": {}}
                )
            if module_name == "logistics":
                if path.endswith(f"{name}_bad/tracking") or f"{name}_bad" in path:
                    return (
                        {"code": 42, "message": "TEST_FETCH_FAILED"}
                        if kind.endswith("nonzero")
                        else {"code": 0, "data": {"tracking": [{}]}}
                    )
                return {
                    "code": 0,
                    "data": {
                        "tracking": [
                            {
                                "description": "TEST_NEW_EVENT",
                                "update_time_millis": 3000,
                            }
                        ]
                    },
                }
            if path == module.PAYOUTS_ENDPOINT:
                return {
                    "code": 0,
                    "data": {
                        "payments": [
                            {"payment_id": name, "currency": "USD", "create_time": 3}
                        ]
                    },
                }
            if path == module.STATEMENTS_ENDPOINT:
                return {
                    "code": 0,
                    "data": {
                        "statements": [
                            {
                                "id": f"{name}_bad",
                                "payment_id": name,
                                "statement_time": 2,
                            },
                            {
                                "id": f"{name}_good",
                                "payment_id": name,
                                "statement_time": 3,
                                "currency": "USD",
                            },
                        ]
                    },
                }
            return {
                "code": 0,
                "data": {
                    "statement_transactions": [
                        {
                            "transaction_id": f"{name}_bad_txn"
                            if f"{name}_bad" in path
                            else f"{name}_good_txn",
                            "fee_amount": "1",
                        }
                    ]
                },
            }

        kwargs = {"image_fetcher": lambda **kwargs: {}} if kind == "products" else {}
        result = module.run(session, proxy_call=proxy, shop_id=name, **kwargs)
        assert result.rows_failed > 0
        with session.begin():
            assert (
                watermarks.get_cursor(session, job_name=cursor_job, scope=name) == 1000
            )
