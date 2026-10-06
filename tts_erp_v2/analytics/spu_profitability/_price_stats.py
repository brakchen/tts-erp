"""Quantity-weighted CNY price statistics for the SPU price panels.

The module reads the immutable TikTok line-price observations written by
``tts_erp_v2.jobs.tiktok.order_prices`` plus the current ROI effective-cost map
resolved by ``_implementation._resolve_costs_batch``, and produces the
``purchase`` / ``originalSale`` / ``paid`` mean+median metrics and the
population coverage counters shared by ``/v2/pages/spu-roi`` and
``/v2/pages/focused-spus``.

Approved contract: ``docs/design/spu-price-statistics.md`` (measurement) and
``docs/business/spu-profitability.md`` (business rubric).  Arithmetic is
delegated to the approved pure-Decimal kernel
``_price_math.summarize_weighted_prices``; this module only owns eligibility,
coverage classification, the per-observation FX conversion and metric assembly.
Refund facts are never joined here, so a later refund or full loss cannot erase
a historical paid price observation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability._price_math import (
    summarize_weighted_prices,
)
from tts_erp_v2.analytics.spu_profitability._types import (
    DEFAULT_COST_SOURCE,
    FxBasis,
    PriceFxUnavailable,
)
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES

# Line currencies that can be converted inside the read snapshot.  Anything else
# is rejected instead of guessed (docs/design/spu-price-statistics.md §2.3).
SUPPORTED_PRICE_CURRENCIES = ("CNY", "USD", "VND")

# Private conversion context (docs/design/spu-price-statistics.md §4.2): fixed
# precision and exponent bounds, ROUND_HALF_EVEN, exact-signal traps on, and
# inexact/rounded traps off so a rate division may stay finite.
_CONVERSION_PRECISION = 80
_CONVERSION_EMAX = 999_999
_CONVERSION_EMIN = -999_999

# Metric field selectors used by the bucket query.  ``original``/``paid`` are the
# two TikTok authority fields.
_ORIGINAL = "original"
_PAID = "paid"


class PriceCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_SAMPLES = "no_samples"
    UNAVAILABLE = "unavailable"


class PriceSource(StrEnum):
    ROI_UNIT_COST = "roi_unit_cost"
    TIKTOK_LINE_ITEM_ORIGINAL_PRICE = "tiktok_line_item_original_price"
    TIKTOK_LINE_ITEM_SALE_PRICE = "tiktok_line_item_sale_price"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class PriceMetric:
    """One quantity-weighted metric plus its independent field coverage."""

    mean_cny: Decimal | None
    median_cny: Decimal | None
    eligible_quantity: int
    observed_quantity: int
    missing_quantity: int
    invalid_quantity: int
    observed_line_count: int
    missing_line_count: int
    invalid_line_count: int
    coverage_ratio: Decimal | None
    status: PriceCoverageStatus
    source: PriceSource
    estimated: bool


@dataclass(frozen=True, slots=True)
class PriceCoverage:
    """Population classification counts for one SPU or the whole scope.

    The per-reason line counts and ``selected_line_count`` are internal
    diagnostics: they verify the mutually-exclusive partition of
    ``population_lines`` and are deliberately not part of the wire payload.
    """

    selected_line_count: int = 0
    eligible_line_count: int = 0
    eligible_quantity: int = 0
    excluded_unpaid_line_count: int = 0
    excluded_unpaid_quantity: int = 0
    excluded_on_hold_line_count: int = 0
    excluded_on_hold_quantity: int = 0
    excluded_cancelled_line_count: int = 0
    excluded_cancelled_quantity: int = 0
    excluded_gift_line_count: int = 0
    excluded_gift_quantity: int = 0
    unknown_gift_line_count: int = 0
    unknown_gift_quantity: int = 0
    unknown_status_line_count: int = 0
    unknown_status_quantity: int = 0
    invalid_quantity_line_count: int = 0
    excluded_valid_quantity: int = 0
    missing_currency_quantity: int = 0
    fx_unavailable_quantity: int = 0
    missing_observation_line_count: int = 0

    def classification_partition_total(self) -> int:
        """Sum of the mutually-exclusive population classifications."""
        return (
            self.eligible_line_count
            + self.excluded_unpaid_line_count
            + self.excluded_on_hold_line_count
            + self.excluded_cancelled_line_count
            + self.excluded_gift_line_count
            + self.unknown_gift_line_count
            + self.unknown_status_line_count
            + self.invalid_quantity_line_count
            + self.missing_observation_line_count
        )


@dataclass(frozen=True, slots=True)
class SpuPriceStats:
    spu_pk: int
    purchase: PriceMetric
    original_sale: PriceMetric
    paid: PriceMetric
    coverage: PriceCoverage


@dataclass(frozen=True, slots=True)
class PriceStatsBasis:
    """Read basis shared with the profitability result of the same snapshot."""

    calculated_at: datetime
    fx: FxBasis
    unit_costs_cny: Mapping[int, tuple[Decimal, str]]
    default_k1_cny: Decimal


@dataclass(frozen=True, slots=True)
class PriceStatsRequest:
    """One shop, one already-resolved SPU selection and one operating window.

    ``selected_spu_pks`` is the profitability selection resolved once by the
    caller (active/focused/exact), so ``scope=focused`` and the standard page
    share this relation instead of resolving a second scope.
    """

    shop_pk: int
    selected_spu_pks: tuple[int, ...]
    window_start_utc: datetime | None
    window_end_exclusive_utc: datetime | None


@dataclass(frozen=True, slots=True)
class PriceStatsOverview:
    by_spu: Mapping[int, SpuPriceStats]
    totals: SpuPriceStats
    cost_basis_fingerprint: str
    cost_basis_estimated: bool
    default_k1_cny: Decimal


# ─── pure helpers (unit-tested without a database) ───────────────────


def summarize_price_metric(
    *,
    eligible_quantity: int,
    eligible_line_count: int,
    observed_buckets: Mapping[Decimal, int] | None = None,
    observed_line_count: int = 0,
    missing_quantity: int = 0,
    missing_line_count: int = 0,
    invalid_quantity: int = 0,
    invalid_line_count: int = 0,
    source: PriceSource,
    estimated: bool = False,
) -> PriceMetric:
    """Assemble one metric from already-classified, CNY-converted buckets.

    ``observed_buckets`` maps one CNY unit price to the units observed at that
    price; the quantity-weighted mean/median come from the approved Decimal
    kernel, never from a row average.  Missing/invalid counts are this metric's
    own price-field coverage, not population exclusions.  An empty eligible
    population is ``no_samples`` with null statistics, never ``0``.
    """
    if eligible_quantity <= 0 or eligible_line_count <= 0:
        return PriceMetric(
            mean_cny=None,
            median_cny=None,
            eligible_quantity=0,
            observed_quantity=0,
            missing_quantity=0,
            invalid_quantity=0,
            observed_line_count=0,
            missing_line_count=0,
            invalid_line_count=0,
            coverage_ratio=None,
            status=PriceCoverageStatus.NO_SAMPLES,
            source=PriceSource.NONE,
            estimated=False,
        )

    summary = summarize_weighted_prices((observed_buckets or {}).items())
    observed_quantity = summary.observed_quantity
    if observed_quantity + missing_quantity + invalid_quantity != eligible_quantity:
        raise ValueError(
            "price metric counts must conserve the eligible quantity: "
            f"{observed_quantity}+{missing_quantity}+{invalid_quantity}"
            f" != {eligible_quantity}"
        )
    if (
        observed_line_count + missing_line_count + invalid_line_count
        > eligible_line_count
    ):
        raise ValueError("price metric line counts exceed the eligible line count")

    status = (
        PriceCoverageStatus.COMPLETE
        if observed_quantity == eligible_quantity
        else PriceCoverageStatus.PARTIAL
    )
    return PriceMetric(
        mean_cny=summary.mean_cny,
        median_cny=summary.median_cny,
        eligible_quantity=eligible_quantity,
        observed_quantity=observed_quantity,
        missing_quantity=missing_quantity,
        invalid_quantity=invalid_quantity,
        observed_line_count=observed_line_count,
        missing_line_count=missing_line_count,
        invalid_line_count=invalid_line_count,
        coverage_ratio=Decimal(observed_quantity) / Decimal(eligible_quantity),
        status=status,
        source=source,
        estimated=estimated,
    )


def native_to_cny_rates(fx: FxBasis) -> Mapping[str, Decimal]:
    """Return the validated native→CNY rates of one FX snapshot."""
    usd_cny = fx.usd_cny
    usd_vnd = fx.usd_vnd
    if not usd_cny.is_finite() or not usd_vnd.is_finite():
        raise PriceFxUnavailable("price FX snapshot rates must be finite")
    if usd_cny <= 0 or usd_vnd <= 0:
        raise PriceFxUnavailable("price FX snapshot rates must be positive")
    with _conversion_context():
        rates = {
            "CNY": Decimal(1),
            "USD": +usd_cny,
            "VND": usd_cny / usd_vnd,
        }
    return MappingProxyType(rates)


@contextmanager
def _conversion_context() -> Iterator[None]:
    with localcontext() as context:
        context.prec = _CONVERSION_PRECISION
        context.rounding = ROUND_HALF_EVEN
        context.Emax = _CONVERSION_EMAX
        context.Emin = _CONVERSION_EMIN
        context.clamp = 0
        context.clear_flags()
        for signal in context.traps:
            context.traps[signal] = False
        for signal in (InvalidOperation, DivisionByZero, Overflow, Underflow):
            context.traps[signal] = True
        yield


def _cny_unit_price(native_price: Decimal, rate: Decimal) -> Decimal:
    if not isinstance(native_price, Decimal) or not native_price.is_finite():
        raise ValueError("observed native price must be a finite Decimal")
    if native_price < 0:
        raise ValueError("observed native price must be non-negative")
    price = native_price * rate
    if not price.is_finite():
        raise ValueError("converted CNY price must be finite")
    return price


def _cost_basis_fingerprint(
    calculated_at: datetime, unit_costs_cny: Mapping[int, tuple[Decimal, str]]
) -> str:
    digest = hashlib.sha256()
    digest.update(calculated_at.astimezone(UTC).isoformat().encode("utf-8"))
    for spu_pk in sorted(unit_costs_cny):
        cost, source = unit_costs_cny[spu_pk]
        digest.update(f"|{spu_pk}:{cost}:{source}".encode())
    return "sha256:" + digest.hexdigest()


# ─── SQL ─────────────────────────────────────────────────────────────

# Canonical observation selection plus the mutually-exclusive population
# classification (docs/design/spu-price-statistics.md §2.2/§4.2).  The prefix is
# shared by both statements below so eligibility is defined exactly once, and
# every statement is a module-level static literal with bound parameters only.
_POPULATION_CTE = """
WITH ranked_price_observations AS (
    SELECT p.id,
           p.order_pk,
           p.external_line_id,
           p.effective_quantity,
           p.original_price_native,
           p.paid_price_native,
           p.currency,
           p.original_price_status,
           p.paid_price_status,
           p.gift_status,
           p.quantity_status,
           row_number() OVER (
               PARTITION BY p.shop_pk, p.order_pk, p.external_line_id
               ORDER BY p.source_order_version_at DESC NULLS LAST,
                        p.source_captured_at DESC,
                        p.semantic_observation_hash ASC
           ) AS canonical_rank
    FROM commerce.sales_order_line_price_observations AS p
    WHERE p.shop_pk = CAST(:shop_pk AS bigint)
),
canonical_price_observations AS (
    SELECT * FROM ranked_price_observations WHERE canonical_rank = 1
),
selected_lines AS (
    SELECT l.spu_pk,
           l.order_pk,
           l.external_line_id,
           o.status AS order_status
    FROM commerce.sales_order_lines AS l
    JOIN commerce.sales_orders AS o ON o.id = l.order_pk
    WHERE o.shop_pk = CAST(:shop_pk AS bigint)
      AND l.spu_pk = ANY(CAST(:selected_pks AS bigint[]))
      AND (CAST(:ws AS timestamptz) IS NULL
           OR coalesce(o.order_time, o.paid_at) >= CAST(:ws AS timestamptz))
      AND (CAST(:we AS timestamptz) IS NULL
           OR coalesce(o.order_time, o.paid_at) < CAST(:we AS timestamptz))
),
population_lines AS (
    SELECT s.spu_pk,
           p.id AS observation_id,
           p.original_price_native,
           p.paid_price_native,
           p.original_price_status,
           p.paid_price_status,
           p.currency,
           CASE
               WHEN p.id IS NULL THEN 'MISSING_OBSERVATION'
               WHEN p.quantity_status NOT IN ('OBSERVED', 'DEFAULT_ONE_PER_LINE')
                 OR p.effective_quantity IS NULL
                 OR p.effective_quantity <= 0
                 OR p.effective_quantity <> trunc(p.effective_quantity)
                 THEN 'INVALID_QUANTITY'
               WHEN s.order_status IS NULL THEN 'UNKNOWN_STATUS'
               WHEN s.order_status = 'CANCELLED' THEN 'EXCLUDED_CANCELLED'
               WHEN s.order_status = 'ON_HOLD' THEN 'EXCLUDED_ON_HOLD'
               WHEN s.order_status = 'UNPAID' THEN 'EXCLUDED_UNPAID'
               WHEN s.order_status <> ALL(CAST(:paid_statuses AS text[]))
                 THEN 'UNKNOWN_STATUS'
               WHEN p.gift_status IS NULL OR p.gift_status = 'UNKNOWN'
                 THEN 'UNKNOWN_GIFT'
               WHEN p.gift_status = 'GIFT' THEN 'EXCLUDED_GIFT'
               ELSE 'ELIGIBLE'
           END AS exclusion_reason,
           CASE
               WHEN p.quantity_status IN ('OBSERVED', 'DEFAULT_ONE_PER_LINE')
                AND p.effective_quantity IS NOT NULL
                AND p.effective_quantity > 0
                AND p.effective_quantity = trunc(p.effective_quantity)
               THEN p.effective_quantity::bigint
           END AS known_valid_quantity
    FROM selected_lines AS s
    LEFT JOIN canonical_price_observations AS p
      ON p.order_pk = s.order_pk
     AND p.external_line_id = s.external_line_id
)
"""

# ``GROUPING SETS`` emits one row per SPU plus one all-scope row (``spu_pk``
# NULL), so totals re-aggregate the same raw population relation instead of
# copying a per-SPU counter.
# pi-lens-ignore: python-sql-injection
_COVERAGE_SELECT = """
SELECT spu_pk,
       count(*)::int AS selected_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'ELIGIBLE')::int
           AS eligible_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_UNPAID')::int
           AS excluded_unpaid_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_ON_HOLD')::int
           AS excluded_on_hold_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_CANCELLED')::int
           AS excluded_cancelled_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_GIFT')::int
           AS excluded_gift_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'UNKNOWN_GIFT')::int
           AS unknown_gift_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'UNKNOWN_STATUS')::int
           AS unknown_status_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'INVALID_QUANTITY')::int
           AS invalid_quantity_line_count,
       count(*) FILTER (WHERE exclusion_reason = 'MISSING_OBSERVATION')::int
           AS missing_observation_line_count,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'ELIGIBLE'), 0)::bigint
           AS eligible_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'EXCLUDED_UNPAID'), 0)::bigint
           AS excluded_unpaid_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'EXCLUDED_ON_HOLD'), 0)::bigint
           AS excluded_on_hold_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'EXCLUDED_CANCELLED'), 0)::bigint
           AS excluded_cancelled_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'EXCLUDED_GIFT'), 0)::bigint
           AS excluded_gift_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'UNKNOWN_GIFT'), 0)::bigint
           AS unknown_gift_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'UNKNOWN_STATUS'), 0)::bigint
           AS unknown_status_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason NOT IN
                   ('ELIGIBLE', 'INVALID_QUANTITY', 'MISSING_OBSERVATION')), 0
           )::bigint AS excluded_valid_quantity,
       coalesce(sum(known_valid_quantity)
           FILTER (WHERE exclusion_reason = 'ELIGIBLE'
                   AND (original_price_status = 'OBSERVED'
                        OR paid_price_status = 'OBSERVED')
                   AND (currency IS NULL OR btrim(currency) = '')), 0
           )::bigint AS missing_currency_quantity
