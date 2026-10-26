"""Real current-clone UI commands, independent persistence and response loss.

No screenshots/traces/console/body recording: the cloned list can contain
non-TEST rows. Only this test's unpredictable TEST identities are inspected.
"""
from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.frontend_p2_accounts import seed_accounts
from tests.support.frontend_p2_binding import checked_session, current_binding
from tests.support.frontend_p2_browser import fill_sensitive, live_browser
from tests.support.frontend_p2_canary import (
    VerifiedDataProbe,
    _bound_json,
    require_data_probe,
    seed_and_check_canary,
)
from tests.support.frontend_p2_live import live_api_server
from tests.support.spu_price_stats_live import api_env
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.config import EnumMap
from tts_erp_v2.db.models.integration import OAuthState, TikTokAppCredential
from tts_erp_v2.proxy import token_service

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


@contextmanager
def independent(probe) -> Iterator[Session]:
    value, binding = require_data_probe(probe)
    engine = create_engine(value)
    try:
        with checked_session(binding, engine, sessionmaker(bind=engine)) as session:
            yield session
    finally:
        engine.dispose()


def _command_facts_absent(
    session: Session, *, shop_id: str, service_id: str, enum_value: str,
) -> bool:
    """Identity-only absence, including other shops referencing the exact app."""
    return (
        session.scalar(select(ChannelAccount.id).where(ChannelAccount.shop_id == shop_id)) is None
        and session.scalar(
            select(ChannelAccount.id).where(ChannelAccount.service_id == service_id)
        ) is None
        and session.scalar(
            select(TikTokAppCredential.service_id).where(TikTokAppCredential.service_id == service_id)
        ) is None
        and session.scalar(
            select(OAuthState.id).where(OAuthState.extra["service_id"].astext == service_id)
        ) is None
        and session.scalar(
            select(EnumMap.id).where(EnumMap.enum_type == "order_status", EnumMap.enum_value == enum_value)
        ) is None
    )


@contextmanager
def owned_command_facts(
    probe: VerifiedDataProbe,
    *,
    shop_id: str,
    service_id: str,
    enum_value: str,
    profile: Literal["live", "errors"],
) -> Iterator[None]:
    """Own only prior-absent command intents inside the original live child."""
    require_data_probe(probe)
    profiles = {
        "live": (
            "TEST_sid_", "TEST_enum_",
            ("TEST_native_shop", "TEST_native_edited"),
            ("TEST_native_label", "TEST_committed_response_lost"), 7,
        ),
        "errors": (
            "TEST_error_sid_", "TEST_error_enum_",
            ("TEST_error_shop", "TEST_revisit", "TEST_wire500_committed"),
            ("TEST_enum_wire500", "TEST_after_cooldown_write"), 3,
        ),
    }
    selected = profiles.get(profile)
    intent_valid = (
        selected is not None
        and re.fullmatch(r"99[0-9]{14}", shop_id) is not None
        and re.fullmatch(re.escape(selected[0]) + r"[0-9a-f]{16}", service_id) is not None
        and re.fullmatch(re.escape(selected[1]) + r"[0-9a-f]{16}", enum_value) is not None
    )
    assert intent_valid, "TEST command facts intent refused"
    with independent(probe) as session:
        prior_absent = _command_facts_absent(
            session, shop_id=shop_id, service_id=service_id, enum_value=enum_value,
        )
    assert prior_absent, "TEST command facts already existed before their owner"
    try:
        yield
    finally:
        with independent(probe) as session:
            shops = session.execute(select(
                ChannelAccount.id, ChannelAccount.platform, ChannelAccount.shop_id,
                ChannelAccount.account_name, ChannelAccount.service_id, ChannelAccount.credential_id,
            ).where(ChannelAccount.shop_id == shop_id)).all()
            app_id = session.scalar(
                select(TikTokAppCredential.service_id).where(TikTokAppCredential.service_id == service_id)
            )
            states = session.execute(select(
                OAuthState.id, OAuthState.provider, OAuthState.consumed_at,
            ).where(OAuthState.extra["service_id"].astext == service_id)).all()
            enums = session.execute(select(
                EnumMap.id, EnumMap.enum_type, EnumMap.enum_value, EnumMap.label_zh, EnumMap.sort_order,
            ).where(EnumMap.enum_type == "order_status", EnumMap.enum_value == enum_value)).all()
            references = session.scalars(
                select(ChannelAccount.id).where(ChannelAccount.service_id == service_id)
            ).all()
            # Validate every recovered row and reference before issuing any delete.
            ownership_valid = (
                len(shops) <= 1
                and all(
                    isinstance(shop.id, int) and shop.id > 0
                    and shop.platform == "tiktok" and shop.shop_id == shop_id
                    and shop.account_name in selected[2]
                    and shop.service_id in (None, service_id) and shop.credential_id is None
                    for shop in shops
                )
                and app_id in (None, service_id)
                and all(reference in {shop.id for shop in shops} for reference in references)
                and (not states or (
                    profile == "live" and len(states) == 1 and app_id == service_id
                    and any(shop.service_id == service_id for shop in shops)
                ))
                and all(
                    isinstance(state.id, int) and state.id > 0
                    and state.provider == "tiktok" and state.consumed_at is None
                    for state in states
                )
                and len(enums) <= 1
                and all(
                    isinstance(fact.id, int) and fact.id > 0
                    and fact.enum_type == "order_status" and fact.enum_value == enum_value
                    and fact.label_zh in selected[3] and fact.sort_order == selected[4]
                    for fact in enums
                )
            )
            assert ownership_valid, "TEST command facts cleanup ownership refused"
            for fact in enums:
                session.execute(delete(EnumMap).where(
                    EnumMap.id == fact.id, EnumMap.enum_type == "order_status", EnumMap.enum_value == enum_value,
                ))
            for state in states:
                session.execute(delete(OAuthState).where(
                    OAuthState.id == state.id, OAuthState.provider == "tiktok", OAuthState.consumed_at.is_(None),
                    OAuthState.extra["service_id"].astext == service_id,
                ))
            for shop in shops:
                session.execute(delete(ChannelAccount).where(
                    ChannelAccount.id == shop.id, ChannelAccount.platform == "tiktok", ChannelAccount.shop_id == shop_id,
                ))
            if app_id is not None:
                session.execute(delete(TikTokAppCredential).where(
                    TikTokAppCredential.service_id == app_id, TikTokAppCredential.service_id == service_id,
                ))
            session.commit()
        # A new original independent session observes committed absence, not our own writes.
        with independent(probe) as session:
            fresh_absent = _command_facts_absent(
                session, shop_id=shop_id, service_id=service_id, enum_value=enum_value,
            )
        assert fresh_absent, "TEST command facts survived owner cleanup"


