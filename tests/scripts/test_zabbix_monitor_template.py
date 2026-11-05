"""Check the checked-in Zabbix export without contacting a Zabbix service."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def validate_template(source: str) -> None:
    export = yaml.safe_load(source)["zabbix_export"]
    assert isinstance(export["version"], str)
    groups = {group["name"] for group in export["template_groups"]}
    assert groups and all(isinstance(name, str) and name for name in groups)
    templates = export["templates"]
    assert isinstance(templates, list) and templates
    identifiers = []
    for template in templates:
        assert template["template"] and template["name"]
        assert all(group["name"] in groups for group in template["groups"])
        items = template["items"]
        assert isinstance(items, list) and items
        keys = [item["key"] for item in items]
        assert all(isinstance(key, str) and key for key in keys)
        assert len(keys) == len(set(keys))
        identifiers.append(template["uuid"])
        identifiers.extend(item["uuid"] for item in items)
    for trigger in export["triggers"]:
        assert trigger["name"] and trigger["expression"]
        assert trigger.get("recovery_mode") != "RECOVERY_EXPRESSION" or trigger["recovery_expression"]
        identifiers.append(trigger["uuid"])
    assert all(isinstance(value, str) and value for value in identifiers)
    assert len(identifiers) == len(set(identifiers))


def test_zabbix_monitor_template():
    path = Path(__file__).resolve().parents[2] / "docs/plugins/monitor/zabbix-monitor.yaml"
    validate_template(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", [
    "zabbix_export: [",
    "zabbix_export: {}",
    "zabbix_export: {version: '7.4', template_groups: [{name: TTS}], templates: []}",
])
def test_invalid_template_is_rejected(source):
    with pytest.raises((yaml.YAMLError, KeyError, AssertionError)):
        validate_template(source)
