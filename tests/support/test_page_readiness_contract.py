"""Guards for the canned-page readiness contract.

Three rules, each with a destructive self-check so the guard is proven to fail
when the thing it guards is broken:

``G1``  coverage — every slug the helper declares must have a contract, and
        every slug the browser suites drive must be declared. Dropping a
        contract must be red, otherwise deleting a criterion silently shrinks
        the protection.
``G2``  no retired DOM — the Jinja template ids must not come back as
        selectors, in the helper or in the React source. The count only looks
        at **code**: a docstring may mention the history, a selector may not
        use it.
``G3``  anchors name something real — every role anchor's accessible name
        must exist in the React source, so a typo like
        ``name="不存在的网格"`` is red without a browser.

The browser-level check (each slug's anchors really hit the canned page) lives
in ``tests/browser/test_page_readiness_contract.py`` — the browser fixtures
only exist under ``tests/browser/``.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from tests.support.page_readiness import CONTRACTS, SUPPORTED_SLUGS, Anchor

PROJECT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = PROJECT / "frontend" / "src"
HELPER = Path(__file__).with_name("page_readiness.py")

#: Ids of the Jinja template P6 retired. React never rendered them, so an
#: anchor that names one can never hit — that is what made the old helper's
#: alert-page branch permanently false.
RETIRED_DOM_IDS = (
    "#alert-page",
    "#alert-status",
    "#alert-rows",
    "#alert-cards",
    "#alert-summary",
    "#alert-freshness",
    "#total-spus",
    "#total-current-orders",
    "#total-previous-orders",
)


def _code_literals(source: str) -> list[str]:
    """Every string literal in the source that is not a docstring.

    A docstring may describe the retired DOM; a selector may not use it.
    """
    tree = ast.parse(source)
    docstrings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


def _retired_dom_hits(source: str) -> set[str]:
    """Retired DOM ids used as selectors in this source."""
    return {
        retired
        for literal in _code_literals(source)
        for retired in RETIRED_DOM_IDS
        if retired in literal
    }


def _missing_contracts(contracts: dict[str, Any], supported: frozenset[str]) -> set[str]:
    """Declared slugs without a contract."""
    return set(supported) - set(contracts)


def _suite_slugs() -> set[str]:
    """The slugs the browser suites drive through the shared helper.

    ``runninghub`` has its own readiness branch in the pilots, so the helper
    deliberately does not declare it.
    """
    from tests.render_support import PAGE_SLUGS
    from tts_erp_v2.accounts.pages import PAGES

    slugs = set(PAGE_SLUGS) | {page.page_id for page in PAGES}
    slugs.discard("runninghub")
    return slugs


def _frontend_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FRONTEND_SRC.rglob("*.ts"))
        + sorted(FRONTEND_SRC.rglob("*.tsx"))
    )


def _missing_source_literals(contracts: dict[str, tuple[Anchor, ...]], source: str) -> list[tuple[str, str]]:
    """Role anchors whose accessible name the React source does not contain."""
    missing: list[tuple[str, str]] = []
    for slug, anchors in contracts.items():
        for anchor in anchors:
            literal = anchor.source_literal
            if literal and literal not in source:
                missing.append((slug, literal))
    return missing


# ── G1 coverage ────────────────────────────────────────────────────────────


def test_every_declared_slug_has_a_contract() -> None:
    assert not _missing_contracts(CONTRACTS, SUPPORTED_SLUGS)


def test_every_suite_slug_is_declared() -> None:
    assert not _suite_slugs() - set(SUPPORTED_SLUGS)


def test_g1_notices_a_dropped_contract() -> None:
    """Destructive check: removing one contract must be reported."""
    broken = {slug: anchors for slug, anchors in CONTRACTS.items() if slug != "shops"}
    assert "shops" in _missing_contracts(broken, SUPPORTED_SLUGS)


# ── G2 no retired DOM ───────────────────────────────────────────────────────


def test_helper_has_no_retired_dom_selectors() -> None:
    assert not _retired_dom_hits(HELPER.read_text(encoding="utf-8"))


def test_react_source_has_no_retired_dom_selectors() -> None:
    hits: set[str] = set()
    for path in sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx")):
        hits |= _retired_dom_hits(path.read_text(encoding="utf-8"))
    assert not hits, f"React 源码里重新出现了已退役的 DOM: {sorted(hits)}"


def test_g2_notices_a_retired_selector() -> None:
    """Destructive check: a retired id used as a selector must be reported."""
    broken = 'def anchor(page):\n    return page.locator("#alert-rows")\n'
    assert "#alert-rows" in _retired_dom_hits(broken)


def test_g2_ignores_a_docstring_mention() -> None:
    """The history may be written down; only code may not use it."""
    harmless = (
        '"""The retired #alert-rows id must not come back."""\n'
        "def anchor(page):\n"
        '    return page.locator(".erp-page")\n'
    )
    assert not _retired_dom_hits(harmless)


# ── G3 anchors name something real ──────────────────────────────────────────


def test_role_anchors_name_something_the_page_renders() -> None:
    missing = _missing_source_literals(CONTRACTS, _frontend_source())
    assert not missing, f"锚点在 React 源码里不存在: {missing}"


def test_g3_notices_a_bogus_anchor() -> None:
    """Destructive check: a grid name the page never renders must be reported."""
    broken = {"shops": (Anchor(kind="role", value="grid", name="不存在的网格"),)}
    missing = _missing_source_literals(broken, _frontend_source())
    assert missing == [("shops", "不存在的网格")]
