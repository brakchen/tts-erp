"""Test-only P2 child binding. No seed, login, OAuth or business writes here."""
from __future__ import annotations

import http.client
import importlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine, make_url

from scripts import _test_db_connections as authority

pytestmark = pytest.mark.domain_frontend
BINDING = "tests.support.frontend_p2_binding"
LIVE = "tests.support.frontend_p2_live"
NONCE = "a" * 64
FAKE_PID = 987654321
FAKE_START = "7654321"


@pytest.fixture
def current():
    value = os.environ["TTS_ERP_DB_URL_TEST"]
    return value, authority.load_run_binding(value)


def doubles(value, actual):
    engine = MagicMock(spec=Engine)
    engine.url = make_url(value)
    session = MagicMock()
    session.get_bind.return_value = engine
    session.scalar.return_value = actual
    session.__enter__.return_value = session
    factory = MagicMock()
    factory.kw = {"bind": engine}
    factory.return_value = session
    return engine, factory, session


@pytest.mark.parametrize("change", ["database", "username", "host", "query"])
def test_actual_engine_mismatch_refuses_before_session_or_connect(current, change):
    support = importlib.import_module(BINDING)
    value, binding = current
    engine, factory, session = doubles(value, binding.run_db)
    changes = {"database": "tts_erp_test_OTHER", "username": "TEST_wrong", "host": "127.0.0.2", "query": {"application_name": "TEST_changed"}}
    engine.url = engine.url.set(**{change: changes[change]})
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        support.validate_application(binding, engine, factory)
    factory.assert_not_called()
    engine.connect.assert_not_called()
    session.scalar.assert_not_called()


@pytest.mark.parametrize("where", ["factory", "binds", "session"])
def test_session_route_mismatch_is_rejected_before_connection(current, where):
    support = importlib.import_module(BINDING)
    value, binding = current
    engine, factory, session = doubles(value, binding.run_db)
    wrong = MagicMock(spec=Engine)
    wrong.url = make_url(value).set(database="tts_erp_test_OTHER")
    if where == "factory":
        factory.kw["bind"] = wrong
    elif where == "binds":
        factory.kw["binds"] = {object: wrong}
    else:
        session.get_bind.return_value = wrong
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        support.validate_application(binding, engine, factory)
    engine.connect.assert_not_called()
    wrong.connect.assert_not_called()
    session.scalar.assert_not_called()


def test_actual_database_mismatch_runs_only_identity_sql(current):
    support = importlib.import_module(BINDING)
    value, binding = current
    engine, factory, session = doubles(value, "tts_erp_test_OTHER")
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        support.validate_application(binding, engine, factory)
    assert [str(call.args[0]) for call in session.scalar.call_args_list] == ["SELECT current_database()"]
    session.commit.assert_not_called()


def test_actual_bound_session_can_read_only_the_expected_identity(current):
    support = importlib.import_module(BINDING)
    value, binding = current
    engine, factory, session = doubles(value, binding.run_db)
    assert support.validate_application(binding, engine, factory) == binding.run_db
    assert [str(call.args[0]) for call in session.scalar.call_args_list] == ["SELECT current_database()"]
    session.commit.assert_not_called()


@pytest.fixture
def receipt(tmp_path, current, monkeypatch):
    support = importlib.import_module(BINDING)
    _, binding = current
    parent_start = authority._process_state(os.getpid())[1]
    payload = {"version": 1, "runId": binding.run_id, "launchNonce": NONCE, "childPid": FAKE_PID,
               "childStart": FAKE_START, "parentPid": os.getpid(), "parentStart": parent_start,
               "connectionDigest": binding.connection_digest, "actualDb": binding.run_db}
    path = tmp_path / "TEST_attestation.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    tmp_path.chmod(0o700)
    real_state = authority._process_state
    monkeypatch.setattr(support, "_process_state", lambda pid: (os.getpid(), FAKE_START) if pid == FAKE_PID else real_state(pid))
    return support, binding, path, payload


def verify(receipt, http_nonce=NONCE):
    support, binding, path, _ = receipt
    return support.verify_attestation(path, binding, nonce=NONCE, child_pid=FAKE_PID, child_start=FAKE_START, http_nonce=http_nonce)


