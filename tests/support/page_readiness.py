"""Positive canned-data readiness for layout/font checks, never error scenarios."""
from __future__ import annotations

import time
from typing import Any

from tests.support.test_runtime import record_phase

TABLES = {
    "ad-daily": "广告日明细", "manual-costs": "SPU 成本工作台", "shops": "店铺接入列表",
    "enum-map": "枚举映射", "sync-jobs": "定时任务", "users": "用户列表",
    "intercept-configs": "拦截配置", "intercept-requests": "拦截记录",
}


def wait_canned_page(page: Any, slug: str) -> None:
    started = time.monotonic()
    if slug == "spu-profit-deterioration" and page.locator("#alert-page").count():
        page.locator('#alert-status[data-kind="ready"]').wait_for()
        page.locator('#alert-rows [data-spu-pk]').first.wait_for(state="attached")
    elif slug == "runninghub":
        # This page has its own root layout; require its displayed GET facts.
        page.get_by_text("2111762608670990337 · plus · 并发 5 · 已校验版本", exact=True).wait_for()
        page.get_by_role("checkbox", name="TEST_RENDER_HUMAN / A · TEST_ren", exact=True).wait_for()
        page.get_by_text("没有就绪图片。请先到资产管理上传服装图。", exact=True).wait_for()
        assert page.get_by_role("alert").count() == 0
    else:
        page.locator("#frontend-root .erp-page").wait_for()
        if slug in TABLES:
            page.get_by_role("grid", name=TABLES[slug], exact=True).wait_for()
        elif slug in {"spu-roi", "focused-spus"}:
            page.locator(".tabulator-row").first.wait_for()
            page.locator('#data-freshness [data-source="ads"]').wait_for()
        elif slug == "dashboard":
            page.get_by_role("region", name="SPU 总数", exact=True).get_by_text("1234", exact=True).wait_for()
            page.get_by_role("grid", name="已注册店铺", exact=True).wait_for()
            page.get_by_role("grid", name="数据同步状态", exact=True).wait_for()
            page.get_by_text("API 正常", exact=True).wait_for()
        elif slug == "intercept-stats":
            page.get_by_role("grid", name="每日请求（UTC）", exact=True).wait_for()
            page.locator(".uplot canvas").first.wait_for()
        elif slug == "runtime-configs":
            page.get_by_role("grid", name="运行配置", exact=True).get_by_text("TEST_RENDER_RUNTIME", exact=True).first.wait_for()
        elif slug == "spu-profit-deterioration":
            page.get_by_text("TEST_RENDER_COMPARE_0", exact=True).wait_for()
        elif slug == "media-assets":
            # The React page opens on the 素材包 tab (index.tsx picks it unless
            # ?view=all|videos), whose empty state is 资产库还没有素材包。
            # The 全部素材 tab carries 没有符合条件的素材。, so wait for the
            # tab that is actually on screen.
            page.get_by_text("资产库还没有素材包。", exact=True).wait_for()
        elif slug == "digital-humans":
            page.get_by_text("当前没有档案。", exact=True).wait_for()
        elif slug == "video-publish":
            page.get_by_role("region", name="发布配置", exact=True).get_by_text("TEST_render_device", exact=True).wait_for()
            page.get_by_text("当前没有运行中或清理中的发布任务。", exact=True).wait_for()
            page.get_by_role("grid", name="视频发布任务", exact=True).wait_for()
        else:
            raise AssertionError(f"Missing canned readiness contract: {slug}")
    page.evaluate("document.fonts.ready")
    record_phase("browser_ready", time.monotonic() - started)
