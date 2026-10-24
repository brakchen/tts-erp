"""Isolated DB data-path verification, never traffic rollout or browser commands."""
from __future__ import annotations

import importlib
import os
import signal
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from scripts import _test_db_connections as authority
from tests.support.frontend_p2_binding import current_binding
from tests.support.frontend_p2_live import VerifiedChild, live_api_server

pytestmark = pytest.mark.domain_frontend
MODULE = "tests.support.frontend_p2_canary"


def fake_child(monkeypatch, support):
    value, binding = current_binding()
    process = MagicMock(pid=987654321)
    process.poll.return_value = None
    child = VerifiedChild(base="http://127.0.0.1:12345", port=12345, pid=987654321, start="7654321",
                          nonce="a" * 64, attestation=Path('/TEST_unused'), binding=binding, process=process)
    monkeypatch.setattr(support, "_process_state", lambda _pid: (os.getpid(), child.start))
    return value, child


def test_checked_session_yields_the_same_identity_checked_session_before_any_seed():
    binding_module = importlib.import_module("tests.support.frontend_p2_binding")
    value, binding = current_binding()
    engine = MagicMock(spec=Engine)
    engine.url = make_url(value)
    session = MagicMock()
    session.__enter__.return_value = session
    session.get_bind.return_value = engine
    session.scalar.return_value = binding.run_db
    factory = MagicMock()
    factory.kw = {"bind": engine}
    factory.return_value = session
    with binding_module.checked_session(binding, engine, factory) as actual:
        assert actual is session
        assert [str(call.args[0]) for call in session.scalar.call_args_list] == ["SELECT current_database()"]
        session.add.assert_not_called()
        session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["old-run", "wrong-base", "live-port", "dead-child", "bad-health"])
def test_unverified_child_cannot_construct_parent_engine_or_seed(monkeypatch, kind):
    support = importlib.import_module(MODULE)
    _value, child = fake_child(monkeypatch, support)
    if kind == "old-run":
        child = replace(child, binding=replace(child.binding, run_id="b" * 64))
    elif kind == "wrong-base":
        child = replace(child, base="http://127.0.0.1:9877")
    elif kind == "live-port":
        child = replace(child, base="http://127.0.0.1:9877", port=9877)
    elif kind == "dead-child":
        monkeypatch.setattr(support, "_process_state", MagicMock(side_effect=ValueError("TEST_dead")))
    engine = MagicMock()
    monkeypatch.setattr(support, "create_engine", engine)
    health = {"auth_mode": "off" if kind == "bad-health" else "enforce"}
    monkeypatch.setattr(support, "_bound_json", MagicMock(return_value=health))
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.seed_and_check_canary(child)
    assert engine.call_count == 0


@pytest.mark.parametrize("fault", ["already-exited", "wrong-handle-pid"])
def test_matching_kernel_identity_does_not_replace_launch_handle_liveness(monkeypatch, fault):
    support = importlib.import_module(MODULE)
    _value, child = fake_child(monkeypatch, support)
    if fault == "already-exited":
        monkeypatch.setattr(child.process, "poll", MagicMock(return_value=17))
    else:
        child.process.pid += 1
    engine, network = MagicMock(), MagicMock(return_value={"auth_mode": "enforce"})
    monkeypatch.setattr(support, "create_engine", engine)
    monkeypatch.setattr(support, "_bound_json", network)
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.seed_and_check_canary(child)
    assert engine.call_count == network.call_count == 0


