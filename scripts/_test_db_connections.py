"""One connection-identity authority for guarded test DB lifecycle commands.

No SQL is executed here. psycopg parses effective libpq parameters; SQLAlchemy
serializes the host-side and command-line URIs without competing string rewrites.
Docker support deliberately covers only the repository's verified local profile.
Reuses installed psycopg 3.3.6 (LGPL-3.0-only) and SQLAlchemy 2.1.2 (MIT),
without adding dependencies or copying upstream code:
https://www.psycopg.org/psycopg3/docs/api/conninfo.html
https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.engine.URL.create
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from urllib.parse import unquote, urlparse

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL
from sqlalchemy.exc import ArgumentError

from scripts._db_url import normalize_db_url

DOCKER_SOCKET = "unix:///var/run/docker.sock"
_CORE = {"user", "password", "host", "port", "dbname"}
_UNSUPPORTED = {"service", "servicefile", "hostaddr", "target_session_attrs", "load_balance_hosts"}
_AMBIENT_ROUTING = {
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGSERVICE",
    "PGSERVICEFILE", "PGOPTIONS", "PGTARGETSESSIONATTRS", "PGLOADBALANCEHOSTS",
}


def _parameters(value: str) -> dict[str, str]:
    if any(os.environ.get(key) for key in _AMBIENT_ROUTING):
        raise ValueError("ambient libpq routing overrides are unsupported")
    if not value.startswith(("postgresql", "postgres://")):
        raise ValueError("an explicit PostgreSQL URI is required")
    try:
        raw_parameters = conninfo_to_dict(normalize_db_url(value))
    except PsycopgError:  # libpq diagnostics may include the unredacted URI.
        raise ValueError("invalid PostgreSQL connection configuration") from None
    parameters: dict[str, str] = {}
    for key, item in raw_parameters.items():
        if not isinstance(item, str):
            raise TypeError("unsupported PostgreSQL parameter type")
        parameters[key] = item
    if not parameters.get("dbname") or not parameters.get("host") or not parameters.get("user"):
        raise ValueError("explicit database, user and TCP host are required")
    if _UNSUPPORTED.intersection(parameters) or any(c in parameters["host"] for c in "/,"):
        raise ValueError("unsupported PostgreSQL routing configuration")
    if any(ord(c) < 32 or ord(c) == 127 for text in parameters.values() for c in text):
        raise ValueError("connection control characters are unsupported")
    try:
        port = int(parameters.get("port", "5432"))
    except ValueError:
        raise ValueError("unsupported PostgreSQL port") from None
    if not 1 <= port <= 65535:
        raise ValueError("unsupported PostgreSQL port")
    parameters["port"] = str(port)
    return parameters


def _uri(parameters: dict[str, str], database: str, *, sqlalchemy: bool = False) -> str:
    expected = {**parameters, "dbname": database}
    try:
        value = URL.create(
            "postgresql+psycopg" if sqlalchemy else "postgresql",
            username=parameters.get("user"), password=parameters.get("password"),
            host=parameters["host"], port=int(parameters["port"]), database=database,
            query={key: item for key, item in parameters.items() if key not in _CORE},
        ).render_as_string(hide_password=False)
        round_trip = conninfo_to_dict(normalize_db_url(value))
    except (PsycopgError, ArgumentError, ValueError):
        raise ValueError("connection identity serialization failed") from None
    if round_trip != expected:
        raise ValueError("connection identity round-trip failed")
    return value


def _test_name(name: str, *, confirmation: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,63}", name) or "test" not in name:
        raise ValueError("an explicitly test-named database is required")
    original = os.environ.get("TTS_ERP_DB_URL")
    # The shared guard is intentionally a database-name check. Bind it to the
    # effective parsed name; this synthetic URI is NEVER used for a connection.
    os.environ["TTS_ERP_DB_URL"] = URL.create("postgresql", host="guard", database=name).render_as_string()
    try:
        from tts_erp_v2.api.deps import (
            is_prod_shaped_db,
            require_destructive_script_guard,
        )

        if is_prod_shaped_db():
            raise ValueError("production-shaped database is forbidden")
        if confirmation:
            require_destructive_script_guard(
                script_name="recreate-test-db", confirmation=True, dangerous=True,
            )
    finally:
        if original is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original


def _transport(parameters: list[dict[str, str]], docker: str) -> tuple[str, str]:
    if not docker:
        return "-", "-"
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise ValueError("ambient Docker routing overrides are unsupported")
    if any(p["host"] != "127.0.0.1" or p["port"] != "5432" for p in parameters):
        raise ValueError("Docker test lifecycle requires the verified local endpoint")
    try:
        endpoint = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        if endpoint != DOCKER_SOCKET:
            raise ValueError("non-local Docker context is unsupported")
        identity = subprocess.run(
            ["docker", "--host", DOCKER_SOCKET, "container", "inspect", "--format", "{{.Id}}", docker],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("cannot verify immutable PostgreSQL container identity")
        publication = subprocess.run(
            ["docker", "--host", DOCKER_SOCKET, "port", identity, "5432/tcp"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.splitlines()
        # Inspect only the routing/role fields used by libpq. No environment
        # values (including unrelated container credentials) leave the container.
        probe = 'test -z "' + "".join("${" + key + "-}" for key in sorted(_AMBIENT_ROUTING)) + '"'
        subprocess.run(
            ["docker", "--host", DOCKER_SOCKET, "exec", identity, "/bin/sh", "-c", probe],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("cannot verify the selected PostgreSQL container route") from None
    if not {"0.0.0.0:5432", "127.0.0.1:5432"}.intersection(publication):
        raise ValueError("PostgreSQL container does not publish the required local port")
    return DOCKER_SOCKET, identity


def derive(base: str, template: str, run: str, docker: str) -> tuple[str, ...]:
    parameters = _parameters(base)
    for name in (parameters["dbname"], template, run):
        _test_name(name)
    if run in {template, parameters["dbname"]}:
        raise ValueError("ephemeral database must be distinct")
    pin, identity = _transport([parameters], docker)
    return (
        parameters["dbname"], _uri(parameters, template, sqlalchemy=True),
        _uri(parameters, run, sqlalchemy=True), _uri(parameters, "postgres"), pin, identity,
    )


def recreation(source: str, target: str, docker: str) -> tuple[str, ...]:
    src, dst = _parameters(source), _parameters(target)
    if unquote(urlparse(normalize_db_url(target)).path.lstrip("/")) != dst["dbname"]:
        raise ValueError("ambiguous test recreation destination")
    _test_name(dst["dbname"], confirmation=True)
    if src["dbname"] == dst["dbname"]:
        raise ValueError("source and target database names must differ")
    pin, identity = _transport([src, dst], docker)
    return (
        dst["dbname"], _uri(dst, "postgres"), pin,
        _uri(src, src["dbname"]), _uri(dst, dst["dbname"]), identity,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("derive", "recreation"))
    parser.add_argument("--template-db")
    parser.add_argument("--run-db")
    args = parser.parse_args()
    try:
        docker = os.environ.get("PG_DOCKER", "postgres")
        target = os.environ.get("TTS_ERP_DB_URL_TEST", "")
        if args.mode == "derive":
            if not args.template_db or not args.run_db:
                raise ValueError("template and ephemeral identities are required")
            values = derive(target, args.template_db, args.run_db, docker)
        else:
            values = recreation(os.environ.get("TTS_ERP_DB_URL", ""), target, docker)
        print("\n".join(values))
    except TypeError:
        print("[test-db] unsupported PostgreSQL parameter type", file=sys.stderr)
        return 3
    except ValueError as error:
        print(f"[test-db] {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
