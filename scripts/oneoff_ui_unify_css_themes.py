#!/usr/bin/env python3
"""把 5 个独立样式表的主题统一到共享 token 体系（暖纸编辑体）。

用法（lane worktree 根目录）：
    python3 scripts/oneoff_ui_unify_css_themes.py            # 试运行
    python3 scripts/oneoff_ui_unify_css_themes.py --apply    # 写盘

原则：
* 令牌名按**语义**对齐，不是机械改名。青色 --signal → 陶土橙 --accent 是有意的
  品牌统一，不是 bug。
* 各文件自己的 :root 一律删掉，令牌统一由 tokens.css 提供（这些 css 经 <link>
  加载在 tokens.css 之后）。
* surface 色（淡强调底、亮/暗纸面）tokens.css 没有对应项，用 color-mix 从规范
  令牌派生，避免再养一套并行色板。
* spu-roi.css 的 --bs-* 桥接块是 Bootstrap 变量覆盖，功能性的，保留。
* border-radius 统一 0；但 50%/999px 是圆形/胶囊（头像、状态点），保留。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "tts_erp_v2" / "static" / "css"

# 语义映射：旧令牌 -> 新令牌表达式
VAR_MAP = {
    # ad-daily.css：青色信号体系 → 陶土橙暖纸体系
    "--signal": "var(--accent)",
    "--signal-bright": "var(--accent-deep)",
    "--warning": "var(--warn)",
    # runtime-configs.css：--rc-* 命名空间 → 规范令牌
    "--rc-ink": "var(--ink)",
    "--rc-muted": "var(--muted)",
    "--rc-paper": "var(--paper)",
    "--rc-panel": "var(--paper-deep)",
    "--rc-rule": "var(--rule)",
    "--rc-accent": "var(--accent)",
    "--rc-danger": "var(--danger)",
}

# surface 色：tokens.css 无对应项，用 color-mix 从规范令牌派生
SURFACE = {
    "--signal-pale": "color-mix(in srgb, var(--accent) 16%, var(--paper))",
    "--rc-accent-soft": "color-mix(in srgb, var(--accent) 14%, var(--paper))",
    "--paper-hi": "color-mix(in srgb, var(--paper) 55%, #ffffff)",
    "--rule-dark": "color-mix(in srgb, var(--ink) 45%, var(--rule))",
}

# tokens.css 已定义、不该再在页面 css 里重复的令牌
CANONICAL = {
    "--paper", "--paper-deep", "--ink", "--ink-soft", "--muted", "--rule",
    "--rule-soft", "--accent", "--accent-deep", "--ok", "--warn", "--danger",
    "--mono", "--sans", "--serif",
}


def rewrite_var_refs(src: str) -> str:
    """把 var(--old) / var(--old, fallback) 改写成 var(--new)。"""
    for old, new in VAR_MAP.items():
        src = re.sub(
            rf"var\(\s*{re.escape(old)}\s*(?:,[^)]*)?\)",
            new,
            src,
        )
    return src


def rebuild_root(css: str, filename: str) -> str:
    """删掉 :root 块，只把非规范的 surface 令牌改成派生值并留在文件顶部。"""

    def repl(m: re.Match) -> str:
        body = m.group(1)
        keep: list[str] = []
        for name, expr in SURFACE.items():
            if re.search(rf"{re.escape(name)}\s*:", body):
                keep.append(f"  {name}: {expr};")
        # --bs-* 是 Bootstrap 变量桥接（spu-roi.css），功能性的，原样保留
        bs = [
            line for line in body.splitlines()
            if line.strip().startswith("--bs-")
        ]
        if bs:
            keep = ["/* Bootstrap 变量桥接：把 Bootstrap 的语义变量指到暖纸令牌上。 */"] + bs + keep
        if not keep:
            return ""
        note = (
            "\n/* 页面局部 surface 色：tokens.css 只收语义令牌，这里从规范令牌派生，\n"
            " * 不另养并行色板。 */\n:root {\n" + "\n".join(keep) + "\n}\n"
        )
        return note

    return re.sub(r":root\s*\{(.*?)\}", repl, css, count=1, flags=re.S)


def fix_radius(css: str) -> str:
    css = re.sub(r"border-radius:\s*3px", "border-radius: var(--radius)", css)
    css = re.sub(r"border-radius:\s*0\.2rem", "border-radius: var(--radius)", css)
    return css


def process(name: str, apply: bool) -> None:
    p = CSS / name
    src = p.read_text(encoding="utf-8")
    before = len(src)
    src = rebuild_root(src, name)
    src = rewrite_var_refs(src)
    src = fix_radius(src)
    # 去掉 tokens.css 已定义的重复声明残留（若 :root 里混有规范令牌但被上面整块删掉则不会出现）
    print(f"  {name}: {before} -> {len(src)} 字节")
    if apply:
        p.write_text(src, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    targets = ["ad-daily.css", "sync-jobs.css", "runtime-configs.css",
               "focused-spus.css", "spu-roi.css"]
    print(f"{'写盘' if args.apply else '试运行'}：{len(targets)} 个文件")
    for t in targets:
        process(t, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
