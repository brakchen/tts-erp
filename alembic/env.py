"""Alembic environment.

Reads TTS_ERP_DB_URL from .env (loaded explicitly; we don't trust
configparser interpolation because the password may contain '%').
Loads every tts_erp_v2 model via load_all_metadata() so autogenerate sees
all 35 tables. Public schema and legacy tables are explicitly ignored.
"""
from __future__ import annotations

import configparser
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Project root on path so we can import tts_erp_v2.db.models ───────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# ── Models registration ──────────────────────────────────────────────
# Importing models registers them on Base.metadata. We do this BEFORE
# alembic touches the config so the metadata is ready when context.configure
# needs it.
from tts_erp_v2.db.models import load_all_metadata  # noqa: E402

# ── alembic config ───────────────────────────────────────────────────
# configparser's default interpolation chokes on passwords containing '%'.
# Install a no-op interpolation handler before we set sqlalchemy.url from env.
class _NoOpInterpolation(configparser.BasicInterpolation):
    def before_set(self, parser, section, option, value):  # type: ignore[override]
        return value
    def before_get(self, parser, section, option, value, defaults):  # type: ignore[override]
        return value


config = context.config
if hasattr(config, "file_config") and config.file_config is not None:
    try:
        config.file_config._interpolation = _NoOpInterpolation()  # type: ignore[attr-defined]
    except Exception:
        pass

# Override sqlalchemy.url from env BEFORE engine_from_config reads it.
# Force the +psycopg driver since the .env URL doesn't specify a driver
# and psycopg2 isn't installed.
_db_url = os.environ.get("TTS_ERP_DB_URL")
if not _db_url:
    raise RuntimeError(
        "TTS_ERP_DB_URL not set. alembic env.py requires it; "
        "load .env or export it before running."
    )
if _db_url.startswith("postgresql://") and "+psycopg" not in _db_url:
    _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://"):]
config.set_main_option("sqlalchemy.url", _db_url)
# Also patch the configparser-stored value directly, since
# engine_from_config reads via get_section which reads file_config fresh.
config.file_config.set("alembic", "sqlalchemy.url", _db_url)

# Logger config from original ini (re-applied because we may have stomped on it).
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

target_metadata = load_all_metadata()

# ── Filters ──────────────────────────────────────────────────────────
OWNED_SCHEMAS = (
    "integration",
    "commerce",
    "procurement",
    "fulfillment",
    "after_sales",
    "finance",
    "linkage",
    "reporting",
    "security",
)


def include_object(object_, name, type_, reflected, compare_to):
    """Only manage objects in the nine tts_erp_v2 schemas."""
    if type_ == "table":
        schema = getattr(object_, "schema", None)
        if schema is None or schema in OWNED_SCHEMAS:
            return True
        return False
    if type_ in {"index", "unique_constraint", "foreign_key_constraint"}:
        tbl = getattr(object_, "table", None)
        if tbl is not None:
            schema = getattr(tbl, "schema", None)
            if schema not in OWNED_SCHEMAS:
                return False
    return True


def run_migrations_offline() -> None:
    """Run migrations without DB connection (emits SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("TTS_ERP_DB_URL not configured; cannot run alembic.")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
