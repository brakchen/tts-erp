"""Actual isolated cookie/role acceptance; no business or browser commands."""
from __future__ import annotations

import importlib
from dataclasses import replace
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from tests.api.test_frontend_pages import _bootstrap
from tests.scripts.test_frontend_p2_canary import fake_child
from tests.support import frontend_p2_canary as canary
from tests.support.frontend_p2_binding import (
    NONCE_HEADER,
    checked_session,
    current_binding,
    verify_attestation,
)
from tests.support.frontend_p2_live import live_api_server

pytestmark = pytest.mark.domain_frontend
MODULE = "tests.support.frontend_p2_accounts"
REFUSAL = "P2 isolated account fixture refused"


def fake_probe(monkeypatch):
    value, child = fake_child(monkeypatch, canary)
    probe = canary.VerifiedDataProbe(child=child, prefix="", enum_pk=123, enum_type="TEST_type",
                                    enum_value="TEST_value", label_zh="TEST_label", key_name="TEST_key",
                                    readonly_key="TEST_readonly_secret")
    rows = [{"id": 123, "enum_type": "TEST_type", "enum_value": "TEST_value", "label_zh": "TEST_label", "sort_order": 0}]
    network = MagicMock(return_value=rows)
    monkeypatch.setattr(canary, "_bound_json", network)
    return value, probe, network


@pytest.mark.parametrize("fault", ["identity-only", "old-run", "wrong-origin", "wrong-prefix", "gone-fact", "duplicate", "changed-fact"])
def test_account_seed_requires_fresh_actual_probe_before_parent_engine_or_user_service(monkeypatch, fault):
    support = importlib.import_module(MODULE)
    _value, probe, network = fake_probe(monkeypatch)
    if fault == "identity-only":
        candidate = probe.child
    elif fault == "old-run":
        candidate = replace(probe, child=replace(probe.child, binding=replace(probe.child.binding, run_id="b" * 64)))
    elif fault == "wrong-origin":
        candidate = replace(probe, child=replace(probe.child, base="http://127.0.0.1:9877", port=9877))
    elif fault == "wrong-prefix":
        candidate = replace(probe, prefix="//TEST_wrong")
    else:
        candidate = probe
        if fault == "gone-fact":
            network.return_value = []
        elif fault == "duplicate":
            network.return_value *= 2
        else:
            network.return_value[0]["label_zh"] = "TEST_other"
    engine, service = MagicMock(), MagicMock()
    monkeypatch.setattr(support, "create_engine", engine)
    monkeypatch.setattr(support, "create_user", service)
    with pytest.raises(ValueError, match=f"^{REFUSAL}$"):
        support.seed_accounts(candidate)
    assert engine.call_count == service.call_count == 0


