"""Phase C：monitor 管理接口（docs/monitor-config-sync.md §6.3）。

覆盖：纳管店铺列表、全局默认草稿/发布、店铺覆盖草稿/发布/强制覆盖，
以及三条不可让步的边界：

* 只有 ``readwrite`` 及以上可调用；monitor 专用 token 调管理接口一律 403
  （管理能力走独立管理接口，§4.2 约束 2/4）。
* 草稿只存 ``secret://`` 引用；任何管理响应都不回显解密后的飞书密钥
  （§4.3），密文只在发布瞬间解冻进不可变交付快照。
* 乐观并发：草稿 ``expectedDraftVersion`` 与强制覆盖 ``expectedDeliveryVersion``
  冲突一律 409；force 幂等检查先于版本冲突（§5.4）。
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
from tts_erp_v2.db.models.config import (
    RuntimeConfigItem,
    RuntimeConfigRevision,
    RuntimeConfigSecret,
)
from tts_erp_v2.db.models.monitor_config import (
    MonitorConfigDelivery,
    MonitorShopState,
)
from tts_erp_v2.db.models.security import ApiKey
from tts_erp_v2.monitor_config import admin

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

SHOP_A = "TEST_monitor_admin_seller_a"
SHOP_B = "TEST_monitor_admin_seller_b"

HOOK_SECRET = "monitor_test_feishu_hook"
SIGNING_SECRET = "monitor_test_feishu_signing"
APP_SECRET = "monitor_test_feishu_app"
HOOK_PLAINTEXT = "https://open.feishu.cn/hook/TEST_HOOK_PLAINTEXT"
SIGNING_PLAINTEXT = "TEST_SIGNING_PLAINTEXT"
APP_PLAINTEXT = "TEST_APP_SECRET_PLAINTEXT"

ADMIN = "/v2/monitor/admin"
DEFAULTS_DRAFT = f"{ADMIN}/defaults/draft"
DEFAULTS_PUBLISH = f"{ADMIN}/defaults/publish"
DELIVERY_URL = "/v2/monitor/config"


# ─── 合成数据 ────────────────────────────────────────────────────


def _complete_settings(**overrides: Any) -> dict[str, Any]:
    """一份语法合法的完整全局默认（§3.1 全部字段）。"""

    return {
        "globalThreshold": "10.0",
        "autoCloseSkuOrderLimit": 0,
        "autoCloseMaxSkuOrders": 5,
        "autoCloseSingleConversionCostThreshold": "2.5",
        "autoCloseCampaignWhitelist": ["TEST_whitelist_a"],
        "closeQueryRecentDays": 7,
        "reportQueryRecentDays": 30,
        "scanIntervalMinSeconds": 30,
        "scanIntervalMaxSeconds": 60,
        "notificationIntervalMinSeconds": 900,
        "notificationIntervalMaxSeconds": 1080,
        "notifyAutoCloseSuccessEnabled": True,
        "notifyAutoCloseFailureRepeatEnabled": True,
        "feishuWebhookUrl": f"secret://{HOOK_SECRET}",
        "feishuSigningSecret": f"secret://{SIGNING_SECRET}",
        # 白名单飞书字段只接受 secret:// 引用或空串（Terra P1-1）。
        "feishuAppId": "",
        "feishuAppSecret": f"secret://{APP_SECRET}",
        "feishuRecipientIdentifiers": ["ou_TEST_recipient"],
        "autoCloseEnabled": True,
        "campaignThresholds": {},
        **overrides,
    }


def _shop_override(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"globalThreshold": "12.5"}
    payload.update(overrides)
    return payload


# ─── fixtures ────────────────────────────────────────────────────


def _session(client: TestClient) -> Session:
    from tts_erp_v2.db.base import get_session_factory

    return get_session_factory()()


def _issue_key(
    session: Session,
    plaintext: str,
    *,
    role: str,
    purpose: str = "general",
    shop_pk: int | None = None,
) -> None:
    session.execute(
        delete(ApiKey).where(
            ApiKey.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
        )
    )
    session.add(
        ApiKey(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            key_prefix=plaintext[:16],
            name=f"TEST_monitor_admin_{plaintext[-12:]}",
            role=role,
            status="active",
            purpose=purpose,
            shop_pk=shop_pk,
        )
    )
    session.commit()


@pytest.fixture()
def shops(db_engine) -> Iterator[dict[str, int]]:
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
        for pk in made.values():
            session.execute(
                delete(MonitorConfigDelivery).where(MonitorConfigDelivery.shop_pk == pk)
            )
            session.execute(
                delete(MonitorShopState).where(MonitorShopState.shop_pk == pk)
            )
        session.execute(delete(ApiKey).where(ApiKey.shop_pk.in_(list(made.values()))))
        session.execute(
            delete(RuntimeConfigRevision).where(
                RuntimeConfigRevision.config_key.like("monitor.%")
            )
        )
        session.execute(
            delete(RuntimeConfigItem).where(
                RuntimeConfigItem.config_key.like("monitor.%")
            )
        )
        session.execute(
            delete(RuntimeConfigSecret).where(
                RuntimeConfigSecret.name.in_([HOOK_SECRET, SIGNING_SECRET, APP_SECRET])
            )
        )
        for seller in (SHOP_A, SHOP_B):
            session.execute(
                delete(ChannelAccount).where(ChannelAccount.shop_id == seller)
            )
        session.commit()


@pytest.fixture()
def keys(db_engine, shops: dict[str, int]) -> Iterator[dict[str, str]]:
    issued = {
        "readonly": "ttserp_readonly_TEST_monitor_admin_ro",
        "readwrite": "ttserp_readwrite_TEST_monitor_admin_rw",
        "admin": "ttserp_admin_TEST_monitor_admin_ad",
        "monitor": "ttserp_readonly_TEST_monitor_admin_mk",
    }
    with Session(db_engine) as session:
        _issue_key(session, issued["readonly"], role="readonly")
        _issue_key(session, issued["readwrite"], role="readwrite")
        _issue_key(session, issued["admin"], role="admin")
        _issue_key(
            session,
            issued["monitor"],
            role="readonly",
            purpose="monitor_config",
            shop_pk=shops["a"],
        )
    from tts_erp_v2.access import clear_credential_cache

    clear_credential_cache()
    yield issued
    with Session(db_engine) as session:
        for plaintext in issued.values():
            session.execute(
                delete(ApiKey).where(
                    ApiKey.key_hash == hashlib.sha256(plaintext.encode()).hexdigest()
                )
            )
        session.commit()
    clear_credential_cache()


@pytest.fixture()
def secrets(db_engine) -> Iterator[None]:
    from tts_erp_v2.runtime_config.repository import upsert_secret

    with Session(db_engine) as session:
        upsert_secret(session, name=HOOK_SECRET, value=HOOK_PLAINTEXT)
        upsert_secret(session, name=SIGNING_SECRET, value=SIGNING_PLAINTEXT)
        upsert_secret(session, name=APP_SECRET, value=APP_PLAINTEXT)
        session.commit()
    yield
    with Session(db_engine) as session:
        session.execute(
            delete(RuntimeConfigSecret).where(
                RuntimeConfigSecret.name.in_([HOOK_SECRET, SIGNING_SECRET, APP_SECRET])
            )
        )
        session.commit()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _put_defaults(
    client: TestClient,
    key: str,
    payload: dict[str, Any] | None = None,
    *,
    expected: int = 0,
) -> Any:
    return client.put(
        DEFAULTS_DRAFT,
        json={
            "expectedDraftVersion": expected,
            "payload": payload or _complete_settings(),
        },
        headers=_auth(key),
    )


def _publish_defaults(client: TestClient, key: str, *, expected: int) -> Any:
    return client.post(
        DEFAULTS_PUBLISH,
        json={"expectedDraftVersion": expected, "comment": "TEST publish"},
        headers=_auth(key),
    )


def _put_shop_draft(
    client: TestClient,
    key: str,
    shop_pk: int,
    payload: dict[str, Any],
    *,
    expected: int = 0,
) -> Any:
    return client.put(
        f"{ADMIN}/shops/{shop_pk}/draft",
        json={"expectedDraftVersion": expected, "payload": payload},
        headers=_auth(key),
    )


def _publish_shop(
    client: TestClient, key: str, shop_pk: int, *, expected: int = 0
) -> Any:
    return client.post(
        f"{ADMIN}/shops/{shop_pk}/publish",
        json={"expectedDraftVersion": expected, "comment": "TEST shop publish"},
        headers=_auth(key),
    )


def _force(
    client: TestClient,
    key: str,
    shop_pk: int,
    *,
    expected_version: str,
    request_id: str | None = None,
) -> Any:
    body: dict[str, Any] = {"expectedDeliveryVersion": expected_version}
    if request_id is not None:
        body["requestId"] = request_id
    return client.post(f"{ADMIN}/shops/{shop_pk}/force", json=body, headers=_auth(key))


def _delivery(client: TestClient, key: str, seller: str) -> Any:
    return client.get(
        DELIVERY_URL,
        params={"sellerId": seller},
        headers={"Authorization": f"Bearer {key}"},
    )


def _delivery_state(client: TestClient, shop_pk: int) -> MonitorShopState | None:
    with _session(client) as session:
        return session.get(MonitorShopState, shop_pk)


# ─── 准入矩阵（§6.3 / §4.2）────────────────────────────────────


@pytest.mark.parametrize("path", [ADMIN + "/shops", DEFAULTS_DRAFT])
def test_readonly_cannot_reach_the_admin_api(
    api_client, keys, shops, secrets, path
) -> None:
    assert api_client.get(path, headers=_auth(keys["readonly"])).status_code == 403


def test_readwrite_reaches_the_admin_api(api_client, keys, shops, secrets) -> None:
    assert (
        api_client.get(f"{ADMIN}/shops", headers=_auth(keys["readwrite"])).status_code
        == 200
    )
    assert (
        api_client.get(DEFAULTS_DRAFT, headers=_auth(keys["readwrite"])).status_code
        == 200
    )


def test_monitor_token_cannot_reach_any_admin_route(
    api_client, keys, shops, secrets
) -> None:
    """role=readonly 不得让 monitor key 读到管理接口（§4.2 约束 2）。"""

    headers = _auth(keys["monitor"])
    assert api_client.get(f"{ADMIN}/shops", headers=headers).status_code == 403
    assert api_client.get(DEFAULTS_DRAFT, headers=headers).status_code == 403
    assert (
        api_client.put(
            DEFAULTS_DRAFT,
            json={"expectedDraftVersion": 0, "payload": _complete_settings()},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        api_client.post(
            f"{ADMIN}/shops/{shops['a']}/force",
            json={"expectedDeliveryVersion": "1", "requestId": str(uuid.uuid4())},
            headers=headers,
        ).status_code
        == 403
    )


def test_admin_token_may_use_the_admin_api(api_client, keys, shops, secrets) -> None:
    assert (
        api_client.get(f"{ADMIN}/shops", headers=_auth(keys["admin"])).status_code
        == 200
    )


def test_anonymous_is_rejected(api_client, shops, secrets) -> None:
    assert api_client.get(f"{ADMIN}/shops").status_code == 401


# ─── 纳管店铺列表 ────────────────────────────────────────────────


def test_shop_list_is_empty_before_onboarding(api_client, keys, shops, secrets) -> None:
    body = api_client.get(f"{ADMIN}/shops", headers=_auth(keys["readwrite"])).json()
    assert body["items"] == []


def test_shop_list_reports_published_and_force_versions(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])
    force_response = _force(
        api_client, rw, shops["a"], expected_version="1", request_id=str(uuid.uuid4())
    )
    assert force_response.status_code == 200

    body = api_client.get(f"{ADMIN}/shops", headers=_auth(rw)).json()
    item = next(row for row in body["items"] if row["shopPk"] == str(shops["a"]))
    assert item["sellerId"] == SHOP_A
    assert item["onboarded"] is True
    assert item["publishedVersion"] == "2"
    assert item["lastForceVersion"] == "2"
    assert item["isForce"] is True
    assert item["sourceVersions"] == {"global": "1", "shop": None}


def test_shop_list_reports_a_drafted_but_unpublished_shop(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_shop_draft(api_client, rw, shops["b"], _shop_override())
    body = api_client.get(f"{ADMIN}/shops", headers=_auth(rw)).json()
    item = next(row for row in body["items"] if row["shopPk"] == str(shops["b"]))
    assert item["onboarded"] is False
    assert item["publishedVersion"] is None
    assert item["lastForceVersion"] is None
    assert item["shopDraftVersion"] == 1


def test_shop_list_never_returns_secret_plaintext(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])
    raw = api_client.get(f"{ADMIN}/shops", headers=_auth(rw)).text
    assert HOOK_PLAINTEXT not in raw
    assert SIGNING_PLAINTEXT not in raw
    assert "encrypted_value" not in raw


# ─── 全局默认草稿 ────────────────────────────────────────────────


def test_defaults_draft_starts_at_version_zero(
    api_client, keys, shops, secrets
) -> None:
    body = api_client.get(DEFAULTS_DRAFT, headers=_auth(keys["readwrite"])).json()
    assert body["configKey"] == "monitor.defaults"
    assert body["draftPayload"] is None
    assert body["draftVersion"] == 0
    assert body["publishedVersion"] is None


def test_defaults_draft_round_trips_secret_references(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    response = _put_defaults(api_client, rw)
    assert response.status_code == 200
    body = response.json()
    assert body["draftVersion"] == 1
    assert body["draftPayload"]["feishuWebhookUrl"] == f"secret://{HOOK_SECRET}"

    fetched = api_client.get(DEFAULTS_DRAFT, headers=_auth(rw)).json()
    assert fetched["draftPayload"] == _complete_settings()
    # 管理面永不回显解密后的密钥。
    assert HOOK_PLAINTEXT not in api_client.get(DEFAULTS_DRAFT, headers=_auth(rw)).text


def test_defaults_draft_rejects_a_stale_expected_version(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    assert _put_defaults(api_client, rw, expected=0).status_code == 409
    assert _put_defaults(api_client, rw, expected=1).status_code == 200


def test_defaults_draft_rejects_an_incomplete_payload(
    api_client, keys, shops, secrets
) -> None:
    incomplete = _complete_settings()
    del incomplete["autoCloseEnabled"]
    assert _put_defaults(api_client, keys["readwrite"], incomplete).status_code == 422


def test_defaults_draft_rejects_a_forbidden_wire_field(
    api_client, keys, shops, secrets
) -> None:
    payload = _complete_settings() | {"monitorEnabled": True}
    assert _put_defaults(api_client, keys["readwrite"], payload).status_code == 422


def test_defaults_draft_rejects_an_unknown_field(
    api_client, keys, shops, secrets
) -> None:
    payload = _complete_settings() | {"serviceUrl": "https://example.invalid"}
    assert _put_defaults(api_client, keys["readwrite"], payload).status_code == 422


def test_draft_rejects_a_secret_reference_outside_the_release_whitelist(
    api_client, keys, shops, secrets
) -> None:
    """§4.3：只有白名单飞书字段允许以 secret:// 交付。"""

    payload = _complete_settings()
    payload["campaignThresholds"] = {
        "TEST_campaign": {"spendThreshold": f"secret://{HOOK_SECRET}"}
    }
    assert _put_defaults(api_client, keys["readwrite"], payload).status_code == 422


