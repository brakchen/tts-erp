"""Request-level consistent read snapshot ownership."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability._types import (
    SnapshotIsolationUnavailable,
)


@contextmanager
def consistent_read_snapshot(session: Session) -> Iterator[datetime]:
    """Enter one read-only repeatable-read transaction for a calculation.

    The request-scoped session is rolled back and closed by ``get_session``.  This
    module only chooses the transaction characteristics and supplies the one
    ``calculated_at`` shared by result, overview, basis, and evidence.

    A caller-owned active PostgreSQL transaction is accepted only when it is
    already read-only repeatable-read; otherwise the module fails before reading
    profitability facts.  Normal HTTP requests enter here before the first SQL
    statement.
    """

    calculated_at = datetime.now(UTC)
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        if not session.in_transaction():
            session.connection(
                execution_options={"isolation_level": "REPEATABLE READ"}
            )
            session.execute(text("SET TRANSACTION READ ONLY"))
        else:
            isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
            read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
            if isolation != "repeatable read" or read_only != "on":
                raise SnapshotIsolationUnavailable(
                    "active transaction must be read-only repeatable-read"
                )
    yield calculated_at
