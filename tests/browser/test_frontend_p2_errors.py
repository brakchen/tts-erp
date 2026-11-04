"""Real 422/409/401 plus explicit wire-429/500 recovery, not server quota tests."""
from __future__ import annotations

import hmac
import re
import secrets
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect
from sqlalchemy import select

from tests.browser.test_frontend_p2_live import (
    command_count,
    discard_shop_dialog,
    independent,
    owned_command_facts,
    public_get,
)
from tests.support.frontend_p2_accounts import seed_accounts
from tests.support.frontend_p2_binding import current_binding
from tests.support.frontend_p2_browser import fill_sensitive, live_browser
from tests.support.frontend_p2_canary import seed_and_check_canary
from tests.support.frontend_p2_live import live_api_server
from tests.support.spu_price_stats_live import api_env
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.config import EnumMap
from tts_erp_v2.proxy import token_service

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
REGISTER = "/v2/admin/shops/register"
ENUM = "/v2/config/enum-map"


def fill_registration(page, gate):
    page.get_by_role("button", name="手工补录", exact=True).click()
    dialog = page.get_by_role("dialog")
    for label, value in (("店铺 ID", gate.shop_id), ("店铺名称", "TEST_error_shop"), ("区域", "VN")):
        dialog.get_by_label(label, exact=True).fill(value)
    return dialog


def cooldown(page, gate, path, button, writer):
    # Install occurred before navigation. Pause before delivery of the 429, so
    # server I/O/action latency cannot eat into the 599/600-second assertions.
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))
    gate.inject_next("GET", path, 429, retry_after=600)
    refresh = page.get_by_role("button", name=button, exact=True)
    refresh.click()
    page.get_by_text("列表读取失败" if path == "/v2/commerce/channel-accounts" else "列表不可用", exact=True).wait_for()
    assert gate.injected[-1] == ("GET", path, 429) and gate.error_next is None
    reads = command_count(gate, "GET", path)
    writer()  # Confirmed write must not bypass the prior read deadline.
    assert command_count(gate, "GET", path) == reads
    page.clock.fast_forward(599_000)
    expect(refresh).to_be_disabled()
    assert command_count(gate, "GET", path) == reads
    page.clock.fast_forward(1000)
    expect(refresh).to_be_enabled()
    assert command_count(gate, "GET", path) == reads  # Expiry enables; never polls.
    refresh.click()
    page.wait_for_load_state("networkidle")
    assert command_count(gate, "GET", path) == reads + 1
    page.clock.resume()


