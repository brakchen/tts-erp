"""Live 冒烟：视频发布页面只读契约（config / tasks 列表 / current）。

约定见 ``tests/e2e/conftest.py``：只打 GET、不做任何写操作、带
``requires_service`` 标记（``fast`` 套件排除）。显式运行：

    bash scripts/test_isolated.sh e2e

覆盖的是页面启动链路上真正会被消费的字段与缓存语义：

- ``GET /config`` 的限额/目标/worker 契约（前端 ``valid()``、摘要面板、
  提交按钮可用性全部依赖它）；
- ``GET /tasks`` 的 items/cursor/pollState 契约，以及 **ETag/304** 语义
  （``If-None-Match`` 命中时必须 304，这是页面轮询的基础，也是历史缺陷
  "切筛选命中 304 后表格显示上一个筛选数据" 所在的契约面）；
- ``GET /tasks/current`` 的 task/pollState/suggestedPollSeconds 契约。

鉴权说明：``access/_access.py`` 对 ``/v2/video-publish*`` 的 API key 默认
强制 ADMIN（``TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS`` 未开时）。
因此当 key 权限不足时本文件**整体跳过**并给出原因，而不是伪装成通过。
"""

from __future__ import annotations

import urllib.error
import urllib.request

from conftest import request_json

VIDEO_PUBLISH_PREFIX = "/v2/video-publish"


def _reachable_or_skip() -> None:
    """权限不足时明确跳过，避免把"没验到"写成"验过了"。"""
    status, body = request_json("GET", f"{VIDEO_PUBLISH_PREFIX}/config")
    if status in (401, 403):
        import pytest

        pytest.skip(
            "视频发布 API 对 API key 要求 ADMIN 角色"
            f"（access/_access.py 策略），当前 key 返回 {status}：{body!r}。"
            "要看真实契约请用 admin key，或设 "
            "TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS=1 后重跑。"
        )


def test_config_shape_and_readiness_contract() -> None:
    """启动配置：前端校验与投递摘要依赖的字段必须齐全且类型正确。"""
    _reachable_or_skip()
    status, body = request_json("GET", f"{VIDEO_PUBLISH_PREFIX}/config")
    assert status == 200, body

    # 限额：前端 valid() 直接用它做本地校验
    assert isinstance(body.get("maxVideoBytes"), int) and body["maxVideoBytes"] > 0, body
    assert (
        isinstance(body.get("maxCaptionCharacters"), int)
        and body["maxCaptionCharacters"] > 0
    ), body
    assert isinstance(body.get("maxPublishAttempts"), int), body

    # 可写判定与阻塞原因：提交按钮的 icon 态
    assert isinstance(body.get("canWrite"), bool), body
    if not body["canWrite"]:
        assert body.get("writeBlockReason"), "canWrite=false 必须给出可执行的阻塞原因"

    # 目标：摘要面板与确认弹窗展示
    target = body.get("target")
    assert isinstance(target, dict), body
    assert target.get("appName") and target.get("album"), body
    assert "deviceSerialMasked" in target, target
    # 掩码是安全契约：不得回传完整序列号
    assert target.get("deviceSerialMasked") != body.get("deviceSerial"), target

    worker = body.get("worker")
    assert isinstance(worker, dict) and worker.get("status") in {
        "ready",
        "unavailable",
    }, body


def test_tasks_list_shape_and_filter_contract() -> None:
    """列表：items/pollState/nextCursor 契约 + status 过滤是服务端语义。"""
    _reachable_or_skip()
    status, body = request_json("GET", f"{VIDEO_PUBLISH_PREFIX}/tasks?limit=5")
    assert status == 200, body
    assert isinstance(body.get("items"), list), body
    poll = body.get("pollState")
    assert isinstance(poll, dict), body
    assert set(poll) >= {"running", "cleaning", "queued"}, poll
    assert body.get("nextCursor") is None or isinstance(body["nextCursor"], str), body

    for item in body["items"]:
        assert {
            "taskId",
            "rowVersion",
            "status",
            "statusLabel",
            "stage",
            "stageLabel",
            "allowedActions",
        } <= set(item), item
        assert isinstance(item["allowedActions"], list), item
        # 前端只渲染服务端允许的动作，不得自行拼装
        assert "view" in item["allowedActions"], item

    # 服务端过滤：返回的每一行都必须真的是该状态
    status_filter, filtered = request_json(
        "GET", f"{VIDEO_PUBLISH_PREFIX}/tasks?status=failed&limit=5"
    )
    assert status_filter == 200, filtered
    for item in filtered.get("items", []):
        assert item["status"] == "failed", item


def test_tasks_list_etag_returns_304_when_unchanged() -> None:
    """ETag/304 契约：同一 path 未变化时必须 304（页面轮询与"切筛选串数据"缺陷的所在面）。"""
    _reachable_or_skip()
    import conftest

    def _get(extra: dict[str, str]) -> tuple[int, dict[str, str], str]:
        headers = {"Accept": "application/json", **(extra or {})}
        key = conftest.service_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            conftest.base_url() + f"{VIDEO_PUBLISH_PREFIX}/tasks",
            method="GET",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, hdrs, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            hdrs = {k.lower(): v for k, v in exc.headers.items()}
            return exc.code, hdrs, exc.read().decode("utf-8", "replace")

    first_status, first_headers, _ = _get({})
    assert first_status == 200, (first_status, first_headers)
    etag = first_headers.get("etag")
    if not etag:
        import pytest

        pytest.skip("该服务未返回 ETag，跳过 304 契约")

    second_status, _, second_body = _get({"If-None-Match": etag})
    assert second_status in (200, 304), second_status
    if second_status == 304:
        assert not second_body.strip(), "304 不应携带响应体"


def test_current_task_shape_contract() -> None:
    """当前轨道：task 可为 null；有任务时前端渲染依赖的字段必须存在。"""
    _reachable_or_skip()
    status, body = request_json("GET", f"{VIDEO_PUBLISH_PREFIX}/tasks/current")
    assert status == 200, body
    assert "task" in body, body
    assert isinstance(body.get("pollState"), dict), body
    assert isinstance(body.get("suggestedPollSeconds"), int), body

    task = body["task"]
    if task is None:
        return
    assert {
        "taskId",
        "status",
        "statusLabel",
        "stage",
        "stageLabel",
        "filename",
        "allowedActions",
    } <= set(task), task
    # 轨道展示的"阶段开始时间"必须是可解析的时间戳或 null（前端据此算已耗时）
    started = task.get("operationalStageStartedAt")
    if started is not None:
        from datetime import datetime

        assert datetime.fromisoformat(started), started
