"""APScheduler wiring for the tts-erp v2 sync worker.

This module owns three concerns:

* The :data:`JOBS` registry — a frozen map of ``job_name`` → ``JobSpec``
  (module path + interval). Every job is registered through this single
  table so :mod:`tts_erp_v2.sync_worker.main` can ``list`` them and
  ``run <job>`` them by name.
* The :func:`_enumerate_tiktok_shops` helper that backs the per-tick
  fan-out: every TikTok job runs once per authorised shop.
* The :func:`build_scheduler` factory that returns a configured
  ``BlockingScheduler`` ready to ``.start()``.

Why we don't reuse the legacy ``sync_cron.py`` table
---------------------------------------------------
``sync_cron.py`` is a 600-line CLI cron-tick wrapper that drives the v1
``POST /sync/*`` HTTP routes. The v2 architecture has no such HTTP
surface (AGENTS.md §3, all ``/sync/*`` are 404 in v2), and the v2 jobs
take a SQLAlchemy session + ``proxy_call`` closure, not an HTTP body.
There is no clean way to share cron-tick logic across the two stacks
without one calling the other — which the design explicitly forbids.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.sync_worker.job_runner import (
    run_with_sync_job,
)
from tts_erp_v2.sync_worker.proxy_call import build_proxy_call

log = logging.getLogger("tts_erp_v2.sync_worker.scheduler")


# ─── Registry ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class JobSpec:
    """One entry in the :data:`JOBS` table.

    Attributes:
        job_name: matches the registry key; also the value written to
            ``integration.sync_jobs.job_name``.
        module_path: dotted path to the module exposing ``run(session,
            *, proxy_call, shop_id, page_size, scope) -> JobResult``
            (for tiktok jobs) or ``sync_token_refresh(session, ...)``
            (for ``token.refresh``).
        interval_seconds: how often APScheduler should fire this job.
            Aligned with the legacy ``sync_cron.py`` cadence so the v2
            worker is a drop-in replacement.
        is_tiktok: True if the module's ``run`` accepts ``proxy_call``
            + ``shop_id`` (needs per-shop fan-out). False for
            ``token.refresh`` which is system-wide.
    """

    job_name: str
    module_path: str
    interval_seconds: int
    is_tiktok: bool = True


# Intervals match the legacy ``sync_cron.py`` cadence (see
# ``SYNC_PLANS`` in that file); the v2 worker is a 1:1 replacement.
JOBS: dict[str, JobSpec] = {
    "tiktok.orders": JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,  # 10 min — orders change all day
    ),
    "tiktok.order_detail": JobSpec(
        job_name="tiktok.order_detail",
        module_path="tts_erp_v2.jobs.tiktok.order_detail",
        interval_seconds=1800,  # 30 min — gap-filler
    ),
    "tiktok.products": JobSpec(
        job_name="tiktok.products",
        module_path="tts_erp_v2.jobs.tiktok.products",
        interval_seconds=21600,  # 6 h — catalog rarely changes
    ),
    "tiktok.logistics": JobSpec(
        job_name="tiktok.logistics",
        module_path="tts_erp_v2.jobs.tiktok.logistics",
        interval_seconds=600,  # 10 min — the historical pain point
    ),
    "tiktok.after_sales": JobSpec(
        job_name="tiktok.after_sales",
        module_path="tts_erp_v2.jobs.tiktok.after_sales",
        interval_seconds=900,  # 15 min
    ),
    "tiktok.finance": JobSpec(
        job_name="tiktok.finance",
        module_path="tts_erp_v2.jobs.tiktok.finance",
        interval_seconds=3600,  # 1 h — settlement cycles
    ),
    "token.refresh": JobSpec(
        job_name="token.refresh",
        module_path="tts_erp_v2.jobs.token_refresh",
        interval_seconds=21600,  # 6 h — covers 24 h expiry window
        is_tiktok=False,
    ),
}


# ─── Shop enumerator ───────────────────────────────────────────────


def _enumerate_tiktok_shops(session: Session) -> list[str]:
    """Return the ``external_account_id`` of every ``provider='tiktok'`` row.

    Filters out ``MOCK_*`` sentinels (the 2026-08-25 leak that
    triggered 1008 wasted upstream calls / day until fixed — see
    ``sync_cron.discover_shops`` for the original guard).

    Returns ``[]`` on DB error so a single transient PG blip does NOT
    abort the worker. APScheduler will simply log an empty tick and
    try again on the next interval.
    """
    try:
        rows = session.execute(
            select(Credentials.external_account_id)
            .where(Credentials.provider == "tiktok")
            .order_by(Credentials.external_account_id)
        ).all()
    except Exception:  # noqa: BLE001 — boundary between SQLAlchemy and our worker
        log.exception("_enumerate_tiktok_shops failed; returning empty list")
        return []
    return [row[0] for row in rows if row[0] and not row[0].startswith("MOCK_")]


# ─── Per-tick executor ─────────────────────────────────────────────


def _run_tiktok_job(
    spec: JobSpec,
    session_factory: sessionmaker[Session],
) -> None:
    """Run ``spec`` once per authorised TikTok shop.

    Each (shop, run) pair writes its own ``integration.sync_jobs`` row
    via :func:`run_with_sync_job`, so a failure on shop A doesn't pollute
    shop B's bookkeeping.
    """
    mod = importlib.import_module(spec.module_path)
    shop_ids = _enumerate_tiktok_shops_in_factory(session_factory)
    if not shop_ids:
        log.info("[%s] no authorised TikTok shops — skipping tick", spec.job_name)
        return

    for shop_id in shop_ids:
        for attempt in range(2):
            session = session_factory()
            try:
                proxy_call = build_proxy_call(session, shop_id=shop_id)
                _row, result = run_with_sync_job(
                    session,
                    job_name=spec.job_name,
                    inner=mod.run,
                    inner_kwargs={
                        "proxy_call": proxy_call,
                        "shop_id": shop_id,
                    },
                )
                log.info(
                    "[%s] shop=%s ok total=%d inserted=%d failed=%d",
                    spec.job_name,
                    shop_id,
                    result.rows_total,
                    result.rows_inserted,
                    result.rows_failed,
                )
                break  # success → exit retry loop
            except Exception:  # noqa: BLE001 — boundary
                if attempt == 0:
                    log.warning(
                        "[%s] shop=%s first attempt failed; retrying in 5s",
                        spec.job_name,
                        shop_id,
                        exc_info=True,
                    )
                    time.sleep(5)
                else:
                    log.exception(
                        "[%s] shop=%s giving up after 2 attempts",
                        spec.job_name,
                        shop_id,
                    )
            finally:
                session.close()


def _run_token_refresh_job(
    spec: JobSpec,
    session_factory: sessionmaker[Session],
) -> None:
    """Run ``token.refresh`` once. No per-shop fan-out.

    Wires the production TikTok refresher registry into the job so the
    no-op stub in :mod:`tts_erp_v2.jobs.token_refresh` is replaced by
    a real HTTP call to ``/api/v2/token/refresh``. Miaoshou still
    receives a no-op refresher (per AGENTS.md §10.2 — no v2 Miaoshou
    refresh path).

    Commit contract: the sync_jobs row written by
    :func:`sync_token_refresh`'s ``run_job`` context manager MUST be
    durable, so we explicitly ``commit()`` on the success path AND on
    any exception path before the session is closed. Without the
    explicit commit, the outer transaction is rolled back by the
    session-close → integration.sync_jobs shows zero rows for
    ``token.refresh`` (the production observation that drove this fix).
    """
    mod = importlib.import_module(spec.module_path)
    # Lazy import: avoid hard dependency when scheduler.py is imported
    # just for the JOBS registry (e.g. ``list`` mode).
    from tts_erp_v2.proxy.tiktok_auth import build_token_registry

    registry = build_token_registry(session_factory=session_factory)

    session = session_factory()
    try:
        result = mod.sync_token_refresh(session, registry=registry)
        # ``sync_token_refresh`` writes the sync_jobs row inside its
        # ``run_job`` context manager but does NOT commit (the
        # context manager is decorator-style; it expects the caller
        # to own the transaction). Commit here so the bookkeeping
        # row is durable even if APScheduler restarts immediately.
        try:
            session.commit()
        except Exception:  # noqa: BLE001 — boundary
            log.exception("[%s] commit() failed after successful run", spec.job_name)
            session.rollback()
        log.info(
            "[%s] ok scanned=%d refreshed=%d skipped=%d failed=%d issues=%d",
            spec.job_name,
            result.get("scanned", 0),
            result.get("refreshed", 0),
            result.get("skipped", 0),
            result.get("failed", 0),
            result.get("issues", 0),
        )
    except Exception:  # noqa: BLE001 — boundary
        # The exception fired BEFORE the sync_jobs row was committed
        # (or DURING the commit). Roll back the inner transaction
        # first, then write a sentinel 'failed' sync_jobs row in a
        # NEW transaction so operators still see the tick happened.
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            log.exception("[%s] rollback during error path failed", spec.job_name)
        _record_failed_tick(session_factory, spec, "tick raised")
        log.exception("[%s] tick failed", spec.job_name)
    finally:
        session.close()


def _enumerate_tiktok_shops_in_factory(
    session_factory: sessionmaker[Session],
) -> list[str]:
    """Open a session, enumerate, close. Used by ``_run_tiktok_job``."""
    session = session_factory()
    try:
        return _enumerate_tiktok_shops(session)
    finally:
        session.close()


def _record_failed_tick(
    session_factory: sessionmaker[Session],
    spec: JobSpec,
    reason: str,
) -> None:
    """Write a sentinel 'failed' sync_jobs row in a fresh transaction.

    Used by :func:`_run_token_refresh_job`'s exception path when the
    inner :func:`sync_token_refresh` raised before its own
    ``run_job`` context manager could mark the row. Operators MUST be
    able to see the tick happened even when the inner code crashed —
    silent loss of bookkeeping is exactly the bug this whole
    Lane-1 fix is meant to eliminate.
    """
    from tts_erp_v2.db.models.integration import SyncJob

    session = session_factory()
    try:
        row = SyncJob(
            job_name=spec.job_name,
            status="failed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            error_message=reason[:2000],
        )
        session.add(row)
        session.commit()
    except Exception:  # noqa: BLE001
        log.exception(
            "[%s] could not write sentinel failed sync_jobs row",
            spec.job_name,
        )
        with suppress(Exception):
            session.rollback()
    finally:
        session.close()


def _make_executor(
    spec: JobSpec,
    session_factory: sessionmaker[Session],
) -> Callable[[], None]:
    """Wrap a job's run logic into a no-arg callable APScheduler can fire."""
    if spec.is_tiktok:
        return lambda: _run_tiktok_job(spec, session_factory)
    return lambda: _run_token_refresh_job(spec, session_factory)


