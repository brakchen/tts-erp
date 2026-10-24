from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ISOLATED = REPO_ROOT / "scripts/test_isolated.sh"
BASE_URL = "postgresql://tester@127.0.0.1:5432/tts_erp_test_binding_base"


@pytest.fixture
def script_doubles() -> ModuleType:
    """Reuse maintained CLI doubles; these never contact PostgreSQL/Docker."""
    spec = importlib.util.spec_from_file_location(
        "TEST_binding_script_doubles", REPO_ROOT / "tests/test_test_template_refresh.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("keep_db", [False, True])
def test_wrapper_issues_fresh_binding_after_clone_and_revokes_on_exit(
    tmp_path: Path, script_doubles: ModuleType, keep_db: bool
) -> None:
    tools, trace = script_doubles._fake_tools(tmp_path)
    capture = tmp_path / "TEST_command_capture.json"
    inherited = tmp_path / "TEST_inherited_proof.json"
    inherited.write_text('{"TEST_stale": true}')
    script_doubles._script(
        tools / "bash",
        f'''
if [[ "${{1:-}}" == scripts/test.sh ]]; then
  "{sys.executable}" - <<'CAPTURE'
import json
import os
from pathlib import Path
path = os.environ.get("TTS_ERP_TEST_RUN_PROOF", "")
proof_file = Path(path) if path else None
proof = json.loads(proof_file.read_text()) if proof_file and proof_file.is_file() else None
from scripts._test_db_connections import load_run_binding
verified = load_run_binding(os.environ["TTS_ERP_DB_URL_TEST"])
capture = {{
    "verifiedRun": verified.run_db,
    "runId": os.environ.get("TTS_ERP_TEST_RUN_ID"),
    "proofPath": path,
    "proof": proof,
}}
Path(os.environ["TEST_CAPTURE_PATH"]).write_text(json.dumps(capture))
with Path(os.environ["FAKE_TRACE"]).open("a") as out:
    out.write("TEST_COMMAND_AFTER_CLONE\\n")
CAPTURE
  exit 0
fi
exec /bin/bash "$@"
''',
    )
    env = script_doubles._environment(tools, trace, None)
    env.update(
        {
            "TTS_ERP_DB_URL_TEST": BASE_URL,
            "TTS_ERP_TEST_RUN_PROOF": str(inherited),
            "TTS_ERP_TEST_RUN_ID": "TEST_stale_run_id",
            "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "TEST_template.lock"),
            "TEST_CAPTURE_PATH": str(capture),
        }
    )
    args = [
        "/bin/bash", str(ISOLATED),
        "--template-db", "tts_erp_test_binding_template",
        "--db-name", "tts_erp_test_binding_run",
    ]
    if keep_db:
        args.append("--keep-db")
    result = subprocess.run(
        [*args, "--", "--help"], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(capture.read_text())
    assert observed["runId"] not in {None, "TEST_stale_run_id"}, (
        "The successful clone must replace inherited run identity, not corroborate its URL"
    )
    assert observed["proofPath"] != str(inherited)
    assert observed["verifiedRun"] == "tts_erp_test_binding_run"
    proof = observed["proof"]
    assert isinstance(proof, dict), "Successful clone must publish independent run proof"
    assert proof["version"] == 1 and proof["created"] is True
    assert proof["runId"] == observed["runId"]
    assert proof["runDb"] == "tts_erp_test_binding_run"
    assert proof["baseDb"] == "tts_erp_test_binding_base"
    assert proof["templateDb"] == "tts_erp_test_binding_template"
    assert proof["ownerPid"] > 0 and proof["ownerStart"]
    assert proof["connectionDigest"]
    assert "postgresql" not in json.dumps(proof)
    assert not Path(observed["proofPath"]).exists(), "keep-db must not keep write authority"
    assert json.loads(inherited.read_text()) == {"TEST_stale": True}
    events = trace.read_text()
    assert events.index("CLONE tts_erp_test_binding_run") < events.index("TEST_COMMAND_AFTER_CLONE")
    if keep_db:
        after_clone = events.split("CLONE tts_erp_test_binding_run", 1)[1]
        assert "host dropdb " not in after_clone


@pytest.mark.parametrize("forbidden_run", ["tts_erp_test_template", "tts_erp_v3_test"])
def test_wrapper_rejects_reserved_names_before_clone_even_with_custom_template(
    tmp_path: Path, script_doubles: ModuleType, forbidden_run: str
) -> None:
    tools, trace = script_doubles._fake_tools(tmp_path)
    env = script_doubles._environment(tools, trace, None)
    env.update({
        "TTS_ERP_DB_URL_TEST": BASE_URL,
        "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "TEST_template.lock"),
    })
    result = subprocess.run(
        ["/bin/bash", str(ISOLATED), "--template-db", "tts_erp_test_custom_template",
         "--db-name", forbidden_run, "--", "--help"],
        cwd=REPO_ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=30,
    )
    assert result.returncode != 0, "A reserved shared/template destination is not a run clone"
    assert script_doubles._mutations(trace.read_text() if trace.exists() else "") == []


@pytest.mark.parametrize("fault", ["missing-proof", "another-clone"])
def test_canonical_conftest_refuses_before_even_constructing_probe_engine(fault: str) -> None:
    """Only execute module/probe against a fail-fast fake engine: zero DB access."""
    env = os.environ.copy()
    env["TTS_ERP_TEST_NO_DOTENV"] = "1"
    if fault == "missing-proof":
        env.pop("TTS_ERP_TEST_RUN_PROOF", None)
    else:
        env["TTS_ERP_DB_URL_TEST"] = (
            "postgresql+psycopg://tester@127.0.0.1:5432/tts_erp_test_other_retained"
        )
    program = r'''
import importlib.util
import os
import sys
from pathlib import Path
import sqlalchemy

def forbidden_engine(*args, **kwargs):
    print("TEST_ENGINE_REQUESTED_BEFORE_PROOF")
    raise RuntimeError("TEST fake prevents all database access")

sqlalchemy.create_engine = forbidden_engine
spec = importlib.util.spec_from_file_location("TEST_canonical_conftest", Path("tests/conftest.py"))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
    module._probe_schema_tables(os.environ["TTS_ERP_DB_URL_TEST"])
except SystemExit:
    raise
except Exception:
    raise SystemExit(23) from None
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 2, "Canonical entry must refuse an unbound run before schema probing"
    assert "TEST_ENGINE_REQUESTED_BEFORE_PROOF" not in result.stdout


def test_nonregular_proof_refuses_without_blocking_or_connecting(tmp_path: Path) -> None:
    proof = tmp_path / "TEST_fifo_proof"
    os.mkfifo(proof, 0o600)
    env = os.environ.copy()
    env["TTS_ERP_TEST_RUN_PROOF"] = str(proof)
    program = '''
import os
from scripts._test_db_connections import load_run_binding
try:
    load_run_binding(os.environ["TTS_ERP_DB_URL_TEST"])
except ValueError:
    raise SystemExit(0) from None
raise SystemExit(7)
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=3,
    )
    assert result.returncode == 0


@pytest.mark.parametrize("decoder_mode", ["native", "recursion_failure"])
def test_deeply_nested_proof_has_fixed_refusal(tmp_path: Path, decoder_mode: str) -> None:
    proof = tmp_path / "TEST_deep_proof.json"
    payload = "[" * 4000 + '"TEST_PROOF_PAYLOAD_SENTINEL"' + "]" * 4000
    assert len(payload) < 8192
    proof.write_text(payload)
    proof.chmod(0o600)
    # Check native bounded input separately from the documented dependency
    # failure. Inject only the decoder exception, never a permissive DB guard.
    env = os.environ.copy()
    env["TTS_ERP_TEST_RUN_PROOF"] = str(proof)
    env["TEST_DECODER_FAILURE"] = decoder_mode
    program = '''
import os
import sys
from scripts import _test_db_connections as authority
native_decoder = authority.json.loads

def observed_decoder(raw):
    print("TEST_DECODER_REACHED", sys.getrecursionlimit())
    if os.environ["TEST_DECODER_FAILURE"] == "recursion_failure":
        raise RecursionError("TEST_PROOF_PAYLOAD_SENTINEL")
    return native_decoder(raw)

authority.json.loads = observed_decoder
try:
    authority.load_run_binding(os.environ["TTS_ERP_DB_URL_TEST"])
except ValueError as error:
    assert str(error) == "current isolated run proof is missing or inconsistent"
    raise SystemExit(0) from None
raise SystemExit(7)
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=5,
    )
    assert result.returncode == 0
    assert "TEST_DECODER_REACHED" in result.stdout, result.stderr
    assert "Traceback" not in result.stderr
    assert "TEST_PROOF_PAYLOAD_SENTINEL" not in result.stderr


@pytest.fixture
def binding_sample(tmp_path: Path) -> tuple[ModuleType, dict[str, Any], Path, str]:
    from scripts import _test_db_connections as authority

    payload = json.loads(Path(os.environ["TTS_ERP_TEST_RUN_PROOF"]).read_text())
    path = tmp_path / "TEST_binding.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return authority, payload, path, os.environ["TTS_ERP_DB_URL_TEST"]


@pytest.mark.parametrize("fault", [
    "bad-json", "oversize", "missing-key", "extra-key", "version-bool", "version-two",
    "not-created", "owner-bool", "owner-zero", "owner-type", "stale-start", "digest",
    "nonce", "base-is-run", "template-is-run", "custom-template-is-run", "reserved-run",
    "public-file", "public-directory", "symlink", "missing-file",
])
def test_invalid_proof_is_fixed_refusal_without_any_engine(binding_sample, fault: str) -> None:
    """Pure authority/file checks: no engine or database is constructed."""
    authority, payload, path, value = binding_sample
    if fault == "bad-json":
        path.write_text('{"TEST_secret_sentinel":')
    elif fault == "oversize":
        path.write_text(" " * 8193)
    elif fault == "public-file":
        path.chmod(0o644)
    elif fault == "public-directory":
        path.parent.chmod(0o755)
    elif fault == "symlink":
        target = path.with_name("TEST_original.json")
        path.rename(target)
        path.symlink_to(target)
    elif fault == "missing-file":
        path.unlink()
    else:
        if fault == "missing-key":
            payload.pop("connectionDigest")
        elif fault == "extra-key":
            payload["TEST_extra"] = "TEST_secret_sentinel"
        elif fault == "version-bool":
            payload["version"] = True
        elif fault == "version-two":
            payload["version"] = 2
        elif fault == "not-created":
            payload["created"] = False
        elif fault == "owner-bool":
            payload["ownerPid"] = True
        elif fault == "owner-zero":
            payload["ownerPid"] = 0
        elif fault == "owner-type":
            payload["ownerStart"] = 42
        elif fault == "stale-start":
            payload["ownerStart"] = str(int(payload["ownerStart"]) + 1)
        elif fault == "digest":
            payload["connectionDigest"] = "0" * 64
        elif fault == "nonce":
            payload["runId"] = "0" * 64
        elif fault == "base-is-run":
            payload["runDb"] = payload["baseDb"]
        elif fault == "template-is-run":
            payload["runDb"] = payload["templateDb"]
        elif fault == "custom-template-is-run":
            payload["templateDb"] = payload["runDb"] = "tts_erp_test_custom_template"
        elif fault == "reserved-run":
            payload["runDb"] = "tts_erp_v3_test"
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError) as caught:
        authority.load_run_binding(value, proof_path=path)
    assert str(caught.value) == "current isolated run proof is missing or inconsistent"


@pytest.mark.parametrize("change", [
    "base", "template", "custom-template", "retained", "shared", "production-token",
    "host", "port", "user", "password", "option", "query-dbname",
])
def test_candidate_identity_is_not_its_own_expectation(binding_sample, change: str) -> None:
    """URLs are metadata only; this pure test creates/connects no engine."""
    from sqlalchemy.engine import make_url

    authority, payload, path, value = binding_sample
    url = make_url(value)
    databases = {
        "base": payload["baseDb"], "template": payload["templateDb"],
        "custom-template": "tts_erp_test_custom_template", "retained": "tts_erp_test_retained",
        "shared": "tts_erp_v3_test", "production-token": "tts_erp_prod",
    }
    if change in databases:
        url = url.set(database=databases[change])
    elif change == "host":
        url = url.set(host="127.0.0.2")
    elif change == "port":
        url = url.set(port=5433)
    elif change == "user":
        url = url.set(username="TEST_other_user")
    elif change == "password":
        url = url.set(password="TEST_other_credential")
    elif change == "option":
        url = url.update_query_dict({"application_name": "TEST_other_application"})
    elif change == "query-dbname":
        url = url.update_query_dict({"dbname": payload["runDb"]})
    with pytest.raises(ValueError, match="^current isolated run proof is missing or inconsistent$"):
        authority.load_run_binding(url.render_as_string(hide_password=False), proof_path=path)


@pytest.mark.parametrize("dead", [False, True])
def test_another_live_or_dead_process_cannot_issue_this_run(binding_sample, dead: bool) -> None:
    authority, payload, path, value = binding_sample
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        _, start = authority._process_state(child.pid)
        payload.update(ownerPid=child.pid, ownerStart=start)
        if dead:
            child.communicate(timeout=5)
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            authority.load_run_binding(value, proof_path=path)
    finally:
        if child.poll() is None:
            child.communicate(timeout=5)


@pytest.mark.parametrize("entry", ["probe", "session-fixture", "actual-database"])
def test_actual_engine_and_database_are_checked_at_the_right_boundary(entry: str) -> None:
    """Fail-fast offline engines; only the actual-DB case permits one fake connect."""
    env = os.environ.copy()
    env["TEST_ENGINE_ENTRY"] = entry
    env["TTS_ERP_TEST_NO_DOTENV"] = "1"
    program = r'''
import importlib.util
import os
import sys
from pathlib import Path
import sqlalchemy
from sqlalchemy.engine import make_url

entry = os.environ["TEST_ENGINE_ENTRY"]
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def execute(self, statement):
        if str(statement) == "SELECT current_database()":
            print("TEST_IDENTITY_SELECT")
            return self
        print("TEST_FORBIDDEN_SCHEMA_OR_SEED")
        raise AssertionError("unexpected query")
    def scalar_one(self): return "tts_erp_test_other_database"
class Engine:
    url = make_url(os.environ["TTS_ERP_DB_URL_TEST"])
    if entry != "actual-database": url = url.set(host="127.0.0.2")
    def connect(self):
        print("TEST_CONNECT")
        if entry != "actual-database": raise AssertionError("connection forbidden")
        return Connection()
    def dispose(self): print("TEST_DISPOSED")
engine = Engine()
sqlalchemy.create_engine = lambda *args, **kwargs: engine
spec = importlib.util.spec_from_file_location("TEST_bound_conftest", Path("tests/conftest.py"))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if entry == "session-fixture":
    from tts_erp_v2.db import base
    base.get_engine = lambda *args, **kwargs: engine
    base.reset_for_testing = lambda: None
try:
    if entry == "session-fixture": next(module.db_engine.__wrapped__(os.environ["TTS_ERP_DB_URL_TEST"]))
    else: module._probe_schema_tables(os.environ["TTS_ERP_DB_URL_TEST"])
except ValueError:
    raise SystemExit(0) from None
raise SystemExit(7)
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert result.returncode == 0
    if entry == "actual-database":
        assert result.stdout.count("TEST_CONNECT") == 1
        assert result.stdout.count("TEST_IDENTITY_SELECT") == 1
    else:
        assert "TEST_CONNECT" not in result.stdout
    assert "TEST_FORBIDDEN_SCHEMA_OR_SEED" not in result.stdout


@pytest.mark.parametrize("failure", ["clone", "issuer", "command", "proof-cleanup"])
def test_failure_lifecycle_never_leaves_writable_provenance(
    tmp_path: Path, script_doubles: ModuleType, failure: str
) -> None:
    """Private script/interpreter doubles only; all PG tools remain offline."""
    tools, trace = script_doubles._fake_tools(tmp_path)
    repo = tmp_path / "TEST_repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv/bin").mkdir(parents=True)
    shutil.copyfile(ISOLATED, repo / "scripts/test_isolated.sh")
    script_doubles._script(
        repo / ".venv/bin/python",
        f'''
if [[ "${{3:-}}" == issue-run-proof && "${{TEST_FAIL_ISSUER:-0}}" == 1 ]]; then
  echo TEST_ISSUER_REFUSED >> "$FAKE_TRACE"
  exit 12
fi
exec "{sys.executable}" "$@"
''',
    )
    if failure == "command":
        script_doubles._script(tools / "bash", '''
if [[ "${1:-}" == scripts/test.sh ]]; then
  [[ -f "$TTS_ERP_TEST_RUN_PROOF" ]] || exit 9
  echo TEST_COMMAND_REFUSED >> "$FAKE_TRACE"
  exit 17
fi
exec /bin/bash "$@"
''')
    if failure == "proof-cleanup":
        script_doubles._script(tools / "rm", "exit 19")
    proof_root = tmp_path / "TEST_proofs"
    proof_root.mkdir(mode=0o700)
    env = script_doubles._environment(tools, trace, None)
    env.update({
        "TTS_ERP_DB_URL_TEST": BASE_URL,
        "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "TEST_template.lock"),
        "PYTHONPATH": str(REPO_ROOT), "TMPDIR": str(proof_root),
        "TEST_FAIL_ISSUER": "1" if failure == "issuer" else "0",
        "FAKE_CLONE_FAIL": "1" if failure == "clone" else "0",
    })
    result = subprocess.run(
        ["/bin/bash", str(repo / "scripts/test_isolated.sh"),
         "--template-db", "tts_erp_test_binding_template",
         "--db-name", "tts_erp_test_binding_failure_run", "--", "--help"],
        cwd=repo, env=env, text=True, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == {"clone": 4, "issuer": 12, "command": 17, "proof-cleanup": 1}[failure]
    if failure == "proof-cleanup":
        from scripts import _test_db_connections as authority

        remaining = list(proof_root.glob("*/run.json"))
        assert len(remaining) == 1
        payload = json.loads(remaining[0].read_text())
        candidate = authority._uri(
            authority._parameters(BASE_URL), "tts_erp_test_binding_failure_run", sqlalchemy=True,
        )
        # Even a deliberately unremoved artifact cannot authorize a later run.
        with pytest.raises(ValueError):
            authority.load_run_binding(candidate, proof_path=remaining[0], run_id=payload["runId"])
    else:
        assert list(proof_root.iterdir()) == []
    events = trace.read_text()
    if failure == "clone":
        assert "CLONE " not in events
        assert "TEST_RUN_URL" not in events
    else:
        after = events.split("CLONE tts_erp_test_binding_failure_run", 1)[1]
        assert "host dropdb " in after
    if failure != "command":
        assert "TEST_COMMAND_REFUSED" not in events


def test_actual_test_script_preserves_binding_and_skips_stale_dotenv(tmp_path: Path) -> None:
    """Run unchanged test.sh with a capture-only executable, not another suite."""
    repo = tmp_path / "TEST_real_chain"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv/bin").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "scripts/test.sh", repo / "scripts/test.sh")
    capture = repo / "TEST_capture.json"
    executable = repo / ".venv/bin/pytest"
    executable.write_text(
        f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
from scripts._test_db_connections import load_run_binding
binding = load_run_binding(os.environ["TTS_ERP_DB_URL_TEST"])
result = {
    "sameRun": binding.run_id == os.environ["TEST_EXPECTED_RUN_ID"],
    "sameProof": os.environ["TTS_ERP_TEST_RUN_PROOF"] == os.environ["TEST_EXPECTED_PROOF"],
}
Path(os.environ["TEST_CAPTURE_PATH"]).write_text(json.dumps(result))
'''
    )
    executable.chmod(0o755)
    (repo / ".env.test").write_text(
        "TTS_ERP_DB_URL_TEST=postgresql://tester@127.0.0.1/tts_erp_test_stale\n"
        "TTS_ERP_TEST_RUN_ID=TEST_stale\nTTS_ERP_TEST_RUN_PROOF=TEST_stale\n"
    )
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(REPO_ROOT), "TEST_CAPTURE_PATH": str(capture),
        "TEST_EXPECTED_RUN_ID": env["TTS_ERP_TEST_RUN_ID"],
        "TEST_EXPECTED_PROOF": env["TTS_ERP_TEST_RUN_PROOF"],
    })
    result = subprocess.run(
        ["/bin/bash", str(repo / "scripts/test.sh"), "fast"], cwd=repo, env=env,
        text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0
    assert json.loads(capture.read_text()) == {"sameRun": True, "sameProof": True}
