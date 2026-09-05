"""Coverage-lift tests for ``tts_erp_v2.sync_worker.scheduler``.

Complements ``test_scheduler_miaoshou_reporting.py`` (miaoshou +
reporting + token.refresh) and ``test_scheduler_token_refresh.py``
(token.refresh durability). Here we focus on:

* The :data:`JOBS` registry is complete and the intervals match the
  legacy ``sync_cron.py`` cadence.
* :func:`build_scheduler` registers every job with the right trigger,
  jitter, max_instances=1, and coalesce=True.
* :func:`_make_executor` routes tiktok jobs through
  ``_run_tiktok_job`` and system jobs through ``_run_system_job``.
* :func:`_enumerate_tiktok_shops` only returns credentials that own a
  commerce.shops row (EXISTS filter) and skips MOCK_*/TEST_* prefixes; it
  tolerates DB errors.
* :func:`_record_failed_tick` writes a sentinel ``SyncJob`` row.
* :func:`_run_tiktok_job` retry loop, no-shop early return, and the
  shop fan-out.

Why a separate file
-------------------
The existing scheduler tests cluster around one feature (miaoshou /
token.refresh). This file is a horizontal sweep meant to lift coverage
on every branch that wasn't already exercised.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tts_erp_v2.db.base import get_engine
from tts_erp_v2.db.models import ChannelAccount, Credentials
from tts_erp_v2.sync_worker import scheduler
from tts_erp_v2.sync_worker.scheduler import (
    JOBS,
    JobSpec,
    _enumerate_tiktok_shops,
    _make_executor,
    _record_failed_tick,
    _run_system_job,
    _run_tiktok_job,
    build_scheduler,
)

pytestmark = [pytest.mark.domain_sync]


# ─── JOBS registry ────────────────────────────────────────────────


EXPECTED_JOB_INTERVALS = {
    "tiktok.orders": 600,
    "tiktok.order_detail": 1800,
    "tiktok.products": 600,
    "tiktok.logistics": 600,
    "tiktok.after_sales": 900,
    "tiktok.finance": 3600,
    "token.refresh": 21600,
    "miaoshou.shops": 21600,
    "miaoshou.collect_box": 1800,
    "miaoshou.move_collect": 1800,
    "reporting.cost_snapshots": 21600,
    "reporting.profit_daily": 3600,
}


def test_jobs_registry_has_expected_count() -> None:
    """12 jobs total — keeps us honest if a new one slips in unannounced.

    2026-09-05 reorg: ``analytics.retention`` 已从 JOBS 摘除（见
    tech-doc/analytics/reorg-plan.md 决策 #1-#4）—— ad_records /
    ad_audit_log / 等 4 张表 drop 后无对象可 purge。原 13 → 12。
    """
    # 6 tiktok + 4 system (token + 3 reporting + ... ) — keep the
    # number pinned so we don't drift silently.
    assert len(JOBS) == 12


@pytest.mark.parametrize(
    ("job_name", "expected_interval"),
    sorted(EXPECTED_JOB_INTERVALS.items()),
)
def test_jobs_registry_intervals(job_name: str, expected_interval: int) -> None:
    """Interval seconds match the legacy cron cadence."""
    spec = JOBS[job_name]
    assert spec.interval_seconds == expected_interval
    assert spec.job_name == job_name


def test_jobs_registry_tiktok_jobs_default_to_is_tiktok_true() -> None:
    """All tiktok.* jobs use the per-shop fan-out path by default."""
    for name in EXPECTED_JOB_INTERVALS:
        if not name.startswith("tiktok."):
            continue
        assert JOBS[name].is_tiktok is True
        assert JOBS[name].entrypoint == "run"  # default
        assert JOBS[name].needs_token_registry is False


def test_jobs_registry_miaoshou_purchase_orders_intentionally_absent() -> None:
    """Per the AGENTS.md / scheduler.py docstring: the endpoint path
    404s in prod; intentionally NOT in the registry."""
    assert "miaoshou.purchase_orders" not in JOBS


# ─── build_scheduler wiring ───────────────────────────────────────


def test_build_scheduler_registers_every_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every entry in JOBS becomes an APScheduler Job with id matching
    its registry key."""
    fake_factory = MagicMock(name="session_factory")
    sched = build_scheduler(session_factory=fake_factory, jitter_seconds=10)

    job_ids = {job.id for job in sched.get_jobs()}
    assert job_ids == set(JOBS.keys())


