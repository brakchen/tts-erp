"""Sync job: token_refresh (1d cadence).

Refreshes OAuth credentials whose ``expires_at`` is within the
configured skew window. Delegates to
:func:`tts_erp_v2.proxy.token_service.refresh_if_needed` so the
encryption / write paths are shared with the API layer (no second
implementation).

Behaviour
---------
* Read every ``integration.credentials`` row whose ``provider`` is in
  ``PROVIDERS`` (default: ``{"tiktok", "miaoshou"}``) and whose
  ``expires_at`` is within ``REFRESH_WINDOW`` of now.
* For each row, call :func:`refresh_if_needed` with a per-provider
  refresher callable.
* Successful refreshes + already-fresh rows are NOT counted as
  ``rows_inserted``; instead ``extra.refreshed`` / ``extra.skipped``
  / ``extra.failed`` break the result down for the SyncJob row.

Refresher resolution
--------------------
The refresher for each provider is looked up from a registry passed
by the caller (typically ``sync_worker.scheduler``). The default
registry built here is a *no-op* — it logs a warning and returns the
existing ciphertext unchanged. Tests inject a ``_FakeRefresher``
that records calls and returns canned payloads.

Failure mode contract
---------------------
* No credentials rows → job finishes with rows_total=0, status='succeeded'.
* Refresher returns a payload without ``access_token`` → row is
  left untouched (stale), counted as ``extra.skipped``.
* Per-row refresh failure (network / 5xx) → recorded as
  ``integration.sync_issues`` row, job continues.
* Unexpected exception → ``run_job`` marks SyncJob ``failed``,
  re-raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.integration import Credentials
from tts_erp_v2.jobs.runner import record_sync_issue, run_job
from tts_erp_v2.proxy.token_service import (
    DEFAULT_REFRESH_SKEW,
    RefresherFn,
    refresh_if_needed,
)

log = logging.getLogger("tts_erp_v2.jobs.token_refresh")

JOB_NAME = "token.refresh"
PROVIDERS_DEFAULT: tuple[str, ...] = ("tiktok", "miaoshou")
#: Refresh window — refresh any row whose expires_at falls within this
#: much of "now". Default 24h covers the daily cadence; the proxy
#: layer's ``DEFAULT_REFRESH_SKEW`` (60s) only kicks in once we are
#: actually inside the per-call freshness window.
REFRESH_WINDOW = DEFAULT_REFRESH_SKEW + timedelta(hours=24)


class RefresherRegistry(Protocol):
    """Protocol the scheduler wires in; tests pass a dict-like object."""

    def __call__(self, provider: str, external_account_id: str) -> RefresherFn: ...


def _default_registry() -> RefresherRegistry:
    """Build a no-op registry that returns a refresher which leaves
    tokens unchanged. Useful for local dry-runs / first-time boot when
    the upstream refresh endpoint isn't wired yet.
    """

    class _NoOp:
        def __call__(self, provider: str, external_account_id: str) -> RefresherFn:
            def _refresher(p: str, eid: str) -> dict[str, Any]:
                log.warning(
                    "token_refresh: no refresher registered for provider=%s "
                    "external_account_id=%s; leaving token unchanged",
                    provider, external_account_id,
                )
                return {"access_token": ""}  # empty → refresh_if_needed skips
            return _refresher

    return _NoOp()


def _query_due_credentials(
    session: Session,
    *,
    providers: tuple[str, ...],
    window: timedelta,
    now: datetime,
) -> list[Credentials]:
    """Return credentials rows whose ``expires_at`` is within ``window`` of ``now``.

    Rows with ``expires_at IS NULL`` are returned too — they are
    treated as "due" (conservative: we don't know they're fresh).
    """
    threshold = now + window
    rows = (
        session.execute(
            select(Credentials)
            .where(Credentials.provider.in_(providers))
            .where(
                (Credentials.expires_at.is_(None))
                | (Credentials.expires_at <= threshold)
            )
            .order_by(Credentials.provider, Credentials.external_account_id)
        )
        .scalars()
        .all()
    )
    return list(rows)


def sync_token_refresh(
    session: Session,
    *,
    registry: RefresherRegistry | None = None,
    providers: tuple[str, ...] = PROVIDERS_DEFAULT,
    window: timedelta = REFRESH_WINDOW,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh all due credentials.

    Args:
        session: SQLAlchemy session (caller commits).
        registry: callable(provider, external_account_id) → refresher.
            Defaults to the no-op registry.
        providers: which provider labels to scan.
        window: how far ahead of ``expires_at`` to consider "due".
        now: override "now" (for tests).

    Returns:
        Dict with ``scanned`` / ``refreshed`` / ``skipped`` /
        ``failed`` / ``issues`` counters.
    """
    if registry is None:
        registry = _default_registry()

    if now is None:
        now = datetime.now(timezone.utc)

    with run_job(session, job_name=JOB_NAME) as job:
        rows = _query_due_credentials(
            session, providers=providers, window=window, now=now
        )

        refreshed = 0
        skipped = 0
        failed = 0
        issues = 0

        for row in rows:
            inner_refresher = registry(row.provider, row.external_account_id)
            # Wrap the inner refresher so we can observe whether
            # refresh_if_needed actually called it (i.e. the row was
            # expired) AND whether it produced a usable access_token.
            # refresh_if_needed is opaque about this distinction — it
            # silently returns the stale CredentialsView when the
            # refresher returns ``{"access_token": ""}``, which we
            # need to count as ``skipped`` rather than ``refreshed``.
            wrapped, info = _instrument(inner_refresher)
            try:
                view = refresh_if_needed(
                    session,
                    provider=row.provider,
                    external_account_id=row.external_account_id,
                    refresher=wrapped,
                )
            except Exception as e:  # noqa: BLE001
                import traceback
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="TOKEN_REFRESH_FAILED",
                    external_id=f"{row.provider}:{row.external_account_id}",
                    details={"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()},
                )
                issues += 1
                failed += 1
                continue

            if view is None:
                # Row disappeared between SELECT and refresh — skip.
                skipped += 1
                continue

            # Classification (driven by the instrumentation above).
            if not info["called"]:
                # Row was still fresh — refresh_if_needed short-circuited.
                skipped += 1
                continue
            if not info["got_token"]:
                # Refresher was called but returned no usable token.
                # refresh_if_needed left the stale row; count as skipped
                # and write an advisory issue so ops can see the pattern.
                record_sync_issue(
                    session,
                    job_name=JOB_NAME,
                    issue_type="TOKEN_REFRESH_NO_TOKEN",
                    external_id=f"{row.provider}:{row.external_account_id}",
                    details={"reason": "refresher returned empty access_token"},
                )
                issues += 1
                skipped += 1
                continue

            refreshed += 1

        import logging
        logging.getLogger("DEBUG").warning("DEBUG token_refresh refreshed=%d skipped=%d failed=%d issues=%d rows=%d", refreshed, skipped, failed, issues, len(rows))

        job.rows_total = len(rows)
        job.rows_inserted = refreshed
        job.rows_failed = failed
        job.extra = {
            "scanned": len(rows),
            "refreshed": refreshed,
            "skipped": skipped,
            "failed": failed,
            "issues": issues,
            "providers": list(providers),
            "window_seconds": int(window.total_seconds()),
            "finished_at_iso": now.isoformat(),
        }
        return {
            "scanned": len(rows),
            "refreshed": refreshed,
            "skipped": skipped,
            "failed": failed,
            "issues": issues,
        }


def _instrument(
    refresher: RefresherFn,
) -> tuple[RefresherFn, dict[str, bool]]:
    """Wrap a refresher so we can observe whether it was called and
    whether it produced an ``access_token``.

    Returns ``(wrapped, info)`` where ``info`` is a single-key dict
    mutated in place: ``{'called': bool, 'got_token': bool}``.
    """
    info: dict[str, bool] = {"called": False, "got_token": False}

    def wrapped(provider: str, external_account_id: str) -> dict[str, Any]:
        info["called"] = True
        result = refresher(provider, external_account_id)
        if isinstance(result, dict) and result.get("access_token"):
            info["got_token"] = True
        return result

    return wrapped, info


__all__ = [
    "JOB_NAME",
    "PROVIDERS_DEFAULT",
    "REFRESH_WINDOW",
    "RefresherRegistry",
    "sync_token_refresh",
]
