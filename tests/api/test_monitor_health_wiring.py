"""The retired Monitor intake is absent from the application routes."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
PATH = "/v2/plugin/monitor/health"


@pytest.mark.parametrize(
    ("fixture_name", "path"),
    [("api_client", PATH), ("prefixed_client", "/tts" + PATH)],
)
def test_retired_monitor_health_is_not_routable(request, fixture_name, path, admin_key):
    client = request.getfixturevalue(fixture_name)
    response = client.post(
        path,
        headers={"X-API-Key": admin_key},
        json={
            "shopId": "TEST_monitor_retired",
            "shopName": "TEST_monitor_store",
            "step": "heartbeat",
            "status": "running",
        },
    )
    assert response.status_code == 404, response.text