FROM population_lines
GROUP BY GROUPING SETS ((spu_pk), ())
"""

# Identical price buckets are merged in SQL (no per-unit row explosion); the
# weighted mean/median then run in the approved Decimal kernel.
# pi-lens-ignore: python-sql-injection
_BUCKET_SELECT = """
SELECT spu_pk,
       metric,
       klass,
       native_price,
       currency,
       sum(known_valid_quantity)::bigint AS quantity,
       count(*)::int AS line_count
FROM (
    SELECT spu_pk,
           metric,
           native_price,
           currency,
           known_valid_quantity,
           CASE
               WHEN field_status = 'OBSERVED'
                AND currency IS NOT NULL AND btrim(currency) <> ''
                 THEN 'OBSERVED'
               WHEN field_status = 'OBSERVED' THEN 'INVALID_CURRENCY'
               WHEN field_status IN ('MISSING', 'NULL') THEN 'MISSING'
               ELSE 'INVALID'
           END AS klass
    FROM (
        SELECT spu_pk,
               'original'::text AS metric,
               original_price_status AS field_status,
               original_price_native AS native_price,
               currency,
               known_valid_quantity
        FROM population_lines
        WHERE exclusion_reason = 'ELIGIBLE'
        UNION ALL
        SELECT spu_pk,
               'paid'::text AS metric,
               paid_price_status AS field_status,
               paid_price_native AS native_price,
               currency,
               known_valid_quantity
        FROM population_lines
        WHERE exclusion_reason = 'ELIGIBLE'
    ) AS metric_fields
) AS classified
GROUP BY GROUPING SETS (
    (spu_pk, metric, klass, native_price, currency),
    (metric, klass, native_price, currency)
)
"""

# pi-lens-ignore: python-sql-injection
_SQL_PRICE_COVERAGE = text(_POPULATION_CTE + _COVERAGE_SELECT)
# pi-lens-ignore: python-sql-injection
_SQL_PRICE_BUCKETS = text(_POPULATION_CTE + _BUCKET_SELECT)


# ─── aggregation ─────────────────────────────────────────────────────


@dataclass(slots=True)
class _MetricAggregate:
    buckets: dict[Decimal, int] = field(default_factory=dict)
    observed_quantity: int = 0
    observed_line_count: int = 0
    missing_quantity: int = 0
    missing_line_count: int = 0
    invalid_quantity: int = 0
    invalid_line_count: int = 0


def _coverage_from_row(row: Mapping[Any, Any]) -> PriceCoverage:
    def count(key: str) -> int:
        value = row[key]
        return int(value) if value is not None else 0

    coverage = PriceCoverage(
        selected_line_count=count("selected_line_count"),
        eligible_line_count=count("eligible_line_count"),
        eligible_quantity=count("eligible_quantity"),
        excluded_unpaid_line_count=count("excluded_unpaid_line_count"),
        excluded_unpaid_quantity=count("excluded_unpaid_quantity"),
        excluded_on_hold_line_count=count("excluded_on_hold_line_count"),
        excluded_on_hold_quantity=count("excluded_on_hold_quantity"),
        excluded_cancelled_line_count=count("excluded_cancelled_line_count"),
        excluded_cancelled_quantity=count("excluded_cancelled_quantity"),
        excluded_gift_line_count=count("excluded_gift_line_count"),
        excluded_gift_quantity=count("excluded_gift_quantity"),
        unknown_gift_line_count=count("unknown_gift_line_count"),
        unknown_gift_quantity=count("unknown_gift_quantity"),
        unknown_status_line_count=count("unknown_status_line_count"),
        unknown_status_quantity=count("unknown_status_quantity"),
        invalid_quantity_line_count=count("invalid_quantity_line_count"),
        excluded_valid_quantity=count("excluded_valid_quantity"),
        missing_currency_quantity=count("missing_currency_quantity"),
        # An observed price whose currency has no verifiable rate fails the
        # whole response with 503 PRICE_FX_UNAVAILABLE (§2.3), so a served
        # response can never carry an FX-unavailable unit count.
        fx_unavailable_quantity=0,
        missing_observation_line_count=count("missing_observation_line_count"),
    )
    if coverage.classification_partition_total() != coverage.selected_line_count:
        raise ValueError(
            "price population classifications must partition the selected lines: "
            f"{coverage.classification_partition_total()}"
            f" != {coverage.selected_line_count}"
        )
    return coverage


def _field_metric(
    coverage: PriceCoverage, aggregate: _MetricAggregate | None, source: PriceSource
) -> PriceMetric:
    builder = aggregate or _MetricAggregate()
    return summarize_price_metric(
        eligible_quantity=coverage.eligible_quantity,
        eligible_line_count=coverage.eligible_line_count,
        observed_buckets=builder.buckets,
        observed_line_count=builder.observed_line_count,
        missing_quantity=builder.missing_quantity,
        missing_line_count=builder.missing_line_count,
        invalid_quantity=builder.invalid_quantity,
        invalid_line_count=builder.invalid_line_count,
        source=source,
    )


def _purchase_metric(
    coverage: PriceCoverage, unit_cost_cny: Decimal, cost_source: str
) -> PriceMetric:
    """``purchase`` is the current ROI effective unit cost for the whole SPU."""
    buckets = (
        {unit_cost_cny: coverage.eligible_quantity}
        if coverage.eligible_quantity > 0
        else {}
    )
    return summarize_price_metric(
        eligible_quantity=coverage.eligible_quantity,
        eligible_line_count=coverage.eligible_line_count,
        observed_buckets=buckets,
        observed_line_count=coverage.eligible_line_count,
        source=PriceSource.ROI_UNIT_COST,
        estimated=cost_source == DEFAULT_COST_SOURCE,
    )


def read_price_stats(
    session: Session,
    *,
    basis: PriceStatsBasis,
    request: PriceStatsRequest,
) -> PriceStatsOverview:
    """Read one price-statistics overview inside the caller's read snapshot."""
    rates = native_to_cny_rates(basis.fx)
    params = {
        "shop_pk": request.shop_pk,
        "selected_pks": list(request.selected_spu_pks),
        "ws": request.window_start_utc,
        "we": request.window_end_exclusive_utc,
        "paid_statuses": list(PAID_SALES_ORDER_STATUSES),
    }
    coverage_rows = session.execute(_SQL_PRICE_COVERAGE, params).mappings().all()
    bucket_rows = session.execute(_SQL_PRICE_BUCKETS, params).mappings().all()

    coverage_by_spu: dict[int, PriceCoverage] = {}
    totals_coverage = PriceCoverage()
    for row in coverage_rows:
        coverage = _coverage_from_row(row)
        row_spu_pk = row["spu_pk"]
        if row_spu_pk is None:
            totals_coverage = coverage
        else:
            coverage_by_spu[int(row_spu_pk)] = coverage

    aggregates: dict[tuple[int | None, str], _MetricAggregate] = {}
    with _conversion_context():
        for row in bucket_rows:
            row_spu_pk = row["spu_pk"]
            key = (
                None if row_spu_pk is None else int(row_spu_pk),
                str(row["metric"]),
            )
            aggregate = aggregates.setdefault(key, _MetricAggregate())
            quantity = int(row["quantity"] or 0)
            line_count = int(row["line_count"] or 0)
            klass = str(row["klass"])
            if klass == "OBSERVED":
                currency = str(row["currency"] or "").strip().upper()
                rate = rates.get(currency)
                if rate is None:
                    raise PriceFxUnavailable(
                        "price currency cannot be converted in the read snapshot "
                        f"(supported: {', '.join(SUPPORTED_PRICE_CURRENCIES)})"
                    )
                price = _cny_unit_price(row["native_price"], rate)
                aggregate.buckets[price] = aggregate.buckets.get(price, 0) + quantity
                aggregate.observed_quantity += quantity
                aggregate.observed_line_count += line_count
            elif klass == "MISSING":
                aggregate.missing_quantity += quantity
                aggregate.missing_line_count += line_count
            else:
                aggregate.invalid_quantity += quantity
                aggregate.invalid_line_count += line_count

    by_spu: dict[int, SpuPriceStats] = {}
    for spu_pk in request.selected_spu_pks:
        coverage = coverage_by_spu.get(spu_pk, PriceCoverage())
        unit_cost_cny, cost_source = basis.unit_costs_cny.get(
            spu_pk, (basis.default_k1_cny, DEFAULT_COST_SOURCE)
        )
        by_spu[spu_pk] = SpuPriceStats(
            spu_pk=spu_pk,
            purchase=_purchase_metric(coverage, unit_cost_cny, cost_source),
            original_sale=_field_metric(
                coverage,
                aggregates.get((spu_pk, _ORIGINAL)),
                PriceSource.TIKTOK_LINE_ITEM_ORIGINAL_PRICE,
            ),
            paid=_field_metric(
                coverage,
                aggregates.get((spu_pk, _PAID)),
                PriceSource.TIKTOK_LINE_ITEM_SALE_PRICE,
            ),
            coverage=coverage,
        )

    totals_purchase_buckets: dict[Decimal, int] = {}
    totals_estimated = False
    for spu_pk, coverage in coverage_by_spu.items():
        if coverage.eligible_quantity <= 0:
            continue
        unit_cost_cny, cost_source = basis.unit_costs_cny.get(
            spu_pk, (basis.default_k1_cny, DEFAULT_COST_SOURCE)
        )
        totals_purchase_buckets[unit_cost_cny] = (
            totals_purchase_buckets.get(unit_cost_cny, 0) + coverage.eligible_quantity
        )
        totals_estimated = totals_estimated or cost_source == DEFAULT_COST_SOURCE
    totals = SpuPriceStats(
        spu_pk=0,
        purchase=summarize_price_metric(
            eligible_quantity=totals_coverage.eligible_quantity,
            eligible_line_count=totals_coverage.eligible_line_count,
            observed_buckets=totals_purchase_buckets,
            observed_line_count=totals_coverage.eligible_line_count,
            source=PriceSource.ROI_UNIT_COST,
            estimated=totals_estimated,
        ),
        original_sale=_field_metric(
            totals_coverage,
            aggregates.get((None, _ORIGINAL)),
            PriceSource.TIKTOK_LINE_ITEM_ORIGINAL_PRICE,
        ),
        paid=_field_metric(
            totals_coverage,
            aggregates.get((None, _PAID)),
            PriceSource.TIKTOK_LINE_ITEM_SALE_PRICE,
        ),
        coverage=totals_coverage,
    )

    return PriceStatsOverview(
        by_spu=MappingProxyType(by_spu),
        totals=totals,
        cost_basis_fingerprint=_cost_basis_fingerprint(
            basis.calculated_at, basis.unit_costs_cny
        ),
        cost_basis_estimated=any(
            source == DEFAULT_COST_SOURCE
            for _, source in basis.unit_costs_cny.values()
        ),
        default_k1_cny=basis.default_k1_cny,
    )


