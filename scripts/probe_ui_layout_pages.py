"""渲染全部页面为静态 HTML，供 UI 布局巡检使用（只读，不碰数据库）。

用法:
    .venv/bin/python scripts/probe_ui_layout_pages.py [--out DIR]

输出 DIR 下 <slug>.html（默认 /tmp/ui-audit/pages）。页面 HTML 由仓库自己的
Vite manifest renderer 渲染，与当前页面入口一致；业务接口不发起，
由 scripts/probe_ui_layout_audit.js 在浏览器侧 mock。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from starlette.requests import Request  # noqa: E402

from tts_erp_v2.accounts.pages import PAGES  # noqa: E402
from tts_erp_v2.api.v2.frontend_pages import render_frontend_page  # noqa: E402

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/tmp/ui-audit/pages", help="输出目录")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for page in PAGES:
        slug = page.page_id
        request = Request({"type": "http", "root_path": "", "path": f"/v2/pages/{slug}"})
        resp = render_frontend_page(request, slug)
        (out / f"{slug}.html").write_text(resp.body.decode("utf-8"), encoding="utf-8")
        print("rendered", slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
