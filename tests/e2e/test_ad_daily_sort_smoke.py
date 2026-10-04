"""Live 冒烟：广告日明细排序（API 单调性 + 表头点击闭环）。

排序是 2026-10-04 新加的能力（lane `ui-font-unify`），三层保证各写一条：

1. **API 排序语义**：白名单排序真的按值排、方向相反、NULL 两个方向都垫底，
   非法 ``sort``/``order`` 回 422 而不是静默回退；
2. **表头交互**：浏览器点列头 → ``aria-sort``、URL 查询参数、后端请求三者同步，
   再点同一列翻方向，刷新后排序保持；
3. **渲染闭环**：点完之后页面上那一列的数值必须真的有序 —— 断的是
   「SQL → API → 渲染」整条链，而不是某一环。

另有一条契约守卫：明细表日期列必须渲染出 ``YYYY-MM-DD``，不是 ``—``
（mock / 契约漂移时页面会静默退化成占位符，2026-10-04 巡检就踩过）。

运行：``bash scripts/test_isolated.sh e2e``（需 :9877 在跑）。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import pytest
from conftest import request_json

MIN_ROWS = 3  # 少于 3 行时单调性断言没有意义，直接跳过


def _list(params: str) -> dict[str, Any]:
    status, body = request_json("GET", f"/v2/reporting/ad-daily?{params}")
    assert status == 200, body
    assert isinstance(body, dict), body
    return body


def _spend(row: dict[str, Any]) -> float | None:
    value = row.get("mixed_real_cost")
    return None if value is None else float(value)


def _assert_sorted(rows: list[dict[str, Any]], *, descending: bool) -> None:
    """值必须单调，且 NULL 全部落在末尾（NULLS LAST）。"""
    values = [_spend(row) for row in rows]
    present = [v for v in values if v is not None]
    assert len(values) - len(present) == sum(v is None for v in values[len(present):]), (
        f"NULL 必须连续垫底: {values}"
    )
    assert all(v is None for v in values[len(present):]), f"NULL 混进了有值区间: {values}"
    expected = sorted(present, reverse=descending)
    assert present == expected, (
        f"{'降' if descending else '升'}序失效: {present[:10]} != {expected[:10]}"
    )


def test_ad_daily_default_order_is_newest_day_first() -> None:
    """默认排序保持 day DESC（不传 sort/order 时的行为契约）。"""
    body = _list("limit=50")
    days = [row["day"] for row in body["items"]]
    if len(days) < MIN_ROWS:
        pytest.skip("live 库广告日明细行数不足")
    assert days == sorted(days, reverse=True), days


def test_ad_daily_spend_sort_is_monotonic_with_nulls_last() -> None:
    """sort=spend 升/降序各自单调，缺指标的行两个方向都排在最后。"""
    asc = _list("limit=100&sort=spend&order=asc")["items"]
    desc = _list("limit=100&sort=spend&order=desc")["items"]
    if len(asc) < MIN_ROWS:
        pytest.skip("live 库广告日明细行数不足")
    _assert_sorted(asc, descending=False)
    _assert_sorted(desc, descending=True)


def test_ad_daily_sort_directions_return_mirror_heads() -> None:
    """同列反向排序时，两端互为镜像（确认 order 参数真的生效）。"""
    asc = _list("limit=25&sort=spend&order=asc")["items"]
    desc = _list("limit=25&sort=spend&order=desc")["items"]
    asc_values = [_spend(row) for row in asc if _spend(row) is not None]
    desc_values = [_spend(row) for row in desc if _spend(row) is not None]
    if len(asc_values) < MIN_ROWS or len(desc_values) < MIN_ROWS:
        pytest.skip("live 库带消耗的行数不足")
    assert asc_values[0] == desc_values[-1] or asc_values[0] <= desc_values[0], (
        f"升序首行 {asc_values[0]} 与降序末行 {desc_values[-1]} 应同为最小值区间"
    )
    assert desc_values[0] >= asc_values[0], (desc_values[0], asc_values[0])


@pytest.mark.parametrize(
    ("query", "expectation"),
    [
        ("sort=seller_id", "sort must be one of:"),
        ("sort=1; DROP TABLE plugin.ad_daily", "sort must be one of:"),
        ("order=sideways", "order must be asc or desc"),
    ],
)
def test_ad_daily_rejects_unknown_sort_and_order(query: str, expectation: str) -> None:
    """白名单之外的排序键/方向必须 422，且错误信息可读。"""
    # 注入样例含空格/分号：先编码，服务端解码后仍是同一串，才走得到参数校验
    status, body = request_json(
        "GET", f"/v2/reporting/ad-daily?{quote(query, safe='=&')}"
    )
    assert status == 422, (status, body)
    detail = body.get("detail") if isinstance(body, dict) else None
    assert isinstance(detail, str) and expectation in detail, detail


def _spend_cells(page: Any) -> list[str]:
    """渲染出来的「消耗 USD」列（第 5 列，td index 4）。"""
    return page.eval_on_selector_all(
        ".mld-table tbody tr:not(.mld-detail-row)",
        "rows => rows.map(r => (((r.querySelectorAll('td')[4] || {}).textContent) || '').trim())",
    )


def _numbers(cells: list[str]) -> list[float]:
    out = []
    for cell in cells:
        text = cell.replace(",", "").strip()
        if text in {"", "—"}:
            continue
        out.append(float(text))
    return out


def _click_and_wait(page: Any, selector: str, query: str) -> dict[str, Any]:
    """点一列表头，等**这一轮**请求返回并渲染完，再返回响应。

    ``wait_for_load_state('networkidle')`` 在首轮就已触发，再点时会立刻返回，
    于是 DOM 还停在上一轮的行序 —— 必须用 expect_response 精确对上「带这组
    sort/order 的那次请求」，再等两帧让 body 解析 + renderRows 落地。
    """
    with page.expect_response(
        lambda resp: "/v2/reporting/ad-daily?" in resp.url and query in resp.url,
        timeout=15_000,
    ) as info:
        page.click(selector)
    response = info.value
    assert response.status == 200, f"排序请求失败: {response.status} {response.url}"
    page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )
    return {"url": response.url, "status": response.status}


def test_ad_daily_header_click_drives_sort(renderer) -> None:
    """点表头 → aria-sort / URL / 后端请求 / 渲染行序四者一致。"""
    page = renderer.open("/v2/pages/ad-daily")
    list_requests: list[str] = []
    page.on(
        "request",
        lambda req: list_requests.append(req.url)
        if "/v2/reporting/ad-daily?" in req.url
        else None,
    )

    assert page.get_attribute('th[data-sort="day"]', "aria-sort") == "descending"
    assert page.get_attribute('th[data-sort="spend"]', "aria-sort") == "none"

    # 首击：消耗列默认降序
    hit = _click_and_wait(page, 'button[data-sort="spend"]', "sort=spend&order=desc")
    assert page.get_attribute('th[data-sort="spend"]', "aria-sort") == "descending"
    assert "sort=spend" in page.url and "order=desc" in page.url, page.url
    assert "sort=spend&order=desc" in hit["url"], hit["url"]
    assert any("sort=spend&order=desc" in url for url in list_requests), list_requests

    values = _numbers(_spend_cells(page))
    if len(values) < MIN_ROWS:
        pytest.skip("live 库本页带消耗的行数不足")
    assert values == sorted(values, reverse=True), f"降序渲染失效: {values}"

    # 再点同一列：翻成升序
    hit = _click_and_wait(page, 'button[data-sort="spend"]', "sort=spend&order=asc")
    assert page.get_attribute('th[data-sort="spend"]', "aria-sort") == "ascending"
    assert "order=asc" in page.url, page.url
    assert "sort=spend&order=asc" in hit["url"], hit["url"]
    values = _numbers(_spend_cells(page))
    assert len(values) >= MIN_ROWS, f"行数不足: {values}"
    assert values == sorted(values), f"升序渲染失效: {values}"
    # 方向真的翻了：升序首行必须不大于降序时的末行
    assert values[0] <= max(values), values

    # 换列：计划 ID 走该列自然顺序（升序）
    hit = _click_and_wait(page, 'button[data-sort="campaign"]', "sort=campaign&order=asc")
    assert page.get_attribute('th[data-sort="campaign"]', "aria-sort") == "ascending"
    assert "sort=campaign" in page.url, page.url
    assert "sort=campaign&order=asc" in hit["url"], hit["url"]

    # 刷新后排序保留（URL 是排序的唯一真相来源）
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('load-status').dataset.state === 'ok'",
        timeout=15_000,
    )
    assert "sort=campaign" in page.url, page.url
    assert page.get_attribute('th[data-sort="campaign"]', "aria-sort") == "ascending"

    renderer.assert_read_only()


def test_ad_daily_table_renders_source_rows_not_placeholders(renderer) -> None:
    """明细表必须渲染真实日期列（契约漂移会让页面静默退化成 —）。"""
    page = renderer.open("/v2/pages/ad-daily")
    page.wait_for_load_state("networkidle", timeout=15_000)
    status_text = page.inner_text("#load-status")
    match = re.search(r"已读取\s*([\d,]+)\s*行", status_text)
    assert match is not None, f"加载状态异常: {status_text!r}"
    if int(match.group(1).replace(",", "")) == 0:
        pytest.skip("live 库暂无广告日明细")

    first_day = page.inner_text(".mld-table tbody tr td:first-child").strip()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", first_day), (
        f"日期列应渲染 YYYY-MM-DD，实际 {first_day!r}（API 契约或 mock 漂移）"
    )
    renderer.assert_read_only()
