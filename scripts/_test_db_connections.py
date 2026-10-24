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
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL
from sqlalchemy.exc import ArgumentError

from scripts._db_url import normalize_db_url

DOCKER_SOCKET = "unix:///var/run/docker.sock"
_CORE = {"user", "password", "host", "port", "dbname"}
_RESERVED_RUN_DATABASES = {"tts_erp_test_template", "tts_erp_v3_test"}
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
    if run in {template, parameters["dbname"], *_RESERVED_RUN_DATABASES}:
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


def _process_state(pid: int) -> tuple[int, str]:
    """Linux proc(5): ppid=field 4, boot-relative starttime=field 22.

    https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
    No command line, environment or credential-bearing process data is read.
    """
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1]), fields[19]
    except (OSError, IndexError, ValueError):
        raise ValueError("test run owner is unavailable") from None


def _require_owner(owner_pid: int, owner_start: str) -> None:
    current = os.getppid()
    for _ in range(256):
        if current <= 0:
            break
        parent, start = _process_state(current)
        if current == owner_pid:
            if start != owner_start:
                break
            return
        current = parent
    raise ValueError("test run owner is not a live ancestor")


def _run_parameters(value: str) -> dict[str, str]:
    parameters = _parameters(value)
    # Run consumers accept only the authority's canonical output. This also
    # rejects query dbname overrides even when they name the expected database.
    if value != _uri(parameters, parameters["dbname"], sqlalchemy=True):
        raise ValueError("noncanonical run connection identity")
    return parameters


def _connection_digest(parameters: dict[str, str], run_id: str) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((run_id + "\n" + payload).encode()).hexdigest()


