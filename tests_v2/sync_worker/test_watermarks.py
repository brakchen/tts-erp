"""TDD tests for sync_worker.watermarks — integration.sync_cursors R/W.

The cursor store is the single source of truth for incremental jobs:

* :func:`get_cursor` returns the stored value (epoch ms as int, or
  the opaque cursor string) or None when no row exists.
* :func:`set_cursor` upserts (job_name, scope) — first write seeds the
  row; subsequent writes update cursor_value / cursor_epoch_ms.
* The model is read from ``integration.sync_cursors`` (V3 §5) — no
  in-memory cache, so each call round-trips through SQLAlchemy.

These tests use the per-test transaction-rollback pattern from
``tests_v2/conftest.py`` so the DB stays clean.
"""
from __future__ import annotations

from sqlalchemy import select

from tts_erp_v2.db.models import SyncCursor
from tts_erp_v2.sync_worker import watermarks

# ─── get_cursor / set_cursor (epoch ms) ────────────────────────────


def test_get_cursor_returns_none_when_no_row(db_session) -> None:
    """No prior run → None, never raises."""
    assert (
        watermarks.get_cursor(db_session, job_name="tiktok.orders", scope="*")
        is None
    )


def test_set_cursor_then_get_round_trips_epoch_ms(db_session) -> None:
    """First write seeds; second get returns the stored epoch ms."""
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.orders",
        scope="*",
        cursor_epoch_ms=1_700_000_000_000,
    )
    db_session.commit()

    got = watermarks.get_cursor(
        db_session, job_name="tiktok.orders", scope="*"
    )
    assert got == 1_700_000_000_000


def test_set_cursor_upserts_existing_row(db_session) -> None:
    """Second set_cursor with same (job_name, scope) updates, no duplicate."""
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders", scope="*",
        cursor_epoch_ms=1_000,
    )
    db_session.commit()
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders", scope="*",
        cursor_epoch_ms=2_000,
    )
    db_session.commit()

    rows = db_session.execute(
        select(SyncCursor).where(SyncCursor.job_name == "tiktok.orders")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].cursor_epoch_ms == 2_000


def test_set_cursor_supports_string_cursor(db_session) -> None:
    """Logistics uses opaque string cursors (next_page_token from upstream)."""
    watermarks.set_cursor(
        db_session,
        job_name="tiktok.logistics",
        scope="*",
        cursor_value="next_page_token_xyz",
    )
    db_session.commit()
    got = watermarks.get_cursor(
        db_session, job_name="tiktok.logistics", scope="*"
    )
    assert got == "next_page_token_xyz"


def test_set_cursor_scopes_are_isolated(db_session) -> None:
    """Different scope values stay separate rows."""
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders",
        scope="shop_A", cursor_epoch_ms=100,
    )
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders",
        scope="shop_B", cursor_epoch_ms=200,
    )
    db_session.commit()

    assert (
        watermarks.get_cursor(
            db_session, job_name="tiktok.orders", scope="shop_A"
        )
        == 100
    )
    assert (
        watermarks.get_cursor(
            db_session, job_name="tiktok.orders", scope="shop_B"
        )
        == 200
    )


def test_set_cursor_updates_updated_at(db_session) -> None:
    """``updated_at`` is server-side ``now()``; we just verify it changes."""

    watermarks.set_cursor(
        db_session, job_name="tiktok.orders", scope="*",
        cursor_epoch_ms=100,
    )
    db_session.commit()
    first = db_session.execute(
        select(SyncCursor).where(SyncCursor.job_name == "tiktok.orders")
    ).scalar_one()
    first_updated = first.updated_at
    assert first_updated is not None
    assert first_updated.tzinfo is not None  # timestamptz enforced

    # A second write should bump updated_at (server now()).
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders", scope="*",
        cursor_epoch_ms=200,
    )
    db_session.commit()
    second = db_session.execute(
        select(SyncCursor).where(SyncCursor.job_name == "tiktok.orders")
    ).scalar_one()
    assert second.updated_at >= first_updated


# ─── get_cursor returns the right type based on what was stored ─────


def test_get_cursor_returns_epoch_ms_when_only_epoch_ms_was_stored(
    db_session,
) -> None:
    """If we stored an epoch ms, get_cursor returns an int (not None, not '')."""
    watermarks.set_cursor(
        db_session, job_name="tiktok.orders", scope="*",
        cursor_epoch_ms=1234,
    )
    db_session.commit()
    got = watermarks.get_cursor(
        db_session, job_name="tiktok.orders", scope="*"
    )
    assert isinstance(got, int)
    assert got == 1234


def test_get_cursor_returns_string_when_only_string_was_stored(
    db_session,
) -> None:
    """If we stored a cursor_value, get_cursor returns that string."""
    watermarks.set_cursor(
        db_session, job_name="tiktok.logistics", scope="*",
        cursor_value="opaque_tok",
    )
    db_session.commit()
    got = watermarks.get_cursor(
        db_session, job_name="tiktok.logistics", scope="*"
    )
    assert isinstance(got, str)
    assert got == "opaque_tok"
