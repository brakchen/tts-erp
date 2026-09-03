"""Handler-level tests: /v2/analytics/sync error observability（v2 化移植版）。

移植自 tests/api/test_analytics_sync_errors.py（/v1 路径 +
analytics_sync 孤岛包），2026-09-02 随 v2 化改写
（tech-doc/analytics-v2-migration-plan.md Phase 3）：

- 路径 /v1/analytics/sync/* → /v2/analytics/sync/*
- monkeypatch 目标 analytics_sync.app → tts_erp_v2.api.v2.analytics
- 审计回读 analytics_sync.pg_repositories.connect → db_engine +
  analytics.ad_audit_log
- 删除 session 级 autouse ALTER fixture（alembic 0004 单轨拥有 schema）

锁定的契约（生产事故回归点）：
1. 每次 _audit_and_error 拒绝都向 stderr 写一条消毒诊断行（字段级
   Pydantic 细节 + request id + key 前缀），不 echo 请求体/凭证。
2. SCHEMA_INVALID 响应带结构化 errors[]（loc/msg/type 三元组，
   丢 input/ctx/url）。
3. errors[] 是 SCHEMA_INVALID 专属 —— MALFORMED_JSON /
   UNSUPPORTED_PROTOCOL_VERSION 不得带该字段。
4. 审计行 error_message 与 stderr 是同一份 ≤500 字符消毒载荷。

数据隔离：全部 TEST_ 哨兵；audit 行按 request_id/path 前缀清理
（写审计走独立连接 commit，逃出测试 savepoint）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

_BATCHES = "/v2/analytics/sync/dumps"


@pytest.fixture(autouse=True)
def _cleanup_audit_rows(db_engine):
    """清掉本文件写进 analytics.ad_audit_log 的 TEST_ 行。"""
    yield
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM analytics.ad_audit_log "
                "WHERE request_id LIKE 'TEST_%' OR path LIKE '%TEST_%'"
            )
        )


def _valid_dump() -> dict:
    """DumpBodyIn 形状：单 dump object（dump architecture，page 隐式 = 1）。"""
    return {
        "endpoint": "/oec_ads/report",
        "method": "POST",
        "day": "2026-08-30",
        "campaignId": "TEST_campaign-1",
        "request": {"url": "https://ads.tiktok.com/report", "headers": {}},
        "response": {"status": 200, "body": {"data": []}},
        "capturedAt": "2026-08-30T18:43:00.000Z",
        "schemaVersion": 2,
    }


def _payload(**dump_overrides) -> dict:
    dump = _valid_dump()
    dump.update(dump_overrides)
    return {
        "protocolVersion": 2,
        "requestId": "TEST_req-observability",
        "scope": {"sellerId": "TEST_seller-1", "advertiserId": "TEST_adv-1"},
        "dump": dump,
    }


def test_schema_invalid_logs_field_detail_to_stderr(api_client, readwrite_key, capsys):
    """A Pydantic-level rejection must surface the failing field on stderr.

    Regression guard for the 2026-08-30 blind spot: the audit row only
    said ``SCHEMA_INVALID``; the operator had to guess which of the ~15
    record fields was malformed.
    """
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="2026-08-30T18:43:00"),  # no timezone → invalid
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            # Correlation id travels in the header (plugin-integration §2):
            # on SCHEMA_INVALID the body never parses, so payload.requestId
            # is unavailable to the handler.
            "X-Request-Id": "TEST_req-observability",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "SCHEMA_INVALID"

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err, (
        f"expected diagnostic line on stderr, got: {err!r}"
    )
    assert "SCHEMA_INVALID" in err
    # Field-level detail from the Pydantic message must be present.
    assert "capturedAt" in err
    # Request correlation id for joining against the audit table.
    assert "TEST_req-observability" in err


def test_schema_invalid_stderr_line_does_not_leak_credentials(
    api_client, readwrite_key, capsys
):
    """The diagnostic line must not echo the bearer token or request body."""
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="2026-08-30T18:43:00"),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 400, r.text

    err = capsys.readouterr().err
    assert readwrite_key not in err
    assert "Bearer" not in err
    assert "Authorization" not in err


def test_malformed_json_logs_to_stderr(api_client, readwrite_key, capsys):
    """MALFORMED_JSON rejections go through the same diagnostic path."""
    r = api_client.post(
        _BATCHES,
        content=b'{"protocolVersion": 2, "scope": ',  # truncated JSON
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "MALFORMED_JSON"

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err
    assert "MALFORMED_JSON" in err


def test_unsupported_protocol_version_logs_to_stderr(api_client, readwrite_key, capsys):
    """UNSUPPORTED_PROTOCOL_VERSION also surfaces on stderr."""
    payload = _payload()
    payload["protocolVersion"] = 99
    r = api_client.post(
        _BATCHES,
        content=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "UNSUPPORTED_PROTOCOL_VERSION"

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err
    assert "UNSUPPORTED_PROTOCOL_VERSION" in err


def test_oversized_body_logs_to_stderr(api_client, readwrite_key, capsys, monkeypatch):
    """413 PAYLOAD_TOO_LARGE goes through the same diagnostic path."""
    monkeypatch.setattr("tts_erp_v2.api.v2.analytics.MAX_BODY_BYTES", 64)
    r = api_client.post(
        _BATCHES,
        content=b'{"protocolVersion": 2, "scope": {}, "records": ["' + b"x" * 64,
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 413, r.text

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err


# ─── Structured errors[] in response body ─────────────────────────────
# Pydantic errors() → 安全三元组（loc/msg/type，丢 input/ctx/url），
# Chrome extension 据此按字段路径分支，不用解析自由文本。审计表
# error_message 列让 ops 在 stderr 轮转后仍可 SQL 查历史 400。


def test_schema_invalid_response_carries_structured_errors_single_field(
    api_client,
    readwrite_key,
):
    """A single failing field shows up as one structured entry."""
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="2026-08-30T18:43:00"),  # missing timezone
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "SCHEMA_INVALID"
    assert "errors" in body, "structured errors[] missing from 400 envelope"
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) == 1
    err = body["errors"][0]
    # Safe identifier triple only — no raw input values, no ctx, no url.
    assert set(err.keys()) == {"loc", "msg", "type"}
    assert err["loc"] == ["dump", "capturedAt"]
    assert err["type"] == "value_error"
    assert "timezone" in err["msg"]


def test_schema_invalid_response_carries_structured_errors_multiple_fields(
    api_client,
    readwrite_key,
):
    """All dump-field validation failures surface as separate entries in order."""
    payload = _payload(
        endpoint="",  # min_length=1 → string_too_short
        campaignId="",  # min_length=1 → string_too_short
        schemaVersion=0,  # ge=1 → greater_than_equal
    )
    r = api_client.post(
        _BATCHES,
        json=payload,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "SCHEMA_INVALID"
    errors = body["errors"]
    assert len(errors) == 3
    assert errors[0]["loc"] == ["dump", "endpoint"]
    assert errors[0]["type"] == "string_too_short"
    assert errors[1]["loc"] == ["dump", "campaignId"]
    assert errors[1]["type"] == "string_too_short"
    assert errors[2]["loc"] == ["dump", "schemaVersion"]
    assert errors[2]["type"] == "greater_than_equal"


def test_schema_invalid_response_strips_input_and_ctx_from_structured_errors(
    api_client,
    readwrite_key,
):
    """The structured triple must NEVER carry ``input`` or ``ctx`` —
    both can echo the offending field value, and the policy is to keep
    them out of the response envelope even when the value is innocuous.

    Regression guard: a future cleanup that pipes Pydantic's errors()
    straight through would leak ``input: '2026-08-30T18:43:00'`` for the
    naive-capturedAt test, or ``ctx: {min_length: ...}`` for the same.
    """
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="2026-08-30T18:43:00"),  # 缺时区 → invalid
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    body = r.json()
    err = body["errors"][0]
    assert "input" not in err
    assert "ctx" not in err
    assert "url" not in err


def test_schema_invalid_response_does_not_leak_token_or_body(
    api_client,
    readwrite_key,
):
    """Bearer token, request body fragments, or Pydantic-rendered input
    values must not appear in ``errors[]`` either. Mirrors the existing
    stderr-line test but on the structured side."""
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="not-a-real-datetime-bearer-abcdef"),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    body = r.json()
    # Token never appears anywhere in the response.
    assert readwrite_key not in json.dumps(body)
    # Token-shaped substring never appears.
    for err in body["errors"]:
        assert readwrite_key not in json.dumps(err)


def test_non_schema_400_paths_omit_errors_field(api_client, readwrite_key):
    """The ``errors`` field is reserved for SCHEMA_INVALID — adding it
    to MALFORMED_JSON / UNSUPPORTED_PROTOCOL_VERSION would change the
    JSON contract for those error codes. Pin that the field is absent."""
    # MALFORMED_JSON — truncated body
    r1 = api_client.post(
        _BATCHES,
        content=b"{",
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r1.status_code == 400
    assert r1.json()["code"] == "MALFORMED_JSON"
    assert "errors" not in r1.json(), "errors[] must be SCHEMA_INVALID-only"

    # UNSUPPORTED_PROTOCOL_VERSION — version 99
    payload = _payload()
    payload["protocolVersion"] = 99
    r2 = api_client.post(
        _BATCHES,
        content=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r2.status_code == 400
    assert r2.json()["code"] == "UNSUPPORTED_PROTOCOL_VERSION"
    assert "errors" not in r2.json()


def test_schema_invalid_persists_error_message_in_audit_log(
    api_client,
    readwrite_key,
    db_engine,
):
    """The audit row must carry the same sanitized Pydantic message
    that goes to stderr — ops need a queryable second copy after the
    stderr log rotates.

    v2 化后 write_audit 在同步 handler 内同步提交（独立 engine 连接），
    响应返回时审计行已落库，无需旧版的轮询等待。
    """
    request_id = "TEST_req-audit-error-message"
    r = api_client.post(
        _BATCHES,
        json=_payload(capturedAt="2026-08-30T18:43:00"),
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "X-Request-Id": request_id,
        },
    )
    assert r.status_code == 400

    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        row = conn.execute(
            text(
                "SELECT error_code, error_message FROM analytics.ad_audit_log "
                "WHERE request_id = :rid ORDER BY created_at DESC LIMIT 1"
            ),
            {"rid": request_id},
        ).fetchone()

    assert row is not None, "audit row missing for SCHEMA_INVALID request"
    error_code, error_message = row
    assert error_code == "SCHEMA_INVALID"
    assert error_message is not None
    # The sanitized stderr line is whitespace-flattened and capped at
    # 500 chars; same payload goes to the DB column.
    assert "capturedAt" in error_message
    assert "timezone" in error_message
    # No newlines (stderr contract is single-line grep-friendly).
    assert "\n" not in error_message
