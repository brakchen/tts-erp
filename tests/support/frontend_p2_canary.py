"""Current-clone authenticated data readback, NOT traffic canary/rollout.

Only TEST EnumMap + readonly API-key facts are inserted, after fresh child and
actual parent-session identity gates. No users/login/OAuth/business commands or
browser write capability is provided. The isolated wrapper owns DB teardown;
this fixture adds no destructive cleanup or production override.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
from dataclasses import dataclass, field

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from scripts._test_db_connections import RunBinding, _process_state
from tests.support.frontend_p2_binding import (
    NONCE_HEADER,
    checked_session,
    current_binding,
    verify_attestation,
)
from tests.support.frontend_p2_live import VerifiedChild

REFUSAL = "P2 isolated data verification refused"


@dataclass(frozen=True)
class VerifiedDataProbe:
    child: VerifiedChild
    prefix: str
    enum_pk: int
    enum_type: str
    enum_value: str
    label_zh: str
    key_name: str
    readonly_key: str = field(repr=False)


def _require_child(child: VerifiedChild) -> tuple[str, RunBinding]:
    child.require_live()
    value, binding = current_binding()
    parent, start = _process_state(child.pid)
    if (child.binding != binding or parent != os.getpid() or start != child.start
            or not 0 < child.port < 65536 or child.port == 9877
            or child.base != f"http://127.0.0.1:{child.port}"):
        raise ValueError(REFUSAL)
    return value, binding


def _bound_json(child: VerifiedChild, path: str, headers: dict[str, str] | None = None) -> object:
    _require_child(child)
    connection = http.client.HTTPConnection("127.0.0.1", child.port, timeout=5)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read(1_048_577)
        verify_attestation(child.attestation, child.binding, nonce=child.nonce,
                           child_pid=child.pid, child_start=child.start,
                           http_nonce=response.getheader(NONCE_HEADER))
        if response.status != 200 or len(body) > 1_048_576:
            raise ValueError(REFUSAL)
        payload = json.loads(body)
        _require_child(child)
        return payload
    finally:
        connection.close()


def verify_probe_payload(expected: dict[str, str | int], payload: object) -> None:
    if isinstance(payload, list):
        matches = [row for row in payload if isinstance(row, dict) and all(
            type(row.get(key)) is type(value) and row[key] == value for key, value in expected.items()
        )]
        if len(matches) == 1:
            return
    raise ValueError(REFUSAL)


def seed_and_check_canary(child: VerifiedChild, *, prefix: str = "") -> VerifiedDataProbe:
    """Readback only. Identity/readiness alone must not bypass any step here."""
    try:
        if prefix not in ("", "/tts"):
            raise ValueError(REFUSAL)
        _require_child(child)
        health = _bound_json(child, prefix + "/healthz")
        if not isinstance(health, dict) or health.get("auth_mode") != "enforce":
            raise ValueError(REFUSAL)
        value, binding = _require_child(child)
        engine = create_engine(value)
        try:
            from tts_erp_v2.db.models.config import EnumMap
            from tts_erp_v2.db.models.security import ApiKey

            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with checked_session(binding, engine, factory) as session:
                _require_child(child)
                # This is the SAME session whose actual bind and DB were checked.
                marker = "TEST_P2_" + secrets.token_hex(16)
                readonly_key = "ttserp_readonly_TEST_P2_" + secrets.token_hex(24)
                row = EnumMap(enum_type=marker, enum_value=marker, label_zh=marker, sort_order=0)
                key = ApiKey(key_hash=hashlib.sha256(readonly_key.encode()).hexdigest(),
                             key_prefix=readonly_key[:16], name=marker, role="readonly", status="active")
                session.add_all([row, key])
                session.flush()
                expected: dict[str, str | int] = {"id": row.id, "enum_type": marker,
                                                "enum_value": marker, "label_zh": marker, "sort_order": 0}
                _require_child(child)
                session.commit()
        finally:
            engine.dispose()
        # First authenticated data request follows successful child/parent identity;
        # API-key lookup may update last_used_at, so it is not storage-pure.
        payload = _bound_json(child, prefix + "/v2/config/enum-map/list",
                              {"Authorization": "Bearer " + readonly_key})
        verify_probe_payload(expected, payload)
        _require_child(child)
        return VerifiedDataProbe(child=child, prefix=prefix, enum_pk=int(expected["id"]),
                                 enum_type=marker, enum_value=marker, label_zh=marker,
                                 key_name=marker, readonly_key=readonly_key)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError,
            http.client.HTTPException, SQLAlchemyError):
        # No retry, no raw payload/credentials/DB exception message in diagnostics.
        raise ValueError(REFUSAL) from None
