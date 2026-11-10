"""清单⑤：核心作业连续失败 ⇒ 巡检以非零码退出。

2026-11-10 生产事实：``miaoshou.purchase_price_clean`` 从 2026-10-09 14:42 起
连续失败 776 次（31 天）没人发现——watchdog 的 finding 只落本地日志（用户暂不
配置飞书 webhook）。⇒ 「作业坏了」必须有一条**会让人看见**的路：非零退出码。

出口码：0 完成 / 2 参数错 / 3 守卫漏网 / 4 守卫误伤 / 5 身份断言失败 /
**6 = 巡检本身正常但核心作业连续失败**（本清单）。

断言分三类，各自独立 method：
* 位置断言 —— 判据落在哪个模块、哪个常量/函数上；
* 数量枚举 —— 核心作业清单与阈值的精确内容；
* 分支覆盖 —— 每个分支一个用例，含「必须命中」与「必须不命中」孪生。
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import check_jobs_actually_run as inspector

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_unit]


def _row(job_name: str, status: str, error: str = "") -> Any:
    """一条 sync_jobs 行的最小替身。"""
    return SimpleNamespace(
        job_name=job_name,
        status=status,
        error_message=error,
        started_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1),
        finished_at=dt.datetime.now(tz=dt.UTC),
        rows_total=0,
        rows_inserted=0,
        rows_updated=0,
        rows_failed=0,
    )


# ─── 位置断言 ─────────────────────────────────────────────────────────────


def test_core_job_rules_live_in_the_inspection_script() -> None:
    """位置断言：核心作业清单与连续失败判据就在这个巡检脚本里。"""
    assert isinstance(inspector.CORE_JOBS, tuple)
    assert callable(inspector._core_job_failures)


def test_core_job_failure_helper_takes_the_streak_threshold() -> None:
    """位置断言：判据函数按传入的阈值判定，不是写死的常量。"""
    rows = [_row("tiktok.finance", "failed")] * 2
    assert (
        inspector._core_job_failures(
            rows, core_jobs=("tiktok.finance",), threshold=3
        )
        == []
    )
    assert len(
        inspector._core_job_failures(
            rows, core_jobs=("tiktok.finance",), threshold=2
        )
    ) == 1


def test_new_exit_code_does_not_collide_with_the_existing_ones() -> None:
    """位置断言：新出口码与已有约定（2/3/4/5）不撞。"""
    existing = {
        inspector.EXIT_USAGE,
        inspector.EXIT_GUARD_LEAK,
        inspector.EXIT_GUARD_FALSE_POSITIVE,
        inspector.EXIT_IDENTITY,
    }
    assert inspector.EXIT_CORE_JOB_FAILURES not in existing
    assert inspector.EXIT_CORE_JOB_FAILURES == 6


# ─── 数量枚举 ─────────────────────────────────────────────────────────────


def test_core_job_list_is_exactly_the_three_agreed_jobs() -> None:
    """数量枚举：核心作业清单恰好三个，且就是约定的那三个。"""
    assert len(inspector.CORE_JOBS) == 3
    assert set(inspector.CORE_JOBS) == {
        "tiktok.finance",
        "miaoshou.purchase_price_clean",
        "reporting.profit_daily",
    }


def test_core_failure_streak_threshold_is_three() -> None:
    """数量枚举：阈值恰好 3（与 watchdog 规则 (b) 的 CONSECUTIVE_FAILURE_THRESHOLD 对齐）。"""
    assert inspector.CORE_FAILURE_STREAK_THRESHOLD == 3


# ─── 分支覆盖 ─────────────────────────────────────────────────────────────


def test_core_job_failure_is_reported_when_the_streak_reaches_the_threshold() -> None:
    """分支覆盖（必须命中）：核心作业连续失败 3 次 ⇒ 进清单⑤。"""
    rows = [
        _row("tiktok.finance", "failed", "boom 1"),
        _row("tiktok.finance", "failed", "boom 2"),
        _row("tiktok.finance", "failed", "boom 3"),
    ]
    out = inspector._core_job_failures(
        rows,
        core_jobs=inspector.CORE_JOBS,
        threshold=inspector.CORE_FAILURE_STREAK_THRESHOLD,
    )
    assert len(out) == 1
    assert out[0]["job_name"] == "tiktok.finance"
    assert out[0]["consecutive_failures"] == 3
    assert out[0]["last_error_message"] == "boom 1"


def test_core_job_failure_below_the_threshold_is_not_reported() -> None:
    """分支覆盖（不命中孪生）：连续失败 2 次 < 3 ⇒ 不进清单⑤。"""
    rows = [
        _row("miaoshou.purchase_price_clean", "failed", "boom 1"),
        _row("miaoshou.purchase_price_clean", "failed", "boom 2"),
    ]
    out = inspector._core_job_failures(
        rows,
        core_jobs=inspector.CORE_JOBS,
        threshold=inspector.CORE_FAILURE_STREAK_THRESHOLD,
    )
    assert out == []


def test_non_core_job_failure_is_not_reported() -> None:
    """分支覆盖（不命中孪生）：非核心作业连败再多也不进清单⑤。"""
    rows = [_row("miaoshou.packages", "failed", "boom")] * 9
    out = inspector._core_job_failures(
        rows,
        core_jobs=inspector.CORE_JOBS,
        threshold=inspector.CORE_FAILURE_STREAK_THRESHOLD,
    )
    assert out == []


def test_a_success_after_the_failures_stops_the_streak() -> None:
    """分支覆盖（不命中孪生）：尾部连续才算——最新一行是成功就不报。"""
    rows = [
        _row("reporting.profit_daily", "succeeded", ""),
        _row("reporting.profit_daily", "failed", "boom 1"),
        _row("reporting.profit_daily", "failed", "boom 2"),
        _row("reporting.profit_daily", "failed", "boom 3"),
    ]
    out = inspector._core_job_failures(
        rows,
        core_jobs=inspector.CORE_JOBS,
        threshold=inspector.CORE_FAILURE_STREAK_THRESHOLD,
    )
    assert out == []


# ─── 出口码 ───────────────────────────────────────────────────────────────


def _patch_inspector(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    """把巡检的外部依赖全部换成假件（不连库、不做身份断言）。"""

    class _Session:
        def close(self) -> None:
            return None

    def fake_factory():
        """sessionmaker 的形状：调用它返回一个 session。"""
        return _Session()

    def fake_session_factory():
        return fake_factory

    monkeypatch.setattr(inspector, "_load_jobs", dict)
    monkeypatch.setattr(inspector, "_session_factory", fake_session_factory)
    monkeypatch.setattr(inspector, "_install_readonly_guard", lambda f: f)
    monkeypatch.setattr(inspector, "_job_rows", lambda session, since: rows)
    monkeypatch.setattr(inspector, "_all_job_rows", lambda session: rows)
    monkeypatch.setattr(inspector, "_datname_by_process_group", lambda session: [])
    monkeypatch.setattr(inspector, "_stale_tables", lambda session, days, now: ([], []))
    # round16 的三道身份断言：这里换成假件（本用例只关心清单⑤与出口码）
    monkeypatch.setattr(
        inspector,
        "_assert_code_identity",
        lambda root, unit: {"origin": "/fake/root", "expected_root": "/fake/root"},
    )
    monkeypatch.setattr(
        inspector,
        "_assert_db_identity",
        lambda session, expected: {"datname": "fake_db", "expected_datname": expected},
    )
    monkeypatch.setattr(
        inspector,
        "_generation_identity",
        lambda units: {"units": [], "failures": [], "skipped": list(units)},
    )


def test_main_exits_with_six_when_a_core_job_is_failing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """分支覆盖（改变结果）：核心作业连败 ⇒ 巡检以 6 退出，且清单⑤有内容。"""
    rows = [_row("miaoshou.purchase_price_clean", "failed", "boom")] * 3
    _patch_inspector(monkeypatch, rows)

    code = inspector.main(["--json", "--skip-generation"])

    assert code == inspector.EXIT_CORE_JOB_FAILURES
    payload = capsys.readouterr().out
    assert "list_5_core_job_failures" in payload
    assert "miaoshou.purchase_price_clean" in payload


def test_main_exits_with_zero_when_nothing_is_wrong(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """分支覆盖（不命中孪生）：没有核心作业连败 ⇒ 退出码 0。"""
    rows = [_row("miaoshou.purchase_price_clean", "succeeded", "")]
    _patch_inspector(monkeypatch, rows)

    code = inspector.main(["--json", "--skip-generation"])

    assert code == 0
    payload = capsys.readouterr().out
    assert '"list_5_core_job_failures": []' in payload


def test_main_rejects_a_zero_streak_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支覆盖（参数错）：--core-fail-streak 0 ⇒ 退出码 2（参数错），不是 6。"""
    _patch_inspector(monkeypatch, [])
    assert inspector.main(["--core-fail-streak", "0"]) == inspector.EXIT_USAGE
