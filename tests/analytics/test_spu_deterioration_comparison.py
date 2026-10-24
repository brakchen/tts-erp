from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytestmark = pytest.mark.layer_unit


def test_comparison_periods_are_disjoint_and_inclusive() -> None:
    from tts_erp_v2.analytics.spu_deterioration_alert.comparison import periods

    assert periods(date(2026, 10, 6), 7) == (
        (date(2026, 9, 23), date(2026, 9, 29)),
        (date(2026, 9, 30), date(2026, 10, 6)),
    )
    assert periods(date(2026, 10, 6), 3) == (
        (date(2026, 10, 1), date(2026, 10, 3)),
        (date(2026, 10, 4), date(2026, 10, 6)),
    )


@pytest.mark.parametrize("window_days", [3, 7])
def test_periods_reject_calendar_underflow(window_days: int) -> None:
    from tts_erp_v2.analytics.spu_deterioration_alert.comparison import (
        ComparisonInputError,
        periods,
    )

    with pytest.raises(ComparisonInputError):
        periods(date.min, window_days)


@pytest.mark.parametrize(
    ("orders", "spend", "expected"),
    [
        (0, Decimal(".00001"), True),
        (1, Decimal(1), False),
        (0, Decimal(0), False),
        (None, Decimal(1), None),
        (0, None, None),
    ],
)
def test_no_order_spend_uses_unrounded_known_facts(orders, spend, expected) -> None:
    from tts_erp_v2.analytics.spu_deterioration_alert.comparison import (
        no_order_ad_spend,
    )

    assert no_order_ad_spend(orders, spend) is expected
