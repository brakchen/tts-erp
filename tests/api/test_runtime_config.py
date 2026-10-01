"""Integration tests for versioned runtime configuration management."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.runtime_config.resolver import resolve_runtime_config

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "X-Requested-With": "tts-erp"}


def _schema(*, secret_reference: bool = False) -> dict:
    value_schema = {"type": "string", "minLength": 1}
    value_key = "token" if secret_reference else "value"
    if secret_reference:
        value_schema["format"] = "secret-reference"
    return {
        "type": "object",
        "required": ["endpoint", value_key],
        "additionalProperties": False,
        "properties": {
            "endpoint": {"type": "string", "minLength": 1},
            value_key: value_schema,
        },
    }


def _clear_runtime_config(db_engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_revisions "
                "WHERE config_key LIKE 'test_runtime_%'"
            )
        )
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_items "
                "WHERE config_key LIKE 'test_runtime_%'"
            )
        )
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_secrets "
                "WHERE name LIKE 'test_runtime_%'"
            )
        )


def test_runtime_config_draft_publish_snapshot_and_secret_redaction(
    api_client, db_engine, readonly_key, readwrite_key
):
    _clear_runtime_config(db_engine)
    try:
        create = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.api",
                "displayName": "TEST runtime API",
                "jsonSchema": _schema(secret_reference=True),
                "draftPayload": {
                    "endpoint": "https://draft.example",
                    "token": "secret://test_runtime_token",
                },
            },
        )
        assert create.status_code == 201, create.text
        draft_version = create.json()["draftVersion"]

        secret = api_client.put(
            "/v2/config/runtime/secrets/test_runtime_token",
            headers=_headers(readwrite_key),
            json={"value": "TEST_plaintext_token"},
        )
        assert secret.status_code == 200, secret.text
        assert secret.json()["ref"] == "secret://test_runtime_token"
        assert "TEST_plaintext_token" not in secret.text

        save = api_client.put(
            "/v2/config/runtime/items/test_runtime.api/draft",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": draft_version,
                "payload": {
                    "endpoint": "https://published.example",
                    "token": "secret://test_runtime_token",
                },
                "rollout": [
                    {
                        "name": "canary",
                        "basisPoints": 10000,
                        "payload": {
                            "endpoint": "https://canary.example",
                            "token": "secret://test_runtime_token",
                        },
                    }
                ],
            },
        )
        assert save.status_code == 200, save.text
        publish = api_client.post(
            "/v2/config/runtime/items/test_runtime.api/publish",
            headers=_headers(readwrite_key),
            json={"expectedDraftVersion": save.json()["draftVersion"], "comment": "TEST publish"},
        )
        assert publish.status_code == 200, publish.text
        assert publish.json()["publishedVersion"] == 1

        assert api_client.get(
            "/v2/config/runtime/items", headers=_headers(readonly_key)
        ).status_code == 403
        assert api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_subject",
            headers=_headers(readonly_key),
        ).status_code == 403

        listed = api_client.get("/v2/config/runtime/items", headers=_headers(readwrite_key))
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["publishedVersion"] == 1
        assert "draftPayload" not in listed.text

        snapshot = api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_subject",
            headers=_headers(readwrite_key),
        )
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["items"]["test_runtime.api"]["payload"] == {
            "endpoint": "https://canary.example",
            "token": "secret://test_runtime_token",
        }
        assert "TEST_plaintext_token" not in snapshot.text
        etag = snapshot.headers["etag"]
        assert api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_subject",
            headers={**_headers(readwrite_key), "If-None-Match": etag},
        ).status_code == 304
        assert api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_other_subject",
            headers={**_headers(readwrite_key), "If-None-Match": etag},
        ).status_code == 200

        blocked_secret_retire = api_client.post(
            "/v2/config/runtime/secrets/test_runtime_token/retire",
            headers=_headers(readwrite_key),
        )
        assert blocked_secret_retire.status_code == 409, blocked_secret_retire.text

        with Session(db_engine) as sess:
            resolved = resolve_runtime_config(
                sess,
                config_key="test_runtime.api",
                rollout_salt="TEST_salt",
                subject="TEST_subject",
            )
        assert resolved["payload"]["token"] == "TEST_plaintext_token"
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_optimistic_lock_and_rollback(api_client, db_engine, readwrite_key):
    _clear_runtime_config(db_engine)
    try:
        created = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.rollback",
                "displayName": "TEST rollback",
                "jsonSchema": _schema(),
                "draftPayload": {"endpoint": "https://v1.example", "value": "v1"},
            },
        )
        assert created.status_code == 201, created.text
        published = api_client.post(
            "/v2/config/runtime/items/test_runtime.rollback/publish",
            headers=_headers(readwrite_key),
            json={"expectedDraftVersion": created.json()["draftVersion"]},
        )
        assert published.json()["publishedVersion"] == 1

        detail = api_client.get(
            "/v2/config/runtime/items/test_runtime.rollback", headers=_headers(readwrite_key)
        )
        stale = api_client.put(
            "/v2/config/runtime/items/test_runtime.rollback/draft",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": detail.json()["draftVersion"] - 1,
                "payload": {"endpoint": "https://stale.example", "value": "stale"},
            },
        )
        assert stale.status_code == 409
        saved = api_client.put(
            "/v2/config/runtime/items/test_runtime.rollback/draft",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": detail.json()["draftVersion"],
                "payload": {"endpoint": "https://v2.example", "value": "v2"},
            },
        )
        assert saved.status_code == 200, saved.text
        assert api_client.post(
            "/v2/config/runtime/items/test_runtime.rollback/publish",
            headers=_headers(readwrite_key),
            json={"expectedDraftVersion": saved.json()["draftVersion"]},
        ).json()["publishedVersion"] == 2

        current = api_client.get(
            "/v2/config/runtime/items/test_runtime.rollback", headers=_headers(readwrite_key)
        )
        rollback = api_client.post(
            "/v2/config/runtime/items/test_runtime.rollback/rollback",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": current.json()["draftVersion"],
                "targetVersion": 1,
            },
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json() == {
            "configKey": "test_runtime.rollback",
            "publishedVersion": 3,
            "rolledBackFrom": 1,
        }
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_readonly_client_uses_redacted_snapshot() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "runtime-configs.js"
    ).read_text(encoding="utf-8")

    assert "async function loadPublished(item)" in source
    assert 'api("/snapshot")' in source
    assert "if (state.writable)" in source
    assert "function installJsonTools()" in source
    assert 'data-action="format"' in source
    assert 'data-action="compact"' in source
    assert 'data-action="validate"' in source
    assert "JSON.stringify(value, null, 2)" in source


def test_runtime_config_rejects_empty_secret_reference(api_client, db_engine, readwrite_key):
    _clear_runtime_config(db_engine)
    try:
        response = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.invalid_secret",
                "displayName": "TEST invalid secret",
                "jsonSchema": _schema(secret_reference=True),
                "draftPayload": {"endpoint": "https://example.test", "token": "secret://"},
            },
        )
        assert response.status_code == 422, response.text
        assert "non-empty secret:// reference" in response.text
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_retirement_lifecycle(api_client, db_engine, readwrite_key):
    _clear_runtime_config(db_engine)
    try:
        created = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.retire",
                "displayName": "TEST retirement",
                "jsonSchema": _schema(),
                "draftPayload": {"endpoint": "https://v1.example", "value": "v1"},
            },
        )
        assert created.status_code == 201, created.text
        assert api_client.post(
            "/v2/config/runtime/items/test_runtime.retire/publish",
            headers=_headers(readwrite_key),
            json={"expectedDraftVersion": created.json()["draftVersion"]},
        ).status_code == 200
        retired = api_client.post(
            "/v2/config/runtime/items/test_runtime.retire/retire",
            headers=_headers(readwrite_key),
        )
        assert retired.json()["status"] == "retired"
        assert api_client.put(
            "/v2/config/runtime/items/test_runtime.retire/draft",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": 2,
                "payload": {"endpoint": "https://v2.example", "value": "v2"},
            },
        ).status_code == 409
        assert "test_runtime.retire" not in api_client.get(
            "/v2/config/runtime/snapshot", headers=_headers(readwrite_key)
        ).json()["items"]
        listed = api_client.get(
            "/v2/config/runtime/items?includeRetired=true",
            headers=_headers(readwrite_key),
        )
        assert listed.json()["items"][0]["retiredAt"] is not None
        assert api_client.post(
            "/v2/config/runtime/items/test_runtime.retire/restore",
            headers=_headers(readwrite_key),
        ).json()["status"] == "active"

        secret = api_client.put(
            "/v2/config/runtime/secrets/test_runtime_unused",
            headers=_headers(readwrite_key),
            json={"value": "TEST_unused"},
        )
        assert secret.status_code == 200, secret.text
        assert api_client.post(
            "/v2/config/runtime/secrets/test_runtime_unused/retire",
            headers=_headers(readwrite_key),
        ).json()["status"] == "retired"
        assert api_client.put(
            "/v2/config/runtime/secrets/test_runtime_unused",
            headers=_headers(readwrite_key),
            json={"value": "TEST_replacement"},
        ).status_code == 409
        assert api_client.post(
            "/v2/config/runtime/secrets/test_runtime_unused/restore",
            headers=_headers(readwrite_key),
        ).json()["status"] == "active"
    finally:
        _clear_runtime_config(db_engine)
