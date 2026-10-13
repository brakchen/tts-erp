"""Phase B：monitor 专用下发 GET 的安全边界与 RFC 9110 条件语义。

契约来源：docs/monitor-config-sync.md §4.2（店铺专用 Token）、§5（交付事务）、
§6.1/§6.2（读取接口、ETag 与错误）。本文件只覆盖下发读取边界；管理面与插件
行为不在本阶段。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.monitor_config import (
    MonitorConfigDelivery,
    MonitorShopState,
)
from tts_erp_v2.db.models.security import ApiKey

SHOP_A = "TEST_monitor_delivery_seller_a"
SHOP_B = "TEST_monitor_delivery_seller_b"
MONITOR_KEY_A = "ttserp_readonly_TEST_monitor_delivery_a"
GENERAL_KEY = "ttserp_readonly_TEST_monitor_delivery_general"
ADMIN_KEY = "ttserp_admin_TEST_monitor_delivery_admin"
DELIVERY_URL = "/v2/monitor/config"

def _complete_settings(seller_id: str) -> dict[str, Any]:
    """A syntactically valid complete-settings payload (synthetic values only)."""

    return {
        "globalThreshold": "10.0",
        "autoCloseSkuOrderLimit": 0,
        "autoCloseMaxSkuOrders": 5,
        "autoCloseSingleConversionCostThreshold": "2.5",
        "autoCloseCampaignWhitelist": ["abc"],
        "closeQueryRecentDays": 7,
        "reportQueryRecentDays": 30,
        "scanIntervalMinSeconds": 30,
        "scanIntervalMaxSeconds": 60,
        "notificationIntervalMinSeconds": 900,
        "notificationIntervalMaxSeconds": 1080,
        "notifyAutoCloseSuccessEnabled": True,
        "notifyAutoCloseFailureRepeatEnabled": True,
        "feishuWebhookUrl": "https://open.feishu.cn/hook/TEST_HOOK",
        "feishuSigningSecret": "TEST_SIGNING",
        "feishuAppId": "TEST_APP_ID",
        "feishuAppSecret": "TEST_APP_SECRET",
        "feishuRecipientIdentifiers": ["ou_test_recipient_1"],
        "autoCloseEnabled": True,
        "campaignThresholds": {},
    }


def _issue_key(session: Session, plaintext: str, *, purpose: str, shop_pk: int | None,
               role: str = "readonly", status: str = "active") -> None:
    session.execute(
        delete(ApiKey).where(ApiKey.key_hash == hashlib.sha256(plaintext.encode()).hexdigest())
    )
    session.add(
        ApiKey(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            key_prefix=plaintext[:16],
            name=f"TEST_monitor_delivery_{plaintext[-8:]}",
            role=role,
            status=status,
            purpose=purpose,
            shop_pk=shop_pk,
        )
    )
    session.commit()


def _publish(session: Session, shop_pk: int, *, is_force: bool = False,
             payload: dict[str, Any] | None = None, seller_id: str = SHOP_A) -> int:
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    version = publish_delivery(
        session,
        shop_pk=shop_pk,
        payload=payload if payload is not None else _complete_settings(seller_id),
        source_global_version=4,
        source_shop_version=None,
        is_force=is_force,
        created_by="TEST_monitor_delivery_actor",
        # force 必须带完整幂等键（§5.4）
        request_id=uuid.uuid4() if is_force else None,
        request_digest=f"sha256:force:{uuid.uuid4().hex[:8]}" if is_force else None,
    )
    session.commit()
    return version


@pytest.fixture()
def shops(db_engine) -> Iterator[dict[str, int]]:
    """Committed shop rows: the app under test authenticates via its own connections."""

    made: dict[str, int] = {}
    with Session(db_engine) as session:
        for label, seller in (("a", SHOP_A), ("b", SHOP_B)):
            shop = ChannelAccount(
                platform="tiktok", shop_id=seller, account_name=f"TEST_{label}"
            )
            session.add(shop)
            session.flush()
            made[label] = shop.id
        session.commit()
    yield made
    with Session(db_engine) as session:
        for label, seller in (("a", SHOP_A), ("b", SHOP_B)):
            session.execute(delete(MonitorShopState).where(
                MonitorShopState.shop_pk == made[label]
            ))
            session.execute(delete(MonitorConfigDelivery).where(
                MonitorConfigDelivery.shop_pk == made[label]
            ))
            session.execute(delete(ApiKey).where(ApiKey.shop_pk == made[label]))
            session.execute(
                delete(ChannelAccount).where(ChannelAccount.shop_id == seller)
            )
        session.commit()


@pytest.fixture()
def issued(db_engine, shops: dict[str, int]) -> Iterator[None]:
    with Session(db_engine) as session:
        _issue_key(session, MONITOR_KEY_A, purpose="monitor_config", shop_pk=shops["a"])
        _issue_key(session, GENERAL_KEY, purpose="general", shop_pk=None)
        _issue_key(session, ADMIN_KEY, purpose="general", shop_pk=None, role="admin")
    from tts_erp_v2.access import clear_credential_cache

    clear_credential_cache()
    yield
    with Session(db_engine) as session:
        for plaintext in (MONITOR_KEY_A, GENERAL_KEY, ADMIN_KEY):
            session.execute(delete(ApiKey).where(
                ApiKey.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
            ))
        session.commit()
    clear_credential_cache()


def _get(client: TestClient, key: str | None = None, *, seller: str = SHOP_A,
         etag: str | None = None) -> Any:
    headers: dict[str, str] = {}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if etag is not None:
        headers["If-None-Match"] = etag
    return client.get(DELIVERY_URL, params={"sellerId": seller}, headers=headers)


# ─── 基本准入 ──────────────────────────────────────────────────


def _session(client: TestClient) -> Session:
    from tts_erp_v2.db.base import get_session_factory

    return get_session_factory()()


def test_delivery_returns_full_envelope_for_its_own_shop(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])

    response = _get(api_client, MONITOR_KEY_A)
    assert response.status_code == 200
    body = response.json()
    assert body["sellerId"] == SHOP_A
    assert str(body["shopPk"]) == str(shops["a"])
    assert body["current"]["version"]
    assert body["current"]["sourceVersions"]["global"] == "4"
    assert body["current"]["sourceVersions"]["shop"] is None
    assert body["current"]["payload"]["sellerId"] == SHOP_A
    assert body["force"] is None
    assert response.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in response.headers["vary"]
    assert response.headers["etag"].startswith('W/"')


def test_monitor_key_cannot_read_another_shops_delivery(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["b"], seller_id=SHOP_B)

    response = _get(api_client, MONITOR_KEY_A, seller=SHOP_B)
    assert response.status_code == 403


def test_general_and_admin_keys_cannot_read_the_delivery(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])

    for key in (GENERAL_KEY, ADMIN_KEY):
        response = _get(api_client, key)
        assert response.status_code == 403, key


def test_anonymous_and_browser_session_cannot_read_the_delivery(
    api_client, issued, shops
) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])

    assert _get(api_client).status_code == 401
    assert api_client.get(
        DELIVERY_URL,
        params={"sellerId": SHOP_A},
        cookies={"tts_erp_session": "TEST_monitor_delivery.not-a-real-session"},
    ).status_code in {401, 403}


def test_unknown_seller_is_404_and_never_returns_global_config(api_client, issued) -> None:
    response = _get(api_client, MONITOR_KEY_A, seller="TEST_monitor_delivery_unknown")
    assert response.status_code == 404


def test_missing_seller_id_is_rejected(api_client, issued) -> None:
    response = api_client.get(
        DELIVERY_URL, headers={"Authorization": f"Bearer {MONITOR_KEY_A}"}
    )
    assert response.status_code in {400, 422}


def test_shop_without_published_delivery_is_404(api_client, issued, shops) -> None:
    response = _get(api_client, MONITOR_KEY_A)
    assert response.status_code == 404


# ─── 授权先于条件请求（§4.2 约束 3 / §6.2）─────────────────────


def test_authorization_runs_before_conditional_evaluation(api_client, issued, shops) -> None:
    """正确 ETag 不能让已被禁用的 token 拿到 304。"""

    with _session(api_client) as session:
        _publish(session, shops["a"])
    etag = _get(api_client, MONITOR_KEY_A).headers["etag"]

    with _session(api_client) as session:
        session.execute(
            delete(ApiKey).where(
                ApiKey.key_hash == hashlib.sha256(MONITOR_KEY_A.encode()).hexdigest()
            )
        )
        session.add(
            ApiKey(
                key_hash=hashlib.sha256(MONITOR_KEY_A.encode()).hexdigest(),
                key_prefix=MONITOR_KEY_A[:16],
                name="TEST_monitor_delivery_revoked",
                role="readonly",
                status="disabled",
                purpose="monitor_config",
                shop_pk=shops["a"],
            )
        )
        session.commit()

    assert _get(api_client, MONITOR_KEY_A, etag=etag).status_code == 403


def test_matching_etag_for_another_shop_does_not_leak_304(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["b"])
    other_etag = _get(api_client, MONITOR_KEY_A, seller=SHOP_B).headers.get("etag")

    if other_etag:
        assert _get(api_client, MONITOR_KEY_A, seller=SHOP_B, etag=other_etag).status_code == 404


# ─── RFC 9110 条件语义 ─────────────────────────────────────────


def test_matching_etag_returns_304_with_same_headers(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    etag = _get(api_client, MONITOR_KEY_A).headers["etag"]

    response = _get(api_client, MONITOR_KEY_A, etag=etag)
    assert response.status_code == 304
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in response.headers["vary"]


def test_star_matches_any_existing_representation(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    assert _get(api_client, MONITOR_KEY_A, etag="*").status_code == 304


def test_multiple_etags_use_rfc9110_weak_comparison(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    etag = _get(api_client, MONITOR_KEY_A).headers["etag"]
    opaque = etag.removeprefix('W/"').removesuffix('"')

    # 列表中出现即命中；强弱写法（RFC 9110 §8.8.2 弱比较）等价。
    assert _get(api_client, MONITOR_KEY_A, etag=f'"other", {etag}').status_code == 304
    assert _get(api_client, MONITOR_KEY_A, etag=f'W/"{opaque}"').status_code == 304
    assert _get(api_client, MONITOR_KEY_A, etag='"nope", "also-nope"').status_code == 200


def test_non_matching_etag_returns_the_body(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    response = _get(api_client, MONITOR_KEY_A, etag='W/"stale"')
    assert response.status_code == 200


def test_republishing_changes_the_etag(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    first = _get(api_client, MONITOR_KEY_A).headers["etag"]
    with _session(api_client) as session:
        _publish(session, shops["a"])
    second = _get(api_client, MONITOR_KEY_A).headers["etag"]
    assert first != second


# ─── force ────────────────────────────────────────────────────


def test_force_reuses_its_delivery_version_and_target_shape(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
        force_version = _publish(session, shops["a"], is_force=True)

    body = _get(api_client, MONITOR_KEY_A).json()
    assert body["force"]["version"] == str(force_version)
    assert body["force"]["target"]["version"] == str(force_version)
    assert body["current"]["version"] == body["force"]["target"]["version"]


def test_forcing_identical_content_still_changes_the_etag(api_client, issued, shops) -> None:
    """相同内容重复强制也必须换 ETag（§11.1.3）。"""

    with _session(api_client) as session:
        _publish(session, shops["a"])
    before = _get(api_client, MONITOR_KEY_A).headers["etag"]
    with _session(api_client) as session:
        _publish(session, shops["a"], is_force=True)
    after = _get(api_client, MONITOR_KEY_A).headers["etag"]
    assert before != after


def test_ordinary_publish_does_not_retract_the_latest_force(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
        _publish(session, shops["a"], is_force=True)
        force_version = _publish(session, shops["a"], is_force=True)
        _publish(session, shops["a"])

    body = _get(api_client, MONITOR_KEY_A).json()
    assert body["force"]["version"] == str(force_version)


# ─── 响应不得泄漏 ─────────────────────────────────────────────


def test_response_never_carries_secret_or_credential_fields(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
    raw = _get(api_client, MONITOR_KEY_A).text
    for forbidden in (
        "TTS_ERP_FERNET_KEY",
        "encrypted_payload",
        "key_hash",
        "rotated_to_key_hash",
        "Authorization",
        MONITOR_KEY_A,
    ):
        assert forbidden not in raw, forbidden


def test_force_block_only_exposes_version_and_target(api_client, issued, shops) -> None:
    with _session(api_client) as session:
        _publish(session, shops["a"])
        _publish(session, shops["a"], is_force=True)
    assert set(_get(api_client, MONITOR_KEY_A).json()["force"]) == {"version", "target"}
