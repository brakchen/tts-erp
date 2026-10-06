"""TDD tests for jobs.token_refresh after the window-mismatch fix.

Production bug pre-2026-10-06:

* The job scanned rows whose ``expires_at`` was within 24h, but it
  forwarded the inner :func:`refresh_if_needed` default of
  ``DEFAULT_REFRESH_SKEW`` (60s). The inner check therefore
  short-circuited every row that wasn't inside the last minute,
  silently skipping the entire 24h window — every tick in the
  production ``integration.sync_jobs`` table shows
  ``refreshed=0, skipped=N``.
* Rows with ``expires_at IS NULL`` were scanned (the legacy
  "NULL = due" assumption) but ``is_expired(None)`` returns False
  ("never expires"), so the refresher was never called — the row
  silently rotted until a 401 surfaced at the proxy layer.
* When everything went wrong, the job still reported
  ``status='succeeded'`` with no per-tick ``sync_issues`` row — ops
  had zero visibility.

This file pins down the fixed contract:

* :func:`_query_due_credentials` scans rows whose ``expires_at`` is
  inside the window **or** whose ``expires_at`` is NULL but
  ``updated_at`` is older than ``STALE_THRESHOLD``.
* :func:`sync_token_refresh` forwards ``skew=window`` for non-NULL
  rows and ``force=True`` for NULL-expiry rows.
* :func:`sync_token_refresh` writes a ``TOKEN_REFRESH_NOOP`` issue
  when the scan picked rows but nothing refreshed, failed, or
  emitted a per-row issue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from tts_erp_v2.db.base import get_engine
from tts_erp_v2.db.models import Credentials, SyncIssue
from tts_erp_v2.jobs.token_refresh import (
    PROVIDERS_DEFAULT,
    REFRESH_WINDOW,
    STALE_THRESHOLD,
    _query_due_credentials,
    sync_token_refresh,
)
from tts_erp_v2.proxy.token_service import upsert_credentials

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_integration]


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture()
def session_factory() -> sessionmaker:
    """Real sessionmaker against the test DB (TTS_ERP_DB_URL_TEST)."""
    engine = get_engine()
    return sessionmaker(bind=engine)


def _seed_credentials(
    session_factory: sessionmaker,
    *,
    external_id: str,
    expires_at: datetime | None,
    created_at: datetime | None = None,
) -> None:
    """Insert a TEST_-prefixed Credentials row.

    The job scans real rows; encryption goes through
    ``upsert_credentials`` so the real Fernet key path is exercised.

    ``created_at`` is optional and back-dated via raw SQL — the
    production code uses ``created_at`` (not ``updated_at``) for
    staleness because :func:`public.fn_touch_updated_at` rewrites
    ``updated_at`` on every UPDATE.
    """
    seed_at = "seed_at_xyz"
    seed_rt = "seed_rt_xyz"
    seed_sc = "seed_cipher_xyz"

    sess: Session = session_factory()
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
        # Optionally back-date created_at to simulate a long-lived row.
        # ``created_at`` is not touched by ``fn_touch_updated_at`` so
        # even an ORM-level update would stick — but we use raw SQL
        # for parity with the future-proof raw approach.
        if created_at is not None:
            from sqlalchemy import text as _text

            sess.execute(
                _text(
                    "UPDATE integration.credentials "
                    "SET created_at = :ts "
                    "WHERE provider = :p AND external_account_id = :eid"
                ),
                {"ts": created_at, "p": "tiktok", "eid": external_id},
            )
            sess.commit()
    finally:
        sess.close()


def _cleanup(session_factory: sessionmaker, *, external_id: str) -> None:
    """Delete the seeded TEST_ row + any sync_issues it produced."""
    sess: Session = session_factory()
    try:
        sess.execute(
            delete(Credentials).where(Credentials.external_account_id == external_id)
        )
        # Clean issues whose external_id starts with the profile prefix
        # (``<provider>:<external_id>``) or '*' (the noop advisory).
        sess.execute(
            delete(SyncIssue).where(
                SyncIssue.issue_type.in_(
                    ("TOKEN_REFRESH_NO_TOKEN", "TOKEN_REFRESH_NOOP", "TOKEN_REFRESH_FAILED")
                )
            )
        )
        sess.commit()
    finally:
        sess.close()


def _make_capture_registry() -> tuple[Any, list[dict]]:
    """Build a registry that captures every ``refresh_if_needed`` call.

    Returns ``(registry, captures)`` where ``captures`` is a list of
    dicts populated in-place. The returned refresher marks
    ``called=True, got_token=True`` and returns a canned payload, so
    the loop counts the row as refreshed.
    """
    captures: list[dict] = []

    def registry(provider: str, external_account_id: str) -> Any:
        def refresher(_p: str, _eid: str) -> dict:
            return {
                "access_token": "rotated_at_xyz",
                "refresh_token": "rotated_rt_xyz",
                "shop_cipher": "rotated_cipher_xyz",
                "expires_at": datetime.now(UTC) + timedelta(hours=2),
            }

        return refresher

    def wrapped(provider: str, external_account_id: str) -> Any:
        # The "captures" wrapper is what ``sync_token_refresh`` calls;
        # it forwards to the inner ``registry`` callable and records
        # ``(provider, external_id)``. The ``kwargs`` of
        # ``refresh_if_needed`` are captured separately by the test
        # via ``monkeypatch``.
        captures.append({"provider": provider, "external_id": external_account_id})
        inner = registry(provider, external_account_id)
        return inner(provider, external_account_id)

    # The job calls ``registry(provider, external_account_id)`` to
    # obtain a per-row refresher. So ``registry`` itself is the
    # outer callable — return it (the inner returns the token dict).
    # To also capture invocations we wrap on top of the registry so
    # that the per-row refresher call (the call inside
    # ``refresh_if_needed``) is recorded too. Simplest: a dict is a
    # callable in Python (no), so we expose a callable class.

    class _Capture:
        def __call__(self, provider: str, external_account_id: str) -> Any:
            captures.append({"provider": provider, "external_id": external_account_id})
            return refresher

        def refresher(_p: str, _eid: str) -> dict:
            return {
                "access_token": "rotated_at_xyz",
                "refresh_token": "rotated_rt_xyz",
                "shop_cipher": "rotated_cipher_xyz",
                "expires_at": datetime.now(UTC) + timedelta(hours=2),
            }

    return _Capture(), captures


# ---------------------------------------------------------------------------
# _query_due_credentials


def test_query_due_credentials_picks_within_window_row(
    session_factory: sessionmaker,
) -> None:
    """A non-NULL expires_at within the 24h window is scanned."""
    external_id = f"TEST_TT_TR_WINDOW_{uuid4().hex[:8]}"
    soon = datetime.now(UTC) + timedelta(hours=12)
    _seed_credentials(session_factory, external_id=external_id, expires_at=soon)
    try:
        sess = session_factory()
        try:
            now = datetime.now(UTC)
            rows = _query_due_credentials(
                sess,
                providers=("tiktok",),
                window=REFRESH_WINDOW,
                stale_threshold=STALE_THRESHOLD,
                now=now,
            )
            ids = [r.external_account_id for r in rows]
            assert external_id in ids
        finally:
            sess.close()
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_query_due_credentials_skips_outside_window_row(
    session_factory: sessionmaker,
) -> None:
    """A non-NULL expires_at more than 24h away is NOT scanned."""
    external_id = f"TEST_TT_TR_FAR_{uuid4().hex[:8]}"
    far = datetime.now(UTC) + timedelta(hours=48)
    _seed_credentials(session_factory, external_id=external_id, expires_at=far)
    try:
        sess = session_factory()
        try:
            rows = _query_due_credentials(
                sess,
                providers=("tiktok",),
                window=REFRESH_WINDOW,
                stale_threshold=STALE_THRESHOLD,
                now=datetime.now(UTC),
            )
            ids = [r.external_account_id for r in rows]
            assert external_id not in ids
        finally:
            sess.close()
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_query_due_credentials_picks_null_expiry_stale_row(
    session_factory: sessionmaker,
) -> None:
    """A NULL expires_at row whose created_at is older than STALE_THRESHOLD is scanned."""
    external_id = f"TEST_TT_TR_NULL_STALE_{uuid4().hex[:8]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        expires_at=None,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    try:
        sess = session_factory()
        try:
            rows = _query_due_credentials(
                sess,
                providers=("tiktok",),
                window=REFRESH_WINDOW,
                stale_threshold=STALE_THRESHOLD,
                now=datetime.now(UTC),
            )
            ids = [r.external_account_id for r in rows]
            assert external_id in ids
        finally:
            sess.close()
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_query_due_credentials_skips_null_expiry_recent_row(
    session_factory: sessionmaker,
) -> None:
    """A NULL expires_at row whose created_at is recent is NOT scanned.

    Why: the legacy behavior scanned every NULL row, which meant a
    freshly-authorized credential (whose upstream hadn't yet returned
    an ``access_token_expire_in``) was force-refreshed minutes after
    authorization — wasted HTTP calls against the upstream. With the
    staleness gate, we wait ``STALE_THRESHOLD`` before assuming the
    NULL is a data-quality problem.
    """
    external_id = f"TEST_TT_TR_NULL_RECENT_{uuid4().hex[:8]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        expires_at=None,
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    try:
        sess = session_factory()
        try:
            rows = _query_due_credentials(
                sess,
                providers=("tiktok",),
                window=REFRESH_WINDOW,
                stale_threshold=STALE_THRESHOLD,
                now=datetime.now(UTC),
            )
            ids = [r.external_account_id for r in rows]
            assert external_id not in ids
        finally:
            sess.close()
    finally:
        _cleanup(session_factory, external_id=external_id)


# ---------------------------------------------------------------------------
# sync_token_refresh kwargs forwarding


def test_sync_token_refresh_forwards_wide_skew_for_non_null_row(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-NULL within-window row is refreshed with ``skew=REFRESH_WINDOW``.

    Patches :func:`refresh_if_needed` to capture kwargs without
    touching the DB. The legacy bug: ``sync_token_refresh`` passed
    the inner default (60s) so ``is_expired`` returned False for
    every row that wasn't inside the last minute, leaving the
    entire 24h window un-refreshed.
    """
    external_id = f"TEST_TT_TR_SKEW_{uuid4().hex[:8]}"
    soon = datetime.now(UTC) + timedelta(hours=12)
    _seed_credentials(session_factory, external_id=external_id, expires_at=soon)
    try:
        captured_kwargs: list[dict] = []

        import tts_erp_v2.proxy.token_service as ts_mod

        original = ts_mod.refresh_if_needed

        def fake_refresh(
            session: Any,
            *,
            provider: str,
            external_account_id: str,
            refresher: Any,
            **kwargs: Any,
        ) -> Any:
            captured_kwargs.append({"provider": provider, **kwargs})
            # Build a fake CredentialsView-ish sentinel (the loop
            # only checks ``view is None``).
            return fake_refresh  # noqa: F841 — non-None sentinel

        # Simpler: return a real CredentialsView by re-using the
        # original, but only after capturing kwargs.
        def spy_refresh(
            session: Any,
            *,
            provider: str,
            external_account_id: str,
            refresher: Any,
            **kwargs: Any,
        ) -> Any:
            captured_kwargs.append(
                {"provider": provider, "external_account_id": external_account_id, **kwargs}
            )
            # Return a non-None sentinel (the original would commit
            # a refresh; we don't want side effects in this test).
            return object()

        monkeypatch.setattr(ts_mod, "refresh_if_needed", spy_refresh)
        # ``sync_token_refresh`` imported the symbol directly.
        import tts_erp_v2.jobs.token_refresh as tr_mod

        monkeypatch.setattr(tr_mod, "refresh_if_needed", spy_refresh)

        sess = session_factory()
        try:
            result = sync_token_refresh(
                sess,
                registry=lambda *_x, **_xx:
                        lambda r: {"access_token": "x", "expires_at": soon},
                window=REFRESH_WINDOW,
                providers=("tiktok",),
            )
        finally:
            sess.close()

        # Find the captured call for this row.
        ours = [k for k in captured_kwargs if k.get("external_account_id") == external_id]
        assert ours, f"refresh_if_needed not called for {external_id}"
        # The skew passed must equal REFRESH_WINDOW — not the 60s default.
        assert ours[0].get("skew") == REFRESH_WINDOW, (
            f"expected skew=REFRESH_WINDOW ({REFRESH_WINDOW}), got {ours[0]}"
        )
        # And must NOT have force=True (this is a non-NULL row).
        assert not ours[0].get("force", False)
        # Sanity: result.scanned includes our row.
        assert result["scanned"] >= 1
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_sync_token_refresh_uses_force_for_null_expiry_stale_row(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NULL-expires stale row is refreshed with ``force=True``.

    Why ``force=True``: ``is_expired(None)`` returns False regardless
    of the skew ("never expires"), so passing a wider skew alone
    would still short-circuit. For NULL-expiry rows we bypass
    ``is_expired`` entirely — the staleness check on ``created_at``
    is the only thing standing between this row and silent rot.
    """
    external_id = f"TEST_TT_TR_NULL_FORCE_{uuid4().hex[:8]}"
    _seed_credentials(
        session_factory,
        external_id=external_id,
        expires_at=None,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    try:
        captured: list[dict] = []

        def spy_refresh(
            session: Any,
            *,
            provider: str,
            external_account_id: str,
            refresher: Any,
            **kwargs: Any,
        ) -> Any:
            captured.append({"external_account_id": external_account_id, **kwargs})
            return object()

        import tts_erp_v2.jobs.token_refresh as tr_mod

        monkeypatch.setattr(tr_mod, "refresh_if_needed", spy_refresh)

        sess = session_factory()
        try:
            result = sync_token_refresh(
                sess,
                registry=lambda *_x, **_xx:
                        lambda r: {"access_token": "x"},
                providers=("tiktok",),
            )
        finally:
            sess.close()

        ours = [k for k in captured if k.get("external_account_id") == external_id]
        assert ours, f"refresh_if_needed not called for {external_id}"
        # Must have been called with force=True (NULL row path).
        assert ours[0].get("force") is True, f"expected force=True, got {ours[0]}"
        # And no skew= forwarded (mutually exclusive path).
        assert "skew" not in ours[0]
        # And the scan picked it up.
        assert result["scanned"] >= 1
    finally:
        _cleanup(session_factory, external_id=external_id)


# ---------------------------------------------------------------------------
# sync_token_refresh anomaly detector (Fix #3)


def test_sync_token_refresh_writes_noop_advisory_when_scan_picks_but_nothing_happens(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the scan picks a row but no refresh / no per-row issue fires, emit ``TOKEN_REFRESH_NOOP``.

    This is the legacy production failure mode: rows picked by the
    24h window, but ``refresh_if_needed`` returned ``view`` without
    calling the refresher (window-mismatch bug). With Fix #1 + #2,
    reaching this branch means the legacy bug still has a path —
    surface it loudly.
    """
    external_id = f"TEST_TT_TR_NOOP_{uuid4().hex[:8]}"
    soon = datetime.now(UTC) + timedelta(hours=12)
    _seed_credentials(session_factory, external_id=external_id, expires_at=soon)
    try:
        # Simulate the legacy bug: refresh_if_needed returns a
        # non-None view but never invokes the refresher. The wrapped
        # instrumentation records ``called=False``.
        def spy_refresh(
            session: Any,
            *,
            provider: str,
            external_account_id: str,
            refresher: Any,
            **kwargs: Any,
        ) -> Any:
            # Note: do NOT call refresher. Return a fake non-None view.
            return object()

        import tts_erp_v2.jobs.token_refresh as tr_mod

        monkeypatch.setattr(tr_mod, "refresh_if_needed", spy_refresh)

        sess = session_factory()
        try:
            result = sync_token_refresh(
                sess,
                registry=lambda *_x, **_xx: lambda _r: {"access_token": "x"},
                providers=("tiktok",),
            )
            # Production scheduler commits after ``sync_token_refresh``
            # returns (``scheduler._run_system_job``); mirror that here
            # so the sync_issues row is visible to the assertion below.
            sess.commit()
        finally:
            sess.close()

        # The anomaly detector fired: issues=1 (the noop advisory).
        assert result["issues"] == 1, f"expected issues=1, got {result}"
        assert result["refreshed"] == 0
        assert result["failed"] == 0

        # And the sync_issues row carries the list of scanned rows.
        sess = session_factory()
        try:
            issues = (
                sess.execute(
                    select(SyncIssue).where(SyncIssue.issue_type == "TOKEN_REFRESH_NOOP")
                )
                .scalars()
                .all()
            )
            assert issues, "expected a TOKEN_REFRESH_NOOP sync_issues row"
            latest = issues[-1]
            assert latest.external_id == "*"
            assert isinstance(latest.details, dict)
            assert "scanned_rows" in latest.details
            ids = [r["external_account_id"] for r in latest.details["scanned_rows"]]
            assert external_id in ids
        finally:
            sess.close()
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_sync_token_refresh_no_noop_advisory_when_refresh_succeeds(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-test: when at least one row refreshes, no noop advisory fires."""
    external_id = f"TEST_TT_TR_OK_{uuid4().hex[:8]}"
    soon = datetime.now(UTC) + timedelta(hours=12)
    _seed_credentials(session_factory, external_id=external_id, expires_at=soon)
    try:
        # The fake refresher returns a usable access_token, so the
        # loop records ``info['got_token']=True`` → counts as refreshed.
        def spy_refresh(
            session: Any,
            *,
            provider: str,
            external_account_id: str,
            refresher: Any,
            **kwargs: Any,
        ) -> Any:
            # Call the wrapped refresher so the instrumentation records
            # ``info['called']=True`` AND ``info['got_token']=True``.
            refresher(provider, external_account_id)
            return object()

        import tts_erp_v2.jobs.token_refresh as tr_mod

        monkeypatch.setattr(tr_mod, "refresh_if_needed", spy_refresh)

        sess = session_factory()
        try:
            # The inner lambda takes the (provider, external_account_id)
            # pair that ``_instrument`` forwards from the wrapped
            # refresher — one positional arg would raise TypeError.
            result = sync_token_refresh(
                sess,
                registry=lambda *_x, **_xx: lambda _p, _eid: {
                    "access_token": "ok"
                },
                providers=("tiktok",),
            )
            sess.commit()
        finally:
            sess.close()

        # refreshed>=1 and issues==0: no noop advisory.
        assert result["refreshed"] >= 1
        assert result["issues"] == 0, f"unexpected issues: {result}"
    finally:
        _cleanup(session_factory, external_id=external_id)


def test_sync_token_refresh_no_noop_advisory_when_scan_empty(
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-test: an empty scan is a healthy no-op — no advisory.

    A fresh install with zero credentials rows is not an anomaly;
    the job should still report ``status='succeeded'`` with
    ``scanned=0``. The noop detector must only fire when the scan
    picked rows.
    """
    def spy_refresh(
        session: Any,
        *,
        provider: str,
        external_account_id: str,
        refresher: Any,
        **kwargs: Any,
    ) -> Any:
        raise AssertionError("refresh_if_needed should not be called on empty scan")

    import tts_erp_v2.jobs.token_refresh as tr_mod

    monkeypatch.setattr(tr_mod, "refresh_if_needed", spy_refresh)

    # Use a provider that has no rows in the test DB.
    sess = session_factory()
    try:
        result = sync_token_refresh(
            sess,
            registry=lambda *_x, **_xx: lambda _r: {"access_token": "x"},
            providers=("not_a_real_provider",),
        )
    finally:
        sess.close()

    assert result["scanned"] == 0
    assert result["refreshed"] == 0
    assert result["issues"] == 0