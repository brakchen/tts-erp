"""Sync-job watchdog — one-shot health check for `integration.sync_jobs`.

Why this script exists
----------------------
The 2026-09-01 outage (TikTok credentials overwritten back to legacy
format by a migration script, full pipeline halted for 22 hours) was
only discoverable by reading ``integration.sync_jobs`` rows directly.
No operator-facing alert existed; the only "signal" was a database row
sitting in ``status='failed'`` waiting to be noticed.

This watchdog closes that gap. It is intentionally:

* **Stateless / one-shot.** Designed to run from cron / systemd timer
  every few minutes. No daemon, no shared state, no log file rotation.
* **Read-only.** Touches ``integration.sync_jobs`` only via SELECT.
* **Registry-driven.** Reads the per-job ``interval_seconds`` from
  :data:`tts_erp_v2.sync_worker.scheduler.JOBS` instead of hardcoding
  thresholds; if the schedule changes (e.g. logistics moves from
  10 min to 5 min) the watchdog automatically tracks it.
* **Outbound-channel agnostic.** Always writes structured JSON to
  stderr (so `>> logs/watchdog.log` captures it); additionally POSTs
  to ``TTS_ERP_ALERT_WEBHOOK_URL`` if set. Webhook failures are
  logged but never fatal — a flaky pager must not mask a real alert
  (or vice-versa).

Detection rules (one alert per affected job)
--------------------------------------------
For every job in the JOBS registry, look at the most-recent N runs
(default N=10, enough to cover 3× the longest interval):

(a) **Stale-success.** No ``status='succeeded'`` row in the last
    ``3 × interval_seconds``. A job that has been running for hours
    without a single success is stuck.

(b) **Consecutive failure streak.** The last ≥3 runs are all
    ``status='failed'``. (≥3, not 1: APScheduler's per-job retry
    can transiently fail twice on a single tick, which we don't
    want to alert on.)

(c) **Credential-class error.** Most-recent ``status='failed'`` row's
    ``error_message`` contains ``DecryptionError`` or
    ``AuthenticationError``. These are a strict subset of (b) but
    flagged with ``severity="credential"`` so the on-call sees them
    first — a credential-format bug (the Sept 1 outage) won't show
    up as "stale" for several hours but DOES produce these strings
    immediately.

Exit codes
----------
``0`` — no findings.
``1`` — at least one finding emitted.

Exit code ``1`` is the contract cron/systemd expects ("non-zero →
alert"). Use ``grep -c '"finding"' logs/watchdog.log`` to count, or
just watch for the exit code on the cron line.

Deployment
----------
Add to the operator's crontab::

    # Watchdog: every 10 minutes, log to logs/watchdog.log
    */10 * * * * cd /home/schan/tts-erp && .venv/bin/python scripts/watchdog_sync.py >> logs/watchdog.log 2>&1

Or as a systemd timer (see tech-doc/watchdog-deploy.md when written).
The webhook URL is configured via env::

    TTS_ERP_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Same env-var loading pattern as scripts/oneoff_finance_reset.py —
# the watchdog must work when invoked directly, without going through
# tests/conftest.py or any other entry point that loads .env.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Make the project root importable so ``from tts_erp_v2.…`` resolves
# even when the watchdog is invoked from a different CWD (e.g. cron
# spawning it with an absolute path). The cron recipe in this
# module's docstring uses ``cd /home/schan/tts-erp &&`` already, but
# we belt-and-brace it here so a one-off operator invocation from
# /tmp/ also works.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    """Best-effort load of .env so TTS_ERP_DB_URL etc. are populated.

    Mirrors the parsing in tests/conftest.py so behaviour is
    consistent whether the watchdog runs from a cron tick (no shell
    env) or a developer invocation (env already exported). Ignores
    comments and blank lines; trims surrounding quotes on values.
    """
    if not ENV_PATH.exists():
        return
    try:
        text = ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

log = logging.getLogger("scripts.watchdog_sync")

# Credentials-class error substrings — matched as plain substrings on
# ``integration.sync_jobs.error_message``. The exact class name strings
# are produced by :func:`run_with_sync_job` which formats the
# exception as ``f"{type(exc).__name__}: {exc}"`` (see
# ``tts_erp_v2/sync_worker/job_runner.py``), so substring matching is
# stable. Listed lowercase; we lower-case the haystack before match.
CREDENTIAL_ERROR_SUBSTRINGS: tuple[str, ...] = (
    "decryptionerror",
    "authenticationerror",
)

# How many of the most recent sync_jobs rows to load per job. The
# longest JOBS interval is 6h = 21600s; with 10 rows we cover 10
# ticks × 21600 = 60h for the slowest job and 10 ticks × 600 = 100
# minutes for the fastest, both well past the 3× threshold for any
# interval in the registry.
RECENT_RUN_WINDOW = 10

# Consecutive-failure threshold (rule (b)).
CONSECUTIVE_FAILURE_THRESHOLD = 3

# Stale-success multiplier (rule (a)). A job that hasn't succeeded
# in (STALE_MULTIPLIER × interval_seconds) is reported as stale.
STALE_MULTIPLIER = 3

# HTTP timeout for the optional webhook POST. Long enough for slow
# pager backends; short enough that a hung webhook cannot wedge the
# cron tick.
WEBHOOK_TIMEOUT_SECONDS = 5.0


# ─── Finding data class ────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One watchdog alert. Serialises to JSON via :func:`emit`.

    Fields:
        job_name: the ``integration.sync_jobs.job_name`` value that
            triggered the finding.
        rule: which detection rule fired (``"stale_success"`` /
            ``"consecutive_failures"`` / ``"credential_error"``).
        severity: ``"credential"`` for rule (c), ``"warning"`` for
            rules (a) and (b). Webhook consumers should page
            immediately on ``severity="credential"``.
        detected_at: UTC ISO-8601 timestamp of when the watchdog
            observed the finding.
        details: rule-specific evidence (last_success_at,
            failed_streak length, matched error substring).
    """

    job_name: str
    rule: str
    severity: str
    detected_at: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


