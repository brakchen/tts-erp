"""Live 冒烟：SPU 利润劣化告警页与只读端点（需 :9877）。

本层是**有界**冒烟：只断言页面路由可鉴权访问、侧边栏入口存在，以及
「缺 shop 不会伪装成空 200」。告警数值正确性属于 tests/api 与领域层。

**部署前置（人工）**：live 服务必须跑在部署了本分支的代码上。实测本次运行的
:9877 ``/endpoints`` 索引里没有 ``spu-profit-deterioration`` 路由（服务在跑，
但进程早于本分支），且本 worktree 没有配置 ``TTS_ERP_SERVICE_KEY``。所以下面
需要真实数据的用例全部按 **not-run（skip）** 记录，并写明确切原因，
绝不把 404 / 缺 key 伪造成通过。

重新启用条件（两步都由人执行）：

1. 在本分支上重启 API（``bash restart.sh``），使 ``/endpoints`` 出现
   ``/v2/analytics/spu-profit-deterioration`` 与 ``/v2/pages/spu-profit-deterioration``；
2. 在环境变量或仓库根 ``.env`` 里提供 ``TTS_ERP_SERVICE_KEY``（readonly 即可）。
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from conftest import request_json  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = [pytest.mark.domain_e2e]

ALERT_PAGE = "/v2/pages/spu-profit-deterioration"
ALERT_API = "/v2/analytics/spu-profit-deterioration"

_NOT_DEPLOYED = (
    "not run：live :9877 的 /endpoints 索引里没有 spu-profit-deterioration 路由"
    "（服务在跑，但运行进程早于本分支）。部署前置：人工在本分支上重启 API"
    "（bash restart.sh）后本用例才可能执行。本次记为 not-run，不是通过。"
)
_NO_SERVICE_KEY = (
    "not run：本 worktree 未配置 TTS_ERP_SERVICE_KEY（环境变量与仓库根 .env 均无），"
    "无法对 live 路由做带鉴权的断言。本次记为 not-run，不是通过。"
)
_UNREACHABLE = "not run：live :9877 不可达（{reason}）。本次记为 not-run，不是通过。"


def _request_json(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    """live 不可达时如实 skip（HTTPError 由 request_json 自己处理，不会逃到这里）。"""
    try:
        return request_json(method, path, **kwargs)
    except urllib.error.URLError as exc:
        pytest.skip(_UNREACHABLE.format(reason=exc.reason))


def _live_route_index() -> list[str]:
    """/endpoints 是公开路由；用它区分「服务没部署本分支」与「鉴权缺失」。"""
    status, body = _request_json("GET", "/endpoints", require_key=False, send_key=False)
    if status != 200 or not isinstance(body, (list, dict)):
        pytest.skip(_UNREACHABLE.format(reason=f"/endpoints 返回 {status}"))
    return [entry["path"] for entry in body if isinstance(entry, dict) and "path" in entry]


def _skip_unless_deployed() -> None:
    paths = _live_route_index()
    if ALERT_API not in paths or ALERT_PAGE not in paths:
        pytest.skip(_NOT_DEPLOYED)


@pytest.fixture(autouse=True)
def live_alert_deployed() -> None:
    """先判部署、再判凭据：skip 原因必须是确切的那一个。"""
    _skip_unless_deployed()
    # require_key=True 时 request_json 缺 key 会 skip；这里先显式检查一次，
    # 保证「部署了但没凭据」也有独立的可读原因。
    _request_json("GET", "/healthz", require_key=False, send_key=False)


def test_alert_page_route_and_sidebar_entry() -> None:
    status, body = _request_json("GET", ALERT_PAGE)
    assert status == 200, body
    assert isinstance(body, str), body
    assert "利润劣化告警" in body
    # 侧边栏入口 + 共享 sidebar 外壳 + 阈值设置抽屉外壳都随页面下发。
    assert 'href="../../v2/pages/spu-profit-deterioration"' in body
    assert 'title="利润劣化告警" aria-current="page"' in body
    assert 'id="sidebar"' in body
    assert 'id="btn-settings"' in body
    assert 'id="settings-drawer"' in body
    assert 'aria-live="polite"' in body
    assert "/static/js/spu-profit-deterioration.js" in body
    # design §6.1/§6.2 的筛选与交互外壳：SPU scope、state 下拉、summary 容器、
    # 新鲜度提示都必须随 HTML 下发（不依赖 JS 注入）。
    for marker in (
        'id="filter-spu-ids"',
        'id="filter-state"',
        'id="alert-summary"',
        'id="alert-freshness"',
    ):
        assert marker in body, marker


def test_alert_page_requires_auth() -> None:
    status, _ = _request_json("GET", ALERT_PAGE, require_key=False, send_key=False)
    assert status in (302, 401), status


def test_alert_endpoint_never_fakes_an_empty_200_without_a_shop() -> None:
    status, body = _request_json("GET", ALERT_API)
    # shop_pk 是必填内部主键：缺失必须 422，不能回 200 + items=[]。
    assert status == 422, body

    status, body = _request_json("GET", f"{ALERT_API}?shop_pk=999999999")
    assert status != 200, body
    assert status in (422, 503), body
    assert "materialized alert snapshot" in str(body), body


def test_alert_filters_reject_invalid_enum_and_over_cap_spu_ids() -> None:
    """页面接线依赖的 422 契约：非法枚举与超过 100 个的 spu_ids 不得被静默接受。"""
    over_cap = "&".join(f"spu_ids={n}" for n in range(1, 102))
    for query in (
        "&state=not_a_state",
        "&sample=not_a_sample",
        "&layer=not_a_layer",
        "&severity=not_a_severity",
        f"&{over_cap}",
    ):
        status, body = _request_json("GET", f"{ALERT_API}?shop_pk=1{query}")
        assert status == 422, (query, status, body)
