from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import Iterable


@dataclass(frozen=True, slots=True)
class WeightedPriceSummary:
    """Quantity-weighted CNY price statistics for one observation population."""

    mean_cny: Decimal | None
    median_cny: Decimal | None
    observed_quantity: int
    observed_line_count: int


def _context_precision(
    prices: list[Decimal], quantities: list[int], total_quantity: int
) -> int:
    """Choose precision from inputs, rather than inheriting a caller's context.

    Prices are expected to originate from NUMERIC/native-to-CNY values (normally
    at most a few dozen significant digits). The input-derived floor leaves
    room for multiplication by large integer quantities, summation carry, and
    a useful division result without quantizing the returned Decimal.
    """
    max_price_digits = max(len(price.as_tuple().digits) for price in prices)
    max_quantity_digits = max(len(str(quantity)) for quantity in quantities)
    return max(80, max_price_digits + max_quantity_digits + len(str(total_quantity)) + 16)


def _validate_observation(observation: object) -> tuple[Decimal, int]:
    try:
        price, quantity = observation  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise TypeError("each observation must contain (Decimal price, integer quantity)") from exc

    if not isinstance(price, Decimal):
        raise TypeError("price must be a Decimal")
    if not price.is_finite():
        raise ValueError("price must be finite")
    if price < 0:
        raise ValueError("price must be non-negative")
    if type(quantity) is not int:  # bool is an int subclass, but is not a quantity.
        raise TypeError("quantity must be an integer")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return price, quantity


def _price_at_rank(buckets: dict[Decimal, int], rank: int) -> Decimal:
    cumulative = 0
    for price in sorted(buckets):
        cumulative += buckets[price]
        if rank <= cumulative:
            return price
    raise AssertionError("median rank exceeded observed quantity")


def summarize_weighted_prices(
    observations: Iterable[tuple[Decimal, int]],
) -> WeightedPriceSummary:
    """Summarize finite, non-negative CNY prices without expanding quantities.

    The iterable is consumed once into unique-price buckets. Median ranks are
    resolved against cumulative bucket quantities, and arithmetic runs in a
    private Decimal context sized from the inputs; the ambient context is not
    changed. No output quantization is performed here.
    """
    buckets: dict[Decimal, int] = {}
    prices: list[Decimal] = []
    quantities: list[int] = []
    total_quantity = 0
    line_count = 0

    for observation in observations:
        price, quantity = _validate_observation(observation)
        buckets[price] = buckets.get(price, 0) + quantity
        prices.append(price)
        quantities.append(quantity)
        total_quantity += quantity
        line_count += 1

    if not buckets:
        return WeightedPriceSummary(None, None, 0, 0)

    with localcontext() as context:
        context.prec = _context_precision(prices, quantities, total_quantity)
        # A recurring mean is valid; caller trap settings must not turn its
        # precision-bound representation into an unrelated failure.
        context.traps[Inexact] = False
        context.traps[Rounded] = False
        weighted_sum = sum(
            (price * quantity for price, quantity in buckets.items()),
            Decimal(0),
        )
        mean = weighted_sum / Decimal(total_quantity)
        lower_rank = total_quantity // 2
        if total_quantity % 2:
            median = _price_at_rank(buckets, lower_rank + 1)
        else:
            median = (
                _price_at_rank(buckets, lower_rank)
                + _price_at_rank(buckets, lower_rank + 1)
            ) / Decimal(2)

    return WeightedPriceSummary(mean, median, total_quantity, line_count)
