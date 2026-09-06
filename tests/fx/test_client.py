"""ExchangeRate-API client parsing vectors (no DB, no network).

Covers the Standard-endpoint contract as documented at
https://www.exchangerate-api.com/docs/standard-requests — including the
doc's sample timestamps ("Fri, 27 Mar 2020 00:00:00 +0000") and the
error envelope (``result != "success"`` + ``error-type``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from tts_erp_v2.proxy.exchangerate import (
    ExchangeRateAPIError,
    fetch_standard_rates,
)

SAMPLE_PAYLOAD = {
    "result": "success",
    "documentation": "https://www.exchangerate-api.com/docs",
    "terms_of_use": "https://www.exchangerate-api.com/terms",
    "time_last_update_unix": 1585267200,
    "time_last_update_utc": "Fri, 27 Mar 2020 00:00:00 +0000",
    "time_next_update_unix": 1585353700,
    "time_next_update_utc": "Sat, 28 Mar 2020 00:00:00 +0000",
    "base_code": "USD",
    "conversion_rates": {
        "USD": 1,
        "AUD": 1.4817,
        "BGN": 1.7741,
        "CAD": 1.3168,
        "CHF": 0.9774,
        "CNY": 6.9454,
        "EUR": 0.9013,
        "GBP": 0.7679,
    },
}


def _stub_get(payload: dict) -> tuple[Callable[[str], httpx.Response], list[str]]:
    """Return (fake http_get, calls) — records requested URLs."""
    calls: list[str] = []

    def _get(url: str) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, json=payload)

    return _get, calls


def test_parses_documented_success_payload() -> None:
    http_get, calls = _stub_get(SAMPLE_PAYLOAD)
    rates = fetch_standard_rates("KEY123", "USD", http_get=http_get)

    assert calls == ["https://v6.exchangerate-api.com/v6/KEY123/latest/USD"]
    assert rates.base_code == "USD"
    # RFC-2822 timestamps parse to aware UTC datetimes.
    assert rates.upstream_last_update == datetime(2020, 3, 27, 0, 0, tzinfo=UTC)
    assert rates.next_update_at == datetime(2020, 3, 28, 0, 0, tzinfo=UTC)
    # Rates land as Decimals (no float drift for money-grade math).
    assert rates.conversion_rates["CNY"] == Decimal("6.9454")
    assert rates.conversion_rates["USD"] == Decimal(1)
    assert len(rates.conversion_rates) == 8
    # Raw payload preserved for the integration.raw_records audit row.
    # (httpx Response.json() re-parses, so compare by value not identity.)
    assert rates.payload == SAMPLE_PAYLOAD


def test_base_url_and_code_normalised() -> None:
    http_get, calls = _stub_get(SAMPLE_PAYLOAD)

    fetch_standard_rates(
        "k", " usd ", base_url="https://example.com/v6/", http_get=http_get
    )
    assert calls == ["https://example.com/v6/k/latest/USD"]


def test_error_result_raises_with_error_type() -> None:
    payload = {"result": "error", "error-type": "quota-exceeded"}
    http_get, _ = _stub_get(payload)
    with pytest.raises(ExchangeRateAPIError) as excinfo:
        fetch_standard_rates("k", http_get=http_get)
    assert excinfo.value.error_type == "quota-exceeded"
    assert "quota-exceeded" in str(excinfo.value)


def test_http_transport_failure_wrapped() -> None:
    def _boom(url: str) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ExchangeRateAPIError) as excinfo:
        fetch_standard_rates("k", http_get=_boom)
    assert "ConnectError" in str(excinfo.value)


def test_http_error_status_wrapped() -> None:
    def _error(url: str) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ExchangeRateAPIError):
        fetch_standard_rates("k", http_get=_error)


def test_missing_conversion_rates_is_malformed() -> None:
    payload = dict(SAMPLE_PAYLOAD)
    payload.pop("conversion_rates")
    http_get, _ = _stub_get(payload)
    with pytest.raises(ExchangeRateAPIError) as excinfo:
        fetch_standard_rates("k", http_get=http_get)
    assert excinfo.value.error_type == "malformed-payload"


def test_missing_upstream_timestamp_is_malformed() -> None:
    """time_last_update_utc is the snapshot identity — refuse without it."""
    payload = dict(SAMPLE_PAYLOAD)
    payload.pop("time_last_update_utc")
    http_get, _ = _stub_get(payload)
    with pytest.raises(ExchangeRateAPIError) as excinfo:
        fetch_standard_rates("k", http_get=http_get)
    assert excinfo.value.error_type == "malformed-payload"


def test_missing_next_update_keeps_none_horizon() -> None:
    payload = dict(SAMPLE_PAYLOAD)
    payload.pop("time_next_update_utc")
    http_get, _ = _stub_get(payload)
    rates = fetch_standard_rates("k", http_get=http_get)
    assert rates.next_update_at is None
    assert rates.conversion_rates["CNY"] == Decimal("6.9454")


def test_empty_api_key_refused_before_network() -> None:
    def _should_not_run(url: str) -> httpx.Response:
        raise AssertionError("must not issue a request without a key")

    with pytest.raises(ExchangeRateAPIError) as excinfo:
        fetch_standard_rates("  ", http_get=_should_not_run)
    assert excinfo.value.error_type == "no-key"
