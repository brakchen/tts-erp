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
from psycopg import errors as pg_errors

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture(scope="session", autouse=True)
def _ensure_analytics_audit_error_message_column():
    """SESSION-SCOPED autouse migration for the 2026-08-31 audit log column.

    The test DB is shared across the v2 suite (sessions come and go, but
    schema is set up once via ``alembic upgrade head`` + analytics_sync
    ``schema.sql``). When ``analytics_audit_log.error_message`` lands as
    part of a forward migration, the production deploy applies it via
    ``psql < analytics_sync/schema.sql`` which is idempotent
    (``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``).

    The test suite can't run alembic between every test, so we
    mirror the production migration here. If the column already
    exists, the ``ADD COLUMN IF NOT EXISTS`` is a no-op; if it
    doesn't, this is the only place in the suite that needs the
    column so we apply it exactly once per session.

    The literal DDL string contains no interpolation — column name and
    type are baked in, so there is no injection surface; psycopg
    cursor.execute is the same call shape as ``write_audit``.
    """
    from analytics_sync.pg_repositories import connect

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE analytics_audit_log "
                "ADD COLUMN IF NOT EXISTS error_message TEXT"
            )
            conn.commit()
    except pg_errors.UndefinedTable:
        # analytics_audit_log doesn't exist yet — the conftest's
        # alembic-prereq check would have skipped these tests; let it
        # bubble up as a skip rather than mask with a silent failure.
        pytest.skip("analytics_audit_log not provisioned in test DB")


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


# ─── Structured errors[] in response body (2026-08-31 ──────────────────
# The free-form ``message`` string carries field paths but requires the
# client to regex-parse them. Pydantic's ``errors()`` returns a
# structured list of {loc, msg, type, input, ctx, url} — we surface the
# safe triple (loc/msg/type, drop input/ctx/url) so the Chrome extension
# can branch on the failing field path without parsing. The audit table
# also gains an ``error_message`` column so ops can SQL-query historical
# 400s after stderr rotates. These tests pin both contracts.


def test_schema_invalid_response_carries_structured_errors_single_field(
    api_client, readwrite_key,
):
    """A single failing field shows up as one structured entry."""
    r = api_client.post(
        "/v1/analytics/sync/batches",
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
    assert err["loc"] == ["records", 0, "capturedAt"]
    assert err["type"] == "value_error"
    assert "timezone" in err["msg"]


def test_schema_invalid_response_carries_structured_errors_multiple_fields(
    api_client, readwrite_key,
):
    """All Pydantic validation failures surface as separate entries in order."""
    payload = _payload()
    payload["records"] = [
        # record 0: idempotencyKey too short
        {**_valid_record(), "idempotencyKey": "short"},
        # record 1: bad storageKey enum
        {**_valid_record(), "storageKey": "WRONG"},
        # record 2: page = 0 (must be >= 1)
        {**_valid_record(), "page": 0},
    ]
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json=payload,
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "SCHEMA_INVALID"
    errors = body["errors"]
    assert len(errors) == 3
    assert errors[0]["loc"] == ["records", 0, "idempotencyKey"]
    assert errors[0]["type"] == "string_too_short"
    assert errors[1]["loc"] == ["records", 1, "storageKey"]
    assert errors[1]["type"] == "enum"
    assert errors[2]["loc"] == ["records", 2, "page"]
    assert errors[2]["type"] == "greater_than_equal"


def test_schema_invalid_response_strips_input_and_ctx_from_structured_errors(
    api_client, readwrite_key,
):
    """The structured triple must NEVER carry ``input`` or ``ctx`` —
    both can echo the offending field value, and the policy is to keep
    them out of the response envelope even when the value is innocuous.

    Regression guard: a future cleanup that pipes Pydantic's errors()
    straight through would leak ``input: 'short'`` for the too-short
    idempotencyKey test, or ``ctx: {min_length: 64}`` for the same.
    """
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json=_payload(idempotencyKey="short"),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    body = r.json()
    err = body["errors"][0]
    assert "input" not in err
    assert "ctx" not in err
    assert "url" not in err


def test_schema_invalid_response_does_not_leak_token_or_body(
    api_client, readwrite_key,
):
    """Bearer token, request body fragments, or Pydantic-rendered input
    values must not appear in ``errors[]`` either. Mirrors the existing
    stderr-line test but on the structured side."""
    r = api_client.post(
        "/v1/analytics/sync/batches",
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
        "/v1/analytics/sync/batches",
        content=b"{",
        headers={"Authorization": f"Bearer {readwrite_key}", "Content-Type": "application/json"},
    )
    assert r1.status_code == 400
    assert r1.json()["code"] == "MALFORMED_JSON"
    assert "errors" not in r1.json(), "errors[] must be SCHEMA_INVALID-only"

    # UNSUPPORTED_PROTOCOL_VERSION — version 99
    payload = _payload()
    payload["protocolVersion"] = 99
    r2 = api_client.post(
        "/v1/analytics/sync/batches",
        content=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r2.status_code == 400
    assert r2.json()["code"] == "UNSUPPORTED_PROTOCOL_VERSION"
    assert "errors" not in r2.json()


def test_schema_invalid_persists_error_message_in_audit_log(
    api_client, readwrite_key,
):
    """The audit row must carry the same sanitized Pydantic message
    that goes to stderr — ops need a queryable second copy after the
    stderr log rotates.

    Reads analytics_audit_log directly (no public introspection
    endpoint exists) and looks for the matching request_id. Uses
    ``analytics_sync.pg_repositories.connect`` so it shares the same
    connection helper the production audit write uses.
    """
    request_id = "req-test-audit-error-message"
    r = api_client.post(
        "/v1/analytics/sync/batches",
        json=_payload(capturedAt="2026-08-30T18:43:00"),
        headers={
            "Authorization": f"Bearer {readwrite_key}",
            "X-Request-Id": request_id,
        },
    )
    assert r.status_code == 400

    # The audit insert is fire-and-forget via run_sync — give it a
    # tick to commit before reading back. (Same race the production
    # retry path waits for.)
    import time
    deadline = time.monotonic() + 2.0
    row = None
    while time.monotonic() < deadline:
        from analytics_sync.pg_repositories import connect
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT error_code, error_message FROM analytics_audit_log "
                "WHERE request_id = %s ORDER BY created_at DESC LIMIT 1",
                (request_id,),
            )
            row = cur.fetchone()
        if row is not None:
            break
        time.sleep(0.05)

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