def issue_run_proof(
    value: str, *, base: str, template: str, run: str,
    run_id: str, owner_pid: int, proof_path: Path,
) -> None:
    """Called by the wrapper ONLY after its createdb command succeeded.

    This is local lifecycle provenance, not a same-UID adversary boundary.
    The private artifact never contains a URL or plaintext connection fields.
    """
    parameters = _run_parameters(value)
    for name in (base, template, run):
        _test_name(name)
    if run in {base, template, *_RESERVED_RUN_DATABASES} or parameters["dbname"] != run:
        raise ValueError("invalid issued run destination")
    if not re.fullmatch(r"[0-9a-f]{64}", run_id) or owner_pid <= 0:
        raise ValueError("invalid issued run identity")
    _, owner_start = _process_state(owner_pid)
    _require_owner(owner_pid, owner_start)
    directory = proof_path.parent.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) & 0o077):
        raise ValueError("test run proof directory is not private")
    payload = {
        "version": 1, "created": True, "runId": run_id,
        "ownerPid": owner_pid, "ownerStart": owner_start,
        "baseDb": base, "templateDb": template, "runDb": run,
        "connectionDigest": _connection_digest(parameters, run_id),
    }
    fd = os.open(proof_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class RunBinding:
    """Credential-free provenance, loaded independently of a candidate URL."""

    run_id: str
    run_db: str
    owner_pid: int
    owner_start: str
    connection_digest: str

    def validate_url(self, value: str) -> None:
        try:
            _require_owner(self.owner_pid, self.owner_start)
            parameters = _run_parameters(value)
            if parameters["dbname"] != self.run_db or not hmac.compare_digest(
                _connection_digest(parameters, self.run_id), self.connection_digest,
            ):
                raise ValueError("run identity mismatch")
        except (ValueError, TypeError, OSError):
            raise ValueError("isolated run connection binding refused") from None

    def verify_database(self, actual: object) -> None:
        if actual != self.run_db:
            raise ValueError("isolated run actual database mismatch")


def load_run_binding(
    value: str, *, proof_path: Path | None = None, run_id: str | None = None,
) -> RunBinding:
    """Fail closed before constructing/connecting an engine. No SQL is run."""
    try:
        if run_id is None:
            run_id = os.environ.get("TTS_ERP_TEST_RUN_ID", "")
        if not re.fullmatch(r"[0-9a-f]{64}", run_id):
            raise ValueError("missing current run identity")
        if proof_path is None:
            path = os.environ.get("TTS_ERP_TEST_RUN_PROOF", "")
            if not path:
                raise ValueError("missing current run proof")
            proof_path = Path(path)
        directory = proof_path.parent.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) & 0o077):
            raise ValueError("unsafe proof directory")
        # A FIFO must not block open() before fstat can reject its file type.
        fd = os.open(proof_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                    or stat.S_IMODE(meta.st_mode) & 0o077):
                raise ValueError("unsafe proof file")
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError("oversized proof")
        payload = json.loads(raw)
        keys = {
            "version", "created", "runId", "ownerPid", "ownerStart",
            "baseDb", "templateDb", "runDb", "connectionDigest",
        }
        if (not isinstance(payload, dict) or set(payload) != keys
                or type(payload["version"]) is not int or payload["version"] != 1
                or payload["created"] is not True or payload["runId"] != run_id
                or type(payload["ownerPid"]) is not int or payload["ownerPid"] <= 0):
            raise ValueError("invalid proof shape")
        for key in ("ownerStart", "baseDb", "templateDb", "runDb", "connectionDigest"):
            if not isinstance(payload[key], str):
                raise TypeError("invalid proof field type")
        if (not re.fullmatch(r"[0-9]+", payload["ownerStart"])
                or not re.fullmatch(r"[0-9a-f]{64}", payload["connectionDigest"])):
            raise ValueError("invalid proof field")
        for key in ("baseDb", "templateDb", "runDb"):
            _test_name(payload[key])
        if payload["runDb"] in {
            payload["baseDb"], payload["templateDb"], *_RESERVED_RUN_DATABASES,
        }:
            raise ValueError("reserved proof destination")
        binding = RunBinding(
            run_id=run_id, run_db=payload["runDb"], owner_pid=payload["ownerPid"],
            owner_start=payload["ownerStart"], connection_digest=payload["connectionDigest"],
        )
        binding.validate_url(value)
        return binding
    except (ValueError, TypeError, OSError, RecursionError):
        # Library/file errors may contain configuration values. Do not relay them.
        raise ValueError("current isolated run proof is missing or inconsistent") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("derive", "recreation", "issue-run-proof"))
    parser.add_argument("--template-db")
    parser.add_argument("--run-db")
    parser.add_argument("--base-db")
    parser.add_argument("--run-id")
    parser.add_argument("--owner-pid", type=int)
    parser.add_argument("--proof-file", type=Path)
    args = parser.parse_args()
    try:
        docker = os.environ.get("PG_DOCKER", "postgres")
        target = os.environ.get("TTS_ERP_DB_URL_TEST", "")
        if args.mode == "derive":
            if not args.template_db or not args.run_db:
                raise ValueError("template and ephemeral identities are required")
            values = derive(target, args.template_db, args.run_db, docker)
        elif args.mode == "recreation":
            values = recreation(os.environ.get("TTS_ERP_DB_URL", ""), target, docker)
        else:
            if not all((args.base_db, args.template_db, args.run_db, args.run_id,
                        args.owner_pid, args.proof_file)):
                raise ValueError("issued run metadata is required")
            issue_run_proof(
                target, base=args.base_db, template=args.template_db, run=args.run_db,
                run_id=args.run_id, owner_pid=args.owner_pid, proof_path=args.proof_file,
            )
            values = ()
        if values:
            print("\n".join(values))
    except TypeError:
        print("[test-db] unsupported PostgreSQL parameter type", file=sys.stderr)
        return 3
    except (ValueError, OSError):
        # External parsers can embed credential-bearing input in their errors.
        # Never relay ValueError text, even for a currently trusted code path.
        print("[test-db] invalid or unsupported PostgreSQL configuration", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
