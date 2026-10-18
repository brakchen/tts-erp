"""Generic operator CLI must not widen managed credential scope."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import api_keys
from tests.api.test_plugin_status_auth_integration import Provisioned, provisioned
from tests.unit.test_plugin_status_access_compat import monitor_shop
from tts_erp_v2.db.models.plugin_status import StatusInstance
from tts_erp_v2.db.models.security import ApiKey

# Explicit pytest fixture re-exports; their implementations remain canonical.
__all__ = ["monitor_shop", "provisioned"]

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture(autouse=True)
def isolated_cli_connection(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (db_engine.url.database or "").startswith("tts_erp_test_")
    assert db_engine.url.database != "tts_erp_test_template"
    url = api_keys.normalize_db_url(db_engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(api_keys, "_connect", lambda: psycopg.connect(url))


@pytest.mark.parametrize("purpose", ["plugin_status", "monitor_config"])
def test_generic_rotation_refuses_managed_scope_before_any_mutation(
    purpose: str,
    provisioned: Provisioned,
    monitor_shop: int,
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(db_engine) as session:
        if purpose == "plugin_status":
            row = session.get(ApiKey, provisioned.key_id)
            assert row is not None
            key_id, prefix, name = row.id, row.key_prefix, row.name
        else:
            name = "TEST_managed_rotate_" + uuid.uuid4().hex
            prefix = "TEST_" + uuid.uuid4().hex[:11]
            row = ApiKey(
                key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                key_prefix=prefix,
                name=name,
                role="readonly",
                status="active",
                purpose=purpose,
                shop_pk=monitor_shop,
            )
            session.add(row)
            session.flush()
            key_id = row.id
            session.commit()
        before = set(session.scalars(select(ApiKey.id).where(ApiKey.name == name)))
    try:
        with pytest.raises(SystemExit, match="restricted"):
            api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            key = verify.get(ApiKey, key_id)
            assert key is not None and key.status == "active" and key.purpose == purpose
            assert key.shop_pk == (
                monitor_shop if purpose == "monitor_config" else None
            )
            assert (
                set(verify.scalars(select(ApiKey.id).where(ApiKey.name == name)))
                == before
            )
            instance = verify.get(StatusInstance, provisioned.instance_id)
            assert (
                instance is not None
                and instance.status_api_key_id == provisioned.key_id
            )
    finally:
        capsys.readouterr()  # All keys here are synthetic; never persist CLI plaintext.
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(ApiKey).where(ApiKey.name == name, ApiKey.id.not_in(before))
            )
            if purpose == "monitor_config":
                cleanup.execute(delete(ApiKey).where(ApiKey.id == key_id))
            cleanup.commit()


def test_generic_general_rotation_keeps_existing_behavior(
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "TEST_general_rotate_" + uuid.uuid4().hex
    prefix = "TEST_" + uuid.uuid4().hex[:11]
    with Session(db_engine) as session:
        row = ApiKey(
            key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            key_prefix=prefix,
            name=name,
            role="readwrite",
            status="active",
            purpose="general",
        )
        session.add(row)
        session.flush()
        key_id = row.id
        session.commit()
    try:
        api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            rows = list(verify.scalars(select(ApiKey).where(ApiKey.name == name)))
            assert len(rows) == 2
            old = next(key for key in rows if key.id == key_id)
            new = next(key for key in rows if key.id != key_id)
            assert old.status == "disabled"
            assert new.status == "active" and new.role == "readwrite"
            assert new.purpose == "general" and new.shop_pk is None
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()


@pytest.mark.parametrize("other_purpose", ["general", "plugin_status"])
def test_generic_rotation_refuses_ambiguous_active_prefix(
    other_purpose: str,
    provisioned: Provisioned,
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "TEST_ambiguous_rotate_" + uuid.uuid4().hex
    with Session(db_engine) as session:
        managed = session.get(ApiKey, provisioned.key_id)
        assert managed is not None
        prefix = (
            managed.key_prefix
            if other_purpose == "plugin_status"
            else "TEST_" + uuid.uuid4().hex[:11]
        )
        count = 1 if other_purpose == "plugin_status" else 2
        for _ in range(count):
            session.add(
                ApiKey(
                    key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    key_prefix=prefix,
                    name=name,
                    role="readwrite",
                    status="active",
                    purpose="general",
                )
            )
        session.commit()
        before = set(session.scalars(select(ApiKey.id)))
    try:
        with pytest.raises(SystemExit, match="ambiguous"):
            api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            assert set(verify.scalars(select(ApiKey.id))) == before
            matching = list(
                verify.scalars(select(ApiKey).where(ApiKey.key_prefix == prefix))
            )
            assert len(matching) == 2
            assert all(row.status == "active" for row in matching)
            instance = verify.get(StatusInstance, provisioned.instance_id)
            assert (
                instance is not None
                and instance.status_api_key_id == provisioned.key_id
            )
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()


def test_rotation_revokes_selected_identity_not_new_prefix_collision(
    provisioned: Provisioned,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "TEST_late_prefix_rotate_" + uuid.uuid4().hex
    prefix = "TEST_" + uuid.uuid4().hex[:11]
    with Session(db_engine) as session:
        row = ApiKey(
            key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            key_prefix=prefix,
            name=name,
            role="readwrite",
            status="active",
            purpose="general",
        )
        session.add(row)
        session.flush()
        key_id = row.id
        session.commit()
    original_insert = api_keys._insert_key

    def insert_then_commit_collision(
        conn, key_name, role, scopes, expires_days, **options
    ):
        value = original_insert(conn, key_name, role, scopes, expires_days, **options)
        # Real second transaction after INSERT, before selected-key revocation.
        # Prefix is non-unique display metadata, not credential identity.
        with Session(db_engine) as other:
            managed = other.get(ApiKey, provisioned.key_id)
            assert managed is not None and managed.status == "active"
            managed.key_prefix = prefix
            other.commit()
        return value

    monkeypatch.setattr(api_keys, "_insert_key", insert_then_commit_collision)
    try:
        api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            managed = verify.get(ApiKey, provisioned.key_id)
            assert managed is not None and managed.status == "active"
            assert managed.purpose == "plugin_status"
            instance = verify.get(StatusInstance, provisioned.instance_id)
            assert (
                instance is not None
                and instance.status_api_key_id == provisioned.key_id
            )
            rows = list(verify.scalars(select(ApiKey).where(ApiKey.name == name)))
            assert len(rows) == 2
            assert next(row for row in rows if row.id == key_id).status == "disabled"
            replacement = next(row for row in rows if row.id != key_id)
            assert replacement.status == "active" and replacement.purpose == "general"
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()


def test_general_rotation_keeps_selected_scope_lock_through_insert(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "TEST_rotate_scope_lock_" + uuid.uuid4().hex
    prefix = "TEST_" + uuid.uuid4().hex[:11]
    with Session(db_engine) as session:
        row = ApiKey(
            key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            key_prefix=prefix,
            name=name,
            role="readwrite",
            status="active",
            purpose="general",
        )
        session.add(row)
        session.flush()
        key_id = row.id
        session.commit()
    original_insert = api_keys._insert_key

    def insert_then_try_scope_change(
        conn, key_name, role, scopes, expires_days, **options
    ):
        value = original_insert(conn, key_name, role, scopes, expires_days, **options)
        blocked = False
        with api_keys._connect() as other, other.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '100ms'")
            try:
                cursor.execute(
                    "UPDATE security.api_keys SET purpose = 'plugin_status' WHERE id = %s",
                    (key_id,),
                )
            except psycopg.errors.LockNotAvailable:
                blocked = True
            finally:
                other.rollback()
        assert blocked, (
            "selected original scope must remain locked until rotation completes"
        )
        return value

    monkeypatch.setattr(api_keys, "_insert_key", insert_then_try_scope_change)
    try:
        api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            rows = list(verify.scalars(select(ApiKey).where(ApiKey.name == name)))
            assert len(rows) == 2
            assert all(row.purpose == "general" for row in rows)
            assert next(row for row in rows if row.id == key_id).status == "disabled"
            assert next(row for row in rows if row.id != key_id).status == "active"
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()


def test_generic_create_and_revoke_keep_existing_behavior(
    db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "TEST_create_revoke_" + uuid.uuid4().hex
    try:
        api_keys.cmd_create(
            SimpleNamespace(name=name, role="readonly", scopes=[], expires_days=None)
        )
        with Session(db_engine) as verify:
            row = verify.scalars(select(ApiKey).where(ApiKey.name == name)).one()
            key_id, prefix = row.id, row.key_prefix
            assert row.status == "active" and row.role == "readonly"
            assert row.purpose == "general" and row.shop_pk is None
        api_keys.cmd_revoke(SimpleNamespace(prefix=prefix))
        with Session(db_engine) as verify:
            row = verify.get(ApiKey, key_id)
            assert row is not None and row.status == "disabled"
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()


def test_general_rotation_rolls_back_replacement_after_insert_failure(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "TEST_rotate_rollback_" + uuid.uuid4().hex
    try:
        api_keys.cmd_create(
            SimpleNamespace(name=name, role="readwrite", scopes=[], expires_days=None)
        )
        with Session(db_engine) as before:
            old = before.scalars(select(ApiKey).where(ApiKey.name == name)).one()
            key_id, prefix = old.id, old.key_prefix
        original_insert = api_keys._insert_key

        def fail_after_insert(conn, key_name, role, scopes, expires_days, **options):
            original_insert(conn, key_name, role, scopes, expires_days, **options)
            raise RuntimeError("TEST_after_insert")

        monkeypatch.setattr(api_keys, "_insert_key", fail_after_insert)
        with pytest.raises(RuntimeError, match="TEST_after_insert"):
            api_keys.cmd_rotate(SimpleNamespace(prefix=prefix))
        # The operator's raw psycopg connection has exited. This independent
        # engine/session must see neither a replacement nor a revoked original.
        with Session(db_engine) as verify:
            rows = list(verify.scalars(select(ApiKey).where(ApiKey.name == name)))
            assert len(rows) == 1
            assert rows[0].id == key_id and rows[0].status == "active"
            assert rows[0].purpose == "general" and rows[0].shop_pk is None
    finally:
        capsys.readouterr()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ApiKey).where(ApiKey.name == name))
            cleanup.commit()
