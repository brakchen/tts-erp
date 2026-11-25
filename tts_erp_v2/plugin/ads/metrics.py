"""Normalize source metrics without changing their raw archived representation."""

from __future__ import annotations

from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

_DECIMAL_FIELDS = (
    "mixed_real_cost",
    "onsite_roi2_shopping_value",
    "onsite_mixed_real_roi2_shopping",
)
_COUNT_FIELD = "onsite_roi2_shopping_sku"
_BIGINT_MIN = Decimal(-(2**63))
_BIGINT_MAX = Decimal(2**63 - 1)
_NUMERIC_INTEGER_LIMIT = Decimal("1e16")
_NUMERIC_SCALE = Decimal("0.0001")
_NUMERIC_CONTEXT = Context(prec=24, rounding=ROUND_HALF_UP)


class InvalidAdMetric(ValueError):
    """A non-placeholder metric cannot be represented by its structured column."""

    def __init__(self, row_index: int, field: str, reason: str) -> None:
        self.row_index = row_index
        self.field = field
        self.reason = reason
        super().__init__(f"rows[{row_index}].{field}: {reason}")


def _decimal(value: Any, *, row_index: int, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in ("", "-"):
            return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise InvalidAdMetric(
            row_index, field, "expected a finite number or a null placeholder"
        )
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidAdMetric(
            row_index, field, "expected a finite number or a null placeholder"
        ) from exc
    if not amount.is_finite():
        raise InvalidAdMetric(row_index, field, "non-finite numbers are not supported")
    return amount


def normalize_ad_metrics(
    row: dict[str, Any], *, row_index: int
) -> dict[str, Decimal | int | None]:
    """Only None, blank strings and '-' mean unknown; zero remains an actual zero."""
    metrics: dict[str, Decimal | int | None] = {}
    for field in _DECIMAL_FIELDS:
        amount = _decimal(row.get(field), row_index=row_index, field=field)
        if amount is not None:
            if amount.copy_abs() >= _NUMERIC_INTEGER_LIMIT:
                raise InvalidAdMetric(row_index, field, "outside NUMERIC(20,4) range")
            # PostgreSQL rounds NUMERIC ties away from zero before checking
            # precision: https://www.postgresql.org/docs/current/datatype-numeric.html
            # Use Decimal's standard ROUND_HALF_UP solely for that range check;
            # the original finite Decimal is bound so PostgreSQL still owns scale.
            with localcontext(_NUMERIC_CONTEXT):
                rounded = amount.quantize(_NUMERIC_SCALE)
            if rounded.copy_abs() >= _NUMERIC_INTEGER_LIMIT:
                raise InvalidAdMetric(
                    row_index, field, "outside NUMERIC(20,4) range after rounding"
                )
        metrics[field] = amount

    count = _decimal(row.get(_COUNT_FIELD), row_index=row_index, field=_COUNT_FIELD)
    if count is not None:
        if count != count.to_integral_value():
            raise InvalidAdMetric(
                row_index, _COUNT_FIELD, "expected an integer without a fractional part"
            )
        if not _BIGINT_MIN <= count <= _BIGINT_MAX:
            raise InvalidAdMetric(row_index, _COUNT_FIELD, "outside BIGINT range")
    metrics[_COUNT_FIELD] = None if count is None else int(count)
    return metrics
