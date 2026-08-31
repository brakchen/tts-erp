"""Handler-level tests: analytics_sync error observability.

Production incident 2026-08-30: Chrome-extension clients got
``HTTP 400 SCHEMA_INVALID`` on ``POST /v1/analytics/sync/batches`` but
the audit table (``analytics_audit_log``) only stores the error *code*,
not the Pydantic field-level message, and the v2 access-log middleware
only records the request body *size*. Result: ops could not tell which
field failed validation without asking the client.

These tests pin the contract that every ``_audit_and_error`` rejection
also writes one sanitized diagnostic line to stderr — field-level
Pydantic detail, request id, key prefix — WITHOUT echoing the request
body or any credential material.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _valid_record() -> dict:
    return {
        "idempotencyKey": "a" * 64,
        "storageKey": "productAnalyses",
        "campaignId": "TEST_campaign-1",
        "day": "2026-08-30",
        "page": 1,
        "expectedPageCount": 1,
        "endpoint": "/oec_ads/report",
        "method": "POST",
        "response": {"data": []},
        "source": "background_poll",
        "capturedAt": "2026-08-30T18:43:00.000Z",
        "schemaVersion": 2,
    }


def _payload(**record_overrides) -> dict:
    record = _valid_record()
    record.update(record_overrides)
    return {
        "protocolVersion": 2,
        "requestId": "req-test-observability",
        "scope": {"sellerId": "TEST_seller-1", "advertiserId": "TEST_adv-1"},
        "records": [record],
    }


def test_schema_invalid_logs_field_detail_to_stderr(api_client, readwrite_key, capsys):
    """A Pydantic-level rejection must surface the failing field on stderr.

    Regression guard for the 2026-08-30 blind spot: the audit row only
    said ``SCHEMA_INVALID``; the operator had to guess which of the ~15
    record fields was malformed.
    """
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json=_payload(capturedAt="2026-08-30T18:43:00"),  # no timezone → invalid
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            # Correlation id travels in the header (plugin-integration §2):
            # on SCHEMA_INVALID the body never parses, so payload.requestId
            # is unavailable to the handler.
            "X-Request-Id": "req-test-observability",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "SCHEMA_INVALID"

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err, f"expected diagnostic line on stderr, got: {err!r}"
    assert "SCHEMA_INVALID" in err
    # Field-level detail from the Pydantic message must be present.
    assert "capturedAt" in err
    # Request correlation id for joining against the audit table.
    assert "req-test-observability" in err


def test_schema_invalid_stderr_line_does_not_leak_credentials(
    api_client, readwrite_key, capsys
):
    """The diagnostic line must not echo the bearer token or request body."""
    r = api_client.post(
        "/v1/analytics/sync/batches",
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
        "/v1/analytics/sync/batches",
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
        "/v1/analytics/sync/batches",
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
    monkeypatch.setattr("analytics_sync.app.MAX_BODY_BYTES", 64)
    r = api_client.post(
        "/v1/analytics/sync/batches",
        content=b'{"protocolVersion": 2, "scope": {}, "records": ["' + b"x" * 64,
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 413, r.text

    err = capsys.readouterr().err
    assert "[analytics-sync]" in err
    assert "PAYLOAD_TOO_LARGE" in err
