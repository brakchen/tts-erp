"""One root@1440 native case: the manual-costs workbench write path.

Repaired managed draft (artifact only; never applied to the repo). Real child, real
browser, real UI: the page writes the cost, then the test reads the fence's own
ledger and an independent checked session for the server's truth.

Ownership is anchored on the original ``manual_cost_facts`` tuples, never on caller
labels: the fixture's three product PKs and two shop PKs are read back and matched
against the fixture's own expected identities; only then must every globally
visible tiktok shop row be one of those two PKs. A foreign row, or an extra row
merely sharing the marker, stops the case before any browser work. The
session-scoped ``frontend_build`` fixture is requested so the compiled release
exists before the module-scoped ``clone`` child starts.
"""
from __future__ import annotations

import secrets
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select

from tests.browser.test_frontend_remaining_auth_native import open_login, repeat_enter
from tests.scripts import test_frontend_remaining_live_api as _facts_owner
from tests.scripts.test_frontend_remaining_live_api import OwnedClone, cost_history
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_browser import fill_sensitive, live_browser
from tests.support.frontend_remaining_targets import RemainingTargets
from tts_erp_v2.db.models.commerce import ChannelAccount, ChannelProduct

# Canonical fixture objects, bound once and with no leftover aliases.
clone = _facts_owner.clone
manual_cost_facts = _facts_owner.manual_cost_facts
del _facts_owner

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

SCREENSHOTS = Path(__file__).resolve().parents[2] / "logs/TEST_manual_native/screenshots"
COST_PATH = "/v2/reporting/manual-costs"
LOGIN_PATH = "/v2/auth/login"
AMOUNT = "12.3400"
CURRENCY = "CNY"
_PRODUCT_COLUMNS = (ChannelProduct.id, ChannelProduct.shop_pk, ChannelProduct.spu_id, ChannelProduct.title)
_SHOP_COLUMNS = (ChannelAccount.id, ChannelAccount.platform, ChannelAccount.shop_id, ChannelAccount.account_name)