class _InjectedCommandFailure(RuntimeError):
    """Constant injected fault after a real commit, not an API mock."""


def test_owned_command_facts_cleanup_after_committed_failure() -> None:
    """Check the same fact lifetime without inventing another fixture factory."""
    value, _binding = current_binding()
    with live_api_server(value) as child:
        probe = seed_and_check_canary(child, prefix="")
        shop_id = "99" + str(secrets.randbelow(10**14)).zfill(14)
        service_id = "TEST_sid_" + secrets.token_hex(8)
        enum_value = "TEST_enum_" + secrets.token_hex(8)
        committed = False
        with (
            pytest.raises(_InjectedCommandFailure, match="^TEST_after_committed_shop$"),
            owned_command_facts(
                probe, shop_id=shop_id, service_id=service_id,
                enum_value=enum_value, profile="live",
            ),
        ):
            with independent(probe) as session:
                session.add(ChannelAccount(
                    platform="tiktok", shop_id=shop_id,
                    account_name="TEST_native_shop", region="VN", status="active",
                ))
                session.commit()
                committed = True
            # No internal PK is passed to the lifetime's caller. SQLAlchemy
            # itself may have assigned one during the real flush/commit.
            raise _InjectedCommandFailure("TEST_after_committed_shop")
        assert committed, "TEST committed-failure injection was not reached"
        with independent(probe) as session:
            survived = session.scalar(
                select(ChannelAccount.id).where(ChannelAccount.shop_id == shop_id)
            ) is not None
        assert not survived, "TEST command facts survived committed setup failure"


def public_get(probe, path):
    return _bound_json(probe.child, probe.prefix + path, {"Authorization": "Bearer " + probe.readonly_key})


def command_count(gate, method, path):
    return sum(verb == method and target == path for verb, target, _csrf in gate.calls)


def register_body(gate):
    return {"platform": "tiktok", "shop_id": gate.shop_id, "account_name": "TEST_native_shop", "region": "VN"}


