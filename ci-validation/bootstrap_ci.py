"""Restore reviewed schema-only material; never print SQL or secret payloads."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg


class SafeFailure(Exception):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SafeFailure(code)


def main() -> None:
    encoded = "".join(os.environ.pop(f"TTS_ERP_SCHEMA_{part}", "") for part in "ABCD")
    require(bool(encoded) and len(encoded) <= 180_000, "SCHEMA_PAYLOAD_MISSING_OR_LARGE")
    packed = base64.b64decode(encoded, validate=True)
    # A schema secret is reviewed material, not an arbitrary archive.
    decoded = gzip.decompress(packed)
    require(len(decoded) <= 8_000_000, "SCHEMA_PAYLOAD_TOO_LARGE")
    payload = json.loads(decoded)
    sql = payload["ddl"]
    revision = payload["alembic_revision"]
    require(isinstance(sql, str), "SCHEMA_TYPE")
    require(bool(re.fullmatch(r"[A-Za-z0-9_]{1,32}", revision)), "SCHEMA_REVISION")
    schema_sha = hashlib.sha256(sql.encode()).hexdigest()
    require(schema_sha == payload["schema_sha256"], "SCHEMA_HASH")
    forbidden = (
        r"\bCREATE\s+(?:FOREIGN\s+TABLE|SERVER|USER\s+MAPPING|SUBSCRIPTION|DATABASE|ROLE|USER)\b",
        r"\bALTER\s+(?:SYSTEM|ROLE|USER)\b",
        r"\bCONNECTION\b",
        r"(?m)^\s*COPY\b[^\n]*\bFROM\s+stdin\s*;",
    )
    require(not any(re.search(pattern, sql, re.I) for pattern in forbidden), "SCHEMA_FORBIDDEN_DDL")
    for line in sql.splitlines():
        if line.lstrip().startswith("\\"):
            require(bool(re.fullmatch(r"\\(?:un)?restrict\s+[A-Za-z0-9]+", line.strip())), "SCHEMA_META_COMMAND")

    heads = subprocess.run([".venv/bin/alembic", "heads"], capture_output=True, text=True)
    require(heads.returncode == 0, "CODE_REVISION_PROBE")
    code_heads = [line.split()[0] for line in heads.stdout.splitlines() if "(head)" in line]
    require(code_heads == [revision], "SCHEMA_CODE_REVISION_MISMATCH")
    route = subprocess.run(
        [sys.executable, "-m", "scripts._test_db_connections", "derive", "--template-db",
         "tts_erp_test_template", "--run-db", "tts_erp_test_ci_preflight"],
        capture_output=True, text=True,
    )
    require(route.returncode == 0, "CI_POSTGRES_AUTHORITY")
    fields = route.stdout.splitlines()
    require(len(fields) == 6 and fields[4] == "unix:///var/run/docker.sock", "CI_POSTGRES_ROUTE")
    container = fields[5]
    require(bool(re.fullmatch(r"[0-9a-f]{64}", container)), "CI_POSTGRES_CONTAINER")
    plain = "postgresql://postgres:TEST_ci_local_only@127.0.0.1:5432/tts_erp_test_base"
    restore = subprocess.run(
        ["docker", "--host", fields[4], "exec", "-i", container, "psql", "--no-psqlrc",
         "--quiet", "--set", "ON_ERROR_STOP=1", "--dbname", plain],
        input=sql, capture_output=True, text=True,
    )
    # Do not relay psql stdout/stderr: failed function/default DDL can include literals.
    require(restore.returncode == 0, "CI_SCHEMA_RESTORE")
    with psycopg.connect(plain, connect_timeout=10) as connection:
        require(connection.execute("SELECT current_database()").fetchone()[0] == "tts_erp_test_base", "CI_BASE_IDENTITY")
        require(connection.execute("SELECT version_num FROM alembic_version").fetchall() == [], "CI_SCHEMA_HAS_DATA")
        connection.execute("INSERT INTO alembic_version(version_num) VALUES (%s)", (revision,))
    results = Path(os.environ["RUNNER_TEMP"]) / "ad-metric-validation-results"
    results.mkdir(exist_ok=True)
    (results / "schema-metadata.json").write_text(json.dumps({
        "schema_sha256": schema_sha, "alembic_revision": revision,
        "schema_only": True, "production_connection_used": False,
    }, indent=2))
    print("Reviewed schema restored into the isolated CI base; no SQL emitted")


if __name__ == "__main__":
    try:
        main()
    except SafeFailure as error:
        print(f"CI schema bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"CI schema bootstrap failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
