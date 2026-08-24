"""TDD: _safe_int() helper — per-request int() hardening (replaces ~68 sites).

Coverage:
- happy path: int string → int
- None/empty → default
- ValueError (non-numeric) → default + log
- TypeError (None inside str → still None) → default
- edge: 0 is preserved (not falsy → not replaced by default)
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _capture_log(caplog):
    caplog.set_level(logging.WARNING, logger="tts-erp")


def test_safe_int_happy_path_int():
    from tts_erp import _safe_int
    assert _safe_int("42") == 42


def test_safe_int_happy_path_int_value():
    from tts_erp import _safe_int
    assert _safe_int(42) == 42


def test_safe_int_none_returns_default():
    from tts_erp import _safe_int
    assert _safe_int(None, default=10) == 10


def test_safe_int_empty_string_returns_default():
    from tts_erp import _safe_int
    assert _safe_int("", default=10) == 10


def test_safe_int_invalid_value_returns_default_and_logs(caplog):
    from tts_erp import _safe_int
    caplog.clear()
    result = _safe_int("not_a_number", default=99, source="test.invalid")
    assert result == 99
    # Verify warning logged with source attribution
    assert any("test.invalid" in r.message and "not_a_number" in r.message
               for r in caplog.records), f"expected log, got: {[r.message for r in caplog.records]}"


def test_safe_int_typeerror_returns_default(caplog):
    """e.g. int(None) → TypeError → must catch, not crash."""
    from tts_erp import _safe_int
    caplog.clear()
    # Pass a value that raises TypeError on int() conversion (not ValueError)
    result = _safe_int([1, 2, 3], default=0, source="test.typeerror")
    assert result == 0


def test_safe_int_zero_preserved():
    """0 is a valid int — must not be replaced by default."""
    from tts_erp import _safe_int
    assert _safe_int("0", default=99) == 0
    assert _safe_int(0, default=99) == 0


def test_safe_int_negative_preserved():
    from tts_erp import _safe_int
    assert _safe_int("-7", default=99) == -7


def test_safe_int_default_param_optional():
    """_safe_int() without explicit default uses 0."""
    from tts_erp import _safe_int
    assert _safe_int(None) == 0
    assert _safe_int("invalid") == 0


def test_safe_int_source_default_unknown():
    """If source not given, default is 'unknown' (no crash on signature)."""
    from tts_erp import _safe_int
    # Just verify it doesn't raise TypeError on missing source
    assert _safe_int("invalid") == 0