def denied_register(page, probe, gate, *, csrf):
    code = page.evaluate("""({url, body, csrf}) => fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type':'application/json', ...(csrf ? {'X-Requested-With':'tts-erp'} : {})},
      body: JSON.stringify(body)}).then(response => response.status)""",
                         {"url": probe.prefix + "/v2/admin/shops/register", "body": register_body(gate), "csrf": csrf})
    assert code == 403
    with independent(probe) as session:
        assert session.scalar(select(ChannelAccount.id).where(ChannelAccount.shop_id == gate.shop_id)) is None


def shop_task(page, gate, probe):
    denied_register(page, probe, gate, csrf=False)
    count = command_count(gate, "POST", "/v2/admin/shops/register")
    page.get_by_role("button", name="注册店铺", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("店铺 ID", exact=True).fill(gate.shop_id)
    dialog.get_by_label("店铺名称", exact=True).fill("TEST_native_shop")
    dialog.get_by_label("区域", exact=True).fill("VN")
    dialog.get_by_role("button", name="保存店铺", exact=True).click()
    dialog.wait_for(state="hidden")
    row = page.get_by_role("row").filter(has=page.get_by_text(gate.shop_id, exact=True))
    row.wait_for()
    assert command_count(gate, "POST", "/v2/admin/shops/register") == count + 1
    with independent(probe) as session:
        shop = session.scalar(select(ChannelAccount).where(ChannelAccount.shop_id == gate.shop_id))
        assert shop is not None and shop.account_name == "TEST_native_shop" and shop.credential_id is None
        shop_pk = shop.id
        gate.shop_pks.add(shop_pk)  # Only after same-clone TEST identity confirmation.
    snapshot = public_get(probe, f"/v2/commerce/channel-accounts/{shop_pk}")
    assert isinstance(snapshot, dict) and snapshot["shop_id"] == gate.shop_id
    row.get_by_role("button", name="编辑元信息", exact=True).click()
    dialog.get_by_label("店铺名称", exact=True).fill("TEST_native_edited")
    dialog.get_by_label("开店日期", exact=True).fill("2026-01-02")
    dialog.get_by_role("button", name="保存店铺", exact=True).click()
    dialog.wait_for(state="hidden")
    expect(row.get_by_text("TEST_native_edited", exact=True)).to_be_visible()
    with independent(probe) as session:
        shop = session.get(ChannelAccount, shop_pk)
        assert shop is not None and shop.account_name == "TEST_native_edited"
        assert str(shop.opened_date) == "2026-01-02"
    app_key, secret = "TEST_key_" + secrets.token_hex(8), "TEST_secret_9_" + secrets.token_hex(16)
    row.get_by_role("button", name="配置 App", exact=True).click()
    field = dialog.get_by_label("App Secret", exact=True)
    secret_empty = field.input_value() == ""
    assert secret_empty and field.get_attribute("type") == "password"
    dialog.get_by_label("Service ID", exact=True).fill(gate.service_id)
    fill_sensitive(dialog.get_by_label("App Key", exact=True), app_key)
    fill_sensitive(field, secret)
    dialog.get_by_role("button", name="保存店铺", exact=True).click()
    dialog.wait_for(state="hidden")
    expect(row.get_by_text("已配置", exact=True)).to_be_visible()
    with independent(probe) as session:
        credentials = token_service.resolve_tiktok_app_credentials(session, service_id=gate.service_id)
        pair_matches = hmac.compare_digest(credentials.app_key, app_key) and hmac.compare_digest(credentials.app_secret, secret)
        assert pair_matches  # Never include credential values in assertion output.
    before = command_count(gate, "PATCH", f"/v2/admin/shops/{shop_pk}")
    row.get_by_role("button", name="配置 App", exact=True).click()
    rotated = "TEST_rotated_9_" + secrets.token_hex(16)
    fill_sensitive(dialog.get_by_label("App Key", exact=True), app_key)
    fill_sensitive(dialog.get_by_label("App Secret", exact=True), rotated)
    lost_before = gate.lost
    gate.drop_next = ("PATCH", f"/v2/admin/shops/{shop_pk}")
    dialog.get_by_role("button", name="保存店铺", exact=True).click()
    expect(dialog.get_by_role("button", name="查询核对", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="保存店铺", exact=True)).to_be_disabled()
    assert gate.lost == lost_before + 1 and gate.drop_next is None
    draft_preserved = dialog.get_by_label("App Secret", exact=True).input_value() == rotated
    assert draft_preserved
    with independent(probe) as session:
        credentials = token_service.resolve_tiktok_app_credentials(session, service_id=gate.service_id)
        rotation_matches = hmac.compare_digest(credentials.app_secret, rotated)
        assert rotation_matches
    dialog.get_by_role("button", name="查询核对", exact=True).click()
    expect(dialog.get_by_text("公开记录已读取，仅供核对，不代表原提交已确认。", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="核对后明确重新提交", exact=True)).to_be_enabled()
    page.wait_for_load_state("networkidle")
    assert command_count(gate, "PATCH", f"/v2/admin/shops/{shop_pk}") == before + 1
    dialog.get_by_role("button", name="取消", exact=True).click()
    trigger = row.get_by_role("button", name="配置 App", exact=True)
    page.wait_for_function("nodes => document.activeElement === nodes[0] || document.activeElement === nodes[1]",
                           arg=[trigger.element_handle(), row.element_handle()])
    trigger.click()
    secret_cleared = dialog.get_by_label("App Secret", exact=True).input_value() == ""
    assert secret_cleared
    dialog.get_by_role("button", name="取消", exact=True).click()
    page.reload(wait_until="networkidle")
    expect(row.get_by_text("已配置", exact=True)).to_be_visible()
    assert command_count(gate, "PATCH", f"/v2/admin/shops/{shop_pk}") == before + 1
    row.get_by_role("button", name="获取授权链接", exact=True).click()
    expect(page.get_by_role("region", name="授权链接")).to_be_visible()
    assert command_count(gate, "GET", "/v2/oauth/tiktok/authorize") == 1
    with independent(probe) as session:
        states = list(session.scalars(select(OAuthState).where(OAuthState.extra["service_id"].astext == gate.service_id)))
        assert len(states) == 1 and states[0].consumed_at is None
    page.reload(wait_until="networkidle")
    expect(row.get_by_text("TEST_native_edited", exact=True)).to_be_visible()
    expect(row.get_by_text("已配置", exact=True)).to_be_visible()
    assert command_count(gate, "GET", "/v2/oauth/tiktok/authorize") == 1
    snapshot = public_get(probe, f"/v2/commerce/channel-accounts/{shop_pk}")
    assert isinstance(snapshot, dict) and snapshot["account_name"] == "TEST_native_edited"


def enum_task(page, gate, probe):
    page.goto(probe.child.base + probe.prefix + "/v2/pages/enum-map", wait_until="networkidle")
    page.get_by_role("button", name=re.compile(" 分类$")).click()
    page.get_by_role("option", name="订单状态（order_status）", exact=True).click()
    page.get_by_role("button", name="新增映射", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("英文值", exact=True).fill(gate.enum_value)
    dialog.get_by_label("中文标签", exact=True).fill("TEST_native_label")
    dialog.get_by_label("排序", exact=True).fill("7")
    dialog.get_by_role("button", name="保存映射", exact=True).click()
    dialog.wait_for(state="hidden")
    row = page.get_by_role("row").filter(has=page.get_by_text(gate.enum_value, exact=True))
    row.wait_for()
    with independent(probe) as session:
        fact = session.scalar(select(EnumMap).where(EnumMap.enum_type == "order_status", EnumMap.enum_value == gate.enum_value))
        assert fact is not None and fact.label_zh == "TEST_native_label" and fact.sort_order == 7
        enum_pk = fact.id
        gate.enum_pks.add(enum_pk)
    rows = public_get(probe, "/v2/config/enum-map/list")
    assert isinstance(rows, list)
    assert sum(item["id"] == enum_pk and item["label_zh"] == "TEST_native_label" for item in rows) == 1
    row.get_by_role("button", name="编辑映射", exact=True).click()
    assert not dialog.get_by_label("英文值", exact=True).is_editable()
    dialog.get_by_label("中文标签", exact=True).fill("TEST_committed_response_lost")
    lost_before = gate.lost
    gate.drop_next = ("PUT", "/v2/config/enum-map")
    dialog.get_by_role("button", name="保存映射", exact=True).click()
    expect(dialog.get_by_role("button", name="查询核对", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="保存映射", exact=True)).to_be_disabled()
    assert dialog.get_by_label("中文标签", exact=True).input_value() == "TEST_committed_response_lost"
    assert gate.lost == lost_before + 1
    with independent(probe) as session:
        fact = session.get(EnumMap, enum_pk)
        assert fact is not None and fact.label_zh == "TEST_committed_response_lost"
    dialog.get_by_role("button", name="查询核对", exact=True).click()
    expect(dialog.get_by_text("公开列表已读取，当前记录如下；不证明原提交结果。", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="核对后明确重新提交", exact=True)).to_be_enabled()
    page.wait_for_load_state("networkidle")
    assert command_count(gate, "PUT", "/v2/config/enum-map") == 2  # Add + one lost edit; no replay.
    dialog.get_by_role("button", name="取消", exact=True).click()
    page.reload(wait_until="networkidle")
    page.get_by_role("button", name=re.compile(" 分类$")).click()
    page.get_by_role("option", name="订单状态（order_status）", exact=True).click()
    expect(row.get_by_text("TEST_committed_response_lost", exact=True)).to_be_visible()
    row.get_by_role("button", name="删除映射", exact=True).click()
    dialog.get_by_role("button", name="确认删除", exact=True).click()
    dialog.wait_for(state="hidden")
    expect(page.get_by_text("映射删除已确认", exact=True)).to_be_visible()
    with independent(probe) as session:
        assert session.get(EnumMap, enum_pk) is None
    rows = public_get(probe, "/v2/config/enum-map/list")
    assert isinstance(rows, list)
    assert all(item["id"] != enum_pk for item in rows)
    page.reload(wait_until="networkidle")
    expect(page.get_by_text(gate.enum_value, exact=True)).to_have_count(0)
    assert command_count(gate, "PUT", "/v2/config/enum-map") == 2
    assert command_count(gate, "DELETE", f"/v2/config/enum-map/{enum_pk}") == 1


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize(("role", "width"), [("viewer", 375), ("operator", 768), ("admin", 1440)])
def test_real_ui_persistence_permissions_and_lost_response(frontend_build, monkeypatch, prefix, role, width):
    value, _binding = current_binding()
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", api_env(value)["TTS_ERP_FERNET_KEY"])
    token_service._reset_for_testing()
    try:
        with live_api_server(value) as child:
            probe = seed_and_check_canary(child, prefix=prefix)
            account = next(account for account in seed_accounts(probe) if account.role_code == role)
            shop_id = "99" + str(secrets.randbelow(10**14)).zfill(14)
            service_id = "TEST_sid_" + secrets.token_hex(8)
            enum_value = "TEST_enum_" + secrets.token_hex(8)
            with owned_command_facts(
                probe, shop_id=shop_id, service_id=service_id,
                enum_value=enum_value, profile="live",
            ):
                with independent(probe) as session:
                    assert session.scalar(select(ChannelAccount.id).where(ChannelAccount.shop_id == shop_id)) is None
                with live_browser(probe, account, shop_id=shop_id, service_id=service_id,
                                  enum_value=enum_value, width=width) as (page, gate):
                    target = "/v2/pages/intercept-stats" if role == "viewer" else "/v2/pages/shops"
                    page.goto(child.base + prefix + "/v2/auth/login?" + urlencode({"next": prefix + target}), wait_until="networkidle")
                    page.get_by_label("用户名", exact=True).fill(account.username)
                    fill_sensitive(page.get_by_label("密码", exact=True), account.password)
                    page.get_by_role("button", name="登录", exact=True).click()
                    page.wait_for_url(child.base + prefix + target)
                    page.locator("#frontend-root").wait_for()
                    if role == "viewer":
                        expect(page.get_by_role("button", name="刷新统计", exact=True)).to_be_visible()
                        statistics = public_get(probe, "/v2/intercept/requests/stats?days=7")
                        assert isinstance(statistics, dict)
                        for label, key in (("总请求数", "total_requests"), ("白名单命中", "whitelisted_requests"),
                                           ("今日请求（UTC）", "today_requests"), ("错误请求", "error_requests")):
                            expect(page.get_by_role("region", name=label, exact=True).get_by_text(f"{statistics[key]:,}", exact=True)).to_be_visible()
                        expect(page.get_by_role("grid", name="每日请求（UTC）", exact=True)).to_be_visible()
                        denied_register(page, probe, gate, csrf=True)
                        response = page.goto(child.base + prefix + "/v2/pages/shops")
                        assert response is not None and response.status == 403
                    else:
                        shop_task(page, gate, probe)
                        if role == "admin":
                            enum_task(page, gate, probe)
                        else:
                            page.goto(child.base + prefix + "/v2/pages/enum-map", wait_until="networkidle")
                            expect(page.get_by_role("button", name="新增映射", exact=True)).to_be_disabled()
                            assert page.get_by_role("button", name="编辑映射", exact=True).count() == 0
                    assert all(csrf for method, _path, csrf in gate.calls if method in {"PUT", "PATCH", "DELETE"})
                    dimensions = page.evaluate("({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
                    assert dimensions["scroll"] <= dimensions["client"] + 1
                    assert not gate.violations
    finally:
        token_service._reset_for_testing()
