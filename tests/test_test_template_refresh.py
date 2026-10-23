from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORTER = REPO_ROOT / "scripts/import_prod_to_test.sh"
ISOLATED = REPO_ROOT / "scripts/test_isolated.sh"
SOURCE_URL = "postgresql://tester@127.0.0.1:5432/source_db"
TARGET_URL = "postgresql://tester@127.0.0.1:5432/tts_erp_test_template"
_UNSET = object()


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")
    path.chmod(0o755)


def _fake_tools(
    tmp_path: Path,
    *,
    host_major: int = 18,
    server_major: int = 18,
    prod_revision: str = "",
) -> tuple[Path, Path]:
    """All PostgreSQL/Docker commands are doubles; no database is contacted."""
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "trace.log"
    program = tools / "TEST_pg_tool.py"
    program.write_text(
        f"#!{sys.executable}\n"
        + r'''
import os
import sys
from pathlib import Path

args = sys.argv[1:]
name = Path(sys.argv[0]).name
trace = Path(os.environ["FAKE_TRACE"])
def record(value):
    with trace.open("a") as f:
        f.write(value + "\n")
record(("docker " if name == "docker" else "host " + name + " ") + " ".join(args))
major = __HOST_MAJOR__
transport = "host"
if name == "docker":
    transport = "docker"
    major = 18
    if args[:1] == ["--host"]:
        args = args[2:]
    if args[:1] == ["context"]:
        print(os.environ.get("FAKE_DOCKER_ENDPOINT", "unix:///var/run/docker.sock"))
        sys.exit(0)
    if args[:1] == ["port"]:
        print("0.0.0.0:" + os.environ.get("FAKE_DOCKER_PORT", "5432"))
        sys.exit(0)
    if args[:1] != ["exec"]:
        sys.exit(2)
    args = args[1:]
    if args[:1] == ["-i"]:
        args = args[1:]
    args = args[1:]  # one quoted container identity
    name, args = args[0], args[1:]
if args[:1] == ["--version"]:
    print(f"{name} (PostgreSQL) {major}.6")
    sys.exit(0)
fresh = trace.parent / "TEST_fresh_target"
if name == "pg_dump":
    if os.environ.get("FAKE_DUMP_FAIL") == "1":
        sys.exit(4)
    print("-- TEST_complete_schema_snapshot")
    record("DUMP_COMPLETE")
    sys.exit(0)
if name == "psql":
    command = " ".join(args)
    if "SHOW server_version_num" in command:
        print("__SERVER_MAJOR__0006")
    elif "table_schema || '.' || table_name" in command:
        print("commerce.shops\nsecurity.api_keys")
    elif "SELECT 1 FROM pg_database" in command:
        if os.environ.get("FAKE_TEMPLATE_MISSING") != "1":
            print("1")
    elif "SELECT version_num" in command:
        print(os.environ.get("FAKE_PROD_REVISION", ""))
    elif "--command" not in args:
        record(sys.stdin.read())
        if os.environ.get("FAKE_NEEDS_FRESH") == "1" and not fresh.exists():
            print("TEST target-only constraint on an existing table blocks restore", file=sys.stderr)
            sys.exit(4)
        if os.environ.get("FAKE_RESTORE_FAIL") == "1":
            sys.exit(4)
        record(transport + " psql restored stdin")
    sys.exit(0)
if name in {"createdb", "dropdb"}:
    database = args[-1]
    reset = "template0" in args or "--template=template0" in args
    if name == "dropdb" and database == "tts_erp_test_template" and os.environ.get("FAKE_DROP_FAIL") == "1":
        sys.exit(4)
    if name == "dropdb" and database == "tts_erp_test_refresh_run" and os.environ.get("FAKE_RUN_DROP_FAIL") == "1" and "CLONE " in trace.read_text():
        sys.exit(4)
    if name == "createdb" and reset:
        if os.environ.get("FAKE_CREATE_FAIL") == "1":
            sys.exit(4)
        fresh.write_text("TEST_new_database")
    elif name == "createdb":
        record("CLONE " + database)
    sys.exit(0)
sys.exit(2)
'''.replace("__HOST_MAJOR__", str(host_major)).replace("__SERVER_MAJOR__", str(server_major))
    )
    program.chmod(0o755)
    for name in ("pg_dump", "psql", "createdb", "dropdb", "docker"):
        (tools / name).symlink_to(program.name)
    # Observe the nested wrapper's test destination, without running another suite.
    _script(
        tools / "bash",
        '''
if [[ "${1:-}" == scripts/test.sh ]]; then
  printf 'TEST_RUN_URL %s\n' "$TTS_ERP_DB_URL_TEST" >> "$FAKE_TRACE"
  exit 0
fi
exec /bin/bash "$@"
''',
    )
    return tools, trace


