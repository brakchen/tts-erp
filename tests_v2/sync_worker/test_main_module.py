"""Smoke tests for ``tts_erp_v2.sync_worker.__main__``.

The ``__main__`` shim is just::

    from tts_erp_v2.sync_worker.main import main
    if __name__ == "__main__":
        sys.exit(main())

So we verify:

* The module imports without side effects.
* It exposes :func:`main` as the same callable ``main.main`` returns.
* Executing it as ``__main__`` (via :mod:`runpy`) reaches ``main()`` —
  done with a monkeypatched main() that returns a sentinel exit code
  to prove the dispatch wired correctly without touching the real DB.
"""

from __future__ import annotations

import runpy
import sys

import pytest

pytestmark = [pytest.mark.domain_sync]


def test_main_module_exports_main_callable() -> None:
    """`from tts_erp_v2.sync_worker.__main__ import main` is the same
    callable as `tts_erp_v2.sync_worker.main.main`."""
    from tts_erp_v2.sync_worker import __main__ as mod
    from tts_erp_v2.sync_worker import main as main_mod

    assert mod.main is main_mod.main


def test_main_module_runs_main_when_invoked_as_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m tts_erp_v2.sync_worker`` dispatches into main.main()."""
    import tts_erp_v2.sync_worker.__main__ as target_mod

    sentinel = object()
    monkeypatch.setattr(target_mod, "main", lambda: sentinel)

    # Reload via runpy as __main__; sys.argv is wiped to mimic real CLI.
    monkeypatch.setattr(sys, "argv", ["tts_erp_v2.sync_worker"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module(
            "tts_erp_v2.sync_worker",
            run_name="__main__",
            alter_sys=True,
        )
    assert exc.value.code is sentinel
