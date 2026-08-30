"""Top-level entrypoint for the tts-erp v2 sync-worker systemd unit.

Subcommands
-----------
``python -m tts_erp_v2.sync_worker.main`` (or ``python -m
tts_erp_v2.sync_worker``, via the ``__main__`` shim):

* (no args)         — daemon mode. Starts :class:`BlockingScheduler`
                       and blocks until SIGTERM/SIGINT.
* ``list``          — print the :data:`JOBS` registry with module
                       path + interval + next-fire time. No DB needed.
* ``run <job_name>``— one-shot run of the named job. Useful for
                       backfill / manual triggers; the legacy
                       ``POST /sync/*`` HTTP routes are gone in v2.

Startup validation
------------------
``TTS_ERP_DB_URL`` and ``TTS_ERP_FERNET_KEY`` are both required. We
fail-fast at boot with an actionable error rather than crashing
mid-tick when the first DB call or credential decryption blows up.

The Fernet key is read from ``TTS_ERP_FERNET_KEY`` (the v2 canonical
name per AGENTS.md §6). If you're bootstrapping from a deployment that
previously only set ``OAUTH_DB_ENCRYPTION_KEY`` (the legacy oauth-
receiver name), mirror it into ``TTS_ERP_FERNET_KEY`` — the ciphertext
in ``integration.credentials`` was encrypted with that key.

Why this file is NOT auto-imported by :mod:`tts_erp_v2.app`
-----------------------------------------------------------
The sync worker is a separate systemd unit (``tts-erp-sync.service``)
running in its own process. Importing :mod:`tts_erp_v2.app` would
kick off the FastAPI startup, doubling resource use and creating
two competing scheduler instances if anyone ever also imports
``scheduler.build_scheduler()`` from the API process. Keep the
two entry points independent.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

from tts_erp_v2.sync_worker.scheduler import JOBS, build_scheduler

log = logging.getLogger("tts_erp_v2.sync_worker.main")


# ─── Environment validation ────────────────────────────────────────


def _require_env() -> None:
    """Fail-fast check for the env vars the worker can't live without.

    Emits a multi-line error to stderr and :func:`sys.exit` ``2`` so
    systemd reports a clear failure (rather than crashing inside
    APScheduler with an obscure traceback).
    """
    missing: list[str] = []
    if not os.environ.get("TTS_ERP_DB_URL", "").strip():
        missing.append("TTS_ERP_DB_URL")
    if not os.environ.get("TTS_ERP_FERNET_KEY", "").strip():
        missing.append("TTS_ERP_FERNET_KEY")
    if missing:
        sys.stderr.write(
            "tts-erp sync-worker cannot start; missing env var(s):\n"
            + "\n".join(f"  - {name}" for name in missing)
            + "\n"
            "Set them in /home/schan/tts-erp/.env; the systemd unit's "
            "EnvironmentFile= forwards them at start.\n"
        )
        sys.exit(2)


def _configure_logging() -> None:
    """Plain INFO-to-stderr logging. systemd captures stderr to journalctl."""
    logging.basicConfig(
        level=os.environ.get("TTS_ERP_SYNC_LOG_LEVEL", "INFO").upper(),
        format=("%(asctime)s %(levelname)-7s %(name)s | %(message)s"),
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stderr,
    )


# ─── Subcommand handlers ───────────────────────────────────────────


def _print_jobs() -> None:
    """``list`` subcommand — print the registry + next-fire preview."""
    # ``build_scheduler`` needs a session_factory, but list-mode never
    # fires any job, so we use a throwaway factory (never invoked).
    sched = build_scheduler(session_factory=_noop_session_factory())
    jobs = sched.get_jobs()
    print(f"{'JOB NAME':<22} {'INTERVAL':>10}  {'MODULE':<48}")
    print("-" * 82)
    for job in jobs:
        spec = JOBS[job.id]
        try:
            secs = int(job.trigger.interval.total_seconds())
        except (TypeError, ValueError):
            secs = 0
        print(f"{job.id:<22} {_fmt_duration(secs):>10}  {spec.module_path}")
    print()
    print("next-fire preview (UTC):")
    for job in jobs:
        # ``next_run_time`` is a property on APScheduler 3.x's Job, but
        # it only materialises after ``.start()`` — use ``getattr`` so
        # ``list`` (which doesn't start the scheduler) doesn't blow up.
        nft = getattr(job, "next_run_time", None)
        print(f"  {job.id:<22}  {nft.isoformat() if nft else '(pending)'}")


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _noop_session_factory():
    """A sessionmaker that, if ever called, raises — used by ``list``."""

    class _Nope:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("list mode does not open DB sessions")

    return _Nope()


def _run_one_job(name: str) -> int:
    """``run <name>`` subcommand — invoke the named job once and exit."""
    if name not in JOBS:
        supported = ", ".join(sorted(JOBS.keys()))
        sys.stderr.write(
            f"unknown job: {name!r}\navailable jobs ({len(JOBS)}): {supported}\n"
        )
        return 2

    # Force a real session factory (this branch DOES need PG).
    from tts_erp_v2.db.base import get_session_factory

    session_factory = get_session_factory()
    spec = JOBS[name]
    if spec.is_tiktok:
        from tts_erp_v2.sync_worker.scheduler import _run_tiktok_job

        _run_tiktok_job(spec, session_factory)
    else:
        from tts_erp_v2.sync_worker.scheduler import _run_token_refresh_job

        _run_token_refresh_job(spec, session_factory)
    return 0


# ─── Daemon ────────────────────────────────────────────────────────


def _run_daemon() -> int:
    """Daemon mode — start BlockingScheduler and block forever."""
    from tts_erp_v2.db.base import get_session_factory

    session_factory = get_session_factory()
    sched = build_scheduler(session_factory)

    def _shutdown(signum, _frame):  # noqa: ANN001 — signal handler shape
        log.info("received signal %d, shutting down", signum)
        try:
            sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001 — boundary
            log.exception("error shutting down scheduler")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info(
        "tts-erp sync-worker starting (%d jobs registered): %s",
        len(sched.get_jobs()),
        ", ".join(sorted(j.id for j in sched.get_jobs())),
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("sync-worker stopped")
    return 0


# ─── Entrypoint ────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher. See module docstring for the subcommand set."""
    _configure_logging()
    args = list(sys.argv[1:] if argv is None else argv)
    subcommand = args[0] if args else "daemon"

    if subcommand == "list":
        # ``list`` doesn't open a DB session, but the scheduler factory
        # still needs an env var to construct. So we DO require env here
        # — better than silently passing a throwaway factory through.
        _require_env()
        _print_jobs()
        return 0

    if subcommand == "run":
        if len(args) < 2:
            sys.stderr.write("usage: sync_worker run <job_name>\n")
            return 2
        _require_env()
        return _run_one_job(args[1])

    if subcommand in ("daemon", "-h", "--help"):
        if subcommand != "daemon":
            print(
                "usage: sync_worker [list | run <job_name>]\n"
                "       (no args → daemon mode)\n"
            )
            return 0
        _require_env()
        return _run_daemon()

    sys.stderr.write(f"unknown subcommand: {subcommand!r}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