# ─── 白名单飞书字段不得以明文入库/回显（Terra P1-1）────────────────


@pytest.mark.parametrize(
    "field,literal",
    [
        ("feishuWebhookUrl", "https://open.feishu.cn/hook/LITERAL_LEAK"),
        ("feishuSigningSecret", "LITERAL_SIGNING_SECRET"),
        ("feishuAppId", "LITERAL_APP_ID"),
        ("feishuAppSecret", "LITERAL_APP_SECRET"),
    ],
)
def test_defaults_draft_rejects_a_literal_feishu_secret(
    api_client, keys, shops, secrets, field, literal
) -> None:
    rw = keys["readwrite"]
    payload = _complete_settings()
    payload[field] = literal
    response = _put_defaults(api_client, rw, payload)
    assert response.status_code == 422
    # 被拒的载荷不得留在任何地方：草稿版本仍为零，且字面量不在任何响应里。
    stored = api_client.get(DEFAULTS_DRAFT, headers=_auth(rw)).json()
    assert stored["draftVersion"] == 0
    assert stored["draftPayload"] is None
    assert literal not in response.text
    assert literal not in api_client.get(DEFAULTS_DRAFT, headers=_auth(rw)).text


def test_shop_draft_rejects_a_literal_feishu_secret(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    response = _put_shop_draft(
        api_client, rw, shops["a"], {"feishuAppSecret": "LITERAL_SHOP_SECRET"}
    )
    assert response.status_code == 422
    stored = api_client.get(
        f"{ADMIN}/shops/{shops['a']}/draft", headers=_auth(rw)
    ).json()
    assert stored["draftVersion"] == 0
    assert stored["draftPayload"] is None


def test_defaults_draft_accepts_a_reference_on_a_released_field(
    api_client, keys, shops, secrets
) -> None:
    """拒绝字面量不能顺带拒绝正确的 secret:// 引用（§4.3 的正常路径）。"""

    response = _put_defaults(api_client, keys["readwrite"])
    assert response.status_code == 200
    assert (
        response.json()["draftPayload"]["feishuAppSecret"] == f"secret://{APP_SECRET}"
    )
    assert response.json()["draftPayload"]["feishuAppId"] == ""


def test_publish_rejects_a_literal_feishu_secret_before_freezing_a_delivery(
    api_client, keys, shops, secrets
) -> None:
    """历史遗留的字面量 payload 也不能绕过草稿校验进入交付快照。"""

    rw = keys["readwrite"]
    with _session(api_client) as session:
        session.add(
            RuntimeConfigItem(
                config_key="monitor.defaults",
                display_name="TEST monitor defaults",
                json_schema=admin.defaults_settings_schema(),
                draft_payload=_complete_settings()
                | {"feishuWebhookUrl": "https://open.feishu.cn/hook/LITERAL_LEAK"},
                draft_version=1,
            )
        )
        session.commit()

    # 草稿本身可以存在（直接写库），但发布必须拒绝并整体回滚。
    assert _publish_defaults(api_client, rw, expected=1).status_code == 422
    with _session(api_client) as session:
        item = session.get(RuntimeConfigItem, "monitor.defaults")
        published = None if item is None else item.published_version
    assert published is None, "被拒绝的发布不得留下 revision"


def test_shop_draft_accepts_an_empty_override(api_client, keys, shops, secrets) -> None:
    response = _put_shop_draft(api_client, keys["readwrite"], shops["b"], {})
    assert response.status_code == 200
    assert response.json()["configKey"] == f"monitor.shop.{shops['b']}"


def test_shop_draft_for_an_unknown_shop_is_404(
    api_client, keys, shops, secrets
) -> None:
    assert (
        _put_shop_draft(
            api_client, keys["readwrite"], 999999, _shop_override()
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            f"{ADMIN}/shops/999999/draft", headers=_auth(keys["readwrite"])
        ).status_code
        == 404
    )


def test_shop_draft_rejects_an_unknown_shop_pk_format(
    api_client, keys, shops, secrets
) -> None:
    assert api_client.get(
        f"{ADMIN}/shops/not-a-number/draft", headers=_auth(keys["readwrite"])
    ).status_code in {404, 422}


# ─── 发布：合成与冻结 ────────────────────────────────────────────


def test_publishing_defaults_before_a_draft_is_422(
    api_client, keys, shops, secrets
) -> None:
    assert (
        _publish_defaults(api_client, keys["readwrite"], expected=0).status_code == 422
    )


def test_publishing_defaults_with_a_stale_version_is_409(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    assert _publish_defaults(api_client, rw, expected=0).status_code == 409


def test_global_publish_reaches_already_onboarded_shops(
    api_client, keys, shops, secrets
) -> None:
    """全局发布要为**已纳管**店铺重新冻结交付，而不是只发源 revision。"""

    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])  # 纳管 A：delivery 1

    # 新的全局默认值 + A 的新覆盖草稿（草稿尚未发布，不得进入合成）。
    _put_defaults(
        api_client, rw, _complete_settings(globalThreshold="20.0"), expected=2
    )
    _put_shop_draft(api_client, rw, shops["a"], _shop_override(globalThreshold="99.0"))
    assert _publish_defaults(api_client, rw, expected=3).status_code == 200

    body = _delivery(api_client, keys["monitor"], SHOP_A).json()
    assert body["current"]["version"] == "2"
    # 只带已发布 revision：全局升到 v2，未发布的店铺草稿被忽略。
    assert body["current"]["sourceVersions"]["global"] == "2"
    assert body["current"]["sourceVersions"]["shop"] is None
    assert body["current"]["payload"]["settings"]["globalThreshold"] == "20.0"
    # §4.3 限定例外：白名单飞书字段在有效店铺边界解冻。
    assert body["current"]["payload"]["settings"]["feishuWebhookUrl"] == HOOK_PLAINTEXT
    assert (
        body["current"]["payload"]["settings"]["feishuSigningSecret"]
        == SIGNING_PLAINTEXT
    )


