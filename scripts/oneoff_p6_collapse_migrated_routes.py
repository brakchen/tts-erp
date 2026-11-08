"""把 pages.py / ad_daily.py 里的「已迁移页面双路分支」收敛成单一 React renderer。

背景：P6 退役。`MIGRATED_PAGE_IDS` 是硬编码常量，部署时不变，
所以 `if "<page>" in MIGRATED_PAGE_IDS:` 的 else 分支（旧 Jinja renderer）
是死代码。收敛后即可删除对应旧模板与 `_render_page` 辅助函数。

用「精确片段替换」实现：每处目标片段都由源码事实确定，
替换前逐条断言片段存在且唯一，避免正则跨行匹配的脆弱性。

用法：python scripts/oneoff_p6_collapse_migrated_routes.py [--check]
  --check  只报告将要改动的位置，不写文件
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGES = ROOT / "tts_erp_v2" / "api" / "v2" / "pages.py"
AD_DAILY = ROOT / "tts_erp_v2" / "api" / "v2" / "ad_daily.py"


def pages_branch(page: str) -> tuple[str, str]:
    """Return (old_fragment, new_fragment) for one pages.py branch."""
    old = (
        f'  if "{page}" in frontend_pages.MIGRATED_PAGE_IDS:\n'
        "    if request is None:\n"
        '      raise RuntimeError("Migrated pages require a request")\n'
        f'    return frontend_pages.render_frontend_page(request, "{page}")\n'
        f'  return _render_page("{page}.html", current_page="{page}")\n'
    )
    new = f'  return frontend_pages.render_frontend_page(request, "{page}")\n'
    return old, new


def pages_branch_multiline(page: str) -> tuple[str, str]:
    """Return (old_fragment, new_fragment) for the wrapped _render_page call."""
    old = (
        f'  if "{page}" in frontend_pages.MIGRATED_PAGE_IDS:\n'
        "    if request is None:\n"
        '      raise RuntimeError("Migrated pages require a request")\n'
        f'    return frontend_pages.render_frontend_page(request, "{page}")\n'
        "  return _render_page(\n"
        f'    "{page}.html", current_page="{page}"\n'
        "  )\n"
    )
    new = f'  return frontend_pages.render_frontend_page(request, "{page}")\n'
    return old, new


def ad_daily_branch(page: str) -> tuple[str, str]:
    """Return (old_fragment, new_fragment) for the ad_daily.py branch."""
    old = (
        f'    if "{page}" in frontend_pages.MIGRATED_PAGE_IDS:\n'
        f'        return frontend_pages.render_frontend_page(request, "{page}")\n'
        "    html = (\n"
        '        _PAGE_HTML.replace("__CSS_VERSION__", _asset_version("css/ad-daily.css"))\n'
        '        .replace("__TOKENS_VERSION__", _asset_version("css/tokens.css"))\n'
        '        .replace("__COMMON_VERSION__", _asset_version("css/common.css"))\n'
        '        .replace("__JS_VERSION__", _asset_version("js/ad-daily.js"))\n'
        '        .replace("__SIDEBAR_CSS__", str(_shared_sidebar_css("ad-daily")))\n'
        '        .replace("__SIDEBAR_NAV__", str(_shared_sidebar("ad-daily")))\n'
        "    )\n"
        "    return HTMLResponse(html)\n"
    )
    new = f'    return frontend_pages.render_frontend_page(request, "{page}")\n'
    return old, new


# pages.py 里 _render_page 调用被折行的那一页
MULTILINE_PAGE = "spu-profit-deterioration"

PAGES_TARGETS = [
    "shops",
    MULTILINE_PAGE,
    "manual-costs",
    "dashboard",
    "intercept-configs",
    "intercept-requests",
    "intercept-stats",
    "enum-map",
    "runtime-configs",
    "sync-jobs",
    "video-publish",
    "users",
    "media-assets",
    "digital-humans",
]


def apply(text: str, targets: list[str], builder) -> tuple[str, list[str]]:
    changed: list[str] = []
    for page in targets:
        old, new = builder(page)
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"片段匹配失败：{page} 期望 1 处，实际 {count} 处。"
                f"请核对源码是否已变更。"
            )
        text = text.replace(old, new)
        changed.append(page)
    return text, changed


def main() -> int:
    check_only = "--check" in sys.argv
    total: list[str] = []

    plan = [
        (PAGES, PAGES_TARGETS, pages_branch_multiline),
        (AD_DAILY, ["ad-daily"], ad_daily_branch),
    ]
    for path, targets, builder in plan:
        if path == PAGES:
            # 只有 multiline 那页用折行形态，其余用单行形态
            pieces: list[str] = []
            current = path.read_text(encoding="utf-8")
            for page in targets:
                old, new = (
                    pages_branch_multiline(page)
                    if page == MULTILINE_PAGE
                    else pages_branch(page)
                )
                count = current.count(old)
                if count != 1:
                    raise SystemExit(
                        f"片段匹配失败：{page} 期望 1 处，实际 {count} 处。"
                    )
                current = current.replace(old, new)
                pieces.append(page)
            updated, changed = current, pieces
        else:
            original = path.read_text(encoding="utf-8")
            updated, changed = apply(original, targets, builder)

        total.extend(changed)
        print(f"{path.relative_to(ROOT)}: {len(changed)} 个分支")
        for page in changed:
            print(f"  - {page}")
        if not check_only and updated != path.read_text(encoding="utf-8"):
            path.write_text(updated, encoding="utf-8")
            print(f"  已写入 {path.relative_to(ROOT)}")

    print(f"\n合计 {len(total)} 处: {', '.join(total)}")
    if check_only:
        print("\n(--check 模式，未写文件)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
