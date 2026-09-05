"""Watermark R/W for sync-worker jobs.

Reads / writes ``integration.sync_cursors`` (V3 §5). The cursor table
is the single source of truth for incremental jobs — no in-process
cache, every call round-trips through SQLAlchemy.

Two value shapes are supported:

* ``cursor_epoch_ms`` — integer epoch milliseconds, used by jobs that
  sync by update_time (orders / after-sales / finance).
* ``cursor_value`` — opaque string, used by jobs that paginate via
  upstream-supplied next_page_token (logistics).

Each row is uniquely keyed by ``(job_name, scope)``. ``scope`` is
typically a shop_id or the literal ``"*"`` for system-wide jobs.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models import SyncCursor


def get_cursor(
    session: Session,
    *,
    job_name: str,
    scope: str,
) -> int | str | None:
    """Return the stored cursor, preferring epoch ms over string.

    Returns None if no row exists. Returns the integer when the row
    has a ``cursor_epoch_ms`` value, else the ``cursor_value`` string.
    """
    row = session.execute(
        select(SyncCursor).where(
            SyncCursor.job_name == job_name,
            SyncCursor.scope == scope,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.cursor_epoch_ms is not None:
        return row.cursor_epoch_ms
    return row.cursor_value


def set_cursor(
    session: Session,
    *,
    job_name: str,
    scope: str,
    cursor_value: str | None = None,
    cursor_epoch_ms: int | None = None,
) -> None:
    """Upsert the cursor row for ``(job_name, scope)``.

    Caller is responsible for ``session.commit()`` — kept consistent
    with the rest of the codebase, which lets the caller batch the
    cursor write with related business-row writes.

    At least one of ``cursor_value`` / ``cursor_epoch_ms`` must be
    supplied; otherwise the row would carry no signal and there's no
    point writing it.
    """
    if cursor_value is None and cursor_epoch_ms is None:
        raise ValueError(
            "set_cursor requires at least one of cursor_value or cursor_epoch_ms"
        )

    insert_values: dict = {
        "job_name": job_name,
        "scope": scope,
        "cursor_value": cursor_value,
        "cursor_epoch_ms": cursor_epoch_ms,
    }
    insert_stmt = pg_insert(SyncCursor).values(**insert_values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["job_name", "scope"],
        set_={
            "cursor_value": cursor_value,
            "cursor_epoch_ms": cursor_epoch_ms,
            # 关键修复:无新数据时 cursor 值不变,必须 bump updated_at
            # 否则 staleness 监控误报("假死锁"),ADR-0002 §3.3 提到的就是这个
            "updated_at": text("now()"),
        },
    )
    session.execute(upsert_stmt)


__all__ = ["get_cursor", "set_cursor"]
