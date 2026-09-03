"""Unit tests for scripts.migrate_v1_to_v2.common — pure helpers, no DB.

These cover:
  * Three time-unit conversions (epoch seconds / epoch ms / GMT+8 strings).
  * Mock-shop filter.
  * iter_batches behavior.
  * DryRunSink accumulation.
  * Production-migration kill-switch
    (``is_prod_migration_allowed`` / ``require_prod_guard``)

They run without any DB connection so they exercise edge cases safely.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.migrate_v1_to_v2.common import (
    MOCK_SHOP_ID,
    PROD_GUARD_ENV,
    DryRunSink,
    epoch_ms_to_utc,
    epoch_seconds_to_utc,
    gmt8_string_to_utc,
    is_prod_migration_allowed,
    is_real_shop_id,
    iter_batches,
    require_prod_guard,
)

pytestmark = [pytest.mark.domain_migration, pytest.mark.layer_integration, pytest.mark.slow]

# ─── time conversion ──────────────────────────────────────────────


class TestEpochSeconds:
    """epoch_seconds_to_utc: epoch-second ints → UTC datetime."""

    def test_basic(self) -> None:
        # 2026-08-29 10:05:15 UTC (verified against live source)
        result = epoch_seconds_to_utc(1787997915)
        assert result == datetime(2026, 8, 29, 10, 5, 15, tzinfo=UTC)

    def test_none_returns_none(self) -> None:
        assert epoch_seconds_to_utc(None) is None

    def test_zero_returns_none(self) -> None:
        # Zero is treated as "no value" to avoid 1970-01-01 false positives.
        assert epoch_seconds_to_utc(0) is None

    def test_empty_string_returns_none(self) -> None:
        # str '' is falsy → None.
        assert epoch_seconds_to_utc("") is None  # type: ignore[arg-type]

    def test_negative_seconds_produces_pre_epoch_datetime(self) -> None:
        # Negative seconds are valid (pre-1970). Production data has none,
        # but the function shouldn't reject them silently — it returns
        # the corresponding 1969 datetime.
        result = epoch_seconds_to_utc(-1)
        assert result is not None
        assert result.year < 1970

    def test_overflow_returns_none(self) -> None:
        # Out-of-range: catches accidental int64 misuse.
        assert epoch_seconds_to_utc(10**20) is None

    def test_returns_aware_utc(self) -> None:
        result = epoch_seconds_to_utc(1787997915)
        assert result is not None
        assert result.tzinfo is not None
        offset = result.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0


class TestEpochMs:
    """epoch_ms_to_utc: epoch-millisecond ints → UTC datetime."""

    def test_basic(self) -> None:
        # 1787997915000ms == 1787997915s → same wall clock.
        result = epoch_ms_to_utc(1787997915000)
        assert result == datetime(2026, 8, 29, 10, 5, 15, tzinfo=UTC)

    def test_returns_same_as_seconds_when_divided(self) -> None:
        # Cross-check: epoch_ms and epoch_seconds agree for the same wall clock.
        ms = epoch_ms_to_utc(1788018419000)
        s = epoch_seconds_to_utc(1788018419)
        assert ms == s

    def test_none(self) -> None:
        assert epoch_ms_to_utc(None) is None

    def test_zero(self) -> None:
        assert epoch_ms_to_utc(0) is None

    def test_falsy(self) -> None:
        assert epoch_ms_to_utc("") is None  # type: ignore[arg-type]

    def test_returns_aware_utc(self) -> None:
        result = epoch_ms_to_utc(1787997915000)
        assert result is not None
        assert result.tzinfo is not None
        offset = result.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0


class TestGmt8String:
    """gmt8_string_to_utc: miaoshou wall-clock strings (UTC+8) → UTC."""

    def test_basic(self) -> None:
        # 2026-08-29 16:18:01 UTC+8 → 2026-08-29 08:18:01 UTC.
        result = gmt8_string_to_utc("2026-08-29 16:18:01")
        assert result == datetime(2026, 8, 29, 8, 18, 1, tzinfo=UTC)

    def test_iso_format_with_t_separator(self) -> None:
        # Some clients emit ISO 8601; we accept and normalize.
        result = gmt8_string_to_utc("2026-08-29T16:18:01")
        assert result == datetime(2026, 8, 29, 8, 18, 1, tzinfo=UTC)

    def test_date_only(self) -> None:
        # Date-only string falls back to a midnight parse.
        result = gmt8_string_to_utc("2026-08-29")
        assert result == datetime(2026, 8, 28, 16, 0, 0, tzinfo=UTC)

    def test_with_microseconds_stripped(self) -> None:
        # ``.123456`` suffix is dropped (miaoshou doesn't emit it but we
        # defend against future schema additions).
        result = gmt8_string_to_utc("2026-08-29 16:18:01.500000")
        assert result == datetime(2026, 8, 29, 8, 18, 1, tzinfo=UTC)

    def test_none(self) -> None:
        assert gmt8_string_to_utc(None) is None

    def test_empty_string(self) -> None:
        assert gmt8_string_to_utc("") is None

    def test_unparseable_returns_none(self) -> None:
        # Garbage input must not raise — the caller treats None as
        # "skip this row's timestamp column".
        assert gmt8_string_to_utc("not-a-date") is None

    def test_returns_aware_utc(self) -> None:
        result = gmt8_string_to_utc("2026-08-29 16:18:01")
        assert result is not None
        assert result.tzinfo is not None
        offset = result.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0


# ─── shop id filter ────────────────────────────────────────────────


class TestIsRealShopId:
    """is_real_shop_id: filters out MOCK_SHOP_12345 + falsy inputs."""

    def test_mock_shop_rejected(self) -> None:
        assert is_real_shop_id(MOCK_SHOP_ID) is False
        assert is_real_shop_id("MOCK_SHOP_12345") is False

    def test_real_shop_accepted(self) -> None:
        assert is_real_shop_id("7494763368967603447") is True

    def test_none_rejected(self) -> None:
        assert is_real_shop_id(None) is False

    def test_empty_string_rejected(self) -> None:
        assert is_real_shop_id("") is False

    def test_other_garbage_rejected(self) -> None:
        # Non-MOCK but invalid shop ids are still accepted by the filter
        # (downstream FK lookup will catch them). The filter is a
        # allow-list for the known mock row.
        assert is_real_shop_id("MOCK_SHOP_99999") is True


# ─── iter_batches ──────────────────────────────────────────────────


class TestIterBatches:
    """iter_batches: chunk an iterable into fixed-size lists."""

    def test_exact_multiple(self) -> None:
        result = list(iter_batches([1, 2, 3, 4, 5, 6], 3))
        assert result == [[1, 2, 3], [4, 5, 6]]

    def test_with_remainder(self) -> None:
        result = list(iter_batches([1, 2, 3, 4, 5, 6, 7], 3))
        assert result == [[1, 2, 3], [4, 5, 6], [7]]

    def test_empty_input(self) -> None:
        result = list(iter_batches([], 3))
        assert result == []

    def test_smaller_than_batch(self) -> None:
        result = list(iter_batches([1, 2], 10))
        assert result == [[1, 2]]

    def test_zero_batch_size_falls_back(self) -> None:
        # batch_size <= 0 falls back to a single batch (one item each).
        # This is the documented guard so a caller passing 0 doesn't get
        # a ZeroDivisionError or infinite loop.
        result = list(iter_batches([1, 2, 3], 0))
        assert len(result) >= 1

    def test_yields_list_objects(self) -> None:
        result = list(iter_batches([1, 2, 3], 2))
        for batch in result:
            assert isinstance(batch, list)


# ─── DryRunSink ────────────────────────────────────────────────────


class TestDryRunSink:
    """DryRunSink: accumulates counters and prints a plan summary."""

    def test_record_and_counts(self) -> None:
        sink = DryRunSink()
        sink.record("public.orders", 100)
        sink.record("public.orders", 50)
        sink.record("public.shops", 2)
        assert sink.counts == {
            "public.orders": 150,
            "public.shops": 2,
        }

    def test_report_lists_sorted(self) -> None:
        sink = DryRunSink()
        sink.record("zzz", 1)
        sink.record("aaa", 1)
        out = sink.report()
        assert "DRY-RUN PLAN:" in out
        assert "aaa: 1" in out
        assert "zzz: 1" in out
        # aaa comes before zzz in the report (sorted ascending).
        assert out.index("aaa") < out.index("zzz")

    def test_initial_counts_empty(self) -> None:
        sink = DryRunSink()
        assert sink.counts == {}
        # Empty report should still be well-formed.
        out = sink.report()
        assert "DRY-RUN PLAN:" in out


# ─── prod migration kill-switch ────────────────────────────────────
#
# These tests verify the 2026-08-30 incident guard:
# ``is_prod_migration_allowed`` / ``require_prod_guard`` together refuse
# to write to the production DB unless ``TTS_ERP_ALLOW_PROD_MIGRATION=1``
# is set in the environment. They use ``monkeypatch.setenv`` /
# ``monkeypatch.delenv`` so they're hermetic and don't depend on the
# actual process env. Each test restores the previous state automatically
# (monkeypatch fixture lifecycle).


class TestIsProdMigrationAllowed:
    """is_prod_migration_allowed: env var → bool predicate."""

    def test_unset_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PROD_GUARD_ENV, raising=False)
        assert is_prod_migration_allowed() is False

    def test_set_to_one_returns_true(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(PROD_GUARD_ENV, "1")
        assert is_prod_migration_allowed() is True

    def test_set_to_empty_string_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Empty string is unset-equivalent. Belt-and-braces against
        # ``export TTS_ERP_ALLOW_PROD_MIGRATION=``.
        monkeypatch.setenv(PROD_GUARD_ENV, "")
        assert is_prod_migration_allowed() is False

    def test_set_to_truthy_non_one_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Shell truthiness ("true", "yes", "on") is NOT accepted — the
        # explicit "1" literal avoids accidental opt-in via accidental
        # shell truthiness. This is the security-critical property.
        for value in ("true", "True", "TRUE", "yes", "on", "0", "2", " "):
            monkeypatch.setenv(PROD_GUARD_ENV, value)
            assert is_prod_migration_allowed() is False, (
                f"value {value!r} should NOT enable prod migrations"
            )


class TestRequireProdGuard:
    """require_prod_guard: refuse non-dry-run writes without the env var."""

    def test_dry_run_skips_check(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Even with the env var unset, dry_run=True must be a no-op
        # (it doesn't write, so the kill-switch doesn't apply).
        monkeypatch.delenv(PROD_GUARD_ENV, raising=False)
        # No SystemExit raised.
        require_prod_guard(dry_run=True, action="dry-run test")

    def test_real_run_with_env_var_succeeds(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(PROD_GUARD_ENV, "1")
        # No SystemExit raised.
        require_prod_guard(dry_run=False, action="real-run with opt-in")

    def test_real_run_without_env_var_exits_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.delenv(PROD_GUARD_ENV, raising=False)
        with pytest.raises(SystemExit) as excinfo:
            require_prod_guard(
                dry_run=False, action="migrate_shops.run() against prod",
            )
        # Exit code 2 distinguishes the kill-switch refusal from per-script
        # error codes (0 / 1).
        assert excinfo.value.code == 2
        # The reason is printed to stderr so the operator sees what to fix.
        captured = capsys.readouterr()
        assert "REFUSED" in captured.err
        assert "TTS_ERP_ALLOW_PROD_MIGRATION=1" in captured.err
        assert "migrate_shops.run()" in captured.err

    def test_real_run_with_truthy_non_one_exits_2(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # "yes" / "true" / etc. must NOT enable the guard — the explicit
        # "1" literal is the only valid opt-in. This is the property the
        # test above documents; this test pins it at the function level.
        for value in ("true", "yes", "on", "0"):
            monkeypatch.setenv(PROD_GUARD_ENV, value)
            with pytest.raises(SystemExit) as excinfo:
                require_prod_guard(dry_run=False, action="real-run")
            assert excinfo.value.code == 2, (
                f"value {value!r} must NOT bypass the guard"
            )
