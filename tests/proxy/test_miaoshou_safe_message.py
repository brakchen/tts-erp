"""上游失败信息要**可自解释**，而且**绝不带凭据**。

2026-11-10 生产事实（只读核查 ``integration.sync_jobs``）：
``miaoshou.purchase_price_clean`` 从 2026-10-09 14:42 起连续失败 776 次，
``sync_jobs.error_message`` 只有一句::

    RuntimeError: Miaoshou purchase business failure: result='fail' code=50001

上游其实给了人类可读的原因（``您的授权已经过期，请重新登录``）——是我们丢掉了，
于是运维只看到一个无法自解释的 code=50001，31 天没人处理。

安全纪律（代码里原有注释，必须遵守）：上游错误体是「不可信的认证错误体，
可能回显 Cookie/token」⇒ **只带 reason 字段，不带整个 payload**；reason 本身
也要先剥离疑似凭据形态的片段。

断言分三类，各自独立 method：
* 位置断言 —— 判据落在哪个模块、哪个函数上；
* 数量枚举 —— 调用点 / 导出项的精确数量；
* 分支覆盖 —— 每个分支各一个用例，含「必须命中」与「必须不命中」孪生。
"""

from __future__ import annotations

import ast
import io
import json
import urllib.error
from http.client import HTTPMessage
from pathlib import Path
from typing import Any, Self, cast

import pytest

from tts_erp_v2.jobs.miaoshou import purchase_price_clean as job
from tts_erp_v2.proxy.miaoshou import client as ms_client
from tts_erp_v2.proxy.miaoshou import safe_message

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_unit]

# 上游 2026-11-10 实测原文（复刻，不含任何真实凭据）
UPSTREAM_EXPIRED_PAYLOAD: dict[str, Any] = {
    "result": "fail",
    "code": 50001,
    "reason": "您的授权已经过期，请重新登录",
}

#: 疑似凭据片段——任何一条出现在错误消息里都算泄漏。
CREDENTIAL_FRAGMENTS = ("miao_token", "zebra_value", "sessionid", "Cookie:")


# ─── 位置断言：判据落在哪儿 ───────────────────────────────────────────────


def test_safe_message_module_lives_in_proxy_miaoshou() -> None:
    """位置断言：脱敏与标记的唯一实现落在 proxy/miaoshou/safe_message.py。"""
    path = Path(safe_message.__file__).resolve()
    assert path.name == "safe_message.py"
    assert path.parent.name == "miaoshou"
    assert path.parent.parent.name == "proxy"
    assert callable(safe_message.sanitize_reason)
    assert callable(safe_message.classify_failure_marker)


def test_purchase_job_and_client_both_use_the_shared_sanitizer() -> None:
    """位置断言：作业与代理客户端都从同一个模块取脱敏函数，不各写一套。"""
    for module in (job, ms_client):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "safe_message"
            ):
                imported |= {alias.name for alias in node.names}
        assert {"sanitize_reason", "classify_failure_marker"} <= imported, (
            f"{module.__name__} 没有从 safe_message 导入脱敏/标记函数"
        )


def test_purchase_job_failure_message_carries_the_human_reason() -> None:
    """位置断言：作业的业务失败消息里真的带上了 reason。"""
    message = job._business_failure_message(UPSTREAM_EXPIRED_PAYLOAD)
    assert "您的授权已经过期，请重新登录" in message
    assert "50001" in message
    assert safe_message.CREDENTIAL_EXPIRED_MARKER in message


def test_purchase_job_failure_message_has_no_credential_fragments() -> None:
    """位置断言：作业的消息里没有 cookie / token / zebra 片段。"""
    payload = {
        "result": "fail",
        "code": 50001,
        "reason": (
            "您的授权已经过期，请重新登录；cookie=miao_token_abcdef123456 "
            "x-app-zebra=zebra_value_987654321"
        ),
    }
    message = job._business_failure_message(payload)
    for fragment in CREDENTIAL_FRAGMENTS:
        assert fragment not in message
    assert safe_message.CREDENTIAL_EXPIRED_MARKER in message


# ─── 数量枚举：精确数量 ───────────────────────────────────────────────────


def test_safe_message_exports_exactly_six_public_names() -> None:
    """数量枚举：公开面恰好六个名字（三个常量 + 错误码表 + 两个函数）。"""
    assert len(safe_message.__all__) == 6
    assert set(safe_message.__all__) == {
        "CREDENTIAL_EXPIRED_CODES",
        "CREDENTIAL_EXPIRED_MARKER",
        "MAX_REASON_CHARS",
        "REASON_UNCLASSIFIED_MARKER",
        "classify_failure_marker",
        "sanitize_reason",
    }


