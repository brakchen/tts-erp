from __future__ import annotations

from decimal import (
    Decimal,
    Inexact,
    Overflow,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Subnormal,
    Underflow,
    getcontext,
    localcontext,
)
from typing import Iterable

import pytest

from tts_erp_v2.analytics.spu_profitability._price_math import (
    WeightedPriceSummary,
    summarize_weighted_prices,
)


D = Decimal
pytestmark = pytest.mark.layer_unit


def _expanded_oracle(rows: Iterable[tuple[Decimal, int]]) -> tuple[Decimal | None, Decimal | None]:
    """Independent small-input oracle; production code must not expand quantities."""
    units = [price for price, quantity in rows for _ in range(quantity)]
    if not units:
        return None, None
    ordered = sorted(units)
    mean = sum(ordered, D(0)) / D(len(ordered))
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / D(2)
    )
    return mean, median


def test_empty_input_returns_null_statistics() -> None:
    result = summarize_weighted_prices([])

    assert result == WeightedPriceSummary(None, None, 0, 0)


def test_quantity_weighted_mean_and_median_not_unweighted() -> None:
    result = summarize_weighted_prices([(D("10"), 1), (D("40"), 9)])

    assert result.mean_cny == D("37")
    assert result.median_cny == D("40")
    assert result.observed_quantity == 10
    assert result.observed_line_count == 2


def test_odd_weighted_population_uses_cumulative_bucket_rank() -> None:
    rows = [(D("20"), 1), (D("50"), 2), (D("80"), 2)]
    result = summarize_weighted_prices(rows)

    assert (result.mean_cny, result.median_cny) == _expanded_oracle(rows)
    assert result.mean_cny == D("56")
    assert result.median_cny == D("50")


def test_paid_price_decimal_mean_and_median() -> None:
    rows = [(D("18"), 1), (D("45"), 2), (D("72"), 2)]
    result = summarize_weighted_prices(rows)

    assert result.mean_cny == D("50.4")
    assert result.median_cny == D("45")


def test_even_population_averages_two_middle_prices() -> None:
    rows = [(D("10"), 2), (D("40"), 2)]
    result = summarize_weighted_prices(rows)

    assert result.mean_cny == D("25")
    assert result.median_cny == D("25")


def test_odd_and_even_ranks_within_a_duplicate_price_bucket() -> None:
    assert summarize_weighted_prices([(D("10"), 3), (D("30"), 2)]).median_cny == D("10")
    assert summarize_weighted_prices([(D("10"), 2), (D("30"), 3)]).median_cny == D("30")
    assert summarize_weighted_prices([(D("10"), 2), (D("30"), 2)]).median_cny == D("20")


def test_ties_mixed_order_and_duplicate_buckets_are_sorted_and_combined() -> None:
    rows = ((price, qty) for price, qty in [(D("40"), 1), (D("10"), 2), (D("40"), 3)])
    result = summarize_weighted_prices(rows)

    assert result.mean_cny == D("30")
    assert result.median_cny == D("40")
    assert result.observed_quantity == 6
    assert result.observed_line_count == 3


def test_generator_is_consumed_once() -> None:
    consumed = 0

    def rows():
        nonlocal consumed
        for row in [(D("2"), 1), (D("8"), 2)]:
            consumed += 1
            yield row

    result = summarize_weighted_prices(rows())

    assert consumed == 2
    assert result == WeightedPriceSummary(D("6"), D("8"), 3, 2)


def test_explicit_zero_is_a_valid_observation() -> None:
    result = summarize_weighted_prices([(D("0"), 2)])

    assert result == WeightedPriceSummary(D("0"), D("0"), 2, 1)


def test_large_weights_do_not_expand_units() -> None:
    result = summarize_weighted_prices([(D("1"), 10**12), (D("9"), 10**12 + 1)])

    assert result.observed_quantity == 2_000_000_000_001
    assert result.observed_line_count == 2
    assert result.median_cny == D("9")
    assert result.mean_cny == D(
        "5.0000000000019999999999990000000000004999999999997500000000001249999999999375000"
    )


