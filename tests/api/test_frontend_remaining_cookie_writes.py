"""Entry-point guard coverage; real cookie/SQL workflows live in the live API tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import HTTPException, Request

from tts_erp_v2.api.deps import require_cookie_csrf
from tts_erp_v2.api.v2 import config, users

pytestmark = pytest.mark.domain_frontend

_BODY = object()
_KEY = "test_cookie_guard"
_CASES = (
    (config.create_runtime_config_item, "sess", {"body": _BODY}),
    (config.save_runtime_config_draft, "sess", {"config_key": _KEY, "body": _BODY}),
    (config.publish_runtime_config_item, "sess", {"config_key": _KEY, "body": _BODY}),
    (config.rollback_runtime_config_item, "sess", {"config_key": _KEY, "body": _BODY}),
    (config.retire_runtime_config_item, "sess", {"config_key": _KEY}),
    (config.restore_runtime_config_item, "sess", {"config_key": _KEY}),
    (config.save_runtime_config_secret, "sess", {"name": _KEY, "body": _BODY}),
    (config.retire_runtime_config_secret, "sess", {"name": _KEY}),
    (config.restore_runtime_config_secret, "sess", {"name": _KEY}),
    (users.create_user, "session", {"body": _BODY}),
    (users.patch_user, "session", {"user_id": 1, "body": _BODY}),
    (users.reset_password, "session", {"user_id": 1, "body": _BODY}),
    (users.revoke_one_session, "session", {"user_id": 1, "session_id": 1}),
    (users.revoke_all_sessions, "session", {"user_id": 1}),
    (users.create_role, "session", {"body": _BODY}),
    (users.patch_role, "session", {"code": "TEST_guard", "body": _BODY}),
    (users.delete_role, "session", {"code": "TEST_guard"}),
)


def _request(method: str, header: bytes) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/TEST_cookie_guard",
            "headers": [(b"x-requested-with", header)] if header else [],
            "auth_method": method,
            "api_key_role": "admin",
        }
    )


@pytest.mark.parametrize("header", [b"", b"wrong"], ids=["missing", "wrong"])
@pytest.mark.parametrize(
    ("handler", "session_argument", "arguments"),
    _CASES,
    ids=[case[0].__name__ for case in _CASES],
)
def test_cookie_mutations_reject_before_payload_or_storage(
    handler: Callable[..., object],
    session_argument: str,
    arguments: dict[str, object],
    header: bytes,
) -> None:
    # Deliberately unusable payload/storage: no validation bypass or fake DB.
    # Reaching either instead of the existing guard must fail this unit check.
    with pytest.raises(HTTPException) as failure:
        handler(
            **arguments,
            **{session_argument: object(), "request": _request("cookie", header)},
        )
    assert failure.value.status_code == 403
    assert failure.value.detail == (
        "cookie-authenticated mutations require X-Requested-With: tts-erp"
    )


@pytest.mark.parametrize("method", ["api_key", "bypass"])
@pytest.mark.parametrize("header", [b"", b"wrong"])
def test_shared_guard_preserves_non_cookie_behavior(method: str, header: bytes) -> None:
    # This checks the guard contract, not authentication of a machine client.
    require_cookie_csrf(_request(method, header))


def test_read_only_schema_preview_remains_available_without_cookie_write_header() -> None:
    result = config.preview_runtime_config_schema(
        config.RuntimeSchemaPreview(payload={"TEST_value": 1}), _request("cookie", b"")
    )
    assert result["jsonSchema"]["type"] == "object"
