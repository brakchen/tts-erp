"""Smoke tests for ``tts_erp_v2.sync_worker.__main__``.

The ``__main__`` shim is just::

    from tts_erp_v2.sync_worker.main import main
    if __name__ == "__main__":
        sys.exit(main())

So we verify:

* The module imports without side effects.
* It exposes :func:`main` as the same callable ``main.main`` returns.
* Executing it as a real subprocess (``python -m tts_erp_v2.sync_worker``)
  reaches ``main()``. We use ``list`` as the subcommand because it exits
  without starting a :class:`BlockingScheduler` (the daemon path blocks
  forever and is exercised by the systemd unit test suite, not here).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.domain_sync]


def test_main_module_exports_main_callable() -> None:
    """`from tts_erp_v2.sync_worker.__main__ import main` is the same
    callable as `tts_erp_v2.sync_worker.main.main`."""
    from tts_erp_v2.sync_worker import __main__ as mod
    from tts_erp_v2.sync_worker import main as main_mod

    assert mod.main is main_mod.main


def test_main_module_runs_main_when_invoked_as_script() -> None:
    """``python -m tts_erp_v2.sync_worker list`` dispatches into main.main()
    and exits 0 without starting a scheduler.

    Run via subprocess instead of runpy — runpy has a known collision
    with the ``__main__`` shim when the module is already imported (it
    warns ``found in sys.modules after import of package`` and ends up
    running the daemon branch which blocks for 30s).
    """
    env = {
        **os.environ,
        "TTS_ERP_DB_URL": "postgresql+psycopg://x/y",
        "TTS_ERP_FERNET_KEY": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
    }
    result = subprocess.run(
        [sys.executable, "-m", "tts_erp_v2.sync_worker", "list"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        cwd="/home/schan/tts-erp",  # run from project root so package discovery works
    )
    # ``list`` exits 0 and prints a table of JOBS. The exact format is
    # not asserted (column widths etc. are cosmetic) — just verify the
    # invocation reached ``main()`` and listed at least one registered
    # job.
    assert result.returncode == 0, (
        f"non-zero exit; stderr=\n{result.stderr}\nstdout=\n{result.stdout}"
    )
    assert "tiktok.orders" in result.stdout, (
        f"expected tiktok.orders in list output; got\n{result.stdout}"
    )
