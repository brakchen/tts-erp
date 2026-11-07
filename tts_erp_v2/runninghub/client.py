"""Official HTTP protocol. No create retries; no credentials in diagnostics.

Uses existing HTTPX (BSD-3-Clause), https://www.python-httpx.org/advanced/transports/.
Protocol checked 2026-11-07 against RunningHub's official API documentation.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

_result_transfer: ContextVar[bool] = ContextVar(
    "runninghub_result_transfer", default=False
)


class _ResultLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _result_transfer.get():
            # HTTPX's INFO log includes the full signed request URL by default.
            record.msg, record.args = "RunningHub result HTTP transfer", ()
        return True


logging.getLogger("httpx").addFilter(_ResultLogFilter())

# Official error-code reference doc-8287338, checked 2026-11-07.
# 804/813 mean already running/queued, never a rejected creation.
FATAL_CONFIGURATION_CODES = {380, 401, 416, 801, 802, 803, 806, 810, 811, 812}
DEFINITE_REJECTION_CODES = FATAL_CONFIGURATION_CODES | {412, 421}


class RemoteError(RuntimeError):
    def __init__(
        self, code: str, *, uncertain: bool = False, receipt: dict | None = None
    ) -> None:
        super().__init__(code)
        self.uncertain = uncertain
        self.receipt = receipt or {}


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            k: sanitize(v, secrets)
            for k, v in value.items()
            if str(k).lower()
            not in {
                "apikey",
                "authorization",
                "accesspassword",
                "netwssurl",
                "url",
                "fileurl",
                "download_url",
            }
        }
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        # Upstream may echo secrets or signed URLs in arbitrary message fields.
        if (
            any(secret and secret in value for secret in secrets)
            or "://" in value
            or "bearer " in value.lower()
        ):
            return "[redacted]"
        try:
            nested = json.loads(value)
        except (ValueError, TypeError):
            return value[:4000]
        if isinstance(nested, (dict, list)):
            return sanitize(nested, secrets)
    return value


class RunningHubClient:
    def __init__(
        self,
        key: str,
        base_url: str = "https://www.runninghub.cn",
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("INVALID_RUNNINGHUB_BASE_URL")
        self.key = key
        self.http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            transport=transport or httpx.HTTPTransport(retries=0),
            timeout=120,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self.http.close()

    def _post(self, path: str, *, creating: bool = False, **kwargs: Any) -> dict:
        try:
            response = self.http.post(path, **kwargs)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (httpx.HTTPError, ValueError, TypeError):
            raise RemoteError(
                "SUBMISSION_UNKNOWN" if creating else "UPSTREAM_UNAVAILABLE",
                uncertain=creating,
            ) from None

    def _data(self, path: str, **kwargs: Any) -> dict:
        value = self._post(path, **kwargs)
        if (
            type(value.get("code")) is not int
            or value["code"] != 0
            or not isinstance(value.get("data"), dict)
        ):
            raise RemoteError("UPSTREAM_REJECTED", receipt=sanitize(value, (self.key,)))
        return value["data"]

    def account(self) -> dict:
        data = self._data("/uc/openapi/accountStatus", json={"apikey": self.key})
        return sanitize(
            {
                k: data.get(k)
                for k in (
                    "remainCoins",
                    "currentTaskCounts",
                    "remainMoney",
                    "currency",
                    "apiType",
                )
            },
            (self.key,),
        )

    def workflow(self, workflow_id: str) -> Any:
        data = self._data(
            "/api/openapi/getJsonApiFormat",
            json={"apiKey": self.key, "workflowId": workflow_id},
        )
        return data.get("prompt")

    def upload(self, path: Path) -> str:
        with path.open("rb") as source:
            data = self._data(
                "/openapi/v2/media/upload/binary",
                files={"file": (path.name, source, "application/octet-stream")},
            )
        name = data.get("fileName")
        if not isinstance(name, str) or not name or "://" in name:
            raise RemoteError("UPLOAD_FILENAME_MISSING")
        return name

    def create(self, body: dict) -> dict:
        # Caller commits SUBMITTING before entering here and immediately persists any taskId.
        return self._post(
            "/task/openapi/create", creating=True, json={**body, "apiKey": self.key}
        )

    def query(self, task_id: str) -> dict:
        value = self._post("/openapi/v2/query", json={"taskId": task_id})
        if not isinstance(value.get("status"), str) or (
            value.get("taskId") is not None and str(value["taskId"]) != task_id
        ):
            raise RemoteError("INVALID_QUERY_RESPONSE")
        return value

    def cancel(self, task_id: str) -> dict:
        # api-425749015 acknowledges cancellation with code=0 and null data.
        value = sanitize(
            self._post(
                "/task/openapi/cancel", json={"apiKey": self.key, "taskId": task_id}
            ),
            (self.key,),
        )
        if type(value.get("code")) is not int or value["code"] != 0:
            raise RemoteError("UPSTREAM_CANCEL_REJECTED", receipt=value)
        return value


def public_download_url(url: str) -> None:
    """Only public HTTPS result hosts; no user-supplied download endpoints."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise RemoteError("INVALID_RESULT_URL")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise RemoteError("RESULT_HOST_UNAVAILABLE") from None
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise RemoteError("PRIVATE_RESULT_HOST_REJECTED")


def download(
    url: str, destination: Path, *, max_bytes: int = 512 * 1024 * 1024
) -> None:
    public_download_url(url)
    part = destination.with_suffix(".part")
    log_token = _result_transfer.set(True)
    deadline = time.monotonic() + 300
    # A separate client deliberately has no RunningHub Authorization header.
    try:
        with (
            httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http,
            http.stream("GET", url) as response,
        ):
            response.raise_for_status()
            size = 0
            with part.open("wb") as target:
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise RemoteError("DOWNLOAD_WAIT_LIMIT")
                    size += len(chunk)
                    if size > max_bytes:
                        raise RemoteError("RESULT_TOO_LARGE")
                    target.write(chunk)
        part.replace(destination)
    except httpx.HTTPError:
        raise RemoteError("DOWNLOAD_FAILED") from None
    finally:
        part.unlink(missing_ok=True)
        _result_transfer.reset(log_token)
