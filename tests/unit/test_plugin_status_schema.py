"""Real migrated schema and actual application assembly, without bootstrap."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine


@pytest.mark.parametrize(
    "table",
    [
        "status_instances",
        "status_reports",
        "status_decisions",
        "status_notification_outbox",
    ],
)
def test_migrated_status_table_exists(db_engine: Engine, table: str) -> None:
    assert inspect(db_engine).has_table(table, schema="plugin")


def test_actual_application_mounts_status_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.app import build_app

    monkeypatch.setenv("TTS_ERP_AUTH_MODE", "off")
    client = TestClient(build_app())  # Deliberately no lifespan/startup.
    try:
        response = client.get("/endpoints")
        paths = {item["path"] for item in response.json()["endpoints"]}
        assert "/v2/plugin/status/report" in paths
        assert "/v2/plugin/admin/instances" in paths
    finally:
        client.close()


def test_actual_cors_exposes_existing_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tts_erp_v2.app import DEFAULT_CORS_ORIGINS, build_app

    client = TestClient(build_app())
    try:
        response = client.get("/healthz", headers={"Origin": DEFAULT_CORS_ORIGINS[0]})
        exposed = {
            value.strip().lower()
            for value in response.headers.get(
                "access-control-expose-headers", ""
            ).split(",")
        }
        assert {
            "x-request-id",
            "retry-after",
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
        } <= exposed
    finally:
        client.close()


def test_actual_migration_revision(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0071_plugin_status"
        )


def test_existing_migrated_outbox_has_pending_default(db_engine: Engine) -> None:
    """Inspect the clone as received, without replaying or replacing its schema."""
    columns = inspect(db_engine).get_columns(
        "status_notification_outbox", schema="plugin"
    )
    state = next(column for column in columns if column["name"] == "state")
    default = state["default"]
    assert default is not None, "existing migrated outbox lacks the pending default"
    assert default.split("::", 1)[0] == "'pending'"


def test_fresh_status_migration_supplies_pending_default(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the real upgrade only inside an empty, guarded ephemeral clone.

    The default template is never changed. Transactional DDL rolls back even
    on failure; no downgrade or fixture-created replacement schema is used.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from tts_erp_v2.api.deps import require_destructive_script_guard
    from tts_erp_v2.db.models.plugin_status import (
        StatusDecision,
        StatusInstance,
        StatusNotificationOutbox,
        StatusReport,
    )
    from tts_erp_v2.db.models.security import ApiKey

    assert (db_engine.url.database or "").startswith("tts_erp_test_")
    assert db_engine.url.database != "tts_erp_test_template"
    monkeypatch.setenv(
        "TTS_ERP_DB_URL", db_engine.url.render_as_string(hide_password=False)
    )
    require_destructive_script_guard(
        script_name="TEST_empty_clone_status_migration_replay",
        confirmation=True,
    )
    path = Path(__file__).parents[2] / "alembic/versions/0071_plugin_status.py"
    spec = importlib.util.spec_from_file_location("TEST_status_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            models = (
                StatusNotificationOutbox,
                StatusDecision,
                StatusReport,
                StatusInstance,
            )
            # Refuse to erase even test report facts; this clone must be empty.
            for model in models:
                assert (
                    conn.execute(select(model.__table__.c.id).limit(1)).first() is None
                )
            for model in models:
                model.__table__.drop(conn)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            key_id = conn.execute(
                ApiKey.__table__.insert()
                .values(
                    key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    key_prefix="TEST_default",
                    name="TEST_default",
                    role="readwrite",
                    status="active",
                    purpose="plugin_status",
                )
                .returning(ApiKey.id)
            ).scalar_one()
            instance_id = uuid.uuid4()
            conn.execute(
                StatusInstance.__table__.insert().values(
                    id=instance_id,
                    plugin_id="ads-data-sync",
                    scope={"kind": "installation"},
                    capabilities=[],
                    status_api_key_id=key_id,
                )
            )
            state = conn.execute(
                StatusNotificationOutbox.__table__.insert()
                .values(
                    id=uuid.uuid4(),
                    instance_id=instance_id,
                    incident_key="TEST_default",
                    notification_kind="TEST_default",
                    dedupe_key="TEST_" + uuid.uuid4().hex,
                    payload={},
                    next_attempt_at=datetime.now(UTC),
                )
                .returning(StatusNotificationOutbox.state)
            ).scalar_one()
            assert state == "pending"
        finally:
            transaction.rollback()