def fixture_products(
    clone: OwnedClone, spu_pks: tuple[int, ...]
) -> list[tuple[int, int, str, str]]:
    """The fixture's own product rows only: id -> (shop_pk, external id, title)."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        rows = session.execute(
            select(*_PRODUCT_COLUMNS)
            .where(ChannelProduct.id.in_(spu_pks))
            .order_by(ChannelProduct.id)
        ).all()
    return [(int(row[0]), int(row[1]), str(row[2]), str(row[3])) for row in rows]


def owned_shop_rows(
    clone: OwnedClone, shop_pks: set[int]
) -> list[tuple[int, str, str, str]]:
    """The owned shop rows: id -> (platform, external shop id, account name)."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        rows = session.execute(
            select(*_SHOP_COLUMNS)
            .where(ChannelAccount.id.in_(sorted(shop_pks)))
            .order_by(ChannelAccount.id)
        ).all()
    return [(int(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows]


def visible_shop_rows(clone: OwnedClone) -> list[tuple[int, str]]:
    """Every tiktok shop row the account list exposes, in the server's order."""
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        rows = session.execute(
            select(ChannelAccount.id, ChannelAccount.account_name)
            .where(ChannelAccount.platform == "tiktok")
            .order_by(ChannelAccount.id)
            .limit(500)
        ).all()
    return [(int(row[0]), str(row[1])) for row in rows]


def operator_credentials(page: Page, clone: OwnedClone) -> None:
    """Fill the real login form for the seeded write account (owner helper)."""
    account = clone.accounts["operator"]
    page.get_by_label("用户名", exact=True).fill(account.username)
    fill_sensitive(page.get_by_label("密码", exact=True), account.password)


def test_manual_native_write_preview_and_readback(
    frontend_build: None,
    clone: OwnedClone,
    manual_cost_facts: tuple[str, tuple[int, ...], int],
) -> None:
    """Write one cost through the real UI, then read the server's own truth."""
    marker, spu_pks, fixture_shop_a_pk = manual_cost_facts
    shared_external = marker + "_shared"
    other_external = marker + "_other"

    # 1. The authoritative fixture tuples, read back and matched exactly.
    products = fixture_products(clone, spu_pks)
    by_pk = {pk: (shop_pk, spu_id, title) for pk, shop_pk, spu_id, title in products}
    set_exact = len(spu_pks) == 3 and len(set(spu_pks)) == 3 and set(by_pk) == set(spu_pks)
    first_pk, second_pk, third_pk = spu_pks
    first_row = by_pk.get(first_pk)
    second_row = by_pk.get(second_pk)
    third_row = by_pk.get(third_pk)
    tuples_ok = (
        set_exact
        and first_row == (fixture_shop_a_pk, shared_external, marker)
        and third_row == (fixture_shop_a_pk, other_external, marker)
        and second_row is not None
        and (second_row[0] != fixture_shop_a_pk and second_row[1] == shared_external and second_row[2] == marker)
    )
    assert tuples_ok, "TEST manual native: the fixture product tuples are not the expected three"
    shop_a_pk = fixture_shop_a_pk
    shop_b_pk = second_row[0]

    # 2. The two owned shop rows, checked field by field (no label-only oracle).
    owned_shop_pks = {shop_a_pk, shop_b_pk}
    shops = owned_shop_rows(clone, owned_shop_pks)
    expected_shops = {
        shop_a_pk: ("tiktok", marker + "_shop_a", marker),
        shop_b_pk: ("tiktok", marker + "_shop_b", marker),
    }
    shops_ok = (
        len(shops) == 2
        and {pk for pk, _p, _s, _n in shops} == owned_shop_pks
        and all(
            (platform, shop_id, account_name) == expected_shops[pk]
            for pk, platform, shop_id, account_name in shops
        )
    )
    assert shops_ok, "TEST manual native: the owned shop rows are not the expected two"

    # 3. Every globally visible tiktok shop row must be one of those two PKs.
    visible = visible_shop_rows(clone)
    visible_ok = bool(visible) and all(
        pk in owned_shop_pks and account_name == marker for pk, account_name in visible
    )
    assert visible_ok, "TEST manual native: a non-owned shop row is visible"
    first_visible_pk = visible[0][0]

    # 4. The target is the shared-id product of the shop the UI actually picked.
    target_by_shop = {shop_a_pk: first_pk, shop_b_pk: second_pk}
    remaining = RemainingTargets(
        marker=marker,
        shop_pks=frozenset(owned_shop_pks),
        product_pks=frozenset(spu_pks),
    )
    service_id = "TEST_native_" + secrets.token_hex(8)
    enum_value = "TEST_native_" + secrets.token_hex(8)
    shop_id = "99" + str(secrets.randbelow(10**14)).zfill(14)
    with live_browser(
        clone.probe,
        clone.accounts["operator"],
        shop_id=shop_id,
        service_id=service_id,
        enum_value=enum_value,
        width=1440,
        remaining=remaining,
    ) as (page, gate):
        open_login(page, clone, "/v2/pages/manual-costs")
        operator_credentials(page, clone)
        repeat_enter(page)
        page.wait_for_url(clone.child.base + clone.prefix + "/v2/pages/manual-costs")
        expect(page.get_by_role("button", name="退出登录", exact=True)).to_be_visible()
        page.wait_for_load_state("networkidle")
        logins = sum(1 for verb, target, _c in gate.calls if verb == "POST" and target == LOGIN_PATH)
        assert logins == 1, "TEST manual native: the UI login issued more than one request"

        # 5. The shop the UI actually selected, validated against the owned PKs.
        shop_select = page.get_by_label("当前店铺", exact=True)
        selected_value = shop_select.input_value()
        selected_ok = selected_value in {str(pk) for pk in owned_shop_pks}
        assert selected_ok, "TEST manual native: the selected shop is not an owned shop"
        default_contract = selected_value == str(first_visible_pk)
        assert default_contract, "TEST manual native: the UI default is not the first shop"
        selected_shop_pk = int(selected_value)
        target_pk = target_by_shop.get(selected_shop_pk)
        assert target_pk is not None, "TEST manual native: the selected shop has no shared product"
        shared_id = shared_external

        region = page.get_by_role("region", name="SPU 表格滚动区域", exact=True)
        row = region.get_by_role("row").filter(has=page.get_by_text(shared_id, exact=True))
        expect(row).to_have_count(1)

        row.get_by_role("button", name="编辑详情", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("heading", name="编辑成本草稿", exact=True)).to_be_visible()
        dialog.get_by_label("单位成本", exact=True).fill(AMOUNT)
        dialog.get_by_label("备注", exact=True).fill(marker)
        dialog.get_by_role("button", name="确认此行草稿", exact=True).click()

        confirm = page.get_by_role("dialog")
        expect(confirm.get_by_role("heading", name="确认逐行写入", exact=True)).to_be_visible()
        preview = (
            f"店铺 #{selected_shop_pk} · PK {target_pk} · SPU {shared_id} · "
            f"{AMOUNT} {CURRENCY} · 备注：{marker} · 生效：服务端当前 UTC"
        )
        expect(confirm.get_by_text(preview, exact=True)).to_have_count(1)
        confirm.get_by_role("button", name="确认逐行提交", exact=True).click()
        expect(row).to_contain_text("已确认成功")

        # 6. The fence's own ledger carries the ERP API status (no PW adapter).
        posts = [e for e in gate.responses if e[0] == "POST" and e[1] == COST_PATH]
        post_accepted = len(posts) == 1 and posts[0][2] == 201
        assert post_accepted, "TEST manual native: the cost POST was not accepted exactly once"

        # 7. The server's own row, read independently of the page.
        stored = cost_history(clone, (target_pk,))
        exact = (
            len(stored) == 1
            and stored[0][2] == Decimal(AMOUNT)
            and stored[0][3] == CURRENCY
            and stored[0][6] == marker
            and stored[0][5] is None
            and stored[0][4].utcoffset().total_seconds() == 0
        )  # the same expression the original manual-cost test uses on that column
        assert exact, "TEST manual native: the stored cost differs from the submitted one"

        # 8. The server's own history and catalogue, re-read after the write.
        page.get_by_role("button", name="最近提交", exact=True).click()
        history_region = page.get_by_role("region", name="历史表格滚动区域", exact=True)
        history_row = history_region.get_by_role("row").filter(has=page.get_by_text(shared_id, exact=True))
        expect(history_row).to_have_count(1)
        expect(history_row).to_contain_text(f"{AMOUNT} {CURRENCY}")

        page.get_by_role("button", name="全部 SPU", exact=True).click()
        page.get_by_role("button", name="刷新当前列表", exact=True).click()
        page.wait_for_load_state("networkidle")
        region = page.get_by_role("region", name="SPU 表格滚动区域", exact=True)
        row = region.get_by_role("row").filter(has=page.get_by_text(shared_id, exact=True))
        expect(row).to_have_count(1)
        expect(row).to_contain_text(f"人工：{AMOUNT} {CURRENCY}")

        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        page.evaluate("document.fonts.ready")
        label = "tts" if clone.prefix else "root"
        page.screenshot(
            path=str(SCREENSHOTS / f"TEST_manual_costs_{label}_1440.png"), full_page=True
        )
        page.get_by_role("button", name="退出登录", exact=True).click()
