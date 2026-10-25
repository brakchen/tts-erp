"""Human migration CLI safety; all commands/connections are controlled doubles."""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL

pytestmark = [pytest.mark.layer_unit]
SCRIPT = Path(__file__).parents[2] / "scripts/oneoff_migrate_monitor_health.py"
spec = importlib.util.spec_from_file_location("monitor_health_db_script", SCRIPT)
assert spec and spec.loader
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
URL_TEST = URL.create(
    "postgresql+psycopg",
    username="TEST_user",
    password="TEST_password",
    host="127.0.0.1",
    port=5432,
    database="TEST_monitor_health",
)


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    events = []
    revisions = iter([script.SOURCE, script.SOURCE, script.TARGET])
    monkeypatch.setattr(script, "read_db_url", lambda _: URL_TEST)

    def probe(_):
        events.append("probe")
        return next(revisions)

    monkeypatch.setattr(script, "probe", probe)
    monkeypatch.setattr(
        script, "backup", lambda *_: events.append("backup") or tmp_path / "TEST.dump"
    )
    monkeypatch.setattr(script, "upgrade", lambda *_: events.append("upgrade"))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        builtins, "input", lambda _: events.append("confirm") or URL_TEST.database
    )
    args = argparse.Namespace(
        apply=True, env_file=tmp_path / "TEST.env", backup_dir=tmp_path
    )
    return args, events


def test_apply_requires_backup_before_exact_upgrade(workflow):
    args, events = workflow
    assert script.run(args) == 0
    assert events == ["probe", "confirm", "backup", "probe", "upgrade", "probe"]


@pytest.mark.parametrize("revision", ["0068_video_publish_tasks", "0071_future", ""])
def test_wrong_revision_never_backs_up_or_upgrades(workflow, monkeypatch, revision):
    args, events = workflow
    monkeypatch.setattr(script, "probe", lambda _: revision)
    with pytest.raises(script.OperatorError, match="版本"):
        script.run(args)
    assert events == []


def test_default_is_read_only(workflow):
    args, events = workflow
    args.apply = False
    assert script.run(args) == 0
    assert events == ["probe"]


def test_already_0070_is_idempotent(workflow, monkeypatch):
    args, events = workflow
    monkeypatch.setattr(script, "probe", lambda _: script.TARGET)
    assert script.run(args) == 0
    assert events == []


@pytest.mark.parametrize(
    "tty,confirmation", [(False, "TEST_monitor_health"), (True, "wrong")]
)
def test_confirmation_refusal_does_not_back_up(
    workflow, monkeypatch, tty, confirmation
):
    args, events = workflow
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr(builtins, "input", lambda _: confirmation)
    with pytest.raises(script.OperatorError):
        script.run(args)
    assert events == ["probe"]


def test_backup_failure_prevents_upgrade(workflow, monkeypatch):
    args, events = workflow

    def failed(*_):
        events.append("backup")
        raise script.OperatorError("备份失败")

    monkeypatch.setattr(script, "backup", failed)
    with pytest.raises(script.OperatorError, match="备份"):
        script.run(args)
    assert events == ["probe", "confirm", "backup"]


def test_changed_revision_after_backup_stops(workflow, monkeypatch):
    args, events = workflow
    values = iter([script.SOURCE, script.TARGET])
    monkeypatch.setattr(script, "probe", lambda _: next(values))
    with pytest.raises(script.OperatorError):
        script.run(args)
    assert events == ["confirm", "backup"]


def test_upgrade_failure_does_not_retry(workflow, monkeypatch):
    args, events = workflow

    def failed(_):
        events.append("upgrade")
        raise script.OperatorError("迁移状态未确认")

    monkeypatch.setattr(script, "upgrade", failed)
    with pytest.raises(script.OperatorError):
        script.run(args)
    assert events == ["probe", "confirm", "backup", "probe", "upgrade"]


def test_wrong_postcondition_is_not_success(workflow, monkeypatch):
    args, _ = workflow
    monkeypatch.setattr(script, "probe", lambda _: script.SOURCE)
    with pytest.raises(script.OperatorError):
        script.run(args)


def env_file(tmp_path, value):
    p = tmp_path / "TEST.env"
    p.write_text("TTS_ERP_DB_URL='" + value + "'\nTEST_IGNORED=secret\n")
    p.chmod(0o600)
    return p


def test_env_parser_preserves_encoded_password(tmp_path):
    url = URL_TEST.set(password="TEST_a:b#$d%")
    actual = script.read_db_url(
        env_file(tmp_path, url.render_as_string(hide_password=False))
    )
    assert actual == url


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "postgresql://u:p@127.0.0.1",
        "postgresql://u:p@127.0.0.1/test?dbname=other",
        "postgresql://u:p@127.0.0.1/test?options=-crole%3Dother",
        "postgresql://u:p@a,b/test",
    ],
)
def test_ambiguous_or_invalid_connection_is_rejected(tmp_path, value):
    with pytest.raises(script.OperatorError):
        script.read_db_url(env_file(tmp_path, value))


def test_world_readable_config_is_refused(tmp_path):
    p = env_file(tmp_path, URL_TEST.render_as_string(hide_password=False))
    p.chmod(0o644)
    with pytest.raises(script.OperatorError):
        script.read_db_url(p)


def test_ambient_routing_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PGDATABASE", "TEST_other")
    with pytest.raises(script.OperatorError):
        script.read_db_url(
            env_file(tmp_path, URL_TEST.render_as_string(hide_password=False))
        )


def test_child_environment_is_allowlisted():
    assert set(script._child_env()) <= {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}


