from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
capture = {{
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