def test_shop_publish_without_a_published_default_is_409(
    api_client, keys, shops, secrets
) -> None:
    response = _publish_shop(api_client, keys["readwrite"], shops["a"])
    assert response.status_code == 409
    assert _delivery_state(api_client, shops["a"]) is None


def test_shop_publish_composes_global_with_the_shop_override(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _put_shop_draft(
        api_client, rw, shops["a"], _shop_override(autoCloseMaxSkuOrders=None)
    )
    assert _publish_shop(api_client, rw, shops["a"], expected=1).status_code == 200

    settings = _delivery(api_client, keys["monitor"], SHOP_A).json()["current"][
        "payload"
    ]["settings"]
    assert settings["globalThreshold"] == "12.5"  # 店铺覆盖（Decimal 序列化为字符串）
    assert settings["reportQueryRecentDays"] == 30  # 继承全局（整数保持整数）
    assert settings["autoCloseMaxSkuOrders"] is None  # 显式 null 禁用


def test_shop_publish_without_an_override_draft_inherits_global(
    api_client, keys, shops, secrets
) -> None:
    """§5.2：无已发布店铺覆盖时 sourceVersions.shop 为 null，不杜撰 revision。"""

    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    assert _publish_shop(api_client, rw, shops["a"], expected=0).status_code == 200

    body = _delivery(api_client, keys["monitor"], SHOP_A).json()
    assert body["current"]["sourceVersions"] == {"global": "1", "shop": None}


def test_global_publish_rolls_back_entirely_when_one_shop_cannot_compose(
    api_client, keys, shops, secrets
) -> None:
    """§5.3：任一步失败整体回滚，不出现半份指针或半份 revision。"""

    rw = keys["readwrite"]
    # 先让 A 店纳管。
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])
    before = _delivery(api_client, keys["monitor"], SHOP_A).json()["current"]["version"]

    # 直接种入一条「单独看合法、合成后非法」的已发布店铺 revision：
    # max=60 自身满足 >=30，但与全局 min=900 合成后违反 min<=max。
    with _session(api_client) as session:
        item = session.get(RuntimeConfigItem, f"monitor.shop.{shops['b']}")
        if item is None:
            from tts_erp_v2.monitor_config.admin import override_settings_schema

            item = RuntimeConfigItem(
                config_key=f"monitor.shop.{shops['b']}",
                display_name="TEST monitor shop b",
                json_schema=override_settings_schema(),
            )
            session.add(item)
            session.flush()
        # published_version 必须指向刚插入的 revision，否则合成看不到该覆盖。
        item.published_version = 1
        item.draft_version = 1
        session.flush()
        session.add(
            RuntimeConfigRevision(
                config_key=item.config_key,
                version=1,
                payload={"notificationIntervalMaxSeconds": 60},
                comment="TEST un-composable",
                created_by="TEST_monitor_admin_actor",
            )
        )
        session.add(
            MonitorConfigDelivery(
                shop_pk=shops["b"],
                version=1,
                source_global_version=1,
                source_shop_version=1,
                schema_version=1,
                content_hash="sha256:" + "0" * 64,
                encrypted_payload=b"TEST-UNUSED",
                is_force=False,
                created_by="TEST_monitor_admin_actor",
            )
        )
        session.add(MonitorShopState(shop_pk=shops["b"], current_delivery_version=1))
        session.commit()

    # 重新写入全局草稿（发布后 draft_version 已递增，必须用当前版本号），
    # 保证接下来的发布真的走到合成阶段，而不是在“无草稿”处就返回 422。
    _put_defaults(
        api_client, rw, _complete_settings(globalThreshold="20.0"), expected=2
    )
    assert _publish_defaults(api_client, rw, expected=3).status_code == 422

    # 回滚：全局没有新 revision，A 店交付版本未推进。
    with _session(api_client) as session:
        item = session.get(RuntimeConfigItem, "monitor.defaults")
        published_version = None if item is None else item.published_version
    assert published_version == 1, (
        f"全局 revision 不得提交；实际 published_version={published_version}"
    )
    assert (
        _delivery(api_client, keys["monitor"], SHOP_A).json()["current"]["version"]
        == before
    ), "已纳管店铺的交付不得在整体回滚后被推进"