@pytest.mark.parametrize("prefix", ["", "/tts"])
def test_real_http_rejection_and_wire_error_recovery(frontend_build, monkeypatch, prefix):
    value, _binding = current_binding()
    monkeypatch.setenv("TTS_ERP_FERNET_KEY", api_env(value)["TTS_ERP_FERNET_KEY"])
    token_service._reset_for_testing()
    try:
        with live_api_server(value) as child:
            probe = seed_and_check_canary(child, prefix=prefix)
            account = next(user for user in seed_accounts(probe) if user.role_code == "admin")
            shop_id = "99" + str(secrets.randbelow(10**14)).zfill(14)
            service_id = "TEST_error_sid_" + secrets.token_hex(8)
            enum_value = "TEST_error_enum_" + secrets.token_hex(8)
            with owned_command_facts(
                probe, shop_id=shop_id, service_id=service_id,
                enum_value=enum_value, profile="errors",
            ):
                with independent(probe) as session:
                    assert session.scalar(select(ChannelAccount.id).where(ChannelAccount.shop_id == shop_id)) is None
                with live_browser(probe, account, shop_id=shop_id, service_id=service_id,
                                  enum_value=enum_value, width=375) as (page, gate):
                    page.clock.install()
                    page.goto(child.base + prefix + "/v2/auth/login?" + urlencode({"next": prefix + "/v2/pages/shops"}), wait_until="networkidle")
                    page.get_by_label("用户名", exact=True).fill(account.username)
                    fill_sensitive(page.get_by_label("密码", exact=True), account.password)
                    page.get_by_role("button", name="登录", exact=True).click()
                    page.wait_for_url(child.base + prefix + "/v2/pages/shops")
                    secret = "TEST_error_secret_9_" + secrets.token_hex(12)
                    dialog = fill_registration(page, gate)
                    gate.invalid_region_next = True
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    expect(dialog.get_by_text("校验未通过，请检查输入（HTTP 422）", exact=True)).to_be_visible()
                    assert gate.mutated == 1 and ("POST", REGISTER, 422) in gate.responses
                    assert dialog.get_by_label("区域", exact=True).input_value() == "VN"
                    assert dialog.get_by_label("店铺名称", exact=True).input_value() == "TEST_error_shop"
                    expect(dialog.get_by_label("App Secret", exact=True)).to_have_count(0)
                    with independent(probe) as session:
                        assert session.scalar(select(ChannelAccount.id).where(ChannelAccount.shop_id == shop_id)) is None
                    assert command_count(gate, "POST", REGISTER) == 1
                    dialog.get_by_label("区域", exact=True).fill("TH")
                    validity = dialog.locator("input").evaluate_all("els => els.map(e => ({name:e.name,valid:e.validity.valid}))")
                    assert all(item["valid"] for item in validity), validity
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    dialog.wait_for(state="hidden")
                    row = page.get_by_role("row").filter(has=page.get_by_text(shop_id, exact=True))
                    row.wait_for()
                    with independent(probe) as session:
                        shop = session.scalar(select(ChannelAccount).where(ChannelAccount.shop_id == shop_id))
                        assert shop is not None and shop.service_id is None and shop.credential_id is None
                        shop_pk = shop.id
                        gate.shop_pks.add(shop_pk)
                    patch = f"/v2/admin/shops/{shop_pk}"
                    row.get_by_role("button", name="配置 App", exact=True).click()
                    dialog.get_by_label("Service ID", exact=True).fill(gate.service_id)
                    fill_sensitive(dialog.get_by_label("App Key", exact=True), "TEST_error_key")
                    fill_sensitive(dialog.get_by_label("App Secret", exact=True), secret)
                    gate.invalid_app_key_next = True
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    expect(dialog.get_by_text("校验未通过，请检查输入（HTTP 422）", exact=True)).to_be_visible()
                    assert gate.mutated == 2 and ("PATCH", patch, 422) in gate.responses
                    retained = dialog.get_by_label("App Secret", exact=True).input_value() == secret
                    assert retained and dialog.get_by_label("Service ID", exact=True).input_value() == gate.service_id
                    with independent(probe) as session:
                        shop = session.get(ChannelAccount, shop_pk)
                        assert shop is not None and shop.service_id is None
                    fill_sensitive(dialog.get_by_label("App Key", exact=True), "TEST_error_key_retry")
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    dialog.wait_for(state="hidden")
                    expect(row.get_by_text("已配置", exact=True)).to_be_visible()
                    row.get_by_role("button", name="配置 App", exact=True).click()
                    dialog.get_by_label("Service ID", exact=True).fill(gate.service_id + "_conflict")
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    expect(dialog.get_by_text("当前店铺的此 Service ID 尚未配置，请填写 App Key 和 App Secret 后保存", exact=True)).to_be_visible()
                    assert ("PATCH", patch, 409) in gate.responses
                    assert dialog.get_by_label("Service ID", exact=True).input_value() == gate.service_id + "_conflict"
                    with independent(probe) as session:
                        shop = session.get(ChannelAccount, shop_pk)
                        assert shop is not None and shop.service_id == gate.service_id
                        credentials = token_service.resolve_tiktok_app_credentials(session, shop_pk=shop_pk, service_id=gate.service_id)
                        matches = hmac.compare_digest(credentials.app_secret, secret)
                        assert matches
                    discard_shop_dialog(page, dialog)

                    def revisit_shop():
                        form = fill_registration(page, gate)
                        form.get_by_role("button", name="保存", exact=True).click()
                        form.wait_for(state="hidden")
                        expect(page.get_by_text("店铺信息已保存", exact=True)).to_be_visible()

                    cooldown(page, gate, "/v2/commerce/channel-accounts", "刷新店铺", revisit_shop)
                    row.wait_for()
                    row.get_by_role("button", name="编辑信息", exact=True).click()
                    dialog.get_by_label("开店日期", exact=True).fill("2026-02-03")
                    before = command_count(gate, "PATCH", patch)
                    gate.inject_next("PATCH", patch, 500)
                    dialog.get_by_role("button", name="保存", exact=True).click()
                    expect(dialog.get_by_role("button", name="读取当前记录核对", exact=True)).to_be_visible()
                    assert gate.injected[-1] == ("PATCH", patch, 500) and ("PATCH", patch, 200) in gate.responses
                    expect(dialog.get_by_role("button", name="保存", exact=True)).to_be_disabled()
                    with independent(probe) as session:
                        shop = session.get(ChannelAccount, shop_pk)
                        assert shop is not None and shop.account_name == "TEST_error_shop" and str(shop.opened_date) == "2026-02-03"
                    dialog.get_by_role("button", name="读取当前记录核对", exact=True).click()
                    expect(dialog.get_by_role("button", name="核对后重新保存", exact=True)).to_be_enabled()
                    discard_shop_dialog(page, dialog)
                    page.reload(wait_until="networkidle")
                    expect(row.get_by_text("2026-02-03", exact=True)).to_be_visible()
                    assert command_count(gate, "PATCH", patch) == before + 1
                    page.goto(child.base + prefix + "/v2/pages/enum-map", wait_until="networkidle")
                    page.get_by_role("button", name=re.compile(" 分类$")).click()
                    page.get_by_role("option", name="订单状态（order_status）", exact=True).click()

                    def enum_form(label):
                        page.get_by_role("button", name="新增映射", exact=True).click()
                        form = page.get_by_role("dialog")
                        form.get_by_label("英文值", exact=True).fill(gate.enum_value)
                        form.get_by_label("中文标签", exact=True).fill(label)
                        form.get_by_label("排序", exact=True).fill("3")
                        form.get_by_role("button", name="保存映射", exact=True).click()
                        return form

                    gate.inject_next("PUT", ENUM, 500)
                    form = enum_form("TEST_enum_wire500")
                    expect(form.get_by_role("button", name="查询核对", exact=True)).to_be_visible()
                    assert gate.injected[-1] == ("PUT", ENUM, 500) and ("PUT", ENUM, 200) in gate.responses
                    expect(form.get_by_role("button", name="保存映射", exact=True)).to_be_disabled()
                    assert form.get_by_label("中文标签", exact=True).input_value() == "TEST_enum_wire500"
                    with independent(probe) as session:
                        fact = session.scalar(select(EnumMap).where(EnumMap.enum_type == "order_status", EnumMap.enum_value == gate.enum_value))
                        assert fact is not None and fact.label_zh == "TEST_enum_wire500"
                    form.get_by_role("button", name="查询核对", exact=True).click()
                    expect(form.get_by_role("button", name="核对后明确重新提交", exact=True)).to_be_enabled()
                    form.get_by_role("button", name="取消", exact=True).click()
                    page.reload(wait_until="networkidle")
                    assert command_count(gate, "PUT", ENUM) == 1
                    rows = public_get(probe, "/v2/config/enum-map/list")
                    assert isinstance(rows, list) and any(item["enum_value"] == gate.enum_value for item in rows)

                    def update_enum():
                        form = enum_form("TEST_after_cooldown_write")
                        form.wait_for(state="hidden")
                        expect(page.get_by_text("映射保存已确认", exact=True)).to_be_visible()

                    # A reload resets category; select our known category before the read fault.
                    page.get_by_role("button", name=re.compile(" 分类$")).click()
                    page.get_by_role("option", name="订单状态（order_status）", exact=True).click()
                    cooldown(page, gate, ENUM + "/list", "刷新映射", update_enum)
                    logins = command_count(gate, "GET", "/v2/auth/login")
                    logout = page.evaluate("url => fetch(url, {method:'POST', credentials:'same-origin'}).then(r => r.status)", prefix + "/v2/auth/logout")
                    assert logout == 204
                    page.get_by_role("button", name="刷新映射", exact=True).click()
                    page.wait_for_url("**/v2/auth/login?*")
                    assert ("GET", ENUM + "/list", 401) in gate.responses
                    assert command_count(gate, "GET", "/v2/auth/login") == logins + 1
                    empty_password = page.get_by_label("密码", exact=True).input_value() == ""
                    assert empty_password and not gate.violations
    finally:
        token_service._reset_for_testing()
