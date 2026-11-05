"""Build a test selection from an explicit, synchronized Git comparison."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_PREFIX = "frontend/src/pages/"
PAGE_TEST_FILTERS = {"digital-humans": "src/pages/media-assets/workflows.test.tsx"}
COMPONENTS = "tests/frontend/test_components.py"
PACKAGER = "tests/frontend/test_build_package.py"
SOURCE_CONTRACTS = "tests/frontend/test_source_contracts.py"
STYLE_TOKENS = "tests/frontend/test_style_tokens.py"
SELECTOR_TESTS = "tests/scripts/test_test_selection.py"
ZABBIX_TEMPLATE = "docs/plugins/monitor/zabbix-monitor.yaml"
ZABBIX_CHECK = "tests/scripts/test_zabbix_monitor_template.py"
DRAWER_BROWSER = "tests/browser/test_frontend_pilots.py::test_navigation_drawer_theme_and_focus"
LEGACY_FRONTEND = (
    "tests/publishing/test_polling_channels.py",
    "tests/publishing/test_upload_cancellation.py",
    "tests/publishing/test_video_publish_frontend.py",
)

# Only map a domain narrowly when its provider and consumer tests are known.
API_CONTRACTS = {
    "ad_daily": (("ad-daily",), ("tests/api/test_ad_daily.py",)),
    "analytics": (
        ("dashboard", "spu-profitability"),
        (
            "tests/api/test_analytics_coverage.py", "tests/api/test_analytics_dumps_v4.py",
            "tests/api/test_analytics_v2_contract.py", "tests/api/test_analytics_v2_errors.py",
            "tests/api/test_spu_roi_api.py", "tests/api/test_spu_price_stats.py", "tests/api/test_spu_roi_fee_rate.py",
        ),
    ),
    "config": (
        ("enum-map", "runtime-configs"),
        ("tests/api/test_enum_map.py", "tests/api/test_runtime_config.py", "tests/api/test_runtime_config_review.py"),
    ),
    "focused_spus": (("spu-profitability",), ("tests/api/test_focused_spus.py",)),
    "intercept": (
        ("intercept-configs", "intercept-requests", "intercept-stats"),
        ("tests/api/test_intercept_configs.py", "tests/api/test_intercept_sync.py"),
    ),
    "media": (("digital-humans", "media-assets"), ("tests/api/test_media_api.py",)),
    "spu_deterioration_alert": (("spu-profit-deterioration",), ("tests/api/test_spu_deterioration_alert_api.py",)),
    "sync_status": (
        ("dashboard", "sync-jobs"),
        ("tests/api/test_sync_status_api.py", "tests/api/test_sync_job_management.py"),
    ),
    "users": (("users",), ("tests/api/test_user_auth.py", "tests/api/test_frontend_remaining_cookie_writes.py")),
    "video_publish": (("video-publish",), ("tests/publishing/test_http_contracts.py",)),
}

MOCK_BROWSER = {
    "ad-daily": ("tests/browser/test_frontend_ad_daily_filters.py",),
    "dashboard": ("tests/browser/test_frontend_dashboard_layout.py",),
    "shops": ("tests/browser/test_frontend_shops.py",),
    "spu-profitability": ("tests/browser/test_spu_react_workspace.py",),
    "spu-profit-deterioration": ("tests/browser/test_spu_deterioration_alert_page.py",),
}

SCENARIOS = {
    "page-assets": "tests/api/test_spu_roi_api.py::test_spu_roi_page_cache_busts_css_and_js_from_their_own_content",
    "account-page": "tests/scripts/test_frontend_p2_accounts.py::test_real_cookie_roles_page_access_scope_and_revocation_use_same_clone",
    "login-cookie": "tests/browser/test_frontend_remaining_auth_native.py::test_native_enter_cookie_next_logout_and_blank_login_capture",
}
PAGE_ASSETS = {
    "scripts/build_frontend.sh",
    "frontend/scripts/package-build.mjs",
    "tts_erp_v2/api/v2/frontend_pages.py",
}


@dataclass
class Selection:
    frontend: bool = False
    vitest: list[str] = field(default_factory=list)
    backend_all: bool = False
    backend: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    browser_mock: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    build: bool = False
    refresh_template: bool = False
    docs: bool = False
    reasons: dict[str, str] = field(default_factory=dict)

    def finish(self) -> None:
        for items in (self.vitest, self.backend, self.checks, self.contracts, self.browser_mock, self.scenarios):
            items[:] = sorted(set(items))
        self.build = bool(self.browser_mock or self.scenarios or self.build)


def _frontend_filters(selection: Selection, *patterns: str) -> None:
    if not selection.frontend or selection.vitest:
        selection.vitest.extend(patterns)
    selection.frontend = True


def _page(selection: Selection, page: str) -> None:
    _frontend_filters(selection, PAGE_TEST_FILTERS.get(page, f"src/pages/{page}/"))


def _frontend_all(selection: Selection) -> None:
    selection.frontend = True
    selection.vitest.clear()


def _contract(selection: Selection, api: str) -> None:
    pages, tests = API_CONTRACTS[api]
    for page in pages:
        _page(selection, page)
    selection.backend.extend(tests)
    selection.contracts.append(api)


def _classify(selection: Selection, path: str) -> str:
    if path in {ZABBIX_TEMPLATE, ZABBIX_CHECK}:
        selection.checks.append(ZABBIX_CHECK)
        return "Zabbix template syntax and structure"
    if path == "frontend/src/app/navigation-drawer.css":
        _frontend_filters(selection, "src/app/AppShell.test.tsx", "src/app/SessionActions.test.tsx")
        selection.checks.append(STYLE_TOKENS)
        selection.browser_mock.append(DRAWER_BROWSER)
        return "mobile navigation drawer theme and focus"
    if path in PAGE_ASSETS:
        _frontend_all(selection)
        selection.checks.append(PACKAGER)
        selection.scenarios.append("page-assets")
        return "page assets and serving contract"
    if path == "tests/fixtures/ad_daily_summary.json":
        _contract(selection, "ad_daily")
        return "ad-daily shared contract sample"
    if path.startswith("tts_erp_v2/api/v2/") and Path(path).stem in API_CONTRACTS:
        _contract(selection, Path(path).stem)
        return "registered API provider and consumer"
    if path in {"tts_erp_v2/api/v2/auth.py", "tts_erp_v2/api/v2/oauth.py", "tts_erp_v2/middleware/auth.py"}:
        _frontend_filters(
            selection, "src/app/", "src/bootstrap-login.test.ts", "src/pages/login/",
            "src/pages/oauth-onboard/", "src/shared/api/",
        )
        selection.backend.extend((
            "tests/api/test_auth_login.py", "tests/api/test_cookie_csrf.py", "tests/api/test_middleware.py",
            "tests/api/test_oauth_api.py", "tests/api/test_user_auth.py",
        ))
        return "auth contract; add a named browser scenario if browser behavior changes"
    if path.startswith(("tts_erp_v2/api/", "frontend/src/shared/api/")) or path in {
        "frontend/src/bootstrap.ts", "frontend/src/shared/contracts.ts", "tts_erp_v2/app.py",
    }:
        _frontend_all(selection)
        selection.backend_all = True
        return "shared boundary; both browser-free suites"
    if path.startswith(PAGE_PREFIX) and "/" in path[len(PAGE_PREFIX):]:
        page = path[len(PAGE_PREFIX):].split("/", 1)[0]
        _page(selection, page)
        if page in {"dashboard", "shops", "spu-profitability", "spu-profit-deterioration", "sync-jobs"}:
            selection.checks.append(SOURCE_CONTRACTS)
        if path.endswith(".css"):
            selection.checks.append(STYLE_TOKENS)
            selection.browser_mock.extend(MOCK_BROWSER.get(page, ()))
            if page not in MOCK_BROWSER:
                return f"frontend page {page}; review the unregistered layout check"
        return f"frontend page {page}"
    if path in LEGACY_FRONTEND or (
        path.startswith("tests/frontend/") and Path(path).name.startswith("test_")
    ):
        _frontend_all(selection)
        selection.checks.append(path)
        return "changed frontend test"
    if path == "tts_erp_v2/static/css/video-publish.css":
        _page(selection, "video-publish")
        selection.checks.extend((
            STYLE_TOKENS,
            "tests/publishing/test_contracts.py::test_publish_responsive_accessibility_contract_uses_existing_css",
            "tests/publishing/test_contracts.py::test_publish_confirm_dialog_layout_and_copy_match_design_spec",
        ))
        return "legacy video-publish stylesheet and accessibility contract"
    if path.startswith(("frontend/", "tests/frontend/", "tts_erp_v2/static/css/", "tts_erp_v2/static/js/")):
        _frontend_all(selection)
        if path.endswith(".css"):
            selection.checks.extend((SOURCE_CONTRACTS, STYLE_TOKENS))
            selection.browser_mock.extend(MOCK_BROWSER["dashboard"] + MOCK_BROWSER["spu-profitability"])
        if path in {"frontend/vite.config.ts", "frontend/package.json", "frontend/package-lock.json"}:
            selection.build = True
            selection.checks.append(PACKAGER)
        return "frontend"
    if path.startswith(("alembic/", "docs/schema/")) or path == "alembic.ini":
        selection.backend_all = True
        selection.refresh_template = True
        return "database schema"
    if path.startswith("tts_erp_v2/analytics/spu_profitability/") and Path(path).name in {
        "_formula_v10.py", "_projection.py",
    }:
        selection.backend.append("tests/analytics/test_spu_profitability_formula.py")
        return "backend profitability formula"
    if path == "tts_erp_v2/analytics/spu_roi.py":
        _contract(selection, "analytics")
        return "SPU ROI API provider and consumer"
    if path.startswith("tts_erp_v2/analytics/"):
        selection.backend_all = True
        return "backend analytics"
    if path.startswith("tts_erp_v2/"):
        selection.backend_all = True
        return "backend"
    if path == "tests/browser/conftest.py":
        selection.browser_mock.extend(MOCK_BROWSER["dashboard"])
        return "changed browser fixtures; check representative mock browser"
    if path.startswith("tests/browser/") and Path(path).name.startswith("test_"):
        if any(path == target.split("::", 1)[0] for files in MOCK_BROWSER.values() for target in files):
            selection.browser_mock.append(path)
            return "changed mocked browser regression"
        selection.checks.append(path)
        selection.build = True
        return "changed browser test; run that file only"
    if path in {"tests/api/test_spu_roi_api.py", "tests/scripts/test_frontend_p2_accounts.py"}:
        selection.backend.append(path)
        selection.scenarios.append("page-assets" if path.startswith("tests/api/") else "account-page")
        return "changed backend test file with an explicit asset-dependent case"
    if path.startswith("tests/") and path.endswith(".py") and path != "tests/conftest.py":
        if path.startswith("tests/e2e/"):
            return "explicit smoke change; validate affected smoke separately in its approved environment"
        if path.startswith("tests/support/") or path.endswith("/conftest.py"):
            selection.backend_all = True
            _frontend_all(selection)
            return "shared test or explicit smoke surface"
        selection.backend_all = True
        return "backend test"
    if path.startswith("scripts/test") or path in {"scripts/_test_selection.py", "AGENTS.md"}:
        selection.backend.extend((
            SELECTOR_TESTS, "tests/test_test_template_refresh.py", "tests/test_test_run_binding.py",
        ))
        _frontend_all(selection)
        return "test infrastructure"
    if path.endswith(".md") and not path.startswith(("docs/api/", "docs/testing/", "docs/guides/agent-")):
        selection.docs = True
        return "documentation"
    selection.backend_all = True
    _frontend_all(selection)
    return "unclassified; both browser-free suites"


def select(
    paths: list[str], scenarios: list[str] | None = None, *,
    scope: str = "auto", deleted: frozenset[str] = frozenset(),
) -> Selection:
    selection = Selection(
        frontend=scope in {"frontend", "fast"},
        backend_all=scope in {"backend", "fast"},
        build=scope == "fast",
    )
    if scope == "auto":
        for path in sorted(set(paths)):
            if path in deleted and path.startswith(("tests/frontend/", "tests/browser/")):
                _frontend_all(selection)
                selection.reasons[path] = "removed test; validate remaining frontend coverage"
            elif path in deleted and path.startswith("tests/") and path.endswith(".py"):
                selection.backend_all = True
                selection.reasons[path] = "removed test; validate remaining backend coverage"
            else:
                selection.reasons[path] = _classify(selection, path)
    else:
        selection.reasons["scope"] = f"explicit {scope} suite"
    selection.scenarios.extend(scenarios or ())
    selection.finish()
    return selection


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def _git_paths(raw: bytes) -> list[str]:
    if raw and not raw.endswith(b"\0"):
        raise ValueError("Git path output was incomplete")
    return [path.decode("utf-8", errors="surrogateescape") for path in raw.split(b"\0")[:-1]]


def select_commits(base: str, head: str, scenarios: list[str] | None, scope: str) -> Selection:
    fields = _git_paths(git("diff", "--name-status", "-z", "--no-renames", base, head, "--"))
    if len(fields) % 2:
        raise ValueError("Git status output was incomplete")
    changes = dict(zip(fields[1::2], fields[::2], strict=True))
    deleted = frozenset(path for path, status in changes.items() if status == "D")
    plan = select(list(changes), scenarios, scope=scope, deleted=deleted)
    if plan.vitest:
        files = _git_paths(git("ls-tree", "-r", "--name-only", "-z", head, "--", "frontend/src"))
        tests = [path.removeprefix("frontend/") for path in files if path.endswith((".test.ts", ".test.tsx"))]
        if any(not any(test.startswith(pattern) for test in tests) for pattern in plan.vitest):
            _frontend_all(plan)
            plan.reasons["vitest"] = "unmapped page tests; run full frontend component suite"
    return plan


def resolve_commit(revision: str) -> str:
    return git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--scope", choices=("auto", "frontend", "backend", "fast"), default="auto")
    parser.add_argument("--head", default="HEAD", help="inspect a historical commit without checking it out")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), action="append")
    args = parser.parse_args()
    try:
        base, head = resolve_commit(args.base), resolve_commit(args.head)
        git("merge-base", "--is-ancestor", base, head)
        plan = select_commits(base, head, args.scenario, args.scope)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"test selection: {exc}\n")
    print(json.dumps({"base": base, "head": head, **asdict(plan)}, indent=2))


if __name__ == "__main__":
    main()
