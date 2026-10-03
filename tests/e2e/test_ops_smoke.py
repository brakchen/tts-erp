"""Live 冒烟：运维/配置只读端点（同步任务、拦截统计、运行时配置、汇率换算）。

覆盖 /v2/sync/*、/v2/intercept/*、/v2/config/*、/v2/fx/convert 的读侧，
断言结构与类型契约；任务清单/枚举表来自迁移种子，可安全断言非空。
"""

from __future__ import annotations

from conftest import request_json


def test_sync_status_shape() -> None:
    """同步状态总览：server_time ISO 字符串 + jobs 数组。"""
    status, body = request_json("GET", "/v2/sync/status")
    assert status == 200, body
    assert isinstance(body.get("server_time"), str), body
    assert isinstance(body.get("jobs"), list), body


def test_sync_jobs_registry_non_empty() -> None:
    """任务注册表非空（种子/代码注册），每项带 job_name/interval/enabled 契约。"""
    status, body = request_json("GET", "/v2/sync/jobs")
    assert status == 200, body
    jobs = body.get("jobs")
    assert isinstance(jobs, list) and jobs, "任务注册表不应为空"
    for job in jobs:
        assert {"job_name", "interval_seconds", "enabled"} <= set(job), job
        assert isinstance(job["interval_seconds"], int), job


def test_intercept_request_stats_shape() -> None:
    """拦截请求统计：计数为 int、分组维度为数组。"""
    status, body = request_json("GET", "/v2/intercept/requests/stats")
    assert status == 200, body
    assert isinstance(body.get("total_requests"), int), body
    assert body["total_requests"] >= 0, body
    assert isinstance(body.get("by_status"), list), body


def test_intercept_configs_shape() -> None:
    """拦截配置清单：configs 数组 + total int。"""
    status, body = request_json("GET", "/v2/intercept/configs")
    assert status == 200, body
    assert isinstance(body.get("configs"), list), body
    assert isinstance(body.get("total"), int), body


def test_config_enum_map_non_empty() -> None:
    """枚举映射表（迁移种子）非空，每项带 enum_type/enum_value/label_zh。"""
    status, body = request_json("GET", "/v2/config/enum-map/list")
    assert status == 200, body
    assert isinstance(body, list) and body, "枚举映射表应有迁移种子数据"
    for entry in body:
        assert {"enum_type", "enum_value", "label_zh"} <= set(entry), entry


def test_config_runtime_items_shape() -> None:
    """运行时配置项：{items: [...]} 容器契约（内容随部署可变，可为空）。"""
    status, body = request_json("GET", "/v2/config/runtime/items")
    assert status == 200, body
    assert isinstance(body, dict), body
    assert isinstance(body.get("items"), list), body


def test_fx_convert_local_pair() -> None:
    """汇率换算（本地缓存）：USD→VND 回显参数 + 返回 rate/converted/stale。"""
    status, body = request_json("GET", "/v2/fx/convert?amount=100&from_code=USD&to_code=VND")
    assert status == 200, body
    assert body.get("from_code") == "USD", body
    assert body.get("to_code") == "VND", body
    assert isinstance(body.get("rate"), str) and body["rate"], body
    assert isinstance(body.get("converted"), str) and body["converted"], body
    assert isinstance(body.get("stale"), bool), body


def test_fx_convert_missing_params_422() -> None:
    """缺 from_code/to_code → 422 + pydantic detail 列表。"""
    status, body = request_json("GET", "/v2/fx/convert?amount=100")
    assert status == 422, body
    assert isinstance(body, dict) and isinstance(body.get("detail"), list), body


def test_llm_context_markdown() -> None:
    """/v2/llm-context：Markdown 文档以标题行开头（非 JSON）。"""
    status, body = request_json("GET", "/v2/llm-context")
    assert status == 200, body
    assert isinstance(body, str), type(body)
    assert body.lstrip().startswith("#"), body[:80]
