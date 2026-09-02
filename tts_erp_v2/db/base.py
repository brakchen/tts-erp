"""Declarative base + engine/session factory for tts_erp_v2.

Reads TTS_ERP_DB_URL from os.environ (populated from .env at app startup).
Does NOT mutate the existing public.* schema or the legacy tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Schemas we manage. Alembic uses these to create schemas before table create.
SCHEMAS: tuple[str, ...] = (
    "integration",
    "commerce",
    "procurement",
    "fulfillment",
    "after_sales",
    "finance",
    "linkage",
    "reporting",
    "security",
    "analytics",
)


class Base(DeclarativeBase):
    """Declarative base for all tts_erp_v2 models.

    Forces every ``Mapped[datetime]`` annotation to render as
    ``TIMESTAMP WITH TIME ZONE`` on PostgreSQL. V3 §14 requires this
    (time一律 timestamptz). SQLAlchemy 2.0 supports overriding the
    inferred column type per-declarative-base via ``type_annotation_map``.
    """

    type_annotation_map = {
        datetime: TIMESTAMP(timezone=True),
    }


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _resolve_db_url() -> str:
    url = os.environ.get("TTS_ERP_DB_URL")
    if not url:
        raise RuntimeError(
            "TTS_ERP_DB_URL not configured. Set it in .env or os.environ "
            "before importing tts_erp_v2.db.base."
        )
    return url


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Return a process-wide Engine. Idempotent; recreate only if url changes."""
    global _engine
    target = url or _resolve_db_url()
    if _engine is None or _engine.url.render_as_string(hide_password=False) != target:
        _engine = create_engine(target, echo=echo, future=True, pool_pre_ping=True)
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a process-wide SessionLocal. Idempotent."""
    global _SessionLocal
    if _SessionLocal is None:
        eng = engine or get_engine()
        _SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    return _SessionLocal


def reset_for_testing() -> None:
    """Drop the cached engine/sessionmaker. Used by conftest fixtures."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_scope() -> Iterator[Session]:
    """Context manager-style session helper for one-off scripts."""
    SessionLocal = get_session_factory()
    sess = SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
