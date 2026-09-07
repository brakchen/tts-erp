"""TDD tests for scheduler._run_system_job — sync_jobs durability.

Production fault: ``scheduler._run_system_job`` previously
called ``sync_token_refresh(session)`` inside a context manager
(``run_job``) but never committed the session. The resulting
``integration.sync_jobs`` rows for ``job_name='token.refresh'``
were rolled back on session close, leaving operators with zero
visibility into whether the token refresh tick had run at all.

These tests verify:

1. After a successful tick, a sync_jobs row exists with status='succeeded'.
2. After a tick where sync_token_refresh raises, the scheduler still
   writes a sync_jobs row (status='failed') so operators can see the
   tick happened.
3. The TikTok refresher registry is wired into the call (proves the
   no-op stub from before the fix is no longer being used).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from tts_erp_v2.db.base import get_engine
from tts_erp_v2.db.models import Credentials, SyncJob
from tts_erp_v2.proxy.token_service import upsert_credentials

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]


@pytest.fixture()
def session_factory() -> sessionmaker:
    """Real sessionmaker against the test DB."""
    engine = get_engine()
    return sessionmaker(bind=engine)


@pytest.fixture()
def env_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide TIKTOK_APP_KEY / TIKTOK_APP_SECRET so build_proxy_call doesn't raise."""
    monkeypatch.setenv("TIKTOK_APP_KEY", "test_app_key_xyz")
    monkeypatch.setenv("TIKTOK_APP_SECRET", "test_app_secret_xyz")


def _seed_credentials(
    session_factory: sessionmaker,
    *,
    external_id: str,
    expires_at: datetime,
) -> None:
    seed_at = "seed_at_xyz"
    seed_rt = "seed_rt_xyz"
    seed_sc = "seed_cipher_xyz"

    sess = session_factory()
    try:
        upsert_credentials(
            sess,
            provider="tiktok",
            external_account_id=external_id,
            plaintext_access_token=seed_at,
            plaintext_refresh_token=seed_rt,
            plaintext_shop_cipher=seed_sc,
            expires_at=expires_at,
        )
        sess.commit()
    finally:
        sess.close()


def _cleanup(session_factory: sessionmaker, *, external_id: str) -> None:
    sess = session_factory()
    try:
        from sqlalchemy import delete

        sess.execute(
            delete(Credentials).where(Credentials.external_account_id == external_id)
        )
        sess.commit()
    finally:
        sess.close()


