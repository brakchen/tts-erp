"""Tests for the manual shop-registration admin endpoints.

Pins the contract for ``POST /v2/admin/shops/register`` and
``GET /v2/admin/shops/unregistered`` (feature/shop-registration lane):

  - admin/readwrite registers a plugin-only shop into ``commerce.shops``
    with ``credential_id = NULL`` and ``status = 'active'`` — the row
    exists purely for query association (spu-roi shop filter), NOT for
    sync scheduling
  - registration is idempotent: re-registering returns the existing row
    with ``created=false`` and only backfills still-NULL display fields
    (never touches ``credential_id`` / ``status``)
  - ``shop_id`` must be a numeric TikTok shop id; ``TEST_``/``MOCK_``
    prefixes are rejected (they must never become registered shops)
  - a registered-but-credential-less shop is NOT enumerated by the sync
    worker; adding a credential flips it into the API-sync set
    (the plugin → API upgrade path)
  - ``unregistered`` lists shop ids seen in plugin / analytics
    plugin data that have no ``commerce.shops`` row
  - role matrix: readwrite+; readonly → 403, anonymous → 401
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select, text

from tts_erp_v2.db.models.commerce import ChannelAccount

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# Numeric pseudo shop ids (registration rejects TEST_/MOCK_ prefixes, so
# tests use real-shaped numeric ids and clean up by exact match).
SHOP_A = "8800000000000000001"
SHOP_B = "8800000000000000002"
SHOP_IDS = (SHOP_A, SHOP_B)
SERVICE_A = "TEST_ADMIN_SERVICE_123"
SERVICE_B = "TEST_ADMIN_SERVICE_456"


@pytest.fixture(autouse=True)
def _cleanup_registered_shops(db_engine):
    """Delete shops rows + plugin-source rows created by these tests.

    Handlers commit inside the request, so the shared ``db_session``
    savepoint rollback does NOT cover them — wipe by exact id.
    """
    yield
    with db_engine.begin() as conn:
        conn.execute(
            delete(ChannelAccount).where(ChannelAccount.shop_id.in_(SHOP_IDS))
        )
        conn.execute(
            text(
                "DELETE FROM plugin.ad_daily WHERE seller_id IN (:a, :b)"
            ).bindparams(a=SHOP_A, b=SHOP_B)
        )
        conn.execute(
            text(
                "DELETE FROM integration.tiktok_app_credentials "
                "WHERE service_id IN (:a, :b)"
            ).bindparams(a=SERVICE_A, b=SERVICE_B)
        )


def _register(api_client, key, **overrides):
    body = {"platform": "tiktok", "shop_id": SHOP_A}
    body.update(overrides)
    return api_client.post(
        "/v2/admin/shops/register",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )


# ─── POST /v2/admin/shops/register ─────────────────────────────────────


def test_register_creates_plugin_only_shop(api_client, admin_key, db_engine):
    r = _register(
        api_client,
        admin_key,
        account_name="Bridge nook 2",
        region="VN",
        opened_date="2026-06-01",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    shop = body["shop"]
    assert shop["shop_id"] == SHOP_A
    assert shop["platform"] == "tiktok"
    assert shop["account_name"] == "Bridge nook 2"
    assert shop["region"] == "VN"
    assert shop["opened_date"] == "2026-06-01"
    assert shop["status"] == "active"  # 注册即完整店铺，无「待授权」中间态
    assert shop["credential_id"] is None

    from sqlalchemy.orm import Session

    with db_engine.connect() as conn:
        row = Session(bind=conn).execute(
            select(ChannelAccount).where(
                ChannelAccount.platform == "tiktok",
                ChannelAccount.shop_id == SHOP_A,
            )
        ).scalar_one()
    assert row.credential_id is None
    assert row.status == "active"
    assert str(row.opened_date) == "2026-06-01"


def test_register_with_service_app_pair_is_atomic(api_client, admin_key):
    r = _register(
        api_client,
        admin_key,
        shop_id=SHOP_B,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
        app_secret="TEST_ADMIN_APP_SECRET_456",
    )
    assert r.status_code == 200, r.text
    shop = r.json()["shop"]
    assert shop["service_id"] == SERVICE_B
    assert shop["app_credentials_configured"] is True
    assert shop["credential_id"] is None


def test_register_is_idempotent_and_backfills_null_fields(
    api_client, admin_key
):
    r1 = _register(api_client, admin_key, account_name="Shop A")
    assert r1.status_code == 200 and r1.json()["created"] is True

    # Second call: different display fields — created=false, existing
    # account_name NOT overwritten (only NULL fields get backfilled),
    # opened_date (was NULL) gets filled.
    r2 = _register(
        api_client,
        admin_key,
        account_name="Renamed Shop",
        opened_date="2026-06-01",
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["created"] is False
    assert body["shop"]["account_name"] == "Shop A"  # not clobbered
    assert body["shop"]["opened_date"] == "2026-06-01"  # backfilled


def test_register_never_clobbers_credential_link(api_client, admin_key, db_engine):
    """An existing API shop (credential_id set, status='active') must
    survive a register call untouched — only NULL display fields may be
    backfilled."""
    from tts_erp_v2.proxy.token_service import upsert_credentials

    with db_engine.begin() as conn:
        from sqlalchemy.orm import Session

        sess = Session(bind=conn)
        cred = upsert_credentials(
            sess,
            provider="tiktok",
            external_account_id=SHOP_A,
            plaintext_access_token="tok",
            plaintext_refresh_token="rtok",
            plaintext_shop_cipher="cipher",
        )
        sess.flush()
        cred_id = cred.id
        conn.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, credential_id) "
                "VALUES ('tiktok', :sid, 'API Shop', 'active', :cid)"
            ).bindparams(sid=SHOP_A, cid=cred_id)
        )
    try:
        r = _register(api_client, admin_key, account_name="X")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] is False
        shop = body["shop"]
        assert shop["credential_id"] == cred_id  # untouched
        assert shop["status"] == "active"  # untouched
        assert shop["account_name"] == "API Shop"  # untouched
    finally:
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM integration.credentials "
                    "WHERE external_account_id = :sid"
                ).bindparams(sid=SHOP_A)
            )


def test_register_rejects_non_numeric_and_test_prefixes(api_client, admin_key):
    for bad in ("TEST_SHOP_1", "MOCK_SHOP", "abc123", "", "12 34"):
        r = _register(api_client, admin_key, shop_id=bad)
        assert r.status_code == 422, (
            f"shop_id={bad!r} should be 422, got {r.status_code}: {r.text}"
        )


def test_register_rejects_unknown_platform(api_client, admin_key):
    r = _register(api_client, admin_key, platform="shopee")
    assert r.status_code == 422, r.text


def test_register_role_matrix(api_client, readwrite_key, readonly_key):
    """readwrite 可注册（2026-09-11 用户拍板，不再要求 admin）；readonly → 403。"""
    r = _register(api_client, readwrite_key, shop_id=SHOP_B)
    assert r.status_code == 200, r.text
    assert r.json()["shop"]["shop_id"] == SHOP_B

    r = _register(api_client, readonly_key)
    assert r.status_code == 403, (
        f"readonly should be 403, got {r.status_code}: {r.text}"
    )


def test_register_app_credentials_allow_readwrite(api_client, readwrite_key):
    """App Key/Secret 保存 readwrite 即可（2026-09-29 用户拍板，不再要求 admin）。"""
    r = _register(
        api_client,
        readwrite_key,
        shop_id=SHOP_B,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
        app_secret="TEST_ADMIN_APP_SECRET_456",
    )
    assert r.status_code == 200, r.text
    shop = r.json()["shop"]
    assert shop["service_id"] == SERVICE_B
    assert shop["app_credentials_configured"] is True
    assert "app_secret" not in r.text


def test_register_anonymous_is_401(api_client):
    r = api_client.post(
        "/v2/admin/shops/register",
        json={"platform": "tiktok", "shop_id": SHOP_A},
    )
    assert r.status_code == 401


def test_registered_shop_not_synced_until_credential(api_client, admin_key):
    """A registered plugin-only shop must NOT be picked up by the sync
    worker (no credential); once a credential exists it joins the
    API-sync fan-out — the plugin → API upgrade path."""
    from tts_erp_v2.sync_worker.scheduler import _enumerate_tiktok_shops

    r = _register(api_client, admin_key)
    assert r.status_code == 200

    from tts_erp_v2.db.base import get_session_factory

    sess = get_session_factory()()
    try:
        shops = _enumerate_tiktok_shops(sess)
        assert SHOP_A not in shops
    finally:
        sess.close()


# ─── GET /v2/admin/shops/unregistered ──────────────────────────────────


def _insert_ad_daily(db_engine, seller_id: str) -> None:
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plugin.ad_daily "
                "(seller_id, advertiser_id, campaign_id, product_id, "
                " endpoint, day) "
                "VALUES (:sid, 'adv1', 'c1', 'p1', "
                "'/api/v1.0/qsc/reports/integrated/get/', '2026-09-10') "
                "ON CONFLICT DO NOTHING"
            ).bindparams(sid=seller_id)
        )


def test_unregistered_lists_analytics_source_only(api_client, admin_key, db_engine):
    """Phase 3 之后 (2026-09-17)：raw_log 表已 drop，plugin 域 source 移除。
    /v2/admin/shops/unregistered 现在只从 analytics 域 (ad_today / ad_daily /
    ad_monthly / plugin_logs) 枚举未注册店铺。
    """
    _insert_ad_daily(db_engine, SHOP_B)

    r = api_client.get(
        "/v2/admin/shops/unregistered",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text
    candidates = {c["shop_id"]: c for c in r.json()["candidates"]}
    assert SHOP_B in candidates
    assert "analytics" in candidates[SHOP_B]["sources"]
    # plugin source 已下线（Phase 3 drop plugin.raw_log）
    assert "plugin" not in {src for c in candidates.values() for src in c["sources"]}


def test_unregistered_excludes_registered_shops(api_client, admin_key, db_engine):
    _register(api_client, admin_key)

    r = api_client.get(
        "/v2/admin/shops/unregistered",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text
    ids = {c["shop_id"] for c in r.json()["candidates"]}
    # Phase 3 之后 SHOP_A 仅在 raw_log 里才有；raw_log drop 后它根本不会进入
    # unregistered 候选列表（不需要走 _register 豁免路径）
    assert SHOP_A not in ids


def test_unregistered_role_matrix(api_client, readwrite_key, readonly_key):
    """readwrite 可查候选列表；readonly → 403。"""
    r = api_client.get(
        "/v2/admin/shops/unregistered",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    assert "candidates" in r.json()

    r = api_client.get(
        "/v2/admin/shops/unregistered",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, (
        f"readonly should be 403, got {r.status_code}: {r.text}"
    )


def test_unregistered_anonymous_is_401(api_client):
    r = api_client.get("/v2/admin/shops/unregistered")
    assert r.status_code == 401


# ─── PATCH /v2/admin/shops/{shop_pk} ─────────────────────────────────


def _patch(api_client, key, shop_pk, **fields):
    return api_client.patch(
        f"/v2/admin/shops/{shop_pk}",
        headers={"Authorization": f"Bearer {key}"},
        json=fields,
    )


def test_update_shop_metadata_full(api_client, admin_key, db_engine):
    """PATCH 覆盖全部四个可编辑元信息字段（2026-09-28 扩展 name/region）。"""
    r = _register(api_client, admin_key, account_name="Old Name", region="VN")
    assert r.status_code == 200
    pk = r.json()["shop"]["id"]

    r = _patch(
        api_client,
        admin_key,
        pk,
        account_name="New Name",
        region="TH",
        opened_date="2026-09-01",
        service_id=SERVICE_A,
        app_key="TEST_ADMIN_APP_KEY_123",
        app_secret="TEST_ADMIN_APP_SECRET_123",
    )
    assert r.status_code == 200, r.text
    assert "TEST_ADMIN_APP_SECRET_123" not in r.text
    shop = r.json()["shop"]
    assert shop["id"] == pk
    assert shop["account_name"] == "New Name"
    assert shop["region"] == "TH"
    assert shop["opened_date"] == "2026-09-01"
    assert shop["service_id"] == SERVICE_A
    assert shop["app_credentials_configured"] is True
    with db_engine.connect() as conn:
        persisted = conn.execute(
            text(
                "SELECT app_key, app_secret_ciphertext "
                "FROM integration.tiktok_app_credentials "
                "WHERE service_id = :service_id"
            ),
            {"service_id": SERVICE_A},
        ).one()
    assert persisted.app_key == "TEST_ADMIN_APP_KEY_123"
    assert persisted.app_secret_ciphertext != b"TEST_ADMIN_APP_SECRET_123"


def test_update_shop_partial_keeps_existing(api_client, admin_key):
    """未传字段（null/缺省）保持原值 —— COALESCE 语义。"""
    r = _register(
        api_client,
        admin_key,
        account_name="Keep Me",
        region="VN",
        opened_date="2026-06-01",
    )
    assert r.status_code == 200
    pk = r.json()["shop"]["id"]

    r = _patch(api_client, admin_key, pk, region="ID")
    assert r.status_code == 200, r.text
    shop = r.json()["shop"]
    assert shop["region"] == "ID"
    assert shop["account_name"] == "Keep Me"  # 未被清掉
    assert shop["opened_date"] == "2026-06-01"  # 未被清掉


def test_update_shop_never_touches_credential_or_status(api_client, admin_key):
    """PATCH 只动元信息；credential_id / status 不在可改面内。"""
    r = _register(api_client, admin_key)
    pk = r.json()["shop"]["id"]

    r = _patch(api_client, admin_key, pk, account_name="Renamed")
    assert r.status_code == 200, r.text
    shop = r.json()["shop"]
    assert shop["credential_id"] is None
    assert shop["status"] == "active"


def test_update_app_credentials_allow_readwrite(
    api_client, admin_key, readwrite_key
):
    """PATCH 配置 App pair readwrite 即可（2026-09-29 用户拍板）。"""
    r = _register(api_client, admin_key, shop_id=SHOP_B)
    pk = r.json()["shop"]["id"]
    r = _patch(
        api_client,
        readwrite_key,
        pk,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
        app_secret="TEST_ADMIN_APP_SECRET_456",
    )
    assert r.status_code == 200, r.text
    assert r.json()["shop"]["app_credentials_configured"] is True


def test_update_service_id_requires_resolvable_app_pair(api_client, admin_key):
    r = _register(api_client, admin_key, shop_id=SHOP_B)
    pk = r.json()["shop"]["id"]
    r = _patch(api_client, admin_key, pk, service_id=SERVICE_B)
    assert r.status_code == 409, r.text
    assert "not configured" in r.text


def test_update_app_key_secret_must_be_submitted_together(api_client, admin_key):
    r = _register(api_client, admin_key, shop_id=SHOP_B)
    pk = r.json()["shop"]["id"]
    r = _patch(
        api_client,
        admin_key,
        pk,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
    )
    assert r.status_code == 422, r.text


def test_update_invalid_app_secret_is_never_echoed(api_client, admin_key):
    r = _register(api_client, admin_key, shop_id=SHOP_B)
    pk = r.json()["shop"]["id"]
    invalid_secret = "S" * 513
    r = _patch(
        api_client,
        admin_key,
        pk,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
        app_secret=invalid_secret,
    )
    assert r.status_code == 422, r.text
    assert invalid_secret not in r.text
    assert "app_secret length is invalid" in r.text


def test_update_shop_not_found(api_client, admin_key):
    r = _patch(api_client, admin_key, 999999999, account_name="Ghost")
    assert r.status_code == 404, r.text


def test_update_shop_role_matrix(api_client, admin_key, readwrite_key, readonly_key):
    r = _register(api_client, admin_key, shop_id=SHOP_B)
    pk = r.json()["shop"]["id"]

    r = _patch(api_client, readwrite_key, pk, account_name="RW Edit")
    assert r.status_code == 200, r.text
    assert r.json()["shop"]["account_name"] == "RW Edit"

    r = _patch(api_client, readonly_key, pk, account_name="RO Edit")
    assert r.status_code == 403, (
        f"readonly should be 403, got {r.status_code}: {r.text}"
    )


def test_update_shop_anonymous_is_401(api_client, admin_key):
    r = _register(api_client, admin_key)
    pk = r.json()["shop"]["id"]
    r = api_client.patch(f"/v2/admin/shops/{pk}", json={"account_name": "X"})
    assert r.status_code == 401


def test_channel_accounts_exposes_service_id(api_client, admin_key, readonly_key):
    """channel-accounts 读面返回 service_id（shops 页编辑/授权链接依赖它）。"""
    r = _register(api_client, admin_key)
    pk = r.json()["shop"]["id"]
    r = _patch(
        api_client,
        admin_key,
        pk,
        service_id=SERVICE_B,
        app_key="TEST_ADMIN_APP_KEY_456",
        app_secret="TEST_ADMIN_APP_SECRET_456",
    )
    assert r.status_code == 200

    r = api_client.get(
        "/v2/commerce/channel-accounts",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = {row["shop_id"]: row for row in r.json()}
    assert rows[SHOP_A]["service_id"] == SERVICE_B
    assert rows[SHOP_A]["app_credentials_configured"] is True


# ─── channel-accounts read surface ─────────────────────────────────────


def test_channel_accounts_exposes_opened_date(api_client, admin_key, readonly_key):
    _register(api_client, admin_key, opened_date="2026-06-01")
    r = api_client.get(
        "/v2/commerce/channel-accounts",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = {row["shop_id"]: row for row in r.json()}
    assert SHOP_A in rows
    assert rows[SHOP_A]["opened_date"] == "2026-06-01"
    assert rows[SHOP_A]["credential_id"] is None  # 插件店铺，无 API 凭证
