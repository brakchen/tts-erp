from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.api.v2.monitor_health import post_health, router

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture
def client():
    app = FastAPI()

    @app.middleware("http")
    async def grant(request, call_next):
        # Local mount supplies the same resolved scope values as AuthMiddleware.
        request.scope["api_key_role"] = request.headers.get("test-role", "readwrite")
        value = request.headers.get("test-scopes", "seller:TEST_seller")
        request.scope["api_key_scopes"] = tuple(value.split(",")) if value else ()
        return await call_next(request)

    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as result:
        yield result


def body(**changes):
    return {
        "shopId": "TEST_seller",
        "shopName": "",
        "step": "heartbeat",
        "status": "running",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": "x"},
        {"shopId": 123},
        {"shopId": ""},
        {"shopId": " "},
        {"shopId": "x" * 129},
        {"shopName": None},
        {"shopName": "x" * 201},
        {"step": "heartbeat", "status": "started"},
        {"step": "scan", "status": "running"},
        {"step": "evaluate", "status": "progress"},
        {"step": "other"},
        {"status": True},
    ],
)
def test_strict_exact_fields_and_combinations(client, changes):
    assert (
        client.post("/v2/plugin/monitor/health", json=body(**changes)).status_code
        == 422
    )


@pytest.mark.parametrize("field", ["shopId", "shopName", "step", "status"])
def test_all_four_fields_required(client, field):
    payload = body()
    del payload[field]
    assert client.post("/v2/plugin/monitor/health", json=payload).status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"test-role": "readonly"},
        {"test-role": ""},
        {"test-scopes": "seller:TEST_other"},
        {"test-scopes": "advertiser:TEST_seller"},
        {"test-scopes": "unknown:x"},
    ],
)
def test_readwrite_and_true_seller_scope_required(client, headers):
    assert (
        client.post(
            "/v2/plugin/monitor/health", headers=headers, json=body()
        ).status_code
        == 403
    )


def test_body_bounded_even_without_content_length(client):
    assert (
        client.post(
            "/v2/plugin/monitor/health", content=iter([b" " * 4097])
        ).status_code
        == 413
    )


def test_malformed_json(client):
    assert client.post("/v2/plugin/monitor/health", content=b"{").status_code == 422


def test_sync_handler_and_204_only_after_commit(client, monkeypatch):
    from tts_erp_v2.api.v2 import monitor_health

    class Session:
        committed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

    session = Session()
    client.app.dependency_overrides[get_session] = lambda: session
    captured = []
    monkeypatch.setattr(
        monitor_health,
        "ingest",
        lambda *args, **kwargs: captured.append((args, kwargs)),
        raising=False,
    )
    response = client.post("/v2/plugin/monitor/health", json=body())
    assert response.status_code == 204 and response.content == b""
    assert session.committed and captured
    assert not inspect.iscoroutinefunction(post_health)


def test_commit_failure_is_not_2xx(client, monkeypatch):
    from tts_erp_v2.api.v2 import monitor_health

    class BrokenSession:
        def commit(self):
            raise RuntimeError("TEST_commit_failure")

        def rollback(self):
            pass

    client.app.dependency_overrides[get_session] = BrokenSession
    monkeypatch.setattr(
        monitor_health, "ingest", lambda *args, **kwargs: None, raising=False
    )
    assert client.post("/v2/plugin/monitor/health", json=body()).status_code == 500


@pytest.mark.parametrize(
    "scopes",
    ["", "*", "seller:TEST_seller", "seller:TEST_seller,advertiser:TEST_other"],
)
def test_legacy_unrestricted_or_exact_seller_authorized(client, monkeypatch, scopes):
    from tts_erp_v2.api.v2 import monitor_health

    class Session:
        def commit(self):
            pass

    client.app.dependency_overrides[get_session] = Session
    monkeypatch.setattr(
        monitor_health, "ingest", lambda *args, **kwargs: None, raising=False
    )
    assert (
        client.post(
            "/v2/plugin/monitor/health", headers={"test-scopes": scopes}, json=body()
        ).status_code
        == 204
    )


def test_router_persists_without_notification_config(client, db_engine, db_session):
    from tts_erp_v2.db.models.monitor_health import MonitorHealth

    with db_engine.begin() as connection:
        MonitorHealth.metadata.tables["plugin.monitor_health"].create(
            connection, checkfirst=True
        )
    client.app.dependency_overrides[get_session] = lambda: db_session
    response = client.post(
        "/v2/plugin/monitor/health", headers={"test-role": "admin"}, json=body()
    )
    assert response.status_code == 204
    row = db_session.get(MonitorHealth, "TEST_seller")
    assert row and row.state["enabled"] and row.shop_name == ""
