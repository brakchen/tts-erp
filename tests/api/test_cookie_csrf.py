from __future__ import annotations

from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request

from tts_erp_v2.api.deps import require_cookie_csrf
from tts_erp_v2.api.v2 import admin, config
from tts_erp_v2.db import base

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


@pytest.mark.parametrize("command", ["register", "patch", "enum-put", "enum-delete"])
def test_cookie_command_requires_header_before_any_database_access(monkeypatch, command: str) -> None:
    touched: list[str] = []

    def forbidden_engine(*args, **kwargs):
        touched.append("engine")
        raise AssertionError("TEST unexpected database access before CSRF")

    class NoDatabase:
        def __getattr__(self, name):
            touched.append(name)
            raise AssertionError("TEST unexpected session access before CSRF")

    monkeypatch.setattr(base, "get_engine", forbidden_engine)
    request = Request({
        "type": "http", "method": "POST", "path": "/TEST_csrf",
        "headers": [], "auth_method": "cookie", "api_key_role": "admin",
    })
    with pytest.raises(HTTPException) as caught:
        if command == "register":
            admin.register_shop(request, admin.ShopRegisterBody(shop_id="8800000000000000099"))
        elif command == "patch":
            admin.update_shop(request, 1, admin.ShopUpdateBody(account_name="TEST_updated"))
        elif command == "enum-put":
            config.upsert_enum_map(
                config.EnumMapUpsert(enum_type="TEST_type", enum_value="TEST_value", label_zh="TEST_label"),
                request, cast(Session, NoDatabase()),
            )
        else:
            config.delete_enum_map(1, request, cast(Session, NoDatabase()))
    assert caught.value.status_code == 403
    assert "X-Requested-With" in str(caught.value.detail)
    assert touched == []


@pytest.mark.parametrize("source", ["api_key", "off", None])
def test_non_cookie_source_does_not_invent_csrf_requirement(source: str | None) -> None:
    request = Request({
        "type": "http", "method": "POST", "path": "/TEST_csrf",
        "headers": [(b"cookie", b"TEST_invalid_cookie")], "auth_method": source,
    })
    require_cookie_csrf(request)


@pytest.mark.parametrize("value", [None, "wrong", "TTS-ERP", "tts-erp ", "tts-erp"])
def test_cookie_header_value_is_exact(value: str | None) -> None:
    headers = [] if value is None else [(b"x-requested-with", value.encode())]
    request = Request({
        "type": "http", "method": "POST", "path": "/TEST_csrf",
        "headers": headers, "auth_method": "cookie",
    })
    if value == "tts-erp":
        require_cookie_csrf(request)
    else:
        with pytest.raises(HTTPException) as caught:
            require_cookie_csrf(request)
        assert caught.value.status_code == 403