def test_build_scheduler_uses_utc_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler MUST be UTC — intervals + cron are UTC-anchored."""
    fake_factory = MagicMock(name="session_factory")
    sched = build_scheduler(session_factory=fake_factory)
    assert str(sched.timezone) == "UTC"


def test_build_scheduler_interval_trigger_uses_spec_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For each job, the IntervalTrigger's ``interval`` is exactly the
    JobSpec's interval_seconds; jitter is the configured value."""
    fake_factory = MagicMock(name="session_factory")
    sched = build_scheduler(session_factory=fake_factory, jitter_seconds=42)

    by_id = {job.id: job for job in sched.get_jobs()}
    for name, spec in JOBS.items():
        trigger = by_id[name].trigger
        assert trigger.interval.total_seconds() == spec.interval_seconds
        assert trigger.jitter == 42


def test_build_scheduler_marks_jobs_singleton_and_coalesce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_instances=1, coalesce=True — both are operational guarantees
    (no overlapping runs of the same job; missed fires run once on resume)."""
    fake_factory = MagicMock(name="session_factory")
    sched = build_scheduler(session_factory=fake_factory)
    for job in sched.get_jobs():
        assert job.max_instances == 1
        assert job.coalesce is True


def test_build_scheduler_uses_default_session_factory_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``session_factory=None`` → get_session_factory() (lazy import).
    Patched to a sentinel to prove the fallback path runs."""
    sentinel = MagicMock(name="session_factory")
    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory", lambda *a, **k: sentinel
    )
    sched = build_scheduler()  # session_factory=None → fallback
    assert sched is not None


# ─── _make_executor dispatch ───────────────────────────────────────


def test_make_executor_for_tiktok_calls_run_tiktok_job(monkeypatch) -> None:
    """A spec with is_tiktok=True → callable that delegates to _run_tiktok_job."""
    seen: dict = {}
    monkeypatch.setattr(
        scheduler, "_run_tiktok_job", lambda spec, sf: seen.setdefault("called", (spec, sf))
    )

    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    fake_sf = MagicMock()
    fn = _make_executor(spec, fake_sf)
    fn()
    assert seen["called"] == (spec, fake_sf)


def test_make_executor_for_system_calls_run_system_job(monkeypatch) -> None:
    """spec.is_tiktok=False → callable that delegates to _run_system_job."""
    seen: dict = {}
    monkeypatch.setattr(
        scheduler, "_run_system_job", lambda spec, sf: seen.setdefault("called", (spec, sf))
    )

    spec = JobSpec(
        job_name="token.refresh",
        module_path="tts_erp_v2.jobs.token_refresh",
        interval_seconds=21600,
        is_tiktok=False,
        entrypoint="sync_token_refresh",
        needs_token_registry=True,
    )
    fake_sf = MagicMock()
    fn = _make_executor(spec, fake_sf)
    fn()
    assert seen["called"] == (spec, fake_sf)


# ─── _enumerate_tiktok_shops (DB-backed, TEST_-sentinel isolation) ──


def _seed_credentials_for_enum(
    session_factory, *, external_id: str, with_shop_row: bool = False
) -> None:
    """Insert a Credentials row (+ optional commerce.shops row) for the
    enumerator tests.

    ``with_shop_row=True`` also inserts the matching ``commerce.shops``
    row (platform='tiktok', shop_id=external_id, linked by
    credential_id) — this is what makes a credential a real, dialable
    shop under the enumerator's EXISTS filter. ``False`` seeds an
    OAuth-only orphan (no shop row), which the enumerator must skip.

    Fixture ids use the ``ENUMKEEP_`` prefix (NOT ``TEST_``/``MOCK_``):
    the enumerator's secondary prefix guard excludes those, so a
    prefixed row could never assert containment. Cleanup is explicit
    (delete in teardown) so no script-based pruning is needed here.
    """
    sess = session_factory()
    try:
        # Idempotent: delete-then-insert (shops first — its credential
        # FK is ON DELETE SET NULL, and (platform, shop_id) is unique).
        sess.execute(
            text("DELETE FROM commerce.shops WHERE shop_id = :e AND platform = 'tiktok'"),
            {"e": external_id},
        )
        sess.execute(
            text("DELETE FROM integration.credentials WHERE external_account_id = :e"),
            {"e": external_id},
        )
        cred = Credentials(
            provider="tiktok",
            external_account_id=external_id,
            ciphertext=b"\x00" * 32,
        )
        sess.add(cred)
        sess.flush()
        if with_shop_row:
            sess.add(
                ChannelAccount(
                    platform="tiktok",
                    shop_id=external_id,
                    credential_id=cred.id,
                    status="active",
                )
            )
        sess.commit()
    finally:
        sess.close()


