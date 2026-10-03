#!/usr/bin/env python3
"""把页面模板的内联 :root / 与 common.css 重复的规则收敛掉，改为引用共享样式表。

用法（在 lane worktree 根目录）：
    python3 scripts/oneoff_ui_converge_templates.py            # 试运行，只打印将要做的事
    python3 scripts/oneoff_ui_converge_templates.py --apply    # 真正写文件

设计要点：
* Jinja 表达式 ``{{ ... }}`` / ``{% ... %}`` 里的花括号会干扰 CSS 解析，
  解析前先换成占位符，写回时再还原。
* 只删除**顶层**规则（不在 @media 里）；@media 里的响应式微调是页面特有的，
  留给页面自己维护。
* 只删除选择器在 common.css 里已定义的规则；页面特有规则一律保留。
* enum-map.html / users.html 是灰蓝体系（--bg/--card/--border/--text + 靛蓝），
  删掉它们的 :root 后必须把残留规则里的变量名映射到暖纸编辑体词表，
  否则 var() 全部解析失败。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "tts_erp_v2" / "templates" / "pages"
COMMON = ROOT / "tts_erp_v2" / "static" / "css" / "common.css"

# 灰蓝体系 → 暖纸编辑体（仅用于 enum-map.html / users.html 的残留规则）
VAR_REMAP = {
    "--bg": "--paper",
    "--card": "--paper",
    "--border": "--rule-soft",
    "--text": "--ink",
    "--muted": "--muted",  # 同名但值不同，映射后统一为 tokens.css 的值
}

LINKS = (
    "  <link rel=\"stylesheet\" href=\"../../static/css/tokens.css?v={{ css_version('tokens.css') }}\">\n"
    "  <link rel=\"stylesheet\" href=\"../../static/css/common.css?v={{ css_version('common.css') }}\">\n"
)

JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def normalize(sel: str) -> str:
    return re.sub(r"\s+", " ", sel).strip()


def common_selectors() -> set[str]:
    """common.css 里**顶层**（不在 @media 内）的选择器集合。"""
    css = COMMON.read_text(encoding="utf-8")
    out: set[str] = set()
    for sel, _start, _end in top_level_selectors(css):
        out.add(normalize(sel))
    return out


def top_level_selectors(css: str):
    """产出 (选择器文本, span起, span止)；span 覆盖整条规则含花括号。"""
    spans = []
    i, n, depth = 0, len(css), 0
    buf_start = 0
    while i < n:
        c = css[i]
        if c == "{":
            if depth == 0:
                sel = css[buf_start:i]
                rule_start = buf_start
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                spans.append((css[rule_start:i], rule_start, i + 1))
                buf_start = i + 1
        i += 1
    for sel, start, end in spans:
        s = sel.split("{", 1)[0]
        yield s, start, end


def protect(text: str):
    """把 Jinja 表达式换成占位符，返回 (文本, 还原函数)。"""
    store: list[str] = []

    def sub(m: re.Match) -> str:
        store.append(m.group(0))
        return f"\x00J{len(store) - 1}\x00"

    def restore(t: str) -> str:
        return re.sub(r"\x00J(\d+)\x00", lambda m: store[int(m.group(1))], t)

    return JINJA_RE.sub(sub, text), restore


def process(path: Path, shared: set[str], apply: bool) -> None:
    raw = path.read_text(encoding="utf-8")
    # 1) 插入共享样式表链接（若尚未存在）
    if "css/tokens.css" not in raw:
        anchor = '  <link rel="stylesheet" href="../../static/vendor/bootstrap.min.css">\n'
        assert anchor in raw, f"{path.name}: 找不到 bootstrap 链接锚点"
        raw = raw.replace(anchor, anchor + LINKS, 1)

    # 2) 处理 <style> 块
    def style_sub(m: re.Match) -> str:
        open_tag, css, close_tag = m.group(1), m.group(2), m.group(3)
        body, restore = protect(css)
        removed: list[str] = []
        drop: list[tuple[int, int]] = []
        for sel, start, end in top_level_selectors(body):
            name = normalize(sel)
            if not name or name.startswith("@"):
                continue  # @media 等留着
            if name in shared or name == ":root":
                drop.append((start, end))
                removed.append(name)
        for start, end in sorted(drop, reverse=True):
            body = body[:start] + body[end:]
        if removed:
            body = re.sub(r"\n{3,}", "\n\n", body)
        result = restore(body)
        if removed:
            print(f"    - {path.name}: 删除 {len(removed)} 条 -> {', '.join(removed[:8])}"
                  f"{' …' if len(removed) > 8 else ''}")
        return open_tag + result + close_tag

    raw = re.sub(r"(<style[^>]*>)(.*?)(</style>)", style_sub, raw, flags=re.S)

    # 3) 灰蓝体系残留变量名映射
    if path.name in {"enum-map.html", "users.html"}:
        for old, new in VAR_REMAP.items():
            raw = re.sub(rf"var\(\s*{re.escape(old)}\s*[,)]", lambda m, o=old, nn=new:
                         m.group(0).replace(o, nn), raw)
        raw = raw.replace("var(--bg)", "var(--paper)").replace("var(--card)", "var(--paper)")
        raw = raw.replace("var(--border)", "var(--rule-soft)").replace("var(--text)", "var(--ink)")
        print(f"    - {path.name}: 灰蓝变量名已映射到暖纸词表")

    if apply:
        path.write_text(raw, encoding="utf-8")
    else:
        print(f"    - {path.name}: （试运行未写盘）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    shared = common_selectors()
    print(f"common.css 顶层选择器 {len(shared)} 个")
    files = sorted(PAGES.glob("*.html"))
    print(f"待处理模板 {len(files)} 个（{'写盘' if args.apply else '试运行'}）")
    for f in files:
        process(f, shared, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
