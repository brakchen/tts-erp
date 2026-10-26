"""Native auth subset through the unchanged P2 fence and owned clone fixture.

Only blank, credential-free login pages are captured. No traces, video, raw
cookies, payloads or post-login lists are recorded. This does not exercise the
remaining eight business pages, external next targets or cross-origin uploads.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select

from tests.scripts.test_frontend_remaining_live_api import OwnedClone
from tests.scripts.test_frontend_remaining_live_api import clone as owned_clone
from tests.support.frontend_p2_binding import checked_session
from tests.support.frontend_p2_browser import BrowserFence, fill_sensitive, live_browser
from tts_erp_v2.accounts.models import UserSession
from tts_erp_v2.middleware.session_auth import SESSION_COOKIE_NAME

# Expose the original decorated fixture without wrapping or changing its scope.
clone = owned_clone
pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
SCREENSHOTS = Path(__file__).resolve().parents[2] / "logs/TEST_auth_native/screenshots"


def count(gate: BrowserFence, method: str, path: str) -> int:
    return sum(verb == method and target == path for verb, target, _csrf in gate.calls)


def open_login(page: Page, clone: OwnedClone, target: str) -> str:
    destination = clone.prefix + target
    url = (
        clone.child.base
        + clone.prefix
        + "/v2/auth/login?"
        + urlencode({"next": destination})
    )
    response = page.goto(url, wait_until="networkidle")
    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name="登录", exact=True)).to_be_visible()
    expect(page.get_by_role("form", name="登录表单", exact=True)).to_be_visible()
    return url


def credentials(page: Page, clone: OwnedClone, *, wrong: bool = False) -> None:
    account = clone.accounts["admin"]
    page.get_by_label("用户名", exact=True).fill(account.username)
    fill_sensitive(
        page.get_by_label("密码", exact=True),
        "TEST_wrong_9_" + secrets.token_hex(12) if wrong else account.password,
    )


def repeat_enter(page: Page) -> None:
    """Real keyboard repeats; no synthetic DOM events or mocked pending state."""
    page.get_by_label("密码", exact=True).focus()
    page.keyboard.down("Enter")
    page.keyboard.down("Enter")
    page.keyboard.up("Enter")


def arrived(page: Page, clone: OwnedClone, target: str) -> None:
    page.wait_for_url(clone.child.base + clone.prefix + target)
    expect(page.get_by_role("button", name="退出登录", exact=True)).to_be_visible()
    if target == "/v2/pages/shops":
        expect(page.get_by_role("button", name="注册店铺", exact=True)).to_be_visible()
    else:
        expect(page.get_by_role("button", name="新增映射", exact=True)).to_be_visible()
    page.wait_for_load_state("networkidle")


def cookie_facts(page: Page) -> list[tuple[str, bool, str]]:
    # Project only safe primitives before any assertion can produce diagnostics.
    return [
        (
            cookie.get("path", ""),
            cookie.get("httpOnly", False),
            cookie.get("sameSite", ""),
        )
        for cookie in page.context.cookies()
        if cookie.get("name") == SESSION_COOKIE_NAME
    ]


def me_facts(page: Page, clone: OwnedClone) -> tuple[int, bool, bool]:
    # Native same-origin fetch still traverses the canonical browser fence and
    # its nonce checks. No request context, alternate transport or raw DTO log.
    facts = page.evaluate(
        """async ({url, username}) => {
      const response = await fetch(url, {credentials: 'same-origin'});
      const data = await response.json();
      return [response.status, data.authenticated === true, data.username === username];
    }""",
        {
            "url": clone.prefix + "/v2/auth/me",
            "username": clone.accounts["admin"].username,
        },
    )
    return tuple(facts)


def logout(page: Page, gate: BrowserFence, clone: OwnedClone) -> None:
    before = count(gate, "POST", "/v2/auth/logout")
    page.get_by_role("button", name="退出登录", exact=True).click()
    page.wait_for_url(clone.child.base + clone.prefix + "/v2/auth/login")
    expect(page.get_by_role("form", name="登录表单", exact=True)).to_be_visible()
    page.wait_for_load_state("networkidle")
    assert count(gate, "POST", "/v2/auth/logout") == before + 1
    assert ("POST", "/v2/auth/logout", 204) in gate.responses
    assert cookie_facts(page) == []
    assert me_facts(page, clone) == (200, False, False)
    # Public login remains public after logout: no unauthorized redirect loop.
    page.reload(wait_until="networkidle")
    expect(page.get_by_role("heading", name="登录", exact=True)).to_be_visible()
    login_url_matches = page.url == clone.child.base + clone.prefix + "/v2/auth/login"
    assert login_url_matches


def sessions(clone: OwnedClone) -> set[tuple[int, bool]]:
    """Independent checked transaction, selecting neither token nor token hash."""
    clone.child.require_live()
    with checked_session(clone.child.binding, clone.engine, clone.factory) as session:
        rows = session.execute(
            select(UserSession.id, UserSession.revoked_at).where(
                UserSession.user_id == clone.accounts["admin"].user_pk
            )
        )
        result = {(pk, revoked is not None) for pk, revoked in rows}
    clone.child.require_live()
    return result


def assert_draft_preserved(page: Page, clone: OwnedClone) -> None:
    username_matches = (
        page.get_by_label("用户名", exact=True).input_value()
        == clone.accounts["admin"].username
    )
    password_matches = (
        page.get_by_label("密码", exact=True).input_value()
        == clone.accounts["admin"].password
    )
    assert username_matches and password_matches


def lost_login(page: Page, gate: BrowserFence, clone: OwnedClone) -> tuple[str, int]:
    url = open_login(page, clone, "/v2/pages/shops")
    credentials(page, clone)
    before = count(gate, "POST", "/v2/auth/login")
    lost_before = gate.lost
    session_before = sessions(clone)
    gate.drop_next = ("POST", "/v2/auth/login")
    repeat_enter(page)
    recovery = page.get_by_role("region", name="登录结果核对", exact=True)
    expect(recovery).to_be_visible()
    expect(page.get_by_role("button", name="登录", exact=True)).to_be_disabled()
    expect(
        recovery.get_by_role("button", name="查询当前会话", exact=True)
    ).to_be_enabled()
    page.wait_for_load_state("networkidle")
    assert gate.lost == lost_before + 1 and gate.drop_next is None
    assert ("POST", "/v2/auth/login", 200) in gate.responses
    assert count(gate, "POST", "/v2/auth/login") == before + 1
    assert count(gate, "GET", "/v2/auth/me") == 0
    login_url_matches = page.url == url
    assert login_url_matches
    assert_draft_preserved(page, clone)
    # The owned API really created a session before its 200 was lost. This is
    # separate from the GET/UI statement, which is deliberately not that proof.
    created = sessions(clone) - session_before
    assert len(created) == 1 and all(not revoked for _pk, revoked in created)
    repeat_enter(page)
    page.wait_for_load_state("networkidle")
    assert count(gate, "POST", "/v2/auth/login") == before + 1
    recovery.get_by_role("button", name="查询当前会话", exact=True).click()
    expect(
        recovery.get_by_text("查询完成：当前存在已认证会话。", exact=True)
    ).to_be_visible()
    expect(
        recovery.get_by_text(
            "当前用户名与原请求一致，但仍不能证明原登录成功。", exact=True
        )
    ).to_be_visible()
    expect(
        recovery.get_by_text(
            "查询仅反映当前 Cookie 对应的会话，不能证明原密码正确、原请求成功或本次签发了会话。输入和原请求仅保留在本页内存；不会自动重试。",
            exact=True,
        )
    ).to_be_visible()
    expect(page.get_by_role("button", name="登录", exact=True)).to_be_disabled()
    page.wait_for_load_state("networkidle")
    assert count(gate, "GET", "/v2/auth/me") == 1
    assert count(gate, "POST", "/v2/auth/login") == before + 1
    assert sessions(clone) == session_before | created
    assert_draft_preserved(page, clone)
    login_url_matches = page.url == url
    assert login_url_matches
    return url, before + 1


@pytest.mark.parametrize("width", [375, 1440, 2560])
def test_native_enter_cookie_next_logout_and_blank_login_capture(
    frontend_build,
    clone: OwnedClone,
    width: int,
) -> None:
    with live_browser(
        clone.probe,
        clone.accounts["admin"],
        shop_id="99" + str(secrets.randbelow(10**14)).zfill(14),
        service_id="TEST_auth_native_" + secrets.token_hex(8),
        enum_value="TEST_auth_native_" + secrets.token_hex(8),
        width=width,
    ) as (page, gate):
        open_login(page, clone, "/v2/pages/shops")
        assert page.get_by_label("用户名", exact=True).input_value() == ""
        password_empty = page.get_by_label("密码", exact=True).input_value() == ""
        assert password_empty
        assert page.get_by_label("密码", exact=True).get_attribute("type") == "password"
        dimensions = page.evaluate(
            "({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        assert dimensions["scroll"] <= dimensions["client"] + 1
        page.evaluate("document.fonts.ready")
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(
                SCREENSHOTS
                / f"TEST_blank_login_{'tts' if clone.prefix else 'root'}_{width}.png"
            ),
            full_page=True,
        )
        # Screenshot is strictly before any username/password is filled.
        page.get_by_label("用户名", exact=True).focus()
        page.keyboard.press("Tab")
        expect(page.get_by_label("密码", exact=True)).to_be_focused()
        credentials(page, clone)
        repeat_enter(page)
        arrived(page, clone, "/v2/pages/shops")
        assert count(gate, "POST", "/v2/auth/login") == 1
        assert cookie_facts(page) == [(clone.prefix or "/", True, "Lax")]
        http_only_hidden = page.evaluate(
            """name => !document.cookie.split(';').some(
          part => part.trim().startsWith(name + '='))""",
            SESSION_COOKIE_NAME,
        )
        assert http_only_hidden
        assert me_facts(page, clone) == (200, True, True)
        logout(page, gate, clone)
        assert count(gate, "POST", "/v2/auth/login") == 1
        assert all(csrf for method, _path, csrf in gate.calls if method == "POST")
        assert not gate.violations


def test_native_401_reauthentication_and_committed_loss_explicit_choices(
    frontend_build,
    clone: OwnedClone,
) -> None:
    # Six login POSTs here + three width cases = nine per module-scoped child;
    # keep the existing real ten/minute throttle and fixture lifecycle intact.
    with live_browser(
        clone.probe,
        clone.accounts["admin"],
        shop_id="99" + str(secrets.randbelow(10**14)).zfill(14),
        service_id="TEST_auth_recovery_" + secrets.token_hex(8),
        enum_value="TEST_auth_recovery_" + secrets.token_hex(8),
        width=1440,
    ) as (page, gate):
        url = open_login(page, clone, "/v2/pages/enum-map")
        credentials(page, clone, wrong=True)
        wrong_password = page.get_by_label("密码", exact=True).input_value()
        repeat_enter(page)
        expect(page.get_by_text("用户名或密码错误", exact=True)).to_be_visible()
        page.wait_for_load_state("networkidle")
        assert ("POST", "/v2/auth/login", 401) in gate.responses
        assert count(gate, "POST", "/v2/auth/login") == 1
        password_preserved = (
            page.get_by_label("密码", exact=True).input_value() == wrong_password
        )
        assert password_preserved
        login_url_matches = page.url == url
        assert login_url_matches
        assert cookie_facts(page) == []
        assert count(gate, "GET", "/v2/auth/me") == 0
        expect(page.get_by_role("button", name="登录", exact=True)).to_be_enabled()
        credentials(page, clone)
        repeat_enter(page)
        arrived(page, clone, "/v2/pages/enum-map")
        assert count(gate, "POST", "/v2/auth/login") == 2
        logout(page, gate, clone)
        open_login(page, clone, "/v2/pages/shops")
        credentials(page, clone)
        repeat_enter(page)
        arrived(page, clone, "/v2/pages/shops")
        assert count(gate, "POST", "/v2/auth/login") == 3
        logout(page, gate, clone)

        # Fresh page/context for each loss branch: no prior Cookie or GET state
        # is carried into the UNKNOWN protocol, but the same owned child stays.
        assert not gate.violations
    for choice in ("current", "retry"):
        with live_browser(
            clone.probe,
            clone.accounts["admin"],
            shop_id="99" + str(secrets.randbelow(10**14)).zfill(14),
            service_id="TEST_auth_loss_" + secrets.token_hex(8),
            enum_value="TEST_auth_loss_" + secrets.token_hex(8),
            width=1440,
        ) as (page, gate):
            url, submitted = lost_login(page, gate, clone)
            if choice == "current":
                page.get_by_role(
                    "button", name="使用已查询的当前会话继续", exact=True
                ).click()
            else:
                before_choice = sessions(clone)
                page.get_by_role(
                    "button", name="我已了解，允许手动重新登录", exact=True
                ).click()
                expect(
                    page.get_by_text(
                        "已允许手动登录；仍需点击登录，不会自动提交。重新查询将撤销此选择。",
                        exact=True,
                    )
                ).to_be_visible()
                page.wait_for_load_state("networkidle")
                login_url_matches = page.url == url
                assert login_url_matches
                assert count(gate, "POST", "/v2/auth/login") == submitted
                assert sessions(clone) == before_choice
                assert_draft_preserved(page, clone)
                page.get_by_role("button", name="登录", exact=True).click()
                submitted += 1
            arrived(page, clone, "/v2/pages/shops")
            assert count(gate, "POST", "/v2/auth/login") == submitted
            assert cookie_facts(page) == [(clone.prefix or "/", True, "Lax")]
            logout(page, gate, clone)
            assert count(gate, "POST", "/v2/auth/login") == submitted
            assert gate.lost == 1 and gate.drop_next is None
            assert all(csrf for method, _path, csrf in gate.calls if method == "POST")
            assert not gate.violations
