"""Guard the P6 retirement of the per-page frontend assets.

P6 (2026-11-08) retired the old per-page implementations: the Jinja page
templates under ``tts_erp_v2/templates/pages/`` and the per-page JavaScript
under ``tts_erp_v2/static/js/``. Every registered page now renders through the
shared React release.

``tests/support/retired_frontend_assets.py`` keeps the *names* of the retired
page scripts so other suites can assert that no served document references
them. **This module is the other half**: it locks the retirement itself in
place, so that re-adding a template, a legacy renderer, or an inline legacy
document fails the suite instead of passing silently.

These are pure filesystem and source-text assertions — no database, no
fixtures, no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.retired_frontend_assets import RETIRED_PAGE_SCRIPTS

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]

REPO = Path(__file__).resolve().parents[2]
TEMPLATES_PAGES = REPO / "tts_erp_v2" / "templates" / "pages"
STATIC_JS = REPO / "tts_erp_v2" / "static" / "js"
PAGES_PY = REPO / "tts_erp_v2" / "api" / "v2" / "pages.py"
AD_DAILY_PY = REPO / "tts_erp_v2" / "api" / "v2" / "ad_daily.py"

# ``console.js`` stays on purpose: it is mounted as a public static asset on
# ``app.py`` (not a page asset) and ``tests/api/test_manual_costs_page_v2.py``
# asserts against its source. It is not a per-page asset.
KEPT_JS = frozenset({"console.js"})


def test_no_page_template_may_reappear():
    """The Jinja page templates are gone; only React owns the pages now."""
    templates = sorted(path.name for path in TEMPLATES_PAGES.glob("*.html"))
    assert not templates, (
        "旧 Jinja 页面模板回来了，请确认是否该用 React 版本并重新退役："
        f"{templates}"
    )


def test_only_kept_assets_remain_under_static_js():
    """Only the deliberately kept public asset may remain in ``static/js``."""
    present = {path.name for path in STATIC_JS.glob("*.js")}
    unexpected = sorted(present - KEPT_JS)
    assert not unexpected, (
        f"static/js 下出现未登记的页面脚本: {unexpected}；"
        "若确为退役资产，请一并删除并更新 "
        "tests/support/retired_frontend_assets.py 的名单"
    )
    assert present <= KEPT_JS, f"意外的 static/js 内容: {sorted(present)}"


def test_retired_page_scripts_are_gone_from_disk():
    """Each name on the retirement list must not exist as a file."""
    remaining = sorted(name for name in RETIRED_PAGE_SCRIPTS if (STATIC_JS / name).exists())
    assert not remaining, f"退役脚本仍在磁盘上: {remaining}"


def test_pages_module_has_no_legacy_jinja_renderer():
    """``_render_page`` and the per-page Jinja environment must not come back."""
    source = PAGES_PY.read_text(encoding="utf-8")

    assert "_render_page" not in source, (
        "pages.py 又出现了 _render_page —— 旧 Jinja renderer 不得复活"
    )
    assert "_templates_env" not in source, (
        "pages.py 又出现了 _templates_env —— 旧 Jinja 环境不得复活"
    )
    assert "MIGRATED_PAGE_IDS:" not in source and (
        "in frontend_pages.MIGRATED_PAGE_IDS" not in source
    ), (
        "pages.py 又按 MIGRATED_PAGE_IDS 做分支 —— "
        "已迁移页面集合不再是「React / 旧页面」的开关"
    )


def test_ad_daily_module_has_no_inline_legacy_document():
    """The ad-daily page must not grow an inline legacy document again."""
    source = AD_DAILY_PY.read_text(encoding="utf-8")

    assert "_PAGE_HTML" not in source, (
        "ad_daily.py 又出现了 _PAGE_HTML —— 内联旧页面文档不得复活"
    )
    assert "in frontend_pages.MIGRATED_PAGE_IDS" not in source, (
        "ad_daily.py 又按 MIGRATED_PAGE_IDS 做分支 —— "
        "该页由 React renderer 唯一拥有"
    )


@pytest.mark.parametrize(
    ("path", "needle", "reason"),
    [
        (
            PAGES_PY,
            "static/js/",
            "pages.py 的页面说明不应再指向旧页面脚本",
        ),
        (
            AD_DAILY_PY,
            "static/js/",
            "ad_daily.py 的页面说明不应再指向旧页面脚本",
        ),
    ],
)
def test_page_modules_do_not_point_at_old_scripts(
    path: Path, needle: str, reason: str
):
    """Docstrings and comments must not send the next reader back to the old code."""
    source = path.read_text(encoding="utf-8")
    offenders = [
        f"{index}: {line.strip()}"
        for index, line in enumerate(source.splitlines(), start=1)
        if needle in line
    ]
    assert not offenders, f"{reason}:\n  " + "\n  ".join(offenders)
