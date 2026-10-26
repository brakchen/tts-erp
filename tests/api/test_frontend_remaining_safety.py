"""Remaining-console guards; direct handlers never run jobs or touch storage."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from tts_erp_v2.access import AccessGrant, AuthMode, Role
from tts_erp_v2.api.v2 import admin, sync_status
from tts_erp_v2.db import base
from tts_erp_v2.db.models.integration import SyncJob

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


def _request(
    method: str = "cookie", marker: str | None = None, role: str = "admin"
) -> Request:
    # AuthMiddleware already resolved cookie precedence. A supplied key must not
    # exempt the resolved cookie mutation from the canonical CSRF contract.
    headers = [(b"authorization", b"Bearer TEST_unused_key")]
    if marker is not None:
        headers.append((b"x-requested-with", marker.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": headers,
            "auth_method": method,
            "access_grant": AccessGrant(
                mode=AuthMode.ENFORCE, role=Role(role), bypass=False
            ),
        }
    )


@pytest.fixture
def effects(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        admin,
        "_require_registered_job",
        lambda name: calls.append(("registered", name)),
    )
    monkeypatch.setattr(
        admin,
        "_validate_manual_shop_scope",
        lambda *args: calls.append(("scope", *args)),
    )
    monkeypatch.setattr(
        base, "get_engine", lambda: calls.append(("engine",)) or object()
    )

    class FakeSession:
        def __init__(self, _engine):
            calls.append(("session",))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def begin(self):
            return nullcontext()

    monkeypatch.setattr(admin, "Session", FakeSession)
    monkeypatch.setattr(
        admin,
        "set_job_enabled",
        lambda _session, name, enabled: calls.append(("write", name, enabled)),
    )
    monkeypatch.setattr(
        admin,
        "run_scheduled_job_once",
        lambda *args, **kwargs: calls.append(("run", args, kwargs)),
    )
    return calls


@pytest.mark.parametrize("marker", [None, "TEST_wrong_marker"])
@pytest.mark.parametrize("operation", ["enabled", "trigger"])
def test_cookie_mutation_is_refused_before_validation_storage_or_enqueue(
    effects, marker, operation
):
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        if operation == "enabled":
            admin.set_sync_job_enabled(
                _request(marker=marker),
                "TEST_job",
                admin.SyncJobEnabledBody(enabled=False),
            )
        else:
            admin.trigger_sync_job(
                _request(marker=marker),
                "TEST_job",
                admin.SyncJobTriggerBody(),
                background,
            )
    assert raised.value.status_code == 403
    assert effects == []
    assert background.tasks == []


@pytest.mark.parametrize(
    "auth_method,marker", [("cookie", "tts-erp"), ("api_key", None)]
)
@pytest.mark.parametrize("operation", ["enabled", "trigger"])
def test_existing_cookie_marker_and_key_paths_reach_only_owned_fakes(
    effects, auth_method, marker, operation
):
    request = _request(auth_method, marker)
    background = BackgroundTasks()
    if operation == "enabled":
        response = admin.set_sync_job_enabled(
            request, "TEST_job", admin.SyncJobEnabledBody(enabled=False)
        )
        assert response.enabled is False
        assert ("write", "TEST_job", False) in effects
    else:
        response = admin.trigger_sync_job(
            request, "TEST_job", admin.SyncJobTriggerBody(), background
        )
        assert response.accepted is True
        assert len(background.tasks) == 1
        assert background.tasks[0].func is admin.run_scheduled_job_once
    assert not any(call[0] == "run" for call in effects)


@pytest.mark.parametrize(
    "operation,role",
    [("enabled", "readwrite"), ("enabled", "readonly"), ("trigger", "readonly")],
)
def test_server_roles_still_deny_before_effects(effects, operation, role):
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        if operation == "enabled":
            admin.set_sync_job_enabled(
                _request(marker="tts-erp", role=role),
                "TEST_job",
                admin.SyncJobEnabledBody(enabled=True),
            )
        else:
            admin.trigger_sync_job(
                _request(marker="tts-erp", role=role),
                "TEST_job",
                admin.SyncJobTriggerBody(),
                background,
            )
    assert raised.value.status_code == 403
    assert effects == [] and background.tasks == []


@pytest.mark.parametrize(
    "raw",
    ["TEST_upstream_authorization_and_body", "TEST_signed_url_and_customer_response"],
)
def test_sync_status_publishes_error_presence_not_stored_upstream_text(raw):
    now = datetime(2026, 10, 24, tzinfo=UTC)
    last = SyncJob(
        job_name="TEST_job",
        started_at=now,
        finished_at=now,
        status="failed",
        error_message=raw,
    )
    result = sync_status._build_status("TEST_job", 60, last, now)
    assert result.last_error == "最近一次运行存在错误；详细信息不公开"
    assert raw not in result.model_dump_json()
    assert result.last_status == "failed" and result.severity == "ok"
    assert last.error_message == raw  # Do not mutate or destroy the audit fact.


@pytest.mark.parametrize(
    "last",
    [
        None,
        SyncJob(
            job_name="TEST_job",
            started_at=datetime(2026, 10, 24, tzinfo=UTC),
            finished_at=None,
            status="success",
            error_message=None,
        ),
    ],
)
def test_sync_status_without_error_keeps_absence(last):
    result = sync_status._build_status(
        "TEST_job", 60, last, datetime(2026, 10, 24, tzinfo=UTC)
    )
    assert result.last_error is None