def _environment(tools: Path, trace: Path, pg_docker: str | None | object) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "TTS_ERP_DB_URL": SOURCE_URL,
        "TTS_ERP_DB_URL_TEST": TARGET_URL,
        "FAKE_TRACE": str(trace),
        "PATH": f"{tools}:{env['PATH']}",
    })
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_CONTEXT", None)
    for key in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGTARGETSESSIONATTRS", "PGLOADBALANCEHOSTS"):
        env.pop(key, None)
    if pg_docker is _UNSET:
        env.pop("PG_DOCKER", None)
    else:
        env["PG_DOCKER"] = "" if pg_docker is None else str(pg_docker)
    return env


def _run_import(
    tmp_path: Path,
    *,
    pg_docker: str | None | object = _UNSET,
    host_major: int = 18,
    server_major: int = 18,
    prod_revision: str = "",
    args: tuple[str, ...] = ("--schema-only", "--yes"),
    settings: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    tools, trace = _fake_tools(tmp_path, host_major=host_major, server_major=server_major)
    env = _environment(tools, trace, pg_docker)
    if prod_revision:
        env["FAKE_PROD_REVISION"] = prod_revision
    env.update(settings or {})
    return subprocess.run(
        ["bash", str(IMPORTER), *args], cwd=REPO_ROOT, env=env,
        text=True, capture_output=True, check=False, timeout=30,
    )


def _run_refresh(
    tmp_path: Path,
    *,
    pg_docker: str | None | object = _UNSET,
    target: str = TARGET_URL,
    settings: dict[str, str] | None = None,
    seed: bool = False,
) -> subprocess.CompletedProcess[str]:
    tools, trace = _fake_tools(tmp_path)
    env = _environment(tools, trace, pg_docker)
    env.update({
        "TTS_ERP_DB_URL_PROD_SOURCE": SOURCE_URL,
        "TTS_ERP_DB_URL_TEST": target,
        "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "template.lock"),
    })
    if seed:
        _fake_alembic_and_python(tools, trace)
        env.update({
            "FAKE_PROD_REVISION": "0052_user_accounts",
            "TTS_ERP_TEST_ALEMBIC_BIN": str(tools / "alembic"),
            "TTS_ERP_TEST_PYTHON_BIN": str(tools / "python"),
        })
    env.update(settings or {})
    return subprocess.run(
        ["bash", str(ISOLATED), "--refresh-template", "--template-db", "tts_erp_test_template",
         "--db-name", "tts_erp_test_refresh_run", "--", "--help"],
        cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False, timeout=30,
    )


def _trace(tmp_path: Path) -> str:
    path = tmp_path / "trace.log"
    return path.read_text() if path.exists() else ""


def _mutations(trace: str) -> list[str]:
    return [line for line in trace.splitlines()
            if (" dropdb " in line or " createdb " in line) and "--version" not in line]


def test_unset_pg_docker_defaults_to_configured_container(tmp_path: Path) -> None:
    result = _run_import(tmp_path, host_major=16, server_major=18)
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "docker exec postgres pg_dump" in trace
    assert "docker exec -i postgres psql" in trace
    assert "host pg_dump" not in trace and "host psql" not in trace


def test_host_client_server_mismatch_fails_before_target_mutation(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker=None, host_major=16, server_major=18)
    assert result.returncode != 0
    assert "major" in result.stderr.lower()
    assert "DROP SCHEMA" not in _trace(tmp_path) and "restored stdin" not in _trace(tmp_path)


def test_configured_container_wins_over_available_host_clients(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker="postgres 18", host_major=16, server_major=18)
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "docker exec postgres 18 pg_dump" in trace
    assert "docker exec -i postgres 18 psql" in trace
    assert "host pg_dump" not in trace and "host psql" not in trace


def test_explicit_empty_pg_docker_selects_host_clients(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker=None)
    assert result.returncode == 0, result.stderr
    assert "host pg_dump" in _trace(tmp_path) and "host psql" in _trace(tmp_path)
    assert "docker " not in _trace(tmp_path)


def test_schema_only_restores_schema_without_data_dumps(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker="postgres")
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "--schema-only" in trace and "--table=" not in trace
    assert "docker psql restored stdin" in trace


def test_existing_template_is_not_dropped_before_refresh_preflight(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path)
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    template_drops = [line for line in _mutations(trace) if line.endswith(" tts_erp_test_template") and " dropdb " in line]
    assert template_drops, trace
    assert trace.index("DUMP_COMPLETE") < trace.index(template_drops[0])
    assert "--force tts_erp_test_template" not in trace


def _fake_alembic_and_python(tools: Path, trace: Path) -> None:
    _script(tools / "alembic", "printf 'fake-alembic %s\\n' \"$*\" >> \"$FAKE_TRACE\"")
    _script(tools / "python", "printf 'fake-python %s\\n' \"$*\" >> \"$FAKE_TRACE\"")


def test_refresh_template_seeds_builtin_roles_after_alembic_upgrade(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, seed=True)
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "fake-alembic stamp 0052_user_accounts" in trace
    assert "fake-alembic upgrade head" in trace
    assert "fake-python -m tts_erp_v2.accounts.cli sync-permissions" in trace
    assert trace.index("fake-alembic upgrade head") < trace.index("fake-python -m tts_erp_v2.accounts.cli sync-permissions")


@pytest.mark.parametrize("pg_docker", [None, "postgres"])
def test_existing_table_extra_constraint_is_removed_by_fresh_template(tmp_path: Path, pg_docker) -> None:
    result = _run_refresh(tmp_path, pg_docker=pg_docker, settings={"FAKE_NEEDS_FRESH": "1"})
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "template0" in trace and "CLONE tts_erp_test_refresh_run" in trace
    assert "DROP TABLE" not in trace and "ALTER TABLE" not in trace
    assert "source_db" not in "\n".join(_mutations(trace))


def test_query_to_other_test_database_never_becomes_lifecycle_destination(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, target=TARGET_URL + "?dbname=tts_erp_test_shared")
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "tts_erp_test_shared" not in trace
    url = next(line.removeprefix("TEST_RUN_URL ") for line in trace.splitlines() if line.startswith("TEST_RUN_URL "))
    parsed = urlparse(url)
    assert parsed.path == "/tts_erp_test_refresh_run"
    assert "dbname" not in parse_qs(parsed.query)


def test_query_to_production_is_rejected_before_lifecycle_actions(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, target=TARGET_URL + "?dbname=tts_erp")
    assert result.returncode != 0
    trace = _trace(tmp_path)
    assert not _mutations(trace) and "restored stdin" not in trace and "TEST_RUN_URL" not in trace


@pytest.mark.parametrize("settings", [
    {"FAKE_DOCKER_PORT": "6543"},
    {"FAKE_DOCKER_ENDPOINT": "ssh://TEST_remote"},
    {"DOCKER_HOST": "tcp://TEST_remote:2375"},
    {"DOCKER_CONTEXT": "TEST_remote"},
])
def test_unverified_docker_route_cannot_mutate_or_invoke_tests(tmp_path: Path, settings) -> None:
    result = _run_refresh(tmp_path, settings=settings)
    assert result.returncode != 0
    trace = _trace(tmp_path)
    assert not _mutations(trace) and "restored stdin" not in trace and "TEST_RUN_URL" not in trace


@pytest.mark.parametrize("key,value", [
    ("PGHOSTADDR", "192.0.2.1"), ("PGSERVICE", "TEST_remote"),
    ("PGSERVICEFILE", "/TEST_services"), ("PGUSER", "TEST_other"),
    ("PGPORT", "6543"), ("PGDATABASE", "tts_erp_test_shared"),
    ("PGOPTIONS", "-c search_path=TEST_schema"),
])
def test_ambient_libpq_route_is_rejected_before_any_lifecycle(tmp_path: Path, key, value) -> None:
    result = _run_refresh(tmp_path, settings={key: value})
    assert result.returncode != 0
    trace = _trace(tmp_path)
    assert not _mutations(trace) and "restored stdin" not in trace and "TEST_RUN_URL" not in trace


def test_lifecycle_requires_explicit_user_identity(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, target="postgresql://127.0.0.1:5432/tts_erp_test_base")
    assert result.returncode != 0
    assert not _mutations(_trace(tmp_path))


def test_host_lifecycle_uses_same_nondefault_routing_as_test_uri(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, pg_docker=None, target="postgresql://tester@TEST_host:6543/tts_erp_test_base")
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    for line in _mutations(trace):
        assert "TEST_host:6543" in line, line
    url = next(line.removeprefix("TEST_RUN_URL ") for line in trace.splitlines() if line.startswith("TEST_RUN_URL "))
    assert urlparse(url).hostname == "test_host" and urlparse(url).port == 6543


def test_dump_failure_preserves_existing_template(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, settings={"FAKE_DUMP_FAIL": "1"})
    assert result.returncode != 0
    assert not [line for line in _mutations(_trace(tmp_path)) if line.endswith(" tts_erp_test_template")]
    assert "CLONE" not in _trace(tmp_path) and "TEST_RUN_URL" not in _trace(tmp_path)


@pytest.mark.parametrize("setting", ["FAKE_DROP_FAIL", "FAKE_CREATE_FAIL", "FAKE_RESTORE_FAIL"])
def test_rebuild_failure_cannot_continue_to_clone(tmp_path: Path, setting: str) -> None:
    result = _run_refresh(tmp_path, settings={setting: "1"})
    assert result.returncode != 0
    assert "CLONE" not in _trace(tmp_path) and "TEST_RUN_URL" not in _trace(tmp_path)


def test_missing_template_is_created_only_after_complete_dump(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, settings={"FAKE_TEMPLATE_MISSING": "1", "FAKE_NEEDS_FRESH": "1"})
    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    creates = [line for line in _mutations(trace) if " createdb " in line]
    assert creates and trace.index("DUMP_COMPLETE") < trace.index(creates[0])


@pytest.mark.parametrize("args,target", [
    (("--recreate-test-db", "--yes"), TARGET_URL),
    (("--recreate-test-db", "--schema-only"), TARGET_URL),
    (("--recreate-test-db", "--schema-only", "--yes"), "postgresql://tester@127.0.0.1:5432/tts_erp"),
    (("--recreate-test-db", "--schema-only", "--yes"), TARGET_URL + "?dbname=tts_erp_test_shared"),
])
def test_explicit_recreation_mode_rejects_missing_consent_or_ambiguous_target(tmp_path: Path, args, target) -> None:
    result = _run_import(tmp_path, args=args, settings={"TTS_ERP_DB_URL_TEST": target})
    assert result.returncode != 0
    assert not _mutations(_trace(tmp_path)) and "restored stdin" not in _trace(tmp_path)


def test_failed_run_cleanup_cannot_report_success(tmp_path: Path) -> None:
    result = _run_refresh(tmp_path, settings={"FAKE_RUN_DROP_FAIL": "1"})
    assert "TEST_RUN_URL" in _trace(tmp_path)
    assert result.returncode != 0


def test_recreation_guard_is_bound_to_target_and_refusal_propagates(monkeypatch) -> None:
    from scripts._test_db_connections import recreation
    from tts_erp_v2.api import deps

    seen = {}
    def refuse(**kwargs):
        seen.update(kwargs)
        seen["database"] = urlparse(os.environ["TTS_ERP_DB_URL"]).path
        raise SystemExit(2)
    monkeypatch.setattr(deps, "require_destructive_script_guard", refuse)
    with pytest.raises(SystemExit):
        recreation(SOURCE_URL, TARGET_URL, "")
    assert seen == {"script_name": "recreate-test-db", "confirmation": True, "dangerous": True, "database": "/tts_erp_test_template"}


def test_recreation_parser_error_does_not_echo_secret(tmp_path: Path) -> None:
    secret = "TEST_not_a_real_password"
    target = f"postgresql://tester:{secret}@127.0.0.1:5432/tts_erp_test_template?TEST_invalid=1"
    result = _run_import(tmp_path, args=("--schema-only", "--yes", "--recreate-test-db"), settings={"TTS_ERP_DB_URL_TEST": target})
    assert result.returncode != 0 and secret not in result.stderr + result.stdout
    assert not _mutations(_trace(tmp_path))


def test_recreation_refuses_effectively_identical_source_and_target(tmp_path: Path) -> None:
    result = _run_import(tmp_path, args=("--schema-only", "--yes", "--recreate-test-db"), settings={"TTS_ERP_DB_URL": SOURCE_URL + "?dbname=tts_erp_test_template"})
    assert result.returncode != 0 and not _mutations(_trace(tmp_path))


def test_wrapper_help_does_not_require_installed_project_python(tmp_path: Path) -> None:
    scripts = tmp_path / "TEST_checkout" / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "test_isolated.sh"
    wrapper.write_text(ISOLATED.read_text())
    result = subprocess.run(["/bin/bash", str(wrapper), "--help"], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("unexpected", [None, 5432])
def test_connection_parameter_types_fail_closed(monkeypatch, unexpected) -> None:
    from scripts import _test_db_connections as connections

    monkeypatch.setattr(connections, "conninfo_to_dict", lambda *args, **kwargs: {
        "host": "127.0.0.1", "port": "5432", "dbname": "tts_erp_test_template", "password": unexpected,
    })
    with pytest.raises(TypeError, match="unsupported PostgreSQL parameter type"):
        connections._parameters(TARGET_URL)


def test_uri_library_error_is_redacted(monkeypatch) -> None:
    from sqlalchemy.exc import ArgumentError

    from scripts import _test_db_connections as connections

    def broken_constructor(*args, **kwargs):
        raise ArgumentError("TEST_constructor_secret")
    monkeypatch.setattr(connections.URL, "create", broken_constructor)
    with pytest.raises(ValueError) as error:
        connections._uri({"host": "127.0.0.1", "port": "5432"}, "tts_erp_test_template")
    assert "TEST_constructor_secret" not in str(error.value)


def test_recreation_dry_run_does_not_replace_target(tmp_path: Path) -> None:
    result = _run_import(tmp_path, args=("--schema-only", "--yes", "--recreate-test-db", "--dry-run"))
    assert result.returncode == 0, result.stderr
    assert not _mutations(_trace(tmp_path)) and "restored stdin" not in _trace(tmp_path)