# ─── DB-side helpers ───────────────────────────────────────────────────


def _recent_runs(session, job_name: str, limit: int = RECENT_RUN_WINDOW) -> list[Any]:
    """Return the most-recent ``limit`` sync_jobs rows for ``job_name``.

    Sorted ``started_at DESC`` so index ``(job_name, started_at)``
    (defined on the SyncJob model) is used in reverse. Returns an
    empty list when the job has never run (the watchdog will treat
    this as a "stale_success" finding after the interval elapses).
    """
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

    stmt = (
        select(SyncJob)
        .where(SyncJob.job_name == job_name)
        .order_by(SyncJob.started_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def _last_success_at(session, job_name: str) -> dt.datetime | None:
    """Return the ``started_at`` of the most recent ``succeeded`` row, or None.

    ``started_at`` (not ``finished_at``) because that's what we
    compare against the scheduler interval — a 10-min interval means
    a new attempt starts every 10 min, and we want to know when the
    last successful ATTEMPT began.
    """
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

    stmt = (
        select(SyncJob.started_at)
        .where(SyncJob.job_name == job_name, SyncJob.status == "succeeded")
        .order_by(SyncJob.started_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _trailing_failure_streak(runs: Iterable[Any]) -> int:
    """Length of the leading run of ``status='failed'`` rows.

    ``runs`` must be ordered newest-first (the order returned by
    :func:`_recent_runs`). Returns 0 when the most-recent row is
    not a failure, so a job that has been succeeding for 24 hours
    after a single transient failure does not alert.
    """
    n = 0
    for run in runs:
        if run.status == "failed":
            n += 1
        else:
            break
    return n


# ─── Detection rules ───────────────────────────────────────────────────


def _check_stale_success(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    session,
) -> Finding | None:
    """Rule (a): no succeeded row in ``3 × interval_seconds``."""
    threshold = now - dt.timedelta(seconds=interval_seconds * STALE_MULTIPLIER)
    last_success = _last_success_at(session, job_name)
    if last_success is None or last_success < threshold:
        # None == never succeeded → treat as "infinitely stale" (use now).
        since = (now - last_success).total_seconds() if last_success else None
        return Finding(
            job_name=job_name,
            rule="stale_success",
            severity="warning",
            detected_at=now.isoformat(),
            details={
                "interval_seconds": interval_seconds,
                "threshold_seconds": interval_seconds * STALE_MULTIPLIER,
                "last_success_at": last_success.isoformat() if last_success else None,
                "stale_for_seconds": since,
            },
        )
    return None


def _check_consecutive_failures(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    runs: list[Any],
) -> Finding | None:
    """Rule (b): last ≥3 runs are all failed."""
    streak = _trailing_failure_streak(runs)
    if streak >= CONSECUTIVE_FAILURE_THRESHOLD:
        # Surface the most-recent error message so the on-call can
        # triage without opening psql. Trim to 500 chars to keep the
        # webhook payload reasonable.
        last_err = runs[0].error_message if runs and runs[0].error_message else ""
        return Finding(
            job_name=job_name,
            rule="consecutive_failures",
            severity="warning",
            detected_at=now.isoformat(),
            details={
                "interval_seconds": interval_seconds,
                "consecutive_failure_count": streak,
                "last_error_message": (last_err or "")[:500],
            },
        )
    return None


def _check_credential_error(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    runs: list[Any],
) -> Finding | None:
    """Rule (c): most-recent failure's error mentions a credential class.

    Subset of rule (b) (only fires when the most-recent row is
    failed), but with ``severity="credential"`` so the pager /
    channel routing escalates it.
    """
    if not runs or runs[0].status != "failed":
        return None
    err = (runs[0].error_message or "").lower()
    matched = next((s for s in CREDENTIAL_ERROR_SUBSTRINGS if s in err), None)
    if matched is None:
        return None
    return Finding(
        job_name=job_name,
        rule="credential_error",
        severity="credential",
        detected_at=now.isoformat(),
        details={
            "interval_seconds": interval_seconds,
            "matched_substring": matched,
            "last_error_message": (runs[0].error_message or "")[:500],
        },
    )


# ─── Orchestration ─────────────────────────────────────────────────────


def evaluate(
    session_factory: Any,
    *,
    now: dt.datetime | None = None,
    jobs: dict[str, Any] | None = None,
) -> list[Finding]:
    """Run all three rules across every job in the JOBS registry.

    Args:
        session_factory: a SQLAlchemy ``sessionmaker`` (or any
            zero-arg callable returning a :class:`Session`). Tests
            inject a factory bound to the test engine; production
            uses :func:`tts_erp_v2.db.base.get_session_factory`.
        now: override for clock (testability). Defaults to
            ``datetime.now(tz=dt.timezone.utc)``.
        jobs: override for the registry (testability). Defaults to
            :data:`tts_erp_v2.sync_worker.scheduler.JOBS`.

    Returns:
        One :class:`Finding` per detected problem. Order is
        registry order × rule order. A job that triggers multiple
        rules (e.g. rule (b) AND (c)) produces multiple findings —
        this is intentional; the operator wants the credential
        finding AND the streak finding, not one OR the other.
    """
    if jobs is None:
        # Lazy import: keeps `python -c "import scripts.watchdog_sync"`
        # working without a configured DB / scheduler boot path.
        from tts_erp_v2.sync_worker.scheduler import JOBS

        jobs = JOBS
    if now is None:
        now = dt.datetime.now(tz=dt.timezone.utc)

    findings: list[Finding] = []
    for job_name, spec in jobs.items():
        sess = session_factory()
        try:
            runs = _recent_runs(sess, job_name)
            interval = int(getattr(spec, "interval_seconds", 0))
            for finding in (
                _check_stale_success(
                    job_name, interval, now=now, session=sess
                ),
                _check_consecutive_failures(
                    job_name, interval, now=now, runs=runs
                ),
                _check_credential_error(
                    job_name, interval, now=now, runs=runs
                ),
            ):
                if finding is not None:
                    findings.append(finding)
        finally:
            sess.close()
    return findings


def emit(findings: Iterable[Finding], *, stream=None) -> None:
    """Write one JSON line per finding to ``stream`` (default stderr).

    Always emit the ``watchdog_tick`` summary line first so a single
    cron tick is easy to grep out of the log::

        {"event":"watchdog_tick","ts":"...","finding_count":0}
        {"event":"finding","ts":"...","job_name":"...","rule":"...","severity":"..."}

    Findings are emitted AFTER the summary line so a downstream
    pipeline (filebeat → ES) can attribute them to the tick.

    Note: ``stream`` defaults to ``None`` and is resolved to
    ``sys.stderr`` at call time (NOT a default-arg of ``sys.stderr``).
    A default-arg would be evaluated at module import time, before
    pytest has a chance to replace ``sys.stderr`` for capture —
    the writes would then leak to the real terminal and
    ``capsys`` / ``capfd`` would see nothing.
    """
    findings = list(findings)
    if stream is None:
        stream = sys.stderr
    stream.write(
        json.dumps(
            {
                "event": "watchdog_tick",
                "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                "finding_count": len(findings),
            },
            sort_keys=True,
        )
        + "\n"
    )
    for f in findings:
        # Prepend the watchdog's own wrapper field so log scrapers can
        # route on ``event="finding"``; keep the Finding's own fields
        # inside ``details`` (and at the top level for grep convenience).
        envelope = {
            "event": "finding",
            "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            "job_name": f.job_name,
            "rule": f.rule,
            "severity": f.severity,
            "details": f.details,
        }
        stream.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n")
    stream.flush()


def post_webhook(url: str, findings: list[Finding], *, timeout: float = WEBHOOK_TIMEOUT_SECONDS) -> bool:
    """Best-effort POST the findings to ``url``. Returns True on success.

    Webhook failures are NEVER fatal — a flaky pager backend must
    not mask the stderr write. We log the failure (which the cron
    captures to logs/watchdog.log) and return False so the caller
    knows.
    """
    if not findings:
        # Don't ping the webhook when there's nothing to say — keeps
        # the pager quiet and saves a round-trip.
        return True
    payload = {
        "event": "watchdog_alert",
        "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "findings": [json.loads(f.to_json()) for f in findings],
    }
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dep
        log.warning("httpx not importable; skipping webhook POST")
        return False
    try:
        resp = httpx.post(
            url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            log.warning(
                "webhook POST %s returned %d: %s",
                url,
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — boundary
        log.warning("webhook POST %s failed: %s", url, exc)
        return False


# ─── Entrypoint ────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI: evaluate, emit, optionally POST webhook, return exit code.

    Exit codes:
        ``0`` — no findings (healthy).
        ``1`` — at least one finding (operator should investigate).
        ``2`` — fatal error (DB unreachable, no env, etc.).

    The ``2`` exit code lets cron / systemd distinguish "real alert"
    from "the watchdog itself broke" — the latter should page the
    on-call directly.
    """
    if not os.environ.get("TTS_ERP_DB_URL", "").strip():
        sys.stderr.write(
            "watchdog_sync: TTS_ERP_DB_URL is not set; refusing to run.\n"
            "Set it in /home/schan/tts-erp/.env or export it in the shell.\n"
        )
        return 2

    try:
        from tts_erp_v2.db.base import get_session_factory

        session_factory = get_session_factory()
    except Exception as exc:  # noqa: BLE001 — boundary
        sys.stderr.write(f"watchdog_sync: cannot build session factory: {exc}\n")
        return 2

    try:
        findings = evaluate(session_factory)
    except Exception as exc:  # noqa: BLE001 — boundary
        # DB error / model import failure — still emit a tick so log
        # scrapers see us alive, then bail with exit 2.
        sys.stderr.write(f"watchdog_sync: evaluation failed: {exc}\n")
        sys.stderr.write(
            json.dumps(
                {
                    "event": "watchdog_tick",
                    "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                    "finding_count": -1,
                    "error": str(exc),
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 2

    emit(findings)
    webhook_url = os.environ.get("TTS_ERP_ALERT_WEBHOOK_URL", "").strip()
    if webhook_url:
        post_webhook(webhook_url, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Finding",
    "CREDENTIAL_ERROR_SUBSTRINGS",
    "CONSECUTIVE_FAILURE_THRESHOLD",
    "STALE_MULTIPLIER",
    "RECENT_RUN_WINDOW",
    "evaluate",
    "emit",
    "post_webhook",
    "main",
]
