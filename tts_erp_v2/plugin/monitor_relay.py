"""Provision Monitor hosts and forward current observations to Zabbix.

Zabbix owns clocks, problem state, recovery and notifications. ERP authenticates
the shop and stores only its central service credential through token_service.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from sqlalchemy.orm import Session
from zabbix_utils import ZabbixAPI
from zabbix_utils.exceptions import APIRequestError

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.proxy.token_service import load_credentials

GROUP = "TTS Monitor shops"
TEMPLATE = "TTS Monitor"
CREDENTIAL_PROVIDER = "zabbix"
CREDENTIAL_ACCOUNT = "monitor-relay"


class MonitorRelayError(RuntimeError):
    """Sanitized configuration or upstream error, safe for an HTTP response."""


def _client(session: Session) -> ZabbixAPI:
    url = os.environ.get("TTS_ERP_ZABBIX_URL", "").strip()
    parsed = urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (not parsed.hostname or (parsed.scheme != "https" and not local_http)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise MonitorRelayError("Zabbix relay URL is not configured")
    credential = load_credentials(session, provider=CREDENTIAL_PROVIDER, external_account_id=CREDENTIAL_ACCOUNT)
    if credential is None:
        raise MonitorRelayError("Zabbix relay credential is not configured")
    return ZabbixAPI(url=url, token=credential.access_token, timeout=2)


def _find_host(api: ZabbixAPI, host: str) -> dict | None:
    hosts = api.host.get(
        filter={"host": host}, output=["hostid", "name", "status"],
        selectHostGroups=["name"], selectParentTemplates=["host"],
    )
    if not hosts:
        return None
    if (len(hosts) != 1 or hosts[0]["status"] != "0"
            or not any(group["name"] == GROUP for group in hosts[0]["hostgroups"])
            or not any(template["host"] == TEMPLATE for template in hosts[0]["parentTemplates"])):
        raise MonitorRelayError("Zabbix shop host configuration requires operator attention")
    return hosts[0]


def _ensure_host(api: ZabbixAPI, shop: ChannelAccount) -> str:
    host = f"tts-monitor-{shop.shop_id}"
    # Zabbix visible names are limited to 128 characters. Preserve the unique
    # seller suffix even for the ERP's longer shop names.
    label = shop.account_name or ""
    display_name = f"{label[:125 - len(shop.shop_id)]} · {shop.shop_id}" if label else shop.shop_id
    existing = _find_host(api, host)
    if existing is None:
        groups = api.hostgroup.get(filter={"name": GROUP}, output=["groupid"])
        templates = api.template.get(filter={"host": TEMPLATE}, output=["templateid"])
        if len(groups) != 1 or len(templates) != 1:
            raise MonitorRelayError("Zabbix Monitor template or host group is not configured")
        try:
            api.host.create(host=host, name=display_name, groups=groups, templates=templates)
        except APIRequestError:
            # The unique host name also fences a concurrent first observation.
            # Only a fully provisioned host may satisfy a competing creation.
            existing = _find_host(api, host)
            if existing is None:
                raise
    if existing is not None and existing["name"] != display_name:
        api.host.update(hostid=existing["hostid"], name=display_name)
    return host


def forward_observations(session: Session, shop: ChannelAccount, items: list[dict]) -> None:
    """Return only after Zabbix has accepted every item in the current batch."""
    try:
        api = _client(session)
        host = _ensure_host(api, shop)
        result = api.history.push([{**item, "host": host} for item in items])
        rows = result.get("data", []) if isinstance(result, dict) else []
        if (not isinstance(result, dict) or result.get("response") != "success"
                or not isinstance(rows, list) or len(rows) != len(items)
                or not all(isinstance(row, dict) and "error" not in row
                           and re.fullmatch(r"[1-9]\d*", str(row.get("itemid", ""))) for row in rows)):
            raise MonitorRelayError("Zabbix did not accept every observation")
    except MonitorRelayError:
        raise
    except Exception:
        # SDK/transport errors can contain credential-bearing upstream details.
        raise MonitorRelayError("Zabbix relay is temporarily unavailable") from None