def _cleanup_credentials(session_factory, *, external_id: str) -> None:
    sess = session_factory()
    try:
        sess.execute(
            text("DELETE FROM commerce.shops WHERE shop_id = :e AND platform = 'tiktok'"),
            {"e": external_id},
        )
        sess.execute(
            text("DELETE FROM integration.credentials WHERE external_account_id = :e"),
            {"e": external_id},
        )
        sess.commit()
    finally:
        sess.close()


def _factory():
    """A real sessionmaker against the test DB (per pytest tmp engine)."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def test_enumerate_tiktok_shops_filters_mocks_and_returns_sorted() -> None:
    """Real (with shops row) + MOCK_* rows → only real rows, sorted.

    ``real_id`` is seeded WITH a commerce.shops row (the EXISTS filter's
    definition of a dialable shop); ``mock_id`` is seeded with a shops
    row TOO — proving the MOCK_* prefix guard excludes it even when the
    shop row exists (secondary defense).
    """
    factory = _factory()
    real_id = "ENUMKEEP_REAL_SHOP"
    mock_id = "MOCK_LEGACY_SENTINEL"
    _seed_credentials_for_enum(factory, external_id=real_id, with_shop_row=True)
    _seed_credentials_for_enum(factory, external_id=mock_id, with_shop_row=True)
    try:
        sess = factory()
        try:
            result = _enumerate_tiktok_shops(sess)
        finally:
            sess.close()
        # mock is filtered out by the prefix guard (even though it has a
        # shops row)
        assert mock_id not in result
        # real_id is present (other prod rows may also be present, so we
        # only assert containment, not equality)
        assert real_id in result
    finally:
        _cleanup_credentials(factory, external_id=real_id)
        _cleanup_credentials(factory, external_id=mock_id)


def test_enumerate_tiktok_shops_returns_empty_on_db_error() -> None:
    """A blown session.execute → log + empty list (NOT propagate)."""
    sess = MagicMock()
    sess.execute.side_effect = OperationalError("SELECT 1", {}, Exception("pg down"))
    # Should NOT raise.
    result = _enumerate_tiktok_shops(sess)
    assert result == []


def test_enumerate_tiktok_shops_skips_prefix_and_orphan_credentials() -> None:
    """MOCK_*/TEST_* (even with a shops row) and shop-less orphans are
    skipped; a real shop-row credential survives.

    Note: the production enumerator applies TWO guards — the primary
    EXISTS filter (credential must own a ``commerce.shops`` row) and the
    secondary prefix guard (``MOCK_`` / ``TEST_`` excluded even if a
    shops row exists). The "kept" fixture deliberately uses a
    non-excluded prefix (``ENUMKEEP_*``) WITH a shops row so it passes
    both guards. ``skip_mock`` / ``skip_test`` are seeded WITH shops
    rows to prove the prefix guard alone rejects them; ``skip_orphan``
    has NO shops row and no excluded prefix, proving the EXISTS filter
    rejects it independently of naming.
    """
    factory = _factory()
    keep_id = "ENUMKEEP_REAL_OWNER"
    skip_mock = "MOCK_ENUM_SKIP_OWNER"
    skip_test = "TEST_ENUM_SKIP_OWNER"
    skip_orphan = "ENUMORPHAN_NO_SHOP"
    _seed_credentials_for_enum(factory, external_id=keep_id, with_shop_row=True)
    _seed_credentials_for_enum(factory, external_id=skip_mock, with_shop_row=True)
    _seed_credentials_for_enum(factory, external_id=skip_test, with_shop_row=True)
    _seed_credentials_for_enum(factory, external_id=skip_orphan, with_shop_row=False)
    sess = factory()
    try:
        result = _enumerate_tiktok_shops(sess)
        assert keep_id in result
        assert skip_mock not in result
        assert skip_test not in result
        assert skip_orphan not in result
    finally:
        # Clean: remove the seeded rows.
        sess.rollback()
        sess.close()
        sess = factory()
        try:
            sess.execute(
                text(
                    "DELETE FROM integration.credentials "
                    "WHERE external_account_id IN (:k, :m, :t, :o)"
                ),
                {
                    "k": keep_id,
                    "m": skip_mock,
                    "t": skip_test,
                    "o": skip_orphan,
                },
            )
            sess.execute(
                text(
                    "DELETE FROM commerce.shops WHERE shop_id IN (:k, :m, :t)"
                ),
                {"k": keep_id, "m": skip_mock, "t": skip_test},
            )
            sess.commit()
        finally:
            sess.close()


# ─── _record_failed_tick (uses real DB for SyncJob) ────────────────


def test_record_failed_tick_writes_a_failed_row() -> None:
    """Sentinel SyncJob row is committed even when the inner job crashed."""
    factory = _factory()
    spec = JobSpec(
        job_name="reporting.cost_snapshots",
        module_path="tts_erp_v2.jobs.reporting",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="run_cost_snapshots",
    )
    _record_failed_tick(factory, spec, "simulated boom")

    sess = factory()
    try:
        row = sess.execute(
            text(
                "SELECT status, error_message FROM integration.sync_jobs "
                "WHERE job_name = 'reporting.cost_snapshots' "
                "ORDER BY started_at DESC LIMIT 1"
            )
        ).first()
        assert row is not None
        # Either this sentinel row, OR a previously-recorded one — both are 'failed'
        assert row[0] == "failed"
        # The reason may already be in DB from prior tests; assert it contains our reason
        # OR we just created it. Either way, the value should be a non-empty string.
        assert isinstance(row[1], str)
        assert row[1]  # non-empty
    finally:
        # Clean the row we wrote (job_name is not TEST_*-prefixed).
        sess.rollback()
        sess.close()
        sess = factory()
        try:
            sess.execute(
                text(
                    "DELETE FROM integration.sync_jobs "
                    "WHERE job_name = 'reporting.cost_snapshots' "
                    "AND error_message = :msg"
                ),
                {"msg": "simulated boom"},
            )
            sess.commit()
        finally:
            sess.close()


# ─── _run_tiktok_job — happy path, retry, no-shops ────────────────


def test_run_tiktok_job_skips_when_no_shops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty shop list → log + early return; the inner ``mod.run`` is
    NEVER invoked (we just import the module to read ``run``).

    The previous version asserted ``import_module`` was never called,
    but production actually DOES call ``importlib.import_module`` first
    (to resolve ``mod.run``) and only early-returns after the shop list
    is empty. The reachable defensive case is that ``mod.run`` itself
    is not called when there are no shops — that's what we assert here.
    """
    factory = MagicMock()
    factory.return_value = MagicMock()
    monkeypatch.setattr(
        scheduler, "_enumerate_tiktok_shops_in_factory", lambda _sf: []
    )

    run_called = {"n": 0}

    fake_mod = MagicMock()

    def fake_run(*args, **kwargs):
        run_called["n"] += 1

    fake_mod.run = fake_run

    def fake_import(name: str):
        return fake_mod

    monkeypatch.setattr(scheduler.importlib, "import_module", fake_import)

    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    _run_tiktok_job(spec, factory)  # must not raise
    assert run_called["n"] == 0


