"""Prove daily test selection keeps expensive coverage behind its own triggers."""

from __future__ import annotations

from scripts._test_selection import select


def test_page_without_layout_change_runs_only_frontend():
    plan = select(["frontend/src/pages/dashboard/index.tsx", "README.md"])
    assert plan.frontend and plan.vitest == ["src/pages/dashboard/"]
    assert not (plan.backend or plan.backend_all or plan.build or plan.scenarios)


def test_dashboard_layout_adds_only_its_mock_browser_file():
    plan = select(["frontend/src/pages/dashboard/spacing with spaces.css"])
    assert plan.browser_mock == ["tests/browser/test_frontend_dashboard_layout.py"]
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
    assert not (plan.build or plan.browser_mock or plan.scenarios)


def test_scenario_adds_a_build_without_changing_selected_sides():
    plan = select(["tts_erp_v2/api/v2/auth.py"], ["login-cookie"])
    assert plan.frontend and plan.backend
    assert plan.scenarios == ["login-cookie"] and plan.build


def test_empty_and_docs_only_select_no_application_test():
    for changes in ([], ["docs/README.md"]):
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
