from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    MAX_EMAX,
    MAX_PREC,
    MIN_EMIN,
    Decimal,
    ROUND_HALF_EVEN,
    localcontext,
)
from typing import Iterable


@dataclass(frozen=True, slots=True)
class WeightedPriceSummary:
    """Quantity-weighted CNY price statistics for one observation population."""

    mean_cny: Decimal | None
    median_cny: Decimal | None
    observed_quantity: int
    observed_line_count: int


def _context_parameters(
    buckets: dict[Decimal, int], total_quantity: int
) -> tuple[int, int, int]:
    """Return precision and exponent bounds derived solely from the inputs.

    The precision spans every significant decimal position in the weighted
    products, so adding widely separated finite prices does not discard the
    smaller term. Values whose required span exceeds the Decimal module's
    representable context are rejected instead of being silently rounded.
    """
    nonzero = [(price, quantity) for price, quantity in buckets.items() if not price.is_zero()]
    if not nonzero:
        return 80, 999_999, -999_999

    min_exponent = min(price.as_tuple().exponent for price, _ in nonzero)
    # Use coalesced bucket quantities: arithmetic multiplies by these values,
    # which may have more digits than any individual input line quantity.
    max_adjusted_product = max(
        price.adjusted() + len(str(quantity))
        for price, quantity in nonzero
    )
    max_product_digits = max(
        len(price.as_tuple().digits) + len(str(quantity)) + 1
        for price, quantity in nonzero
    )
    total_quantity_digits = len(str(total_quantity))
    # The total quantity also bounds carries while summing distinct buckets;
    # unlike a fixed allowance, this scales with the supported population.
    precision = max(
        80,
        max_adjusted_product - min_exponent + total_quantity_digits + 1,
        max_product_digits + total_quantity_digits,
    )
    if precision > MAX_PREC:
        raise ValueError("price exponent span exceeds Decimal context capacity")

    # Division by total quantity can move the least significant exponent down;
    # the same total-quantity bound covers sum carries in the upper direction.
    emin = min(-999_999, min_exponent - total_quantity_digits - 1)
    emax = max(999_999, max_adjusted_product + total_quantity_digits + 1)
    if emin < MIN_EMIN or emax > MAX_EMAX:
        raise ValueError("price exponent exceeds Decimal context capacity")
    return precision, emax, emin


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
    used for precision, rounding, exponent bounds, flags, or traps. No output
    quantization is performed here.
    """
    buckets: dict[Decimal, int] = {}
    total_quantity = 0
    line_count = 0

    for observation in observations:
        price, quantity = _validate_observation(observation)
        buckets[price] = buckets.get(price, 0) + quantity
        total_quantity += quantity
        line_count += 1

    if not buckets:
        return WeightedPriceSummary(None, None, 0, 0)

    precision, emax, emin = _context_parameters(buckets, total_quantity)
    with localcontext() as context:
        context.prec = precision
        context.rounding = ROUND_HALF_EVEN
        context.Emax = emax
        context.Emin = emin
        context.clamp = 0
        context.clear_flags()
        for signal in context.traps:
            context.traps[signal] = False

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

    if not mean.is_finite() or not median.is_finite():
        raise ValueError("price arithmetic exceeded Decimal context capacity")
    return WeightedPriceSummary(mean, median, total_quantity, line_count)
