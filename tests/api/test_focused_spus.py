from __future__ import annotations

from pathlib import Path

from sqlalchemy import text


def _seed_shop_and_products(db_engine):
    with db_engine.begin() as conn:
        shop_a = conn.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_FOCUSED_SHOP_A', "
                "'TEST_FOCUSED_SHOP_A', 'active') RETURNING id"
            )
        ).scalar_one()
        shop_b = conn.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_FOCUSED_SHOP_B', "
                "'TEST_FOCUSED_SHOP_B', 'active') RETURNING id"
            )
        ).scalar_one()
        for shop_pk, spu_id, title in (
            (shop_a, "TEST_FOCUSED_A_1", "TEST Alpha"),
            (shop_a, "TEST_FOCUSED_A_2", "TEST Beta"),
            (shop_b, "TEST_FOCUSED_B_1", "TEST Other Shop"),
        ):
            conn.execute(
                text(
                    "INSERT INTO commerce.products_spu "
                    "(shop_pk, spu_id, title, status) "
                    "VALUES (:shop_pk, :spu_id, :title, 'ACTIVATE')"
                ),
                {"shop_pk": shop_pk, "spu_id": spu_id, "title": title},
            )
    return int(shop_a), int(shop_b)


def test_focused_spus_profile_guards_empty_scope_and_uses_delta_patch() -> None:
    root = Path(__file__).resolve().parents[2]
    kernel = (root / "tts_erp_v2/static/js/spu-profitability-page.js").read_text()
    focused = (root / "tts_erp_v2/static/js/focused-spus.js").read_text()
    assert "if (!state.selectionQueryable)" in kernel
    assert "renderEmptySelection();" in kernel
    assert 'analyticsParams: () => ({ scope: "focused" })' in focused
    assert "addSpuIds: addIds" in focused
    assert "removeSpuIds: removeIds" in focused
    assert '"X-Requested-With": "tts-erp"' in focused
    assert "context.setSelectionQueryable(total > 0)" in focused


def test_focused_spus_page_uses_shared_profitability_kernel(
    api_client,
    readonly_key,
):
    response = api_client.get(
        "/v2/pages/focused-spus",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert "重点关注 SPU" in response.text
    assert 'href="../../v2/pages/focused-spus"' in response.text
    assert 'title="重点关注 SPU"' in response.text
    assert 'data-page-profile="focused-spus"' in response.text
    assert "static/css/spu-roi.css?v=" in response.text
    assert "static/css/focused-spus.css?v=" in response.text
    assert "static/js/focused-spus.js?v=" in response.text
    assert "static/js/spu-profitability-page.js?v=" in response.text
    assert 'id="selection-slot"' in response.text
    assert "__PROFILE_" not in response.text
    assert "__JSV_" not in response.text


def test_focused_spus_auth_and_camel_case_contract(
    api_client,
    readonly_key,
    readwrite_key,
    db_engine,
):
    shop_a, _ = _seed_shop_and_products(db_engine)
    path = f"/v2/reporting/focused-spus/{shop_a}"

    assert api_client.get(path).status_code == 401
    readonly_headers = {"Authorization": f"Bearer {readonly_key}"}
    readwrite_headers = {"Authorization": f"Bearer {readwrite_key}"}

    empty = api_client.get(path, headers=readonly_headers)
    assert empty.status_code == 200
    assert empty.json() == {
        "shopPk": shop_a,
        "items": [],
        "total": 0,
        "matchedTotal": 0,
        "limit": 50,
        "offset": 0,
    }
    assert (
        api_client.patch(
            path,
            headers=readonly_headers,
            json={"addSpuIds": ["TEST_FOCUSED_A_1"]},
        ).status_code
        == 403
    )

    added = api_client.patch(
        path,
        headers=readwrite_headers,
        json={"addSpuIds": [" TEST_FOCUSED_A_1 ", "TEST_FOCUSED_A_2"]},
    )
    assert added.status_code == 200
    assert added.json() == {
        "shopPk": shop_a,
        "total": 2,
        "addedSpuIds": ["TEST_FOCUSED_A_1", "TEST_FOCUSED_A_2"],
        "removedSpuIds": [],
    }

    listed = api_client.get(path, headers=readonly_headers, params={"q": "Beta"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert body["matchedTotal"] == 1
    assert [item["spuId"] for item in body["items"]] == ["TEST_FOCUSED_A_2"]
    assert set(body["items"][0]) == {
        "spuPk",
        "spuId",
        "title",
        "status",
        "createdAt",
        "updatedAt",
    }

    removed = api_client.patch(
        path,
        headers=readwrite_headers,
        json={"removeSpuIds": ["TEST_FOCUSED_A_1"]},
    )
    assert removed.status_code == 200
    assert removed.json()["total"] == 1
    assert removed.json()["removedSpuIds"] == ["TEST_FOCUSED_A_1"]


def test_focused_spus_patch_is_atomic_and_shop_scoped(
    api_client,
    readwrite_key,
    db_engine,
):
    shop_a, _ = _seed_shop_and_products(db_engine)
    path = f"/v2/reporting/focused-spus/{shop_a}"
    headers = {"Authorization": f"Bearer {readwrite_key}"}

    invalid = api_client.patch(
        path,
        headers=headers,
        json={
            "addSpuIds": ["TEST_FOCUSED_A_1", "TEST_FOCUSED_B_1"],
            "removeSpuIds": [],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "SPU_NOT_FOUND_IN_SHOP"
    after = api_client.get(path, headers=headers).json()
    assert after["total"] == 0

    conflict = api_client.patch(
        path,
        headers=headers,
        json={
            "addSpuIds": [" TEST_FOCUSED_A_1"],
            "removeSpuIds": ["TEST_FOCUSED_A_1 "],
        },
    )
    assert conflict.status_code == 422
    assert conflict.json()["detail"]["code"] == "FOCUSED_SPU_PATCH_CONFLICT"


def test_focused_spus_cookie_patch_requires_csrf_header(
    api_client,
    readwrite_key,
    db_engine,
):
    shop_a, _ = _seed_shop_and_products(db_engine)
    login = api_client.post("/v2/auth/login", json={"key": readwrite_key})
    assert login.status_code == 200
    path = f"/v2/reporting/focused-spus/{shop_a}"

    denied = api_client.patch(
        path,
        json={"addSpuIds": ["TEST_FOCUSED_A_1"]},
    )
    assert denied.status_code == 403
    assert "X-Requested-With" in denied.text

    allowed = api_client.patch(
        path,
        headers={"X-Requested-With": "tts-erp"},
        json={"addSpuIds": ["TEST_FOCUSED_A_1"]},
    )
    assert allowed.status_code == 200
