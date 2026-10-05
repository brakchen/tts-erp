"""Sync job: token_refresh (1d cadence).

Refreshes OAuth credentials whose ``expires_at`` is within the
configured skew window. Delegates to
:func:`tts_erp_v2.proxy.token_service.refresh_if_needed` so the
encryption / write paths are shared with the API layer (no second
implementation).

Behaviour
---------
* Read every ``integration.credentials`` row whose ``provider`` is in
  ``PROVIDERS`` (default: ``{"tiktok", "miaoshou"}``) and either:
    - has a non-NULL ``expires_at`` within ``REFRESH_WINDOW`` of now, or
    - has a NULL ``expires_at`` AND ``created_at`` older than
      ``STALE_THRESHOLD`` (default 7 days) — the upstream never told
      us when this token expires, so we use **creation time** as a
      proxy. ``updated_at`` is unsuitable here because the
      ``public.fn_touch_updated_at`` trigger rewrites it on every
      UPDATE, so a row that hasn't been refreshed in months looks like
      it was touched today.
* For each row, call :func:`refresh_if_needed` with the per-provider
  refresher callable. The same ``REFRESH_WINDOW`` is forwarded as the
  ``skew=`` argument so the inner check agrees with the scan
  (previously only the 60-second default was used, so 24h-window
  matches short-circuited and were silently skipped).
* NULL-expiry rows are refreshed with ``force=True`` because
  :func:`tts_erp_v2.proxy.token_service.is_expired` returns ``False``
  for NULL (treated as "never expires") — a wider skew alone would still
  short-circuit, so the staleness case needs an explicit force.
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
  left untouched (stale), counted as ``extra.skipped`` and a
  ``TOKEN_REFRESH_NO_TOKEN`` issue is written.
* Per-row refresh failure (network / 5xx) → recorded as
  ``integration.sync_issues`` row, job continues.
* Scan picked rows but none were refreshed AND none failed AND no
  issues fired → the system is silently no-op'ing, which is the
  production failure mode that motivated this rewrite. Emit a single
  ``TOKEN_REFRESH_NOOP`` advisory issue per tick with the list of
  scanned rows so ops can see it.
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
#: much of "now". The proxy layer's :data:`DEFAULT_REFRESH_SKEW`
#: (60s) is added on top so the inner ``refresh_if_needed`` is asked
#: with the same window (not the much tighter 60-second default).
REFRESH_WINDOW = DEFAULT_REFRESH_SKEW + timedelta(hours=24)
#: Treat rows with a NULL ``expires_at`` as stale once ``created_at``
#: is older than this. The upstream response omitted the expiry
#: (data-quality issue), so we fall back to creation time as a
#: proxy: refreshing a 7-day-old credential with no recorded expiry
#: is cheap insurance against silent expiry.
#:
#: We use ``created_at`` (not ``updated_at``) because
#: :func:`public.fn_touch_updated_at` rewrites ``updated_at`` on every
#: UPDATE — including every refresh, every operator write, every probe.
#: Without this choice the staleness gate would never trip.
STALE_THRESHOLD = timedelta(days=7)


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
    stale_threshold: timedelta,
    now: datetime,
) -> list[Credentials]:
    """Return credentials rows whose ``expires_at`` is within ``window`` of ``now``,
    OR whose ``expires_at`` is NULL but whose ``created_at`` is older than
    ``stale_threshold``.

    The NULL branch is what added a *data-quality* check that the
    legacy implementation was missing: a NULL expiry might mean the
    upstream forgot to include ``access_token_expire_in`` in the
    response, which leaves us blind to when the token actually
    expires. We assume such rows are stale after ``stale_threshold``
    and force a refresh so we re-establish a known-good state.

    ``created_at`` (not ``updated_at``) is the staleness signal:
    :func:`public.fn_touch_updated_at` rewrites ``updated_at`` on
    every UPDATE, so a never-refreshed row still appears "fresh".
    """
    threshold = now + window
    stale_before = now - stale_threshold
    rows = (
        session.execute(
            select(Credentials)
            .where(Credentials.provider.in_(providers))
            .where(
                # Non-NULL expires_at inside the refresh window.
                (
                    (Credentials.expires_at.is_not(None))
                    & (Credentials.expires_at <= threshold)
                )
                # OR NULL expires_at but row was created long enough
                # ago to suspect the missing expiry is a data-quality
                # problem — force a refresh to re-establish a known-
                # good state.
                | (
                    (Credentials.expires_at.is_(None))
                    & (Credentials.created_at <= stale_before)
                )
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
    stale_threshold: timedelta = STALE_THRESHOLD,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh all due credentials.

    Args:
        session: SQLAlchemy session (caller commits).
        registry: callable(provider, external_account_id) → refresher.
            Defaults to the no-op registry.
        providers: which provider labels to scan.
        window: how far ahead of ``expires_at`` to consider "due".
        stale_threshold: a row with ``expires_at IS NULL`` is
            treated as due once ``updated_at`` is older than this.
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
            session,
            providers=providers,
            window=window,
            stale_threshold=stale_threshold,
            now=now,
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
            # Forward the same window the scan used: ``refresh_if_needed``
            # defaults to ``DEFAULT_REFRESH_SKEW`` (60s), which would
            # short-circuit every row that wasn't inside the last
            # minute — silently skipping everything inside the 24h
            # window. For NULL-expiry rows the inner check returns
            # ``False`` regardless of skew (NULL → never expires), so
            # we must force the refresher instead.
            refresh_kwargs: dict[str, Any] = {"skew": window}
            if row.expires_at is None:
                refresh_kwargs = {"force": True}
            try:
                view = refresh_if_needed(
                    session,
                    provider=row.provider,
                    external_account_id=row.external_account_id,
                    refresher=wrapped,
                    **refresh_kwargs,
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
                # This branch should be unreachable after Fix #1:
                # with ``skew=window`` and a row that satisfied
                # ``expires_at <= now + window``, ``is_expired`` must
                # return True and trigger a refresher call. Reach it
                # only if the row's expires_at was changed between
                # SELECT and the refresh call. Count as skipped for
                # stability; the row is still treated as fresh.
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

        # Anomaly detector: scan picked rows but NOTHING happened
        # (no refresh succeeded, no failure, no per-row issue). After
        # Fix #1 + Fix #2 this branch should be unreachable in
        # production. If it fires, the most likely causes are:
        #   * the registry resolved to a no-op for every provider AND
        #     the rows are inside the 24h window — the wrapped
        #     refresher fires and returns ``{"access_token": ""}``,
        #     but that already routes through the
        #     ``TOKEN_REFRESH_NO_TOKEN`` issue path above (issues>0,
        #     so this branch is skipped). Therefore reaching here
        #     means each row's ``is_expired`` returned False despite the
        #     scan saying due — the legacy bug. Surface it loudly.
        if (
            len(rows) > 0
            and refreshed == 0
            and failed == 0
            and issues == 0
        ):
            details_payload = {
                "scanned_rows": [
                    {
                        "provider": r.provider,
                        "external_account_id": r.external_account_id,
                        "expires_at": (
                            r.expires_at.isoformat()
                            if r.expires_at is not None
                            else None
                        ),
                        "created_at": (
                            r.created_at.isoformat()
                            if r.created_at is not None
                            else None
                        ),
                    }
                    for r in rows
                ],
                "window_seconds": int(window.total_seconds()),
                "stale_threshold_seconds": int(stale_threshold.total_seconds()),
            }
            record_sync_issue(
                session,
                job_name=JOB_NAME,
                issue_type="TOKEN_REFRESH_NOOP",
                external_id="*",
                details=details_payload,
            )
            issues += 1

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
            "stale_threshold_seconds": int(stale_threshold.total_seconds()),
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
    "STALE_THRESHOLD",
    "RefresherRegistry",
    "sync_token_refresh",
]