def test_secret_update_requires_republish_before_a_delivery_changes(
    api_client, keys, shops, secrets
) -> None:
    """§11.1.9：改密钥不会偷偷改变既有 delivery/hash。"""

    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])
    first = _delivery(api_client, keys["monitor"], SHOP_A).json()

    updated = "https://open.feishu.cn/hook/TEST_HOOK_UPDATED"
    saved = api_client.put(
        f"/v2/config/runtime/secrets/{HOOK_SECRET}",
        json={"value": updated},
        headers=_auth(rw),
    )
    assert saved.status_code == 200
    frozen = _delivery(api_client, keys["monitor"], SHOP_A).json()
    assert frozen["current"]["contentHash"] == first["current"]["contentHash"]
    assert (
        frozen["current"]["payload"]["settings"]["feishuWebhookUrl"] == HOOK_PLAINTEXT
    )

    _put_defaults(api_client, rw, expected=2)
    _publish_defaults(api_client, rw, expected=3)
    after = _delivery(api_client, keys["monitor"], SHOP_A).json()
    assert after["current"]["contentHash"] != first["current"]["contentHash"]
    assert after["current"]["payload"]["settings"]["feishuWebhookUrl"] == updated


def test_publish_responses_never_carry_secret_plaintext(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    put_text = _put_defaults(api_client, rw).text
    publish_text = _publish_defaults(api_client, rw, expected=1).text
    shop_text = _publish_shop(api_client, rw, shops["a"]).text
    for raw in (put_text, publish_text, shop_text):
        assert HOOK_PLAINTEXT not in raw
        assert SIGNING_PLAINTEXT not in raw


# ─── 强制覆盖 ────────────────────────────────────────────────────


@pytest.fixture()
def published_shop(api_client, keys, shops, secrets) -> int:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    _publish_shop(api_client, rw, shops["a"])
    return shops["a"]


def test_force_requires_a_published_delivery(api_client, keys, shops, secrets) -> None:
    response = _force(
        api_client,
        keys["readwrite"],
        shops["b"],
        expected_version="1",
        request_id=str(uuid.uuid4()),
    )
    assert response.status_code == 409


def test_force_requires_a_request_id(api_client, keys, published_shop) -> None:
    response = _force(
        api_client, keys["readwrite"], published_shop, expected_version="1"
    )
    assert response.status_code == 422


def test_force_rejects_a_stale_expected_version(
    api_client, keys, published_shop
) -> None:
    response = _force(
        api_client,
        keys["readwrite"],
        published_shop,
        expected_version="9",
        request_id=str(uuid.uuid4()),
    )
    assert response.status_code == 409


def test_force_moves_both_pointers_even_with_identical_content(
    api_client, keys, published_shop
) -> None:
    """§11.1.3：内容相同也必须产生新 version/ETag。"""

    rw = keys["readwrite"]
    before = _delivery(api_client, keys["monitor"], SHOP_A)
    response = _force(
        api_client,
        rw,
        published_shop,
        expected_version="1",
        request_id=str(uuid.uuid4()),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deliveryVersion"] == "2"
    assert body["isForce"] is True

    after = _delivery(api_client, keys["monitor"], SHOP_A)
    assert after.headers["etag"] != before.headers["etag"]
    assert (
        after.json()["current"]["contentHash"]
        == before.json()["current"]["contentHash"]
    )
    assert after.json()["force"]["version"] == "2"
    assert after.json()["force"]["target"]["version"] == "2"


def test_force_replays_the_same_request_id(api_client, keys, published_shop) -> None:
    """丢失响应后重试必须可恢复（§5.4）。"""

    rw = keys["readwrite"]
    request_id = str(uuid.uuid4())
    first = _force(
        api_client, rw, published_shop, expected_version="1", request_id=request_id
    )
    second = _force(
        api_client, rw, published_shop, expected_version="1", request_id=request_id
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["deliveryVersion"] == second.json()["deliveryVersion"] == "2"
    state = _delivery_state(api_client, published_shop)
    assert state is not None and state.current_delivery_version == 2


def test_force_rejects_the_same_request_id_with_different_metadata(
    api_client, keys, published_shop
) -> None:
    """expected-version 已是旧值；幂等键相同但请求不同 → 409。"""

    rw = keys["readwrite"]
    request_id = str(uuid.uuid4())
    _force(api_client, rw, published_shop, expected_version="1", request_id=request_id)
    response = _force(
        api_client, rw, published_shop, expected_version="2", request_id=request_id
    )
    assert response.status_code == 409


def test_idempotent_replay_wins_over_a_stale_expected_version(
    api_client, keys, published_shop
) -> None:
    """幂等检查先于 expected-version 冲突判断（§5.4）。"""

    rw = keys["readwrite"]
    request_id = str(uuid.uuid4())
    _force(api_client, rw, published_shop, expected_version="1", request_id=request_id)
    replay = _force(
        api_client, rw, published_shop, expected_version="1", request_id=request_id
    )
    assert replay.status_code == 200
    assert replay.json()["deliveryVersion"] == "2"


def test_an_ordinary_publish_keeps_the_latest_force_pointer(
    api_client, keys, published_shop
) -> None:
    rw = keys["readwrite"]
    _force(
        api_client,
        rw,
        published_shop,
        expected_version="1",
        request_id=str(uuid.uuid4()),
    )
    _put_defaults(api_client, rw, expected=2)
    _publish_defaults(api_client, rw, expected=3)
    body = _delivery(api_client, keys["monitor"], SHOP_A).json()
    assert body["force"]["version"] == "2"
    assert body["current"]["version"] != body["force"]["version"]


# ─── 通用运行配置入口必须拒绝保留键（§5.1）─────────────────────


@pytest.mark.parametrize(
    "method,path_suffix,body",
    [
        (
            "post",
            "/runtime/items",
            {
                "configKey": "monitor.defaults",
                "displayName": "TEST bypass",
                "draftPayload": _complete_settings(),
            },
        ),
        (
            "put",
            "/runtime/items/monitor.defaults/draft",
            {"expectedDraftVersion": 0, "payload": _complete_settings()},
        ),
        (
            "post",
            "/runtime/items/monitor.defaults/publish",
            {"expectedDraftVersion": 1},
        ),
        (
            "post",
            "/runtime/items/monitor.defaults/rollback",
            {"expectedDraftVersion": 1, "targetVersion": 1},
        ),
        ("post", "/runtime/items/monitor.defaults/retire", None),
        ("post", "/runtime/items/monitor.defaults/restore", None),
    ],
)
def test_generic_runtime_mutation_refuses_the_reserved_namespace(
    api_client, keys, shops, secrets, method, path_suffix, body
) -> None:
    response = api_client.request(
        method, f"/v2/config{path_suffix}", json=body, headers=_auth(keys["admin"])
    )
    assert response.status_code == 409
    assert "/v2/monitor/admin" in response.json()["detail"]


def test_generic_runtime_snapshot_never_decrypts_monitor_secrets(
    api_client, keys, shops, secrets
) -> None:
    rw = keys["readwrite"]
    _put_defaults(api_client, rw)
    _publish_defaults(api_client, rw, expected=1)
    raw = api_client.get("/v2/config/runtime/snapshot", headers=_auth(rw)).text
    assert f"secret://{HOOK_SECRET}" in raw
    assert HOOK_PLAINTEXT not in raw