def test_attestation_and_http_nonce_bind_to_the_independent_live_child(receipt):
    result = verify(receipt)
    assert result.child_pid == FAKE_PID
    assert result.actual_db == receipt[1].run_db


@pytest.mark.parametrize("field,value", [("version", True), ("runId", "b" * 64), ("launchNonce", "c" * 64), ("childPid", FAKE_PID + 1), ("childStart", "1"), ("parentPid", 1), ("parentStart", "1"), ("connectionDigest", "d" * 64), ("actualDb", "tts_erp_test_OTHER"), ("TEST_extra", "TEST_raw_secret")])
def test_old_run_launch_wrong_process_digest_or_database_is_not_readiness(receipt, field, value):
    _, _, path, payload = receipt
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        verify(receipt)


@pytest.mark.parametrize("kind", ["mode", "directory", "symlink", "fifo", "oversize", "missing"])
def test_private_bounded_nonblocking_attestation_read(receipt, kind, monkeypatch):
    support, _, path, _ = receipt
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "directory":
        path.parent.chmod(0o755)
    elif kind == "oversize":
        path.write_bytes(b" " * 8193)
    else:
        path.unlink()
        if kind == "symlink":
            path.symlink_to(path.parent / "TEST_missing")
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
            original_open = os.open

            def bounded_open(filename, flags, *args, **kwargs):
                assert flags & os.O_NONBLOCK, "FIFO open must be nonblocking before attempting it"
                return original_open(filename, flags, *args, **kwargs)

            monkeypatch.setattr(support.os, "open", bounded_open)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        verify(receipt)


def test_wrong_http_nonce_and_reused_pid_start_are_not_readiness(receipt, monkeypatch):
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        verify(receipt, "b" * 64)
    support = receipt[0]
    monkeypatch.setattr(support, "_process_state", lambda _pid: (os.getpid(), "999"))
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        verify(receipt)


def test_attestation_issuer_is_private_exclusive_and_credential_free(tmp_path, current):
    support = importlib.import_module(BINDING)
    value, binding = current
    path = tmp_path / "TEST_issued.json"
    tmp_path.chmod(0o700)
    support.write_attestation(path, binding, NONCE)
    text = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(text)) == {"version", "runId", "launchNonce", "childPid", "childStart", "parentPid", "parentStart", "connectionDigest", "actualDb"}
    credential_free = value not in text and "password" not in text and "postgresql" not in text
    assert credential_free, "attestation must contain no connection configuration"
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        support.write_attestation(path, binding, NONCE)


def test_child_environment_overwrites_stale_launch_and_contains_no_ambient_secrets(tmp_path, current, monkeypatch):
    live = importlib.import_module(LIVE)
    value, binding = current
    monkeypatch.setenv("TIKTOK_APP_SECRET", "TEST_do_not_inherit")
    monkeypatch.setenv("ALLOW_PROD_DESTRUCTIVE", "TEST_do_not_inherit")
    monkeypatch.setenv("TTS_ERP_TEST_P2_LAUNCH_NONCE", "b" * 64)
    tmp_path.chmod(0o700)
    env = live.child_environment(value, nonce=NONCE, attestation=tmp_path / "TEST_fresh.json")
    assert "TIKTOK_APP_SECRET" not in env and "ALLOW_PROD_DESTRUCTIVE" not in env
    same_destination = env["TTS_ERP_DB_URL"] == env["TTS_ERP_DB_URL_TEST"] == value
    assert same_destination, "both child database variables must match the validated current run"
    assert env["TTS_ERP_TEST_RUN_ID"] == binding.run_id
    assert env["TTS_ERP_TEST_RUN_PROOF"] == os.environ["TTS_ERP_TEST_RUN_PROOF"]
    assert env["TTS_ERP_TEST_P2_LAUNCH_NONCE"] == NONCE
    assert env["TTS_ERP_AUTH_MODE"] == "enforce" and env["TTS_ERP_TEST_NO_DOTENV"] == "1"


