"""FastAPI dependencies shared by /v2 routers.

- ``get_session`` yields a SQLAlchemy Session whose transaction is
  rolled back when the request finishes (handler-agnostic; works for
  read paths too — saving cost of needless commits).
- ``caller_key_hash`` reads the SHA-256 key hash that ``AuthMiddleware``
  stashed in ``request.scope`` (it persists on ``request.scope`` because
  Starlette forwards the same scope into dependencies).
- ``is_prod_shaped_db`` / ``require_destructive_guard`` /
  ``require_destructive_script_guard`` (2026-09-13) form the single
  source of truth for "is this code path about to mutate prod data,
  and if so, who authorised it?". See module docstring of the
  destructive-guard section below for the rationale and the
  incident that motivated it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session


def get_session() -> Session:
    """Yield a request-scoped ORM session; rollback at end of request."""
    from tts_erp_v2.db.base import get_session_factory

    SessionLocal = get_session_factory()
    sess = SessionLocal()
    try:
        yield sess
    finally:
        try:
            sess.rollback()
        finally:
            sess.close()


SessionDep = Annotated[Session, Depends(get_session)]


def caller_key_hash(request: Request) -> str | None:
    """Return the authenticated key hash (or None if exempt)."""
    return request.scope.get("api_key_hash")





def caller_role(request: Request) -> str | None:
    """Return the authenticated role name (or None if exempt)."""
    return request.scope.get("api_key_role")


def require_role_at_least(request: Request, min_role: str) -> None:
    """Raise 403 if the caller's role is below ``min_role``.

    Mirror of the middleware check, but raised from a dependency so
    individual endpoints can be tighter without registering a brand-new
    path class in ``required_role()``.
    """
    from tts_erp_v2.middleware.auth import ROLE_LEVEL

    level = ROLE_LEVEL.get(request.scope.get("api_key_role") or "")
    needed = ROLE_LEVEL[min_role]
    if level is None or level < needed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires {min_role}",
        )


# ─── Prod-shape destructive guard (2026-09-13) ────────────────────────
#
# Why this exists
# ---------------
# 2026-09-13 P0 incident: ``tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables``
# ran against the prod database ``tts_erp`` because the worktree's
# ``.env`` symlinked to the main repo's prod ``.env`` and the runner
# did not source ``.env.test``. ``/v2/admin/purge-plugin-data`` had
# no prod-shape guard and ``require_role_at_least("readwrite")`` was
# satisfied by any non-prod test key — the wipe blanked 14,719 rows
# of ``plugin.ad_daily`` (246 campaigns × 65 days). See
# ``tech-doc/incident-reports/2026-09-13-ad-daily-purge.md``.
#
# This module provides a SINGLE source of truth for "is this code path
# about to mutate prod data, and if so, who authorised it?". Every
# destructive entrypoint in the codebase (HTTP endpoints, scripts,
# alembic upgrades, scheduled jobs) MUST call one of the helpers below
# before issuing a DELETE / TRUNCATE / DROP statement. New destructive
# paths added without this guard are a P1 review finding.


_PROD_SHAPED_DBNAMES: frozenset[str] = frozenset({"tts_erp", "tts_erp_prod"})


def is_prod_shaped_db() -> bool:
    """True if the current ``TTS_ERP_DB_URL`` points at a prod-shape dbname.

    Fail-closed: if the env var is missing or unparseable, returns True
    so callers refuse to operate. The caller may opt back in via
    ``ALLOW_PROD_DESTRUCTIVE=1`` (or a custom env name passed to
    :func:`require_destructive_guard`).

    Examples:
        >>> os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h/tts_erp"
        >>> is_prod_shaped_db()
        True
        >>> os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h/tts_erp_v3_test"
        >>> is_prod_shaped_db()
        False
    """
    import os as _os
    from urllib.parse import urlparse as _urlparse

    db_url = _os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not db_url:
        # Unset env var → refuse. Fail-closed is intentional: a missing
        # var usually means the operator forgot to source .env.
        return True
    try:
        # postgresql+psycopg://u:p@h:port/dbname → urlparse needs plain
        # postgresql:// scheme. Strip the driver suffix.
        path = _urlparse(
            db_url.replace("postgresql+psycopg://", "postgresql://")
        ).path
        dbname = path.lstrip("/").split("?")[0]
    except Exception:  # noqa: BLE001 — defensive: bad URL = refuse.
        return True
    if not dbname:
        return True
    return (
        dbname in _PROD_SHAPED_DBNAMES
        or dbname.startswith("tts_erp_prod_")
    )


def require_destructive_guard(
    request: Request,
    *,
    op_name: str,
    allow_env: str = "ALLOW_PROD_DESTRUCTIVE",
    reason: str | None = None,
) -> None:
    """FastAPI endpoint guard: refuse destructive ops on prod-shape dbnames.

    Use at the top of any endpoint that issues DELETE / TRUNCATE /
    DROP / irreversible UPDATE. Pair with ``require_role_at_least``
    for defense-in-depth.

    Args:
        request: FastAPI Request.
        op_name: short label for the op (e.g. ``"intercept_config.delete"``).
            Used in the 403 response so operators can pinpoint which
            endpoint refused.
        allow_env: env var name that, if set to ``"1"``, opts back into
            destructive mode. Defaults to ``ALLOW_PROD_DESTRUCTIVE``.
        reason: custom refusal text. Defaults to a generic message
            that includes the dbname and opt-in env name.

    Raises:
        HTTPException(403) when running on a prod-shape dbname without
        the opt-in env var set. No-op on test/dev dbnames.

    Example:
        >>> @router.delete("/configs/{config_id}")
        >>> def delete_config(request: Request, config_id: int):
        ...     require_destructive_guard(request, op_name="intercept_config.delete")
        ...     db.execute(text("DELETE FROM ..."), {"id": config_id})
    """
    import os as _os

    if not is_prod_shaped_db():
        return  # test / dev db — proceed.

    if _os.environ.get(allow_env, "0") == "1":
        return  # explicit operator opt-in.

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=reason or (
            f"Refused: {op_name} is destructive and runs against a "
            "prod-shape dbname. Set {env}=1 in the environment to "
            "override (NOT recommended; you are about to mutate live "
            "data).".format(env=allow_env)
        ),
    )


def require_destructive_script_guard(
    *,
    script_name: str,
    confirmation: bool,
    dangerous: bool = True,
    allow_env: str = "ALLOW_PROD_DESTRUCTIVE",
) -> None:
    """Script / alembic / job guard: refuse destructive ops on prod-shape dbnames.

    Use at the top of any CLI script or scheduled job that issues
    DELETE / TRUNCATE / DROP. ``confirmation`` is the script's own
    ``--confirm`` flag (must be True to even consider destructive mode).
    ``dangerous`` distinguishes dry-run previews (False) from real
    writes (True).

    Args:
        script_name: short label for the caller (e.g.
            ``"oneoff_finance_reset"``, ``"alembic upgrade"``).
        confirmation: the script's own explicit ``--confirm`` flag.
            If False, the function allows dry-run previews on prod
            (prints what WOULD run, no writes) and exits without error.
        dangerous: True when the call will issue writes. False when
            the call is a dry-run preview.
        allow_env: env var name that, if set to ``"1"``, opts back into
            destructive mode.

    Raises:
        SystemExit(2) when running on a prod-shape dbname with
        ``confirmation=True`` and no opt-in env var. No-op on test/dev.
        Dry-runs on prod-shape dbnames are allowed and just print
        ``DRY-RUN on prod-shape db — preview only, no writes.``

    Example:
        >>> # in scripts/oneoff_finance_reset.py:
        >>> require_destructive_script_guard(
        ...     script_name="oneoff_finance_reset",
        ...     confirmation=args.confirm,
        ...     dangerous=True,
        ... )
        >>> db.execute("TRUNCATE finance.settlement_components, ...")
    """
    import os as _os
    import sys as _sys

    if not is_prod_shaped_db():
        return  # test / dev db — proceed.

    if not dangerous:
        # Dry-run preview is always safe to print, even on prod.
        print(
            f"[{script_name}] DRY-RUN on prod-shape db — preview only, "
            "no writes will be performed."
        )
        return

    if _os.environ.get(allow_env, "0") == "1":
        print(
            f"[{script_name}] !!! {allow_env}=1 !!! destructive op on "
            "prod-shape db. Proceeding — operator takes responsibility."
        )
        return

    print(
        f"\n[{script_name}] REFUSED: --confirm on prod-shape dbname.\n"
        f"             Set {allow_env}=1 to override (NOT recommended;\n"
        "             you are about to mutate live data).\n"
    )
    _sys.exit(2)
