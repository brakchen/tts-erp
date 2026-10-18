"""Lifecycle notification delivery for the plugin status domain (design §11).

The outbox row is created inside the report/sweep transaction; *this* module
only puts bytes on the wire, and it does so outside any transaction so a slow
or failing webhook never holds a database lock (§11).

There is no reusable notification infrastructure in this repository yet, so this
is the design's "narrow sender for this domain" rather than a placeholder. It
speaks a generic JSON webhook POST; the secret is resolved through the existing
``token_service`` decrypt helper from the ``config.runtime_config_secrets`` row,
never by reading ``integration.credentials`` directly (§9.8, §11).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import RuntimeConfigSecret

log = logging.getLogger("tts_erp_v2.plugin.status.notifications")

__all__ = [
    "DELIVERED",
    "PENDING_MANUAL",
    "RETRY",
    "HttpWebhookSender",
    "NotificationMessage",
    "Sender",
    "render_message",
    "resolve_webhook_url",
    "retry_delay_seconds",
]

#: Outcomes a sender may report back to the sweep.
DELIVERED = "delivered"
RETRY = "retry"
PENDING_MANUAL = "pending_manual"

#: ``config.runtime_config_secrets.name`` holding the lifecycle webhook URL.
WEBHOOK_SECRET_NAME = "plugin_status_lifecycle_webhook"

#: §8 full-jitter backoff: base 30 s, hard cap 300 s. Never exponential to
#: infinity and never shorter than a second, so a flapping endpoint cannot be
#: hammered and a real outage is not retried in a tight loop.
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 300
RETRY_MIN_SECONDS = 1.0

DEFAULT_TIMEOUT_SECONDS = 10.0


def retry_delay_seconds(
    attempt: int, *, rng: Callable[[], float] | None = None
) -> float:
    """Full-jitter backoff for delivery attempt ``attempt`` (1-based)."""
    draw = rng if rng is not None else random.random
    cap = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    return max(RETRY_MIN_SECONDS, draw() * cap)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """One delivery unit. Carries template parameters, never a secret."""

    kind: str
    instance_id: str
    title: str
    text: str
    payload: dict[str, Any]
    attempt: int


class Sender(Protocol):
    """Anything that can turn a message into one of the three outcomes."""

    def __call__(self, message: NotificationMessage) -> str: ...


def _text_for(payload: dict[str, Any]) -> str:
    """Human-readable body. Says what was observed, never what was guessed."""
    kind = payload.get("kind")
    if kind == "instance_stale":
        return (
            f"插件实例 {payload.get('pluginId')} 已连续 "
            f"{payload.get('heartbeatStaleSeconds')} 秒没有上报心跳"
            f"（可能休眠或断网），最后收到时间 {payload.get('lastSeenAt')}。"
            "这不代表页面已关闭或店铺已退出登录。"
        )
    if kind == "episode_recovered":
        return (
            f"插件实例 {payload.get('pluginId')} 的 episode "
            f"{payload.get('incidentId')} 已恢复运行"
            f"（绑定 {payload.get('bindingPhase')}，执行 {payload.get('executionPhase')}）。"
            "无需人工处理。"
        )
    if kind == "episode_no_candidate":
        return (
            f"插件实例 {payload.get('pluginId')} 的 episode "
            f"{payload.get('incidentId')} 已持续 "
            f"{payload.get('noCandidateSeconds')} 秒没有可用候选页面。"
            "可能页面已关闭或需要人工打开并登录。"
        )
    reason = payload.get("reasonCode")
    binding = payload.get("bindingPhase")
    execution = payload.get("executionPhase")
    return (
        f"插件实例 {payload.get('pluginId')} 的 episode "
        f"{payload.get('incidentId')} 需要人工处理："
        f"原因 {reason}，绑定 {binding}，执行 {execution}。"
    )


def render_message(
    *, kind: str, instance_id: str, payload: dict[str, Any], attempt: int
) -> NotificationMessage:
    title = {
        "instance_stale": "插件实例失联",
        "episode_requires_user": "插件绑定需要人工处理",
        "episode_recovered": "插件绑定已恢复",
        "episode_no_candidate": "插件长时间没有可用页面",
    }.get(kind, "插件状态通知")
    return NotificationMessage(
        kind=kind,
        instance_id=instance_id,
        title=title,
        text=_text_for(payload),
        payload=dict(payload),
        attempt=attempt,
    )


def resolve_webhook_url(session: Session) -> str | None:
    """Decrypt the lifecycle webhook URL from the existing secret owner.

    Returns ``None`` when the deployment has not configured one; the sweep then
    reports the notification as awaiting configuration instead of pretending it
    was delivered.
    """
    from tts_erp_v2.proxy.token_service import DecryptionError, SigningError, decrypt

    row = session.get(RuntimeConfigSecret, WEBHOOK_SECRET_NAME)
    if row is None or row.retired_at is not None:
        return None
    try:
        value = decrypt(row.encrypted_value).strip()
    except (DecryptionError, SigningError, ValueError):
        log.exception("[plugin-status] lifecycle webhook secret could not be read")
        return None
    return value or None


class HttpWebhookSender:
    """POST one JSON message to a webhook URL.

    A 2xx is delivery. 429/5xx and network errors are retryable. Any other 4xx
    is a configuration or signing problem — retrying it forever would just burn
    the outbox, so it is parked for a person instead.
    """

    def __init__(
        self,
        *,
        url: str | None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Any | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._transport = transport

    def __call__(self, message: NotificationMessage) -> str:
        if not self._url:
            return PENDING_MANUAL
        import httpx

        body = {
            "kind": message.kind,
            "title": message.title,
            "text": message.text,
            "instanceId": message.instance_id,
            "attempt": message.attempt,
        }
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(self._url, json=body)
        except httpx.HTTPError:
            log.warning(
                "[plugin-status] notification transport failed kind=%s attempt=%d",
                message.kind,
                message.attempt,
            )
            return RETRY
        if 200 <= response.status_code < 300:
            return DELIVERED
        if response.status_code == 429 or response.status_code >= 500:
            log.warning(
                "[plugin-status] notification refused kind=%s status=%d",
                message.kind,
                response.status_code,
            )
            return RETRY
        log.error(
            "[plugin-status] notification configuration error kind=%s status=%d",
            message.kind,
            response.status_code,
        )
        return PENDING_MANUAL
