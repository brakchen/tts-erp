"""P2-3 修复回归：所有错误响应 detail.message 必须是中文文案，
绝不能是机器码当文案（那等于把 code 当 message 透给运营）。

按 docs/design §10.12：errors 必须说明发生位置与可执行动作。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.domain_publishing]

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tts_erp_v2.api.v2 import video_publish

# P2-3 错误码 message 全量覆盖：每条都查 message 不能是 raw code 本身。
P2_3_CASES = [
    # code, 期望是 message 而不是 code 自身的子串（避免 code = message 这种偷懒）
    ("TASK_NOT_FOUND",            "TASK_NOT_FOUND"),
    ("TASK_ACTION_NOT_ALLOWED",   "TASK_ACTION_NOT_ALLOWED"),
    ("TASK_VERSION_CONFLICT",      "TASK_VERSION_CONFLICT"),
    ("OBJECT_CLEANUP_IN_PROGRESS", "OBJECT_CLEANUP_IN_PROGRESS"),
    ("CLEANUP_REQUIRED",           "CLEANUP_REQUIRED"),
    ("TASK_RETRY_NOT_SAFE",        "TASK_RETRY_NOT_SAFE"),
    ("UPLOAD_REPLACEMENT_REQUIRED", "UPLOAD_REPLACEMENT_REQUIRED"),
    ("RETRY_BUDGET_EXHAUSTED",     "RETRY_BUDGET_EXHAUSTED"),
    ("PUBLISH_WORKER_UNAVAILABLE", "PUBLISH_WORKER_UNAVAILABLE"),
    ("OBJECT_STORE_UNAVAILABLE",   "OBJECT_STORE_UNAVAILABLE"),
    ("DEVICE_OFFLINE",             "DEVICE_OFFLINE"),
    ("DEVICE_LOCKED",              "DEVICE_LOCKED"),
    ("APP_NOT_INSTALLED",          "APP_NOT_INSTALLED"),
    ("ARTEMIS_UNREACHABLE",        "ARTEMIS_UNREACHABLE"),
    ("ARTEMIS_UNKNOWN_OUTCOME",    "ARTEMIS_UNKNOWN_OUTCOME"),
    ("VERIFY_INCONCLUSIVE",        "VERIFY_INCONCLUSIVE"),
    ("VERIFY_ALREADY_RUNNING",     "VERIFY_ALREADY_RUNNING"),
    ("CSRF_HEADER_REQUIRED",       "CSRF_HEADER_REQUIRED"),
]


def _make_client(db_session: Session) -> TestClient:
    """构造一个最小可工作的 FastAPI TestClient，让 _error_detail 真能跑。"""
    from fastapi import FastAPI, Request

    app = FastAPI()
    app.include_router(video_publish.router)

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        # 让 _owns_task / _action_conflict / _error_detail 拿到合理 scope。
        request.scope.setdefault("auth_method", "cookie")
        request.scope.setdefault("access_grant", None)
        request.scope.setdefault("user_id", None)
        request.scope.setdefault("api_key_role", None)
        return await call_next(request)

    return TestClient(app, raise_server_exceptions=True)


def test_p2_3_every_machine_code_has_human_message(db_session: Session) -> None:
    """用户可观察的契约：每个机器码对应的 message 必须可读，绝不能是 code 自身。"""

    from tts_erp_v2.api.v2.video_publish import _error_detail

    # 直接调用 _error_detail 拿到所有码的 detail，断言：
    # 1) message 不等于 code（修了之后由 _ERROR_DETAIL_MESSAGES 提供）
    # 2) message 是非空字符串
    # 3) message 不是纯空白
    class _Req:
        def __init__(self): self.headers = {}
        scope = {}

    req = _Req()
    for code, raw in P2_3_CASES:
        d = _error_detail(req, code, raw, retryable=False)
        assert d["code"] == code
        assert d["message"], f"{code}: message 不可为空"
        assert d["message"] != code, (
            f"{code}: message 仍是机器码 '{d['message']}'，"
            f"需要加入 _ERROR_DETAIL_MESSAGES 或传真 message"
        )
        assert d["message"].strip() == d["message"], f"{code}: message 含首尾空白"


def test_p2_3_action_conflict_delegates_to_error_detail(db_session: Session) -> None:
    """_action_conflict 也走 _error_detail，所以一处修复全覆盖。"""
    from tts_erp_v2.api.v2.video_publish import _action_conflict, _error_detail

    class _Req:
        def __init__(self):
            self.headers = {}
            self.scope = {}

    req = _Req()
    a = _action_conflict(req, task=None, code="CLEANUP_REQUIRED", message="CLEANUP_REQUIRED", retryable=False)
    e = _error_detail(req, "CLEANUP_REQUIRED", "CLEANUP_REQUIRED", retryable=False)
    assert a["message"] == e["message"], (
        "_action_conflict 必须复用 _error_detail 的 message 逻辑，"
        "否则会出现 _error_detail 已修但 _action_conflict 还是机器码的撕裂"
    )


def test_p2_3_no_codes_return_raw_machine_code() -> None:
    """反向断言：所有要修复的码都不能让 message == code（仓库的"偷懒模式"）。"""
    from tts_erp_v2.api.v2.video_publish import _ERROR_DETAIL_MESSAGES

    # 防御性：如果 _ERROR_DETAIL_MESSAGES 里漏了码，该码的 message 仍是 code 本身
    # → 下游消费者（前端 / 运营）看到的就是机器码
    for code, _raw in P2_3_CASES:
        assert code in _ERROR_DETAIL_MESSAGES, (
            f"{code} 不在 _ERROR_DETAIL_MESSAGES 里——"
            "调用方若把 code 当 message 传，detail.message 会回退到原码"
        )