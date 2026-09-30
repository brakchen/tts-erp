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


def test_focused_spus_profile_uses_realtime_inline_multi_select() -> None:
    root = Path(__file__).resolve().parents[2]
    focused = (root / "tts_erp_v2/static/js/focused-spus.js").read_text()
    assert 'label.htmlFor = "focused-spu-select"' in focused
    assert "new TomSelectClass(selectElement" in focused
    assert "summaries.parentNode.insertBefore(slot, summaries)" in focused
    assert '"mx-lg-4"' in focused
    assert "maxItems: null" in focused
    assert "onItemAdd:" in focused
    assert "onItemRemove:" in focused
    assert "resolvePastedIds" in focused
    assert 'analyticsParams: () => ({ scope: "focused" })' in focused
    assert "addSpuIds: addIds" in focused
    assert "removeSpuIds: removeIds" in focused
    assert '"X-Requested-With": "tts-erp"' in focused
    assert "context.setSelectionQueryable(true)" in focused
    assert "return { queryable: true, count: total }" in focused
    assert "编辑关注 SPU" not in focused
    assert "保存修改" not in focused


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


def test_focused_spus_page_keeps_the_full_roi_dashboard(
    api_client,
    readonly_key,
):
    headers = {"Authorization": f"Bearer {readonly_key}"}
    standard = api_client.get("/v2/pages/spu-roi", headers=headers)
    focused = api_client.get("/v2/pages/focused-spus", headers=headers)
    assert standard.status_code == focused.status_code == 200
    shared_hooks = (
        "summaries",
        "sum-total-orders",
        "sum-spend",
        "sum-orders",
        "sum-sales",
        "sum-refund-count",
        "sum-loss-qty",
        "sum-cancel-count",
        "sum-net-profit",
        "sum-roi",
        "sum-projection-status",
        "sum-projection-basis-orders",
        "sum-projection-refund-rate",
        "sum-projection-full-loss-rate",
        "sum-unresolved-orders",
        "sum-projected-future-loss-qty",
        "sum-projected-net-revenue",
        "sum-projected-net-profit",
        "sum-projected-roi",
        "sum-projected-breakeven-roi",
        "sum-projected-ad-roi",
        "sum-projected-ad-breakeven-roi",
        "fee-card",
        "rows",
        "tpl-drilldown-panel",
        "filter-limit",
        "pager-pages",
    )
    for hook in shared_hooks:
        marker = f'id="{hook}"'
        assert marker in standard.text
        assert marker in focused.text

    root = Path(__file__).resolve().parents[2]
    standard_profile = (root / "tts_erp_v2/static/js/spu-roi.js").read_text()
    focused_profile = (root / "tts_erp_v2/static/js/focused-spus.js").read_text()
    for hook in shared_hooks:
        if not hook.startswith("sum-projection") and not hook.startswith(
            "sum-unresolved"
        ) and not hook.startswith("sum-projected"):
            continue
        marker = f'"{hook}"'
        assert marker in standard_profile
        assert marker in focused_profile

    # 运营不需要在页首重复展示待确认件数；后端字段和 SPU 诊断明细保留。
    assert '"sum-unresolved-qty"' not in standard_profile
    assert '"sum-unresolved-qty"' not in focused_profile
    assert '"sum-projected-terminal-loss-qty"' not in standard_profile
    assert '"sum-projected-terminal-loss-qty"' not in focused_profile
    assert 'id="sum-projected-terminal-loss-qty"' not in standard.text
    assert 'id="sum-projected-terminal-loss-qty"' not in focused.text


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