def parent_session(monkeypatch, support, value):
    engine = MagicMock(spec=Engine)
    engine.url = make_url(value)
    session = MagicMock()
    session.__enter__.return_value = session
    session.get_bind.return_value = engine
    factory = MagicMock()
    factory.kw = {"bind": engine}
    factory.return_value = session
    monkeypatch.setattr(support, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(support, "sessionmaker", MagicMock(return_value=factory))
    return engine, session, factory


@pytest.mark.parametrize("fault", ["bind", "database"])
def test_actual_parent_session_must_match_before_user_service(monkeypatch, fault):
    support = importlib.import_module(MODULE)
    value, probe, _network = fake_probe(monkeypatch)
    engine, session, _factory = parent_session(monkeypatch, support, value)
    if fault == "bind":
        wrong = MagicMock(spec=Engine)
        wrong.url = make_url(value).set(database="tts_erp_test_OTHER")
        session.get_bind.return_value = wrong
    else:
        session.scalar.return_value = "tts_erp_test_OTHER"
    service = MagicMock()
    monkeypatch.setattr(support, "create_user", service)
    with pytest.raises(ValueError, match=f"^{REFUSAL}$"):
        support.seed_accounts(probe)
    assert service.call_count == 0
    assert session.scalar.call_count == (0 if fault == "bind" else 1)
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("exit_at_commit", [False, True])
def test_internal_service_commits_keep_same_session_launch_gate_and_remove_listener(monkeypatch, exit_at_commit):
    support = importlib.import_module(MODULE)
    value, probe, _network = fake_probe(monkeypatch)
    engine, session, _factory = parent_session(monkeypatch, support, value)
    session.scalar.return_value = probe.child.binding.run_db
    listeners = []
    monkeypatch.setattr(support.event, "listen", lambda target, name, handler: listeners.append((target, name, handler)))
    remove = MagicMock()
    monkeypatch.setattr(support.event, "remove", remove)
    roles = []

    def create_user(actual, **fields):
        assert actual is session
        roles.append(fields["roles"])
        if exit_at_commit:
            monkeypatch.setattr(probe.child.process, "poll", MagicMock(return_value=17))
        target, name, handler = listeners[0]
        assert target is session and name == "before_commit"
        handler(session)  # Model the canonical service's internally owned commit.
        session.commit()
        return MagicMock(id=len(roles), username=fields["username"])

    monkeypatch.setattr(support, "create_user", create_user)
    if exit_at_commit:
        with pytest.raises(ValueError, match=f"^{REFUSAL}$"):
            support.seed_accounts(probe)
        assert roles == [["viewer"]]
        session.commit.assert_not_called()
    else:
        accounts = support.seed_accounts(probe)
        assert roles == [["viewer"], ["operator"], ["admin"]]
        assert len(accounts) == session.commit.call_count == 3
        assert all(account.password not in repr(account) for account in accounts)
    assert len(listeners) == 1
    remove.assert_called_once_with(*listeners[0])
    engine.dispose.assert_called_once()


def bound_request(probe, client, method, path, **kwargs):
    # Only the fixed launched origin. No ambient proxy or redirect follower.
    assert str(client.base_url).rstrip("/") == probe.child.base
    assert path.startswith("/") and not path.startswith("//")
    canary.require_data_probe(probe)
    with client.stream(method, path, **kwargs) as response:
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            assert len(data) <= 1_048_576, "TEST bounded response exceeded"
        verify_attestation(probe.child.attestation, probe.child.binding, nonce=probe.child.nonce,
                           child_pid=probe.child.pid, child_start=probe.child.start,
                           http_nonce=response.headers.get(NONCE_HEADER))
        probe.child.require_live()
        return httpx.Response(response.status_code, headers=response.headers, content=bytes(data))


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_real_cookie_roles_page_access_scope_and_revocation_use_same_clone(prefix):
    support = importlib.import_module(MODULE)
    from tts_erp_v2.accounts.models import User, UserRole, UserSession
    from tts_erp_v2.accounts.pages import BUILTIN_ROLES
    from tts_erp_v2.accounts.passwords import verify_password

    value, binding = current_binding()
    with live_api_server(value) as child:
        probe = canary.seed_and_check_canary(child, prefix=prefix)
        accounts = support.seed_accounts(probe)
        engine = create_engine(value)
        try:
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with httpx.Client(base_url=child.base, timeout=5, trust_env=False, follow_redirects=False) as anonymous:
                key_only = bound_request(probe, anonymous, "GET", prefix + "/v2/roles",
                                         headers={"Authorization": "Bearer " + probe.readonly_key})
                assert key_only.status_code == 403
                api_denied = bound_request(probe, anonymous, "GET", prefix + "/v2/pages/intercept-stats",
                                           headers={"Accept": "application/json"})
                assert api_denied.status_code == 401
                protected = bound_request(probe, anonymous, "GET", prefix + "/v2/pages/intercept-stats",
                                          headers={"Accept": "text/html"})
                assert protected.status_code == 302
                location = httpx.URL(protected.headers["location"])
                assert location.path == prefix + "/v2/auth/login"
            for account in accounts:
                with checked_session(binding, engine, factory) as session:
                    user = session.get(User, account.user_pk)
                    assert user is not None and user.username == account.username
                    assert verify_password(account.password, user.password_hash)
                    assert list(session.scalars(select(UserRole.role_code).where(UserRole.user_id == user.id))) == [account.role_code]
                with httpx.Client(base_url=child.base, timeout=5, trust_env=False, follow_redirects=False) as client:
                    response = bound_request(probe, client, "POST", prefix + "/v2/auth/login",
                                             json={"username": account.username, "password": account.password})
                    assert response.status_code == 200
                    attributes = {part.partition("=")[0].strip().lower(): part.partition("=")[2].strip()
                                  for part in response.headers["set-cookie"].split(";")[1:]}
                    assert {"httponly", "samesite", "path"} <= attributes.keys()
                    assert attributes["samesite"].lower() == "lax"
                    cookies = list(client.cookies.jar)
                    assert len(cookies) == 1
                    assert cookies[0].name == "tts_erp_session" and cookies[0].path == (prefix or "/")
                    raw_cookie = cookies[0].value
                    assert isinstance(raw_cookie, str)
                    me = bound_request(probe, client, "GET", prefix + "/v2/auth/me")
                    assert me.status_code == 200
                    payload = me.json()
                    assert payload["authenticated"] is True and payload["username"] == account.username
                    assert payload["role"] == BUILTIN_ROLES[account.role_code][0]
                    assert {"page:shops", "page:enum-map"}.issubset(payload["pages"]) is (account.role_code != "viewer")
                    for page_id in ("intercept-stats", "shops", "enum-map"):
                        page = bound_request(probe, client, "GET", prefix + "/v2/pages/" + page_id)
                        expected = 403 if account.role_code == "viewer" and page_id != "intercept-stats" else 200
                        assert page.status_code == expected
                    # Without this cookie the readonly key cannot access the
                    # admin role catalog. Admin cookie wins over that Bearer.
                    headers = {"Authorization": "Bearer " + probe.readonly_key}
                    dashboard = bound_request(probe, client, "GET", prefix + "/v2/pages/dashboard", headers=headers)
                    assert dashboard.status_code == 200
                    bootstrap = _bootstrap(dashboard.text)
                    assert bootstrap["rootPath"] == prefix and bootstrap["pageId"] == "dashboard"
                    assert bootstrap["roleCapabilities"] == {
                        "readwrite": account.role_code != "viewer", "admin": account.role_code == "admin",
                    }
                    catalog = bound_request(probe, client, "GET", prefix + "/v2/roles", headers=headers)
                    assert catalog.status_code == (200 if account.role_code == "admin" else 403)
                    if prefix:
                        unprefixed = bound_request(probe, client, "GET", "/v2/auth/me")
                        assert unprefixed.json()["authenticated"] is False
                    with checked_session(binding, engine, factory) as session:
                        user = session.get(User, account.user_pk)
                        assert user is not None and user.last_login_at is not None
                        rows = list(session.scalars(select(UserSession).where(UserSession.user_id == account.user_pk)))
                        assert len(rows) == 1 and rows[0].revoked_at is None
                        assert isinstance(rows[0].token_hash, bytes) and len(rows[0].token_hash) == 32
                    logout = bound_request(probe, client, "POST", prefix + "/v2/auth/logout")
                    assert logout.status_code == 204
                    assert len(list(client.cookies.jar)) == 0
                    client.cookies.set("tts_erp_session", raw_cookie, domain="127.0.0.1", path=prefix or "/")
                    revoked = bound_request(probe, client, "GET", prefix + "/v2/auth/me")
                    assert revoked.json()["authenticated"] is False
                    with checked_session(binding, engine, factory) as session:
                        row = session.scalar(select(UserSession).where(UserSession.user_id == account.user_pk))
                        assert row is not None and row.revoked_at is not None
        finally:
            engine.dispose()
