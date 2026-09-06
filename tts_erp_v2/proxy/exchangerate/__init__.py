"""tts_erp_v2.proxy.exchangerate — ExchangeRate-API outbound layer.

Single upstream surface for v6.exchangerate-api.com (Free plan = 1500
requests / month, overage is billed). Only the **Standard endpoint**
(``GET /v6/{key}/latest/{base}``) is used — it returns rates for every
supported currency in one request, which is what makes the quota budget
viable (~1 request/day). Everything downstream (pair conversion, cross
currency math) is derived locally from the cached snapshot.

Auth: the API key travels in the URL (upstream's default scheme). It is
read from env by the sync job and NEVER appears in /v2 responses, logs
or the browser — the API process has no path to this client.
"""

from __future__ import annotations

from tts_erp_v2.proxy.exchangerate.client import (
    DEFAULT_BASE_URL,
    DEFAULT_HTTP_TIMEOUT,
    ExchangeRateAPIError,
    StandardRates,
    fetch_standard_rates,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_HTTP_TIMEOUT",
    "ExchangeRateAPIError",
    "StandardRates",
    "fetch_standard_rates",
]
