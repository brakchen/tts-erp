"""Actual app/auth and two-session boundaries; no lifespan or bootstrap DDL.

These regressions deliberately use the canonical database credential owner.
External services/background startup are never activated.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tts_erp_v2.access import authenticate_key, clear_credential_cache, hash_key
from tts_erp_v2.db.models.plugin_status import (
    StatusDecision,
    StatusInstance,
    StatusNotificationOutbox,
    StatusReport,
)
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.middleware import rate_limit
from tts_erp_v2.plugin.status import service
from tts_erp_v2.plugin.status.contracts import (
    InstanceRegistrationRequest,
    report_payload_hash,
    status_report_from_wire,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
REPORT = "/v2/plugin/status/report"


@dataclass(frozen=True)
class Provisioned:
    instance_id: uuid.UUID
    key_id: int
    token: str


@pytest.fixture()
def provisioned(db_engine: Engine) -> Iterator[Provisioned]:
    with Session(db_engine) as session:
        payload = service.register_instance(
            session,
            command=InstanceRegistrationRequest.model_validate(
                {
                    "pluginId": "ads-data-sync",
                    "scope": {"kind": "installation"},
                    "capabilities": [],
                }
            ),
            now=datetime.now(UTC),
        )
        session.commit()
        instance_id = uuid.UUID(payload["instanceId"])
        key_id = payload["statusApiKey"]["keyId"]
        token = payload["statusApiKey"]["value"]
    clear_credential_cache()
    rate_limit.reset_shared(limit=100)
    try:
        yield Provisioned(instance_id, key_id, token)
    finally:
        with Session(db_engine) as session:
            for model in (StatusNotificationOutbox, StatusDecision, StatusReport):
                session.execute(delete(model).where(model.instance_id == instance_id))
            session.execute(
                delete(StatusInstance).where(StatusInstance.id == instance_id)
            )
            session.execute(delete(ApiKey).where(ApiKey.id == key_id))
            session.commit()
        rate_limit.reset_shared(limit=100)
        clear_credential_cache()


def report_body(instance_id: uuid.UUID) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "pluginId": "ads-data-sync",
        "pluginVersion": "TEST_1",
        "instanceId": str(instance_id),
        "eventId": str(uuid.uuid4()),
        "reportSeq": "1",
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "eventType": "heartbeat",
        "reasonCode": "NONE",
        "scope": {"kind": "installation"},
        "execution": {"phase": "running", "reasonCode": "NONE"},
        "capabilities": [],
        "capabilityState": {},
        "decisionResults": [],
    }


def client_for(monkeypatch: pytest.MonkeyPatch, mode: str) -> TestClient:
    from tts_erp_v2.app import build_app

    monkeypatch.setenv("TTS_ERP_AUTH_MODE", mode)
    return TestClient(build_app())  # Never enter a lifespan context.


def assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    request_id = payload["requestId"]
    assert str(uuid.UUID(request_id)) == request_id
    assert response.headers["x-request-id"] == request_id
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["retryable"], bool)


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_actual_app_valid_status_report(
    mode: str, monkeypatch: pytest.MonkeyPatch, provisioned: Provisioned
) -> None:
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT,
            headers={"Authorization": f"Bearer {provisioned.token}"},
            json=report_body(provisioned.instance_id),
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["receipt"]["projectionUpdated"] is True
        assert response.headers["x-request-id"] == response.json()["requestId"]
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_actual_app_missing_bearer_is_protocol_401(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT,
            headers={"Accept": "text/html", "x-request-id": "not-a-uuid"},
            json={},
        )
        assert_error(response, 401, "INVALID_CREDENTIAL")
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("mutation", ["general", "admin", "readonly"])
def test_actual_app_wrong_purpose_or_exact_role_is_json_403(
    mode: str,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
    db_engine: Engine,
) -> None:
    with Session(db_engine) as session:
        key = session.get(ApiKey, provisioned.key_id)
        assert key is not None
        if mutation == "general":
            key.purpose = "general"
        else:
            key.role = mutation
        session.commit()
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT,
            headers={
                "Authorization": f"Bearer {provisioned.token}",
                "Accept": "text/html",
            },
            json={},
        )
        assert_error(response, 403, "PURPOSE_DENIED")
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("state", ["disabled", "rotated"])
def test_actual_app_current_key_rejects_cached_success(
    mode: str,
    state: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
    db_engine: Engine,
) -> None:
    assert authenticate_key(provisioned.token) is not None
    with Session(db_engine) as session:
        key = session.get(ApiKey, provisioned.key_id)
        assert key is not None
        if state == "rotated":
            key.rotated_to_key_hash = "0" * 64
        else:
            key.status = "disabled"
        session.commit()
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT, headers={"Authorization": f"Bearer {provisioned.token}"}, json={}
        )
        assert_error(response, 401, "INVALID_CREDENTIAL")
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_actual_app_both_rate_limit_paths_preserve_protocol(
    authenticated: bool,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
) -> None:
    rate_limit.reset_shared(limit=1)
    client = client_for(monkeypatch, mode)
    headers = {"Authorization": f"Bearer {provisioned.token}"} if authenticated else {}
    try:
        first = client.post(REPORT, headers=headers, json={})
        assert first.status_code == (400 if authenticated else 401)
        limited = client.post(REPORT, headers=headers, json={})
        assert_error(limited, 429, "RATE_LIMITED")
        assert int(limited.headers["retry-after"]) > 0
        assert limited.headers["x-ratelimit-limit"] == "1"
        assert limited.headers["x-ratelimit-remaining"] == "0"
    finally:
        client.close()


def test_actual_report_logs_redact_query_and_credential_prefix(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tts_erp_v2.middleware.access_log import logger

    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", "1")
    logger.addHandler(caplog.handler)
    client = client_for(monkeypatch, "enforce")
    token = "TEST_never_issued_status_log_sentinel"
    try:
        response = client.post(
            REPORT + "?sensitive=TEST_QUERY_SENTINEL",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert response.status_code == 401
        assert "TEST_QUERY_SENTINEL" not in caplog.text
        assert token[:16] not in caplog.text
        captured = capsys.readouterr()
        assert token[:16] not in captured.err
        assert "TEST_QUERY_SENTINEL" not in captured.out + captured.err
    finally:
        client.close()
        logger.removeHandler(caplog.handler)


def test_actual_report_exception_log_redacts_query(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tts_erp_v2.middleware import auth
    from tts_erp_v2.middleware.access_log import logger

    async def fail_auth(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("TEST_injected_auth_failure")

    monkeypatch.setattr(auth, "evaluate_access", fail_auth)
    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", "1")
    logger.addHandler(caplog.handler)
    client = client_for(monkeypatch, "enforce")
    try:
        with pytest.raises(RuntimeError, match="TEST_injected_auth_failure"):
            client.post(REPORT + "?sensitive=TEST_EXCEPTION_QUERY_SENTINEL", json={})
        assert "request crashed" in caplog.text
        assert "TEST_EXCEPTION_QUERY_SENTINEL" not in caplog.text
    finally:
        client.close()
        logger.removeHandler(caplog.handler)


@pytest.mark.parametrize(
    "change",
    [
        "disable",
        "rotate",
        "rebind",
        "role",
        "purpose",
        "instance_disable",
        "digest",
        "expired",
    ],
)
@pytest.mark.parametrize("commit_change", [False, True])
def test_ingest_rechecks_authentication_after_other_transaction(
    change: str, commit_change: bool, provisioned: Provisioned, db_engine: Engine
) -> None:
    """A authenticates; B commits or rolls back; A takes fresh locks.

    No sleeps, threads or blocked-last_used timeout: both sessions are used
    synchronously, and B completes before A attempts the write boundary.
    """
    with Session(db_engine) as session_a:
        authenticated = service.authenticate_report(
            session_a, bearer_token=provisioned.token
        )
        with Session(db_engine) as session_b:
            key = session_b.get(ApiKey, provisioned.key_id)
            instance = session_b.get(StatusInstance, provisioned.instance_id)
            assert key is not None and instance is not None
            replacement: ApiKey | None = None
            if change == "disable":
                key.status = "disabled"
            elif change == "rotate":
                key.rotated_to_key_hash = "0" * 64
            elif change == "expired":
                key.last_used_at = datetime(1969, 12, 31, tzinfo=UTC)
            elif change == "role":
                key.role = "admin"
            elif change == "purpose":
                key.purpose = "general"
            elif change == "digest":
                key.key_hash = hash_key("TEST_changed_digest_" + str(uuid.uuid4()))
            elif change == "instance_disable":
                instance.enabled = False
            else:
                replacement = ApiKey(
                    key_hash=hash_key("TEST_rebind_" + str(uuid.uuid4())),
                    key_prefix="TEST_rebind",
                    name="TEST_status_replacement",
                    role="readwrite",
                    purpose="plugin_status",
                    status="active",
                )
                session_b.add(replacement)
                session_b.flush()
                instance.status_api_key_id = replacement.id
            replacement_id = replacement.id if replacement else None
            if commit_change:
                session_b.commit()
            else:
                session_b.rollback()
        report = status_report_from_wire(report_body(provisioned.instance_id))
        try:
            if commit_change:
                with pytest.raises(service.StatusServiceError) as refusal:
                    service.ingest_report(
                        session_a,
                        authenticated=authenticated,
                        instance_id=authenticated.instance_id,
                        report=report,
                        payload_hash=report_payload_hash(report),
                        request_id=str(uuid.uuid4()),
                        now=datetime.now(UTC),
                    )
                expected = (
                    "INVALID_CREDENTIAL"
                    if change in {"disable", "rotate", "digest", "expired"}
                    else "INSTANCE_DENIED"
                    if change in {"rebind", "instance_disable"}
                    else "PURPOSE_DENIED"
                )
                assert refusal.value.code == expected
                assert (
                    session_a.scalar(
                        select(StatusReport).where(
                            StatusReport.instance_id == authenticated.instance_id
                        )
                    )
                    is None
                )
            else:
                response = service.ingest_report(
                    session_a,
                    authenticated=authenticated,
                    instance_id=authenticated.instance_id,
                    report=report,
                    payload_hash=report_payload_hash(report),
                    request_id=str(uuid.uuid4()),
                    now=datetime.now(UTC),
                )
                assert response.receipt.projection_updated is True
        finally:
            session_a.rollback()
            if commit_change and replacement_id is not None:
                with Session(db_engine) as cleanup:
                    row = cleanup.get(StatusInstance, provisioned.instance_id)
                    assert row is not None
                    row.status_api_key_id = provisioned.key_id
                    cleanup.flush()
                    cleanup.execute(delete(ApiKey).where(ApiKey.id == replacement_id))
                    cleanup.commit()
    with Session(db_engine) as verify:
        assert (
            verify.execute(
                select(StatusReport).where(
                    StatusReport.instance_id == provisioned.instance_id
                )
            )
            .scalars()
            .all()
            == []
        )


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_report_store_unavailable_is_retryable_503(
    mode: str, monkeypatch: pytest.MonkeyPatch, provisioned: Provisioned
) -> None:
    from sqlalchemy.exc import OperationalError

    from tts_erp_v2.access import _credentials

    def unavailable(key_hash: str, *, require_current: bool = False) -> None:
        raise OperationalError("TEST_store_unavailable", {}, None)

    monkeypatch.setattr(_credentials, "_db_lookup", unavailable)
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT,
            headers={
                "Authorization": f"Bearer {provisioned.token}",
                "Accept": "text/html",
            },
            json={},
        )
        assert_error(response, 503, "STORE_UNAVAILABLE")
        assert response.json()["error"]["retryable"] is True
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
@pytest.mark.parametrize("carrier", ["cookie", "x-api-key", "invalid-bearer"])
def test_report_requires_bearer_not_cookie_or_xapi(
    mode: str,
    carrier: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
    ua_user_factory: Any,
    ua_session_factory: Any,
) -> None:
    user = ua_user_factory("test_ua_status_boundary", roles=("admin",))
    cookie = ua_session_factory(user.user_id)
    headers = {"Accept": "text/html", "Cookie": f"tts_erp_session={cookie}"}
    if carrier == "x-api-key":
        headers["X-API-Key"] = provisioned.token
    elif carrier == "invalid-bearer":
        headers["Authorization"] = "Bearer TEST_unknown_status_credential"
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(REPORT, headers=headers, json={})
        assert_error(response, 401, "INVALID_CREDENTIAL")
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v2/commerce/channel-accounts"),
        ("GET", "/v2/plugin/admin/instances"),
        ("GET", REPORT),
        ("POST", REPORT + "/other"),
        ("GET", "/v2/monitor/config"),
    ],
)
def test_status_key_has_no_other_authenticated_grant(
    mode: str,
    method: str,
    path: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
) -> None:
    client = client_for(monkeypatch, mode)
    try:
        response = client.request(
            method, path, headers={"Authorization": f"Bearer {provisioned.token}"}
        )
        assert response.status_code == 403
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_valid_bearer_ignores_cookie_and_preserves_uuid(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    provisioned: Provisioned,
    ua_user_factory: Any,
    ua_session_factory: Any,
) -> None:
    user = ua_user_factory("test_ua_status_cookie", roles=("admin",))
    cookie = ua_session_factory(user.user_id)
    request_id = str(uuid.uuid4())
    client = client_for(monkeypatch, mode)
    try:
        response = client.post(
            REPORT,
            headers={
                "Authorization": f"Bearer {provisioned.token}",
                "Cookie": f"tts_erp_session={cookie}",
                "x-request-id": request_id,
            },
            json=report_body(provisioned.instance_id),
        )
        assert response.status_code == 200
        assert (
            response.headers["x-request-id"]
            == response.json()["requestId"]
            == request_id
        )
    finally:
        client.close()


def test_ordinary_off_route_keeps_anonymous_dev_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = client_for(monkeypatch, "off")
    try:
        response = client.get("/v2/plugin/admin/instances")
        assert response.status_code == 200
    finally:
        client.close()


def test_live_authenticated_ingest_commits(
    provisioned: Provisioned, db_engine: Engine
) -> None:
    with Session(db_engine) as session:
        authenticated = service.authenticate_report(
            session, bearer_token=provisioned.token
        )
        report = status_report_from_wire(report_body(provisioned.instance_id))
        response = service.ingest_report(
            session,
            authenticated=authenticated,
            instance_id=authenticated.instance_id,
            report=report,
            payload_hash=report_payload_hash(report),
            request_id=str(uuid.uuid4()),
            now=datetime.now(UTC),
        )
        assert response.receipt.projection_updated is True
        session.commit()
    with Session(db_engine) as verify:
        rows = (
            verify.execute(
                select(StatusReport).where(
                    StatusReport.instance_id == provisioned.instance_id
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


def test_report_ingest_store_failure_is_retryable_503(
    monkeypatch: pytest.MonkeyPatch, provisioned: Provisioned
) -> None:
    from sqlalchemy.exc import OperationalError

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("TEST_ingest_store_unavailable", {}, None)

    monkeypatch.setattr(service, "ingest_report", unavailable)
    client = client_for(monkeypatch, "enforce")
    try:
        response = client.post(
            REPORT,
            headers={"Authorization": f"Bearer {provisioned.token}"},
            json=report_body(provisioned.instance_id),
        )
        assert_error(response, 503, "STORE_UNAVAILABLE")
        assert response.json()["error"]["retryable"] is True
    finally:
        client.close()


@pytest.mark.parametrize(
    ("method", "path", "redacted"),
    [
        ("POST", REPORT, True),
        ("POST", "/tts" + REPORT, True),
        ("GET", REPORT, False),
        ("POST", REPORT + "/other", False),
    ],
)
def test_access_log_query_rule_is_exact(
    method: str,
    path: str,
    redacted: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provisioned: Provisioned,
) -> None:
    from tts_erp_v2.middleware.access_log import logger

    monkeypatch.setenv("TTS_ERP_EXTERNAL_PREFIX", "/tts")
    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", "1")
    logger.addHandler(caplog.handler)
    client = client_for(monkeypatch, "enforce")
    try:
        response = client.request(
            method,
            path + "?sensitive=TEST_EXACT_QUERY",
            headers={"Authorization": f"Bearer {provisioned.token}"},
            json=report_body(provisioned.instance_id),
        )
        assert response.status_code == (200 if redacted else 403)
        assert ("TEST_EXACT_QUERY" not in caplog.text) is redacted
    finally:
        client.close()
        logger.removeHandler(caplog.handler)