def test_bad_factory_launch_refuses_before_engine_or_app_construction(monkeypatch):
    live = importlib.import_module(LIVE)
    from tts_erp_v2.db import base

    monkeypatch.delenv("TTS_ERP_TEST_P2_LAUNCH_NONCE", raising=False)
    get_engine = MagicMock()
    monkeypatch.setattr(base, "get_engine", get_engine)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    get_engine.assert_not_called()


def test_revoked_run_proof_cannot_reuse_a_previously_valid_receipt(receipt, monkeypatch):
    monkeypatch.setenv("TTS_ERP_TEST_RUN_PROOF", str(receipt[2].parent / "TEST_revoked"))
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        verify(receipt)


@pytest.mark.parametrize("field,value", [("TTS_ERP_DB_URL", "postgresql+psycopg://TEST_never_connect@127.0.0.1/tts_erp_prod"), ("TTS_ERP_AUTH_MODE", "off"), ("TTS_ERP_TEST_NO_DOTENV", "0"), ("TTS_ERP_TEST_P2_PARENT_PID", "1"), ("TTS_ERP_TEST_P2_PARENT_START", "1"), ("TTS_ERP_FERNET_KEY", "TEST_foreign_key")])
def test_factory_environment_mismatch_fails_before_engine(tmp_path, current, monkeypatch, field, value):
    live = importlib.import_module(LIVE)
    from tests.support.spu_price_stats_live import TEST_FERNET_KEY
    from tts_erp_v2.db import base

    url, _ = current
    tmp_path.chmod(0o700)
    for key, text in {"TTS_ERP_DB_URL": url, live.LAUNCH: NONCE, live.ATTESTATION: str(tmp_path / "TEST_new.json"), live.PARENT_PID: str(os.getppid()), live.PARENT_START: authority._process_state(os.getppid())[1], "TTS_ERP_AUTH_MODE": "enforce", "TTS_ERP_TEST_NO_DOTENV": "1", "TTS_ERP_FERNET_KEY": TEST_FERNET_KEY}.items():
        monkeypatch.setenv(key, text)
    monkeypatch.setenv(field, value)
    get_engine = MagicMock()
    monkeypatch.setattr(base, "get_engine", get_engine)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.create_app()
    get_engine.assert_not_called()


def test_ambient_pg_routing_is_refused_not_silently_sanitized(tmp_path, current, monkeypatch):
    live = importlib.import_module(LIVE)
    monkeypatch.setenv("PGDATABASE", "tts_erp_prod")
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live.child_environment(current[0], nonce=NONCE, attestation=tmp_path / "TEST_unused")


