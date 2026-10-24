"""Test-only child provenance; never an alternative DSN parser or write grant.

RunBinding/proc identity remain owned by scripts._test_db_connections. Private
file I/O follows that authority's bounded O_NOFOLLOW/O_NONBLOCK pattern.
SQLAlchemy binding API: https://docs.sqlalchemy.org/en/20/orm/session_api.html
"""
from __future__ import annotations

import hmac
import os
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from scripts._test_db_connections import RunBinding, _process_state, load_run_binding

REFUSAL = "P2 isolated child identity refused"
NONCE_HEADER = "x-tts-test-launch"


class ChildAttestation(BaseModel):
    """Credential-free strict wire DTO, not a business success/status channel."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: int = Field(ge=1, le=1)
    run_id: str = Field(alias="runId", pattern=r"^[0-9a-f]{64}$")
    launch_nonce: str = Field(alias="launchNonce", pattern=r"^[0-9a-f]{64}$")
    child_pid: int = Field(alias="childPid", gt=0)
    child_start: str = Field(alias="childStart", pattern=r"^[0-9]+$")
    parent_pid: int = Field(alias="parentPid", gt=0)
    parent_start: str = Field(alias="parentStart", pattern=r"^[0-9]+$")
    connection_digest: str = Field(alias="connectionDigest", pattern=r"^[0-9a-f]{64}$")
    actual_db: str = Field(alias="actualDb")


def current_binding() -> tuple[str, RunBinding]:
    try:
        value = os.environ["TTS_ERP_DB_URL_TEST"]
        return value, load_run_binding(value)
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError(REFUSAL) from None


def validate_application(
    binding: RunBinding, engine: Engine, factory: sessionmaker[Session],
) -> str:
    """Validate actual engine AND API session routes before the first connect.

    Nonempty per-entity binds are unsupported, not guessed. This checks the
    canonical API factory, rather than constructing a reassuring second engine.
    """
    try:
        def check(candidate: object) -> None:
            if not isinstance(candidate, Engine):
                raise TypeError(REFUSAL)
            binding.validate_url(candidate.url.render_as_string(hide_password=False))

        check(engine)
        if factory.kw.get("binds"):
            raise ValueError(REFUSAL)
        check(factory.kw["bind"])
        with factory() as session:
            check(session.get_bind())
            binding.verify_database(session.scalar(text("SELECT current_database()")))
        return binding.run_db
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, SQLAlchemyError):
        raise ValueError(REFUSAL) from None


def private_directory(path: Path) -> None:
    meta = path.parent.lstat()
    if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid()
            or stat.S_IMODE(meta.st_mode) & 0o077):
        raise ValueError(REFUSAL)


def require_nonce(nonce: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise ValueError(REFUSAL)


def write_attestation(path: Path, binding: RunBinding, nonce: str) -> None:
    """Only the test factory calls this, after checking its actual DB session."""
    try:
        private_directory(path)
        require_nonce(nonce)
        pid = os.getpid()
        parent, child_start = _process_state(pid)
        _, parent_start = _process_state(parent)
        payload = ChildAttestation.model_validate({
            "version": 1, "runId": binding.run_id, "launchNonce": nonce,
            "childPid": pid, "childStart": child_start,
            "parentPid": parent, "parentStart": parent_start,
            "connectionDigest": binding.connection_digest, "actualDb": binding.run_db,
        })
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload.model_dump_json(by_alias=True))
            stream.flush()
            os.fsync(stream.fileno())
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError(REFUSAL) from None


def verify_attestation(
    path: Path, binding: RunBinding, *, nonce: str, child_pid: int,
    child_start: str, http_nonce: str | None,
) -> ChildAttestation:
    """Compare against this Popen PID/start and HTTP launch, not the file alone."""
    try:
        # Revoked proofs and changed wrapper ancestry cannot reuse a cached receipt.
        _, fresh = current_binding()
        if fresh != binding:
            raise ValueError(REFUSAL)
        private_directory(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                    or stat.S_IMODE(meta.st_mode) & 0o077):
                raise ValueError(REFUSAL)
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError(REFUSAL)
        receipt = ChildAttestation.model_validate_json(raw)
        parent, actual_start = _process_state(child_pid)
        _, parent_start = _process_state(os.getpid())
        require_nonce(nonce)
        if (receipt.run_id != binding.run_id or receipt.actual_db != binding.run_db
                or receipt.child_pid != child_pid or receipt.child_start != child_start
                or actual_start != child_start or parent != os.getpid()
                or receipt.parent_pid != os.getpid() or receipt.parent_start != parent_start
                or not hmac.compare_digest(receipt.connection_digest, binding.connection_digest)
                or not hmac.compare_digest(receipt.launch_nonce, nonce)
                or http_nonce is None or not hmac.compare_digest(http_nonce, nonce)):
            raise ValueError(REFUSAL)
        return receipt
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError):
        # Never print malformed payloads, connection configuration or library messages.
        raise ValueError(REFUSAL) from None
