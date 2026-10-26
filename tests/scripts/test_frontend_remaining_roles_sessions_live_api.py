"""Own TEST role/user binding and explicit session revoke on the original clone.

Real HTTP, real CSRF, real application sessions and fresh SQL through the
original owned child and its original fixtures. This is not native/browser,
worker-completion or deployment acceptance. Only this file's own TEST facts are
written: the shipped built-in roles are never sent a mutating request, and the
built-in refusal path is exercised on one own TEST surrogate role.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
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
from tts_erp_v2.accounts.models import (
    Role,
    RolePermission,
    User,
    UserRole,
    UserSession,
)
from tts_erp_v2.middleware.session_auth import SESSION_COOKIE_NAME

# Alias the original decorated, module-scoped fixture, never its wrapped body.
clone = owned_clone
pytestmark = [pytest.mark.domain_frontend, pytest.mark.layer_integration]

# Permission codes are asserted present in the live catalog response before
# this file uses them; this file defines no permission policy of its own.
DASHBOARD_PAGE = "page:dashboard"
RUNTIME_CONFIGS_PAGE = "page:runtime-configs"


@dataclass
class OwnedRole:
    """One own TEST role: the exact code owns it, the names prove ownership."""

    code: str
    names: list[str]
    api_tier: str
    permissions: tuple[str, ...]

    @property
    def name(self) -> str:
        """The exact name this test last set through the API."""
        return self.names[-1]


@dataclass
class OwnedUser:
    """One own TEST user: the exact username owns it, the names prove ownership."""

    username: str
    display_names: list[str]
    password: str = field(repr=False)
    user_pk: int | None = None

    @property
    def display_name(self) -> str:
        """The exact display name this test last set through the API."""
        return self.display_names[-1]


def role_facts(clone: OwnedClone, code: str) -> tuple[tuple | None, tuple[str, ...]]:
    """Safe role columns and permission codes only, in an independent transaction."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        row = session.execute(
            select(Role.code, Role.name, Role.api_tier, Role.is_builtin).where(
                Role.code == code
            )
        ).one_or_none()
        permissions = tuple(
            session.scalars(
                select(RolePermission.permission_code)
                .where(RolePermission.role_code == code)
                .order_by(RolePermission.permission_code)
            )
        )
    return (tuple(row) if row is not None else None, permissions)


def user_facts(
    clone: OwnedClone, user_pk: int
) -> tuple[tuple | None, tuple[str, ...], dict[int, bool]]:
    """Safe user columns, bound role codes and per-session revocation flags.

    No password hash, token hash or cookie value ever enters the result.
    """
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        row = session.execute(
            select(User.id, User.username, User.display_name, User.status).where(
                User.id == user_pk
            )
        ).one_or_none()
        roles = tuple(
            session.scalars(
                select(UserRole.role_code)
                .where(UserRole.user_id == user_pk)
                .order_by(UserRole.role_code)
            )
        )
        sessions = {
            session_id: revoked is not None
            for session_id, revoked in session.execute(
                select(UserSession.id, UserSession.revoked_at)
                .where(UserSession.user_id == user_pk)
                .order_by(UserSession.id)
            )
        }
    return (tuple(row) if row is not None else None, roles, sessions)


def session_id_for_cookie(
    clone: OwnedClone, client: httpx.Client, user_pk: int
) -> int | None:
    """Bind one retained cookie to its own session row and expose only the id."""
    cookies: list[str] = []
    for cookie in client.cookies.jar:
        if cookie.name == SESSION_COOKIE_NAME and cookie.value is not None:
            cookies.append(cookie.value)
    if len(cookies) != 1:
        return None
    digest = sha256(cookies[0].encode()).digest()
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        return session.scalar(
            select(UserSession.id).where(
                UserSession.user_id == user_pk,
                UserSession.token_hash == digest,
            )
        )