def test_each_module_sanitizes_every_failure_path() -> None:
    """数量枚举：作业与客户端的失败路径都过了脱敏，标记只走一个入口。"""
    job_source = Path(job.__file__).read_text(encoding="utf-8")
    assert job_source.count("sanitize_reason(") == 1, (
        f"{job.__name__} 的 sanitize_reason 调用点数量不对"
    )
    assert job_source.count("classify_failure_marker(") == 1, (
        f"{job.__name__} 的 classify_failure_marker 调用点数量不对"
    )
    client_source = Path(ms_client.__file__).read_text(encoding="utf-8")
    assert client_source.count("classify_failure_marker(") == 1, (
        f"{ms_client.__name__} 的 classify_failure_marker 调用点数量不对"
    )


def test_client_error_paths_never_echo_the_raw_body() -> None:
    """位置断言：HTTP 错误路径的 preview 必须先脱敏再进异常消息。

    上游错误体不可信（可能回显 Cookie/token/x-app-zebra），而异常消息会落进
    ``integration.sync_jobs.error_message`` 与日志 ⇒ 原始 body 不能直接回显。
    """
    source = Path(ms_client.__file__).read_text(encoding="utf-8")
    previews = [
        line for line in source.splitlines() if line.strip().startswith("preview =")
    ]
    assert previews, "client.py 里找不到 preview 赋值，护栏需要跟着改"
    for line in previews:
        assert "sanitize_reason(" in line, f"preview 未脱敏：{line.strip()}"


# ─── 分支覆盖：每个分支一个用例 ───────────────────────────────────────────


def test_sanitize_reason_keeps_a_plain_reason() -> None:
    """分支覆盖：普通中文原因原样保留（不能被过度脱敏）。"""
    assert (
        safe_message.sanitize_reason("您的授权已经过期，请重新登录")
        == "您的授权已经过期，请重新登录"
    )


@pytest.mark.parametrize(
    "reason",
    [
        "cookie=miao_token_abcdef123456",
        "Cookie: miao_token_abcdef123456; Path=/",
        "token=abcdef0123456789abcdef",
        "x-app-zebra=zebra_value_987654321",
        "sessionid=abc123def456",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefgh",
        "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
    ],
)
def test_sanitize_reason_strips_credential_shapes(reason: str) -> None:
    """分支覆盖：疑似凭据形态（cookie/token/zebra/JWT/长 hex）必须被剥离。"""
    cleaned = safe_message.sanitize_reason(reason)
    for fragment in CREDENTIAL_FRAGMENTS:
        assert fragment not in cleaned
    assert "miao_token_abcdef123456" not in cleaned
    assert "zebra_value_987654321" not in cleaned
    assert "abcdef0123456789abcdef" not in cleaned
    assert "abc123def456" not in cleaned
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in cleaned
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned


def test_sanitize_reason_truncates_long_reason() -> None:
    """分支覆盖：超长 reason 被截断（不整段带进 sync_jobs）。"""
    cleaned = safe_message.sanitize_reason("啊" * 500)
    assert len(cleaned) <= safe_message.MAX_REASON_CHARS
    assert cleaned.endswith("…")


def test_classify_marker_hits_on_the_stable_code_without_any_wording() -> None:
    """主判据：只靠上游稳定错误码命中，零中文措辞也能判过期。

    这条是本次事故形态的直接对策：上游改一次文案，判据就瞎一次。
    """
    assert (
        safe_message.classify_failure_marker("50001", "上游换了说法，看不懂")
        == safe_message.CREDENTIAL_EXPIRED_MARKER
    )
    assert (
        safe_message.classify_failure_marker(50001, "")
        == safe_message.CREDENTIAL_EXPIRED_MARKER
    )


def test_classify_marker_does_not_guess_credential_from_wording_alone() -> None:
    """负向：不认识的中文措辞不得被当成凭据过期（措辞不能自己凑出结论）。"""
    assert (
        safe_message.classify_failure_marker(None, "上游换了说法，看不懂")
        == safe_message.REASON_UNCLASSIFIED_MARKER
    )


def test_classify_marker_keeps_the_wording_as_a_fallback() -> None:
    """辅助判据：陌生错误码 + 过期措辞仍判过期（上游还没上稳定错误码时）。"""
    assert (
        safe_message.classify_failure_marker("99999", "您的授权已经过期，请重新登录")
        == safe_message.CREDENTIAL_EXPIRED_MARKER
    )


