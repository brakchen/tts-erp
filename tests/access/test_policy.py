from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.access import (
    AccessEffect,
    AccessRequest,
    AuthMode,
    Role,
)
from tts_erp_v2.access._access import evaluate_access
from tts_erp_v2.access._credentials import clear_credential_cache
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.middleware.rate_limit import reset_shared


@pytest.fixture()
def readonly_key(db_engine) -> Iterator[str]:
    plaintext = "TEST_access_policy_readonly_key"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    clear_credential_cache()
    with Session(db_engine) as session:
        session.execute(delete(ApiKey).where(ApiKey.key_hash == key_hash))
        session.add(
            ApiKey(
                key_hash=key_hash,
                key_prefix=plaintext[:16],
                name="TEST_access_policy_readonly",
                role="readonly",
                status="active",
            )
        )
        session.commit()
    yield plaintext
    with Session(db_engine) as session:
        session.execute(delete(ApiKey).where(ApiKey.key_hash == key_hash))
        session.commit()
    clear_credential_cache()


def test_unknown_route_fails_closed_for_anonymous_request() -> None:
    reset_shared(limit=100)
    try:
        decision = asyncio.run(
            evaluate_access(
                AccessRequest(
                    method="GET",
                    route_path="/v2/future-endpoint",
                    accepts_html=False,
                    client_ip="127.0.0.1",
                ),
                mode=AuthMode.ENFORCE,
            )
        )
    finally:
        reset_shared()

    assert decision.effect is AccessEffect.DENY
    assert decision.required_role is Role.ADMIN
    assert decision.status == 401
    assert decision.detail is not None
    assert decision.detail.startswith("missing bearer token")


def test_bearer_principal_allows_readonly_route(readonly_key: str) -> None:
    decision = asyncio.run(
        evaluate_access(
            AccessRequest(
                method="GET",
                route_path="/v2/commerce/sales-orders",
                accepts_html=False,
                client_ip="127.0.0.1",
                bearer_key=readonly_key,
            ),
            mode=AuthMode.ENFORCE,
        )
    )

    assert decision.effect is AccessEffect.ALLOW
    assert decision.grant.role is Role.READONLY
    assert decision.grant.auth_method == "bearer"
    assert decision.grant.key_hash is not None


def test_auth_store_failure_is_unavailable_in_enforce(monkeypatch) -> None:
    from tts_erp_v2.access import _credentials

    def _fail_lookup(_key_hash: str):
        raise RuntimeError("TEST database unavailable")

    clear_credential_cache()
    monkeypatch.setattr(_credentials, "_db_lookup", _fail_lookup)

    decision = asyncio.run(
        evaluate_access(
            AccessRequest(
                method="GET",
                route_path="/v2/commerce/sales-orders",
                accepts_html=False,
                client_ip="127.0.0.1",
                bearer_key="TEST_unavailable_key",
            ),
            mode=AuthMode.ENFORCE,
        )
    )

    assert decision.effect is AccessEffect.UNAVAILABLE
    assert decision.status == 503
    assert decision.detail == "auth store unavailable"


def test_auth_store_failure_is_shadow_allow(monkeypatch) -> None:
    from tts_erp_v2.access import _credentials

    def _fail_lookup(_key_hash: str):
        raise RuntimeError("TEST database unavailable")

    clear_credential_cache()
    monkeypatch.setattr(_credentials, "_db_lookup", _fail_lookup)

    decision = asyncio.run(
        evaluate_access(
            AccessRequest(
                method="GET",
                route_path="/v2/commerce/sales-orders",
                accepts_html=True,
                client_ip="127.0.0.1",
                bearer_key="TEST_unavailable_key",
            ),
            mode=AuthMode.SHADOW,
        )
    )

    assert decision.effect is AccessEffect.SHADOW_ALLOW
    assert decision.grant.bypass is True
    assert decision.status == 503


def test_bypass_grant_allows_handler_role_gate() -> None:
    from tts_erp_v2.access import AccessGrant

    grant = AccessGrant(mode=AuthMode.OFF, bypass=True)

    assert grant.allows(Role.ADMIN) is True
