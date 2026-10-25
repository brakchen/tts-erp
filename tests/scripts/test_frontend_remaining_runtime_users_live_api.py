"""Runtime/users HTTP and fresh-SQL acceptance on the original owned child.

No native/editor/visual or deployment claim. Nonsecret test_runtime_* keys and
new TEST users are removed per function; the original runner owns clone DROP.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from tests.scripts.test_frontend_remaining_live_api import (
    CSRF,
    OwnedClone,
    login,
    owned,
)
from tests.scripts.test_frontend_remaining_live_api import clone as owned_clone
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_canary import require_data_probe
from tts_erp_v2.accounts.models import User, UserRole, UserSession
from tts_erp_v2.accounts.passwords import verify_password
from tts_erp_v2.db.models.config import RuntimeConfigItem, RuntimeConfigRevision

# Alias the original decorated, module-scoped fixture, never its wrapped body.
clone = owned_clone
pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]


@dataclass
class RuntimeTarget:
    key: str
    marker: str


@dataclass
class UserTarget:
    username: str
    marker: str
    user_pk: int | None = None


@pytest.fixture()
def runtime_targets(clone: OwnedClone) -> Iterator[tuple[RuntimeTarget, RuntimeTarget]]:
    suffix = uuid4().hex
    targets = tuple(RuntimeTarget("test_runtime_remaining_" + suffix + tail,
                                  "TEST_runtime_" + suffix + tail)
                    for tail in ("_primary", "_denied"))
    require_data_probe(clone.probe)
    for target in targets:
        assert runtime_state(clone, target.key) == (None, [])
    try:
        yield targets
    finally:
        for target in targets:
            require_data_probe(clone.probe)
            clone.child.require_live()
            with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
                item = session.get(RuntimeConfigItem, target.key)
                if item is not None:
                    assert item.display_name == target.marker
                    revision_ids = list(session.scalars(select(RuntimeConfigRevision.id).where(
                        RuntimeConfigRevision.config_key == target.key
                    )))
                    clone.child.require_live()
                    session.execute(delete(RuntimeConfigRevision).where(
                        RuntimeConfigRevision.id.in_(revision_ids),
                        RuntimeConfigRevision.config_key == target.key,
                    ))
                    clone.child.require_live()
                    session.execute(delete(RuntimeConfigItem).where(
                        RuntimeConfigItem.config_key == target.key,
                        RuntimeConfigItem.display_name == target.marker,
                    ))
                    clone.child.require_live()
                    session.commit()
            assert runtime_state(clone, target.key) == (None, [])


@pytest.fixture()
def user_targets(clone: OwnedClone) -> Iterator[tuple[UserTarget, UserTarget]]:
    suffix = uuid4().hex
    targets = tuple(UserTarget("test_remaining_" + suffix[:14] + tail,
                               "TEST_user_" + suffix + tail)
                    for tail in ("_p", "_d"))
    require_data_probe(clone.probe)
    for target in targets:
        absent = user_state(clone, target) == (None, [], [])
        assert absent
    try:
        yield targets
    finally:
        for target in targets:
            require_data_probe(clone.probe)
            clone.child.require_live()
            with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
                user = session.scalar(select(User).where(
                    User.username == target.username, User.display_name == target.marker,
                ))
                if user is not None:
                    if target.user_pk is not None:
                        assert user.id == target.user_pk
                    # Exact unique marker also recovers the created ID if response parsing failed.
                    user_pk = user.id
                    session_ids = list(session.scalars(select(UserSession.id).where(
                        UserSession.user_id == user_pk
                    )))
                    role_codes = list(session.scalars(select(UserRole.role_code).where(
                        UserRole.user_id == user_pk
                    )))
                    clone.child.require_live()
                    session.execute(delete(UserSession).where(
                        UserSession.id.in_(session_ids), UserSession.user_id == user_pk,
                    ))
                    clone.child.require_live()
                    session.execute(delete(UserRole).where(
                        UserRole.user_id == user_pk, UserRole.role_code.in_(role_codes),
                    ))
                    clone.child.require_live()
                    session.execute(delete(User).where(
                        User.id == user_pk, User.username == target.username,
                        User.display_name == target.marker,
                    ))
                    clone.child.require_live()
                    session.commit()
            absent = user_state(clone, target) == (None, [], [])
            assert absent
            if target.user_pk is not None:
                assert target_sessions(clone, target.user_pk) == []


def runtime_state(clone: OwnedClone, key: str) -> tuple:
    """Only this test's nonsecret key, in a new independent transaction."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        item = session.execute(select(*RuntimeConfigItem.__table__.columns).where(
            RuntimeConfigItem.config_key == key
        )).one_or_none()
        revisions = session.execute(select(*RuntimeConfigRevision.__table__.columns).where(
            RuntimeConfigRevision.config_key == key
        ).order_by(RuntimeConfigRevision.version)).all()
        return (dict(item._mapping) if item else None, [dict(r._mapping) for r in revisions])