@pytest.mark.parametrize("body", [b"[]", b"{TEST_raw_secret", b'{"auth_mode":"off"}', b"[" * 1100 + b"]" * 1100])
def test_invalid_readiness_is_a_fixed_refusal_not_a_body_or_parser_error(receipt, monkeypatch, body):
    live = importlib.import_module(LIVE)
    _support, binding, path, _ = receipt
    proc = MagicMock(pid=FAKE_PID)
    proc.poll.return_value = None
    child = live.VerifiedChild(base="http://127.0.0.1:12345", port=12345, pid=FAKE_PID, start=FAKE_START, nonce=NONCE, attestation=path, binding=binding, process=proc)
    response = MagicMock()
    response.status = 200
    response.read.return_value = body
    response.getheader.return_value = NONCE
    connection = MagicMock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(live.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live._wait_ready(child, timeout=0.1)
    connection.close.assert_called_once()


def test_decoder_recursion_exception_is_normalized_at_the_owner(receipt, monkeypatch):
    live = importlib.import_module(LIVE)
    _support, binding, path, _ = receipt
    proc = MagicMock(pid=FAKE_PID)
    proc.poll.return_value = None
    child = live.VerifiedChild(base="http://127.0.0.1:12345", port=12345, pid=FAKE_PID, start=FAKE_START, nonce=NONCE, attestation=path, binding=binding, process=proc)
    response = MagicMock()
    response.status = 200
    response.read.return_value = b"[]"
    connection = MagicMock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(live.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    decoder = MagicMock()
    decoder.loads.side_effect = RecursionError("TEST_decoder_depth")
    monkeypatch.setattr(live, "json", decoder)
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live._wait_ready(child, timeout=0.1)
    connection.close.assert_called_once()


def test_child_exit_during_readiness_response_is_not_a_live_yield(receipt, monkeypatch):
    live = importlib.import_module(LIVE)
    _support, binding, path, _ = receipt
    proc = MagicMock(pid=FAKE_PID)
    child = live.VerifiedChild(base="http://127.0.0.1:12345", port=12345, pid=FAKE_PID, start=FAKE_START, nonce=NONCE, attestation=path, binding=binding, process=proc)
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"auth_mode":"enforce"}'
    response.getheader.return_value = NONCE
    connection = MagicMock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(live.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    proc.poll.side_effect = [None, 17]
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"):
        live._wait_ready(child, timeout=0.1)
    connection.close.assert_called_once()


def test_missing_run_proof_never_starts_an_api_process(current, monkeypatch):
    live = importlib.import_module(LIVE)
    spawn = MagicMock()
    monkeypatch.setattr(live.subprocess, "Popen", spawn)
    monkeypatch.delenv("TTS_ERP_TEST_RUN_PROOF")
    with pytest.raises(ValueError), live.live_api_server(current[0]):
        pytest.fail("missing proof must never yield an API")
    assert spawn.call_count == 0


def test_failed_readiness_stops_the_owned_group_and_never_yields(current, monkeypatch):
    live = importlib.import_module(LIVE)
    from tests.support import spu_price_stats_live as maintained

    proc = MagicMock()
    proc.pid = FAKE_PID
    spawn = MagicMock(return_value=proc)
    stop = MagicMock()
    monkeypatch.setattr(live.subprocess, "Popen", spawn)
    monkeypatch.setattr(maintained, "_stop_process_group", stop)
    monkeypatch.setattr(live, "_process_state", lambda _pid: (os.getpid(), FAKE_START))
    monkeypatch.setattr(live, "_wait_ready", MagicMock(side_effect=ValueError(live.REFUSAL)))
    with pytest.raises(ValueError, match="^P2 isolated child identity refused$"), live.live_api_server(current[0]):
        pytest.fail("failed readiness must never yield an API")
    assert stop.call_count == 1 and stop.call_args.args == (proc,)
    evidence_path = Path(spawn.call_args.kwargs["env"][live.ATTESTATION])
    assert not evidence_path.parent.exists()


def test_test_only_asgi_wrapper_owns_one_response_nonce_without_new_routes():
    live = importlib.import_module(LIVE)
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def endpoint(_request):
        return PlainTextResponse("TEST_response", headers={live.NONCE_HEADER: "b" * 64})

    with TestClient(live.NonceApp(Starlette(routes=[Route("/TEST", endpoint)]), NONCE)) as client:
        response = client.get("/TEST")
        assert response.text == "TEST_response"
        assert response.headers.get_list(live.NONCE_HEADER) == [NONCE]


def test_real_current_clone_child_health_both_prefixes_and_fresh_launch_without_seed(current):
    live = importlib.import_module(LIVE)
    support = importlib.import_module(BINDING)
    value, binding = current
    with live.live_api_server(value) as child:
        first_nonce, first_path, pid = child.nonce, child.attestation, child.pid
        assert child.binding == binding and child.pid != os.getpid()
        for prefix in ("", "/tts"):
            connection = http.client.HTTPConnection("127.0.0.1", child.port, timeout=5)
            try:
                connection.request("GET", prefix + "/healthz")
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["auth_mode"] == "enforce"
                receipt = support.verify_attestation(child.attestation, binding, nonce=child.nonce, child_pid=child.pid, child_start=child.start, http_nonce=response.getheader(support.NONCE_HEADER))
                assert receipt.actual_db == binding.run_db
            finally:
                connection.close()
    assert not first_path.exists()
    with pytest.raises(ValueError):
        authority._process_state(pid)
    with live.live_api_server(value) as second:
        assert second.nonce != first_nonce and second.attestation != first_path
        assert second.binding == binding
