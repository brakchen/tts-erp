"""Compiled page interaction/layout using inert API responses, no paid tasks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from render_support import overflow_report
from starlette.requests import Request

from tts_erp_v2.api.v2.frontend_pages import render_frontend_page

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@pytest.mark.parametrize("width", [390, 1440, 2560])
@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_runninghub_admin_forms_and_unknown_recovery_layout(
    frontend_browser_renderer, width, prefix
):
    page = frontend_browser_renderer.open(prefix + "/v2/pages/runninghub", width=width)
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = render_frontend_page(
        Request(
            {
                "type": "http",
                "root_path": prefix,
                "path": "/v2/pages/runninghub",
                "api_key_role": "admin",
            }
        ),
        "runninghub",
    ).body
    long_name = "TEST_服装图片_长名称与图片变体保持独立_" * 3

    def mock(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v2/pages/runninghub"):
            route.fulfill(status=200, content_type="text/html", body=html)
            return
        if "/v2/media/assets" in path:
            value = {
                "items": [
                    {
                        "asset_id": "123",
                        "kind": "image",
                        "filename": long_name + ".png",
                        "content_type": "image/png",
                        "size_bytes": 16,
                        "tags": [],
                        "status": "ready",
                        "created_at": "2026-11-07T00:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 24,
                "offset": 0,
            }
        elif path.endswith("/digital-humans"):
            value = []
        elif path.endswith("/config"):
            value = {
                "credentialRef": "env:RUNNINGHUB_API_KEY",
                "credentialConfigured": False,
                "workflowId": "2111762608670990337",
                "instanceType": "plus",
                "concurrency": 5,
                "workflowVersionId": "TEST_version",
            }
        elif path.endswith("/templates"):
            value = {
                "items": [
                    {
                        "id": "TEST_template",
                        "humanId": "1",
                        "humanName": "TEST_人物",
                        "name": "A",
                        "clips": [],
                    }
                ]
            }
        elif path.endswith("/batches"):
            value = {"items": []}
        else:
            route.continue_()
            return
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(value)
        )

    page.route("**/v2/**", mock)
    page.reload()
    page.get_by_role("heading", level=1, name="换装视频", exact=True).wait_for()
    page.get_by_text("管理配置", exact=True).click()
    page.get_by_label("凭据引用", exact=True).wait_for()
    page.get_by_text("配置人物模板的六段视频", exact=True).click()
    page.get_by_label("默认款式编号", exact=True).fill("TEST_style")
    checkbox = page.get_by_role("checkbox", name=long_name + ".png", exact=True)
    checkbox.focus()
    checkbox.press("Space")
    assert checkbox.is_checked()
    page.get_by_label("SKU " + long_name + ".png", exact=True).wait_for()
    assert page.get_by_role("button", name="保存批次", exact=True).is_disabled()
    assert not errors, errors
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    screenshots = Path(__file__).resolve().parents[2] / "frontend/.cache/screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=str(
            screenshots / f"TEST_runninghub_{width}_{prefix.strip('/') or 'direct'}.png"
        ),
        full_page=True,
    )
