"""Compiled RunningHub workbench layout and controls using inert API responses."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from render_support import overflow_report
from starlette.requests import Request

from tts_erp_v2.api.v2.frontend_pages import render_frontend_page

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def image_asset(asset_id: str, filename: str, color: str, background: str) -> dict:
    shirt = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">'
        f'<rect width="200" height="80" fill="{background}"/>'
        '<path d="M86 12 72 17 61 32l12 10 9-5v29h36V37l9 5 12-10-11-15-14-5'
        f'q-14 12-28 0z" fill="{color}" stroke="#456052" stroke-width=".6"/></svg>'
    )
    return {
        "asset_id": asset_id,
        "kind": "image",
        "filename": filename,
        "content_type": "image/png",
        "size_bytes": 16,
        "tags": [],
        "status": "ready",
        "created_at": "2026-11-07T00:00:00Z",
        "url": "data:image/svg+xml," + quote(shirt),
    }


@pytest.mark.parametrize("width", [390, 1440, 2560])
@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_runninghub_workbench_matches_prototype(frontend_browser_renderer, width, prefix):
    page = frontend_browser_renderer.open(prefix + "/v2/pages/runninghub", width=width)
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = render_frontend_page(
        Request({
            "type": "http", "root_path": prefix, "path": "/v2/pages/runninghub",
            "api_key_role": "admin",
        }),
        "runninghub",
    ).body
    long_name = "TEST_服装图片_长名称与图片变体保持独立_" * 3 + ".png"
    images = [
        image_asset("123", "TEST_奶油白短袖.png", "#faf2dd", "#eee7d7"),
        image_asset("124", "TEST_苔绿短袖.png", "#7a9578", "#e1e9dc"),
        image_asset("125", long_name, "#6e879d", "#e0e7ed"),
    ]
    templates = [
        {
            "id": "TEST_template_" + template, "humanId": str(i),
            "humanName": person, "name": template,
            "clips": [
                {"assetId": str(100 + clip), "filename": f"TEST_{clip}.mp4"}
                for clip in range(6)
            ],
        }
        for i, (person, template) in enumerate([("TEST_林夏", "A"), ("TEST_安娜", "B")], 1)
    ]
    batch = {
        "id": "TEST_024", "mode": "live", "status": "ACTIVE", "error": None,
        "total": 12, "counts": {"SUCCEEDED": 7, "QUEUED": 5},
        "groups": [{"path": "TEST_style/A/TEST_林夏/TEST_奶油白短袖", "total": 6, "succeeded": 4}],
        "costs": {}, "unknownCostTasks": 12,
    }
    empty = False

    def mock(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/v2/pages/runninghub"):
            route.fulfill(status=200, content_type="text/html", body=html)
            return
        if "/v2/media/assets" in path:
            value = {"items": [] if empty else images, "total": 0 if empty else 3, "limit": 6, "offset": 0}
        elif path.endswith("/digital-humans"):
            value = []
        elif path.endswith("/config"):
            value = {
                "credentialRef": "env:RUNNINGHUB_STUDIO_API_KEY", "credentialConfigured": True,
                "workflowId": "2111762608670990337", "instanceType": "plus", "concurrency": 5,
                "workflowVersionId": "TEST_version",
            }
        elif path.endswith("/templates"):
            value = {"items": [] if empty else templates}
        elif path.endswith("/batches"):
            value = {"items": [] if empty else [batch]}
        elif path.endswith("/batches/TEST_024"):
            value = batch
        elif path.endswith("/batches/TEST_024/jobs"):
            value = {"items": []}
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

    # Keep plan preview entirely in the page: the compiled browser fixture
    # rejects network writes even when a Playwright route would fulfill them.
    plan_path = json.dumps(prefix + "/v2/runninghub/plan")
    page.add_init_script(
        f"""(() => {{
          const fetchNetwork = window.fetch.bind(window);
          window.fetch = (input, options) =>
            input === {plan_path} && options?.method === 'POST'
              ? Promise.resolve(Response.json({{ total: 12, groups: 2, paths: ['TEST_style/A/01.mp4'] }}))
              : fetchNetwork(input, options);
        }})()"""
    )
    page.route("**/v2/**", mock)
    page.reload()
    page.get_by_role("heading", level=1, name="换装视频", exact=True).wait_for()
    create = page.get_by_role("region", name="创建批次", exact=True)
    plan = page.get_by_role("region", name="生成计划", exact=True)
    records = page.get_by_role("region", name="批次记录", exact=True)
    page.get_by_role("progressbar", name="成片进度 TEST_024").wait_for()
    assert not page.get_by_label("生成模式").count()
    assert not page.get_by_role("checkbox", name="仅跑首段").is_checked()
    assert not page.get_by_label("凭据引用", exact=True).is_visible()
    assert not page.get_by_label("第 6 段", exact=True).is_visible()
    create_box, plan_box, record_box = (item.bounding_box() for item in (create, plan, records))
    if width == 390:
        assert plan_box["y"] >= create_box["y"] + create_box["height"]
    else:
        assert plan_box["x"] >= create_box["x"] + create_box["width"]
        assert abs(create_box["y"] - plan_box["y"]) <= 2
    assert record_box["y"] >= max(create_box["y"] + create_box["height"], plan_box["y"] + plan_box["height"])

    page.get_by_role("button", name="工作流配置").click()
    config = page.get_by_role("dialog", name="工作流配置")
    assert config.get_by_label("凭据引用", exact=True).input_value() == "env:RUNNINGHUB_STUDIO_API_KEY"
    config.get_by_role("button", name="关闭弹窗").click()
    page.get_by_role("button", name="配置六段模板").click()
    template_dialog = page.get_by_role("dialog", name="配置人物模板")
    template_dialog.get_by_label("第 6 段", exact=True).wait_for()
    template_dialog.get_by_role("button", name="关闭弹窗").click()

    page.get_by_label("默认款式编号", exact=True).fill("TEST_style")
    page.get_by_text(images[0]["filename"], exact=True).click()
    assert page.get_by_role("checkbox", name=images[0]["filename"], exact=True).is_checked()
    checkbox = page.get_by_role("checkbox", name=long_name, exact=True)
    checkbox.focus()
    checkbox.press("Space")
    assert checkbox.is_checked()
    template_card = page.get_by_role("checkbox", name="TEST_林夏 · 模板 A", exact=True)
    template_card.focus()
    template_card.press("Space")
    page.get_by_role("button", name="预览计划").click()
    review = page.get_by_role("dialog", name="确认生成计划")
    review.get_by_text("服务端计划：12 段成片 / 2 组").wait_for()
    review.get_by_role("button", name="返回修改").click()
    page.get_by_role("button", name="批次详情 TEST_024").click()
    detail = page.get_by_role("dialog", name="批次详情")
    detail.get_by_text("派发中 · 共 12 个任务").wait_for()
    detail.get_by_role("button", name="终止批次", exact=True).click()
    detail.get_by_role("heading", name="确认终止批次？").wait_for()
    detail.get_by_role("button", name="返回", exact=True).click()
    detail.get_by_role("button", name="终止批次", exact=True).wait_for()
    detail.get_by_role("button", name="关闭批次详情").click()
    assert not errors, errors
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    screenshots = Path(__file__).resolve().parents[2] / "frontend/.cache/screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    records.get_by_role("region", name="批次记录表格滚动区域").evaluate("node => { node.scrollLeft = 0; }")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshots / f"TEST_runninghub_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)

    if width == 2560 and prefix == "/tts":
        empty = True
        page.reload()
        create.get_by_text("没有就绪图片。", exact=False).wait_for()
        create.get_by_text("暂无人物模板。", exact=False).wait_for()
        assert plan.get_by_role("button", name="预览计划").is_disabled()
        assert not page.get_by_role("dialog").count()
        page.screenshot(path=str(screenshots / "TEST_runninghub_2560_empty.png"))
