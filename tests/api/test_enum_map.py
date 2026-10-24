from __future__ import annotations

from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session
from starlette.requests import Request

from tts_erp_v2.api import deps
from tts_erp_v2.api.v2 import config
from tts_erp_v2.db.models.config import EnumMap

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]
ENUM_TYPE = "TEST_P2_ENUM_CSRF"
BODY = {"enum_type": ENUM_TYPE, "enum_value": "TEST_value", "label_zh": "TEST_标签", "sort_order": 2}


@pytest.fixture(autouse=True)
def cleanup_owned_enums(db_engine):
    yield
    with Session(db_engine) as session:
        session.execute(delete(EnumMap).where(EnumMap.enum_type == ENUM_TYPE))
        session.commit()


def _create(api_client, admin_key) -> int:
    result = api_client.put(
        "/v2/config/enum-map", headers={"Authorization": f"Bearer {admin_key}"}, json=BODY,
    )
    assert result.status_code == 200
    return result.json()["id"]


def _login(api_client, ua_user_factory, role: str = "admin") -> None:
    user = ua_user_factory("test_ua_p2_enum", roles=(role,))
    response = api_client.post(
        "/v2/auth/login", json={"username": user.username, "password": user.password},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("header", [None, "wrong"])
def test_cookie_enum_write_refuses_missing_or_wrong_header(
    api_client, admin_key, ua_user_factory, method: str, header: str | None,
) -> None:
    row_id = _create(api_client, admin_key)
    _login(api_client, ua_user_factory)
    headers = {} if header is None else {"X-Requested-With": header}
    if method == "PUT":
        response = api_client.put("/v2/config/enum-map", headers=headers, json={**BODY, "label_zh": "TEST_changed"})
    else:
        response = api_client.delete(f"/v2/config/enum-map/{row_id}", headers=headers)
    assert response.status_code == 403
    assert "X-Requested-With" in response.json()["detail"]
    rows = api_client.get("/v2/config/enum-map/list").json()
    row = next(item for item in rows if item["id"] == row_id)
    assert row["label_zh"] == BODY["label_zh"]


def test_cookie_enum_crud_with_header_persists_and_repeated_delete_is_404(
    api_client, ua_user_factory,
) -> None:
    _login(api_client, ua_user_factory)
    headers = {"x-requested-with": "tts-erp"}
    first = api_client.put("/v2/config/enum-map", headers=headers, json=BODY)
    assert first.status_code == 200 and first.json()["action"] == "created"
    row_id = first.json()["id"]
    updated = api_client.put("/v2/config/enum-map", headers=headers, json={**BODY, "label_zh": "TEST_updated"})
    assert updated.status_code == 200
    assert updated.json() == {"id": row_id, "action": "updated"}
    rows = api_client.get("/v2/config/enum-map/list").json()
    assert next(item for item in rows if item["id"] == row_id)["label_zh"] == "TEST_updated"
    deleted = api_client.delete(f"/v2/config/enum-map/{row_id}", headers=headers)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": row_id}
    assert api_client.delete(f"/v2/config/enum-map/{row_id}", headers=headers).status_code == 404


@pytest.mark.parametrize("key_fixture", ["readonly_key", "readwrite_key"])
def test_enum_write_remains_admin_only(api_client, request, key_fixture: str) -> None:
    key = request.getfixturevalue(key_fixture)
    response = api_client.put(
        "/v2/config/enum-map", headers={"Authorization": f"Bearer {key}", "X-Requested-With": "tts-erp"}, json=BODY,
    )
    assert response.status_code == 403 and response.json()["detail"] == "requires admin"
    deleted = api_client.delete(
        "/v2/config/enum-map/999999999",
        headers={"Authorization": f"Bearer {key}", "X-Requested-With": "tts-erp"},
    )
    assert deleted.status_code == 403 and deleted.json()["detail"] == "requires admin"


def test_enum_delete_real_shared_guard_refuses_before_row_access(monkeypatch) -> None:
    touched: list[str] = []

    class NoDatabase:
        def get(self, *args):
            touched.append("get")
            raise AssertionError("TEST row must not be accessed before destructive guard")

    # Force only DENIAL, never an allow override or a connection to a prod URL.
    monkeypatch.setattr(deps, "is_prod_shaped_db", lambda: True)
    monkeypatch.delenv("ALLOW_PROD_DESTRUCTIVE", raising=False)
    request = Request({
        "type": "http", "method": "DELETE", "path": "/TEST_enum",
        "headers": [(b"x-requested-with", b"tts-erp")],
        "auth_method": "cookie", "api_key_role": "admin",
    })
    with pytest.raises(HTTPException) as caught:
        config.delete_enum_map(1, request, cast(Session, NoDatabase()))
    assert caught.value.status_code == 403
    assert "destructive" in str(caught.value.detail)
    assert touched == []
