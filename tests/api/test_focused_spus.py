from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests._job_invocation import authorize_test_shop


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
        with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
            authorize_test_shop(session, int(shop_a), "TEST_FOCUSED_SHOP_A")
            authorize_test_shop(session, int(shop_b), "TEST_FOCUSED_SHOP_B")
            session.commit()
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


def test_focused_spus_page_uses_shared_profitability_kernel(api_client, readonly_key):
    from tests.api.test_frontend_pages import _bootstrap

    response = api_client.get(
        "/v2/pages/focused-spus",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "private, no-store"
    bootstrap = _bootstrap(response.text)
    assert bootstrap["pageId"] == "focused-spus"
    assert bootstrap["roleCapabilities"] == {"readwrite": False, "admin": False}
    active = [item for item in bootstrap["navigation"] if item["id"] == "focused-spus"]
    assert len(active) == 1
    assert active[0]["label"] == "重点关注 SPU"
    assert active[0]["href"] == bootstrap["rootPath"] + "/v2/pages/focused-spus"
    assert 'id="frontend-root"' in response.text
    assert "/static/frontend/" in response.text
    for legacy in ("focused-spus.js", "focused-spus.css", "spu-profitability-page.js", "spu-roi.css"):
        assert legacy not in response.text

    root = Path(__file__).resolve().parents[2]
    loader = (root / "frontend/src/app/page-loader.ts").read_text(encoding="utf-8")
    for page_id in ("spu-roi", "focused-spus"):
        assert f"'{page_id}': () => import('../pages/spu-profitability')" in loader


def test_focused_spus_page_keeps_the_full_roi_dashboard(api_client, readonly_key):
    from tests.api.test_frontend_pages import _bootstrap

    headers = {"Authorization": f"Bearer {readonly_key}"}
    for page_id in ("spu-roi", "focused-spus"):
        response = api_client.get(f"/v2/pages/{page_id}", headers=headers)
        assert response.status_code == 200
        assert _bootstrap(response.text)["pageId"] == page_id

    root = Path(__file__).resolve().parents[2]
    directory = root / "frontend/src/pages/spu-profitability"
    summary = (directory / "Summary.tsx").read_text(encoding="utf-8")
    page = (directory / "index.tsx").read_text(encoding="utf-8")
    table = (directory / "ProfitabilityTable.tsx").read_text(encoding="utf-8")
    drill = (directory / "DrillDetails.tsx").read_text(encoding="utf-8")
    expected = (
        "sum-total-orders", "sum-spend", "sum-orders", "sum-sales",
        "sum-refund-count", "sum-refund-rate", "sum-loss-qty", "sum-loss-rate",
        "sum-cancel-count", "sum-cancel-rate", "sum-net-profit", "sum-roi",
        "sum-roi-breakeven", "sum-roi-ad-actual", "sum-roi-ad",
        "sum-projection-completed-basis-orders", "sum-projection-completed-loss-orders",
        "sum-projection-full-loss-rate", "sum-unresolved-orders",
        "sum-delivered-unsettled-orders", "sum-full-loss-exposure-unsettled-orders",
        "sum-projected-future-loss-qty", "sum-projected-net-revenue",
        "sum-projected-net-profit", "sum-projected-roi", "sum-projected-breakeven-roi",
        "sum-projected-ad-roi", "sum-projected-ad-breakeven-roi",
    )
    for hook in expected:
        assert f"'{hook}'" in summary, hook
    for hook in ("summaries", "sum-projection-status", "fee-card"):
        assert f'id="{hook}"' in summary
    for component in ("<Summary ", "<FeeFacts ", "<PriceSummary ", "<ProfitabilityTable ", "<DrillDetails "):
        assert component in page
    assert 'id="rows"' in table and 'id="filter-limit"' in page
    assert 'aria-label="SPU ROI 分页"' in page
    assert "<Metrics values={row}" in drill
    for retired in ("sum-unresolved-qty", "sum-projected-terminal-loss-qty"):
        assert retired not in summary and retired not in page


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
    ua_user_factory,
    db_engine,
):
    shop_a, _ = _seed_shop_and_products(db_engine)
    user = ua_user_factory("test_ua_focused_csrf", roles=("operator",))
    login = api_client.post(
        "/v2/auth/login",
        json={"username": user.username, "password": user.password},
    )
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
