"""Normalize a SQLAlchemy-style postgres URL for raw psycopg3.

The project standardises on ``TTS_ERP_DB_URL`` / ``OAUTH_DB_URL`` in
``.env``. v2 code consumes these through SQLAlchemy, which accepts
either ``postgresql://...`` or ``postgresql+psycopg://...``. Raw
``psycopg.connect()`` (the legacy ``sync_cron.py``, the ``api_keys.py``
CLI, the ``analytics_sync.pg_repositories`` service, the one-off
``verify_db.py`` script, etc.) only accepts the plain
``postgresql://`` scheme — ``psycopg3``'s conninfo parser raises
``ProgrammingError: missing "=" after "postgresql+psycopg://..."``.

Every script that hands the URL to raw psycopg must run it through
:func:`normalize_db_url` first.

The fix is a 5-line wrapper rather than a 2-line ``.replace("+psycopg",
"")`` because the dialect can be ``+psycopg`` (psycopg 3) or
``+asyncpg`` or any third-party driver — and the suffix can appear
between the scheme and ``://`` (``postgresql+foo://...``) without
showing up in the scheme prefix.

Kept in ``scripts/`` (empty ``__init__.py``) so importing it is
free of the v2 model/SQLAlchemy boot cost that ``tts_erp_v2.db``
carries.
"""
from __future__ import annotations


def normalize_db_url(url: str) -> str:
    """Strip the SQLAlchemy ``+dialect`` suffix from a postgres URL.

    >>> normalize_db_url("postgresql+psycopg://u:p@h:5432/d")
    'postgresql://u:p@h:5432/d'
    >>> normalize_db_url("postgresql+asyncpg://u:p@h/d")
    'postgresql://u:p@h/d'
    >>> normalize_db_url("postgresql://u:p@h/d")
    'postgresql://u:p@h/d'
    >>> normalize_db_url("postgresql+psycopg2://legacy-host/x")
    'postgresql://legacy-host/x'
    """
    if not url or not url.startswith("postgresql"):
        return url
    scheme_end = url.find("://")
    if scheme_end <= 0:
        return url
    scheme = url[:scheme_end]
    if "+" in scheme:
        return "postgresql://" + url[scheme_end + 3 :]
    return url


__all__ = ["normalize_db_url"]
