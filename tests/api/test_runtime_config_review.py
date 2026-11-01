"""Regression coverage for read-only review and atomic runtime publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture
def review_config(api_client, db_engine, readwrite_key):
    key = f"test_runtime_review_{uuid4().hex}"
    headers = {"Authorization": f"Bearer {readwrite_key}", "X-Requested-With": "tts-erp"}
    base = f"/v2/config/runtime/items/{key}"
    created = api_client.post(
        "/v2/config/runtime/items",
        headers=headers,
        json={
            "configKey": key,
            "displayName": "TEST publication review",
            "jsonSchema": {"type": "object"},
            "draftPayload": {"value": "TEST saved draft"},
        },
    )
    assert created.status_code == 201, created.text
    yield key, base, headers, created.json()
    with Session(db_engine) as session:
        session.execute(delete(RuntimeConfigRevision).where(RuntimeConfigRevision.config_key == key))
        session.execute(delete(RuntimeConfigItem).where(RuntimeConfigItem.config_key == key))
        session.execute(delete(RuntimeConfigSecret).where(RuntimeConfigSecret.name == key))
        session.commit()


def _state(db_engine, key):
    with Session(db_engine) as session:
        item = session.get(RuntimeConfigItem, key)
        revisions = session.scalars(
            select(RuntimeConfigRevision)
            .where(RuntimeConfigRevision.config_key == key)
            .order_by(RuntimeConfigRevision.version)
        ).all()
        return (
            item.draft_version, item.published_version, item.draft_payload, item.updated_at,
            [(row.version, row.payload, row.comment, row.created_at) for row in revisions],
        )


def _body(detail, candidate, **extra):
    return {
        "expectedDraftVersion": detail["draftVersion"],
        "expectedPublishedVersion": detail["publishedVersion"],
        "payload": candidate,
        **extra,
    }


def test_review_preview_is_read_only_and_preserves_large_integer(api_client, db_engine, review_config):
    key, base, headers, detail = review_config
    before = _state(db_engine, key)
    payload = {"z": [9007199254740993, 9007199254740995], "a": {"enabled": True}}
    response = api_client.post(f"{base}/preview", headers=headers, json=_body(detail, payload))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "configKey": key,
        "draftVersion": 1,
        "publishedVersion": None,
        "hasChanges": True,
        "publishedText": None,
        "candidateText": json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        "sourceRevisionVersion": None,
    }
    assert _state(db_engine, key) == before


def test_review_atomic_publish_uses_candidate_without_intermediate_draft(api_client, db_engine, review_config):
    key, base, headers, detail = review_config
    payload = {"value": "TEST candidate", "large": 9007199254740993}
    response = api_client.post(
        f"{base}/publish", headers=headers,
        json=_body(detail, payload, comment="TEST atomic publish"),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"configKey": key, "publishedVersion": 1}
    state = _state(db_engine, key)
    assert state[:3] == (2, 1, None)  # Only publish increments draftVersion.
    assert len(state[4]) == 1
    assert state[4][0][:3] == (1, payload, "TEST atomic publish")


def test_review_empty_first_publication_without_saved_draft(api_client, db_engine, review_config):
    key, base, headers, detail = review_config
    with Session(db_engine) as session:
        item = session.get(RuntimeConfigItem, key)
        item.draft_payload = None
        item.draft_version = 0
        session.commit()
    detail = api_client.get(base, headers=headers).json()
    preview = api_client.post(f"{base}/preview", headers=headers, json=_body(detail, {}))
    assert preview.status_code == 200, preview.text
    assert preview.json()["hasChanges"] is True
    assert preview.json()["publishedText"] is None
    published = api_client.post(f"{base}/publish", headers=headers, json=_body(detail, {}))
    assert published.status_code == 200, published.text
    assert _state(db_engine, key)[:3] == (1, 1, None)


def test_review_flush_failure_rolls_back_candidate_and_old_draft(api_client, db_engine, review_config, monkeypatch):
    from tts_erp_v2.api.v2 import config

    key, base, headers, detail = review_config
    before = _state(db_engine, key)
    real_publish = config.publish

    def fail_after_flush(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise RuntimeError("TEST failure after publication flush")

    monkeypatch.setattr(config, "publish", fail_after_flush)
    with pytest.raises(RuntimeError, match="TEST failure after publication flush"):
        api_client.post(f"{base}/publish", headers=headers, json=_body(detail, {"value": "TEST candidate"}))
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("candidate,has_changes", [
    ({"object": {"b": 2, "a": 1}, "array": [1, 2], "value": 1}, False),
    ({"object": {"a": 1, "b": 2}, "array": [2, 1], "value": 1}, True),
    ({"object": {"a": 1, "b": 2}, "array": [1, 2], "value": True}, True),
    ({"object": {"a": 1, "b": 2}, "array": [1, 2], "value": 1.0}, False),
    ({"object": {"a": 1, "b": 2}, "array": [1, 2], "value": "1"}, True),
    ({"object": {"a": 1, "b": 2}, "array": [1, 2], "value": None}, True),
])
def test_review_semantic_equality(api_client, db_engine, review_config, candidate, has_changes):
    key, base, headers, detail = review_config
    original = {"object": {"a": 1, "b": 2}, "array": [1, 2], "value": 1}
    assert api_client.post(f"{base}/publish", headers=headers, json=_body(detail, original)).status_code == 200
    detail = api_client.get(base, headers=headers).json()
    before = _state(db_engine, key)
    preview = api_client.post(f"{base}/preview", headers=headers, json=_body(detail, candidate))
    assert preview.status_code == 200, preview.text
    assert preview.json()["hasChanges"] is has_changes
    assert json.loads(preview.json()["publishedText"]) == original
    assert _state(db_engine, key) == before
    if not has_changes:
        duplicate = api_client.post(f"{base}/publish", headers=headers, json=_body(detail, candidate))
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"] == "candidate matches current published configuration"
        assert _state(db_engine, key) == before


@pytest.mark.parametrize("operation", ["preview", "publish"])
@pytest.mark.parametrize("override,status", [
    ({"expectedDraftVersion": 0}, 409),
    ({"expectedPublishedVersion": 1}, 409),
    ({"payload": None}, 422),
    ({"payload": []}, 422),
    ({"rollout": []}, 422),
    ({"draftRollout": []}, 422),
    ({"sourceRevisionVersion": 99}, 404),
    ({"payload": {"token": "secret://test_runtime_missing_review"}}, 422),
])
def test_review_failure_preserves_all_state(api_client, db_engine, review_config, operation, override, status):
    key, base, headers, detail = review_config
    before = _state(db_engine, key)
    response = api_client.post(
        f"{base}/{operation}", headers=headers,
        json=_body(detail, {"value": "TEST candidate"}, **override),
    )
    assert response.status_code == status, response.text
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("operation", ["preview", "publish"])
@pytest.mark.parametrize("raw_number", ["1e400", "-1e400", "NaN", "Infinity", "-Infinity"])
def test_review_rejects_nested_nonfinite_raw_numbers(api_client, db_engine, review_config, operation, raw_number):
    key, base, headers, detail = review_config
    before = _state(db_engine, key)
    # Use raw JSON: httpx's json= serializer itself rejects NaN/Infinity, while
    # Starlette's existing parser accepts them and overflows +/-1e400 to inf.
    raw = (
        '{"expectedDraftVersion": ' + str(detail["draftVersion"])
        + ', "expectedPublishedVersion": null, '
        + '"payload": {"unconstrained": [{"number": ' + raw_number + '}]}}'
    )
    response = api_client.post(
        f"{base}/{operation}",
        headers={**headers, "Content-Type": "application/json"},
        content=raw,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {"code": "runtime_number_invalid"}
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("operation", ["preview", "publish"])
def test_review_requires_explicit_published_version(api_client, db_engine, review_config, operation):
    key, base, headers, detail = review_config
    before = _state(db_engine, key)
    response = api_client.post(f"{base}/{operation}", headers=headers, json={
        "expectedDraftVersion": detail["draftVersion"], "payload": {},
    })
    assert response.status_code == 422, response.text
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("operation", ["preview", "publish"])
def test_review_requires_write_role(api_client, db_engine, review_config, readonly_key, operation):
    key, base, _, detail = review_config
    before = _state(db_engine, key)
    response = api_client.post(
        f"{base}/{operation}",
        headers={"Authorization": f"Bearer {readonly_key}", "X-Requested-With": "tts-erp"},
        json=_body(detail, {}),
    )
    assert response.status_code == 403, response.text
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("other_command", ["draft", "publish"])
def test_review_final_publish_rejects_changes_since_preview(api_client, db_engine, review_config, other_command):
    key, base, headers, detail = review_config
    body = _body(detail, {"value": "TEST reviewed"})
    preview = api_client.post(f"{base}/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    if other_command == "draft":
        changed = api_client.put(f"{base}/draft", headers=headers, json={
            "expectedDraftVersion": detail["draftVersion"],
            "payload": {"value": "TEST another operator"},
        })
    else:
        changed = api_client.post(f"{base}/publish", headers=headers, json=_body(detail, {"value": "TEST another operator"}))
    assert changed.status_code == 200, changed.text
    before = _state(db_engine, key)
    response = api_client.post(f"{base}/publish", headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("operation", ["preview", "publish"])
def test_review_revalidates_schema_active_secrets_and_retirement(api_client, db_engine, review_config, operation):
    key, base, headers, detail = review_config
    with Session(db_engine) as session:
        session.add(RuntimeConfigSecret(name=key, encrypted_value=b"TEST_cipher", fingerprint="TEST_fp"))
        item = session.get(RuntimeConfigItem, key)
        item.json_schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
        session.commit()
    before = _state(db_engine, key)
    invalid_schema = api_client.post(f"{base}/{operation}", headers=headers, json=_body(detail, {"value": True}))
    assert invalid_schema.status_code == 422, invalid_schema.text
    assert invalid_schema.json()["detail"] == {"code": "runtime_schema_invalid"}
    assert _state(db_engine, key) == before
    with Session(db_engine) as session:
        session.get(RuntimeConfigItem, key).json_schema = {"type": "object"}
        session.commit()
    valid_body = _body(detail, {"token": f"secret://{key}"})
    preview = api_client.post(f"{base}/preview", headers=headers, json=valid_body)
    assert preview.status_code == 200, preview.text
    # A successful preview must not authorize later use of a retired secret.
    with Session(db_engine) as session:
        session.get(RuntimeConfigSecret, key).retired_at = datetime.now(UTC)
        session.commit()
    before = _state(db_engine, key)
    retired_secret = api_client.post(f"{base}/{operation}", headers=headers, json=_body(detail, {"token": f"secret://{key}"}))
    assert retired_secret.status_code == 422, retired_secret.text
    assert retired_secret.json()["detail"] == {"code": "runtime_secret_unavailable"}
    assert _state(db_engine, key) == before
    api_client.post(f"{base}/retire", headers=headers)
    before = _state(db_engine, key)
    retired_item = api_client.post(f"{base}/{operation}", headers=headers, json=_body(detail, {}))
    assert retired_item.status_code == 409, retired_item.text
    assert _state(db_engine, key) == before


@pytest.mark.parametrize("candidate_mode", [False, True])
def test_review_publish_preserves_unrelated_legacy_metadata(api_client, db_engine, review_config, candidate_mode):
    key, base, headers, detail = review_config
    body = {
        "expectedDraftVersion": detail["draftVersion"],
        "comment": "TEST legacy extra compatibility",
        "clientMetadata": {"source": "TEST older client"},
    }
    expected_payload = detail["draftPayload"]
    if candidate_mode:
        expected_payload = {"value": "TEST reviewed with metadata"}
        body.update(_body(detail, expected_payload))
    response = api_client.post(f"{base}/publish", headers=headers, json=body)
    assert response.status_code == 200, response.text
    state = _state(db_engine, key)
    assert state[:3] == (2, 1, None)
    assert state[4][0][1] == expected_payload


def test_review_legacy_publish_retains_string_validation_detail(api_client, db_engine, review_config):
    key, base, headers, detail = review_config
    with Session(db_engine) as session:
        session.get(RuntimeConfigItem, key).json_schema = {
            "type": "object", "properties": {"value": {"type": "integer"}}
        }
        session.commit()
    before = _state(db_engine, key)
    rejected = api_client.post(
        f"{base}/publish", headers=headers,
        json={"expectedDraftVersion": detail["draftVersion"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"] == "$.value: expected integer"
    assert _state(db_engine, key) == before


def test_review_history_source_and_legacy_publish_compatibility(api_client, db_engine, review_config):
    key, base, headers, detail = review_config
    # Legacy mode still publishes the saved server draft.
    legacy = api_client.post(f"{base}/publish", headers=headers, json={"expectedDraftVersion": 1, "comment": "TEST legacy"})
    assert legacy.status_code == 200, legacy.text
    assert _state(db_engine, key)[4][0][1] == detail["draftPayload"]
    detail = api_client.get(base, headers=headers).json()
    saved = api_client.put(f"{base}/draft", headers=headers, json={"expectedDraftVersion": detail["draftVersion"], "payload": {"value": "TEST v2"}})
    assert saved.status_code == 200, saved.text
    before = _state(db_engine, key)
    conflict = api_client.post(f"{base}/publish", headers=headers, json={"expectedDraftVersion": saved.json()["draftVersion"], "expectedPublishedVersion": None})
    assert conflict.status_code == 409, conflict.text
    assert _state(db_engine, key) == before
    accepted = api_client.post(f"{base}/publish", headers=headers, json={"expectedDraftVersion": saved.json()["draftVersion"], "expectedPublishedVersion": 1})
    assert accepted.status_code == 200, accepted.text
    detail = api_client.get(base, headers=headers).json()
    body = _body(detail, {"value": "TEST edited historical candidate"}, sourceRevisionVersion=1)
    preview = api_client.post(f"{base}/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["sourceRevisionVersion"] == 1
    published = api_client.post(f"{base}/publish", headers=headers, json=body)
    assert published.status_code == 200, published.text
    assert published.json()["publishedVersion"] == 3