@pytest.mark.parametrize(
    "reason",
    [
        "参数错误",
        "数据过期，请稍后重试",          # 有「过期」但不是登录态
        "shop not found",
        "采购单状态不允许取消",
    ],
)
def test_classify_marker_does_not_hit_other_reasons(reason: str) -> None:
    """分支覆盖（不命中孪生）：非登录态原因不得被误判成凭据过期。"""
    assert (
        safe_message.classify_failure_marker("99999", reason)
        == safe_message.REASON_UNCLASSIFIED_MARKER
    )


def test_classify_marker_marks_unknown_failures_unclassified() -> None:
    """未知失败标成「未分类」，不吞进 credential（否则会把人引向错的方向）。"""
    assert (
        safe_message.classify_failure_marker("99999", "参数错误")
        == safe_message.REASON_UNCLASSIFIED_MARKER
    )
    assert safe_message.classify_failure_marker(None, None) is None, (
        "没有失败信息时不该编一个标记出来"
    )


def test_purchase_job_marks_unknown_failures_unclassified() -> None:
    """作业消息里未知失败带 reason_unclassified，且不带 credential_expired。"""
    message = job._business_failure_message(
        {"result": "fail", "code": 99999, "reason": "参数错误"}
    )
    assert safe_message.REASON_UNCLASSIFIED_MARKER in message
    assert safe_message.CREDENTIAL_EXPIRED_MARKER not in message


def test_purchase_job_raises_with_reason_on_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支覆盖（改变结果）：真造一次上游失败 ⇒ 消息含人类可读原因。"""

    class _Resp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(request, timeout=None):
        return _Resp(json.dumps(UPSTREAM_EXPIRED_PAYLOAD).encode("utf-8"))

    monkeypatch.setattr(job.urllib.request, "urlopen", fake_urlopen)

    client = job.MiaoshouPurchaseWebClient(
        cookie="miao_test_cookie", x_app_zebra="zebra_test_value"
    )
    with pytest.raises(RuntimeError) as exc:
        client.search_page(page=1)
    message = str(exc.value)
    assert "您的授权已经过期，请重新登录" in message
    assert safe_message.CREDENTIAL_EXPIRED_MARKER in message
    assert "miao_test_cookie" not in message
    assert "zebra_test_value" not in message


def test_miaoshou_client_envelope_failure_keeps_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支覆盖：ERP 信封 fail ⇒ 消息带 reason，不带整个 payload。"""

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps(UPSTREAM_EXPIRED_PAYLOAD)

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    client = ms_client.MiaoshouErpClient(app_id="app", app_secret="secret")
    with pytest.raises(ms_client.MiaoshouApiError) as exc:
        client._call_erp(path="/api/foo")
    assert "您的授权已经过期，请重新登录" in exc.value.message
    assert safe_message.CREDENTIAL_EXPIRED_MARKER in exc.value.message


def test_miaoshou_client_http_error_does_not_echo_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支覆盖：HTTPError 分支绝不回显原始响应体（含凭据）。"""
    body = b'{"result":"fail","reason":"cookie=miao_token_abcdef123456"}'

    def fake_post(url, body_bytes, timeout, headers=None):
        fp = io.BytesIO(body)
        err = urllib.error.HTTPError(  # type: ignore[call-arg]
            url=url,
            code=403,
            msg="Forbidden",
            hdrs=cast("HTTPMessage", {}),
            fp=fp,
        )
        raise err

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    client = ms_client.MiaoshouErpClient(app_id="app", app_secret="secret")
    with pytest.raises(ms_client.MiaoshouApiError) as exc:
        client._call_erp(path="/api/foo")
    assert "miao_token_abcdef123456" not in exc.value.message
    assert "HTTP 403" in exc.value.message


def test_miaoshou_client_json_decode_error_does_not_echo_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支覆盖：非 JSON 响应 ⇒ 消息不带 body。"""

    def fake_post(url, body_bytes, timeout, headers=None):
        return "cookie=miao_token_abcdef123456"

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    client = ms_client.MiaoshouErpClient(app_id="app", app_secret="secret")
    with pytest.raises(ms_client.MiaoshouApiError) as exc:
        client._call_erp(path="/api/foo")
    assert "miao_token_abcdef123456" not in exc.value.message
    assert "解析" in exc.value.message
