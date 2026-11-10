"""The upstream ``page_size`` defaults are pinned by the upstream's measured limits.

2026-11-10, live TikTok 202309 API, both shops (``7494763368967603447`` and
``7494864868604150914``), read-only, through the jobs' own ``_walk_pages``:

===========================  ==========  =====================================
endpoint                     page_size   measured behaviour
===========================  ==========  =====================================
/finance/202309/payments     50          **FIXED CURSOR** — page 2 returns the
                                         same 244-char ``next_page_token``
                                         *and* the same 50 payments ⇒ the walk
                                         can never advance
/finance/202309/payments     100         1 page, 76 payments, no token ⇒ ends
/finance/202309/statements   50          normal pagination (3 pages, 112 rows,
                                         0 duplicates)
/order/202309/orders/search  50          normal pagination (8+ pages, 400 rows,
                                         0 duplicates, tokens keep changing)
/product/202309/products/... 50          normal pagination (6 pages, 259 rows,
                                         0 duplicates)
/return_refund/.../returns   50          normal pagination
/return_refund/.../returns   100         **REJECTED upstream** — 98001004
                                         "Value Out Of Range. The parameter
                                         `page_size` exceeds the allowed range
                                         (10 to 50)"
/return_refund/.../cancels   100         same rejection (98001004)
===========================  ==========  =====================================

So exactly **one** default moves: finance 50 → 100. Everything else stays:

* ``after_sales`` must stay at 50 — 100 is rejected by the upstream and would
  fail every tick with 98001004;
* ``orders`` is already 100 and paginates normally;
* ``products`` paginates normally at 50 (nothing to fix);
* ``logistics`` has no upstream page walk at all — its ``page_size`` is a
  *local* cap on how many orders one tick processes, not an upstream page size.

These tests pin that decision: a later edit cannot silently move a job to a
page_size the upstream rejects, nor let a caller override the default behind
the job's back.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.domain_finance,
    pytest.mark.domain_commerce,
    pytest.mark.domain_after_sales,
    pytest.mark.layer_unit,
]

from tts_erp_v2.jobs.tiktok import after_sales as after_sales_job
from tts_erp_v2.jobs.tiktok import finance as finance_job
from tts_erp_v2.jobs.tiktok import logistics as logistics_job
from tts_erp_v2.jobs.tiktok import orders as orders_job
from tts_erp_v2.jobs.tiktok import products as products_job

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Jobs that page an upstream endpoint → the page_size each one must use.
_EXPECTED_DEFAULTS: dict[str, int] = {
    # /finance/202309/payments freezes its cursor below 100 (measured).
    "finance": 100,
    # Already 100; 50..100 all paginate normally.
    "orders": 100,
    # 50 paginates normally — nothing to fix, keep the smaller pages.
    "products": 50,
    # The upstream rejects page_size > 50 (98001004, allowed range 10..50).
    "after_sales": 50,
}

#: Jobs that expose a ``page_size`` parameter at all. ``logistics`` is the
#: odd one out: its page_size is a local per-tick cap, not an upstream page
#: size, so it is pinned separately below.
_EXPECTED_PAGE_SIZE_JOBS = {
    "after_sales",
    "finance",
    "logistics",
    "orders",
    "products",
}

#: Jobs that walk upstream pages (``_walk_pages``).
_EXPECTED_PAGING_JOBS = {"after_sales", "finance", "orders", "products"}


def _tiktok_job_modules() -> dict[str, object]:
    """Import every ``tts_erp_v2/jobs/tiktok/*.py`` module by its file stem."""
    import importlib

    package_dir = Path(finance_job.__file__).resolve().parent
    modules: dict[str, object] = {}
    for path in sorted(package_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        modules[path.stem] = importlib.import_module(
            f"tts_erp_v2.jobs.tiktok.{path.stem}"
        )
    return modules


def _page_size_override_call_sites() -> list[str]:
    """Every ``<anything>.run(..., page_size=...)`` call site in the package.

    The scheduler calls ``mod.run(session, proxy_call=..., shop_id=...)``
    *without* ``page_size``, so each job's own default is what production
    uses. A caller that passes ``page_size`` would silently override the
    value this module pins — that must stay empty.
    """
    hits: list[str] = []
    for root in (_REPO_ROOT / "tts_erp_v2", _REPO_ROOT / "scripts"):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "run":
                    continue
                if any(kw.arg == "page_size" for kw in node.keywords):
                    hits.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
    return hits


# ─── 位置断言：默认值在哪儿、是多少 ─────────────────────────────────────


@pytest.mark.parametrize(
    "module,expected",
    [
        (finance_job, 100),
        (orders_job, 100),
        (products_job, 50),
        (after_sales_job, 50),
    ],
    ids=["finance", "orders", "products", "after_sales"],
)
def test_upstream_paging_job_defaults_to_the_measured_page_size(
    module, expected
) -> None:
    """位置断言：page_size 默认值就落在各作业 ``run()`` 的签名上。"""
    default = inspect.signature(module.run).parameters["page_size"].default
    assert default == expected, (
        f"{module.__name__}.run() 的 page_size 默认值应为 {expected}，实际 {default}"
    )


def test_finance_default_is_100_because_the_upstream_cursor_freezes_below_100() -> None:
    """位置断言：finance 的默认值就是 100（唯一需要改的作业）。"""
    default = inspect.signature(finance_job.run).parameters["page_size"].default
    assert default == 100
    # 说明必须留在代码里，否则下一个人会把 100 改回 50。
    doc = inspect.getdoc(finance_job.run) or ""
    assert "page_size" in doc


# ─── 数量枚举：哪些作业有 page_size、哪些作业真的翻上游页 ────────────────


def test_exactly_these_jobs_expose_a_page_size_parameter() -> None:
    """数量枚举：带 page_size 参数的作业集合被钉死（新增作业必须先决策）。"""
    found = {
        name
        for name, module in _tiktok_job_modules().items()
        if hasattr(module, "run")
        and "page_size" in inspect.signature(module.run).parameters
    }
    assert found == _EXPECTED_PAGE_SIZE_JOBS


def test_exactly_these_jobs_walk_upstream_pages() -> None:
    """数量枚举：真正翻上游页的作业集合被钉死。"""
    found = {
        name
        for name, module in _tiktok_job_modules().items()
        if hasattr(module, "_walk_pages")
    }
    assert found == _EXPECTED_PAGING_JOBS


# ─── 分支覆盖：默认值不会被别的入口覆盖 ─────────────────────────────────


def test_no_caller_overrides_the_page_size_default() -> None:
    """分支覆盖：没有任何调用点传 page_size，默认值就是线上实际值。"""
    assert _page_size_override_call_sites() == []


def test_logistics_page_size_is_a_local_cap_not_an_upstream_page_size() -> None:
    """分支覆盖：logistics 不翻上游页，它的 page_size 是本地每 tick 上限。"""
    assert not hasattr(logistics_job, "_walk_pages")
    default = inspect.signature(logistics_job.run).parameters["page_size"].default
    assert default == 100
    # 上游调用不带 page_size：它是本 tick 处理订单数的上限。
    source = inspect.getsource(logistics_job.run)
    assert "TARGET_LIMIT" in source