def user_id_by_username(clone: OwnedClone, username: str) -> int | None:
    """Exact unique TEST username lookup in an independent transaction."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        return session.scalar(select(User.id).where(User.username == username))


def remove_owned_accounts(clone: OwnedClone, role: OwnedRole, user: OwnedUser) -> None:
    """Delete only this test's own rows in foreign-key order, then prove absence.

    The user id is recovered from the exact unique TEST username when the
    creation response was never parsed, so a half-created fact is still
    removed. The exact username and role code own the rows; the known names
    only prove that the rows really are this test's own.
    """
    require_data_probe(clone.probe)
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        recovered = session.scalar(
            select(User.id).where(User.username == user.username)
        )
        user_pk = user.user_pk if user.user_pk is not None else recovered
        if user_pk is not None:
            user_row = session.execute(
                select(User.display_name).where(User.id == user_pk)
            ).one_or_none()
            if user_row is not None:
                assert user_row[0] in user.display_names
            session_ids = list(
                session.scalars(
                    select(UserSession.id).where(UserSession.user_id == user_pk)
                )
            )
            role_codes = list(
                session.scalars(
                    select(UserRole.role_code).where(UserRole.user_id == user_pk)
                )
            )
            clone.child.require_live()
            session.execute(
                delete(UserSession).where(
                    UserSession.id.in_(session_ids),
                    UserSession.user_id == user_pk,
                )
            )
            clone.child.require_live()
            session.execute(
                delete(UserRole).where(
                    UserRole.user_id == user_pk,
                    UserRole.role_code.in_(role_codes),
                )
            )
            clone.child.require_live()
            session.execute(
                delete(User).where(User.id == user_pk, User.username == user.username)
            )
        role_row = session.execute(
            select(Role.name).where(Role.code == role.code)
        ).one_or_none()
        if role_row is not None:
            assert role_row[0] in role.names
        clone.child.require_live()
        session.execute(
            delete(RolePermission).where(RolePermission.role_code == role.code)
        )
        clone.child.require_live()
        session.execute(delete(Role).where(Role.code == role.code))
        clone.child.require_live()
        session.commit()
    assert user_id_by_username(clone, user.username) is None
    if user_pk is not None:
        assert user_facts(clone, user_pk) == (None, (), {})
    assert role_facts(clone, role.code) == (None, ())


@pytest.fixture(scope="module")
def admin_client(clone: OwnedClone) -> Iterator[httpx.Client]:
    """One admin session per prefix, reused by both composites."""
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as client:
        login(clone, client, "admin")
        yield client
        owned(clone, client.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)


@pytest.fixture()
def owned_accounts(
    clone: OwnedClone, admin_client: httpx.Client
) -> Iterator[tuple[OwnedRole, OwnedUser]]:
    """Own TEST role plus an own TEST user bound to it, created through the API."""
    require_data_probe(clone.probe)
    suffix = uuid4().hex[:8]
    catalog = owned(clone, admin_client.get(clone.prefix + "/v2/roles"), 200).json()
    codes = tuple(item["code"] for item in catalog["allPermissions"])
    assert DASHBOARD_PAGE in codes and RUNTIME_CONFIGS_PAGE in codes
    role = OwnedRole(
        code="test_roles_" + suffix,
        names=["TEST Roles " + suffix],
        api_tier="readwrite",
        permissions=(DASHBOARD_PAGE, RUNTIME_CONFIGS_PAGE),
    )
    user = OwnedUser(
        username="test_rolesu_" + suffix,
        display_names=["TEST Roles User " + suffix],
        password="TEST_Roles9_" + suffix,
    )
    try:
        created = owned(
            clone,
            admin_client.post(
                clone.prefix + "/v2/roles",
                json={
                    "code": role.code,
                    "name": role.name,
                    "apiTier": role.api_tier,
                    "permissions": list(role.permissions),
                },
                headers=CSRF,
            ),
            201,
        ).json()
        assert created["code"] == role.code
        created_user = owned(
            clone,
            admin_client.post(
                clone.prefix + "/v2/users",
                json={
                    "username": user.username,
                    "displayName": user.display_name,
                    "password": user.password,
                    "roles": [role.code],
                },
                headers=CSRF,
            ),
            201,
        ).json()
        assert created_user["username"] == user.username
        user.user_pk = created_user["id"]
        assert isinstance(user.user_pk, int) and user.user_pk > 0
        yield role, user
    finally:
        remove_owned_accounts(clone, role, user)


@pytest.fixture()
def builtin_surrogate(clone: OwnedClone) -> Iterator[tuple[str, str]]:
    """One own TEST role flagged built-in, inserted on the owned clone only.

    The shipped service never creates a built-in role, so the shipped refusal
    path is exercised on a surrogate this test owns. The seeded built-in roles
    are never sent a mutating request.
    """
    require_data_probe(clone.probe)
    suffix = uuid4().hex[:8]
    code = "test_builtin_" + suffix
    name = "TEST Builtin Surrogate " + suffix
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        clone.child.require_live()
        session.add(Role(code=code, name=name, api_tier="admin", is_builtin=True))
        clone.child.require_live()
        session.commit()
    try:
        assert role_facts(clone, code)[0] == (code, name, "admin", True)
        yield code, name
    finally:
        require_data_probe(clone.probe)
        with checked_session(
            clone.child.binding, clone.engine, clone.factory
        ) as session:
            clone.child.require_live()
            session.execute(
                delete(RolePermission).where(RolePermission.role_code == code)
            )
            clone.child.require_live()
            session.execute(
                delete(Role).where(
                    Role.code == code,
                    Role.name == name,
                    Role.is_builtin.is_(True),
                )
            )
            clone.child.require_live()
            session.commit()
        assert role_facts(clone, code) == (None, ())


def test_own_test_role_and_user_binding_lifecycle(
    clone: OwnedClone,
    admin_client: httpx.Client,
    owned_accounts: tuple[OwnedRole, OwnedUser],
    builtin_surrogate: tuple[str, str],
) -> None:
    """Own TEST role and user: catalog, binding, update, refusal and deletion."""
    role, user = owned_accounts
    user_pk = user.user_pk
    assert user_pk is not None
    suffix = uuid4().hex[:8]
    roles_path = clone.prefix + "/v2/roles"
    users_path = clone.prefix + "/v2/users"
    role_path = roles_path + "/" + role.code
    user_path = users_path + "/" + str(user_pk)
    require_data_probe(clone.probe)

    # A readonly cookie may not create a role: refused, and no own TEST row.
    denied_code = "test_rolesdenied_" + suffix
    with httpx.Client(base_url=clone.child.base, trust_env=False, timeout=5) as viewer:
        login(clone, viewer, "viewer")
        before_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
        owned(
            clone,
            viewer.post(
                roles_path,
                json={
                    "code": denied_code,
                    "name": "TEST Roles Denied " + suffix,
                    "apiTier": "readwrite",
                    "permissions": [DASHBOARD_PAGE],
                },
                headers=CSRF,
            ),
            403,
        )
        assert role_facts(clone, denied_code) == (None, ())
        after_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
        assert after_facts == before_facts
        owned(clone, viewer.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204)

    # The created role and its own TEST user are real rows, bound to each other.
    assert role_facts(clone, role.code) == (
        (role.code, role.name, role.api_tier, False),
        role.permissions,
    )
    assert user_facts(clone, user_pk) == (
        (user_pk, user.username, user.display_name, "active"),
        (role.code,),
        {},
    )
    detail = owned(clone, admin_client.get(user_path), 200).json()
    assert detail["username"] == user.username
    assert detail["displayName"] == user.display_name
    assert detail["status"] == "active"
    assert detail["roles"] == [role.code]
    assert detail["pages"] == sorted(role.permissions)
    assert detail["apiTier"] == role.api_tier
    listing = owned(clone, admin_client.get(users_path), 200).json()
    listed = [row for row in listing["users"] if row["username"] == user.username]
    assert len(listed) == 1
    assert listed[0]["id"] == user_pk
    assert listed[0]["roles"] == [role.code]
    assert listed[0]["activeSessions"] == 0

    # Role update through the API: name, permission set and API tier.
    updated_name = "TEST Roles Updated " + suffix
    role.names.append(updated_name)
    owned(
        clone,
        admin_client.patch(
            role_path,
            json={
                "name": updated_name,
                "apiTier": "readonly",
                "permissions": [DASHBOARD_PAGE],
            },
            headers=CSRF,
        ),
        200,
    )
    assert role_facts(clone, role.code) == (
        (role.code, updated_name, "readonly", False),
        (DASHBOARD_PAGE,),
    )

    # A role still bound to a user cannot be deleted, and nothing changes.
    before_role = role_facts(clone, role.code)
    before_user = user_facts(clone, user_pk)
    owned(clone, admin_client.delete(role_path, headers=CSRF), 400)
    assert role_facts(clone, role.code) == before_role
    assert user_facts(clone, user_pk) == before_user

    # Unbind the own TEST user and rename it through the API.
    renamed = "TEST Roles User Renamed " + suffix
    user.display_names.append(renamed)
    owned(
        clone,
        admin_client.patch(
            user_path,
            json={"displayName": renamed, "roles": []},
            headers=CSRF,
        ),
        200,
    )
    assert user_facts(clone, user_pk) == (
        (user_pk, user.username, renamed, "active"),
        (),
        {},
    )

    # The unbound own TEST role is deletable and its rows are really gone.
    owned(clone, admin_client.delete(role_path, headers=CSRF), 200)
    assert role_facts(clone, role.code) == (None, ())
    roles_after = owned(clone, admin_client.get(roles_path), 200).json()
    assert role.code not in {row["code"] for row in roles_after["roles"]}
    detail = owned(clone, admin_client.get(user_path), 200).json()
    assert detail["roles"] == []
    assert detail["pages"] == []
    assert detail["apiTier"] == "readonly"

    # A valid own TEST target without the CSRF header is refused and unwritten.
    nocsrf_code = "test_rolesnocsrf_" + suffix
    before_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
    owned(
        clone,
        admin_client.post(
            roles_path,
            json={
                "code": nocsrf_code,
                "name": "TEST Roles No Csrf " + suffix,
                "apiTier": "readwrite",
                "permissions": [DASHBOARD_PAGE],
            },
        ),
        403,
    )
    assert role_facts(clone, nocsrf_code) == (None, ())
    after_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
    assert after_facts == before_facts

    # The shipped tier/page rule refuses an under-tiered own TEST role.
    tier_code = "test_rolestier_" + suffix
    before_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
    owned(
        clone,
        admin_client.post(
            roles_path,
            json={
                "code": tier_code,
                "name": "TEST Roles Tier " + suffix,
                "apiTier": "readonly",
                "permissions": [RUNTIME_CONFIGS_PAGE],
            },
            headers=CSRF,
        ),
        400,
    )
    assert role_facts(clone, tier_code) == (None, ())
    after_facts = (role_facts(clone, role.code), user_facts(clone, user_pk))
    assert after_facts == before_facts

    # Built-in protection on the own TEST surrogate: refused, and rows stay.
    surrogate_code, surrogate_name = builtin_surrogate
    surrogate_before = role_facts(clone, surrogate_code)
    assert surrogate_before[0] == (surrogate_code, surrogate_name, "admin", True)
    owned(
        clone,
        admin_client.patch(
            roles_path + "/" + surrogate_code,
            json={"apiTier": "readonly"},
            headers=CSRF,
        ),
        400,
    )
    assert role_facts(clone, surrogate_code) == surrogate_before
    owned(
        clone,
        admin_client.delete(roles_path + "/" + surrogate_code, headers=CSRF),
        400,
    )
    assert role_facts(clone, surrogate_code) == surrogate_before


def test_own_user_session_explicit_revoke_and_guard_facts(
    clone: OwnedClone,
    admin_client: httpx.Client,
    owned_accounts: tuple[OwnedRole, OwnedUser],
) -> None:
    """Explicit revoke-one and revoke-all on an own TEST user's own sessions."""
    _, user = owned_accounts
    user_pk = user.user_pk
    assert user_pk is not None
    require_data_probe(clone.probe)
    protected = clone.prefix + "/v2/reporting/manual-costs"
    sessions_path = clone.prefix + f"/v2/users/{user_pk}/sessions"

    with (
        httpx.Client(
            base_url=clone.child.base, trust_env=False, timeout=5
        ) as subject_one,
        httpx.Client(
            base_url=clone.child.base, trust_env=False, timeout=5
        ) as subject_two,
    ):
        for subject in (subject_one, subject_two):
            owned(
                clone,
                subject.post(
                    clone.prefix + "/v2/auth/login",
                    json={"username": user.username, "password": user.password},
                ),
                200,
            )
            me = owned(clone, subject.get(clone.prefix + "/v2/auth/me"), 200).json()
            assert me["authenticated"] is True
            assert me["username"] == user.username
            owned(clone, subject.get(protected), 200)

        session_one = session_id_for_cookie(clone, subject_one, user_pk)
        session_two = session_id_for_cookie(clone, subject_two, user_pk)
        assert session_one is not None and session_two is not None
        assert session_one != session_two
        listed = owned(clone, admin_client.get(sessions_path), 200).json()
        assert {row["id"] for row in listed["sessions"]} == {session_one, session_two}
        assert all(row["active"] is True for row in listed["sessions"])
        live_user_facts = user_facts(clone, user_pk)
        assert live_user_facts[2] == {session_one: False, session_two: False}

        # A readonly cookie may not revoke sessions: refused, nothing revoked.
        with httpx.Client(
            base_url=clone.child.base, trust_env=False, timeout=5
        ) as viewer:
            login(clone, viewer, "viewer")
            owned(clone, viewer.delete(sessions_path, headers=CSRF), 403)
            assert user_facts(clone, user_pk) == live_user_facts
            owned(
                clone, viewer.post(clone.prefix + "/v2/auth/logout", headers=CSRF), 204
            )

        # The same valid target without the CSRF header: refused, nothing revoked.
        owned(clone, admin_client.delete(sessions_path), 403)
        assert user_facts(clone, user_pk) == live_user_facts

        # An unknown session id is refused, and the live rows stay untouched.
        owned(
            clone,
            admin_client.delete(
                sessions_path + "/" + str(session_one + 1000000), headers=CSRF
            ),
            404,
        )
        assert user_facts(clone, user_pk) == live_user_facts

        # Revoke one: only that retained cookie stops working.
        revoked_one = owned(
            clone,
            admin_client.delete(sessions_path + "/" + str(session_one), headers=CSRF),
            200,
        ).json()
        assert revoked_one == {"ok": True, "revoked": 1}
        owned(clone, subject_one.get(protected), 401)
        owned(clone, subject_two.get(protected), 200)
        assert user_facts(clone, user_pk)[2] == {session_one: True, session_two: False}

        # Revoke all: the remaining retained cookie stops working as well.
        revoked_all = owned(
            clone, admin_client.delete(sessions_path, headers=CSRF), 200
        ).json()
        assert revoked_all["ok"] is True
        assert revoked_all["revoked"] >= 1
        owned(clone, subject_one.get(protected), 401)
        owned(clone, subject_two.get(protected), 401)
        assert user_facts(clone, user_pk)[2] == {
            session_one: True,
            session_two: True,
        }


