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

Or as a systemd timer (see docs/watchdog-deploy.md when written).
The webhook URL is configured via env::

    TTS_ERP_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
"""

from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlsplit
import logging
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Add the project root before importing the shared reloadable settings layer.
# This keeps direct CLI and systemd invocations consistent with the services.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Make the project root importable so ``from tts_erp_v2.…`` resolves
# even when the watchdog is invoked from a different CWD (e.g. cron
# spawning it with an absolute path). The cron recipe in this
# module's docstring uses ``cd /home/schan/tts-erp &&`` already, but
# we belt-and-brace it here so a one-off operator invocation from
# /tmp/ also works.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tts_erp_v2.runtime_settings import get_setting

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

#: 「上游登录态过期」的稳定标记 —— 由**作业自己**打进错误消息
#: （``tts_erp_v2/proxy/miaoshou/safe_message.py`` 的
#: :data:`CREDENTIAL_EXPIRED_MARKER`），watchdog 按标记匹配。
#:
#: 为什么不在上面那张表里直接加中文「您的授权已经过期」：
#: 那样等于把判据**绑在上游的措辞上**。2026-11-10 实测：上游那句中文不在表里
#: ⇒ 规则 (c) 一次都没命中 ⇒ ``miaoshou.purchase_price_clean`` 从
#: 2026-10-09 14:42 起连续失败 776 次（31 天）没人发现。上游改一次文案（或换
#: 语言），同样的盲区会再出现一次。⇒ 我们让作业打稳定标记，这里按标记匹配。
#:
#: ⚠️ 这里刻意**不 import** ``tts_erp_v2.proxy.miaoshou.safe_message``：
#: watchdog 以 ``python scripts/watchdog_sync.py`` 运行，``sys.path[0]`` 是
#: ``scripts/``，而 ``tts_erp_v2`` 在 venv 里是 **editable 安装**
#: （``__editable___tts_erp_2_0_0_finder.py`` 把 ``tts_erp_v2`` 映射到
#: ``/home/schan/tts-erp/tts_erp_v2``）⇒ import 到的是**主 checkout**，不是
#: 运行态那棵树。⇒ 同一份字面量在两处各存一份，改动必须同步；
#: ``tests/sync_worker/test_watchdog_sync.py`` 有一条断言专门盯这件事。
CREDENTIAL_EXPIRED_MARKERS: tuple[str, ...] = ("credential_expired",)

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

    def to_public(self) -> dict[str, Any]:
        """Outbound projection — allowlisted, non-identifying fields only.

        ``details`` is filtered key by key: rules (b)/(c) and the
        zombie rule can carry upstream error text, shop identifiers
        and credential hints in there, and this payload is about to
        leave the host.
        """
        return {
            "job_name": self.job_name,
            "rule": self.rule,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "details": {
                k: v for k, v in self.details.items() if k in PUBLIC_DETAIL_FIELDS
            },
        }


# ─── Outbound payload allowlist (脱敏在出网面，不在存储面) ───────────
# The webhook payload leaves this host. Only these keys may travel:
# internal job names, rule names, severity, timestamps, and threshold /
# duration style evidence that carries no business identifier.
#
# Explicitly NOT allowed outbound: shop_id / external_id / credential_id,
# upstream error codes and error text (may embed shop identifiers),
# any sync_jobs.extra field, and any request/response body.
#
# sync_jobs.error_message keeps its full text in the database for local
# audit; redaction happens here, on the way out.
PUBLIC_DETAIL_FIELDS: frozenset[str] = frozenset({
    "interval_seconds",
    "threshold_seconds",
    "stale_for_seconds",
    "zombie_age_seconds",
    "consecutive_failure_count",
    "oldest_running_started_at",
    "exit_code",
})


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
        # 作业自打的「登录态过期」稳定标记（见 CREDENTIAL_EXPIRED_MARKERS 注释）
        matched = next((s for s in CREDENTIAL_EXPIRED_MARKERS if s in err), None)
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


# ─── Rule (d): zombie running rows ──────────────────────────────────────
# A run whose worker was SIGKILLed (OOM killer) never writes finished_at,
# so its sync_jobs row stays status='running' forever. The scheduler uses
# max_instances=1, so that stale run blocks every later trigger of the same
# job: the job stops running without ever failing. Rules (a)-(c) cannot see
# this — (a) only looks at the last 'succeeded' row.
# Marker: RULE_D_ZOMBIE_RUNNING

ZOMBIE_MULTIPLIER = 2


def _check_zombie_running(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    session,
) -> Finding | None:
    """Rule (d): a 'running' row older than ``ZOMBIE_MULTIPLIER × interval``."""
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

    if interval_seconds <= 0:
        return None
    threshold = now - dt.timedelta(seconds=interval_seconds * ZOMBIE_MULTIPLIER)
    stmt = (
        select(SyncJob.started_at)
        .where(
            SyncJob.job_name == job_name,
            SyncJob.status == "running",
            SyncJob.started_at < threshold,
        )
        .order_by(SyncJob.started_at.asc())
        .limit(1)
    )
    oldest = session.execute(stmt).scalar_one_or_none()
    if oldest is None:
        return None
    return Finding(
        job_name=job_name,
        rule="zombie_running",
        severity="critical",
        detected_at=now.isoformat(),
        details={
            "interval_seconds": interval_seconds,
            "threshold_seconds": interval_seconds * ZOMBIE_MULTIPLIER,
            "oldest_running_started_at": oldest.isoformat(),
            "zombie_age_seconds": (now - oldest).total_seconds(),
        },
    )


# ─── Rule (e): concurrent writers ─────────────────────────────────────────
# 同一作业在同一时刻有 ≥2 个**活着的**写者 ⇒ 「同一时刻只有一个写者」失效。
#
# 两个关键点（否则这个判据会变成恒红的噪音，然后被忽略）：
# ① 报【活着的写者数】，不是【running 行数】。历史遗留的僵尸行（running 且没有
#    finished_at）永远躺在表里；按 running 行数计数 ⇒ 判据恒红 ⇒ 噪音 ⇒ 被忽略
#    （这个形态本项目已经出现过三次：僵尸行、偏差方向、克隆库再生）。
#    僵尸行**不是写者**：它什么都没写。
# ② 判据用【新产生量 == 0】，不是【当前值 == 0】。窗口之外发生的并发不该让判据
#    永远红；只有**观察窗口内新产生**的并发事件才算数。
#
# Marker: RULE_E_CONCURRENT_WRITERS

CONCURRENT_WRITERS_WINDOW_SECONDS = 3600


def _is_zombie_row(row: Any, *, now: dt.datetime, interval_seconds: int) -> bool:
    """这一行是不是僵尸（running、没有终态、且年龄超过 2×interval）。

    僵尸**不是写者**：它被 SIGKILL 了，什么都没写。把它算进并发会把判据变成
    恒红。
    """
    if getattr(row, "status", None) != "running":
        return False
    if interval_seconds <= 0:
        return False
    started = getattr(row, "started_at", None)
    if started is None:
        return False
    age = (now - started).total_seconds()
    return age > interval_seconds * ZOMBIE_MULTIPLIER


def concurrent_writer_report(
    runs: Iterable[Any],
    *,
    now: dt.datetime,
    interval_seconds: int,
    window_seconds: int = CONCURRENT_WRITERS_WINDOW_SECONDS,
) -> dict[str, Any]:
    """观察窗口内【新产生】的并发写事件 + 当前**活着的写者数**。

    Args:
        runs: 该作业最近的 sync_jobs 行（任何顺序）。
        now: 当前时刻。
        interval_seconds: 作业的调度间隔（用来判僵尸）。
        window_seconds: 观察窗口；窗口之外的事件不算「新产生」。

    Returns:
        ``{"live_writer_count": int, "zombie_count": int,
        "events": [{"overlap_start": ..., "rows": [...]}, ...]}``
    """
    window_start = now - dt.timedelta(seconds=window_seconds)
    live: list[tuple[Any, dt.datetime, dt.datetime]] = []
    zombies = 0
    alive = 0
    for row in runs:
        started = getattr(row, "started_at", None)
        if started is None:
            continue
        if _is_zombie_row(row, now=now, interval_seconds=interval_seconds):
            zombies += 1
            continue
        if getattr(row, "status", None) == "running":
            # 还在飞 ⇒ 写区间是 [started_at, now]（status 才是真相，
            # finished_at 只是历史遗留的清理痕迹）。
            alive += 1
            if started < window_start:
                continue
            live.append((row, started, now))
            continue
        if started < window_start:
            # 窗口之外 ⇒ 不是「新产生」的事件。
            continue
        finished = getattr(row, "finished_at", None)
        live.append((row, started, finished or now))

    events: list[dict[str, Any]] = []
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            row_a, start_a, end_a = live[i]
            row_b, start_b, end_b = live[j]
            overlap_start = max(start_a, start_b)
            overlap_end = min(end_a, end_b)
            if overlap_start < overlap_end:
                events.append(
                    {
                        "overlap_start": overlap_start.isoformat(),
                        "overlap_seconds": (overlap_end - overlap_start).total_seconds(),
                        "rows": [
                            {
                                "id": getattr(row_a, "id", None),
                                "status": getattr(row_a, "status", None),
                                "started_at": start_a.isoformat(),
                            },
                            {
                                "id": getattr(row_b, "id", None),
                                "status": getattr(row_b, "status", None),
                                "started_at": start_b.isoformat(),
                            },
                        ],
                    }
                )
    return {
        "live_writer_count": alive,
        "zombie_count": zombies,
        "events": events,
        "window_seconds": window_seconds,
    }


def _check_concurrent_writers(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    runs: list[Any],
) -> Finding | None:
    """Rule (e): 观察窗口内新产生了并发写者。"""
    report = concurrent_writer_report(
        runs, now=now, interval_seconds=interval_seconds
    )
    if not report["events"]:
        return None
    return Finding(
        job_name=job_name,
        rule="concurrent_writers",
        severity="critical",
        detected_at=now.isoformat(),
        details={
            "interval_seconds": interval_seconds,
            "live_writer_count": report["live_writer_count"],
            "zombie_count": report["zombie_count"],
            "window_seconds": report["window_seconds"],
            "concurrent_events": report["events"][:5],
            "concurrent_event_count": len(report["events"]),
        },
    )


# ─── Rule (f): refused by the writer lock ───────────────────────────────
# 拿不到写者锁 ⇒ 作业**拒绝启动**（不排队），并留下一行 status='skipped' 的痕迹。
# 用户暂不配置飞书 webhook ⇒ watchdog 日志是唯一的告警通道，所以这条痕迹必须
# 在 watchdog 里可见，而不是躺在没人看的 sync_jobs 表里。
#
# 与规则 (e) 同一个时间维度：只报**观察窗口内新产生**的痕迹，历史行不会让判据
# 永远红。
# Marker: RULE_F_WRITER_LOCK_REFUSED

WRITER_LOCK_REFUSED_MARKER = "writer lock held:"


def _check_writer_lock_refused(
    job_name: str,
    interval_seconds: int,
    *,
    now: dt.datetime,
    session,
) -> Finding | None:
    """Rule (f): 最近有作业被写者锁挡住（拒绝启动的留痕）。"""
    from sqlalchemy import func, select

    from tts_erp_v2.db.models import SyncJob

    window_start = now - dt.timedelta(seconds=CONCURRENT_WRITERS_WINDOW_SECONDS)
    stmt = (
        select(func.count())
        .where(
            SyncJob.job_name == job_name,
            SyncJob.status == "skipped",
            SyncJob.error_message.like(f"{WRITER_LOCK_REFUSED_MARKER}%"),
            SyncJob.started_at >= window_start,
        )
    )
    refused = int(session.execute(stmt).scalar_one())
    if refused == 0:
        return None
    return Finding(
        job_name=job_name,
        rule="writer_lock_refused",
        severity="critical",
        detected_at=now.isoformat(),
        details={
            "interval_seconds": interval_seconds,
            "window_seconds": CONCURRENT_WRITERS_WINDOW_SECONDS,
            "refused_count": refused,
            "note": "another instance holds the writer lock for this job",
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
                _check_zombie_running(
                    job_name, interval, now=now, session=sess
                ),
                _check_concurrent_writers(
                    job_name, interval, now=now, runs=runs
                ),
                _check_writer_lock_refused(
                    job_name, interval, now=now, session=sess
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
    if not url.lower().startswith(("http://", "https://")):
        log.warning("webhook URL must be http(s); refusing to POST")
        return False
    payload = {
        "event": "watchdog_alert",
        "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "findings": [f.to_public() for f in findings],
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
    if not (get_setting("TTS_ERP_DB_URL", "") or "").strip():
        sys.stderr.write(
            "watchdog_sync: TTS_ERP_DB_URL is not set; refusing to run.\n"
            "Configure local .env or TTS_ERP_RUNTIME_ENV_FILE.\n"
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
    webhook_url = (get_setting("TTS_ERP_ALERT_WEBHOOK_URL", "") or "").strip()
    if webhook_url:
        delivered = post_webhook(webhook_url, findings)
        if not delivered:
            # A dropped alert must leave a trace. Otherwise "the webhook
            # is configured" is indistinguishable from "the webhook works",
            # and a 401/400 from the destination looks exactly like success.
            sys.stderr.write(
                json.dumps(
                    {
                        "event": "watchdog_webhook_failed",
                        "ts": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
                        "url_host": urlsplit(webhook_url).hostname or "",
                        "finding_count": len(findings),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            sys.stderr.flush()
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Finding",
    "CREDENTIAL_ERROR_SUBSTRINGS",
    "CREDENTIAL_EXPIRED_MARKERS",
    "CONSECUTIVE_FAILURE_THRESHOLD",
    "STALE_MULTIPLIER",
    "CONCURRENT_WRITERS_WINDOW_SECONDS",
    "concurrent_writer_report",
    "WRITER_LOCK_REFUSED_MARKER",
    "RECENT_RUN_WINDOW",
    "evaluate",
    "emit",
    "post_webhook",
    "main",
]