def test_wrong_actual_parent_session_refuses_before_inserts_or_commit(monkeypatch):
    support = importlib.import_module(MODULE)
    value, child = fake_child(monkeypatch, support)
    monkeypatch.setattr(support, "_bound_json", MagicMock(return_value={"auth_mode": "enforce"}))
    engine = MagicMock(spec=Engine)
    engine.url = make_url(value)
    wrong = MagicMock(spec=Engine)
    wrong.url = make_url(value).set(database="tts_erp_test_OTHER")
    session = MagicMock()
    factory = MagicMock()
    factory.kw = {"bind": wrong}
    factory.return_value = session
    monkeypatch.setattr(support, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(support, "sessionmaker", MagicMock(return_value=factory))
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.seed_and_check_canary(child)
    assert factory.call_count == 0
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("bad", [None, {}, [], [{"id": 1}], [{"id": 1, "enum_type": "TEST_type", "enum_value": "TEST_value", "label_zh": "TEST_wrong", "sort_order": 0}]])
def test_missing_wrong_or_malformed_data_is_not_authenticated_probe_acceptance(bad):
    support = importlib.import_module(MODULE)
    expected = {"id": 1, "enum_type": "TEST_type", "enum_value": "TEST_value", "label_zh": "TEST_label", "sort_order": 0}
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.verify_probe_payload(expected, bad)


@pytest.mark.parametrize("prefix", ["/tts/", "//TEST_wrong", "https://example.invalid", "/TEST_wrong"])
def test_probe_prefix_cannot_select_another_origin_or_scope(monkeypatch, prefix):
    support = importlib.import_module(MODULE)
    _value, child = fake_child(monkeypatch, support)
    network, engine = MagicMock(), MagicMock()
    monkeypatch.setattr(support, "_bound_json", network)
    monkeypatch.setattr(support, "create_engine", engine)
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.seed_and_check_canary(child, prefix=prefix)
    assert network.call_count == engine.call_count == 0


@pytest.mark.parametrize("change", ["bool-id", "bool-sort", "wrong-id", "duplicate"])
def test_canary_fact_identity_is_typed_exact_and_unique(change):
    support = importlib.import_module(MODULE)
    expected = {"id": 1, "enum_type": "TEST_type", "enum_value": "TEST_value", "label_zh": "TEST_label", "sort_order": 0}
    actual = dict(expected)
    if change == "bool-id":
        actual["id"] = True
    elif change == "bool-sort":
        actual["sort_order"] = False
    elif change == "wrong-id":
        actual["id"] = 2
    rows = [actual, actual] if change == "duplicate" else [actual]
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.verify_probe_payload(expected, rows)


def test_missing_probe_after_one_commit_cannot_accept_or_automatically_seed_again(monkeypatch):
    support = importlib.import_module(MODULE)
    value, child = fake_child(monkeypatch, support)
    network = MagicMock(side_effect=[{"auth_mode": "enforce"}, []])
    monkeypatch.setattr(support, "_bound_json", network)
    engine = MagicMock(spec=Engine)
    engine.url = make_url(value)
    session = MagicMock()
    session.__enter__.return_value = session
    session.get_bind.return_value = engine
    session.scalar.return_value = child.binding.run_db
    session.flush.side_effect = lambda: setattr(session.add_all.call_args.args[0][0], "id", 123)
    factory = MagicMock()
    factory.kw = {"bind": engine}
    factory.return_value = session
    monkeypatch.setattr(support, "create_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(support, "sessionmaker", MagicMock(return_value=factory))
    with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
        support.seed_and_check_canary(child)
    assert session.commit.call_count == 1 and session.add_all.call_count == 1
    assert network.call_count == 2
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("stage", ["health", "probe"])
def test_owned_child_exit_during_valid_response_refuses_seed_or_acceptance(monkeypatch, stage):
    support = importlib.import_module(MODULE)
    live = importlib.import_module("tests.support.frontend_p2_live")
    value, _binding = current_binding()
    original_spawn = live.subprocess.Popen
    owned = []

    def capture(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr(live.subprocess, "Popen", capture)
    with live.live_api_server(value) as child:
        process = owned[0]
        assert process.pid == child.pid
        assert child.process is process
        original_connection = support.http.client.HTTPConnection
        seen, closed, zombies = [], [], []
        engine = MagicMock(wraps=support.create_engine)
        monkeypatch.setattr(support, "create_engine", engine)

        class BoundConnection:
            def __init__(self, *args, **kwargs):
                self.connection = original_connection(*args, **kwargs)
                self.path = ""

            def request(self, method, path, **kwargs):
                self.path = path
                seen.append((method, path))
                return self.connection.request(method, path, **kwargs)

            def getresponse(self):
                response = self.connection.getresponse()
                path = self.path

                class BoundResponse:
                    status = response.status

                    def getheader(self, name):
                        return response.getheader(name)

                    def read(self, size):
                        body = response.read(size)
                        is_target = path.endswith("/healthz") if stage == "health" else path.endswith("/enum-map/list")
                        if is_target:
                            assert response.status == 200
                            assert response.getheader(support.NONCE_HEADER) == child.nonce
                            # Signal only this captured test launch. Do not poll/wait:
                            # the regression needs an exited, unreaped PID with its
                            # matching parent/start still present in the kernel.
                            process.send_signal(signal.SIGTERM)
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                # A multithreaded leader can show Z before the whole
                                # process is waitable. WNOWAIT proves complete exit
                                # without stealing the launch owner's eventual reap.
                                event = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                                if event is not None:
                                    assert event.si_pid == process.pid
                                    stat = (Path("/proc") / str(process.pid) / "stat").read_text().rsplit(")", 1)[1].split()
                                    assert stat[0] == "Z"
                                    zombies.append(authority._process_state(process.pid))
                                    break
                                time.sleep(0.01)  # Condition polling, not a guessed exit delay.
                            assert zombies == [(os.getpid(), child.start)], "owned child must be unreaped with the original identity"
                        return body

                return BoundResponse()

            def close(self):
                self.connection.close()
                closed.append(True)

        monkeypatch.setattr(support.http.client, "HTTPConnection", BoundConnection)
        with pytest.raises(ValueError, match="^P2 isolated data verification refused$"):
            support.seed_and_check_canary(child)
        assert zombies == [(os.getpid(), child.start)]
        assert engine.call_count == (0 if stage == "health" else 1)
        assert len(seen) == len(closed) == (1 if stage == "health" else 2)
        assert all(method == "GET" for method, _path in seen)


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_real_authenticated_data_probe_is_same_clone_and_readonly_after_identity(prefix):
    support = importlib.import_module(MODULE)
    binding_module = importlib.import_module("tests.support.frontend_p2_binding")
    from tts_erp_v2.db.models.config import EnumMap
    from tts_erp_v2.db.models.security import ApiKey

    value, binding = current_binding()
    with live_api_server(value) as child:
        probe = support.seed_and_check_canary(child, prefix=prefix)
        assert probe.child.binding == binding and probe.enum_type.startswith("TEST_")
        safe_repr = probe.readonly_key not in repr(probe)
        assert safe_repr, "test authentication material must be excluded from evidence repr"
        engine = create_engine(value)
        try:
            with binding_module.checked_session(binding, engine, sessionmaker(bind=engine)) as session:
                actual = session.get(EnumMap, probe.enum_pk)
                assert actual is not None and actual.label_zh == probe.label_zh
                key = session.scalar(select(ApiKey).where(ApiKey.name == probe.key_name))
                assert key is not None and key.role == "readonly" and key.status == "active"
        finally:
            engine.dispose()
        assert not hasattr(probe, "login") and not hasattr(probe, "write")
