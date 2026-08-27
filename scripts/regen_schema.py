#!/usr/bin/env python3
"""Regenerate schema.sql from live PG database.

Run:
    python3 scripts/regen_schema.py > schema.sql

What it does:
  1. Runs `pg_dump --schema-only --no-owner --no-privileges
     --no-tablespaces --no-comments` against BOTH `tts_erp` and
     `oauth_receiver` databases.
  2. Cleans the dumps:
       - Strips `\\restrict ...` security token (NOT for source control)
       - Drops CREATE SEQUENCE / ALTER SEQUENCE / sequence SETVAL
         (SERIAL/BIGSERIAL columns own these implicitly — keeping the
         SEQUENCE creates a race when re-running schema.sql against
         a fresh DB)
       - Adds IF NOT EXISTS to CREATE TABLE / CREATE FUNCTION for
         idempotency (schema.sql is meant to be re-runnable)
       - Strips `pg_catalog.set_config` lines (session-only noise)
  3. Concatenates into a single schema.sql with a clear banner
     separating the two DBs.

Why a script, not a manual edit:
  - As of 2026-08-25 the schema.sql had drifted from reality (missing 7 new
    tables; wrong `order_status` vs `order_status_name`). Drift was found
    when healthz / sync / db inspection disagreed.
  - Re-running this script after any future schema change is the only
    way to keep schema.sql authoritative.

The script is intentionally dumb (no AST parsing of SQL, just text
transformations of pg_dump output). If pg_dump format changes
substantively, the script needs updating — which is fine because
that means schema.sql was probably going to be inaccurate anyway.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _env_value(key: str) -> str | None:
    """Read KEY=... from .env (no shell). Returns None if missing."""
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _redact(url: str) -> str:
    """postgresql://user:pass@host/db → postgresql://user:***@host/db."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


def _pg_dump(db_url: str) -> str:
    """Run pg_dump with safe options and return the cleaned dump."""
    raw = subprocess.check_output(
        [
            "docker",
            "exec",
            "postgres",
            "pg_dump",
            "-U",
            "postgres",
            "-d",
            _resolve_db_name(db_url),
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--no-tablespaces",
            "--no-comments",
        ],
        stderr=subprocess.STDOUT,
        text=True,
    )
    return _clean(raw)


def _resolve_db_name(db_url: str) -> str:
    """postgresql://...:5432/tts_erp → tts_erp. Raises if missing."""
    last = db_url.rstrip("/").rsplit("/", 1)[-1]
    if not last or "?" in last:
        raise RuntimeError(f"could not extract db name from {db_url!r}")
    return last


def _clean(dump: str) -> str:
    """Strip noise from pg_dump output."""
    lines = dump.splitlines()
    out: list[str] = []
    for line in lines:
        # Security token — NEVER commit
        if line.startswith("\\restrict"):
            continue
        # Session config noise
        if line.startswith("SET ") and (
            "search_path" in line
            or "statement_timeout" in line
            or "lock_timeout" in line
            or "idle_in_transaction" in line
            or "transaction_timeout" in line
            or "client_encoding" in line
            or "standard_conforming" in line
            or "check_function_bodies" in line
            or "xmloption" in line
            or "client_min_messages" in line
            or "row_security" in line
            or "default_tablespace" in line
            or "default_table_access_method" in line
        ):
            continue
        # Drop SEQUENCE statements — SERIAL/BIGSERIAL columns own their
        # own implicit sequences; re-creating them on a fresh DB breaks.
        if line.startswith("CREATE SEQUENCE "):
            continue
        if line.startswith("ALTER SEQUENCE "):
            continue
        if line.startswith("-- Name: ") and "Type: SEQUENCE" in line:
            # Drops both "-- Name: foo; Type: SEQUENCE; ..." and
            # "-- Name: foo; Type: SEQUENCE OWNED BY; ..." headers.
            continue
        # Drop orphan sequence parameters (left behind by SEQUENCE removal):
        #     START WITH 1
        #     INCREMENT BY 1
        #     NO MINVALUE
        #     NO MAXVALUE
        #     CACHE 1
        if line.startswith(
            (
                "    START WITH ",
                "    INCREMENT BY ",
                "    NO MINVALUE",
                "    NO MAXVALUE",
                "    CACHE ",
            )
        ):
            continue
        # Drop the dump-version preamble
        if line.startswith(
            ("-- Dumped from database version", "-- Dumped by pg_dump version")
        ):
            continue
        if line == "CREATE FUNCTION" or line.startswith("CREATE FUNCTION "):
            # pass through
            pass
        # Add IF NOT EXISTS to CREATE TABLE (Postgres 9.1+).
        if line.startswith("CREATE TABLE "):
            line = line.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
        # CREATE FUNCTION does not support IF NOT EXISTS in Postgres (any
        # version as of 18). Use CREATE OR REPLACE FUNCTION for idempotency.
        if line.startswith("CREATE FUNCTION "):
            line = line.replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1)
        # CREATE INDEX supports IF NOT EXISTS (Postgres 9.5+).
        if line.startswith("CREATE INDEX "):
            line = line.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
        # CREATE TRIGGER: IF NOT EXISTS is not supported in any PG version (even
        # though docs claim PG 14+ — actual error). Use OR REPLACE for idempotency.
        if line.startswith("CREATE TRIGGER "):
            line = line.replace("CREATE TRIGGER ", "CREATE OR REPLACE TRIGGER ", 1)
        # Strip "ALTER TABLE ONLY ... ALTER COLUMN id SET DEFAULT nextval(...)".
        # pg_dump emits these for SERIAL columns, but we already dropped the
        # matching CREATE SEQUENCE statements — so the sequence doesn't exist
        # on a fresh DB and the ALTER fails. SERIAL columns own their own
        # sequences implicitly, so the SET DEFAULT is redundant.
        if "SET DEFAULT nextval(" in line:
            continue
        if line.strip() == "-- Name: " and False:  # placeholder
            continue
        # Skip the "Type: TABLE / Schema: public / Owner: -" comment blocks
        if line.strip().startswith("-- Owner:") or line.strip() == "--":
            # Keep `--` separators that look meaningful
            if line.strip() in ("--",):
                continue
            if "Owner:" in line:
                continue
        out.append(line)
    return "\n".join(out) + "\n"


