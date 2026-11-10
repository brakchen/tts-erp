"""Positive canned-data readiness for layout/font checks, never error scenarios.

The contract table below is the single source of truth: every slug names the
**structural** anchors the page really renders, so "ready" never depends on
fixture text. P6 retired the Jinja templates, so anchors that name their DOM
(``#alert-page`` / ``#alert-status`` / ``#alert-rows``) are permanently false
and are banned by ``tests/support/test_page_readiness_contract.py``.

Two rules keep the table honest:

* An anchor must be something the **current owner** renders — the React page
  under ``frontend/src/pages/<slug>/``. HeroUI renders its tables with the
  accessible role ``grid`` (not ``table``).
* A fixture value (for example ``TEST_RENDER_COMPARE_0``) is a **data
  assertion** and belongs in the test that owns the data, never here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from tests.support.test_runtime import record_phase


@dataclass(frozen=True, slots=True)
class Anchor:
    """One anchor a canned page must really render.

    ``kind`` is ``role`` (accessible role + accessible name), ``text`` (visible
    text, optionally scoped to a role) or ``css`` (a selector).
    """

    kind: str
    value: str = ""
    name: str = ""
    within: tuple[str, str] | None = None

    def locator(self, page: Any) -> Any:
        if self.kind == "role":
            return page.get_by_role(self.value, name=self.name, exact=True)
        if self.kind == "text":
            scope = page
            if self.within is not None:
                role, name = self.within
                scope = page.get_by_role(role, name=name, exact=True)
            return scope.get_by_text(self.value, exact=True).first
        if self.kind == "css":
            return page.locator(self.value).first
        raise AssertionError(f"Unknown anchor kind: {self.kind}")

    @property
    def source_literal(self) -> str | None:
        """The string that must exist in the React source, if any.

        Only role anchors have one: the accessible name comes from an
        ``aria-label`` in ``frontend/src``. Fixture text and library-generated
        class names have no source literal, so they are not checked here.
        """
        if self.kind == "role":
            return self.name
        return None


def _role(role: str, name: str) -> Anchor:
    return Anchor(kind="role", value=role, name=name)


def _text(value: str, within: tuple[str, str] | None = None) -> Anchor:
    return Anchor(kind="text", value=value, within=within)


def _css(selector: str) -> Anchor:
    return Anchor(kind="css", value=selector)


#: Slug → the anchors that prove the page really rendered.
CONTRACTS: dict[str, tuple[Anchor, ...]] = {
    "ad-daily": (_role("grid", "广告日明细"),),
    "dashboard": (
        _role("region", "SPU 总数"),
        _text("1234", within=("region", "SPU 总数")),
        _role("grid", "已注册店铺"),
        _role("grid", "数据同步状态"),
        _text("API 正常"),
    ),
    "digital-humans": (_text("当前没有档案。"),),
    "enum-map": (_role("grid", "枚举映射"),),
    "focused-spus": (
        _css(".tabulator-row"),
        _css('#data-freshness [data-source="ads"]'),
    ),
    "intercept-configs": (_role("grid", "拦截配置"),),
    "intercept-requests": (_role("grid", "拦截记录"),),
    "intercept-stats": (
        _role("grid", "每日请求（UTC）"),
        _css(".uplot canvas"),
    ),
    "manual-costs": (_role("grid", "SPU 成本工作台"),),
    "media-assets": (_text("资产库还没有素材包。"),),
    "runtime-configs": (
        _role("grid", "运行配置"),
        _text("TEST_RENDER_RUNTIME", within=("grid", "运行配置")),
    ),
    "shops": (_role("grid", "店铺接入列表"),),
    "spu-profit-deterioration": (_role("grid", "SPU 两期盈利对比"),),
    "spu-roi": (
        _css(".tabulator-row"),
        _css('#data-freshness [data-source="ads"]'),
    ),
    "sync-jobs": (_role("grid", "定时任务"),),
    "users": (_role("grid", "用户列表"),),
    "video-publish": (
        _text("TEST_render_device", within=("region", "发布配置")),
        _text("当前没有运行中或清理中的发布任务。"),
        _role("grid", "视频发布任务"),
    ),
}

#: The slugs this helper supports — the declaration, kept apart from
#: ``CONTRACTS`` on purpose: dropping a contract must be detected (guard G1)
#: instead of silently shrinking the protection. ``runninghub`` is deliberately
#: absent: the browser suite has its own readiness branch for that page and
#: the old helper branch was stale text that could never hit.
SUPPORTED_SLUGS: frozenset[str] = frozenset(
    {
        "ad-daily",
        "dashboard",
        "digital-humans",
        "enum-map",
        "focused-spus",
        "intercept-configs",
        "intercept-requests",
        "intercept-stats",
        "manual-costs",
        "media-assets",
        "runtime-configs",
        "shops",
        "spu-profit-deterioration",
        "spu-roi",
        "sync-jobs",
        "users",
        "video-publish",
    }
)


def wait_canned_page(page: Any, slug: str) -> None:
    """Wait until the canned page for ``slug`` really rendered.

    Raises ``AssertionError`` for a slug without a contract instead of
    silently waiting for an element that will never appear.
    """
    started = time.monotonic()
    anchors = CONTRACTS.get(slug)
    if anchors is None:
        raise AssertionError(f"Missing canned readiness contract: {slug}")
    page.locator("#frontend-root .erp-page").wait_for()
    for anchor in anchors:
        anchor.locator(page).wait_for()
    page.evaluate("document.fonts.ready")
    record_phase("browser_ready", time.monotonic() - started)
