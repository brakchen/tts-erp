"""P6 退役：删除旧 Jinja 页面渲染器与旧前端资产。

删除范围：
1. ``tts_erp_v2/api/v2/pages.py`` 的 ``_render_page`` 与 ``_templates_env``
   —— 路由收敛后已无调用者；保留 ``_sidebar`` / ``_sidebar_css`` /
   ``_SIDEBAR_CSS`` / ``_SIDEBAR_TOGGLE_JS``（``docs/design/reuse-evaluation-2026-10.md``
   已决定原样保留，测试也直接断言这些常量）。
2. ``tts_erp_v2/templates/pages/*.html``（12 个旧 Jinja 页面模板）
3. ``tts_erp_v2/static/js/*.js``（12 个旧页面脚本；``console.js`` **不删**
   —— 它是 ``app.py`` 上的 public static 资源，不是页面资产，
   ``tests/api/test_middleware.py`` 仍断言它可达）

用法：python scripts/oneoff_p6_retire_legacy_assets.py [--check]
  --check  只报告将要删除的内容，不写文件
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_PY = ROOT / "tts_erp_v2" / "api" / "v2" / "pages.py"
TEMPLATES_DIR = ROOT / "tts_erp_v2" / "templates" / "pages"
JS_DIR = ROOT / "tts_erp_v2" / "static" / "js"

# console.js 本轮保留：public static 资源，不是页面资产
KEEP_JS = {"console.js"}

RETIRED_HTML = (
    "dashboard.html", "enum-map.html", "intercept-configs.html",
    "intercept-requests.html", "intercept-stats.html", "manual-costs.html",
    "runtime-configs.html", "shops.html", "spu-profit-deterioration.html",
    "sync-jobs.html", "users.html", "video-publish.html",
)

RETIRED_JS = (
    "ad-daily.js", "dashboard.js", "enum-map.js", "intercept-configs.js",
    "intercept-requests.js", "intercept-stats.js", "runtime-configs.js",
    "shops.js", "spu-profit-deterioration.js", "sync-jobs.js", "users.js",
    "video-publish.js",
)

# pages.py 里要删除的片段（精确文本，逐条断言唯一存在）
PAGE_PY_BLOCKS = (
    (
        '_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"\n'
        "class _TrustedHtml(str):\n"
        '  """HTML generated only from fixed templates and escaped labels."""\n'
        "  def __html__(self) -> str:\n"
        "    return self\n"
        "\n"
        "\n"
        "_templates_env = Environment(\n"
        '  loader=FileSystemLoader(_TEMPLATES_DIR),\n'
        '  autoescape=select_autoescape(["html"]),\n'
        "  keep_trailing_newline=True,\n"
        ")\n"
        "_templates_env.globals.update(\n"
        "  js_version=_js_version,\n"
        "  css_version=_css_version,\n"
        "  sidebar=_sidebar,\n"
        "  sidebar_css=_sidebar_css,\n"
        ")\n"
        "\n"
        "\n"
        'def _render_page(template: str, *, current_page: str = "", **context: Any) -> HTMLResponse:\n'
        '  """Render ``templates/pages/<template>`` (Jinja2) into an HTML response.\n'
        "\n"
        "  Template context: ``current_page`` (sidebar active slug) plus per-page\n"
        "  extras. Asset cache-busting is exposed to templates as the\n"
        "  ``js_version``/``css_version`` globals (SHA-256 content stamps).\n"
        '  """\n'
        '  html = _templates_env.get_template(f"pages/{template}").render(\n'
        "    current_page=current_page, **context\n"
        "  )\n"
        "  return HTMLResponse(html)\n",
        None,
    ),
)


def main() -> int:
    check_only = "--check" in sys.argv
    problems: list[str] = []

    # 1) pages.py
    text = PAGES_PY.read_text(encoding="utf-8")
    for block, _ in PAGE_PY_BLOCKS:
        count = text.count(block)
        if count != 1:
            problems.append(f"pages.py 片段匹配 {count} 次（期望 1）：{block[:60]!r}")
        else:
            text = text.replace(block, "")
    # jinja2 import 在删掉 Environment 后不再使用
    if "Environment(" not in text and "from jinja2 import" in text:
        text = text.replace(
            "from jinja2 import Environment, FileSystemLoader, select_autoescape\n", ""
        )

    # 2) 待删文件
    targets: list[Path] = []
    for name in RETIRED_HTML:
        path = TEMPLATES_DIR / name
        if not path.exists():
            problems.append(f"模板不存在：{path.relative_to(ROOT)}")
        else:
            targets.append(path)
    for name in RETIRED_JS:
        if name in KEEP_JS:
            continue
        path = JS_DIR / name
        if not path.exists():
            problems.append(f"脚本不存在：{path.relative_to(ROOT)}")
        else:
            targets.append(path)

    print(f"pages.py：将删除 _render_page / _templates_env 基础设施")
    print(f"待删文件 {len(targets)} 个：")
    for path in targets:
        print(f"  - {path.relative_to(ROOT)}")
    print(f"保留：{', '.join(sorted(KEEP_JS))}（public static 资源）")

    if problems:
        print("\n问题：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if check_only:
        print("\n(--check 模式，未写文件)")
        return 0

    PAGES_PY.write_text(text, encoding="utf-8")
    for path in targets:
        path.unlink()
    print(f"\n已删除 {len(targets)} 个文件并清理 pages.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
