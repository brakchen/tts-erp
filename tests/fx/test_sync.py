"""fx.sync job behavior: horizon skip / fetch / idempotent refresh /
env validation / quota cooldown.

Everything runs inside the rolled-back ``db_session`` — rows never
persist. The module-level ``_cooldown_until`` dict is reset per test so
the quota-cooldown test cannot leak into later ones.

Isolation: the production ``fx.sync`` job writes real USD snapshots
(+ hourly ``fx.sync`` SyncJob rows) to the shared dev DB, so every
fixture seeds and asserts against ``BASE`` — a TEST_-prefixed base code
the live job never touches — and all row counts are scoped by base.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import (
    ExchangeRate,
    ExchangeRateSnapshot,
    RawRecord,
    SyncIssue,
    SyncJob,
)
from tts_erp_v2.jobs.exchangerate import sync as fx_sync
from tts_erp_v2.jobs.exchangerate.sync import (
    ENV_API_KEY,
    ENV_BASE_CODE,
    JOB_NAME,
    run_scheduled,
    sync_fx_rates,
)
from tts_erp_v2.proxy.exchangerate import (
    ExchangeRateAPIError,
    StandardRates,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
RATES = {"USD": "1", "CNY": "7.1", "EUR": "0.9"}

#: Test-scoped base code — the live fx.sync job syncs real currency
#: bases (USD by default), so this namespace stays empty in the shared
#: dev DB no matter what production state the sync worker has reached.
BASE = "TEST_USD"


@pytest.fixture(autouse=True)
def _reset_cooldown() -> Iterator[None]:
    fx_sync._cooldown_until.clear()
    yield
    fx_sync._cooldown_until.clear()


def _rates(
    *,
    base: str = "USD",
    upstream: datetime = T0,
    next_update: datetime | None = T0 + timedelta(hours=23),
    rates: dict[str, str] | None = None,
) -> StandardRates:
    rates = rates or RATES
    return StandardRates(
        base_code=base,
        upstream_last_update=upstream,
        next_update_at=next_update,
        conversion_rates={code: Decimal(v) for code, v in rates.items()},
        payload={"result": "success", "base_code": base},
    )


def _seed_snapshot(
    session: Session,
    *,
    upstream: datetime = T0,
    next_update: datetime | None = T0 + timedelta(hours=23),
    rates: dict[str, str] | None = None,
) -> ExchangeRateSnapshot:
    rates = rates or RATES
    snap = ExchangeRateSnapshot(
        base_code=BASE,
        upstream_last_update=upstream,
        next_update_at=next_update,
        fetched_at=T0,
        rates_count=len(rates),
    )
    session.add(snap)
    session.flush()
    for code, value in rates.items():
        session.add(
            ExchangeRate(
                snapshot_id=snap.id,
                base_code=BASE,
                target_code=code,
                rate=Decimal(value),
            )
        )
    session.flush()
    return snap


def _count(session: Session, model, *, base_code: str | None = None) -> int:
    """Row count, scoped to ``base_code`` when given.

    The shared dev DB holds real fx.sync rows (USD base); unscoped
    counts would make every assertion depend on live-job state.
    """
    stmt = select(func.count()).select_from(model)
    if base_code is not None:
        stmt = stmt.where(model.base_code == base_code)
    return session.execute(stmt).scalar_one()


def test_first_fetch_inserts_snapshot_and_rate_rows(db_session) -> None:
    called: list[tuple[str, str]] = []

    def fetcher(api_key: str, base_code: str) -> StandardRates:
        called.append((api_key, base_code))
        return _rates(next_update=T0 + timedelta(hours=20))

    result = sync_fx_rates(
        db_session, api_key="k", base_code=BASE, fetcher=fetcher, now=T0
    )

    assert result["status"] == "fetched"
    assert result["rows_inserted"] == 3
    assert result["base_code"] == BASE
    assert called == [("k", BASE)]

    snaps = (
        db_session.execute(
            select(ExchangeRateSnapshot)
            .where(ExchangeRateSnapshot.base_code == BASE)
            .order_by(ExchangeRateSnapshot.id)
        )
        .scalars()
        .all()
    )
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.base_code == BASE
    assert snap.upstream_last_update == T0
    # Horizon stored from the upstream's time_next_update_utc.
    assert snap.next_update_at == T0 + timedelta(hours=20)
    assert snap.rates_count == 3

    assert _count(db_session, ExchangeRate, base_code=BASE) == 3
    # Raw upstream payload lands in integration.raw_records (audit).
    # Scoped to our base — the live fx.sync run also leaves raw records
    # for its real USD fetch.
    raw = (
        db_session.execute(
            select(RawRecord).where(
                RawRecord.endpoint.like(f"exchangerate/v6/{BASE}/%")
            )
        )
        .scalars()
        .all()
    )
    assert len(raw) == 1
    assert raw[0].external_id == BASE


def test_fetch_within_horizon_skips_network(db_session) -> None:
    _seed_snapshot(db_session, next_update=T0 + timedelta(hours=23))

    def should_not_run(api_key: str, base_code: str) -> StandardRates:
        raise AssertionError("must not dial upstream inside the cache horizon")

    result = sync_fx_rates(
        db_session,
        api_key="k",
        base_code=BASE,
        fetcher=should_not_run,
        now=T0 + timedelta(hours=1),
    )
    assert result["status"] == "skipped"
    assert "horizon" in result["reason"]
    assert _count(db_session, ExchangeRateSnapshot, base_code=BASE) == 1


def test_force_bypasses_horizon_and_inserts_new_snapshot(db_session) -> None:
    _seed_snapshot(db_session, next_update=T0 + timedelta(hours=23))
    new_upstream = T0 + timedelta(days=1)

    def fetcher(api_key: str, base_code: str) -> StandardRates:
        return _rates(upstream=new_upstream)

    result = sync_fx_rates(
        db_session,
        api_key="k",
        base_code=BASE,
        fetcher=fetcher,
        now=T0 + timedelta(hours=1),
        force=True,
    )
    assert result["status"] == "fetched"
    assert _count(db_session, ExchangeRateSnapshot, base_code=BASE) == 2
    assert _count(db_session, ExchangeRate, base_code=BASE) == 6


def test_same_upstream_timestamp_refreshes_in_place(db_session) -> None:
    _seed_snapshot(db_session, upstream=T0, next_update=T0 + timedelta(hours=1))

    def fetcher(api_key: str, base_code: str) -> StandardRates:
        return _rates(upstream=T0, next_update=T0 + timedelta(days=1))

    result = sync_fx_rates(
        db_session,
        api_key="k",
        base_code=BASE,
        fetcher=fetcher,
        now=T0 + timedelta(hours=3),
    )
    assert result["status"] == "upstream_unchanged"
    assert result["rows_inserted"] == 0
    # Same snapshot row, refreshed horizon — no duplicates.
    assert _count(db_session, ExchangeRateSnapshot, base_code=BASE) == 1
    assert _count(db_session, ExchangeRate, base_code=BASE) == 3
    snap = db_session.execute(
        select(ExchangeRateSnapshot).where(ExchangeRateSnapshot.base_code == BASE)
    ).scalar_one()
    assert snap.next_update_at == T0 + timedelta(days=1)


def test_missing_api_key_env_raises(db_session, monkeypatch) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(RuntimeError, match=ENV_API_KEY):
        run_scheduled(db_session)


def test_run_scheduled_happy_path_writes_succeeded_job(db_session, monkeypatch) -> None:
    # fx.sync only ever runs against real currency bases; point this
    # tick at the TEST_ base so live USD state cannot gate it.
    monkeypatch.setenv(ENV_API_KEY, "test-key")
    monkeypatch.setenv(ENV_BASE_CODE, BASE)

    def fetcher(api_key: str, base_code: str) -> StandardRates:
        return _rates()

    monkeypatch.setattr(
        "tts_erp_v2.jobs.exchangerate.sync.fetch_standard_rates", fetcher
    )
    result = run_scheduled(db_session)

    assert result["status"] == "fetched"
    assert result["base_code"] == BASE
    job = (
        db_session.execute(
            select(SyncJob)
            .where(SyncJob.job_name == JOB_NAME)
            .order_by(SyncJob.id.desc())
        )
        .scalars()
        .first()
    )
    assert job is not None
    # Newest-first + extra check: real hourly fx.sync rows (base USD)
    # live in the same table and must not be mistaken for this row.
    assert job.status == "succeeded"
    assert job.extra["base_code"] == BASE
    assert job.rows_inserted == 3


def test_run_scheduled_quota_error_sets_cooldown_and_skips(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "test-key")
    monkeypatch.setenv(ENV_BASE_CODE, BASE)

    def quota_error(api_key: str, base_code: str) -> StandardRates:
        raise ExchangeRateAPIError("quota exceeded", error_type="quota-exceeded")

    monkeypatch.setattr(
        "tts_erp_v2.jobs.exchangerate.sync.fetch_standard_rates", quota_error
    )

    result = run_scheduled(db_session)
    assert result["status"] == "skipped"
    assert "quota-exceeded" in result["reason"]

    job = (
        db_session.execute(
            select(SyncJob)
            .where(SyncJob.job_name == JOB_NAME)
            .order_by(SyncJob.id.desc())
        )
        .scalars()
        .first()
    )
    assert job is not None
    assert job.status == "skipped"
    assert job.extra["base_code"] == BASE

    issue = (
        db_session.execute(
            select(SyncIssue)
            .where(SyncIssue.job_name == JOB_NAME)
            .where(SyncIssue.external_id == BASE)
            .order_by(SyncIssue.id.desc())
        )
        .scalars()
        .first()
    )
    assert issue is not None
    assert issue.issue_type == "EXCHANGERATE_UPSTREAM_ERROR"
    assert issue.details["error_type"] == "quota-exceeded"

    # A second tick within the cooldown skips without dialing upstream.
    result2 = run_scheduled(db_session)
    assert result2["status"] == "skipped"
    assert "cooldown" in result2["reason"]


def test_run_scheduled_non_quota_upstream_error_fails_loudly(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_API_KEY, "test-key")
    monkeypatch.setenv(ENV_BASE_CODE, BASE)

    def bad_payload(api_key: str, base_code: str) -> StandardRates:
        raise ExchangeRateAPIError("malformed body", error_type="malformed-payload")

    monkeypatch.setattr(
        "tts_erp_v2.jobs.exchangerate.sync.fetch_standard_rates", bad_payload
    )
    with pytest.raises(ExchangeRateAPIError):
        run_scheduled(db_session)

    job = (
        db_session.execute(
            select(SyncJob)
            .where(SyncJob.job_name == JOB_NAME)
            .order_by(SyncJob.id.desc())
        )
        .scalars()
        .first()
    )
    assert job is not None
    assert job.status == "failed"
    assert "ExchangeRateAPIError" in (job.error_message or "")
