"""Test-only browser egress gate, not server authorization or a release flag."""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.scripts.test_frontend_p2_accounts import fake_probe

pytestmark = pytest.mark.domain_frontend
MODULE = "tests.support.frontend_p2_browser"


def fence(monkeypatch, prefix=""):
    module = importlib.import_module(MODULE)
    _value, probe, _network = fake_probe(monkeypatch)
    from dataclasses import replace

    probe = replace(probe, prefix=prefix)
    return module.BrowserFence(probe, username="test_p2_actor", shop_id="9900123456789",
                               service_id="TEST_service", enum_value="TEST_value")


@pytest.mark.parametrize(("method", "url", "body"), [
    ("POST", "http://127.0.0.1:9877/v2/admin/shops/register", {}),
    ("GET", "http://127.0.0.1:54321/v2/auth/me", None),
    ("GET", "http://localhost:12345/v2/auth/me", None),
    ("GET", "https://example.invalid/v2/auth/me", None),
    ("GET", "http://TEST_user@127.0.0.1:12345/v2/auth/me", None),
    ("GET", "http://127.0.0.1:12345/v2/oauth/tiktok/callback", None),
    ("DELETE", "http://127.0.0.1:12345/v2/config/enum-map/123", None),
    ("PATCH", "http://127.0.0.1:12345/v2/admin/shops/123", {"account_name": "TEST_name"}),
    ("POST", "http://127.0.0.1:12345/v2/admin/shops/register", {"shop_id": "111", "account_name": "TEST_name"}),
    ("PUT", "http://127.0.0.1:12345/v2/config/enum-map", {"enum_type": "order_status", "enum_value": "REAL", "label_zh": "TEST_label", "sort_order": 0}),
    ("GET", "http://127.0.0.1:12345/v2/oauth/tiktok/authorize?format=json&service_id=REAL_service", None),
    ("GET", "http://127.0.0.1:12345/v2/intercept/requests/stats?days=30", None),
    ("GET", "http://127.0.0.1:12345/v2/commerce/channel-accounts?platform=tiktok&limit=501", None),
    ("POST", "http://127.0.0.1:12345/v2/auth/login", {"username": "real_user", "password": "TEST_password"}),
])
def test_wrong_origin_method_path_or_non_test_object_never_reaches_transport(monkeypatch, method, url, body):
    gate = fence(monkeypatch)
    route = MagicMock()
    route.request.method = method
    route.request.url = url
    route.request.post_data_json = body
    gate.handle(route)
    route.fetch.assert_not_called()
    route.abort.assert_called_once()
    assert gate.violations == ["P2 browser request refused"]


@pytest.mark.parametrize("path", ["/v2/auth/me", "/tts-other/v2/auth/me", "/tts/../v2/auth/me", "/tts/%2e%2e/v2/auth/me"])
def test_prefix_cannot_be_bypassed(monkeypatch, path):
    gate = fence(monkeypatch, "/tts")
    with pytest.raises(ValueError):
        gate.check_request("GET", "http://127.0.0.1:12345" + path, None)


def test_current_launch_exit_is_checked_before_browser_transport(monkeypatch):
    gate = fence(monkeypatch)
    monkeypatch.setattr(gate.probe.child.process, "poll", MagicMock(return_value=17))
    route = MagicMock()
    route.request.method, route.request.url = "GET", "http://127.0.0.1:12345/v2/auth/me"
    route.request.post_data_json = None
    gate.handle(route)
    route.fetch.assert_not_called()
    route.abort.assert_called_once()


def test_sensitive_input_driver_failure_never_surfaces_the_value():
    module = importlib.import_module(MODULE)
    locator = MagicMock()
    locator.fill.side_effect = module.PlaywrightError("TEST_sensitive_payload")
    with pytest.raises(ValueError, match="^P2 sensitive input refused$") as error:
        module.fill_sensitive(locator, "TEST_sensitive_payload")
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_only_explicit_test_objects_and_bounded_reads_are_allowed(monkeypatch):
    gate = fence(monkeypatch)
    root = "http://127.0.0.1:12345"
    for path in ("/v2/auth/me", "/v2/pages/shops", "/v2/commerce/channel-accounts?platform=tiktok&limit=500",
                 "/v2/intercept/requests/stats?days=7", "/static/frontend/TEST_release/main.js"):
        gate.check_request("GET", root + path, None)
    gate.check_request("POST", root + "/v2/admin/shops/register", {"shop_id": gate.shop_id, "account_name": "TEST_name", "platform": "tiktok"})
    gate.shop_pks.add(123)  # Real consumer adds only after independent TEST-row DB confirmation.
    gate.enum_pks.add(456)
    gate.check_request("PATCH", root + "/v2/admin/shops/123", {"account_name": "TEST_edited"})
    gate.check_request("DELETE", root + "/v2/config/enum-map/456", None)