# ─── independent price sort identifiers (§4.4) ───────────────────────

_PRICE_SORT_FIELDS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "purchasePriceMean": ("purchase", "mean_cny"),
        "purchasePriceMedian": ("purchase", "median_cny"),
        "originalSalePriceMean": ("original_sale", "mean_cny"),
        "originalSalePriceMedian": ("original_sale", "median_cny"),
        "paidPriceMean": ("paid", "mean_cny"),
        "paidPriceMedian": ("paid", "median_cny"),
    }
)


def is_price_sort_field(sort_field: str) -> bool:
    return sort_field in _PRICE_SORT_FIELDS


def price_sort_value(
    overview: PriceStatsOverview | None, sort_field: str, spu_pk: int
) -> Decimal | None:
    """Return the sort key for one price column (``None`` sorts last)."""
    if overview is None:
        return None
    stats = overview.by_spu.get(spu_pk)
    if stats is None:
        return None
    metric_name, attribute = _PRICE_SORT_FIELDS[sort_field]
    return getattr(getattr(stats, metric_name), attribute)


__all__ = [
    "SUPPORTED_PRICE_CURRENCIES",
    "PriceCoverage",
    "PriceCoverageStatus",
    "PriceMetric",
    "PriceSource",
    "PriceStatsBasis",
    "PriceStatsOverview",
    "PriceStatsRequest",
    "SpuPriceStats",
    "is_price_sort_field",
    "native_to_cny_rates",
    "price_sort_value",
    "read_price_stats",
    "summarize_price_metric",
]
