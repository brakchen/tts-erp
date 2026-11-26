"""Prove daily test selection keeps expensive coverage behind its own triggers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import _test_selection as selection_module
from scripts._test_selection import (
    DRAWER_BROWSER,
    MONITOR_TESTS,
    OAUTH_TESTS,
    PROFITABILITY_TESTS,
    PROTOTYPE_CHECK,
    SCHEDULER,
    SCHEDULER_TESTS,
    SHOP_NAME_CONSUMERS,
    SHOP_NAME_HELPER,
    SHOP_NAME_PAGES,
    SHOP_NAME_TEST,
    ZABBIX_CHECK,
    select,
    select_commits,
)


def test_page_ui_change_runs_its_browser_and_components():
    plan = select(["frontend/src/pages/dashboard/index.tsx", "README.md"])
    assert plan.frontend and plan.vitest == ["src/pages/dashboard/"]
    assert plan.browser_mock == ["tests/browser/test_frontend_dashboard_layout.py"]
    assert plan.build and not (plan.backend or plan.backend_all or plan.scenarios)


def test_dashboard_layout_adds_only_its_mock_browser_file():
    plan = select(["frontend/src/pages/dashboard/spacing with spaces.css"])
    assert plan.browser_mock == ["tests/browser/test_frontend_dashboard_layout.py"]
    assert plan.build and not plan.scenarios


def test_runninghub_page_keeps_its_real_browser_boundaries():
    plan = select(["frontend/src/pages/runninghub/index.tsx"])
    assert plan.browser_mock == ["tests/browser/test_runninghub_page.py"]
    assert plan.vitest == ["src/pages/runninghub/"]
    assert plan.build and not plan.scenarios


def test_formula_uses_its_backend_tests_without_node():
    plan = select(["tts_erp_v2/analytics/spu_profitability/_formula_v10.py"])
    assert plan.backend == ["tests/analytics/test_spu_profitability_formula.py"]
    assert not (plan.frontend or plan.build or plan.backend_all)


def test_api_contract_pairs_both_sides_without_browser():
    plan = select(["tts_erp_v2/api/v2/ad_daily.py"])
    assert plan.contracts == ["ad_daily"]
    assert plan.backend == ["tests/api/test_ad_daily.py"]
    assert plan.vitest == ["src/pages/ad-daily/"]
    assert plan.frontend and not (plan.build or plan.browser_mock or plan.scenarios)


def test_unknown_uses_both_without_browser_and_cannot_lose_broad_frontend():
    plan = select(["!new-surface", "frontend/src/pages/dashboard/index.tsx"])
    assert plan.backend_all and plan.frontend and not plan.vitest
    assert plan.browser_mock == ["tests/browser/test_frontend_dashboard_layout.py"]
    assert plan.build and not plan.scenarios


def test_scenario_adds_a_build_without_changing_selected_sides():
    plan = select(["tts_erp_v2/api/v2/auth.py"], ["login-cookie"])
    assert plan.frontend and plan.backend
    assert plan.scenarios == ["login-cookie"] and plan.build


def test_empty_and_docs_only_select_no_application_test():
    for changes in (
        [], ["docs/README.md"], ["docs/api/external-api.md"],
        ["docs/guides/agent-testing.md"], ["tests/fixtures/media/README.md"],
    ):
        plan = select(changes)
        assert not (plan.frontend or plan.backend_all or plan.backend or plan.build)


def test_changed_test_runs_even_when_it_needs_the_frontend_build():
    packager = select(["tests/frontend/test_build_package.py"])
    assert packager.checks == ["tests/frontend/test_build_package.py"]
    assert not packager.build

    account = select(["tests/scripts/test_frontend_p2_accounts.py"])
    assert account.scenarios == ["account-page"] and account.build


def test_browser_test_edits_are_scoped_to_the_changed_test_file():
    plan = select(["tests/browser/test_frontend_p2_live.py"])
    assert plan.checks == ["tests/browser/test_frontend_p2_live.py"]
    assert plan.build and not (plan.backend_all or plan.frontend or plan.scenarios)


def test_contract_fixture_selects_its_provider_and_consumer():
    assert select(["tests/fixtures/ad_daily_summary.json"]).contracts == ["ad_daily"]


def test_explicit_side_stays_independent_of_git_diff():
    paths = ["tts_erp_v2/api/v2/frontend_pages.py"]
    front = select(paths, scope="frontend")
    back = select(paths, scope="backend")
    assert front.frontend and not (front.backend_all or front.build)
    assert back.backend_all and not (back.frontend or back.build)


def test_registered_api_with_multiple_consumers_runs_all_its_pages():
    plan = select(["tts_erp_v2/api/v2/media.py"])
    assert plan.backend == ["tests/api/test_media_api.py"]
    assert plan.vitest == ["src/pages/media-assets/", "src/pages/media-assets/workflows.test.tsx"]
    assert not plan.build


def test_auth_selection_covers_session_and_middleware_contracts():
    plan = select(["tts_erp_v2/middleware/auth.py"])
    assert "tests/api/test_user_auth.py" in plan.backend
    assert "tests/api/test_middleware.py" in plan.backend
    assert "src/pages/login/" in plan.vitest
    assert "src/shared/api/" in plan.vitest
    assert "src/pages/manual-costs/" not in plan.vitest
    assert not plan.build


def test_api_contract_selection_keeps_enum_and_roi_consumers():
    config = select(["tts_erp_v2/api/v2/config.py"])
    assert "tests/api/test_enum_map.py" in config.backend
    assert "src/pages/enum-map/" in config.vitest
    analytics = select(["tts_erp_v2/api/v2/analytics.py"])
    assert "tests/api/test_spu_price_stats.py" in analytics.backend
    assert "tests/api/test_spu_roi_fee_rate.py" in analytics.backend


def test_deleted_test_is_not_collected_and_browser_fixture_change_gets_a_browser_check():
    deleted = "tests/browser/test_obsolete.py"
    plan = select([deleted, "tests/browser/conftest.py"], deleted=frozenset({deleted}))
    assert deleted not in plan.checks
    assert plan.browser_mock == ["tests/browser/test_frontend_dashboard_layout.py"]
    assert plan.frontend and plan.build



def test_pinned_fast_requests_full_frontend_backend_and_build():
    plan = select([], scope="fast")
    assert plan.frontend and plan.backend_all and plan.build
    assert not plan.vitest


def test_zabbix_template_selects_validation_without_application_suites():
    plan = select(["docs/plugins/monitor/zabbix-monitor.yaml", "docs/plugins/monitor/zabbix-monitor.md"])
    assert plan.checks == [ZABBIX_CHECK]
    assert not (plan.frontend or plan.backend_all or plan.backend or plan.build or plan.scenarios)
    assert select(["docs/unknown.yaml"]).backend_all


def test_drawer_has_a_relevant_browser_check_and_keeps_named_scenarios():
    path = "frontend/src/app/navigation-drawer.css"
    drawer = select([path])
    assert drawer.vitest == ["src/app/AppShell.test.tsx", "src/app/SessionActions.test.tsx"]
    assert drawer.browser_mock == [DRAWER_BROWSER]
    assert drawer.build and not (drawer.backend_all or drawer.backend or drawer.scenarios)
    assert select([path], ["login-cookie"]).scenarios == ["login-cookie"]
    shared = select(["frontend/src/app/workspace.css", path])
    assert not shared.vitest
    assert "tests/browser/test_frontend_dashboard_layout.py" in shared.browser_mock


def test_component_test_only_does_not_trigger_browser_or_build():
    plan = select(["frontend/src/pages/dashboard/index.test.tsx"])
    assert plan.vitest == ["src/pages/dashboard/"]
    assert not (plan.build or plan.browser_mock or plan.backend_all)


def test_page_browser_keeps_all_viewports_and_shared_shell_is_broader():
    page = select(["frontend/src/pages/ad-daily/index.tsx"])
    assert page.browser_mock == ["tests/browser/test_frontend_ad_daily_filters.py"]
    shared = select(["frontend/src/app/AppShell.tsx"])
    assert "tests/browser/test_frontend_pilots.py" in shared.browser_mock
    assert shared.frontend and shared.build and not shared.vitest


def test_media_fixture_helper_and_ui_keep_only_known_consumers():
    plan = select([
        "frontend/src/pages/media-assets/AssetPreview.tsx",
        "tests/fixtures/media/TEST_preview.webm",
        "tests/support/media_browser.py",
        "tts_erp_v2/media/repository.py",
        "tests/api/test_media_api.py",
        "docs/api/external-api.md",
    ])
    assert not plan.backend_all
    assert plan.backend == ["tests/api/test_media_api.py"]
    assert plan.vitest == ["src/pages/media-assets/", "src/pages/media-assets/workflows.test.tsx"]
    assert plan.checks == plan.browser_mock == ["tests/browser/test_media_workflows.py"]
    assert plan.build and plan.docs


def test_changed_backend_test_is_narrow_but_deleted_and_shared_tests_stay_broad():
    path = "tests/api/test_media_api.py"
    assert select([path]).backend == [path]
    assert not select([path]).backend_all
    assert select([path], deleted=frozenset({path})).backend_all
    assert select(["tests/api/conftest.py"]).backend_all
    assert select(["tts_erp_v2/db/session.py"]).backend_all


def test_spu_deterioration_page_selects_both_its_mock_and_e2e_files():
    """SPU 对比页的 React 改动必须同时选中 mock browser 与 e2e 冒烟。

    e2e 冒烟起真实 API 子进程、对着隔离库跑真实 React 产物，是 mock browser
    文件替代不了的覆盖。它原先不在任何页面登记里，于是改这个页面时
    ``--scope auto`` 选不到它，门禁是瞎的。
    """
    plan = select(["frontend/src/pages/spu-profit-deterioration/index.tsx"])
    assert plan.browser_mock == [
        "tests/browser/test_spu_deterioration_alert_page.py",
        "tests/e2e/test_spu_deterioration_alert_smoke.py",
    ]
    assert plan.build


def test_prototype_acceptance_task_runs_only_real_static_asset_checks() -> None:
    # b28f840f's complete diff: prototype style acceptance must not build ERP.
    plan = select(["prd/03-prototype-full-replica.md", "prd/prototypes/styles.css"])
    assert plan.checks == [PROTOTYPE_CHECK]
    assert plan.docs and not (plan.frontend or plan.backend or plan.backend_all or plan.build)
    for path in ("prd/prototypes/dashboard.html", "prd/prototypes/shell.js"):
        assert select([path]).checks == [PROTOTYPE_CHECK]


def test_scheduler_interval_task_keeps_all_worker_and_api_consumers() -> None:
    plan = select([SCHEDULER, "tests/sync_worker/test_scheduler_jobs_coverage.py"])
    assert set(SCHEDULER_TESTS) <= set(plan.backend)
    assert {
        "tests/api/test_sync_status_api.py", "tests/api/test_sync_job_management.py",
        "tests/api/test_sync_freshness_api.py", "tests/api/test_frontend_remaining_safety.py",
    } <= set(plan.backend)
    assert plan.vitest == ["src/pages/dashboard/", "src/pages/spu-profitability/", "src/pages/sync-jobs/"]
    assert not (plan.backend_all or plan.build or plan.browser_mock or plan.scenarios)


def test_shop_alias_task_preserves_page_coverage_without_full_component_suite() -> None:
    # The shared helper and its actual six page consumers from f6f3e38b.
    plan = select([
        *SHOP_NAME_CONSUMERS, "docs/api/external-api.md",
        "frontend/src/pages/dashboard/index.test.tsx",
        "frontend/src/pages/manual-costs/index.test.tsx",
        "frontend/src/pages/spu-profit-deterioration/index.test.tsx",
        "frontend/src/pages/spu-profitability/index.test.tsx",
        "frontend/src/pages/sync-jobs/index.test.tsx",
    ])
    assert plan.vitest == sorted([
        "src/shared/shop-name.test.ts", *(f"src/pages/{page}/" for page in SHOP_NAME_PAGES),
    ])
    assert plan.frontend and plan.build and not plan.backend_all
    assert "tests/browser/test_frontend_dashboard_layout.py" in plan.browser_mock
    assert "tests/browser/test_frontend_remaining_manual_native.py" in plan.browser_mock
    assert "tests/e2e/test_spu_deterioration_alert_smoke.py" in plan.browser_mock


def test_monitor_oauth_and_profitability_selection_keep_known_providers_and_consumers() -> None:
    monitor = select(["tts_erp_v2/plugin/monitor_relay.py"])
    assert monitor.backend == sorted(MONITOR_TESTS)
    assert not (monitor.frontend or monitor.backend_all or monitor.build)
    for path in sorted(selection_module.OAUTH_PATHS):
        oauth = select([path])
        assert set(OAUTH_TESTS) <= set(oauth.backend)
        assert {"src/pages/shops/", "src/pages/message/", "src/pages/oauth-onboard/"} <= set(oauth.vitest)
        assert not (oauth.backend_all or oauth.build)
    profitability = select([selection_module.PROFITABILITY_SELECTION])
    assert set(PROFITABILITY_TESTS) <= set(profitability.backend)
    assert {"analytics", "focused_spus", "spu_deterioration_alert"} <= set(profitability.contracts)
    assert not profitability.backend_all


@pytest.mark.parametrize("path", [
    "prd/prototypes/TEST_unknown.py", "prd/TEST_runtime.js",
    "frontend/src/shared/TEST_new-helper.ts", "tts_erp_v2/sync_worker/TEST_new.py",
    "tts_erp_v2/analytics/TEST_new.py", "tts_erp_v2/plugin/TEST_new.py",
    "tts_erp_v2/db/session.py", "tts_erp_v2/api/deps.py",
])
def test_narrow_registrations_do_not_absorb_unknown_or_shared_runtime_paths(path: str) -> None:
    plan = select([path])
    assert plan.backend_all or (plan.frontend and not plan.vitest)


def test_prototype_deletion_checks_remaining_links_but_test_deletion_stays_broad() -> None:
    asset = "prd/prototypes/dashboard.html"
    assert select([asset], deleted=frozenset({asset})).checks == [PROTOTYPE_CHECK]
    assert select([PROTOTYPE_CHECK], deleted=frozenset({PROTOTYPE_CHECK})).backend_all
    unknown = "tts_erp_v2/TEST_removed.py"
    assert select([unknown], deleted=frozenset({unknown})).backend_all


def _candidate_git(monkeypatch: pytest.MonkeyPatch, consumers: set[str], *, missing_test: bool = False) -> None:
    def git(*args: str) -> bytes:
        if args[0] == "diff":
            return f"M\0{SHOP_NAME_HELPER}\0".encode()
        if args[0] == "grep":
            assert "TEST_head" in args and args[-1] == "frontend/src"
            return "".join(f"TEST_head:{path}\0" for path in sorted(consumers)).encode()
        assert args[0] == "ls-tree" and "TEST_head" in args
        tests = [f"frontend/src/pages/{page}/index.test.tsx" for page in SHOP_NAME_PAGES]
        if not missing_test:
            tests.append(SHOP_NAME_TEST)
        return "".join(f"{path}\0" for path in tests).encode()
    monkeypatch.setattr(selection_module, "git", git)


def test_candidate_shop_helper_checks_known_consumers_without_a_build(monkeypatch: pytest.MonkeyPatch) -> None:
    _candidate_git(monkeypatch, set(SHOP_NAME_CONSUMERS))
    plan = select_commits("TEST_base", "TEST_head", None, "auto")
    assert len(plan.vitest) == len(SHOP_NAME_PAGES) + 1
    assert not (plan.build or plan.backend_all)


@pytest.mark.parametrize("extra", ["frontend/src/pages/TEST_new/index.tsx", "frontend/src/app/TEST_shell.tsx"])
def test_new_shop_helper_consumer_widens_candidate_selection(
    monkeypatch: pytest.MonkeyPatch, extra: str,
) -> None:
    _candidate_git(monkeypatch, {*SHOP_NAME_CONSUMERS, extra})
    plan = select_commits("TEST_base", "TEST_head", None, "auto")
    assert plan.frontend and not plan.vitest
    assert "unmapped" in plan.reasons["shop-name"]


def test_missing_helper_consumer_or_test_widens_candidate_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    for consumers, missing_test in ((set(), False), (set(SHOP_NAME_CONSUMERS), True)):
        _candidate_git(monkeypatch, consumers, missing_test=missing_test)
        plan = select_commits("TEST_base", "TEST_head", None, "auto")
        assert plan.frontend and not plan.vitest


def test_every_selected_test_path_still_exists_on_disk():
    """选择器引用的测试文件必须真实存在。

    P6 退役删掉三个旧前端测试后，选择器仍把它们当作「必跑目标」，
    于是每次非 fast 范围的基线运行都把它们报成 missing 并 exit 1。
    这条断言把「引用了已删文件」变成红灯，而不是等某次基线跑到一半才发现。

    扫描方式是通用的：模块里任何看起来像测试路径的字符串常量都会被检查，
    所以以后新增映射表时漏改也会被这条抓住。
    """
    def references(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value} if value.startswith("tests/") else set()
        if isinstance(value, dict):
            return set().union(*(references(item) for item in value.values()))
        if isinstance(value, (tuple, list, set, frozenset)):
            return set().union(*(references(item) for item in value))
        return set()

    def exists(target: str) -> bool:
        path: Path = selection_module.ROOT / target.split("::", 1)[0]
        return path.is_file() or (path.is_dir() and any(path.rglob("test_*.py")))

    referenced = set().union(*(
        references(value) for name, value in vars(selection_module).items() if not name.startswith("_")
    ))
    assert "tests/api/test_ad_daily.py" in referenced  # Nested provider/consumer table.
    assert DRAWER_BROWSER in referenced  # Node targets must not escape the audit.
    assert "tests/sync_worker" in referenced  # Whole domain targets are intentional.
    missing = sorted(target for target in referenced if not exists(target))
    assert not missing, f"选择器引用了不存在的测试路径: {missing}"