def test_own_fact_cleanup_survives_unexpected_creation_before_id_capture(
    clone: OwnedClone,
    admin_client: httpx.Client,
) -> None:
    """A real 201 the harness did not expect must not leak own TEST rows.

    The role creation really answers 201, so the role exists and its code is
    known without parsing a body. The user creation really answers 201 as
    well, but the harness expected 403, so owned() fails before the user id is
    ever captured. The own fact cleanup must still recover the exact rows from
    their known markers and prove their fresh absence.
    """
    require_data_probe(clone.probe)
    suffix = uuid4().hex[:8]
    role_code = "test_regressr_" + suffix
    role_name = "TEST Regress Role " + suffix
    username = "test_regressu_" + suffix
    display_name = "TEST Regress User " + suffix
    password = "TEST_Regress9_" + suffix
    roles_path = clone.prefix + "/v2/roles"
    users_path = clone.prefix + "/v2/users"
    role = OwnedRole(
        code=role_code,
        names=[role_name],
        api_tier="readwrite",
        permissions=(DASHBOARD_PAGE,),
    )
    user = OwnedUser(
        username=username,
        display_names=[display_name],
        password=password,
    )
    try:
        # Real 201: the role row exists and its exact code is already known.
        owned(
            clone,
            admin_client.post(
                roles_path,
                json={
                    "code": role_code,
                    "name": role_name,
                    "apiTier": "readwrite",
                    "permissions": [DASHBOARD_PAGE],
                },
                headers=CSRF,
            ),
            201,
        )
        # The harness expected this user creation to be refused. The real API
        # answers 201 and commits the row, so owned() fails before the user id
        # is captured: exactly the hazard the own fact cleanup must survive.
        with pytest.raises(pytest.fail.Exception):
            owned(
                clone,
                admin_client.post(
                    users_path,
                    json={
                        "username": username,
                        "displayName": display_name,
                        "password": password,
                        "roles": [role_code],
                    },
                    headers=CSRF,
                ),
                403,
            )
        assert user.user_pk is None
        # Both rows are really committed, found by the exact known markers.
        assert user_id_by_username(clone, username) is not None
        assert role_facts(clone, role_code)[0] is not None
        # The same cleanup, with no captured user id, must remove every own row.
        remove_owned_accounts(clone, role, user)
        assert user_id_by_username(clone, username) is None
        assert role_facts(clone, role_code) == (None, ())
    finally:
        remove_owned_accounts(clone, role, user)
