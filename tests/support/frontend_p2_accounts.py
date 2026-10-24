"""TEST accounts only, after current-clone authenticated probe revalidation.

No business/browser commands, role-definition writes, cookie synthesis or seed
retry. Existing account service owns password policy and commits. The instance
before_commit hook consumes the existing launch owner, not a new DSN authority:
https://docs.sqlalchemy.org/en/20/orm/events.html#sqlalchemy.orm.SessionEvents.before_commit
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_canary import (
    VerifiedDataProbe,
    _require_child,
    require_data_probe,
)
from tts_erp_v2.accounts.service import create_user

REFUSAL = "P2 isolated account fixture refused"


@dataclass(frozen=True)
class SeedAccount:
    user_pk: int
    username: str
    role_code: str
    password: str = field(repr=False)


def seed_accounts(probe: VerifiedDataProbe) -> tuple[SeedAccount, ...]:
    """Use only the same checked Session and real canonical builtin role codes."""
    try:
        value, binding = require_data_probe(probe)
        engine = create_engine(value)
        try:
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with checked_session(binding, engine, factory) as session:
                _require_child(probe.child)

                def before_commit(_session: Session) -> None:
                    _require_child(probe.child)

                # Instance-only, not a global Session hook. Canonical create_user
                # commits internally, so check the launch at that actual seam.
                event.listen(session, "before_commit", before_commit)
                try:
                    accounts = []
                    for role in ("viewer", "operator", "admin"):
                        _require_child(probe.child)
                        username = "test_p2_" + secrets.token_hex(6) + "_" + role
                        password = "TEST_p2_9_" + secrets.token_hex(24)
                        user = create_user(session, username=username, display_name="TEST_P2_" + role,
                                           password=password, roles=[role], actor="TEST_frontend_p2")
                        _require_child(probe.child)
                        accounts.append(SeedAccount(user_pk=user.id, username=user.username,
                                                    role_code=role, password=password))
                    return tuple(accounts)
                finally:
                    event.remove(session, "before_commit", before_commit)
        finally:
            engine.dispose()
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, SQLAlchemyError):
        # No password/DB error payload, retries or manual destructive cleanup.
        raise ValueError(REFUSAL) from None
