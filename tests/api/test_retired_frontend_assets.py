"""Guard the P6 retirement of the per-page frontend assets.

P6 (2026-11-08) retired the old per-page implementations: the Jinja page
templates under ``tts_erp_v2/templates/pages/``, the per-page JavaScript under
``tts_erp_v2/static/js/``, and the per-page stylesheets under
``tts_erp_v2/static/css/``. Every registered page now renders through the
shared React release.

``tests/support/retired_frontend_assets.py`` keeps the *names* of the retired
page scripts and stylesheets so other suites can assert that no served
document references them. **This module is the other half**: it locks the
retirement itself in place, so that re-adding a template, a legacy renderer,
an inline legacy document, or a retired stylesheet fails the suite instead of
passing silently.

These are pure filesystem and source-text assertions — no database, no
fixtures, no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.retired_frontend_assets import (
    RETIRED_ASSETS,
    RETIRED_PAGE_SCRIPTS,
    RETIRED_PAGE_STYLES,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]

REPO = Path(__file__).resolve().parents[2]
TEMPLATES_PAGES = REPO / "tts_erp_v2" / "templates" / "pages"
STATIC_JS = REPO / "tts_erp_v2" / "static" / "js"
STATIC_CSS = REPO / "tts_erp_v2" / "static" / "css"
PAGES_PY = REPO / "tts_erp_v2" / "api" / "v2" / "pages.py"
AD_DAILY_PY = REPO / "tts_erp_v2" / "api" / "v2" / "ad_daily.py"

# ``console.js`` stays on purpose: it is mounted as a public static asset on
# ``app.py`` (not a page asset) and ``tests/api/test_manual_costs_page_v2.py``
# asserts against its source. It is not a per-page asset.
KEPT_JS = frozenset({"console.js"})

# The stylesheets that legitimately remain under ``static/css``: the shared
# token palette and navigation rules are imported by ``frontend/src/styles.css``,
# and the publishing contracts assert against the other two.
KEPT_CSS = frozenset(
    {
        "common.css",
        "navigation.css",
        "tokens.css",
        "video-publish.css",
    }
)


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


def test_retired_page_stylesheets_are_gone_from_disk():
    """A retired stylesheet must not come back to disk either.

    A stylesheet is an asset exactly like a script. Re-adding one would give
    the old page-specific styling a second owner next to the React release.
    """
    remaining = sorted(name for name in RETIRED_PAGE_STYLES if (STATIC_CSS / name).exists())
    assert not remaining, f"退役样式表仍在磁盘上: {remaining}"


def test_only_kept_stylesheets_remain_under_static_css():
    """Only the shared/live stylesheets may remain in ``static/css``.

    An unregistered file here is either a leftover page stylesheet or a new
    asset that nobody registered; both must be deliberate, so the list is
    closed.
    """
    present = {path.name for path in STATIC_CSS.glob("*.css")}
    unexpected = sorted(present - KEPT_CSS)
    assert not unexpected, (
        f"static/css 下出现未登记的样式表: {unexpected}；"
        "若确为退役资产，请一并删除并更新 "
        "tests/support/retired_frontend_assets.py 的名单"
    )
    assert present <= KEPT_CSS, f"意外的 static/css 内容: {sorted(present)}"


def test_retired_assets_cover_scripts_and_styles():
    """The combined retirement list must contain both asset categories.

    Guard against the two lists drifting apart: a style added to one list but
    not the other would silently escape the "no served document may reference
    a retired asset" assertion.
    """
    assert set(RETIRED_ASSETS) == set(RETIRED_PAGE_SCRIPTS) | set(RETIRED_PAGE_STYLES)
    assert set(RETIRED_PAGE_SCRIPTS) & set(RETIRED_PAGE_STYLES) == set()
    assert any(name.endswith(".js") for name in RETIRED_ASSETS)
    assert any(name.endswith(".css") for name in RETIRED_ASSETS)


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


@pytest.mark.parametrize(
    "path",
    [PAGES_PY, AD_DAILY_PY],
)
def test_page_modules_do_not_point_at_retired_stylesheets(path: Path):
    """A retired stylesheet must not be referenced by the page modules.

    The check names the retired stylesheets individually instead of matching
    the ``static/css/`` prefix: live stylesheets such as ``navigation.css``
    are still imported by React and are referenced from ``pages.py``, so a
    directory-prefix match would flag a healthy reference.
    """
    source = path.read_text(encoding="utf-8")
    offenders = [
        f"{index}: {line.strip()}"
        for index, line in enumerate(source.splitlines(), start=1)
        if any(style in line for style in RETIRED_PAGE_STYLES)
    ]
    assert not offenders, (
        f"{path.name} 又指向了已退役的页面样式表:\n  " + "\n  ".join(offenders)
    )
