"""Unit contract for quantity-weighted price metric assembly.

These tests exercise the pure Decimal helpers of
``tts_erp_v2.analytics.spu_profitability._price_stats`` without a database: the
metric assembly (weighted mean/median, coverage status, zero-versus-null),
the validated native→CNY rate derivation and the price sort identifiers.

The independent oracle expands units by hand so a broken bucketed/weighted
implementation cannot pass by agreeing with itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tts_erp_v2.analytics.spu_profitability._price_stats import (
    PriceCoverage,
    PriceCoverageStatus,
    PriceMetric,
    PriceSource,
    _cny_unit_price,
    _purchase_metric,
    is_price_sort_field,
    native_to_cny_rates,
    price_sort_value,
    summarize_price_metric,
)
from tts_erp_v2.analytics.spu_profitability._types import (
    FxBasis,
    PriceFxUnavailable,
)

D = Decimal
pytestmark = pytest.mark.layer_unit


def _oracle(rows: Iterable[tuple[Decimal, int]]) -> tuple[Decimal | None, Decimal | None]:
    """Expand units in the test only; production must bucket instead."""
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


def _metric(**overrides) -> PriceMetric:
    params = {
        "eligible_quantity": 1,
        "eligible_line_count": 1,
        "observed_buckets": {D("10"): 1},
        "observed_line_count": 1,
        "source": PriceSource.TIKTOK_LINE_ITEM_SALE_PRICE,
    }
    params.update(overrides)
    return summarize_price_metric(**params)


def test_empty_population_is_no_samples_not_zero() -> None:
    metric = _metric(
        eligible_quantity=0,
        eligible_line_count=0,
        observed_buckets={},
        observed_line_count=0,
        source=PriceSource.ROI_UNIT_COST,
        estimated=True,
    )

    assert metric.mean_cny is None
    assert metric.median_cny is None
    assert metric.eligible_quantity == 0
    assert metric.observed_quantity == 0
    assert metric.coverage_ratio is None
    assert metric.status is PriceCoverageStatus.NO_SAMPLES
    assert metric.source is PriceSource.NONE
    assert metric.estimated is False


def test_weighted_mean_and_median_are_not_row_average() -> None:
    metric = _metric(
        eligible_quantity=10,
        eligible_line_count=2,
        observed_buckets={D("10"): 1, D("40"): 9},
        observed_line_count=2,
    )

    assert metric.mean_cny == D("37")
    assert metric.median_cny == D("40")
    # 行均值/行中位数都是 25 —— 上面两个值必须不同
    assert metric.observed_quantity == 10
    assert metric.observed_line_count == 2
    assert metric.coverage_ratio == D("1")
    assert metric.status is PriceCoverageStatus.COMPLETE


def test_odd_and_even_medians_match_the_expanded_oracle() -> None:
    odd_rows = [(D("20"), 1), (D("50"), 2), (D("80"), 2)]
    odd = _metric(
        eligible_quantity=5,
        eligible_line_count=3,
        observed_buckets=dict(odd_rows),
        observed_line_count=3,
    )
    assert (odd.mean_cny, odd.median_cny) == _oracle(odd_rows)
    assert odd.median_cny == D("50")

    even_rows = [(D("10"), 2), (D("40"), 2)]
    even = _metric(
        eligible_quantity=4,
        eligible_line_count=2,
        observed_buckets=dict(even_rows),
        observed_line_count=2,
    )
    assert (even.mean_cny, even.median_cny) == _oracle(even_rows)
    assert even.mean_cny == D("25")
    assert even.median_cny == D("25")


def test_single_observation_has_identical_mean_and_median() -> None:
    metric = _metric(observed_buckets={D("12.5"): 3}, eligible_quantity=3)

    assert metric.mean_cny == D("12.5")
    assert metric.median_cny == D("12.5")
    assert metric.status is PriceCoverageStatus.COMPLETE


def test_explicit_zero_price_is_an_observation() -> None:
    metric = _metric(observed_buckets={D("0"): 2}, eligible_quantity=2)

    assert metric.mean_cny == D("0")
    assert metric.median_cny == D("0")
    assert metric.observed_quantity == 2
    assert metric.status is PriceCoverageStatus.COMPLETE


def test_missing_price_quantity_yields_null_statistics_and_partial_status() -> None:
    metric = _metric(
        eligible_quantity=9,
        eligible_line_count=2,
        observed_buckets={},
        observed_line_count=0,
        missing_quantity=9,
        missing_line_count=1,
        source=PriceSource.TIKTOK_LINE_ITEM_ORIGINAL_PRICE,
    )

    assert metric.mean_cny is None
    assert metric.median_cny is None
    assert metric.observed_quantity == 0
    assert metric.missing_quantity == 9
    assert metric.missing_line_count == 1
    assert metric.coverage_ratio == D("0")
    assert metric.status is PriceCoverageStatus.PARTIAL
    assert metric.source is PriceSource.TIKTOK_LINE_ITEM_ORIGINAL_PRICE


def test_invalid_price_quantity_is_tracked_separately_from_missing() -> None:
    metric = _metric(
        eligible_quantity=6,
        eligible_line_count=3,
        observed_buckets={D("5"): 2},
        observed_line_count=1,
        missing_quantity=1,
        missing_line_count=1,
        invalid_quantity=3,
        invalid_line_count=1,
    )

    assert metric.mean_cny == D("5")
    assert metric.observed_quantity == 2
    assert metric.missing_quantity == 1
    assert metric.invalid_quantity == 3
    assert metric.coverage_ratio == D(2) / D(6)


def test_counts_must_conserve_the_eligible_quantity() -> None:
    with pytest.raises(ValueError, match="conserve the eligible quantity"):
        _metric(
            eligible_quantity=3,
            eligible_line_count=1,
            observed_buckets={D("5"): 1},
            observed_line_count=1,
        )


def test_line_counts_cannot_exceed_the_eligible_line_count() -> None:
    with pytest.raises(ValueError, match="line counts exceed"):
        _metric(
            eligible_quantity=1,
            eligible_line_count=1,
            observed_buckets={D("5"): 1},
            observed_line_count=2,
        )


def test_default_cost_purchase_metric_is_estimated_and_manual_is_not() -> None:
    coverage = PriceCoverage(eligible_line_count=2, eligible_quantity=9)
    default = _purchase_metric(coverage, D(40), "DEFAULT_K1")
    manual = _purchase_metric(coverage, D(25), "MANUAL")

    assert default.mean_cny == D(40)
    assert default.median_cny == D(40)
    assert default.observed_quantity == 9
    assert default.source is PriceSource.ROI_UNIT_COST
    assert default.estimated is True
    assert manual.mean_cny == D(25)
    assert manual.estimated is False

    empty = _purchase_metric(PriceCoverage(), D(40), "DEFAULT_K1")
    assert empty.status is PriceCoverageStatus.NO_SAMPLES
    assert empty.mean_cny is None
    assert empty.estimated is False


def _fx_basis(usd_cny: str, usd_vnd: str) -> FxBasis:
    return FxBasis(
        snapshot_id=901,
        usd_cny=D(usd_cny),
        cny_usd=D(1) / D(usd_cny),
        usd_vnd=D(usd_vnd),
        as_of=datetime(2026, 10, 5, 11, 59, tzinfo=UTC),
    )


def test_native_to_cny_rates_use_the_snapshot() -> None:
    rates = native_to_cny_rates(_fx_basis("6.5", "26000"))

    assert rates["CNY"] == D(1)
    assert rates["USD"] == D("6.5")
    assert rates["VND"] == D("6.5") / D(26000)
    assert _cny_unit_price(D(40000), rates["VND"]) == D(10)
    assert _cny_unit_price(D(4), rates["USD"]) == D(26)


def test_non_positive_or_non_finite_rates_fail_closed() -> None:
    with pytest.raises(PriceFxUnavailable):
        native_to_cny_rates(_fx_basis("6.5", "0"))
    with pytest.raises(PriceFxUnavailable):
        native_to_cny_rates(_fx_basis("-1", "26000"))
    with pytest.raises(PriceFxUnavailable):
        native_to_cny_rates(_fx_basis("NaN", "26000"))


def test_converted_price_rejects_non_finite_and_negative_native_values() -> None:
    with pytest.raises(ValueError):
        _cny_unit_price(D("-1"), D(1))
    with pytest.raises(ValueError):
        _cny_unit_price(D("Infinity"), D(1))


def test_classification_partition_total_sums_every_reason() -> None:
    coverage = PriceCoverage(
        selected_line_count=5,
        eligible_line_count=1,
        excluded_unpaid_line_count=1,
        excluded_cancelled_line_count=1,
        invalid_quantity_line_count=1,
        missing_observation_line_count=1,
    )

    assert coverage.classification_partition_total() == coverage.selected_line_count
    assert PriceCoverage().classification_partition_total() == 0


def test_price_sort_identifiers_map_to_the_six_metrics() -> None:
    coverage = PriceCoverage(eligible_line_count=1, eligible_quantity=1)
    from tts_erp_v2.analytics.spu_profitability._price_stats import (
        PriceStatsOverview,
        SpuPriceStats,
    )

    stats = SpuPriceStats(
        spu_pk=12,
        purchase=_purchase_metric(coverage, D("10"), "MANUAL"),
        original_sale=_metric(
            observed_buckets={D("20"): 1},
            source=PriceSource.TIKTOK_LINE_ITEM_ORIGINAL_PRICE,
        ),
        paid=_metric(observed_buckets={D("18"): 1}),
        coverage=coverage,
    )
    overview = PriceStatsOverview(
        by_spu={12: stats},
        totals=stats,
        cost_basis_fingerprint="sha256:test",
        cost_basis_estimated=False,
        default_k1_cny=D(40),
    )

    assert is_price_sort_field("purchasePriceMean") is True
    assert is_price_sort_field("paidPriceMedian") is True
    assert is_price_sort_field("purchase_price_mean") is False
    assert is_price_sort_field("net_profit") is False
    assert price_sort_value(overview, "purchasePriceMean", 12) == D("10")
    assert price_sort_value(overview, "purchasePriceMedian", 12) == D("10")
    assert price_sort_value(overview, "originalSalePriceMean", 12) == D("20")
    assert price_sort_value(overview, "purchasePriceMean", 99) is None
    assert price_sort_value(None, "purchasePriceMean", 12) is None
