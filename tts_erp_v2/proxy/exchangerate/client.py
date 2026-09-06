"""ExchangeRate-API Standard-endpoint client.

Fetches and parses ``GET {base}/{api_key}/latest/{base_code}`` — the
Standard endpoint response:

.. code-block:: json

    {
      "result": "success",
      "time_last_update_unix": 1585267200,
      "time_last_update_utc": "Fri, 27 Mar 2020 00:00:00 +0000",
      "time_next_update_utc": "Sat, 28 Mar 2020 00:00:00 +0000",
      "base_code": "USD",
      "conversion_rates": {"USD": 1, "CNY": 6.9454, ...}
    }

The two timestamps are the cache contract the sync job lives on:
``time_last_update_utc`` is the snapshot's identity key and
``time_next_update_utc`` is the horizon before which the stored rates
are authoritative (no network call needed).

``http_get`` is injectable for tests (default = httpx sync GET with a
15 s timeout); tests pass a stub callable returning ``httpx.Response``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

DEFAULT_BASE_URL = "https://v6.exchangerate-api.com/v6"
DEFAULT_HTTP_TIMEOUT = 15.0

# http_get(url) -> object exposing .json() (httpx.Response in prod;
# tests inject a stub). Loosely typed so the client has no hard import
# of httpx at module load.
HttpGet = Callable[[str], Any]


class ExchangeRateAPIError(RuntimeError):
    """Upstream returned a non-success result, an HTTP error, or unparseable body.

    Attributes:
        error_type: upstream ``error-type`` code when the JSON body
            carried one (e.g. ``quota-exceeded``, ``invalid-key``).
        status_code: HTTP status when known.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


@dataclass(frozen=True)
class StandardRates:
    """Parsed Standard-endpoint payload (one base → all supported codes)."""

    base_code: str
    upstream_last_update: datetime
    #: Cache horizon: while now < next_update_at the rates are
    #: authoritative. None when the upstream omitted the field.
    next_update_at: datetime | None
    conversion_rates: dict[str, Decimal]
    #: The raw upstream JSON — persisted verbatim by the job to
    #: ``integration.raw_records`` for audit / drift forensics.
    payload: dict[str, Any] = field(repr=False)


def _default_http_get() -> HttpGet:
    import httpx

    def _get(url: str) -> httpx.Response:
        with httpx.Client(
            timeout=DEFAULT_HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            return client.get(url)

    return _get


def _parse_upstream_datetime(raw: str | None) -> datetime | None:
    """Parse RFC-2822 style upstream timestamps ("Fri, 27 Mar 2020 00:00:00 +0000").

    Falls back to bare-ISO parsing for robustness; always returns an
    aware UTC datetime.
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        # last resort: ISO-8601 (rare; upstream uses RFC-2822 today).
        # Python ≥3.11 parses a trailing "Z" natively.
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_success(payload: dict[str, Any], base_code: str) -> StandardRates:
    raw_rates = payload.get("conversion_rates")
    if not isinstance(raw_rates, dict) or not raw_rates:
        raise ExchangeRateAPIError(
            "upstream success payload missing conversion_rates",
            error_type="malformed-payload",
        )
    upstream_last_update = _parse_upstream_datetime(payload.get("time_last_update_utc"))
    if upstream_last_update is None:
        # The snapshot's identity key in fx.exchange_rate_snapshots is
        # (base_code, upstream_last_update) — without it we cannot store
        # idempotently, so refuse the payload.
        raise ExchangeRateAPIError(
            "upstream success payload missing/invalid time_last_update_utc",
            error_type="malformed-payload",
        )
    rates = {
        code.upper(): Decimal(str(value))
        for code, value in raw_rates.items()
        if isinstance(code, str)
    }
    return StandardRates(
        base_code=payload.get("base_code") or base_code.upper(),
        upstream_last_update=upstream_last_update,
        next_update_at=_parse_upstream_datetime(payload.get("time_next_update_utc")),
        conversion_rates=rates,
        payload=payload,
    )


def fetch_standard_rates(
    api_key: str,
    base_code: str = "USD",
    *,
    base_url: str | None = None,
    http_get: HttpGet | None = None,
) -> StandardRates:
    """Fetch the latest Standard snapshot for ``base_code``.

    Raises:
        ExchangeRateAPIError: non-success ``result``, HTTP error, or an
            unparseable / malformed body.
    """
    if not api_key or not api_key.strip():
        raise ExchangeRateAPIError("exchangerate api key is empty", error_type="no-key")
    resolved_base = (
        (base_url or os.environ.get("EXCHANGERATE_BASE_URL") or DEFAULT_BASE_URL)
        .strip()
        .rstrip("/")
    )
    code = base_code.strip().upper()
    url = f"{resolved_base}/{api_key.strip()}/latest/{code}"

    if http_get is None:
        http_get = _default_http_get()
    try:
        resp = http_get(url)
        payload = resp.json()
    except ExchangeRateAPIError:
        raise
    except Exception as exc:  # transport/parse boundary (chained re-raise)
        status_code = getattr(exc, "response", None)
        status_code = getattr(status_code, "status_code", None)
        raise ExchangeRateAPIError(
            f"exchangerate request failed: {type(exc).__name__}: {exc}",
            status_code=status_code,
        ) from exc

    result = payload.get("result") if isinstance(payload, dict) else None
    if result != "success":
        error_type = (
            payload.get("error-type") if isinstance(payload, dict) else None
        ) or "unknown"
        raise ExchangeRateAPIError(
            f"exchangerate upstream error: result={result!r} error-type={error_type!r}",
            error_type=str(error_type),
        )
    if not isinstance(payload, dict):
        raise ExchangeRateAPIError(
            "exchangerate success payload is not an object",
            error_type="malformed-payload",
        )
    return _parse_success(payload, code)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_HTTP_TIMEOUT",
    "ExchangeRateAPIError",
    "HttpGet",
    "StandardRates",
    "fetch_standard_rates",
]
