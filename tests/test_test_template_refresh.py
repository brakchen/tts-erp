from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORTER = REPO_ROOT / "scripts/import_prod_to_test.sh"
ISOLATED = REPO_ROOT / "scripts/test_isolated.sh"


SOURCE_URL = "postgresql://tester@db/source_db"
TARGET_URL = "postgresql://tester@db/tts_erp_test_template"
_UNSET = object()


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n")
    path.chmod(0o755)


def _fake_tools(
    tmp_path: Path,
    *,
    host_major: int = 18,
    server_major: int = 18,
    prod_revision: str = "",
) -> tuple[Path, Path]:
    """Create host and container PostgreSQL command doubles with trace logging.

    When ``prod_revision`` is set, the fake ``psql`` returns it for the
    ``SELECT version_num FROM alembic_version`` query so ``refresh_template``
    takes the ``alembic stamp`` / ``alembic upgrade head`` branch.
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "trace.log"
    host_dump = tools / "pg_dump"
    host_psql = tools / "psql"
    _script(
        host_dump,
        f"""
        printf 'host pg_dump %s\\n' "$*" >> "$FAKE_TRACE"
        if [[ "${{1:-}}" == "--version" ]]; then
          printf 'pg_dump (PostgreSQL) {host_major}.15\\n'
          exit 0
        fi
        printf -- '-- fake schema dump --\\n'
        """,
    )
    _script(
        host_psql,
        f"""
        printf 'host psql %s\\n' "$*" >> "$FAKE_TRACE"
        if [[ "${{1:-}}" == "--version" ]]; then
          printf 'psql (PostgreSQL) {host_major}.15\\n'
          exit 0
        fi
        command_line="$*"
        if [[ "$command_line" == *"SHOW server_version_num"* ]]; then
          printf '{server_major}0006\\n'
        elif [[ "$command_line" == *"table_schema || '.' || table_name"* ]]; then
          printf 'commerce.shops\\n'
        elif [[ "$command_line" == *"SELECT version_num"* ]]; then
          if [[ -n "${{FAKE_PROD_REVISION:-}}" ]]; then
            printf '%s\\n' "$FAKE_PROD_REVISION"
          fi
        else
          cat >> "$FAKE_TRACE"
          printf 'host psql restored stdin\\n' >> "$FAKE_TRACE"
        fi
        """,
    )
    _script(
        tools / "docker",
        f"""
        printf 'docker %s\\n' "$*" >> "$FAKE_TRACE"
        [[ "${{1:-}}" == exec ]] || exit 2
        shift
        if [[ "${{1:-}}" == -i ]]; then shift; fi
        container="${{1:-}}"
        shift
        command_name="${{1:-}}"
        shift
        case "$command_name" in
          pg_dump)
            if [[ "${{1:-}}" == "--version" ]]; then
              printf 'pg_dump (PostgreSQL) 18.6\\n'
            else
              printf -- '-- fake schema dump --\\n'
            fi
            ;;
          psql)
            if [[ "${{1:-}}" == "--version" ]]; then
              printf 'psql (PostgreSQL) 18.6\\n'
            else
              command_line="$*"
              if [[ "$command_line" == *"SHOW server_version_num"* ]]; then
              printf '180006\\n'
            elif [[ "$command_line" == *"table_schema || '.' || table_name"* ]]; then
              printf 'commerce.shops\\n'
            elif [[ "$command_line" == *"SELECT 1 FROM pg_database"* ]]; then
              printf '1\\n'
              elif [[ "$command_line" == *"SELECT version_num"* ]]; then
                if [[ -n "${{FAKE_PROD_REVISION:-}}" ]]; then
                  printf '%s\\n' "$FAKE_PROD_REVISION"
                fi
              else
                cat >> "$FAKE_TRACE"
                printf 'docker psql restored stdin\\n' >> "$FAKE_TRACE"
              fi
            fi
            ;;
          createdb|dropdb)
            :
            ;;
          *)
            printf 'unknown docker command: %s\\n' "$command_name" >&2
            exit 2
            ;;
        esac
        """,
    )
    return tools, trace


def _run_import(
    tmp_path: Path,
    *,
    pg_docker: str | None | object = _UNSET,
    host_major: int = 18,
    server_major: int = 18,
    prod_revision: str = "",
) -> subprocess.CompletedProcess[str]:
    tools, trace = _fake_tools(
        tmp_path,
        host_major=host_major,
        server_major=server_major,
        prod_revision=prod_revision,
    )
    env = os.environ.copy()
    env.update(
        {
            "TTS_ERP_DB_URL": SOURCE_URL,
            "TTS_ERP_DB_URL_TEST": TARGET_URL,
            "FAKE_TRACE": str(trace),
            "PATH": f"{tools}:{env['PATH']}",
        }
    )
    if prod_revision:
        env["FAKE_PROD_REVISION"] = prod_revision
    if pg_docker is _UNSET:
        env.pop("PG_DOCKER", None)
    elif pg_docker is None:
        env["PG_DOCKER"] = ""
    else:
        env["PG_DOCKER"] = pg_docker
    return subprocess.run(
        ["bash", str(IMPORTER), "--schema-only", "--yes"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _trace(tmp_path: Path) -> str:
    return (tmp_path / "trace.log").read_text()


def test_unset_pg_docker_defaults_to_configured_container(tmp_path: Path) -> None:
    result = _run_import(tmp_path, host_major=16, server_major=18)

    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "docker exec postgres pg_dump" in trace
    assert "docker exec -i postgres psql" in trace
    assert "host pg_dump" not in trace
    assert "host psql" not in trace


def test_host_client_server_mismatch_fails_before_target_mutation(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker=None, host_major=16, server_major=18)

    assert result.returncode != 0
    assert "major" in result.stderr.lower()
    trace = _trace(tmp_path)
    assert "DROP SCHEMA" not in trace
    assert "restored stdin" not in trace


def test_configured_container_wins_over_available_host_clients(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker="postgres 18", host_major=16, server_major=18)

    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "docker exec postgres 18 pg_dump" in trace
    assert "docker exec -i postgres 18 psql" in trace
    assert "host pg_dump" not in trace
    assert "host psql" not in trace


def test_explicit_empty_pg_docker_selects_host_clients(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker=None)

    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "host pg_dump" in trace
    assert "host psql" in trace
    assert "docker " not in trace


def test_schema_only_restores_schema_without_data_dumps(tmp_path: Path) -> None:
    result = _run_import(tmp_path, pg_docker="postgres")

    assert result.returncode == 0, result.stderr
    trace = _trace(tmp_path)
    assert "--schema-only" in trace
    assert "--table=" not in trace
    assert "docker psql restored stdin" in trace


def test_existing_template_is_not_dropped_before_refresh_preflight(tmp_path: Path) -> None:
    tools, trace = _fake_tools(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "TTS_ERP_DB_URL_PROD_SOURCE": SOURCE_URL,
            "TTS_ERP_DB_URL_TEST": TARGET_URL,
            "PG_DOCKER": "postgres",
            "FAKE_TRACE": str(trace),
            "PATH": f"{tools}:{env['PATH']}",
            "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "template.lock"),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(ISOLATED),
            "--refresh-template",
            "--template-db",
            "tts_erp_test_template",
            "--db-name",
            "tts_erp_test_refresh_run",
            "--",
            "--help",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    trace_text = _trace(tmp_path)
    assert "dropdb -U postgres --if-exists --force tts_erp_test_template" not in trace_text
    assert "createdb -U postgres tts_erp_test_template" not in trace_text
    assert "docker exec postgres pg_dump" in trace_text
    assert "docker exec -i postgres psql" in trace_text


def _fake_alembic_and_python(tools: Path, trace: Path) -> None:
    """Add fake ``alembic`` and ``python`` that record their invocations to ``trace``."""
    _script(
        tools / "alembic",
        """
        printf 'fake-alembic %s\\n' "$*" >> "$FAKE_TRACE"
        """,
    )
    _script(
        tools / "python",
        """
        printf 'fake-python %s\\n' "$*" >> "$FAKE_TRACE"
        """,
    )


def test_refresh_template_seeds_builtin_roles_after_alembic_upgrade(tmp_path: Path) -> None:
    """refresh_template must invoke ``seed_builtin_roles`` after ``alembic upgrade head``.

    The 0052 migration's INSERTs into ``security.roles`` / ``security.permissions``
    are skipped by ``alembic stamp`` when refreshing a schema-only template; the
    CLI seed step fills them back in idempotently.
    """
    tools, trace = _fake_tools(tmp_path, prod_revision="0052_user_accounts")
    _fake_alembic_and_python(tools, trace)
    env = os.environ.copy()
    env.update(
        {
            "TTS_ERP_DB_URL_PROD_SOURCE": SOURCE_URL,
            "TTS_ERP_DB_URL_TEST": TARGET_URL,
            "PG_DOCKER": "postgres",
            "FAKE_TRACE": str(trace),
            "FAKE_PROD_REVISION": "0052_user_accounts",
            "PATH": f"{tools}:{env['PATH']}",
            "TTS_ERP_TEST_TEMPLATE_LOCK": str(tmp_path / "template.lock"),
            "TTS_ERP_TEST_ALEMBIC_BIN": str(tools / "alembic"),
            "TTS_ERP_TEST_PYTHON_BIN": str(tools / "python"),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(ISOLATED),
            "--refresh-template",
            "--template-db",
            "tts_erp_test_template",
            "--db-name",
            "tts_erp_test_refresh_seed",
            "--",
            "--help",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    trace_text = _trace(tmp_path)
    assert "fake-alembic stamp 0052_user_accounts" in trace_text, trace_text
    assert "fake-alembic upgrade head" in trace_text, trace_text
    # Seed step targets $TEMPLATE_URL and uses the same .venv/fallback python.
    assert "fake-python -m tts_erp_v2.accounts.cli sync-permissions" in trace_text, trace_text
    # Seed must be invoked AFTER ``alembic upgrade head``.
    upgrade_idx = trace_text.find("fake-alembic upgrade head")
    seed_idx = trace_text.find("fake-python -m tts_erp_v2.accounts.cli sync-permissions")
    assert upgrade_idx != -1 and seed_idx != -1
    assert seed_idx > upgrade_idx, (
        f"seed (idx {seed_idx}) must come after alembic upgrade (idx {upgrade_idx})"
    )
