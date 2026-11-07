"""Authenticated telemetry through normal prefixed and unprefixed routes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2 import plugin_shops
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.plugin import monitor_relay
from tts_erp_v2.plugin.monitor_relay import MonitorRelayError

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
ITEMS = [{"key": "monitor.heartbeat", "value": 0}]


@pytest.fixture
def monitor_shop(db_engine):
    seller = str(uuid4().int)
    with Session(db_engine) as session:
        shop = ChannelAccount(
            platform="tiktok", shop_id=seller, account_name="TEST_monitor_telemetry",
            plugin_metadata={"plugins": {"monitor": {"observedAt": datetime.now(UTC).isoformat()}}},
        )
        session.add(shop)
        session.commit()
        yield seller
        session.delete(shop)
        session.commit()


@pytest.fixture
def forwarded(monkeypatch):
    forward = Mock()
    forward.seen = []
    forward.side_effect = lambda session, shop, items, **_: forward.seen.append((shop.shop_id, items))
    monkeypatch.setattr(plugin_shops, "forward_observations", forward)
    return forward


@pytest.mark.parametrize(("fixture_name", "prefix"), [("api_client", ""), ("prefixed_client", "/tts")])
def test_existing_erp_key_forwards_without_browser_zabbix_credentials(request, fixture_name, prefix, readwrite_key, monitor_shop, forwarded):
    response = request.getfixturevalue(fixture_name).post(
        f"{prefix}/v2/plugin/shops/{monitor_shop}/telemetry",
        headers={"X-API-Key": readwrite_key}, json={"items": ITEMS},
    )
    assert response.status_code == 204, response.text
    assert not response.content
    assert response.headers["cache-control"] == "no-store"
    assert forwarded.seen == [(monitor_shop, ITEMS)]


def test_readonly_key_cannot_send_telemetry(api_client, readonly_key, monitor_shop, forwarded):
    response = api_client.post(f"/v2/plugin/shops/{monitor_shop}/telemetry", headers={"X-API-Key": readonly_key}, json={"items": ITEMS})
    assert response.status_code == 403
    forwarded.assert_not_called()


def test_unknown_shop_does_not_provision_a_zabbix_host(api_client, readwrite_key, forwarded):
    response = api_client.post(f"/v2/plugin/shops/{uuid4().int}/telemetry", headers={"X-API-Key": readwrite_key}, json={"items": ITEMS})
    assert response.status_code == 409
    forwarded.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"items": [{"key": "system.run", "value": 1}]},
    {"items": [{"key": "monitor.heartbeat", "value": True}]},
    {"items": [{"key": "monitor.heartbeat", "value": "1"}]},
    {"items": [{"key": "monitor.stage.progress[scan]", "value": 0}]},
    {"items": [{"key": "monitor.heartbeat", "value": 1, "host": "other-shop"}]},
    {"items": [{"key": "monitor.heartbeat", "value": 1, "clock": 1790000000, "ns": 100}]},
    {"items": ITEMS * 2},
    {"items": [], "token": "TEST_unexpected_credential"},
])
def test_invalid_observations_never_reach_zabbix(api_client, readwrite_key, monitor_shop, forwarded, payload):
    response = api_client.post(f"/v2/plugin/shops/{monitor_shop}/telemetry", headers={"X-API-Key": readwrite_key}, json=payload)
    assert response.status_code == 422
    forwarded.assert_not_called()


def test_partial_or_failed_upstream_is_an_http_failure(api_client, readwrite_key, monitor_shop, forwarded):
    forwarded.side_effect = MonitorRelayError("Zabbix did not accept every observation")
    response = api_client.post(f"/v2/plugin/shops/{monitor_shop}/telemetry", headers={"X-API-Key": readwrite_key}, json={"items": ITEMS})
    assert response.status_code == 503


def test_delayed_telemetry_keeps_its_earlier_receipt_time(api_client, readwrite_key, monitor_shop, monkeypatch):
    timestamps = iter((1_790_000_000_100_000_000, 1_790_000_000_200_000_000))
    monkeypatch.setattr(plugin_shops, "time_ns", lambda: next(timestamps))
    older_at_zabbix = Event()
    resume_older = Event()
    received = []
    zabbix = Mock()

    def get_host(**_):
        if not older_at_zabbix.is_set():
            older_at_zabbix.set()
            assert resume_older.wait(timeout=10)
        return [{
            "hostid": "42", "name": f"TEST_monitor_telemetry · {monitor_shop}", "status": "0",
            "hostgroups": [{"name": monitor_relay.GROUP}],
            "parentTemplates": [{"host": monitor_relay.TEMPLATE}],
        }]

    def push(items):
        received.extend(items)
        return {"response": "success", "data": [{"itemid": 1}]}

    zabbix.host.get.side_effect = get_host
    zabbix.history.push.side_effect = push
    monkeypatch.setattr(monitor_relay, "_client", lambda session: zabbix)

    def report(active):
        return api_client.post(
            f"/v2/plugin/shops/{monitor_shop}/telemetry",
            headers={"X-API-Key": readwrite_key},
            json={"items": [{"key": "monitor.stage.activity[close]", "value": active}]},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        older = executor.submit(report, 1)
        try:
            assert older_at_zabbix.wait(timeout=10)
            newer = executor.submit(report, 0)
            assert newer.result(timeout=10).status_code == 204
        finally:
            resume_older.set()
        assert older.result(timeout=10).status_code == 204

    assert [item["value"] for item in received] == [0, 1]
    assert [(item["clock"], item["ns"]) for item in received] == [
        (1_790_000_000, 200_000_000),
        (1_790_000_000, 100_000_000),
    ]


def test_shop_scope_is_checked_before_forwarding():
    request = Request({"type": "http", "api_key_role": "readwrite", "api_key_scopes": ["seller:111"]})
    with pytest.raises(HTTPException) as error:
        plugin_shops._require_shop_scope(request, "222")
    assert error.value.status_code == 403