def test_upgrade_is_pinned_and_secrets_not_in_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs)) or SimpleNamespace(returncode=0)
        ),
    )
    script.upgrade(URL_TEST)
    argv, options = calls[0]
    assert argv[-2:] == ["upgrade", script.TARGET]
    assert (
        "head" not in argv and "stamp" not in argv and "TEST_password" not in str(argv)
    )
    assert options["env"]["ALLOW_PROD_DESTRUCTIVE"] == "1"
    assert options["env"]["TTS_ERP_DB_URL"] == URL_TEST.render_as_string(
        hide_password=False
    )
    assert Path(options["cwd"]) == script.ROOT


def test_backup_is_private_verified_and_does_not_rotate(tmp_path, monkeypatch):
    old = tmp_path / "TEST_old.dump"
    old.write_bytes(b"old")
    calls = []
    password_files = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)

    def command(argv, **kwargs):
        calls.append(argv)
        assert "TEST_password" not in str(argv)
        if Path(argv[0]).name == "pg_dump":
            assert kwargs["env"]["PGDATABASE"] == URL_TEST.database
            assert kwargs["env"]["PGHOST"] == URL_TEST.host
            passfile = Path(kwargs["env"]["PGPASSFILE"])
            password_files.append(passfile)
            assert passfile.stat().st_mode & 0o777 == 0o600
            assert "TEST_password" in passfile.read_text()
            Path(argv[argv.index("--file") + 1]).write_bytes(b"PGDMP-TEST")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", command)
    path = script.backup(URL_TEST, tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert [Path(c[0]).name for c in calls] == ["pg_dump", "pg_restore"]
    assert calls[1][1] == "--list" and old.read_bytes() == b"old"
    assert all(not p.exists() for p in password_files)


def test_backup_error_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(
            1, argv, stderr="TEST_password secret://TEST_webhook"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(script.OperatorError) as exc:
        script.backup(URL_TEST, tmp_path)
    assert "TEST_password" not in str(exc.value) and "secret://" not in str(exc.value)


@pytest.fixture
def metadata_probe(monkeypatch):
    state = {
        "revision": [script.TARGET],
        "exists": True,
        "database": URL_TEST.database,
        "schema": "public",
        "pk": ["shop_id"],
    }
    columns = [
        {"name": "shop_id", "type": String(128), "nullable": False},
        {"name": "shop_name", "type": String(200), "nullable": False},
        {"name": "state", "type": JSONB(), "nullable": False},
    ]
    statements = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def exec_driver_sql(self, sql):
            statements.append(sql)
            return SimpleNamespace(
                one=lambda: (state["database"], state["schema"]),
                scalars=lambda: SimpleNamespace(all=lambda: state["revision"]),
            )

    inspector = SimpleNamespace(
        has_table=lambda *_, **__: state["exists"],
        get_columns=lambda *_, **__: columns,
        get_pk_constraint=lambda *_, **__: {"constrained_columns": state["pk"]},
    )
    monkeypatch.setattr(
        script,
        "create_engine",
        lambda *_, **__: SimpleNamespace(connect=Conn, dispose=lambda: None),
    )
    monkeypatch.setattr(script, "inspect", lambda _: inspector)
    return state, columns, statements


def test_probe_uses_readonly_transaction_and_checks_0070(metadata_probe):
    _, _, statements = metadata_probe
    assert script.probe(URL_TEST) == script.TARGET
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(
        not s.startswith(("UPDATE", "DELETE", "DROP", "CREATE")) for s in statements
    )


@pytest.mark.parametrize(
    "case",
    [
        "database",
        "schema",
        "multihead",
        "missing_table",
        "bad_pk",
        "nullable",
        "wrong_type",
        "unexpected_0069_table",
    ],
)
def test_probe_refuses_inconsistent_database(metadata_probe, case):
    state, columns, _ = metadata_probe
    if case == "database":
        state["database"] = "TEST_other"
    elif case == "schema":
        state["schema"] = "TEST_other"
    elif case == "multihead":
        state["revision"] = [script.SOURCE, script.TARGET]
    elif case == "missing_table":
        state["exists"] = False
    elif case == "bad_pk":
        state["pk"] = []
    elif case == "nullable":
        columns[2]["nullable"] = True
    elif case == "wrong_type":
        columns[2]["type"] = String(20)
    else:
        state["revision"] = [script.SOURCE]
    with pytest.raises(script.OperatorError):
        script.probe(URL_TEST)


def test_main_never_echoes_untrusted_exception(monkeypatch, capsys):
    monkeypatch.setattr(script, "_ensure_clean_repo", lambda: None)
    monkeypatch.setattr(
        script,
        "run",
        lambda _: (_ for _ in ()).throw(
            ValueError("TEST_password secret://TEST_webhook")
        ),
    )
    assert script.main([]) != 0
    output = capsys.readouterr()
    assert "TEST_password" not in output.out + output.err
    assert "secret://" not in output.out + output.err


def test_existing_0070_probe_on_authorized_clone(db_engine):
    # db_engine is fenced by the canonical conftest's current-run binding.
    assert db_engine.url.database.startswith("tts_erp_test_")
    assert script.probe(db_engine.url) == script.TARGET


def test_env_file_routing_override_is_refused(tmp_path):
    path = env_file(tmp_path, URL_TEST.render_as_string(hide_password=False))
    with path.open("a") as output:
        output.write("PGDATABASE=TEST_other\n")
    with pytest.raises(script.OperatorError):
        script.read_db_url(path)


def test_help_never_opens_config_or_database(monkeypatch):
    monkeypatch.setattr(
        script, "run", lambda _: pytest.fail("help entered DB workflow")
    )
    with pytest.raises(SystemExit) as exc:
        script.main(["--help"])
    assert exc.value.code == 0
