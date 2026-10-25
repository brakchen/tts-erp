"""Restricted test collaborators must not introduce another launch/DB authority."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from scripts._test_db_connections import _process_state, load_run_binding
from tests.support import frontend_p2_live as live

pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]
PROFILE_ENV = "TTS_ERP_TEST_P2_COLLABORATORS"
PROFILE = "remaining-console-v1"


@pytest.fixture
def current():
    value = os.environ["TTS_ERP_DB_URL_TEST"]
    return value, load_run_binding(value)


@pytest.fixture
def launch(monkeypatch):
    from tests.support import spu_price_stats_live as maintained

    process = MagicMock(pid=987654321)
    process.poll.return_value = None
    spawn = MagicMock(return_value=process)
    stop = MagicMock()
    monkeypatch.setattr(live.subprocess, "Popen", spawn)
    monkeypatch.setattr(maintained, "_stop_process_group", stop)
    monkeypatch.setattr(
        live, "_process_state", lambda _pid: (os.getpid(), "TEST_start")
    )
    monkeypatch.setattr(live, "_wait_ready", lambda _child: None)
    return spawn, stop


def test_only_explicit_launch_opt_in_selects_the_fixed_profile(current, launch):
    with live.live_api_server(current[0], collaborators=True):
        pass
    env = launch[0].call_args.kwargs["env"]
    assert env.get(PROFILE_ENV) == PROFILE
    command = launch[0].call_args.args[0]
    assert "tests.support.frontend_p2_live:create_app" in command
    assert launch[1].call_count == 1


def test_default_launch_does_not_inherit_a_profile(current, launch, monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    with live.live_api_server(current[0]):
        pass
    assert PROFILE_ENV not in launch[0].call_args.kwargs["env"]


@pytest.mark.parametrize("option", [1, "true", "TEST_module:factory", {}])
def test_non_boolean_options_refuse_before_spawning(current, launch, option):
    with (
        pytest.raises(ValueError, match="^P2 isolated child identity refused$"),
        live.live_api_server(current[0], collaborators=option),
    ):
        pass
    launch[0].assert_not_called()


def test_environment_builder_strips_even_an_upstream_ambient_profile(
    current, tmp_path, monkeypatch
):
    from tests.support import spu_price_stats_live as maintained

    original = maintained.api_env

    def upstream(value):
        return {**original(value), PROFILE_ENV: PROFILE}

    monkeypatch.setattr(maintained, "api_env", upstream)
    tmp_path.chmod(0o700)
    env = live.child_environment(
        current[0], nonce="a" * 64, attestation=tmp_path / "TEST_receipt"
    )
    assert PROFILE_ENV not in env


@pytest.fixture
def factory(current, tmp_path, monkeypatch):
    from tests.support.spu_price_stats_live import TEST_FERNET_KEY
    from tts_erp_v2 import app as production_app
    from tts_erp_v2.db import base

    value, binding = current
    tmp_path.chmod(0o700)
    parent = os.getppid()
    for name, text in {
        "TTS_ERP_DB_URL": value,
        "TTS_ERP_AUTH_MODE": "enforce",
        "TTS_ERP_TEST_NO_DOTENV": "1",
        "TTS_ERP_FERNET_KEY": TEST_FERNET_KEY,
        live.LAUNCH: "a" * 64,
        live.ATTESTATION: str(tmp_path / "TEST_receipt"),
        live.PARENT_PID: str(parent),
        live.PARENT_START: _process_state(parent)[1],
    }.items():
        monkeypatch.setenv(name, text)
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    order = []
    engine, session_factory = object(), object()
    monkeypatch.setattr(base, "get_engine", lambda: engine)
    monkeypatch.setattr(base, "get_session_factory", lambda: session_factory)
    app = FastAPI()

    def validate(received, actual_engine, actual_factory):
        assert received == binding
        assert actual_engine is engine and actual_factory is session_factory
        order.append("validate")
        return binding.run_db

    monkeypatch.setattr(live, "validate_application", validate)
    monkeypatch.setattr(
        production_app, "build_app", lambda: (order.append("build"), app)[1]
    )
    monkeypatch.setattr(
        live, "write_attestation", lambda *_args: order.append("attest")
    )
    module = ModuleType("tests.support.frontend_test_collaborators")

    def install(actual_app):
        assert actual_app is app
        order.append("install")

        @asynccontextmanager
        async def configured(_outer):
            yield

        return configured

    module.install = MagicMock(side_effect=install)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return app, order, module, base


def test_default_factory_keeps_its_original_validation_sequence(factory):
    _app, order, module, _base = factory
    assert isinstance(live.create_app(), live.NonceApp)
    assert order == ["validate", "build", "validate", "attest"]
    module.install.assert_not_called()


def test_installer_runs_after_original_checks_and_before_a_final_binding_check(
    factory, monkeypatch
):
    _app, order, module, _base = factory
    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    assert isinstance(live.create_app(), live.NonceApp)
    assert order == ["validate", "build", "validate", "install", "validate", "attest"]
    module.install.assert_called_once()


@pytest.mark.parametrize("result", [None, "TEST_arbitrary_callback", 42])
def test_profile_requires_a_lifecycle_not_an_ignored_setup_result(
    factory, monkeypatch, result
):
    _app, order, module, _base = factory
    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    module.install.side_effect = None
    module.install.return_value = result
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    assert "attest" not in order


@pytest.mark.parametrize(
    "profile", ["", "TEST_arbitrary:factory", "true", "remaining-console-v2"]
)
def test_unrecognized_child_profile_is_not_a_module_import_or_app_build(
    factory, monkeypatch, profile
):
    _app, order, module, _base = factory
    monkeypatch.setenv(PROFILE_ENV, profile)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    assert order == []
    module.install.assert_not_called()


def test_missing_proof_prevents_the_installer_and_application(factory, monkeypatch):
    _app, order, module, _base = factory
    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    monkeypatch.delenv("TTS_ERP_TEST_RUN_PROOF")
    with pytest.raises(ValueError):
        live.create_app()
    assert order == []
    module.install.assert_not_called()


def test_setup_failure_is_normalized_and_never_attested(factory, monkeypatch):
    _app, order, module, _base = factory
    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    module.install.side_effect = RuntimeError("TEST_private_failure")
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    assert "attest" not in order


def test_setup_cannot_rebind_the_canonical_application(factory, current, monkeypatch):
    from tests.scripts.test_frontend_p2_live_support import doubles
    from tests.support.frontend_p2_binding import validate_application

    _app, order, module, base = factory
    value, binding = current
    engine, session_factory, _session = doubles(value, binding.run_db)
    monkeypatch.setattr(base, "get_engine", lambda: engine)
    monkeypatch.setattr(base, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(live, "validate_application", validate_application)
    monkeypatch.setenv(PROFILE_ENV, PROFILE)

    original_installer = module.install.side_effect

    def rebind(actual_app):
        engine.url = engine.url.set(database="tts_erp_test_OTHER")
        return original_installer(actual_app)

    module.install.side_effect = rebind
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    assert "attest" not in order
    engine.connect.assert_not_called()


def test_installer_requires_current_run_proof(monkeypatch):
    from tests.support.frontend_test_collaborators import install

    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    monkeypatch.delenv("TTS_ERP_TEST_RUN_PROOF")
    with pytest.raises(ValueError):
        install(FastAPI())


def test_installer_does_not_enable_itself_without_explicit_profile(monkeypatch):
    from tests.support.frontend_test_collaborators import install

    monkeypatch.delenv(PROFILE_ENV, raising=False)
    with pytest.raises(ValueError):
        install(FastAPI())


def test_fixed_profile_only_replaces_collaborators_and_restores_on_shutdown(
    current, monkeypatch
):
    import asyncio

    from fastapi import Request
    from fastapi.testclient import TestClient

    from tests.support.frontend_test_collaborators import install
    from tts_erp_v2.api.v2 import admin, video_publish
    from tts_erp_v2.publishing.artemis_client import (
        ArtemisClient,
        ArtemisTransportError,
    )

    monkeypatch.setenv(PROFILE_ENV, PROFILE)
    original_job = MagicMock(side_effect=AssertionError("Real job must never run"))
    original_device = MagicMock(
        side_effect=AssertionError("Real device must never run")
    )
    monkeypatch.setattr(admin, "run_scheduled_job_once", original_job)
    monkeypatch.setattr(ArtemisClient, "_request", original_device)
    from starlette.applications import Starlette
    from starlette.routing import Mount

    original_db = video_publish.get_session
    inner_events = []

    @asynccontextmanager
    async def original_lifespan(_app):
        inner_events.append("production_startup")
        yield
        inner_events.append("production_shutdown")

    app = FastAPI(lifespan=original_lifespan)
    preserved_dependency = object()
    app.dependency_overrides[object] = preserved_dependency
    configured = install(app)
    # Match the actual launcher: mounted sub-app lifespans do not run.
    outer = Starlette(
        routes=[Mount("/tts", app=app), Mount("/", app=app)], lifespan=configured
    )
    with TestClient(outer):
        assert inner_events == []
        assert admin.run_scheduled_job_once is not original_job
        assert ArtemisClient._request is not original_device
        assert video_publish.get_session is original_db
        assert app.dependency_overrides[object] is preserved_dependency
        assert set(app.dependency_overrides) == {object, video_publish.get_store}
        admin.run_scheduled_job_once("TEST_job", shop_id="TEST_shop")
        state = app.state.frontend_test_collaborators
        assert state.jobs == [("TEST_job", "TEST_shop")]
        device = ArtemisClient("http://127.0.0.1:9")
        devices = asyncio.run(device.list_devices())
        assert [row.get("serial") for row in devices] == ["TEST_device"]
        with pytest.raises(ArtemisTransportError):
            asyncio.run(device._request("POST", "/api/execute"))
        request = Request(
            {
                "type": "http",
                "scheme": "http",
                "server": ("127.0.0.1", 12345),
                "path": "/tts/v2/video-publish/tasks",
                "headers": [(b"host", b"127.0.0.1:12345")],
            }
        )
        wrong_authority = Request(
            {**request.scope, "headers": [(b"host", b"127.0.0.1:12346")]}
        )
        with pytest.raises(ValueError, match="^TEST_UPLOAD_ORIGIN_REFUSED$"):
            app.dependency_overrides[video_publish.get_store](wrong_authority)
        store = app.dependency_overrides[video_publish.get_store](request)
        assert store.bucket == "TEST_video"
        assert app.state.frontend_test_collaborators is state
    assert admin.run_scheduled_job_once is original_job
    assert ArtemisClient._request is original_device
    assert app.dependency_overrides == {object: preserved_dependency}
    original_job.assert_not_called()
    original_device.assert_not_called()
    assert inner_events == []  # Opt-in must not start unrelated production hooks.


def test_controlled_store_is_real_loopback_put_with_exact_cors_and_version():
    import hashlib
    import http.client
    from urllib.parse import urlsplit

    from tests.support.frontend_test_object_store import ControlledObjectStore

    with ControlledObjectStore() as store:
        origin = "http://127.0.0.1:12345"
        store.allow_origin(origin)
        target = urlsplit(store.presign_put("TEST_video.mp4", "video/mp4"))
        assert target.hostname == "127.0.0.1" and target.port
        body = b"TEST_video_bytes"
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            connection.request(
                "OPTIONS",
                target.path,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            options = connection.getresponse()
            options.read()
            assert options.status == 204
            assert options.getheader("Access-Control-Allow-Origin") == origin
            assert options.getheader("Access-Control-Allow-Credentials") is None
            connection.request(
                "PUT",
                target.path,
                body=body,
                headers={"Origin": origin, "Content-Type": "video/mp4"},
            )
            response = connection.getresponse()
            response.read()
            assert response.status == 200
            metadata = store.stat("TEST_video.mp4")
            assert metadata.get("size") == len(body)
            assert metadata.get("sha256") == hashlib.sha256(body).hexdigest()
            assert metadata.get("cookieReceived") is False
        finally:
            connection.close()


@pytest.mark.parametrize(
    "forbidden",
    [
        {"Cookie": "TEST_not_a_real_cookie"},
        {"Authorization": "Bearer TEST_not_a_real_key"},
        {"X-API-Key": "TEST_not_a_real_key"},
        {"X-Requested-With": "tts-erp"},
    ],
)
def test_controlled_receiver_rejects_erp_credentials_before_accepting_bytes(forbidden):
    import http.client
    from urllib.parse import urlsplit

    from tests.support.frontend_test_object_store import ControlledObjectStore
    from tts_erp_v2.storage.minio_client import ObjectNotFound

    with ControlledObjectStore() as store:
        origin = "http://127.0.0.1:12345"
        store.allow_origin(origin)
        target = urlsplit(store.presign_put("TEST_rejected.mp4", "video/mp4"))
        assert target.hostname == "127.0.0.1" and target.port
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        try:
            connection.request(
                "PUT",
                target.path,
                body=b"TEST",
                headers={"Origin": origin, "Content-Type": "video/mp4", **forbidden},
            )
            response = connection.getresponse()
            response.read()
            assert response.status == 403
            with pytest.raises(ObjectNotFound):
                store.stat("TEST_rejected.mp4")
        finally:
            connection.close()


@pytest.mark.parametrize(
    "origin",
    [
        "https://TEST.invalid",
        "http://127.0.0.1:12345/tts",
        "null",
        "http://TEST_user@127.0.0.1:12345",
    ],
)
def test_store_does_not_accept_an_arbitrary_upload_origin(origin):
    from tests.support.frontend_test_object_store import ControlledObjectStore

    with ControlledObjectStore() as store, pytest.raises(ValueError):
        store.allow_origin(origin)


def test_activated_collaborators_recheck_binding_before_any_request(
    current, monkeypatch
):
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from tests.scripts.test_frontend_p2_live_support import doubles
    from tests.support import frontend_test_collaborators as profile
    from tests.support.frontend_test_object_store import ControlledObjectStore
    from tts_erp_v2.db import base

    value, binding = current
    engine, session_factory, _session = doubles(value, binding.run_db)
    monkeypatch.setattr(base, "get_engine", lambda: engine)
    monkeypatch.setattr(base, "get_session_factory", lambda: session_factory)
    monkeypatch.setenv(PROFILE_ENV, PROFILE)

    class RebindingStore(ControlledObjectStore):
        def __enter__(self):
            result = super().__enter__()
            engine.url = engine.url.set(database="tts_erp_test_OTHER")
            return result

    monkeypatch.setattr(profile, "ControlledObjectStore", RebindingStore)
    app = FastAPI()
    outer = Starlette(routes=[Mount("/", app=app)], lifespan=profile.install(app))
    with (
        pytest.raises(ValueError, match="^P2 isolated child identity refused$"),
        TestClient(outer),
    ):
        pytest.fail("Changed runtime binding must not serve any request")
    engine.connect.assert_not_called()
    assert app.dependency_overrides == {}


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_opted_in_real_clone_keeps_auth_sql_and_owned_child_identity(current, prefix):
    from datetime import UTC, datetime
    from urllib.parse import urlsplit
    from uuid import UUID, uuid4

    import httpx
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from tests.support.frontend_p2_accounts import seed_accounts
    from tests.support.frontend_p2_binding import checked_session, verify_attestation
    from tests.support.frontend_p2_canary import (
        require_data_probe,
        seed_and_check_canary,
    )
    from tts_erp_v2.db.models.publishing import PublishWorkerHeartbeat, VideoPublishTask
    from tts_erp_v2.sync_worker.scheduler import JOBS

    value, binding = current
    marker = "TEST_hook_" + uuid4().hex
    with live.live_api_server(value, collaborators=True) as child:
        probe = seed_and_check_canary(child, prefix=prefix)
        account = next(row for row in seed_accounts(probe) if row.role_code == "admin")
        require_data_probe(probe)
        engine = create_engine(value)
        try:
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with checked_session(binding, engine, factory) as session:
                child.require_live()
                session.add(
                    PublishWorkerHeartbeat(
                        instance_id=marker,
                        hostname="TEST_host",
                        pid=12345,
                        status="ready",
                        device_status="ready",
                        started_at=datetime.now(UTC),
                        heartbeat_at=datetime.now(UTC),
                    )
                )
                session.commit()

            def owned(response, status):
                child.require_live()
                verify_attestation(
                    child.attestation,
                    binding,
                    nonce=child.nonce,
                    child_pid=child.pid,
                    child_start=child.start,
                    http_nonce=response.headers.get(live.NONCE_HEADER),
                )
                assert response.status_code == status
                return response

            with httpx.Client(
                base_url=child.base, trust_env=False, timeout=5
            ) as client:
                owned(client.get(prefix + "/v2/video-publish/devices"), 401)
                owned(
                    client.post(
                        prefix + "/v2/auth/login",
                        json={
                            "username": account.username,
                            "password": account.password,
                        },
                    ),
                    200,
                )
                me = owned(client.get(prefix + "/v2/auth/me"), 200).json()
                assert me["authenticated"] and me["username"] == account.username
                devices = owned(
                    client.get(prefix + "/v2/video-publish/devices"), 200
                ).json()
                assert [row["serial"] for row in devices["devices"]] == ["TEST_device"]
                content = b"TEST_video_bytes"
                payload = {
                    "clientRequestId": str(uuid4()),
                    "filename": "TEST_video.mp4",
                    "contentType": "video/mp4",
                    "sizeBytes": len(content),
                    "caption": marker,
                }
                owned(
                    client.post(prefix + "/v2/video-publish/tasks", json=payload), 403
                )
                ticket = owned(
                    client.post(
                        prefix + "/v2/video-publish/tasks",
                        json=payload,
                        headers={"X-Requested-With": "tts-erp"},
                    ),
                    201,
                ).json()
                target = urlsplit(ticket["upload"]["url"])
                assert target.scheme == "http" and target.hostname == "127.0.0.1"
                assert target.port and target.port != child.port
                # Separate HTTP client: no ERP cookie/auth propagation to storage.
                with httpx.Client(trust_env=False, timeout=5) as upload:
                    result = upload.put(
                        ticket["upload"]["url"],
                        content=content,
                        headers={"Content-Type": "video/mp4", "Origin": child.base},
                    )
                    assert result.status_code == 200
                confirmed = owned(
                    client.post(
                        prefix
                        + f"/v2/video-publish/tasks/{ticket['taskId']}/confirm-upload",
                        json={"rowVersion": ticket["rowVersion"]},
                        headers={"X-Requested-With": "tts-erp"},
                    ),
                    200,
                ).json()
                assert confirmed["stage"] == "queued"
                with checked_session(binding, engine, factory) as session:
                    row = session.scalar(
                        select(VideoPublishTask).where(
                            VideoPublishTask.public_id == UUID(ticket["taskId"]),
                            VideoPublishTask.caption == marker,
                            VideoPublishTask.created_by_user_id == account.user_pk,
                        )
                    )
                    assert row is not None and row.stage == "queued" and row.object_etag
                job = next(name for name, spec in JOBS.items() if not spec.is_tiktok)
                owned(
                    client.post(
                        prefix + f"/v2/admin/sync-jobs/{job}/trigger",
                        json={},
                        headers={"X-Requested-With": "tts-erp"},
                    ),
                    200,
                )
                owned(
                    client.post(
                        prefix + "/v2/auth/logout",
                        headers={"X-Requested-With": "tts-erp"},
                    ),
                    204,
                )
                assert (
                    owned(client.get(prefix + "/v2/auth/me"), 200).json()[
                        "authenticated"
                    ]
                    is False
                )
        finally:
            engine.dispose()