def user_state(clone: OwnedClone, target: UserTarget) -> tuple:
    """Private comparison snapshot; callers assert only precomputed booleans.

    Password/token hashes never enter assertion representations or diagnostics.
    """
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        user = session.execute(select(*User.__table__.columns).where(
            User.username == target.username
        )).one_or_none()
        if user is None:
            return None, [], []
        roles = session.execute(select(*UserRole.__table__.columns).where(
            UserRole.user_id == user.id
        ).order_by(UserRole.role_code)).all()
        sessions = session.execute(select(*UserSession.__table__.columns).where(
            UserSession.user_id == user.id
        ).order_by(UserSession.id)).all()
        return dict(user._mapping), [tuple(r) for r in roles], [tuple(r) for r in sessions]


def target_sessions(clone: OwnedClone, user_pk: int) -> list[tuple[int, bool]]:
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        return [(row.id, row.revoked_at is not None) for row in session.execute(
            select(UserSession.id, UserSession.revoked_at).where(
                UserSession.user_id == user_pk
            ).order_by(UserSession.id)
        )]


def password_matches(clone: OwnedClone, user_pk: int, password: str) -> bool:
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        password_hash = session.scalar(select(User.password_hash).where(User.id == user_pk))
        return isinstance(password_hash, str) and verify_password(password, password_hash)


def issued_session_matches(clone: OwnedClone, client: httpx.Client, user_pk: int) -> bool:
    """Bind the actual login cookie to fresh SQL, exposing only a boolean."""
    cookies = [c.value for c in client.cookies.jar if c.name == "tts_erp_session"]
    if len(cookies) != 1:
        return False
    digest = sha256(cookies[0].encode()).digest()
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        return session.scalar(select(UserSession.id).where(
            UserSession.user_id == user_pk, UserSession.token_hash == digest,
            UserSession.revoked_at.is_(None),
        )) is not None


