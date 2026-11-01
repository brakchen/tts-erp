"""Finish test arrangement before entering a job that owns its transactions.

The canonical db_session fixture joins an outer rollback transaction through
SAVEPOINTs. Committing arrangement releases only that fixture SAVEPOINT; it
does not escape test isolation. Evaluate arguments before this boundary so
expired seed objects cannot autobegin a transaction inside the job invocation.
New transaction-boundary tests use fresh Sessions directly instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.orm import Session

Result = TypeVar("Result")


def invoke_seeded_job(
    function: Callable[..., Result], session: Session, *args: Any, **kwargs: Any
) -> Result:
    session.commit()
    return function(session, *args, **kwargs)
