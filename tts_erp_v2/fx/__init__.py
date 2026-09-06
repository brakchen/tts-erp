"""tts_erp_v2.fx — cached exchange-rate read service (never dials upstream).

The upstream (ExchangeRate-API, quota-billed) is only ever contacted by
the ``fx.sync`` sync-worker job; every read path in this package serves
the cached ``fx.*`` tables and derives currency pairs locally.
"""

from __future__ import annotations

from tts_erp_v2.fx.rates import (
    DEFAULT_BASE_CODE,
    RATE_QUANTUM,
    Conversion,
    RateMap,
    UnknownCurrencyError,
    convert,
    convert_or_none,
    load_rate_map,
    quantize_rate,
)

__all__ = [
    "DEFAULT_BASE_CODE",
    "RATE_QUANTUM",
    "Conversion",
    "RateMap",
    "UnknownCurrencyError",
    "convert",
    "convert_or_none",
    "load_rate_map",
    "quantize_rate",
]
