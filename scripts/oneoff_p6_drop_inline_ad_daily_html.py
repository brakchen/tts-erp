"""删除 ad_daily.py 里已被 React renderer 取代的内联旧页面 HTML。

P6 退役：`_PAGE_HTML` 在路由收敛（`tts_erp_v2/api/v2/ad_daily.py` 的
`ad_daily_page` 只走 `render_frontend_page`）后已无任何使用者。

用法：python scripts/oneoff_p6_drop_inline_ad_daily_html.py [--check]
  --check  只报告，不写文件
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tts_erp_v2" / "api" / "v2" / "ad_daily.py"

START = '_PAGE_HTML = """<!doctype html>'
END = '"""\n'


def main() -> int:
    check_only = "--check" in sys.argv
    text = TARGET.read_text(encoding="utf-8")

    if START not in text:
        print("未找到 _PAGE_HTML 定义，可能已被删除或改名；请核对源码。")
        return 1

    start = text.index(START)
    end = text.index(END, start) + len(END)
    block = text[start:end]

    # 确认这个块确实是完整的一段内联 HTML，而不是别的东西
    assert block.count('"""') == 2, "内联 HTML 块的引号配对异常"
    assert "<html" in block and "</html>" in block, "内联 HTML 块内容异常"

    # 删除该定义及其前后的空行，保持文件整洁
    updated = text[:start].rstrip("\n") + "\n\n\n" + text[end:].lstrip("\n")

    removed_lines = block.count("\n")
    print(f"将删除 _PAGE_HTML 定义（{removed_lines} 行）")
    if check_only:
        print("(--check 模式，未写文件)")
        return 0

    TARGET.write_text(updated, encoding="utf-8")
    print(f"已写入 {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