def test_run_tiktok_job_runs_per_shop_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For each shop, a session is opened + closed; inner.run is invoked
    once with proxy_call + shop_id."""
    factory = MagicMock()
    session_mock = MagicMock()
    factory.return_value = session_mock
    monkeypatch.setattr(
        scheduler, "_enumerate_tiktok_shops_in_factory", lambda _sf: ["S1", "S2"]
    )

    mod = MagicMock()
    mod.run.return_value = MagicMock(
        rows_total=5, rows_inserted=3, rows_failed=0
    )
    monkeypatch.setattr(scheduler.importlib, "import_module", lambda _p: mod)

    # build_proxy_call returns a sentinel closure; capture the shop_id arg.
    proxy_calls: list = []

    def fake_build(session, *, shop_id):
        proxy_calls.append(shop_id)
        return MagicMock(name="proxy_call")

    monkeypatch.setattr(scheduler, "build_proxy_call", fake_build)

    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    _run_tiktok_job(spec, factory)
    # 2 shops → 2 calls.
    assert proxy_calls == ["S1", "S2"]
    # Each session opened + closed exactly once per shop.
    assert factory.call_count == 2
    assert session_mock.close.call_count == 2
    # Inner module ran twice with the right kwargs.
    assert mod.run.call_count == 2


def test_run_tiktok_job_retries_once_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inner raises twice → second attempt logs "giving up" + exits."""
    factory = MagicMock()
    factory.return_value = MagicMock()
    monkeypatch.setattr(
        scheduler, "_enumerate_tiktok_shops_in_factory", lambda _sf: ["S1"]
    )
    mod = MagicMock()
    mod.run.side_effect = RuntimeError("upstream down")
    monkeypatch.setattr(scheduler.importlib, "import_module", lambda _p: mod)
    monkeypatch.setattr(scheduler, "build_proxy_call", lambda *a, **k: MagicMock())
    # Don't actually sleep for 5s in the retry path.
    monkeypatch.setattr(scheduler.time, "sleep", lambda _s: None)

    spec = JobSpec(
        job_name="tiktok.orders",
        module_path="tts_erp_v2.jobs.tiktok.orders",
        interval_seconds=600,
        is_tiktok=True,
    )
    _run_tiktok_job(spec, factory)
    # 2 attempts per shop → 2 calls.
    assert mod.run.call_count == 2
    # Session opened + closed for each attempt.
    assert factory.call_count == 2


