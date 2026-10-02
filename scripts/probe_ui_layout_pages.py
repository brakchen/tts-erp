"""渲染全部页面为静态 HTML，供 UI 布局巡检使用（只读，不碰数据库）。

用法:
    .venv/bin/python scripts/probe_ui_layout_pages.py [--out DIR]

输出 DIR 下 <slug>.html（默认 /tmp/ui-audit/pages）。页面 HTML 由仓库自己的
Jinja 环境渲染（tts_erp_v2.api.v2.pages），与线上完全一致；业务接口不发起，
由 scripts/probe_ui_layout_audit.js 在浏览器侧 mock。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tts_erp_v2.api.v2.pages import (  # noqa: E402
    _SpuProfitabilityPageConfig,
    _render_page,
    _render_spu_profitability_page,
)

PLAIN = {
    "dashboard": "dashboard.html",
    "shops": "shops.html",
    "enum-map": "enum-map.html",
    "runtime-configs": "runtime-configs.html",
    "sync-jobs": "sync-jobs.html",
    "manual-costs": "manual-costs.html",
    "intercept-configs": "intercept-configs.html",
    "intercept-requests": "intercept-requests.html",
    "intercept-stats": "intercept-stats.html",
}

SPU_PAGES = (
    ("spu-roi", "standard-roi", "spu-roi.js", "SPU 实际 ROI"),
    ("focused-spus", "focused-spus", "focused-spus.js", "重点关注 SPU"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/tmp/ui-audit/pages", help="输出目录")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for slug, tpl in PLAIN.items():
        resp = _render_page(tpl, current_page=slug)
        (out / f"{slug}.html").write_text(resp.body.decode("utf-8"), encoding="utf-8")
        print("rendered", slug)

    for slug, profile_id, entry, title in SPU_PAGES:
        resp = _render_spu_profitability_page(
            _SpuProfitabilityPageConfig(
                slug=slug,
                title=title,
                profile_id=profile_id,
                entrypoint_js=entry,
            )
        )
        (out / f"{slug}.html").write_text(resp.body.decode("utf-8"), encoding="utf-8")
        print("rendered", slug)

    # ad-daily 页面 HTML 内联在 ad_daily.py，直接调其路由函数。
    from tts_erp_v2.api.v2 import ad_daily  # noqa: E402

    resp = ad_daily.ad_daily_page()
    (out / "ad-daily.html").write_text(resp.body.decode("utf-8"), encoding="utf-8")
    print("rendered ad-daily")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
