"""Integration tests for versioned runtime configuration management."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.config import (
    ALERT_CONFIG_KEY,
    SEED_FALLBACK_CONFIG,
    config_to_payload,
)
from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.runtime_config.repository import lock_referenced_secrets

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
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_revisions "
                "WHERE config_key LIKE 'test_runtime_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_items "
                "WHERE config_key LIKE 'test_runtime_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM config.runtime_config_secrets "
                "WHERE name LIKE 'test_runtime_%'"
            )
        )


def _clear_alert_runtime_config(db_engine) -> None:
    with Session(db_engine) as session:
        session.execute(
            delete(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY
            )
        )
        session.execute(
            delete(RuntimeConfigItem).where(
                RuntimeConfigItem.config_key == ALERT_CONFIG_KEY
            )
        )
        session.commit()


def _load_alert_migration():
    migration_path = (
        Path(__file__).parents[2] / "alembic/versions/0068_spu_deterioration_alert.py"
    )
    spec = importlib.util.spec_from_file_location(
        "alert_migration_0053_runtime_config_test", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _ensure_alert_migration(db_engine):
    migration = _load_alert_migration()
    with db_engine.connect() as connection:
        # pi-lens-ignore: python-sql-injection
        database = connection.execute(text("SELECT current_database()")).scalar_one()
        assert database.startswith("tts_erp_test_")
        assert database != "tts_erp_test_template"
        if inspect(connection).has_table(
            "spu_deterioration_alerts", schema="analytics"
        ):
            return migration
    with db_engine.begin() as connection:
        # pi-lens-ignore: python-sql-injection
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
        migration.__dict__["op"] = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    return migration


@pytest.fixture()
def _alert_config_cleanup(db_engine):
    yield
    _clear_alert_runtime_config(db_engine)


def test_alert_migration_seeds_strict_draft_idempotently_without_publication(
    db_engine, _alert_config_cleanup
) -> None:
    migration = _ensure_alert_migration(db_engine)
    _clear_alert_runtime_config(db_engine)
    with db_engine.begin() as connection:
        migration._seed_runtime_config(connection)
    with Session(db_engine) as session:
        item = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
        assert item is not None
        assert item.published_version is None
        assert item.draft_rollout == []
        assert item.draft_payload == config_to_payload(SEED_FALLBACK_CONFIG)
        assert item.draft_payload is not None
        assert set(item.draft_payload) == {
            "enabled",
            "maturityDays",
            "fast",
            "confirmation",
        }
        assert item.json_schema["additionalProperties"] is False
        assert "回测暂定" in item.json_schema["title"]
        assert "回测暂定" in item.json_schema["description"]
        assert set(item.json_schema["required"]) == {
            "enabled",
            "maturityDays",
            "fast",
            "confirmation",
        }
        for layer_name in ("fast", "confirmation"):
            layer_schema = item.json_schema["properties"][layer_name]
            assert set(layer_schema["properties"]) == {"1", "3", "7"}
            assert layer_schema["additionalProperties"] is False
            for window_schema in layer_schema["properties"].values():
                assert set(window_schema["properties"]) == {"warning", "critical"}
                assert window_schema["additionalProperties"] is False
                for threshold_schema in window_schema["properties"].values():
                    assert set(threshold_schema["properties"]) == {
                        "roiAbsDelta",
                        "roiRelativeDecline",
                        "netProfitDecline",
                        "minSpendCny",
                        "minOrders",
                        "minAdOrders",
                    }
                    assert threshold_schema["additionalProperties"] is False
        assert not session.scalars(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY
            )
        ).all()
        item.json_schema = {"sentinel": True}
        item.draft_payload = {"sentinel": True}
        item.draft_version = 99
        session.add(
            RuntimeConfigRevision(
                config_key=ALERT_CONFIG_KEY,
                version=9,
                payload={"sentinel": True},
                rollout=[],
                created_by="TEST_existing_publication",
            )
        )
        item.published_version = 9
        session.commit()
    with db_engine.begin() as connection:
        migration._seed_runtime_config(connection)
    with Session(db_engine) as session:
        preserved = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
        assert preserved is not None
        assert preserved.json_schema == {"sentinel": True}
        assert preserved.draft_payload == {"sentinel": True}
        assert preserved.draft_version == 99
        assert preserved.published_version == 9
        revision = session.scalar(
            select(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                RuntimeConfigRevision.version == 9,
            )
        )
        assert revision is not None
        assert revision.payload == {"sentinel": True}


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
            },
        )
        assert save.status_code == 200, save.text
        publish = api_client.post(
            "/v2/config/runtime/items/test_runtime.api/publish",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": save.json()["draftVersion"],
                "comment": "TEST publish",
            },
        )
        assert publish.status_code == 200, publish.text
        assert publish.json()["publishedVersion"] == 1

        assert (
            api_client.get(
                "/v2/config/runtime/items", headers=_headers(readonly_key)
            ).status_code
            == 403
        )
        assert (
            api_client.get(
                "/v2/config/runtime/snapshot",
                headers=_headers(readonly_key),
            ).status_code
            == 403
        )

        listed = api_client.get(
            "/v2/config/runtime/items", headers=_headers(readwrite_key)
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["publishedVersion"] == 1
        assert "draftPayload" not in listed.text

        snapshot = api_client.get(
            "/v2/config/runtime/snapshot",
            headers=_headers(readwrite_key),
        )
        assert snapshot.status_code == 200, snapshot.text
        body = snapshot.json()
        # Generic rollout is retired: the envelope carries no subject and each
        # entry is exactly the active published payload/version.
        assert "subject" not in body
        assert set(body["items"]["test_runtime.api"]) == {"version", "payload"}
        assert body["items"]["test_runtime.api"]["payload"] == {
            "endpoint": "https://published.example",
            "token": "secret://test_runtime_token",
        }
        assert "rolloutRuleIndex" not in snapshot.text
        assert "TEST_plaintext_token" not in snapshot.text
        etag = snapshot.headers["etag"]
        assert (
            api_client.get(
                "/v2/config/runtime/snapshot",
                headers={**_headers(readwrite_key), "If-None-Match": etag},
            ).status_code
            == 304
        )
        # A leftover subject query parameter no longer selects anything.
        other_subject = api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_other_subject",
            headers=_headers(readwrite_key),
        )
        assert other_subject.status_code == 200, other_subject.text
        assert other_subject.headers["etag"] == etag
        assert other_subject.json() == body

        blocked_secret_retire = api_client.post(
            "/v2/config/runtime/secrets/test_runtime_token/retire",
            headers=_headers(readwrite_key),
        )
        assert blocked_secret_retire.status_code == 409, blocked_secret_retire.text

        revisions = api_client.get(
            "/v2/config/runtime/items/test_runtime.api/revisions",
            headers=_headers(readwrite_key),
        )
        assert revisions.status_code == 200, revisions.text
        assert revisions.json()["items"][0] == {
            "version": 1,
            "payload": {
                "endpoint": "https://published.example",
                "token": "secret://test_runtime_token",
            },
            "comment": "TEST publish",
            "createdBy": revisions.json()["items"][0]["createdBy"],
            "createdAt": revisions.json()["items"][0]["createdAt"],
        }
        assert "rollout" not in revisions.text
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_optimistic_lock_and_rollback(
    api_client, db_engine, readwrite_key
):
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
            "/v2/config/runtime/items/test_runtime.rollback",
            headers=_headers(readwrite_key),
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
        assert (
            api_client.post(
                "/v2/config/runtime/items/test_runtime.rollback/publish",
                headers=_headers(readwrite_key),
                json={"expectedDraftVersion": saved.json()["draftVersion"]},
            ).json()["publishedVersion"]
            == 2
        )

        current = api_client.get(
            "/v2/config/runtime/items/test_runtime.rollback",
            headers=_headers(readwrite_key),
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


def test_alert_runtime_config_lifecycle_and_legacy_rollout_rejection(
    api_client, db_engine, readwrite_key
):
    """The alert key keeps normal publish/rollback and never accepts rollout input."""
    payload = config_to_payload(SEED_FALLBACK_CONFIG)
    _clear_runtime_config(db_engine)
    _clear_alert_runtime_config(db_engine)
    schema = {"type": "object", "additionalProperties": True}
    headers = _headers(readwrite_key)
    legacy_rollout = [{"name": "canary", "basisPoints": 10000, "payload": payload}]
    try:
        created = api_client.post(
            "/v2/config/runtime/items",
            headers=headers,
            json={
                "configKey": ALERT_CONFIG_KEY,
                "displayName": "TEST SPU alert",
                "jsonSchema": schema,
                "draftPayload": payload,
            },
        )
        assert created.status_code == 201, created.text
        assert "draftRollout" not in created.json()
        # A retired rollout field is an explicit client error, not a silent drop.
        create_with_draft_rollout = api_client.post(
            "/v2/config/runtime/items",
            headers=headers,
            json={
                "configKey": ALERT_CONFIG_KEY,
                "displayName": "TEST SPU alert",
                "jsonSchema": schema,
                "draftPayload": payload,
                "draftRollout": legacy_rollout,
            },
        )
        assert create_with_draft_rollout.status_code == 422, (
            create_with_draft_rollout.text
        )
        rejected_rollout_save = api_client.put(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/draft",
            headers=headers,
            json={
                "expectedDraftVersion": created.json()["draftVersion"],
                "payload": payload,
                "rollout": legacy_rollout,
            },
        )
        assert rejected_rollout_save.status_code == 422, rejected_rollout_save.text
        saved = api_client.put(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/draft",
            headers=headers,
            json={
                "expectedDraftVersion": created.json()["draftVersion"],
                "payload": payload,
            },
        )
        assert saved.status_code == 200, saved.text
        assert "rollout" not in saved.json()
        published = api_client.post(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/publish",
            headers=headers,
            json={"expectedDraftVersion": saved.json()["draftVersion"]},
        )
        assert published.status_code == 200, published.text
        with Session(db_engine) as session:
            revision = session.scalar(
                select(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.config_key == ALERT_CONFIG_KEY,
                    RuntimeConfigRevision.version == 1,
                )
            )
            assert revision is not None
            # New code never writes rollout behavior; the legacy NOT NULL column
            # keeps its empty default.
            assert revision.rollout == []
        updated = dict(payload)
        updated["enabled"] = False
        published_detail = api_client.get(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}", headers=headers
        )
        assert published_detail.status_code == 200, published_detail.text
        saved_again = api_client.put(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/draft",
            headers=headers,
            json={
                "expectedDraftVersion": published_detail.json()["draftVersion"],
                "payload": updated,
            },
        )
        assert saved_again.status_code == 200, saved_again.text
        republished = api_client.post(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/publish",
            headers=headers,
            json={"expectedDraftVersion": saved_again.json()["draftVersion"]},
        )
        assert republished.status_code == 200, republished.text
        republished_detail = api_client.get(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}", headers=headers
        )
        assert republished_detail.status_code == 200, republished_detail.text
        rollback = api_client.post(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/rollback",
            headers=headers,
            json={
                "expectedDraftVersion": republished_detail.json()["draftVersion"],
                "targetVersion": published.json()["publishedVersion"],
            },
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["publishedVersion"] == 3
        with Session(db_engine) as session:
            item = session.get(RuntimeConfigItem, ALERT_CONFIG_KEY)
            assert item is not None
            session.add(
                RuntimeConfigRevision(
                    config_key=ALERT_CONFIG_KEY,
                    version=4,
                    payload=payload,
                    rollout=legacy_rollout,
                    created_by="TEST_legacy_rollout",
                )
            )
            item.published_version = 4
            session.commit()
        legacy_detail = api_client.get(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}", headers=headers
        )
        assert legacy_detail.status_code == 200, legacy_detail.text
        legacy_rollback = api_client.post(
            f"/v2/config/runtime/items/{ALERT_CONFIG_KEY}/rollback",
            headers=headers,
            json={
                "expectedDraftVersion": legacy_detail.json()["draftVersion"],
                "targetVersion": 4,
            },
        )
        assert legacy_rollback.status_code == 422, legacy_rollback.text
        assert "global deterioration alert" in legacy_rollback.text
    finally:
        _clear_alert_runtime_config(db_engine)


def test_alert_malformed_published_payload_fails_closed(
    api_client, db_engine, readonly_key
):
    _clear_alert_runtime_config(db_engine)
    with Session(db_engine) as session:
        session.add(
            RuntimeConfigItem(
                config_key=ALERT_CONFIG_KEY,
                display_name="TEST malformed alert",
                json_schema={"type": "object"},
                draft_payload=None,
                draft_rollout=[],
                draft_version=0,
                published_version=1,
            )
        )
        session.add(
            RuntimeConfigRevision(
                config_key=ALERT_CONFIG_KEY,
                version=1,
                payload=["malformed"],  # type: ignore[arg-type]
                rollout=[],
                created_by="TEST_malformed",
            )
        )
        session.commit()
    try:
        response = api_client.get(
            "/v2/analytics/spu-profit-deterioration?shop_pk=1",
            headers=_headers(readonly_key),
        )
        assert response.status_code == 503
    finally:
        _clear_alert_runtime_config(db_engine)


def test_runtime_config_infers_a_permissive_schema_from_initial_draft(
    api_client, db_engine, readwrite_key
):
    _clear_runtime_config(db_engine)
    try:
        created = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.inferred",
                "displayName": "TEST inferred schema",
                "draftPayload": {
                    "enabled": True,
                    "retries": 3,
                    "tags": ["canary"],
                    "token": "secret://test_runtime_token",
                },
            },
        )
        assert created.status_code == 201, created.text
        schema = created.json()["jsonSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True
        assert "required" not in schema
        assert schema["properties"]["enabled"] == {"type": "boolean"}
        assert schema["properties"]["retries"] == {"type": "integer"}
        assert schema["properties"]["tags"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert schema["properties"]["token"] == {
            "type": "string",
            "format": "secret-reference",
        }
        invalid_type = api_client.put(
            "/v2/config/runtime/items/test_runtime.inferred/draft",
            headers=_headers(readwrite_key),
            json={
                "expectedDraftVersion": created.json()["draftVersion"],
                "payload": {"enabled": "yes"},
            },
        )
        assert invalid_type.status_code == 422, invalid_type.text
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_schema_preview_matches_creation_inference(
    api_client, readwrite_key
):
    response = api_client.post(
        "/v2/config/runtime/schema/preview",
        headers=_headers(readwrite_key),
        json={"payload": {"enabled": True, "items": [1], "extra": None}},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "jsonSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "items": {"type": "array", "items": {"type": "integer"}},
                "extra": {"type": "null"},
            },
            "additionalProperties": True,
        }
    }


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
    assert "async function installJsonEditors()" in source
    assert "new JSONEditor(container" in source
    assert "../../static/vendor/jsoneditor.min.js" in source
    assert "../../static/vendor/jsoneditor.min.css" in source
    assert "onChange: syncEditorText" in source
    assert "onChangeJSON" not in source
    assert 'await loadItems(item.configKey);\n      notice("草稿已保存");' in source
    assert (
        "await loadItems(state.item.configKey);\n      notice(`已发布 v${result.publishedVersion}`);"
        in source
    )
    assert "installJsonEditorToolbar" in source
    assert 'data-action="format"' in source
    assert 'data-action="compact"' in source
    assert 'data-action="tree"' in source
    assert 'data-action="code"' in source
    assert "editor.setMode(action)" in source
    assert "setupAdvancedSchema" in source
    assert "高级：自定义 Schema" in source
    assert "...(schema ? { jsonSchema: schema } : {})" in source
    assert "textarea.required = false" in source
    assert "field.hidden = true" in source
    assert "scheduleSchemaPreview" in source
    assert 'api("/schema/preview"' in source
    assert "rc-schema-preview-status" in source
    assert "setSchemaOverride" in source
    assert "rc-schema-restore" in source
    assert "正在使用手动编辑的 Schema" in source
    assert "恢复自动推断" in source
    assert "installJsonTools" not in source
    license_text = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "vendor"
        / "jsoneditor.LICENSE"
    ).read_text(encoding="utf-8")
    assert "Apache License" in license_text
    icons = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "vendor"
        / "img"
        / "jsoneditor-icons.svg"
    ).read_text(encoding="utf-8")
    assert "svg" in icons
    template = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "templates"
        / "pages"
        / "runtime-configs.html"
    ).read_text(encoding="utf-8")
    assert 'pattern="[a-z][a-z0-9_.\\-]*"' in template
    assert 'pattern="[a-z0-9_.\\-]+"' in template


def test_runtime_config_rejects_empty_secret_reference(
    api_client, db_engine, readwrite_key
):
    _clear_runtime_config(db_engine)
    try:
        response = api_client.post(
            "/v2/config/runtime/items",
            headers=_headers(readwrite_key),
            json={
                "configKey": "test_runtime.invalid_secret",
                "displayName": "TEST invalid secret",
                "jsonSchema": _schema(secret_reference=True),
                "draftPayload": {
                    "endpoint": "https://example.test",
                    "token": "secret://",
                },
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
        assert (
            api_client.post(
                "/v2/config/runtime/items/test_runtime.retire/publish",
                headers=_headers(readwrite_key),
                json={"expectedDraftVersion": created.json()["draftVersion"]},
            ).status_code
            == 200
        )
        retired = api_client.post(
            "/v2/config/runtime/items/test_runtime.retire/retire",
            headers=_headers(readwrite_key),
        )
        assert retired.json()["status"] == "retired"
        assert (
            api_client.put(
                "/v2/config/runtime/items/test_runtime.retire/draft",
                headers=_headers(readwrite_key),
                json={
                    "expectedDraftVersion": 2,
                    "payload": {"endpoint": "https://v2.example", "value": "v2"},
                },
            ).status_code
            == 409
        )
        assert (
            "test_runtime.retire"
            not in api_client.get(
                "/v2/config/runtime/snapshot", headers=_headers(readwrite_key)
            ).json()["items"]
        )
        listed = api_client.get(
            "/v2/config/runtime/items?includeRetired=true",
            headers=_headers(readwrite_key),
        )
        assert listed.json()["items"][0]["retiredAt"] is not None
        assert (
            api_client.post(
                "/v2/config/runtime/items/test_runtime.retire/restore",
                headers=_headers(readwrite_key),
            ).json()["status"]
            == "active"
        )

        secret = api_client.put(
            "/v2/config/runtime/secrets/test_runtime_unused",
            headers=_headers(readwrite_key),
            json={"value": "TEST_unused"},
        )
        assert secret.status_code == 200, secret.text
        assert (
            api_client.post(
                "/v2/config/runtime/secrets/test_runtime_unused/retire",
                headers=_headers(readwrite_key),
            ).json()["status"]
            == "retired"
        )
        assert (
            api_client.put(
                "/v2/config/runtime/secrets/test_runtime_unused",
                headers=_headers(readwrite_key),
                json={"value": "TEST_replacement"},
            ).status_code
            == 409
        )
        assert (
            api_client.post(
                "/v2/config/runtime/secrets/test_runtime_unused/restore",
                headers=_headers(readwrite_key),
            ).json()["status"]
            == "active"
        )
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_restore_rejects_a_retired_secret_reference(
    api_client, db_engine, readwrite_key
):
    """Archiving a config frees its secret; restoring must not revive a broken reference."""
    _clear_runtime_config(db_engine)
    headers = _headers(readwrite_key)
    try:
        created = api_client.post(
            "/v2/config/runtime/items",
            headers=headers,
            json={
                "configKey": "test_runtime.restore_secret",
                "displayName": "TEST restore secret",
                "jsonSchema": _schema(secret_reference=True),
                "draftPayload": {
                    "endpoint": "https://v1.example",
                    "token": "secret://test_runtime_restore_token",
                },
            },
        )
        assert created.status_code == 201, created.text
        assert (
            api_client.put(
                "/v2/config/runtime/secrets/test_runtime_restore_token",
                headers=headers,
                json={"value": "TEST_restore_plaintext"},
            ).status_code
            == 200
        )
        published = api_client.post(
            "/v2/config/runtime/items/test_runtime.restore_secret/publish",
            headers=headers,
            json={"expectedDraftVersion": created.json()["draftVersion"]},
        )
        assert published.status_code == 200, published.text

        # While the key is archived its published reference no longer blocks retirement.
        assert (
            api_client.post(
                "/v2/config/runtime/items/test_runtime.restore_secret/retire",
                headers=headers,
            ).json()["status"]
            == "retired"
        )
        assert (
            api_client.post(
                "/v2/config/runtime/secrets/test_runtime_restore_token/retire",
                headers=headers,
            ).status_code
            == 200
        )

        blocked = api_client.post(
            "/v2/config/runtime/items/test_runtime.restore_secret/restore",
            headers=headers,
        )
        assert blocked.status_code == 409, blocked.text
        assert "test_runtime_restore_token" in blocked.text
        assert "retired or missing" in blocked.text
        assert (
            api_client.get(
                "/v2/config/runtime/items?includeRetired=true", headers=headers
            ).json()["items"][0]["retiredAt"]
            is not None
        )

        # Restoring the secret first makes the key restorable again.
        assert (
            api_client.post(
                "/v2/config/runtime/secrets/test_runtime_restore_token/restore",
                headers=headers,
            ).json()["status"]
            == "active"
        )
        assert (
            api_client.post(
                "/v2/config/runtime/items/test_runtime.restore_secret/restore",
                headers=headers,
            ).json()["status"]
            == "active"
        )
    finally:
        _clear_runtime_config(db_engine)


def test_runtime_config_write_models_reject_retired_rollout_fields(
    api_client, db_engine, readwrite_key
):
    """Retired rollout inputs fail loudly on the two payload-bearing write models.

    ``extra="forbid"`` is deliberately scoped to create and draft update: a
    leftover ``draftRollout``/``rollout`` must surface as 422 instead of being
    silently dropped, while the remaining runtime request models stay lenient.
    """
    _clear_runtime_config(db_engine)
    headers = _headers(readwrite_key)
    config_key = "test_runtime.strict_extra"
    try:
        rejected_create = api_client.post(
            "/v2/config/runtime/items",
            headers=headers,
            json={
                "configKey": config_key,
                "displayName": "TEST strict extra",
                "jsonSchema": _schema(),
                "draftRollout": [{"name": "TEST_canary", "basisPoints": 10000}],
            },
        )
        assert rejected_create.status_code == 422, rejected_create.text
        assert "draftRollout" in rejected_create.text

        # The rejected create must not have persisted a row.
        assert (
            api_client.get("/v2/config/runtime/items", headers=headers).status_code
            == 200
        )

        created = api_client.post(
            "/v2/config/runtime/items",
            headers=headers,
            json={
                "configKey": config_key,
                "displayName": "TEST strict extra",
                "jsonSchema": _schema(),
                "draftPayload": {"endpoint": "https://v1.example", "value": "TEST_v"},
            },
        )
        assert created.status_code == 201, created.text
        draft_version = created.json()["draftVersion"]

        rejected_draft = api_client.put(
            f"/v2/config/runtime/items/{config_key}/draft",
            headers=headers,
            json={
                "expectedDraftVersion": draft_version,
                "payload": {"endpoint": "https://v1.example", "value": "TEST_v"},
                "rollout": [{"name": "TEST_canary", "basisPoints": 10000}],
            },
        )
        assert rejected_draft.status_code == 422, rejected_draft.text
        assert "rollout" in rejected_draft.text

        # The rejected draft left the optimistic version untouched.
        accepted_draft = api_client.put(
            f"/v2/config/runtime/items/{config_key}/draft",
            headers=headers,
            json={
                "expectedDraftVersion": draft_version,
                "payload": {"endpoint": "https://v2.example", "value": "TEST_v2"},
            },
        )
        assert accepted_draft.status_code == 200, accepted_draft.text

        # Other runtime request models are intentionally not strict.
        published = api_client.post(
            f"/v2/config/runtime/items/{config_key}/publish",
            headers=headers,
            json={
                "expectedDraftVersion": accepted_draft.json()["draftVersion"],
                "comment": "TEST strict extra",
                "rollout": [{"name": "TEST_canary", "basisPoints": 10000}],
            },
        )
        assert published.status_code == 200, published.text

        # The snapshot's leftover ``subject`` query stays non-selecting.
        base_snapshot = api_client.get("/v2/config/runtime/snapshot", headers=headers)
        assert base_snapshot.status_code == 200, base_snapshot.text
        subject_snapshot = api_client.get(
            "/v2/config/runtime/snapshot?subject=TEST_other_subject", headers=headers
        )
        assert subject_snapshot.status_code == 200, subject_snapshot.text
        assert subject_snapshot.json() == base_snapshot.json()
        assert subject_snapshot.headers["etag"] == base_snapshot.headers["etag"]
    finally:
        _clear_runtime_config(db_engine)


def test_lock_referenced_secrets_serializes_with_secret_retirement(db_engine) -> None:
    """The publish-side and retire-side locks must genuinely contend.

    ``lock_referenced_secrets`` and ``retire_runtime_config_secret`` each take a
    ``SELECT ... FOR UPDATE`` row lock on the same ``config.runtime_config_secrets``
    row. That pairing is what stops a publish from referencing a secret retired a
    moment later, so this guards BOTH directions rather than re-acquiring one
    lock the same holder already owns.

    Deterministic by construction: the blocked waiter arms a transaction-local
    ``lock_timeout`` instead of sleeping, and each phase uses its own sessions so
    no waiter is ever reused after a rollback (which would silently drop the
    armed ``SET LOCAL``).
    """
    secret_name = "test_runtime_concurrency_token"
    payload = {
        "endpoint": "https://concurrency.example",
        "token": f"secret://{secret_name}",
    }
    # Postgres SQLSTATE for "could not obtain lock on row in relation".
    lock_not_available = "55P03"

    # Commit the secret first: an uncommitted INSERT would itself block the
    # contenders and the phases would prove nothing.
    with Session(db_engine) as seed_session:
        seed_session.add(
            RuntimeConfigSecret(
                name=secret_name,
                encrypted_value=b"TEST_concurrency_ciphertext",
                fingerprint="TEST_concurrency_fp",
            )
        )
        seed_session.commit()

    # ── Phase 1: reference lock held, a second reference lock must block ──
    reference_holder = Session(db_engine)
    try:
        # Uncontended: the secret exists and is active, so nothing is missing.
        assert lock_referenced_secrets(reference_holder, payload) == set()

        reference_waiter = Session(db_engine)
        try:
            # pi-lens-ignore: python-sql-injection
            reference_waiter.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(OperationalError) as blocked:
                lock_referenced_secrets(reference_waiter, payload)
            assert getattr(blocked.value.orig, "sqlstate", None) == lock_not_available
        finally:
            reference_waiter.rollback()
            reference_waiter.close()
    finally:
        reference_holder.rollback()
        reference_holder.close()

    # ── Phase 2: retire-side row lock held, the reference lock must block ──
    retire_holder = Session(db_engine)
    try:
        # Exactly the lock retire_runtime_config_secret takes, and nothing else.
        retire_holder.execute(
            select(RuntimeConfigSecret)
            .where(RuntimeConfigSecret.name == secret_name)
            .with_for_update()
        ).scalar_one()

        publish_waiter = Session(db_engine)
        try:
            # pi-lens-ignore: python-sql-injection
            publish_waiter.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(OperationalError) as blocked:
                lock_referenced_secrets(publish_waiter, payload)
            assert getattr(blocked.value.orig, "sqlstate", None) == lock_not_available
        finally:
            publish_waiter.rollback()
            publish_waiter.close()
    finally:
        retire_holder.rollback()
        retire_holder.close()

    # ── Both locks released: a fresh uncontended session succeeds ──
    fresh_session = Session(db_engine)
    try:
        assert lock_referenced_secrets(fresh_session, payload) == set()
    finally:
        fresh_session.rollback()
        fresh_session.close()

    with Session(db_engine) as cleanup:
        cleanup.execute(
            delete(RuntimeConfigSecret).where(RuntimeConfigSecret.name == secret_name)
        )
        cleanup.commit()
