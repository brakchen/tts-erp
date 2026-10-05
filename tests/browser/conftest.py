"""tests/browser —— **不需要 live 服务**的浏览器渲染回归（marker: domain_browser）。

与两层的分工（lane `render-gates`，2026-10-04）
------------------------------------------------
| 层 | 命令 | 数据来源 | 拦什么 |
| --- | --- | --- | --- |
| browser（本目录） | `scripts/test_isolated.sh fast`（默认就跑） | 本分支 Jinja 渲染 + 本地静态服务 + mock | 多视口横向溢出、computed 字体栈旁路 |
| live e2e（`tests/e2e/`） | `scripts/test_isolated.sh e2e`（需 :9877） | 真服务 + 真数据 | 排序闭环、API 契约、只读约束 |

布局与字体回归**不依赖真数据**，只依赖本分支的 CSS/HTML，所以必须进 `fast`：
2026-10-04 的筛选条 1440 溢出、Bootstrap/uPlot 字体旁路，在当时的 `fast`
与 e2e 下都是绿的，因为没有任何一层打开过浏览器。

只读：本层只发 GET；`render_support.Renderer` 会记录非 GET 请求并在收尾断言为空。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import pytest
from render_support import Renderer

REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_ROOT = REPO_ROOT / "tts_erp_v2" / "static"

# 与 scripts/probe_ui_layout_pages.py 同口径：普通页 + SPU 数据页 + 内联页
_PLAIN = {
    "dashboard": "dashboard.html",
    "shops": "shops.html",
    "enum-map": "enum-map.html",
    "runtime-configs": "runtime-configs.html",
    "sync-jobs": "sync-jobs.html",
    "manual-costs": "manual-costs.html",
    "intercept-configs": "intercept-configs.html",
    "intercept-requests": "intercept-requests.html",
    "intercept-stats": "intercept-stats.html",
    "users": "users.html",
}
_SPU_PAGES: tuple[
    tuple[
        Literal["spu-roi", "focused-spus"],
        Literal["standard-roi", "focused-spus"],
        Literal["spu-roi.js", "focused-spus.js"],
        str,
    ],
    ...,
] = (
    ("spu-roi", "standard-roi", "spu-roi.js", "SPU 实际 ROI"),
    ("focused-spus", "focused-spus", "focused-spus.js", "重点关注 SPU"),
)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}

_NOW = "2026-10-02T12:00:00+00:00"


def _ad_daily_rows() -> list[dict[str, Any]]:
    """按现行契约给 12 行（字段见 tts_erp_v2/api/v2/ad_daily.py::_item）。"""
    sellers = [
        ("749486486860415", "North Nook"),
        ("749486486860914", "QA 店 8（很长的店铺名称测试截断行为）"),
    ]
    return [
        {
            "id": 9000 + i,
            "seller_id": sellers[i % 2][0],
            "shop_pk": 7,
            "shop_name": sellers[i % 2][1],
            "advertiser_id": f"767935757287222470{i % 2}6",
            "campaign_id": "1877052518779186",
            "product_id": f"1737532{867998766722 + i}",
            "product_title": (
                "Áo sơ mi nam ngắn tay màu xám, họa tiết nhỏ thanh lịch"
                if i % 2
                else "Áo polo nam ngắn tay màu đỏ rượu vang cao cấp"
            ),
            "endpoint": "/open_api/ads/manager/report/integrated",
            "day": f"2026-10-0{(i % 3) + 1}",
            "mixed_real_cost": f"{0.01 + i * 0.03:.2f}",
            "onsite_roi2_shopping_sku": i % 4,
            "onsite_roi2_shopping_value": f"{i * 12.5:.2f}",
            "onsite_mixed_real_roi2_shopping": None if i % 5 == 0 else f"{i * 0.42:.2f}",
            "metrics_extra": {"clicks": 12 + i, "impressions": 900 + i},
            "created_at": _NOW,
            "updated_at": "2026-10-04T01:29:00+00:00",
        }
        for i in range(12)
    ]


_SPU_ITEMS = [
    {
        "spu_pk": 1000 + i,
        "spu_id": f"TEST_SPU_{i:03d}",
        "title": f"Áo Thun Nam Ngân Tây Mùa Hè 2024 Bản Cao Cấp {i}",
        "status": "ACTIVATE",
        "main_image_url": None,
        "uses_default_unit_cost": False,
        "refund_rate_alert": False,
        "has_unsettled_orders": False,
        "profit_status": "profit" if i % 2 else "loss",
        "spend": f"{20 + i}.00",
        "ad_system_actual_roi": "1.23",
        "ad_system_breakeven_roi": "1.56",
        "ad_system_breakeven_roi_status": "estimated_known_costs",
        "effective_sales": "1010.00",
        "total_orders": 11,
        "effective_order_count": 9,
        "cancel_rate": "0.010",
        "full_loss_rate": "0.005",
        "net_profit": f"{30 + i}.00",
        "roi_real": "1.80",
    }
    for i in range(12)
]


def _mock_payload(path: str, query: str = "") -> dict[str, Any] | list[Any]:
    """/v2/** 的 canned 只读载荷；没列到的接口给空集合，页面照常渲染外壳。"""
    if path.endswith("/v2/auth/me"):
        return {"authenticated": True, "role": "admin"}
    if path.endswith("/v2/commerce/channel-accounts"):
        return [{"id": 7, "platform": "tiktok", "shop_id": "749486486860415",
                 "account_name": "QA 店 7", "region": "VN", "status": "active"}]
    if path.endswith("/v2/reporting/ad-daily/options"):
        return {
            "sellers": [{"seller_id": "749486486860415", "shop_name": "North Nook",
                         "row_count": 8076}],
            "advertisers": [{"seller_id": "749486486860415",
                             "advertiser_id": "76793575728722247006", "row_count": 8076}],
            "endpoints": [{"endpoint": "/open_api/ads/manager/report/integrated",
                           "row_count": 8076}],
            "min_day": "2026-07-01",
            "max_day": "2026-10-03",
        }
    if path.endswith("/v2/reporting/ad-daily"):
        return {
            "items": _ad_daily_rows(),
            "total": 8076,
            "limit": 50,
            "offset": 0,
            "summary": {"row_count": 8076, "spend": "6674.41", "attributed_orders": 881,
                        "attributed_gmv": "21444.63", "weighted_roi": "3.213",
                        "currency": "USD"},
        }
    if path.endswith("/v2/analytics/spu-roi"):
        return {
            "items": _SPU_ITEMS,
            "total": len(_SPU_ITEMS),
            "totals": {"total_orders": 999, "spend": "1234.56", "net_profit": "4567.89",
                       "roi_real": "1.23", "profit_status": "profit"},
            "meta": {"currency": {"display": "CNY"}, "computed_at": _NOW,
                     "rubric_version": "v10", "reporting_timezone": "Asia/Ho_Chi_Minh",
                     "window": {"first_day": "2026-09-01", "last_day": "2026-09-30"},
                     "fx": {"as_of": "2026-10-05", "as_of_at": _NOW},
                     "presentation": {"rubric_label": "盈利 v10"}},
        }
    if path.endswith("/v2/intercept/requests/stats"):
        # daily 必须给：uPlot 图例（.u-label）只有画出图才存在，而 uPlot 自带的
        # system-ui/Arial 字体栈正是字体回归要拦的东西（2026-10-04 实测）。
        return {
            "total_requests": 4321,
            "whitelisted_requests": 12,
            "today_requests": 120,
            "error_requests": 3,
            "by_host": [{"key": "api.tiktokshop.com", "count": 300},
                        {"key": "seller.tiktokshop.com", "count": 132}],
            "by_method": [{"key": "GET", "count": 380}, {"key": "POST", "count": 52}],
            "by_status": [{"key": "200", "count": 400}, {"key": "500", "count": 31}],
            "daily": [
                {"date": "2026-10-01", "count": 120},
                {"date": "2026-10-02", "count": 80},
                {"date": "2026-10-03", "count": 145},
            ],
        }
    if path.endswith("/v2/sync/status"):
        return {"server_time": _NOW, "jobs": [], "total_spus": 1234,
                "missing_cost_spus": 89, "shop_count": 12, "auth_mode": "enforce"}
    if path.endswith("/v2/sync/freshness"):
        return {
            "server_time": _NOW,
            "shop_pk": 7,
            "shop_id": "TEST_shop",
            "sources": [
                {"key": "ads", "label": "广告", "synced_at": _NOW,
                 "scope": "shop", "basis": "rows", "severity": "ok",
                 "detail": "该店广告事实最近写入"},
                {"key": "orders", "label": "订单", "synced_at": _NOW,
                 "scope": "shop", "basis": "job", "job_name": "tiktok.orders",
                 "severity": "ok", "detail": "最近一次成功同步"},
                {"key": "logistics", "label": "物流", "synced_at": "2026-09-01T00:00:00+00:00",
                 "scope": "shop", "basis": "job", "job_name": "tiktok.logistics",
                 "severity": "crit", "detail": "最近一次成功同步"},
                {"key": "miaoshou", "label": "妙手", "synced_at": _NOW,
                 "scope": "system", "basis": "job", "job_name": "miaoshou.packages",
                 "severity": "warn", "detail": "最近成功：miaoshou.packages"},
            ],
        }
    if path.endswith("/v2/reporting/coverage"):
        return {"costed_spus": 1145, "linked_spus": 1200, "total_spus": 1234,
                "coverage_rate": "0.928", "as_of": "2026-10-01"}
    return {"items": [], "total": 0, "options": [], "configs": [], "jobs": []}


def _render_pages(out: Path) -> None:
    """用仓库自己的 Jinja 渲染全部页面（与线上同一套模板与资源版本戳）。"""
    from tts_erp_v2.api.v2.ad_daily import ad_daily_page
    from tts_erp_v2.api.v2.pages import (
        _render_page,
        _render_spu_profitability_page,
        _SpuProfitabilityPageConfig,
    )

    for slug, tpl in _PLAIN.items():
        resp = _render_page(tpl, current_page=slug)
        (out / f"{slug}.html").write_bytes(resp.body)
    for slug, profile_id, entry, title in _SPU_PAGES:
        resp = _render_spu_profitability_page(
            _SpuProfitabilityPageConfig(
                slug=slug, title=title, profile_id=profile_id, entrypoint_js=entry
            )
        )
        (out / f"{slug}.html").write_bytes(resp.body)
    (out / "ad-daily.html").write_bytes(ad_daily_page().body)


class _Handler(BaseHTTPRequestHandler):
    """静态渲染页 + /static + /v2 canned JSON（只读，不碰数据库）。"""

    rendered_dir: Path

    def do_POST(self) -> None:  # noqa: N802 - http.server 命名约定
        """页面加载期的只读 POST（如 schema/preview）也要有 JSON 应答，
        否则 501 会让页面 JS 报错、渲染出残缺布局，巡检就失真了。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        self._send(json.dumps(_mock_payload(path)).encode("utf-8"), _MIME[".json"])

    def do_GET(self) -> None:  # noqa: N802 - http.server 命名约定
        path = self.path.split("?", 1)[0]
        if path.startswith("/v2/pages/") and not path.endswith((".css", ".js")):
            name = path[len("/v2/pages/"):].strip("/") or "index"
            target = self.rendered_dir / f"{name}.html"
            if target.is_file():
                return self._send(target.read_bytes(), _MIME[".html"])
        if path.startswith("/static/"):
            target = _STATIC_ROOT / path[len("/static/"):]
            if target.is_file() and _STATIC_ROOT in target.resolve().parents:
                return self._send(target.read_bytes(),
                                  _MIME.get(target.suffix, "application/octet-stream"))
        if path.startswith("/v2/"):
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            body = json.dumps(_mock_payload(path, query)).encode("utf-8")
            return self._send(body, _MIME[".json"])
        self.send_error(404)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # 静默访问日志
        return


@pytest.fixture(scope="session")
def site_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """渲染本分支页面 → 本地随机端口提供，测试结束后关掉。"""
    rendered = tmp_path_factory.mktemp("rendered-pages")
    _render_pages(rendered)

    handler = type("BoundHandler", (_Handler,), {"rendered_dir": rendered})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def browser_renderer(site_url: str) -> Any:
    """本层的 headless chromium 渲染器；缺 playwright/chromium 时 skip。

    作用域是 **function**（不是 session）：Playwright 存活期间会在主线程里占着
    事件循环，pytest-asyncio 的后续 async 用例会以「coroutine never awaited /
    Cannot run the event loop while another loop is running」失败——实测
    tests/browser 先于 tests/middleware 跑时必现。每条用例用完即关，退出测试
    进程前不留 Playwright 的循环。
    """
    pw: Any = None
    browser: Any = None
    sync_playwright: Any = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 环境相关
        pytest.skip(f"playwright 不可用: {exc}")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover - 环境相关
        pw.stop()
        pytest.skip(f"chromium 启动失败: {exc}")
    instance = Renderer(browser, site_url)
    yield instance
    instance.close()
    browser.close()
    pw.stop()


@pytest.fixture(scope="module")
def token_font_stacks() -> set[str]:
    """tokens.css 三套栈（归一化）= 字体断言白名单，与 live e2e 同源。"""
    from render_support import token_font_stacks as parse_stacks

    css_path = _STATIC_ROOT / "css" / "tokens.css"
    return parse_stacks(css_path.read_text(encoding="utf-8"))