def test_runtime_owned_key_draft_publish_history_and_write_guards(
    clone: OwnedClone, runtime_targets: tuple[RuntimeTarget, RuntimeTarget],
) -> None:
    target, denied_create = runtime_targets
    path = clone.prefix + "/v2/config/runtime/items"
    item_path = path + "/" + target.key
    initial = {"counter": 900719925474099312345, "fraction": 0.125, "label": target.marker}
    # Raw JSON uses an exact integer above JS's safe range and a supported,
    # exactly representable fractional number, not a financial decimal contract.
    raw_draft = ('{"expectedDraftVersion":1,"payload":{"counter":900719925474099312346,'
                 '"fraction":0.625,"label":"' + target.marker + '"}}')
    updated = {**initial, "counter": 900719925474099312346, "fraction": 0.625}
    create = {"configKey": target.key, "displayName": target.marker, "draftPayload": initial}
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        login(clone, client, "viewer")
        before = runtime_state(clone, target.key)
        owned(clone, client.post(path, json=create, headers=CSRF), 403)
        assert runtime_state(clone, target.key) == before
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)
        login(clone, client, "operator")
        detail = owned(clone, client.post(path, json=create, headers=CSRF), 201).json()
        assert detail["configKey"] == target.key
        assert detail["draftVersion"] == 1
        assert detail["draftPayload"] == initial
        state, history = runtime_state(clone, target.key)
        assert state is not None and history == []
        assert state["draft_payload"] == initial
        assert state["published_version"] is None
        owned(clone, client.put(item_path + "/draft", content=raw_draft,
                               headers={**CSRF, "Content-Type": "application/json"}), 200)
        detail = owned(clone, client.get(item_path), 200).json()
        assert detail["draftVersion"] == 2
        assert detail["draftPayload"] == updated
        state, history = runtime_state(clone, target.key)
        assert state is not None and history == []
        assert state["draft_version"] == 2
        assert state["draft_payload"] == updated
        before = runtime_state(clone, target.key)
        owned(clone, client.put(item_path + "/draft", json={
            "expectedDraftVersion": 1, "payload": initial,
        }, headers=CSRF), 409)
        assert runtime_state(clone, target.key) == before
        owned(clone, client.post(item_path + "/publish", json={
            "expectedDraftVersion": 1,
        }, headers=CSRF), 409)
        assert runtime_state(clone, target.key) == before
        published = owned(clone, client.post(item_path + "/publish", json={
            "expectedDraftVersion": 2, "comment": target.marker,
        }, headers=CSRF), 200).json()
        assert published == {"configKey": target.key, "publishedVersion": 1}
        detail = owned(clone, client.get(item_path), 200).json()
        assert detail["draftPayload"] is None
        assert detail["draftVersion"] == 3
        assert detail["publishedPayload"] == updated
        history = owned(clone, client.get(item_path + "/revisions"), 200).json()["items"]
        assert len(history) == 1
        assert history[0]["version"] == 1
        assert history[0]["payload"] == updated
        assert history[0]["comment"] == target.marker
        current = owned(clone, client.get(clone.prefix + "/v2/config/runtime/snapshot"), 200)
        current_item = current.json()["items"].get(target.key)
        assert current_item == {"version": 1, "payload": updated}
        state, revisions = runtime_state(clone, target.key)
        assert state is not None and len(revisions) == 1
        assert state["draft_version"] == 3 and state["draft_payload"] is None
        assert state["published_version"] == 1
        assert revisions[0]["version"] == 1
        assert revisions[0]["payload"] == updated
        assert revisions[0]["comment"] == target.marker

        # Complete the supported lifecycle before diagnosing security failures.
        # Each status is still required to be 403; passing the observed status
        # to owned() only defers that assertion while retaining its attestation.
        owned(clone, client.put(item_path + "/draft", json={
            "expectedDraftVersion": 3, "payload": initial,
        }, headers=CSRF), 200)
        before = runtime_state(clone, target.key)
        response = client.post(item_path + "/publish", json={
            "expectedDraftVersion": before[0]["draft_version"], "comment": target.marker,
        })
        owned(clone, response, response.status_code)
        publish_status = response.status_code
        publish_unchanged = runtime_state(clone, target.key) == before
        before = runtime_state(clone, target.key)
        response = client.put(item_path + "/draft", json={
            "expectedDraftVersion": before[0]["draft_version"], "payload": updated,
        })
        owned(clone, response, response.status_code)
        draft_status = response.status_code
        draft_unchanged = runtime_state(clone, target.key) == before
        before = runtime_state(clone, denied_create.key)
        response = client.post(path, json={
            "configKey": denied_create.key, "displayName": denied_create.marker,
            "draftPayload": {"label": denied_create.marker},
        })
        owned(clone, response, response.status_code)
        create_status = response.status_code
        create_unchanged = runtime_state(clone, denied_create.key) == before
        assert (publish_status, publish_unchanged, draft_status, draft_unchanged,
                create_status, create_unchanged) == (403, True, 403, True, 403, True)


