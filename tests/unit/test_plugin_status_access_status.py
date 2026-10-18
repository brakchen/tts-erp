"""Approved status-purpose authorization boundaries at the canonical owner."""

from __future__ import annotations

import pytest

from tts_erp_v2.access import ApiKeyPurpose, Role, required_role


@pytest.mark.parametrize(
    ("method", "route", "expected"),
    [
        ("POST", "/v2/plugin/status/report", Role.READWRITE),
        ("GET", "/v2/plugin/status/report", Role.ADMIN),
        ("POST", "/v2/plugin/status/report/other", Role.ADMIN),
        ("POST", "/v2/plugin/admin/instances", Role.ADMIN),
        ("GET", "/v2/plugin/admin/instances", Role.ADMIN),
    ],
)
def test_status_report_role_is_exact_not_a_prefix(
    method: str,
    route: str,
    expected: Role,
) -> None:
    assert required_role(method, route) is expected


def test_status_purpose_is_registered_without_replacing_existing_purposes() -> None:
    values = {purpose.value for purpose in ApiKeyPurpose}
    assert values == {"general", "monitor_config", "plugin_status"}


def test_status_purpose_rejects_direct_shop_binding() -> None:
    from tts_erp_v2.access._credentials import _purpose_of

    assert _purpose_of("plugin_status", None) is ApiKeyPurpose.PLUGIN_STATUS
    assert _purpose_of("plugin_status", 7) is None
