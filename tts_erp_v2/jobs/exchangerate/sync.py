"""fx.sync — horizon-gated sync of ExchangeRate-API Standard snapshots.

The only place in the system that dials v6.exchangerate-api.com. The
Free plan allows 1500 requests / month and bills beyond that, so the job
is cache-first by construction:

1. Look up the latest stored snapshot for the base code.
2. If its ``next_update_at`` is still in the future (the upstream's own
   published refresh horizon), **skip without any network call** — the
   upstream refreshes its rates ~daily, so a healthy installation makes
   ~1 request/day ≈ 30 requests/month.
3. Otherwise fetch the Standard endpoint, persist a new snapshot + one
   row per currency, and record the raw payload in
   ``integration.raw_records`` (same audit convention as every other
   upstream job).

Idempotency: a re-fetch that observes the *same* upstream timestamp
(``time_last_update_utc``) updates the existing snapshot row (fresh
horizon / fetched_at) instead of inserting duplicates — the
(base_code, upstream_last_update) unique key in ``fx.exchange_rate_snapshots``
is the identity.

Failure back-off: ``quota-exceeded`` / ``inactive-account`` /
``invalid-key`` upstream errors set an in-process 24 h cooldown (per
base code) so the job stops hammering an account that cannot serve data
and stops burning counted requests. Other errors fail the tick loudly
(the scheduler writes a sentinel failed row + logs).

Environment (sync-worker only; never used by the API process):
* ``EXCHANGERATE_API_KEY`` — required. Overage is billed.
* ``EXCHANGERATE_BASE_CODE`` — default ``USD``.
* ``EXCHANGERATE_FORCE=1`` — bypass the horizon check (one-shot backfill,
  e.g. ``EXCHANGERATE_FORCE=1 python -m tts_erp_v2.sync_worker.main run fx.sync``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import ExchangeRate, ExchangeRateSnapshot
from tts_erp_v2.fx.rates import quantize_rate
from tts_erp_v2.jobs.runner import record_raw_payload, record_sync_issue, run_job
from tts_erp_v2.proxy.exchangerate import (
    ExchangeRateAPIError,
    StandardRates,
    fetch_standard_rates,
)

log = logging.getLogger("tts_erp_v2.jobs.exchangerate.sync")

JOB_NAME = "fx.sync"

ENV_API_KEY = "EXCHANGERATE_API_KEY"
ENV_BASE_CODE = "EXCHANGERATE_BASE_CODE"
ENV_FORCE = "EXCHANGERATE_FORCE"

#: Fallback horizon when the upstream response carries no
#: ``time_next_update_utc`` (defensive; the Standard endpoint always
#: sends it in practice).
DEFAULT_HORIZON = timedelta(hours=12)

#: Cooldown window for upstream errors that will not self-heal within a
#: tick (account/quota problems) — avoids 24 failed ticks per day.
COOLDOWN = timedelta(hours=24)

#: error-type values that put the base code into cooldown instead of
#: failing the tick (see ExchangeRate-API docs "error-type" table).
_COOLDOWN_ERROR_TYPES = frozenset({"quota-exceeded", "inactive-account", "invalid-key"})

#: Per-base in-process cooldown expiry (datetime). Single sync-worker
#: process ⇒ module state is safe; a restart simply clears it.
_cooldown_until: dict[str, datetime] = {}

Fetcher = Callable[[str, str], StandardRates]


def _default_fetcher() -> Fetcher:
    return fetch_standard_rates


def latest_snapshot(session: Session, base_code: str) -> ExchangeRateSnapshot | None:
    """Newest stored snapshot for ``base_code`` (by id), or None."""
    return session.execute(
        select(ExchangeRateSnapshot)
        .where(ExchangeRateSnapshot.base_code == base_code)
        .order_by(ExchangeRateSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def sync_fx_rates(
    session: Session,
    *,
    api_key: str,
    base_code: str = "USD",
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """One horizon-gated sync pass for ``base_code``. Returns counters.

    Does NOT commit (the scheduler's system-job executor commits on the
    success path — same contract as the reporting.* jobs). Raises
    :class:`ExchangeRateAPIError` / ``RuntimeError`` on upstream trouble;
    ``run_scheduled`` converts quota-style errors into a cooldown skip.
    """
    code = (base_code or "").strip().upper() or "USD"
    moment = now or datetime.now(UTC)
    if fetcher is None:
        fetcher = _default_fetcher()

    cooldown_until = _cooldown_until.get(code)
    if cooldown_until is not None and moment < cooldown_until:
        return {
            "status": "skipped",
            "reason": f"upstream cooldown until {cooldown_until.isoformat()}",
            "base_code": code,
        }

    latest = latest_snapshot(session, code)
    if (
        latest is not None
        and latest.next_update_at is not None
        and not force
        and moment < latest.next_update_at
    ):
        return {
            "status": "skipped",
            "reason": "within upstream refresh horizon (cache still fresh)",
            "base_code": code,
            "snapshot_id": latest.id,
        }

    rates = fetcher(api_key, code)
    rates_count = len(rates.conversion_rates)
    horizon = rates.next_update_at or (moment + DEFAULT_HORIZON)

    # Idempotency: same upstream timestamp → refresh the existing row,
    # do not duplicate rate rows.
    existing = session.execute(
        select(ExchangeRateSnapshot)
        .where(ExchangeRateSnapshot.base_code == code)
        .where(ExchangeRateSnapshot.upstream_last_update == rates.upstream_last_update)
        .order_by(ExchangeRateSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        existing.next_update_at = horizon
        existing.fetched_at = moment
        existing.rates_count = rates_count
        session.flush()
        return {
            "status": "upstream_unchanged",
            "reason": "upstream timestamp unchanged; refreshed cache horizon",
            "base_code": code,
            "snapshot_id": existing.id,
            "rates_count": rates_count,
            "rows_inserted": 0,
        }

    snapshot = ExchangeRateSnapshot(
        base_code=code,
        upstream_last_update=rates.upstream_last_update,
        next_update_at=horizon,
        fetched_at=moment,
        rates_count=rates_count,
    )
    session.add(snapshot)
    session.flush()
    rows = [
        ExchangeRate(
            snapshot_id=snapshot.id,
            base_code=code,
            target_code=target,
            rate=quantize_rate(rate_value),
        )
        for target, rate_value in rates.conversion_rates.items()
    ]
    session.add_all(rows)
    # Raw upstream payload → integration.raw_records (audit / drift).
    record_raw_payload(
        session,
        endpoint=f"exchangerate/v6/{code}/latest",
        payload=rates.payload,
        external_id=code,
    )
    return {
        "status": "fetched",
        "base_code": code,
        "snapshot_id": snapshot.id,
        "upstream_last_update": rates.upstream_last_update.isoformat(),
        "rates_count": rates_count,
        "rows_inserted": len(rows),
    }


def run_scheduled(session: Session) -> dict[str, Any]:
    """Scheduler entrypoint for ``fx.sync`` (system job, is_tiktok=False).

    Reads the sync-worker env (``EXCHANGERATE_API_KEY`` etc.), wraps the
    pass in the shared ``sync_jobs`` lifecycle and returns counters.
    """
    api_key = (os.environ.get(ENV_API_KEY) or "").strip()
    if not api_key:
        raise RuntimeError(
            f"{ENV_API_KEY} is not set — add it to .env (the "
            "tts-erp-sync.service EnvironmentFile) before enabling fx.sync"
        )
    base_code = (os.environ.get(ENV_BASE_CODE) or "USD").strip().upper() or "USD"
    force = (os.environ.get(ENV_FORCE) or "").strip() == "1"

    with run_job(session, job_name=JOB_NAME) as job:
        try:
            result = sync_fx_rates(
                session, api_key=api_key, base_code=base_code, force=force
            )
        except ExchangeRateAPIError as exc:
            # Quota/account problems → record the issue (dedup keeps one
            # open row) and back off in-process instead of failing 24
            # ticks a day. Everything else re-raises → run_job marks the
            # row failed and the scheduler logs it.
            if exc.error_type in _COOLDOWN_ERROR_TYPES:
                _cooldown_until[base_code] = datetime.now(UTC) + COOLDOWN
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="EXCHANGERATE_UPSTREAM_ERROR",
                    external_id=base_code,
                    details={
                        "error_type": exc.error_type,
                        "cooldown_hours": int(COOLDOWN.total_seconds() // 3600),
                        "message": str(exc),
                    },
                )
                job.status = "skipped"
                job.rows_total = 0
                job.extra = {
                    "status": "skipped",
                    "reason": f"upstream {exc.error_type} — cooldown "
                    f"{int(COOLDOWN.total_seconds() // 3600)} h",
                    "base_code": base_code,
                }
                return dict(job.extra)
            raise
        if result.get("status") == "skipped":
            job.status = "skipped"
            job.rows_total = 0
            job.extra = result
            return result
        rows = result.get("rows_inserted", 0)
        job.rows_total = rows
        job.rows_inserted = rows
        job.extra = result
        return result


__all__ = [
    "COOLDOWN",
    "DEFAULT_HORIZON",
    "ENV_API_KEY",
    "ENV_BASE_CODE",
    "ENV_FORCE",
    "JOB_NAME",
    "run_scheduled",
    "sync_fx_rates",
]
