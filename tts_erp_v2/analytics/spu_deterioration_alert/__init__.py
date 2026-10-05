"""SPU profit deterioration alert domain.

The package owns windowing, aggregation and severity policy.  HTTP and SQL
adapters consume these typed seams; profitability remains owned by the
canonical SPU profitability module.
"""

from .config import (
    ALERT_CONFIG_KEY,
    SEED_FALLBACK_CONFIG,
    AlertConfig,
    AlertLayerConfig,
    AlertThresholds,
    validate_alert_config,
)
from .facts import DailyFact, WindowMetric, aggregate_facts
from .policy import (
    AlertDecision,
    AlertSeverity,
    AlertState,
    SampleStatus,
    compare_windows,
    confirmation_windows,
    evaluate,
    fast_windows,
    paired_recovery,
)

__all__ = [
    "ALERT_CONFIG_KEY",
    "AlertConfig",
    "AlertLayerConfig",
    "AlertThresholds",
    "SEED_FALLBACK_CONFIG",
    "validate_alert_config",
    "DailyFact",
    "WindowMetric",
    "aggregate_facts",
    "AlertDecision",
    "AlertSeverity",
    "AlertState",
    "SampleStatus",
    "compare_windows",
    "confirmation_windows",
    "evaluate",
    "fast_windows",
    "paired_recovery",
]
