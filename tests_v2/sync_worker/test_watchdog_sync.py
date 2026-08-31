"""TDD tests for ``scripts/watchdog_sync.py`` — sync_jobs health watchdog.

Background
----------
The Sept 1 22-hour outage went unnoticed because the only signal was
a ``status='failed'`` row in ``integration.sync_jobs``. This script
closes that gap: it scans the sync_jobs table for three failure modes
and emits structured JSON findings (plus an optional webhook POST).

What's tested here
------------------
* ``evaluate()`` — the pure-detection core. Takes an injected
  ``session_factory`` + ``jobs`` registry + ``now`` clock so tests
  are deterministic and don't trip over production sync_jobs rows.
* ``emit()`` — JSON shape contract: one ``watchdog_tick`` line per
  run, one ``finding`` line per detected problem.
* ``post_webhook()`` — webhook delivery: succeeds when httpx
  returns 2xx; does NOT raise when httpx fails; skips when no
  findings (keeps the pager quiet).
* ``main()`` — env validation: refuses to run without
  ``TTS_ERP_DB_URL`` and returns exit code 2.

Isolation strategy
------------------
Each test seeds ``integration.sync_jobs`` rows under ``TEST_*``
prefixes via a fresh sessionmaker (``session_factory`` fixture) and
passes an isolated ``fake_jobs`` registry (also ``TEST_*`` prefixed).
The watchdog's ``evaluate(session_factory, now=..., jobs=fake_jobs)``
combination therefore only ever sees TEST_-prefixed rows in TEST_-
prefixed jobs — production rows in ``tiktok.orders`` etc. cannot
trigger spurious findings.

After each test, ``_cleanup_test_sync_jobs`` removes the seeded rows
in case the outer transaction didn't roll them back (defensive — the
default pytest fixtures here commit and must clean up explicitly).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

# Importing the watchdog script as a module — ``scripts/`` is importable
# (it has an empty ``__init__.py``) and the script's top-level code
# does NOT open a DB connection (env is loaded but no engine is built).
from scripts import (
    watchdog_sync as wd,  # noqa: E402 — sys.path mutation happens at import
)
from tts_erp_v2.db.models import SyncJob
from tts_erp_v2.sync_worker.scheduler import JobSpec

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory(db_engine) -> sessionmaker:
    """A real sessionmaker bound to the per-test engine.

    Seeded rows are committed (visible to the watchdog's own sessions
    via READ COMMITTED isolation) and cleaned up at teardown by the
    ``_cleanup_test_sync_jobs`` fixture.
    """
    return sessionmaker(bind=db_engine, expire_on_commit=False, future=True)


@pytest.fixture()
def _cleanup_test_sync_jobs(session_factory: sessionmaker) -> Any:
    """Remove every TEST_*-prefixed sync_jobs row after the test.

    Used by every test that seeds sync_jobs rows. The test-prefix
    convention matches the rest of tests_v2 (AGENTS.md §4: tests
    must carry the ``TEST_`` prefix on identifiers so cleanup can
    purge without touching real data).
    """
    yield
    sess = session_factory()
    try:
        sess.execute(
            delete(SyncJob).where(SyncJob.job_name.like("TEST_%"))
        )
        sess.commit()
    finally:
        sess.close()


def _make_sync_job(
    session,
    *,
    job_name: str,
    status: str,
    started_at: dt.datetime,
    error_message: str | None = None,
) -> SyncJob:
    """Persist one sync_jobs row. Caller is responsible for commit."""
    row = SyncJob(
        job_name=job_name,
        status=status,
        started_at=started_at,
        finished_at=started_at + dt.timedelta(seconds=1),
        rows_total=0,
        rows_inserted=0,
        rows_updated=0,
        rows_failed=0,
        error_message=error_message,
    )
    session.add(row)
    session.flush()
    return row


def _fixed_now() -> dt.datetime:
    """A pinned UTC timestamp so threshold calculations are deterministic."""
    return dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def _isolated_jobs() -> dict[str, JobSpec]:
    """Build a TEST_-prefixed jobs registry that doesn't collide with prod.

    Mirrors the production JOBS registry shape (intervals: 600/900/1800/
    3600/21600) so the watchdog's behaviour matches production exactly,
    but every job_name carries the ``TEST_`` prefix so the rows are
    trivially distinguishable in queries and cleanup is safe.
    """
    return {
        "TEST_tiktok.orders": JobSpec(
            job_name="TEST_tiktok.orders",
            module_path="x",
            interval_seconds=600,
        ),
        "TEST_tiktok.logistics": JobSpec(
            job_name="TEST_tiktok.logistics",
            module_path="x",
            interval_seconds=600,
        ),
        "TEST_tiktok.after_sales": JobSpec(
            job_name="TEST_tiktok.after_sales",
            module_path="x",
            interval_seconds=900,
        ),
        "TEST_tiktok.order_detail": JobSpec(
            job_name="TEST_tiktok.order_detail",
            module_path="x",
            interval_seconds=1800,
        ),
        "TEST_tiktok.finance": JobSpec(
            job_name="TEST_tiktok.finance",
            module_path="x",
            interval_seconds=3600,
        ),
        "TEST_tiktok.products": JobSpec(
            job_name="TEST_tiktok.products",
            module_path="x",
            interval_seconds=21600,
        ),
        "TEST_token.refresh": JobSpec(
            job_name="TEST_token.refresh",
            module_path="x",
            interval_seconds=21600,
            is_tiktok=False,
        ),
    }


def _single_job_registry(job_name: str, interval_seconds: int) -> dict[str, JobSpec]:
    """A registry with exactly one TEST_-prefixed job, sized to the test.

    Use this when the test only seeds rows for one job — the rest of
    ``_isolated_jobs()`` would otherwise produce stale_success
    findings (no row at all → ``_last_success_at`` returns None).
    """
    return {
        job_name: JobSpec(
            job_name=job_name,
            module_path="x",
            interval_seconds=interval_seconds,
        ),
    }


# ─── evaluate(): rule (a) — stale_success ───────────────────────────


def test_evaluate_no_alert_when_jobs_healthy(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """All seeded jobs have a recent succeeded row → no findings."""
    now = _fixed_now()
    # Single-job registry so "no rows for the others" doesn't trigger
    # stale_success findings on jobs we never intended to monitor.
    jobs = {
        "TEST_tiktok.orders": JobSpec(
            job_name="TEST_tiktok.orders",
            module_path="x",
            interval_seconds=600,
        ),
    }
    sess = session_factory()
    try:
        # tiktok.orders: interval=600s, 3x=1800s. Last success 5 min ago = fresh.
        _make_sync_job(
            sess,
            job_name="TEST_tiktok.orders",
            status="succeeded",
            started_at=now - dt.timedelta(minutes=5),
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    assert findings == []


def test_evaluate_stale_success_no_recent_success(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """Job hasn't succeeded in > 3 × interval → stale_success finding."""
    now = _fixed_now()
    # Single-job registry so we only assert on the row we seeded.
    jobs = _single_job_registry("TEST_tiktok.orders", interval_seconds=600)
    sess = session_factory()
    try:
        # TEST_tiktok.orders: 600s interval → 3x = 1800s = 30 min.
        # Last success 1 hour ago → stale.
        _make_sync_job(
            sess,
            job_name="TEST_tiktok.orders",
            status="succeeded",
            started_at=now - dt.timedelta(hours=1),
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    stale = [f for f in findings if f.rule == "stale_success"]
    assert len(stale) == 1
    assert stale[0].job_name == "TEST_tiktok.orders"
    assert stale[0].severity == "warning"
    assert stale[0].details["interval_seconds"] == 600
    assert stale[0].details["threshold_seconds"] == 1800
    assert stale[0].details["stale_for_seconds"] == pytest.approx(3600.0, abs=1)


def test_evaluate_stale_success_never_succeeded(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """Job has zero succeeded rows → stale (with last_success_at=None)."""
    now = _fixed_now()
    # Single-job registry so we only assert on the row we seeded.
    jobs = _single_job_registry("TEST_token.refresh", interval_seconds=21600)
    sess = session_factory()
    try:
        # Seed only failed rows — never a success.
        _make_sync_job(
            sess,
            job_name="TEST_token.refresh",
            status="failed",
            started_at=now - dt.timedelta(minutes=10),
            error_message="ValueError: something broke",
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    stale = [f for f in findings if f.rule == "stale_success"]
    assert len(stale) == 1
    assert stale[0].job_name == "TEST_token.refresh"
    assert stale[0].details["last_success_at"] is None
    assert stale[0].details["stale_for_seconds"] is None


# ─── evaluate(): rule (b) — consecutive_failures ─────────────────────


def test_evaluate_consecutive_failures_below_threshold_no_alert(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """2 consecutive failures (< 3 threshold) → no consecutive_failures finding."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        # Newest-first ordering: most recent row at index 0.
        # Last 2 are failed, then a success → streak = 2 (< threshold).
        base = now - dt.timedelta(hours=2)
        runs = [
            ("succeeded", base + dt.timedelta(minutes=10)),  # oldest
            ("failed",    base + dt.timedelta(minutes=20)),
            ("failed",    base + dt.timedelta(minutes=30)),  # newest
        ]
        for status, ts in runs:
            _make_sync_job(
                sess,
                job_name="TEST_tiktok.orders",
                status=status,
                started_at=ts,
                error_message=("boom" if status == "failed" else None),
            )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    streaks = [f for f in findings if f.rule == "consecutive_failures"]
    assert streaks == []


def test_evaluate_consecutive_failures_meets_threshold(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """3 consecutive failures → consecutive_failures finding."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        base = now - dt.timedelta(hours=2)
        runs = [
            ("succeeded", base + dt.timedelta(minutes=0)),   # oldest
            ("failed",    base + dt.timedelta(minutes=10)),
            ("failed",    base + dt.timedelta(minutes=20)),
            ("failed",    base + dt.timedelta(minutes=30)),  # newest
        ]
        for status, ts in runs:
            _make_sync_job(
                sess,
                job_name="TEST_tiktok.orders",
                status=status,
                started_at=ts,
                error_message=("TypeError: arg missing" if status == "failed" else None),
            )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    streaks = [f for f in findings if f.rule == "consecutive_failures"]
    assert len(streaks) == 1
    assert streaks[0].job_name == "TEST_tiktok.orders"
    assert streaks[0].details["consecutive_failure_count"] == 3
    assert "TypeError" in streaks[0].details["last_error_message"]


def test_evaluate_consecutive_failures_long_streak(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """10 consecutive failures → streak count is exactly the visible length."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        for i in range(10):
            _make_sync_job(
                sess,
                job_name="TEST_tiktok.orders",
                status="failed",
                started_at=now - dt.timedelta(minutes=i * 10),
                error_message=f"attempt {i} failed",
            )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    streaks = [f for f in findings if f.rule == "consecutive_failures"]
    assert len(streaks) == 1
    assert streaks[0].details["consecutive_failure_count"] == 10


# ─── evaluate(): rule (c) — credential_error ────────────────────────


def test_evaluate_credential_error_highlight(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """Most-recent failure mentions DecryptionError → credential finding.

    Note: this is a SUBSET of (b) (3 consecutive failures → also a
    streak finding). The watchdog intentionally emits both — operator
    wants the credential finding AND the streak finding.
    """
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        base = now - dt.timedelta(hours=2)
        # 3 failures, last (newest) error is credential-class.
        for i in range(3):
            _make_sync_job(
                sess,
                job_name="TEST_tiktok.products",
                status="failed",
                # i=0 is the newest (most recent in time).
                started_at=base + dt.timedelta(minutes=10 * (3 - i)),
                error_message=(
                    "DecryptionError: Fernet decryption failed"
                    if i == 0
                    else f"TransientError #{i}"
                ),
            )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    creds = [f for f in findings if f.rule == "credential_error"]
    assert len(creds) == 1
    assert creds[0].severity == "credential"
    assert creds[0].job_name == "TEST_tiktok.products"
    assert creds[0].details["matched_substring"] == "decryptionerror"
    # The streak finding also fires (credential is a subset).
    streaks = [f for f in findings if f.rule == "consecutive_failures"]
    assert len(streaks) == 1


def test_evaluate_credential_error_authentication(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """AuthenticationError in error_message → credential finding."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        _make_sync_job(
            sess,
            job_name="TEST_token.refresh",
            status="failed",
            started_at=now - dt.timedelta(minutes=15),
            error_message=(
                "AuthenticationError: 401 from upstream; refresh failed"
            ),
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    creds = [f for f in findings if f.rule == "credential_error"]
    assert len(creds) == 1
    assert creds[0].details["matched_substring"] == "authenticationerror"


def test_evaluate_non_credential_failure_no_credential_finding(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """A TypeError-class failure does NOT trigger credential_error."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        _make_sync_job(
            sess,
            job_name="TEST_tiktok.orders",
            status="failed",
            started_at=now - dt.timedelta(minutes=5),
            error_message="TypeError: missing kwarg",
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    creds = [f for f in findings if f.rule == "credential_error"]
    assert creds == []


# ─── emit(): JSON output contract ────────────────────────────────────


def test_emit_writes_tick_summary_and_finding_lines(capfd: pytest.CaptureFixture[str]) -> None:
    """emit() prints one tick summary then one line per finding.

    Uses ``capfd`` (file-descriptor level capture) rather than
    ``capsys`` because the watchdog binds ``sys.stderr`` at module
    import time — pytest's ``capsys`` replaces the Python ``sys``
    attribute but a default-arg captured earlier may keep the
    original reference. ``capfd`` captures at the OS level so it's
    immune to this.
    """
    findings = [
        wd.Finding(
            job_name="TEST_tiktok.orders",
            rule="stale_success",
            severity="warning",
            detected_at="2026-09-01T12:00:00+00:00",
            details={"interval_seconds": 600, "threshold_seconds": 1800},
        ),
        wd.Finding(
            job_name="TEST_token.refresh",
            rule="credential_error",
            severity="credential",
            detected_at="2026-09-01T12:00:00+00:00",
            details={"matched_substring": "decryptionerror"},
        ),
    ]
    wd.emit(findings)

    out = capfd.readouterr().err.splitlines()
    assert len(out) == 3  # 1 tick + 2 findings

    tick = json.loads(out[0])
    assert tick["event"] == "watchdog_tick"
    assert tick["finding_count"] == 2

    f0 = json.loads(out[1])
    assert f0["event"] == "finding"
    assert f0["job_name"] == "TEST_tiktok.orders"
    assert f0["rule"] == "stale_success"

    f1 = json.loads(out[2])
    assert f1["event"] == "finding"
    assert f1["job_name"] == "TEST_token.refresh"
    assert f1["severity"] == "credential"


def test_emit_no_findings_only_tick_line(capfd: pytest.CaptureFixture[str]) -> None:
    """Empty findings list → only the tick summary line (finding_count=0)."""
    wd.emit([])
    out = capfd.readouterr().err.splitlines()
    assert len(out) == 1
    tick = json.loads(out[0])
    assert tick["event"] == "watchdog_tick"
    assert tick["finding_count"] == 0


def test_emit_writes_to_custom_stream() -> None:
    """emit() honours a caller-supplied stream (default is sys.stderr)."""
    buf = io.StringIO()
    findings = [
        wd.Finding(
            job_name="TEST_tiktok.orders",
            rule="consecutive_failures",
            severity="warning",
            detected_at="2026-09-01T12:00:00+00:00",
            details={"consecutive_failure_count": 3},
        ),
    ]
    wd.emit(findings, stream=buf)
    out = buf.getvalue().splitlines()
    assert len(out) == 2
    assert json.loads(out[0])["event"] == "watchdog_tick"
    assert json.loads(out[1])["event"] == "finding"


# ─── post_webhook(): optional outbound delivery ──────────────────────


def test_post_webhook_skips_when_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """No findings → no HTTP call at all (saves the pager a ping)."""
    calls: list[dict] = []

    def fake_post(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return httpx.Response(200, request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr(httpx, "post", fake_post)
    ok = wd.post_webhook("https://hooks.example/x", [])
    assert ok is True
    assert calls == []


def test_post_webhook_success_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx returning 2xx → True."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(200, request=httpx.Request("POST", "https://x")),
    )
    findings = [
        wd.Finding(
            job_name="TEST_tiktok.orders",
            rule="stale_success",
            severity="warning",
            detected_at="2026-09-01T12:00:00+00:00",
            details={},
        ),
    ]
    assert wd.post_webhook("https://hooks.example/x", findings) is True


def test_post_webhook_4xx_returns_false_no_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """4xx/5xx response → False, no exception. Logs a warning."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            500, request=httpx.Request("POST", "https://x"), text="boom"
        ),
    )
    findings = [
        wd.Finding(
            job_name="TEST_tiktok.orders",
            rule="stale_success",
            severity="warning",
            detected_at="2026-09-01T12:00:00+00:00",
            details={},
        ),
    ]
    with caplog.at_level(logging.WARNING):
        ok = wd.post_webhook("https://hooks.example/x", findings)
    assert ok is False
    # Warning was logged so the cron operator sees the failure.
    assert any("webhook" in r.message.lower() for r in caplog.records)


def test_post_webhook_httpx_exception_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Network error → False, no exception. Webhook failure is non-fatal."""
    def boom(*a: Any, **kw: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    findings = [
        wd.Finding(
            job_name="TEST_tiktok.orders",
            rule="stale_success",
            severity="warning",
            detected_at="2026-09-01T12:00:00+00:00",
            details={},
        ),
    ]
    with caplog.at_level(logging.WARNING):
        ok = wd.post_webhook("https://hooks.example/x", findings)
    assert ok is False
    assert any("webhook" in r.message.lower() for r in caplog.records)


# ─── main(): env validation ──────────────────────────────────────────


def test_main_returns_2_when_db_url_unset(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No TTS_ERP_DB_URL → fatal exit 2 (distinct from healthy exit 0)."""
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
    rc = wd.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "TTS_ERP_DB_URL" in err


def test_main_skips_webhook_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    db_engine,
) -> None:
    """No TTS_ERP_ALERT_WEBHOOK_URL → no POST. Does not crash.

    We patch ``evaluate`` to return one finding so the script reaches
    the webhook branch and we can verify it doesn't try to POST
    without a configured URL. The script returns 1 (findings present).
    """
    monkeypatch.delenv("TTS_ERP_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(
        wd,
        "evaluate",
        lambda _sf: [
            wd.Finding(
                job_name="TEST_x",
                rule="stale_success",
                severity="warning",
                detected_at="2026-09-01T12:00:00+00:00",
                details={},
            )
        ],
    )
    # If main() tried to POST, httpx would attempt the call. With the
    # env var unset, the webhook branch is skipped — exit 1 (findings).
    rc = wd.main([])
    assert rc == 1


# ─── JOBS-driven interval loading (no hardcoding) ────────────────────


def test_evaluate_uses_jobs_registry_intervals(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """The watchdog pulls intervals from the JOBS registry, not literals.

    If someone bumps an interval in scheduler.JOBS, the watchdog
    automatically tracks — no script edit needed. This test proves
    the indirection by substituting a fake registry with two jobs
    at very different intervals.
    """
    fake_jobs = {
        "TEST_fake.fast": JobSpec(
            job_name="TEST_fake.fast",
            module_path="x",
            interval_seconds=60,  # 3x = 180s = 3 min threshold
        ),
        "TEST_fake.slow": JobSpec(
            job_name="TEST_fake.slow",
            module_path="x",
            interval_seconds=86400,  # 3x = 259200s = 72 h threshold
        ),
    }
    now = _fixed_now()
    sess = session_factory()
    try:
        # TEST_fake.fast: 1 hour stale → finds stale (3-min threshold).
        _make_sync_job(
            sess,
            job_name="TEST_fake.fast",
            status="succeeded",
            started_at=now - dt.timedelta(hours=1),
        )
        # TEST_fake.slow: 1 hour stale → NOT stale (72-hour threshold).
        _make_sync_job(
            sess,
            job_name="TEST_fake.slow",
            status="succeeded",
            started_at=now - dt.timedelta(hours=1),
        )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=fake_jobs)
    by_job = {f.job_name: f for f in findings if f.rule == "stale_success"}
    assert "TEST_fake.fast" in by_job
    assert "TEST_fake.slow" not in by_job
    assert by_job["TEST_fake.fast"].details["threshold_seconds"] == 180


# ─── Cross-rule interaction: one finding, multiple rules ─────────────


def test_evaluate_emits_multiple_findings_for_same_job_when_both_apply(
    session_factory: sessionmaker,
    _cleanup_test_sync_jobs,
) -> None:
    """When stale AND consecutive-failures AND credential-error all apply,
    three findings are emitted (operator wants all signals, not just one)."""
    now = _fixed_now()
    jobs = _isolated_jobs()
    sess = session_factory()
    try:
        # TEST_tiktok.products: interval=21600s, 3x = 64800s = 18h.
        # Seed 3 DecryptionError failures, oldest at 1 day ago, newest at 1h ago.
        # Stale (last success = None → infinitely stale) + streak (3)
        # + credential_error (substring match).
        for i in range(3):
            _make_sync_job(
                sess,
                job_name="TEST_tiktok.products",
                status="failed",
                started_at=now - dt.timedelta(hours=i + 1),
                error_message="DecryptionError: ciphertext corrupt",
            )
        sess.commit()
    finally:
        sess.close()

    findings = wd.evaluate(session_factory, now=now, jobs=jobs)
    products_findings = [f for f in findings if f.job_name == "TEST_tiktok.products"]
    rules = {f.rule for f in products_findings}
    assert rules == {"stale_success", "consecutive_failures", "credential_error"}
    severities = {f.severity for f in products_findings}
    assert severities == {"warning", "credential"}
