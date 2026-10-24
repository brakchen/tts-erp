"""Actual application/auth/database seam, not a locally mounted router."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tts_erp_v2.access import Role, required_role
from tts_erp_v2.db.models.monitor_health import MonitorHealth

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
PATH = "/v2/plugin/monitor/health"


@pytest.fixture
def health_shop(db_engine: Engine) -> Iterator[str]:
    shop_id = f"TEST_monitor_wiring_{uuid4().hex}"
    yield shop_id
    with Session(db_engine) as session:
        session.execute(delete(MonitorHealth).where(MonitorHealth.shop_id == shop_id))
        session.commit()


def payload(shop_id: str, status: str = "running") -> dict[str, str]:
    return {
        "shopId": shop_id,
        "shopName": "TEST_monitor_store",
        "step": "heartbeat",
        "status": status,
    }


def test_health_role_is_exact_post_readwrite_not_a_broad_exemption():
    assert required_role("POST", PATH) is Role.READWRITE
    assert required_role("GET", PATH) is Role.ADMIN
    assert required_role("POST", PATH + "-other") is Role.ADMIN


@pytest.mark.parametrize(
    ("fixture_name", "path"),
    [("api_client", PATH), ("prefixed_client", "/tts" + PATH)],
)
def test_actual_application_persists_stopped_heartbeat_without_webhook_config(
    request, fixture_name, path, readwrite_key, health_shop, db_engine
):
    client = request.getfixturevalue(fixture_name)
    response = client.post(
        path,
        headers={"X-API-Key": readwrite_key},
        json=payload(health_shop, "stopped"),
    )
    assert response.status_code == 204, response.text
    assert response.content == b""
    with Session(db_engine) as session:
        row = session.get(MonitorHealth, health_shop)
        assert row is not None
        assert row.shop_name == "TEST_monitor_store"
        assert row.state["enabled"] is False
        assert row.state["heartbeat_at"] > 0


def test_actual_application_requires_key(api_client, health_shop):
    assert api_client.post(PATH, json=payload(health_shop)).status_code == 401


def test_actual_application_rejects_readonly_key(api_client, readonly_key, health_shop):
    response = api_client.post(
        PATH, headers={"X-API-Key": readonly_key}, json=payload(health_shop)
    )
    assert response.status_code == 403


def test_resolved_seller_scope_reaches_real_middleware_and_handler(
    api_client, readwrite_key, health_shop, monkeypatch, db_engine
):
    from tts_erp_v2.access import _credentials
    from tts_erp_v2.access._types import Credential

    # The legacy DB key model is unscoped. Inject a resolved credential only
    # for this scope-projection check; other tests authenticate real DB keys.
    monkeypatch.setattr(
        _credentials,
        "_db_lookup",
        lambda key_hash: Credential(
            key_hash=key_hash, role=Role.READWRITE, scopes=("seller:TEST_other",)
        ),
    )
    response = api_client.post(
        PATH, headers={"X-API-Key": readwrite_key}, json=payload(health_shop)
    )
    assert response.status_code == 403
    with Session(db_engine) as session:
        assert session.get(MonitorHealth, health_shop) is None


def test_actual_route_keeps_four_field_validation(
    api_client, readwrite_key, health_shop
):
    response = api_client.post(
        PATH,
        headers={"X-API-Key": readwrite_key},
        json={**payload(health_shop), "extra": "TEST_forbidden"},
    )
    assert response.status_code == 422
