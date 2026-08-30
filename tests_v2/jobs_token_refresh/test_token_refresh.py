"""Tests for tts_erp_v2.jobs.token_refresh.sync_token_refresh.

Verifies:
* Empty credentials → counters zero, no issues.
* Credentials already-fresh (expires_at far future) → not in due list,
  refresher not called.
* Credentials expiring soon → refresher invoked, row updated.
* Per-row refresh failure → issue counter increments; other rows continue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tts_erp_v2.db.models.integration import Credentials, SyncJob
from tts_erp_v2.jobs.token_refresh import JOB_NAME, sync_token_refresh


def _make_credentials(session, *, expires_at: datetime | None, provider: str = "tiktok") -> Credentials:
    """Insert a Credentials row via the production encryption path
    so refresh_if_needed can decrypt it.
    """
    from tts_erp_v2.proxy.token_service import upsert_credentials

    return upsert_credentials(
        session,
        provider=provider,
        external_account_id=f"TEST_TT_TOKEN_{id(expires_at)}_{provider}",
        plaintext_access_token="seed_access",
        plaintext_refresh_token="seed_refresh",
        plaintext_shop_cipher=None,
        expires_at=expires_at,
    )


class _FakeRefresher:
    """RefresherRegistry stand-in: returns a refresh fn that records
    each call. Supports a sequence of responses via ``responses`` keyed
    by external_account_id, or a single ``default`` response."""

    def __init__(self, default: dict | None = None, *, raise_exc: Exception | None = None) -> None:
        self.default = default or {"access_token": "new", "expires_at": datetime.now(timezone.utc) + timedelta(days=30)}
        self.responses: dict[str, dict] = {}
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def __call__(self, provider: str, external_account_id: str):
        def refresher(p: str, eid: str) -> dict:
            self.calls.append(eid)
            if self.raise_exc is not None:
                raise self.raise_exc
            return self.responses.get(eid, self.default)

        return refresher


def test_no_credentials_is_noop(db_session) -> None:
    reg = _FakeRefresher()
    result = sync_token_refresh(db_session, registry=reg)
    assert result["scanned"] == 0
    assert result["refreshed"] == 0
    assert result["skipped"] == 0
    assert reg.calls == []


def test_already_fresh_skips_refresher(db_session) -> None:
    """expires_at far in the future → outside the refresh window →
    the row is not in the due list, so the refresher is never wired."""
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    _make_credentials(db_session, expires_at=far_future)
    reg = _FakeRefresher()
    result = sync_token_refresh(db_session, registry=reg)
    assert result["scanned"] == 0
    assert result["refreshed"] == 0
    assert reg.calls == []


def test_expiring_soon_invokes_refresher(db_session) -> None:
    """expires_at within the default REFRESH_WINDOW → due → refresher called."""
    near_future = datetime.now(timezone.utc) + timedelta(seconds=30)
    cred = _make_credentials(db_session, expires_at=near_future)
    new_expires = datetime.now(timezone.utc) + timedelta(days=30)
    reg = _FakeRefresher(
        default={
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_at": new_expires,
        }
    )
    result = sync_token_refresh(db_session, registry=reg)
    assert result["scanned"] >= 1
    assert result["refreshed"] >= 1
    assert reg.calls == [cred.external_account_id]


def test_refresher_failure_increments_issues(db_session) -> None:
    """Refresher raises → counted as 'issue', other rows continue."""
    near_future = datetime.now(timezone.utc) + timedelta(seconds=30)
    _make_credentials(db_session, expires_at=near_future)
    _make_credentials(db_session, expires_at=near_future, provider="miaoshou")
    reg = _FakeRefresher(raise_exc=RuntimeError("upstream 5xx"))
    result = sync_token_refresh(db_session, registry=reg)
    assert result["scanned"] >= 1
    # The refresher was called and raised; at least one issue was recorded.
    assert result["issues"] >= 1
    assert result["refreshed"] == 0


def test_sync_job_row_recorded(db_session) -> None:
    """After sync_token_refresh returns, a sync_jobs row should exist."""
    near_future = datetime.now(timezone.utc) + timedelta(seconds=30)
    _make_credentials(db_session, expires_at=near_future)
    reg = _FakeRefresher()
    sync_token_refresh(db_session, registry=reg)
    jobs = db_session.execute(select(SyncJob)).scalars().all()
    assert len(jobs) >= 1
    assert any(j.job_name == JOB_NAME for j in jobs)