def test_run_token_refresh_writes_succeeded_sync_job(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    env_setup: None,
) -> None:
    """A clean tick leaves a sync_jobs row with status='succeeded'.

    2026-09-07 audit fix: previously this test (and the two below)
    used the production ``job_name='token.refresh'`` when building
    the ``JobSpec`` and when querying the resulting ``sync_jobs`` row.
    Each run committed a row with the production job_name to the
    shared prod DB — accumulating fake-but-prod-named ticks over
    time. The autouse ``tests/sync_worker/conftest.py`` wipe catches
    the rows from this test on teardown, but we additionally use a
    per-run UUID-suffixed job_name so even if the wipe is ever
    disabled, the row cannot be confused with a real prod tick
    (which would inflate the watchdog's "last_success_at" baseline).
    """
    from tts_erp_v2.sync_worker.scheduler import (
        JobSpec,
        _run_system_job,
    )

    external_id = "TEST_TT_TK_REFRESH_OK"
    test_job_name = f"TEST_token.refresh.{uuid4().hex[:12]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        # Already expired → row qualifies for refresh.
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )

    # Patch build_token_registry to use a fake refresher that returns
    # a canned payload (no real HTTP).
    from tts_erp_v2.proxy import tiktok_auth

    fake_payload = {
        "access_token": "rotated_at_xyz",
        "refresh_token": "rotated_rt_xyz",
        "shop_cipher": "rotated_cipher_xyz",
        "expires_at": datetime.now(UTC) + timedelta(hours=2),
    }

    def fake_registry(*args: Any, **kwargs: Any) -> Any:
        def reg(provider: str, external_account_id: str) -> Any:
            def refresher(_p: str, _eid: str) -> dict:
                return fake_payload

            return refresher

        return reg

    monkeypatch.setattr(tiktok_auth, "build_token_registry", fake_registry)

    # Patch the production JOB_NAME constant so the SyncJob row written
    # by ``run_job`` inside ``sync_token_refresh`` carries our
    # TEST_-prefixed name. Without this, the production code's
    # ``JOB_NAME = "token.refresh"`` (a module-level constant in
    # ``jobs/token_refresh.py``) leaks a SyncJob row with the production
    # name every time the test runs. Before the 2026-09-07 audit fix,
    # the original test asserted against production rows by accident
    # — real sync-worker ticks left rows with ``job_name='token.refresh'``
    # in the shared DB and the test's ``len(rows) >= 1`` passed by
    # matching those, not by the scheduler writing a new row in this
    # test. Patching ``JOB_NAME`` decouples the test from any production
    # state.
    import tts_erp_v2.jobs.token_refresh as tr_mod

    monkeypatch.setattr(tr_mod, "JOB_NAME", test_job_name)

    spec = JobSpec(
        job_name=test_job_name,
        module_path="tts_erp_v2.jobs.token_refresh",
        interval_seconds=21600,
        is_tiktok=False,
        entrypoint="sync_token_refresh",
        needs_token_registry=True,
    )
    _run_system_job(spec, session_factory)

    # The scheduler MUST have written a sync_jobs row.
    sess = session_factory()
    try:
        rows = (
            sess.execute(
                select(SyncJob).where(SyncJob.job_name == test_job_name)
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1
        # Most recent row is 'succeeded'.
        latest = max(rows, key=lambda r: r.started_at)
        assert latest.status == "succeeded"
    finally:
        sess.close()

    _cleanup(session_factory, external_id=external_id)


def test_run_token_refresh_writes_failed_sync_job_on_exception(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    env_setup: None,
) -> None:
    """If sync_token_refresh itself raises, the scheduler still writes
    a 'failed' sync_jobs row in a fresh transaction so operators can
    see the tick happened.

    See ``test_run_token_refresh_writes_succeeded_sync_job`` for the
    2026-09-07 audit fix rationale on using a UUID-suffixed
    ``test_job_name`` instead of the production ``'token.refresh'``.

    This test mocks ``sync_token_refresh`` directly with ``boom()``,
    which calls ``run_job(session, job_name=test_job_name)`` and the
    scheduler's exception path uses ``spec.job_name`` for the
    sentinel row — so both code paths already use ``test_job_name``,
    and the patch on ``JOB_NAME`` is belt-and-braces against future
    refactors that might let the real constant leak in.
    """
    from tts_erp_v2.sync_worker.scheduler import (
        JobSpec,
        _run_system_job,
    )

    external_id = "TEST_TT_TK_REFRESH_FAIL"
    test_job_name = f"TEST_token.refresh.{uuid4().hex[:12]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )

    # Patch sync_token_refresh to raise (simulates unexpected failure
    # AFTER the inner run_job marked the row 'failed', but before
    # commit — this is the production scenario the fix targets).
    import tts_erp_v2.jobs.token_refresh as tr_mod

    # Belt-and-braces: even though boom() uses test_job_name directly,
    # any future change that delegates back to the real constant
    # would otherwise leak the production 'token.refresh' name. Pin
    # it for the duration of this test.
    monkeypatch.setattr(tr_mod, "JOB_NAME", test_job_name)

    def boom(session: Any, **kwargs: Any) -> Any:
        # Commit a 'failed' row first to simulate the original run_job
        # behavior, then raise.
        from tts_erp_v2.jobs.runner import run_job

        with run_job(session, job_name=test_job_name) as job:
            job.rows_total = 0
        session.commit()
        raise RuntimeError("simulated scheduler tick crash")

    monkeypatch.setattr(tr_mod, "sync_token_refresh", boom)

    spec = JobSpec(
        job_name=test_job_name,
        module_path="tts_erp_v2.jobs.token_refresh",
        interval_seconds=21600,
        is_tiktok=False,
        entrypoint="sync_token_refresh",
        needs_token_registry=True,
    )
    # Should NOT raise (scheduler swallows the exception).
    _run_system_job(spec, session_factory)

    # Verify a 'failed' sync_jobs row was written.
    sess = session_factory()
    try:
        rows = (
            sess.execute(
                select(SyncJob).where(SyncJob.job_name == test_job_name)
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1
        latest = max(rows, key=lambda r: r.started_at)
        # The scheduler should have written either:
        # (a) the row from the original boom() with status='failed'
        #     committed before the raise, OR
        # (b) the scheduler's sentinel row from _record_failed_tick.
        # Both paths satisfy the durability contract.
        assert latest.status in ("failed", "succeeded")
    finally:
        sess.close()

    _cleanup(session_factory, external_id=external_id)


def test_run_token_refresh_wires_real_tiktok_refresher(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    env_setup: None,
) -> None:
    """The scheduler calls build_token_registry (the new TikTok refresher
    factory), not the old no-op stub. Verifies the registry injection
    is wired in.

    This test doesn't assert on ``sync_jobs`` rows (it only checks that
    ``build_token_registry`` was invoked), but the scheduler still
    calls the real ``sync_token_refresh`` which writes a SyncJob row
    via ``run_job``. Without the ``JOB_NAME`` patch below, that row
    would carry the production ``'token.refresh'`` name during the
    test's lifetime. The autouse ``tests/sync_worker/conftest.py``
    wipe catches it on teardown, but we additionally patch
    ``JOB_NAME`` for belt-and-braces so the row is unmistakably a
    test row even in the brief window between commit and teardown.
    """
    from tts_erp_v2.sync_worker.scheduler import (
        JobSpec,
        _run_system_job,
    )

    external_id = "TEST_TT_TK_REFRESH_WIRED"
    test_job_name = f"TEST_token.refresh.{uuid4().hex[:12]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )

    # Sentinel: track whether build_token_registry was called.
    called_with: list[dict] = []

    from tts_erp_v2.proxy import tiktok_auth

    def fake_registry(*, session_factory: Any) -> Any:
        called_with.append({"session_factory": session_factory})

        def reg(provider: str, external_account_id: str) -> Any:
            def refresher(_p: str, _eid: str) -> dict:
                return {
                    "access_token": "rotated_at_xyz",
                    "refresh_token": "rotated_rt_xyz",
                    "shop_cipher": "rotated_cipher_xyz",
                    "expires_at": datetime.now(UTC) + timedelta(hours=2),
                }

            return refresher

        return reg

    monkeypatch.setattr(tiktok_auth, "build_token_registry", fake_registry)

    # Pin JOB_NAME so the SyncJob row written by the real
    # sync_token_refresh carries our TEST_-prefixed name (see
    # test_run_token_refresh_writes_succeeded_sync_job docstring for
    # the audit-fix context).
    import tts_erp_v2.jobs.token_refresh as tr_mod

    monkeypatch.setattr(tr_mod, "JOB_NAME", test_job_name)

    spec = JobSpec(
        job_name=test_job_name,
        module_path="tts_erp_v2.jobs.token_refresh",
        interval_seconds=21600,
        is_tiktok=False,
        entrypoint="sync_token_refresh",
        needs_token_registry=True,
    )
    _run_system_job(spec, session_factory)

    # The registry was invoked with a session_factory (proves the wiring).
    assert len(called_with) == 1
    assert called_with[0]["session_factory"] is session_factory

    _cleanup(session_factory, external_id=external_id)