def test_users_owned_target_password_revocation_disable_and_denied_writes(
    clone: OwnedClone, user_targets: tuple[UserTarget, UserTarget],
) -> None:
    target, denied_create = user_targets
    users_path = clone.prefix + "/v2/users"
    old_password = "TEST_Old9_" + uuid4().hex
    new_password = "TEST_New8_" + uuid4().hex
    payload = {"username": target.username, "displayName": target.marker,
               "password": old_password, "roles": ["viewer"]}
    with (httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as admin,
          httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as viewer,
          httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as subject):
        login(clone, viewer, "viewer")
        before = user_state(clone, target)
        owned(clone, viewer.post(users_path, json=payload, headers=CSRF), 403)
        unchanged = user_state(clone, target) == before
        assert unchanged
        login(clone, admin, "admin")
        created = owned(clone, admin.post(users_path, json=payload, headers=CSRF), 201).json()
        target.user_pk = created["id"]
        assert isinstance(target.user_pk, int) and target.user_pk > 0
        assert created["username"] == target.username
        user_path = users_path + f"/{target.user_pk}"
        detail = owned(clone, admin.get(user_path), 200).json()
        assert detail["username"] == target.username
        assert detail["displayName"] == target.marker
        assert detail["roles"] == ["viewer"] and detail["status"] == "active"
        persisted = user_state(clone, target)
        identity_matches = (persisted[0]["id"] == target.user_pk
                            and persisted[0]["display_name"] == target.marker
                            and persisted[0]["status"] == "active"
                            and [role[1] for role in persisted[1]] == ["viewer"])
        assert identity_matches
        old_password_valid = password_matches(clone, target.user_pk, old_password)
        assert old_password_valid
        assert target_sessions(clone, target.user_pk) == []
        owned(clone, subject.post(clone.prefix + "/v2/auth/login", json={
            "username": target.username, "password": old_password,
        }), 200)
        cookie_has_owned_session = issued_session_matches(clone, subject, target.user_pk)
        assert cookie_has_owned_session
        owned(clone, subject.get(clone.prefix + "/v2/reporting/manual-costs"), 200)
        initial_sessions = target_sessions(clone, target.user_pk)
        assert len(initial_sessions) == 1 and initial_sessions[0][1] is False
        before = user_state(clone, target)
        for client, headers in ((viewer, CSRF),):
            owned(clone, client.post(user_path + "/password", json={
                "newPassword": new_password,
            }, headers=headers), 403)
            unchanged = user_state(clone, target) == before
            assert unchanged
            owned(clone, client.patch(user_path, json={"status": "disabled"},
                                      headers=headers), 403)
            unchanged = user_state(clone, target) == before
            assert unchanged
        owned(clone, admin.post(user_path + "/password", json={
            "newPassword": new_password,
        }, headers=CSRF), 200)
        changed = user_state(clone, target)
        hash_changed = changed[0]["password_hash"] != before[0]["password_hash"]
        timestamp_changed = changed[0]["password_changed_at"] > before[0]["password_changed_at"]
        assert hash_changed and timestamp_changed
        new_password_valid = password_matches(clone, target.user_pk, new_password)
        old_password_valid = password_matches(clone, target.user_pk, old_password)
        assert new_password_valid and not old_password_valid
        assert target_sessions(clone, target.user_pk) == [(initial_sessions[0][0], True)]
        # Retain the actually issued cookie: the command, not client logout,
        # must make the previously authorized protected request fail.
        owned(clone, subject.get(clone.prefix + "/v2/reporting/manual-costs"), 401)
        before_bad_login = user_state(clone, target)
        owned(clone, subject.post(clone.prefix + "/v2/auth/login", json={
            "username": target.username, "password": old_password,
        }), 401)
        unchanged = user_state(clone, target) == before_bad_login
        assert unchanged
        owned(clone, subject.post(clone.prefix + "/v2/auth/login", json={
            "username": target.username, "password": new_password,
        }), 200)
        cookie_has_owned_session = issued_session_matches(clone, subject, target.user_pk)
        assert cookie_has_owned_session
        owned(clone, subject.get(clone.prefix + "/v2/reporting/manual-costs"), 200)
        sessions_before_disable = target_sessions(clone, target.user_pk)
        assert len(sessions_before_disable) == 2
        assert sessions_before_disable[0][1] is True
        assert sessions_before_disable[1][1] is False
        owned(clone, admin.patch(user_path, json={"status": "disabled"}, headers=CSRF), 200)
        disabled = user_state(clone, target)
        is_disabled = disabled[0]["status"] == "disabled"
        assert is_disabled
        assert target_sessions(clone, target.user_pk) == [
            (session_id, True) for session_id, _revoked in sessions_before_disable
        ]
        detail = owned(clone, admin.get(user_path), 200).json()
        assert detail["status"] == "disabled"
        owned(clone, subject.get(clone.prefix + "/v2/reporting/manual-costs"), 401)
        before_disabled_login = user_state(clone, target)
        owned(clone, subject.post(clone.prefix + "/v2/auth/login", json={
            "username": target.username, "password": new_password,
        }), 401)
        unchanged = user_state(clone, target) == before_disabled_login
        assert unchanged

        # Canonical admin reset/status services accept a disabled target; no
        # active-status precondition or password proof is required for admin.
        before = user_state(clone, target)
        response = admin.post(user_path + "/password", json={
            "newPassword": "TEST_Denied7_" + uuid4().hex,
        })
        owned(clone, response, response.status_code)
        reset_status = response.status_code
        reset_unchanged = user_state(clone, target) == before
        before = user_state(clone, target)
        response = admin.patch(user_path, json={"status": "active"})
        owned(clone, response, response.status_code)
        patch_status = response.status_code
        patch_unchanged = user_state(clone, target) == before
        before = user_state(clone, denied_create)
        response = admin.post(users_path, json={
            "username": denied_create.username, "displayName": denied_create.marker,
            "password": "TEST_Denied6_" + uuid4().hex, "roles": ["viewer"],
        })
        owned(clone, response, response.status_code)
        create_status = response.status_code
        create_unchanged = user_state(clone, denied_create) == before
        assert (reset_status, reset_unchanged, patch_status, patch_unchanged,
                create_status, create_unchanged) == (403, True, 403, True, 403, True)