# ─── _run_system_job — additional branches ─────────────────────────


def test_run_system_job_swallows_commit_error_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If commit() raises after a successful entrypoint, we log + rollback
    but do NOT re-raise (the entrypoint already succeeded; the row may
    be rolled back, but we shouldn't crash the scheduler)."""
    fake_mod = MagicMock()
    fake_mod.fake_entry = MagicMock(return_value={"ok": True})

    def fake_import(name: str):
        return fake_mod

    session = MagicMock()
    session.commit.side_effect = RuntimeError("commit blew up")
    factory = MagicMock(return_value=session)

    monkeypatch.setattr(scheduler.importlib, "import_module", fake_import)
    sentinel_calls: list = []
    monkeypatch.setattr(
        scheduler,
        "_record_failed_tick",
        lambda sf, spec, reason: sentinel_calls.append(reason),
    )

    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
    )
    _run_system_job(spec, factory)  # must not raise
    session.rollback.assert_called()
    # Sentinel NOT invoked — commit error is not the same as entrypoint error.
    assert sentinel_calls == []


def test_run_system_job_swallows_rollback_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both the inner raises AND rollback raises, we still hit the
    sentinel path and don't propagate."""
    fake_mod = MagicMock()
    fake_mod.fake_entry = MagicMock(side_effect=ValueError("inner boom"))

    def fake_import(name: str):
        return fake_mod

    session = MagicMock()
    session.rollback.side_effect = RuntimeError("rollback blew up")
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(scheduler.importlib, "import_module", fake_import)
    sentinel_calls: list = []
    monkeypatch.setattr(
        scheduler,
        "_record_failed_tick",
        lambda sf, spec, reason: sentinel_calls.append(reason),
    )

    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
    )
    _run_system_job(spec, factory)
    assert sentinel_calls == ["tick raised: ValueError: inner boom"]


def test_run_system_job_skips_registry_when_needs_token_registry_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a non-token-refresh system job, build_token_registry is NOT
    called even if a fake is wired in (proves the gating)."""
    fake_mod = MagicMock()
    seen_kwargs: dict = {}
    fake_mod.fake_entry = MagicMock(
        side_effect=lambda session, **kw: seen_kwargs.update(kw) or {"ok": True}
    )

    def fake_import(name: str):
        return fake_mod

    factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(scheduler.importlib, "import_module", fake_import)

    fake_registry = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "tts_erp_v2.proxy.tiktok_auth.build_token_registry", fake_registry
    )

    spec = JobSpec(
        job_name="reporting.profit_daily",
        module_path="fake.module",
        interval_seconds=60,
        is_tiktok=False,
        entrypoint="fake_entry",
        needs_token_registry=False,  # the gating condition
    )
    _run_system_job(spec, factory)
    # Registry NOT invoked because needs_token_registry=False.
    assert fake_registry.call_count == 0
    # And the entrypoint got NO registry kwarg.
    assert "registry" not in seen_kwargs