OAUTH_HEADER = """-- =============================================================================
-- oauth_receiver schema (regenerated by scripts/regen_schema.py)
-- =============================================================================
--
-- DO NOT EDIT BY HAND. Run `python3 scripts/regen_schema.py` after any
-- schema change; it rewrites schema_oauth.sql + schema_tts_erp.sql.
--
-- To apply:
--     docker exec -i postgres psql -U postgres -d oauth_receiver < schema_oauth.sql
--
-- Idempotency notes: see schema_tts_erp.sql header (same rules).
-- =============================================================================

"""

TTS_HEADER = """-- =============================================================================
-- tts_erp schema (regenerated by scripts/regen_schema.py)
-- =============================================================================
--
-- DO NOT EDIT BY HAND. Run `python3 scripts/regen_schema.py` after any
-- schema change; it rewrites schema_oauth.sql + schema_tts_erp.sql.
--
-- To apply:
--     docker exec -i postgres psql -U postgres -d tts_erp < schema_tts_erp.sql
--
-- FK policy (2026-08-27): sync-mirror tables carry NO foreign keys.
-- Writes are idempotent upserts keyed by natural primary keys; parents
-- are always written before children by the sync layer. The one legacy
-- exception (logistics_events → orders) was dropped in Wave 2 together
-- with that dead table.
--
-- Idempotency:
--   - CREATE TABLE / CREATE INDEX / CREATE TRIGGER use IF NOT EXISTS /
--     OR REPLACE — re-applying is safe.
--   - ALTER TABLE ... ADD CONSTRAINT is NOT idempotent in PG 18; on a
--     populated DB a re-apply emits "already exists" notices (harmless).
--
-- Designed target is "fresh DB clean apply". Do not re-run against a
-- populated production DB without reading the diff carefully.
-- =============================================================================

"""


def main() -> int:
    tts_url = _env_value("TTS_ERP_DB_URL")
    oauth_url = _env_value("OAUTH_DB_URL")
    if not tts_url or not oauth_url:
        sys.stderr.write(
            f"fatal: TTS_ERP_DB_URL or OAUTH_DB_URL missing in {ENV_PATH}\n"
        )
        return 1

    sys.stderr.write(f"# source: tts_erp      ({_redact(tts_url)})\n")
    sys.stderr.write(f"# source: oauth_receiver ({_redact(oauth_url)})\n")

    # W2.4: emit one file per database. The previous single-file schema.sql
    # was applied to BOTH databases, so each DB carried the other DB's full
    # (empty) table set — e.g. an empty oauth_tokens in tts_erp weakened the
    # "never query oauth_tokens from tts-erp code" guardrail (a wrong-DB
    # query returned empty rows instead of erroring).
    out_dir = Path(__file__).resolve().parent.parent

    oauth_dump = _pg_dump(oauth_url)
    (out_dir / "schema_oauth.sql").write_text(
        OAUTH_HEADER + oauth_dump, encoding="utf-8"
    )
    sys.stderr.write("# wrote schema_oauth.sql\n")

    tts_dump = _pg_dump(tts_url)
    (out_dir / "schema_tts_erp.sql").write_text(TTS_HEADER + tts_dump, encoding="utf-8")
    sys.stderr.write("# wrote schema_tts_erp.sql\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
