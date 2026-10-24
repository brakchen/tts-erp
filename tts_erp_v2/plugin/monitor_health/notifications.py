"""Published runtime-config / Feishu boundary. No webhook reaches public state."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from time import monotonic
from urllib.parse import urlsplit

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import RuntimeConfigItem, RuntimeConfigSecret
from tts_erp_v2.proxy.errors import DecryptionError, SigningError
from tts_erp_v2.proxy.token_service import decrypt
from tts_erp_v2.runtime_config.repository import published_revision

CONFIG_KEY = "plugin.monitor_health.v1"
SECRET_NAME = "monitor-health-feishu"
DIGEST_BYTES = 16 * 1024
NOTIFICATION_BUDGET_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 8


class _RedactWebhook(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = re.sub(
            r"https://open\.feishu\.cn(?::443)?/open-apis/bot/v2/hook/[^\s\"']+",
            "[monitor-health-webhook]",
            message,
        )
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


# httpx logs its full request URL at INFO, including bot tokens. This filter
# leaves every unrelated HTTP log intact; it does not disable HTTP observability.
logging.getLogger("httpx").addFilter(_RedactWebhook())


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "open.feishu.cn"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith("/open-apis/bot/v2/hook/")
            and bool(parsed.path.removeprefix("/open-apis/bot/v2/hook/"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("monitor health requires an official HTTPS Feishu bot webhook")


def webhook(session: Session) -> str:
    item = session.get(RuntimeConfigItem, CONFIG_KEY)
    if item is None or item.retired_at is not None:
        raise RuntimeError("monitor health runtime config missing or retired")
    try:
        revision = published_revision(session, item)
    except SQLAlchemyError:
        raise RuntimeError("monitor health published revision unavailable") from None
    if revision is None or revision.rollout:
        raise RuntimeError(
            "monitor health config unpublished or retired rollout present"
        )
    if revision.payload.get("feishuWebhook") != f"secret://{SECRET_NAME}":
        raise RuntimeError(
            "monitor health config requires secret://monitor-health-feishu"
        )
    secret = session.get(RuntimeConfigSecret, SECRET_NAME)
    if secret is None or secret.retired_at is not None:
        raise RuntimeError("monitor health secret missing or retired")
    try:
        url = decrypt(secret.encrypted_value)
        _validate_url(url)
    except (DecryptionError, SigningError, ValueError):
        raise RuntimeError("monitor health secret unavailable or invalid") from None
    return url


class DeliveryError(RuntimeError):
    """Sanitized dispatch failure with only the confirmed accepted prefix."""

    def __init__(self, accepted_batches: int) -> None:
        super().__init__("monitor health Feishu notification failed")
        self.accepted_batches = accepted_batches


async def _deliver_many(
    url: str,
    contents: list[str],
    transport: httpx.AsyncBaseTransport | None,
    accepted: list[int],
    started: float,
) -> None:
    loop = asyncio.get_running_loop()
    elapsed = monotonic() - started
    request_deadline = loop.time() + max(0, REQUEST_TIMEOUT_SECONDS - elapsed)
    total_deadline = loop.time() + max(0, NOTIFICATION_BUDGET_SECONDS - elapsed)
    client = httpx.AsyncClient(
        timeout=2.0, follow_redirects=False, trust_env=False, transport=transport
    )
    try:
        # The total fence also cancels HTTPX-internal unwind cleanup if the
        # request deadline's cancellation was already consumed by that cleanup.
        async with (
            asyncio.timeout_at(total_deadline),
            asyncio.timeout_at(request_deadline),
        ):
            for index, content in enumerate(contents):
                response = await client.send(
                    client.build_request(
                        "POST",
                        url,
                        json={"msg_type": "text", "content": {"text": content}},
                    ),
                    stream=True,
                )
                try:
                    if not response.is_success:
                        raise ValueError("rejected")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 4096:
                            raise ValueError("response capacity")
                    payload = json.loads(raw)
                    if (
                        not isinstance(payload, dict)
                        or type(payload.get("code")) is not int
                        or payload["code"] != 0
                    ):
                        raise ValueError("business rejection")
                    accepted.append(index)
                finally:
                    # Fresh cancellation scope: a prior request cancellation
                    # cannot leave response cleanup awaiting beyond the total.
                    async with asyncio.timeout_at(total_deadline):
                        await response.aclose()
    finally:
        # This must be a separate remaining-budget scope even when response
        # cleanup used up the deadline. No detached cleanup/network task exists.
        async with asyncio.timeout_at(total_deadline):
            await client.aclose()


def send_many(
    url: str,
    batches: list[list[str]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    started = monotonic()
    _validate_url(url)
    contents = ["\n".join(lines) for lines in batches]
    if any(len(content.encode("utf-8")) > DIGEST_BYTES for content in contents):
        raise RuntimeError("monitor health digest exceeds capacity")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("monitor health sender requires synchronous job context")
    accepted: list[int] = []
    try:
        # Request and cooperative cleanup share the total budget. OS/resolver
        # teardown can still exceed it; measure the ENTIRE synchronous call.
        asyncio.run(_deliver_many(url, contents, transport, accepted, started))
        if monotonic() - started > NOTIFICATION_BUDGET_SECONDS:
            raise TimeoutError("total budget")
    except (httpx.HTTPError, ValueError, TimeoutError):
        # Preserve accepted prefix even if a later request/cleanup failed; never
        # chain transport exceptions that can contain the secret URL.
        raise DeliveryError(len(accepted)) from None
    return len(accepted)


def send(
    url: str,
    lines: list[str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    send_many(url, [lines], transport=transport)
