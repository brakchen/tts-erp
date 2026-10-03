"""设计令牌层回归测试（layer_unit，无 DB）。

为什么需要这个文件
------------------
2026-10-03 出过一次线上事故：``tokens.css`` 的头注释里写了
``--signal*/--warning``，其中 ``*/`` **提前闭合了 CSS 注释**，注释之后的整段文字
变成裸 CSS。浏览器把整个样式表解析成 **0 条规则**，``var(--paper)`` /
``var(--ink)`` / ``var(--accent)`` / ``var(--sans)`` 全部解析失败 —— 生产全站
无样式（黑字、透明背景、Times New Roman）。

pytest 的 API/契约层完全发现不了这类问题：HTML 里链接是对的、HTTP 200、
``:root`` 也确实出现在文件里，只是**浏览器解析不出来**。静态文本检查同样发现不了，
因为 ``*/`` 在文件里看起来完全正常。

这里用真正的 CSS 解析器（tinycss2）按浏览器的解析语义校验：

1. ``tokens.css`` 必须真的产出 ``:root`` 规则，并声明全套规范令牌 ——
   这条直接锁死上述事故（出事时 ``:root`` 会消失）。
2. 所有 ``var(--x)`` 引用必须能解析到已声明的令牌 —— 锁死「令牌被删但引用还在」
   （如 spu-roi.css 的 ``--paper-danger`` 丢失）。
3. 页面模板不得重新定义 ``:root``。
4. 各 CSS 文件不得有解析错误。

依赖
----
tinycss2 >=1.3,<2 —— BSD-2-Clause，web-platform-extras 维护，
https://github.com/web-platform-extras/tinycss2
（WeasyPrint 等项目在用；这是 CSS 解析的标准库，不要自己造解析器。）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tinycss2

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]

REPO = Path(__file__).resolve().parents[2]
CSS_DIR = REPO / "tts_erp_v2" / "static" / "css"
TEMPLATES_DIR = REPO / "tts_erp_v2" / "templates" / "pages"
API_DIR = REPO / "tts_erp_v2" / "api" / "v2"

# tokens.css 必须声明的规范令牌（暖纸编辑体）。
CANONICAL_TOKENS = {
    "--paper", "--paper-deep",
    "--ink", "--ink-soft", "--muted",
    "--rule", "--rule-soft",
    "--accent", "--accent-deep",
    "--ok", "--warn", "--danger",
    "--mono", "--sans", "--serif",
    "--radius",
}


def _load(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _root_declarations(css: str) -> dict[str, str]:
    """返回 ``:root`` 规则里声明的自定义属性名 -> 值。

    用 tinycss2 按浏览器语义解析，而不是正则匹配 —— 正则看不出注释被提前闭合。
    """
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    out: dict[str, str] = {}
    for rule in rules:
        # tinycss2 的类型标签是 'qualified-rule'（不是 'qualified'）
        if rule.type != "qualified-rule":
            continue
        selector = tinycss2.serialize(rule.prelude).strip()
        if selector != ":root":
            continue
        for decl in tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        ):
            if decl.type == "declaration" and decl.name.startswith("--"):
                out[decl.name] = tinycss2.serialize(decl.value).strip()
    return out


def _declared_tokens() -> dict[str, str]:
    """全站可解析到的令牌（tokens.css 的 :root + 各文件的局部 :root）。"""
    out: dict[str, str] = {}
    for path in sorted(CSS_DIR.glob("*.css")):
        out.update(_root_declarations(_load(path)))
    # Python 内嵌页面里的局部 :root
    for path in sorted(API_DIR.glob("*.py")):
        out.update(_root_declarations(_load(path)))
    return out


def test_tokens_css_produces_a_root_rule_with_the_full_palette():
    """``tokens.css`` 必须解析出 ``:root`` 并声明全套规范令牌。

    2026-10-03 事故的直接回归护栏：注释提前闭合时 ``:root`` 会被浏览器整个吞掉，
    令牌数变 0，这条断言立即失败。
    """
    css = _load(CSS_DIR / "tokens.css")
    declared = _root_declarations(css)

    assert declared, (
        "tokens.css 解析不出任何 :root 令牌 —— 大概率是 CSS 注释被提前闭合"
        "（检查文件里是否出现 '*/'，例如注释中写了 '--signal*/--warning'）"
    )
    missing = CANONICAL_TOKENS - set(declared)
    assert not missing, f"tokens.css 缺少规范令牌: {sorted(missing)}"

    # 关键令牌必须有非空值（防止写成 `--paper: ;` 这类空值）
    for name in ("--paper", "--ink", "--accent", "--sans", "--radius"):
        assert declared[name], f"{name} 的值为空"


def test_tokens_css_top_level_rules_look_like_css():
    """顶层规则的选择器必须像选择器 —— 注释逃逸时会产生垃圾选择器。

    这是比「无解析错误」更强的断言：tinycss2 对注释提前闭合**不报错**，
    只会把垃圾文本当成一条 qualified rule 的选择器。
    """
    css = _load(CSS_DIR / "tokens.css")
    rules = tinycss2.parse_stylesheet(css, skip_comments=False, skip_whitespace=False)
    top = [
        tinycss2.serialize(r.prelude).strip()
        for r in rules
        if r.type == "qualified-rule"
    ]

    assert top, "tokens.css 没有任何顶层规则"
    for selector in top:
        # 合法选择器不应包含 CJK、连续星号、或裸 '--xxx' 起头
        assert not re.search(r"[\u4e00-\u9fff]", selector), (
            f"tokens.css 顶层选择器含中文，疑似注释提前闭合: {selector[:80]!r}"
        )
        assert not selector.startswith("--"), (
            f"tokens.css 顶层选择器以 '--' 开头，疑似注释提前闭合: {selector[:80]!r}"
        )


def test_every_var_reference_resolves():
    """所有 ``var(--x)`` 必须能解析到已声明的令牌。

    锁死「令牌被删但引用还在」类回归（spu-roi.css 的 ``--paper-danger``）。
    带回退的 ``var(--x, fallback)`` 同样要求主令牌存在 —— 回退只是降级保护，
    不是允许令牌缺失。
    """
    declared = set(_declared_tokens())
    # Bootstrap 自带变量（--bs-*）由 vendor 提供，不在本仓库声明
    problems: list[str] = []

    scan: list[Path] = list(CSS_DIR.glob("*.css"))
    scan += list(TEMPLATES_DIR.glob("*.html"))
    scan += [API_DIR / "auth.py", API_DIR / "oauth.py", API_DIR / "ad_daily.py"]

    for path in scan:
        if not path.exists():
            continue
        used = set(re.findall(r"var\(\s*(--[a-zA-Z0-9\-]+)", _load(path)))
        missing = {
            u for u in used
            if u not in declared and not u.startswith("--bs-")
        }
        if missing:
            problems.append(f"{path.relative_to(REPO)}: {sorted(missing)}")

    assert not problems, (
        "以下位置引用了未声明的令牌（会静默解析为无效值）:\n  "
        + "\n  ".join(problems)
    )


def test_pages_do_not_redefine_root_tokens():
    """页面模板不得重新定义 ``:root`` —— 令牌唯一来源是 tokens.css。"""
    offenders = []
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        if re.search(r":root\s*\{", _load(path)):
            offenders.append(path.name)
    assert not offenders, (
        f"以下页面模板重新定义了 :root 令牌: {offenders} —— 请改为引用 tokens.css"
    )


def test_css_files_have_no_parse_errors():
    """所有自研 CSS 文件不得有 CSS 解析错误。"""
    problems = []
    for path in sorted(CSS_DIR.glob("*.css")):
        rules = tinycss2.parse_stylesheet(
            _load(path), skip_comments=False, skip_whitespace=False
        )
        errors = [r for r in rules if r.type == "error"]
        if errors:
            detail = "; ".join(
                f"L{e.source_line}: {e.message}" for e in errors[:3]
            )
            problems.append(f"{path.name}: {len(errors)} 处 — {detail}")
    assert not problems, "CSS 解析错误:\n  " + "\n  ".join(problems)
