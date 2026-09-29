"""fx read service: latest snapshot + local bridge conversion math.

Pure Decimal arithmetic over seeded fx.* rows (rolled-back per test).
The production ``fx.sync`` job writes real USD snapshots to the shared
dev DB, so every read is scoped to a TEST_-prefixed base code the job
never touches — assertions never depend on the fx tables being empty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ExchangeRate, ExchangeRateSnapshot
from tts_erp_v2.fx.rates import (
    Conversion,
    UnknownCurrencyError,
    convert,
    convert_or_none,
    load_rate_map,
    quantize_rate,
)

#: Test-scoped base code: the live fx.sync job only ever syncs real
#: currency bases (USD by default), so this namespace is guaranteed
#: empty regardless of production state in the shared dev DB.
BASE = "TEST_USD"
RATES = {"USD": "1", "CNY": "7.1", "EUR": "0.9"}

# Sentinel: distinguish "not passed" (→ sensible defaults) from an
# explicit None (→ NULL horizon / NULL upstream, as some tests need).
_MISSING = object()


def _seed(
    session: Session,
    *,
    base: str = BASE,
    rates: dict[str, str] | None = None,
    upstream: datetime | object = _MISSING,
    next_update: datetime | None | object = _MISSING,
) -> int:
    rates = rates or RATES
    now = datetime.now(UTC)
    if upstream is _MISSING:
        upstream = now - timedelta(hours=1)
    if next_update is _MISSING:
        next_update = now + timedelta(hours=23)
    snap = ExchangeRateSnapshot(
        base_code=base,
        upstream_last_update=upstream,  # type: ignore[arg-type]
        next_update_at=next_update,  # type: ignore[arg-type]
        fetched_at=now,
        rates_count=len(rates),
    )
    session.add(snap)
    session.flush()
    for code, value in rates.items():
        session.add(
            ExchangeRate(
                snapshot_id=snap.id,
                base_code=base,
                target_code=code,
                rate=Decimal(value),
            )
        )
    session.flush()
    return snap.id


def test_load_rate_map_returns_none_when_empty(db_session) -> None:
    # BASE is never synced by the live job, so its absence stays
    # assertable even with real USD rows present in the dev DB.
    assert load_rate_map(db_session, base_code=BASE) is None
    # Whitespace around the code is stripped before the lookup.
    assert load_rate_map(db_session, base_code=f" {BASE} ") is None


def test_load_rate_map_latest_snapshot_wins(db_session) -> None:
    _seed(db_session, upstream=datetime(2026, 9, 1, tzinfo=UTC))
    newer = datetime(2026, 9, 5, tzinfo=UTC)
    _seed(db_session, upstream=newer, rates={"USD": "1", "CNY": "7.5"})
    rm = load_rate_map(db_session, base_code=BASE)
    assert rm is not None
    assert rm.upstream_last_update == newer
    assert rm.rates["CNY"] == Decimal("7.50000000")
    assert rm.rates["USD"] == Decimal("1.00000000")
    assert rm.rate_count == 2
    assert rm.base_code == BASE


def test_is_stale_uses_next_update_horizon(db_session) -> None:
    now = datetime.now(UTC)
    _seed(db_session, next_update=now + timedelta(hours=5))
    rm = load_rate_map(db_session, base_code=BASE)
    assert rm is not None
    assert rm.is_stale(now) is False
    assert rm.is_stale(now + timedelta(hours=6)) is True


def test_is_stale_true_when_horizon_unknown(db_session) -> None:
    _seed(db_session, next_update=None)
    rm = load_rate_map(db_session, base_code=BASE)
    assert rm is not None
    assert rm.is_stale() is True


def test_convert_from_base_to_target(db_session) -> None:
    _seed(db_session)
    out = convert(
        db_session, amount=Decimal(100), from_code="USD", to_code="CNY", base_code=BASE
    )
    assert isinstance(out, Conversion)
    assert out.rate == Decimal("7.10000000")
    assert out.converted == Decimal("710.00000000")
    assert out.from_code == "USD"
    assert out.to_code == "CNY"
    assert out.base_code == BASE
    assert out.stale is False


def test_convert_target_to_base_inverts(db_session) -> None:
    _seed(db_session)
    out = convert(
        db_session, amount=Decimal(100), from_code="CNY", to_code="USD", base_code=BASE
    )
    assert out.rate == Decimal("0.14084507")  # 1/7.1 quantized to 8 dp
    assert out.converted == Decimal("14.08450700")


def test_convert_cross_pair_through_base(db_session) -> None:
    _seed(db_session)
    out = convert(
        db_session,
        amount=Decimal("7.1"),
        from_code="CNY",
        to_code="EUR",
        base_code=BASE,
    )
    # 0.9 / 7.1 quantized to 8 dp = 0.12676056; 7.1 × rate rounds to
    # 0.89999998 (the rate is quantized BEFORE the multiply by design).
    assert out.rate == Decimal("0.12676056")
    assert out.converted == Decimal("0.89999998")


def test_convert_same_currency_returns_amount(db_session) -> None:
    _seed(db_session)
    out = convert(
        db_session,
        amount=Decimal("123.45"),
        from_code="CNY",
        to_code="cny",
        base_code=BASE,
    )
    assert out.rate == Decimal("1.00000000")
    assert out.converted == Decimal("123.45000000")


def test_convert_unknown_currency_raises(db_session) -> None:
    _seed(db_session)
    with pytest.raises(UnknownCurrencyError) as excinfo:
        convert(
            db_session,
            amount=Decimal(1),
            from_code="XXX",
            to_code="USD",
            base_code=BASE,
        )
    assert "XXX" in str(excinfo.value)
    assert excinfo.value.codes == ["XXX"]
    # Unknown on the to-side, both sides unknown.
    with pytest.raises(UnknownCurrencyError) as excinfo2:
        convert(
            db_session,
            amount=Decimal(1),
            from_code="USD",
            to_code="ZZZ",
            base_code=BASE,
        )
    assert excinfo2.value.codes == ["ZZZ"]


def test_convert_or_none_returns_none_without_snapshot(db_session) -> None:
    # BASE never has a snapshot (live job only syncs real codes), so
    # None is deterministic even with real USD rows in the dev DB.
    out = convert_or_none(
        db_session,
        amount=Decimal(1),
        from_code="USD",
        to_code="CNY",
        base_code=BASE,
    )
    assert out is None


def test_quantize_rate_precision() -> None:
    assert quantize_rate(Decimal("0.140845070422535")) == Decimal("0.14084507")
    assert quantize_rate(Decimal("7.888888888888")) == Decimal("7.88888889")
