"""TDD tests for ``tts_erp_v2.sync_worker.main`` (the systemd entrypoint).

The sync-worker has three faces:

* ``daemon`` (no args) — start :class:`BlockingScheduler`, install
  SIGTERM/SIGINT handlers, block forever.
* ``list`` — print the :data:`JOBS` registry without touching the DB.
* ``run <job_name>`` — invoke one job once and exit.

Env validation (``_require_env``) is fail-fast: missing
``TTS_ERP_DB_URL`` / ``TTS_ERP_FERNET_KEY`` → exit code 2 with a clear
stderr message.

These tests deliberately avoid starting the scheduler — they patch
``signal.signal`` / ``session_factory`` / ``scheduler.start`` so we
exercise the wiring without holding the test thread hostage.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from tts_erp_v2.sync_worker import main as main_mod
from tts_erp_v2.sync_worker import scheduler
from tts_erp_v2.sync_worker.main import main

pytestmark = [pytest.mark.domain_sync]


# ─── _require_env ──────────────────────────────────────────────────


def test_require_env_exits_when_db_url_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No DB URL → exit 2, stderr names the missing var."""
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "x")
    with pytest.raises(SystemExit) as exc:
        main_mod._require_env()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "TTS_ERP_DB_URL" in err
    assert "TTS_ERP_FERNET_KEY" not in err  # only the missing one


def test_require_env_exits_when_fernet_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No Fernet key → exit 2 with the right var listed."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.delenv("TTS_ERP_FERNET_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main_mod._require_env()
    assert exc.value.code == 2
    assert "TTS_ERP_FERNET_KEY" in capsys.readouterr().err


def test_require_env_exits_when_both_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both missing → both listed."""
    monkeypatch.delenv("TTS_ERP_DB_URL", raising=False)
    monkeypatch.delenv("TTS_ERP_FERNET_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main_mod._require_env()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "TTS_ERP_DB_URL" in err
    assert "TTS_ERP_FERNET_KEY" in err


def test_require_env_returns_silently_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both env vars present → no exception, no exit."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")
    # Should NOT raise.
    main_mod._require_env()


def test_require_env_treats_whitespace_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only values are treated as missing (defensive)."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "   ")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")
    with pytest.raises(SystemExit):
        main_mod._require_env()


# ─── _configure_logging ────────────────────────────────────────────


def test_configure_logging_sets_info_level_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env override → INFO."""
    monkeypatch.delenv("TTS_ERP_SYNC_LOG_LEVEL", raising=False)
    main_mod._configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_respects_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTS_ERP_SYNC_LOG_LEVEL=DEBUG → root logger at DEBUG."""
    monkeypatch.setenv("TTS_ERP_SYNC_LOG_LEVEL", "DEBUG")
    main_mod._configure_logging()
    assert logging.getLogger().level == logging.DEBUG


# ─── _fmt_duration ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (1, "1s"),
        (30, "30s"),
        (59, "59s"),
        (60, "1m"),
        (600, "10m"),  # tiktok.orders interval
        (900, "15m"),  # tiktok.after_sales
        (1800, "30m"),  # tiktok.order_detail
        (3599, "59m"),
        (3600, "1h"),
        (21600, "6h"),  # products + token.refresh
        (86399, "23h"),
        (86400, "1d"),  # day-long job (reporting.profit_daily 等)
        (259200, "3d"),
    ],
)
def test_fmt_duration_brackets(seconds: int, expected: str) -> None:
    """Pure helper; seconds → human-friendly bucket."""
    assert main_mod._fmt_duration(seconds) == expected


# ─── _print_jobs (list subcommand) ─────────────────────────────────


def test_print_jobs_renders_table_without_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`list` mode prints the JOBS registry table; never opens a DB session."""
    main_mod._print_jobs()
    captured = capsys.readouterr().out
    # Header + footer line.
    assert "JOB NAME" in captured
    assert "next-fire preview" in captured
    # Spot-check a few critical entries are listed.
    assert "tiktok.orders" in captured
    assert "tiktok.products" in captured
    assert "token.refresh" in captured


def test_noop_session_factory_raises_if_called() -> None:
    """The placeholder factory used by `list` SHOULD blow up if invoked —
    proves `list` truly doesn't touch the DB."""
    factory = main_mod._noop_session_factory()
    with pytest.raises(RuntimeError, match="list mode"):
        factory()


# ─── _run_one_job (run subcommand) ─────────────────────────────────


def test_run_one_job_unknown_name_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown job name → exit code 2, lists available jobs on stderr."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")
    rc = main_mod._run_one_job("does.not.exist")
    assert rc == 2
    err = capsys.readouterr().err
    assert "does.not.exist" in err
    assert "tiktok.orders" in err  # at least one known job listed


