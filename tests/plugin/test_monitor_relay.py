"""The relay provisions hosts, validates every acknowledgement and sanitizes failures."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tts_erp_v2.plugin import monitor_relay as relay

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


@pytest.fixture
def api(monkeypatch):
    client = SimpleNamespace(host=Mock(), hostgroup=Mock(), template=Mock(), history=Mock())
    client.host.get.return_value = [{
        "hostid": "42", "name": "TEST_shop · 123", "status": "0",
        "hostgroups": [{"name": relay.GROUP}], "parentTemplates": [{"host": relay.TEMPLATE}],
    }]
    client.hostgroup.get.return_value = [{"groupid": "5"}]
    client.template.get.return_value = [{"templateid": "7"}]
    client.history.push.return_value = {"response": "success", "data": [{"itemid": 1}]}
    monkeypatch.setattr(relay, "_client", lambda session: client)
    return client


def test_first_observation_provisions_shop_and_derives_host(api):
    api.host.get.return_value = []
    shop = SimpleNamespace(shop_id="123", account_name="TEST_shop")
    relay.forward_observations(None, shop, [{"key": "monitor.heartbeat", "value": 0}])
    api.host.create.assert_called_once_with(host="tts-monitor-123", name="TEST_shop · 123", groups=[{"groupid": "5"}], templates=[{"templateid": "7"}])
    api.history.push.assert_called_once_with([{"host": "tts-monitor-123", "key": "monitor.heartbeat", "value": 0}])


@pytest.mark.parametrize("result", [None, {"response": "success", "data": []}, {"response": "success", "data": [{"itemid": 1, "error": "rejected"}]}, {"response": "success", "data": [{"itemid": 0}]}])
def test_failed_or_partial_acceptance_raises(api, result):
    api.history.push.return_value = result
    with pytest.raises(relay.MonitorRelayError):
        relay.forward_observations(None, SimpleNamespace(shop_id="123", account_name="TEST_shop"), [{"key": "monitor.heartbeat", "value": 0}])


def test_timeout_does_not_expose_credentials(api):
    api.history.push.side_effect = TimeoutError("TEST_SECRET upstream response")
    with pytest.raises(relay.MonitorRelayError, match="temporarily unavailable") as error:
        relay.forward_observations(None, SimpleNamespace(shop_id="123", account_name="TEST_shop"), [{"key": "monitor.heartbeat", "value": 0}])
    assert "TEST_SECRET" not in str(error.value)


def test_disabled_host_is_not_reenabled_by_heartbeat(api):
    api.host.get.return_value[0]["status"] = "1"
    with pytest.raises(relay.MonitorRelayError, match="operator attention"):
        relay.forward_observations(None, SimpleNamespace(shop_id="123", account_name="TEST_shop"), [{"key": "monitor.heartbeat", "value": 0}])
    api.host.update.assert_not_called()
    api.history.push.assert_not_called()


def test_long_shop_names_preserve_unique_seller_suffix(api):
    api.host.get.return_value = []
    seller = "9" * 64
    relay.forward_observations(None, SimpleNamespace(shop_id=seller, account_name="TEST_" + "店" * 195), [{"key": "monitor.heartbeat", "value": 0}])
    visible_name = api.host.create.call_args.kwargs["name"]
    assert len(visible_name) == 128 and visible_name.endswith(" · " + seller)
