"""Tick-end heap release for the long-lived sync worker."""

from __future__ import annotations

import pytest

from tts_erp_v2.sync_worker import scheduler


pytestmark = pytest.mark.layer_unit


def test_release_tick_memory_collects_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class _Lib:
        def malloc_trim(self, pad: int) -> int:
            calls.append(pad)
            return 1

    monkeypatch.setattr(scheduler.gc, "collect", lambda: calls.append("gc") or 0)
    monkeypatch.setattr(scheduler.ctypes, "CDLL", lambda _name: _Lib())

    scheduler.release_tick_memory()

    assert calls == ["gc", 0]


def test_release_tick_memory_ignores_missing_malloc_trim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler.gc, "collect", lambda: 0)

    def _missing(_name: str) -> object:
        raise OSError("libc.so.6")

    monkeypatch.setattr(scheduler.ctypes, "CDLL", _missing)

    scheduler.release_tick_memory()