# ─── Scheduler factory ────────────────────────────────────────────


def build_scheduler(
    session_factory: sessionmaker[Session] | None = None,
    *,
    jitter_seconds: int = 30,
) -> BlockingScheduler:
    """Return a configured ``BlockingScheduler`` with every :data:`JOBS` entry.

    Args:
        session_factory: SQLAlchemy ``sessionmaker``. Defaults to
            :func:`tts_erp_v2.db.base.get_session_factory` so callers
            (the daemon + ``list`` introspection) don't have to plumb
            it through.
        jitter_seconds: random delay added to each job's first-fire time
            so the 7 jobs don't all thunder at t=0. Subsequent fires are
            at fixed intervals. Defaults to 30s — matches the
            legacy cron's ``random.uniform(2, 8)`` per-call jitter.

    The returned scheduler is NOT started; the caller decides between
    ``.start()`` (daemon mode) and ``.get_jobs()`` (introspection).
    """
    if session_factory is None:
        # Lazy import: avoid pulling SQLAlchemy + the engine at module
        # import time (lets ``scheduler.JOBS`` be readable from tools
        # that don't have ``TTS_ERP_DB_URL`` configured, e.g. ``list``).
        from tts_erp_v2.db.base import get_session_factory

        session_factory = get_session_factory()

    sched = BlockingScheduler(timezone="UTC")
    for name, spec in JOBS.items():
        sched.add_job(
            _make_executor(spec, session_factory),
            trigger=IntervalTrigger(
                seconds=spec.interval_seconds, jitter=jitter_seconds
            ),
            id=name,
            name=name,
            max_instances=1,  # no overlapping runs of the same job
            coalesce=True,  # if we miss fires, run only once on resume
            replace_existing=True,
        )
    return sched


__all__ = ["JOBS", "JobSpec", "build_scheduler", "_enumerate_tiktok_shops"]