def test_run_one_job_dispatches_tiktok_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For an is_tiktok job, _run_tiktok_job is the dispatch target."""
    fake_factory = MagicMock(name="session_factory")
    seen: dict = {}

    def fake_tiktok(spec, sf):
        seen["spec"] = spec
        seen["sf"] = sf

    monkeypatch.setattr(scheduler, "_run_tiktok_job", fake_tiktok)
    monkeypatch.setattr(scheduler, "_run_system_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory",
        lambda *a, **k: fake_factory,
    )

    rc = main_mod._run_one_job("tiktok.orders")
    assert rc == 0
    assert seen["spec"].job_name == "tiktok.orders"
    assert seen["sf"] is fake_factory


def test_run_one_job_dispatches_system_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a system job (token.refresh), _run_system_job is the target."""
    fake_factory = MagicMock(name="session_factory")
    seen: dict = {}

    def fake_system(spec, sf):
        seen["spec"] = spec
        seen["sf"] = sf

    monkeypatch.setattr(scheduler, "_run_system_job", fake_system)
    monkeypatch.setattr(scheduler, "_run_tiktok_job", lambda *a, **k: None)
    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory",
        lambda *a, **k: fake_factory,
    )

    rc = main_mod._run_one_job("token.refresh")
    assert rc == 0
    assert seen["spec"].job_name == "token.refresh"


# ─── main() dispatch ───────────────────────────────────────────────


def test_main_daemon_registers_signal_handlers_and_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()` with no args → daemon mode; signal.signal called for
    SIGTERM and SIGINT, sched.start() called once."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")

    fake_factory = MagicMock(name="session_factory")
    fake_sched = MagicMock(name="scheduler")
    # get_jobs() should return some iterable of mock jobs with .id.
    job_a = MagicMock()
    job_a.id = "tiktok.orders"
    job_b = MagicMock()
    job_b.id = "tiktok.products"
    fake_sched.get_jobs.return_value = [job_a, job_b]

    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory",
        lambda *a, **k: fake_factory,
    )
    monkeypatch.setattr(
        # Patch where main uses build_scheduler (top-level import), not
        # where it's defined — main.py has its own bound name from
        # ``from tts_erp_v2.sync_worker.scheduler import build_scheduler``.
        "tts_erp_v2.sync_worker.main.build_scheduler",
        lambda sf, **k: fake_sched,
    )

    signals_seen: dict = {}

    def fake_signal(signum, handler):
        signals_seen[signum] = handler

    monkeypatch.setattr("signal.signal", fake_signal)

    rc = main([])
    assert rc == 0
    # SIGTERM = 15, SIGINT = 2 on POSIX.
    import signal as _sig

    assert _sig.SIGTERM in signals_seen
    assert _sig.SIGINT in signals_seen
    fake_sched.start.assert_called_once()


def test_main_daemon_signal_handler_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler installed for SIGTERM/SIGINT should call sched.shutdown
    and sys.exit(0) — that is what lets systemd stop the worker cleanly."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")

    fake_factory = MagicMock(name="session_factory")
    fake_sched = MagicMock(name="scheduler")
    fake_sched.get_jobs.return_value = []
    monkeypatch.setattr(
        "tts_erp_v2.db.base.get_session_factory",
        lambda *a, **k: fake_factory,
    )
    monkeypatch.setattr(
        # Patch where main uses build_scheduler (top-level import), not
        # where it's defined.
        "tts_erp_v2.sync_worker.main.build_scheduler",
        lambda sf, **k: fake_sched,
    )

    captured_handler: dict = {}
    monkeypatch.setattr(
        "signal.signal",
        lambda signum, handler: captured_handler.setdefault(signum, handler),
    )

    main([])
    handler = captured_handler[15]  # SIGTERM
    with pytest.raises(SystemExit) as exc:
        handler(15, None)
    assert exc.value.code == 0
    fake_sched.shutdown.assert_called_once()


def test_main_list_runs_without_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`list` subcommand never opens a session."""
    # NOTE: env vars are still required (per main() docstring), so set them.
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")

    called = {"build": 0}

    def fake_build(*args, **kwargs):
        called["build"] += 1
        # The factory passed in is the no-op one from _print_jobs.
        # We return a fake scheduler with one mock job.

        s = MagicMock(name="scheduler")
        job = MagicMock()
        job.id = "tiktok.orders"
        # Force the interval.total_seconds() happy path.
        job.trigger.interval.total_seconds.return_value = 600
        s.get_jobs.return_value = [job]
        return s

    monkeypatch.setattr(
        "tts_erp_v2.sync_worker.main.build_scheduler", fake_build
    )

    rc = main(["list"])
    assert rc == 0
    assert called["build"] == 1
    out = capsys.readouterr().out
    assert "tiktok.orders" in out


def test_main_run_delegates_to_run_one_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run <name>` → _run_one_job(name); env vars required."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")

    called: dict = {}
    # ``or 0`` short-circuits on the truthy setdefault result (which
    # is the just-set value, the job-name string), so the lambda would
    # return ``name`` instead of ``0`` and ``main()`` would propagate it
    # as the exit code. Use update+tuple so the function returns 0
    # unconditionally while still capturing the call argument.
    monkeypatch.setattr(
        main_mod,
        "_run_one_job",
        lambda name: (called.update({"name": name}) or 0),
    )

    rc = main(["run", "tiktok.orders"])
    assert rc == 0
    assert called["name"] == "tiktok.orders"


def test_main_run_without_arg_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`run` with no job name → usage error."""
    monkeypatch.setenv("TTS_ERP_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", "abc")
    rc = main(["run"])
    assert rc == 2
    assert "usage" in capsys.readouterr().err


def test_main_help_returns_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--help` / `-h` → usage text + return 0."""
    rc = main(["--help"])
    assert rc == 0
    assert "usage" in capsys.readouterr().out


def test_main_unknown_subcommand_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Garbage subcommand → exit 2, stderr names the unknown subcommand."""
    rc = main(["frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "frobnicate" in err