def test_decimal_results_do_not_depend_on_low_ambient_precision() -> None:
    with localcontext() as context:
        context.prec = 6
        result = summarize_weighted_prices(
            [(D("1.123456789"), 1), (D("2.987654321"), 1)]
        )

    assert result.mean_cny == D("2.055555555")
    assert result.median_cny == D("2.055555555")


def test_recurring_mean_ignores_ambient_rounding_mode() -> None:
    results = []
    for rounding in (ROUND_DOWN, ROUND_UP):
        with localcontext() as context:
            context.prec = 12
            context.rounding = rounding
            results.append(summarize_weighted_prices([(D("0"), 1), (D("1"), 2)]).mean_cny)

    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_HALF_EVEN
        expected = (D("1") * 2) / D(3)

    assert results == [expected, expected]


def test_valid_large_price_ignores_restricted_ambient_emax() -> None:
    with localcontext() as context:
        context.Emax = 2
        context.traps[Overflow] = True
        result = summarize_weighted_prices([(D("1000"), 1)])

    assert result.mean_cny == D("1000")


def test_valid_small_price_ignores_restricted_emin_and_subnormal_trap() -> None:
    with localcontext() as context:
        context.Emin = -2
        context.traps[Subnormal] = True
        context.traps[Underflow] = True
        result = summarize_weighted_prices([(D("0.001"), 1)])

    assert result.mean_cny == D("0.001")


def test_inherited_flags_and_traps_do_not_affect_normal_calculation() -> None:
    with localcontext() as context:
        for signal in context.traps:
            context.traps[signal] = True
            context.flags[signal] = True
        result = summarize_weighted_prices([(D("0"), 1), (D("1"), 2)])

    assert result.mean_cny is not None
    assert result.mean_cny.as_tuple().digits


def test_wide_price_exponents_are_not_lost_in_weighted_sum() -> None:
    with localcontext() as context:
        context.prec = 256
        expected = (D("1e100") + D("1e-100")) / D(2)

    result = summarize_weighted_prices([(D("1e100"), 1), (D("1e-100"), 1)])

    assert result.mean_cny == expected


def test_coalesced_quantity_digits_are_included_in_precision_bound() -> None:
    rows = [(D("1e100"), 9)] * 22_222 + [(D("1"), 2)]
    with localcontext() as context:
        context.prec = 120
        expected = (D("1e100") * D(199_998) + D(2)) / D(200_000)

    result = summarize_weighted_prices(rows)

    assert result.mean_cny == expected
    assert result.mean_cny is not None
    assert result.mean_cny != D("9.9999e99")


def test_result_is_immutable() -> None:
    result = summarize_weighted_prices([(D("2"), 1)])

    with pytest.raises(AttributeError):
        result.mean_cny = D("3")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([(1, 1)], TypeError),
        ([(D("NaN"), 1)], ValueError),
        ([(D("Infinity"), 1)], ValueError),
        ([(D("-0.01"), 1)], ValueError),
        ([(D("1"), True)], TypeError),
        ([(D("1"), 1.0)], TypeError),
        ([(D("1"), 0)], ValueError),
        ([(D("1"), -1)], ValueError),
        ([(D("1"), 1.5)], TypeError),
    ],
)
def test_invalid_price_or_quantity_fails_loudly(rows, error: type[Exception]) -> None:
    with pytest.raises(error):
        summarize_weighted_prices(rows)


def test_invalid_input_does_not_partially_return_statistics() -> None:
    with pytest.raises(ValueError):
        summarize_weighted_prices([(D("1"), 1), (D("-1"), 1)])


def test_no_global_decimal_context_mutation() -> None:
    with localcontext() as context:
        context.prec = 17
        context.rounding = ROUND_UP
        context.Emax = 77
        context.Emin = -88
        context.clamp = 1
        context.traps[Overflow] = True
        context.flags[Inexact] = True
        before = context.copy()

        summarize_weighted_prices([(D("1"), 1), (D("3"), 1)])

        after = context
        assert after.prec == before.prec
        assert after.rounding == before.rounding
        assert after.Emax == before.Emax
        assert after.Emin == before.Emin
        assert after.clamp == before.clamp
        assert after.traps == before.traps
        assert after.flags == before.flags
