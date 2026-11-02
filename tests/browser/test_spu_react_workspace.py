"""Compiled React SPU workspaces: readable layout and explicit inert mutation paths.

All HTTP facts are canned. Expected PATCHes are fulfilled in the browser before
Renderer acknowledges them; every other write remains a read-only gate failure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from render_support import overflow_report

from tests.browser import conftest as browser_conftest
from tests.browser.test_spu_price_columns import _nested_payload

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]


def _wait_rows(page):
    page.locator('.tabulator-row').first.wait_for(state='visible')
    page.evaluate('document.fonts.ready')


@pytest.mark.parametrize('profile', ['spu-roi', 'focused-spus'])
@pytest.mark.parametrize('width', [375, 1440, 2560])
def test_spu_workspaces_keep_controls_and_a_full_metric_visible(
    frontend_browser_renderer, monkeypatch, profile, width,
):
    original = browser_conftest._mock_payload

    def payload(path, query=''):
        if path.endswith('/v2/analytics/spu-roi'):
            return _nested_payload(original)
        return original(path, query)

    monkeypatch.setattr(browser_conftest, '_mock_payload', payload)
    page = frontend_browser_renderer.open(f'/v2/pages/{profile}?shop_pk=7', width=width, height=900)
    _wait_rows(page)
    assert page.locator('.tabulator-row').count() >= 3
    overview = page.get_by_role('region', name='经营概览').bounding_box()
    sales = page.get_by_label('销售', exact=True).bounding_box()
    costs = page.get_by_label('成本', exact=True).bounding_box()
    profit = page.get_by_label('利润', exact=True).bounding_box()
    assert overview and sales and costs and profit
    assert profit['y'] >= max(sales['y'] + sales['height'], costs['y'] + costs['height'])
    if width >= 1440:
        assert abs(sales['y'] - costs['y']) <= 2
        assert costs['x'] >= sales['x'] + sales['width']
    else:
        assert costs['y'] >= sales['y'] + sales['height']
    filters = page.get_by_role('region', name='店铺与经营范围').bounding_box()
    assert filters
    assert filters['y'] >= overview['y'] + overview['height']
    if width == 2560:
        assert filters['height'] <= 160, filters
        controls = [page.locator(selector).bounding_box() for selector in (
            '#shop-switcher', '#filter-w-start', '#filter-w-end',
            '#filter-projection-lookback-days', '#btn-toolbar-more',
        )]
        assert all(controls)
        bottoms = [box['y'] + box['height'] for box in controls]
        assert max(bottoms) - min(bottoms) <= 2, controls
    elif width == 1440:
        assert filters['height'] <= 230, filters
    report = overflow_report(page)
    assert report['scrollWidth'] <= report['clientWidth'] + 1, report
    assert not report['offenders'], report
    observed = page.locator('.tabulator-tableholder').evaluate('''holder => {
        const frozen = holder.querySelector('.tabulator-cell.tabulator-frozen');
        const bounds = holder.getBoundingClientRect();
        return {width: bounds.width, frozen: frozen.getBoundingClientRect().width,
                available: bounds.width - frozen.getBoundingClientRect().width,
                scrollable: holder.scrollWidth > holder.clientWidth};
    }''')
    assert observed['available'] >= 135, observed
    # Without price columns the focused table can fit on an ultrawide viewport.
    if profile == 'spu-roi' or width < 2560:
        assert observed['scrollable'], observed
    grid = page.locator('#rows').bounding_box()
    for control in [page.locator('#shop-switcher'), page.locator('#filter-w-start'),
                    page.locator('#filter-w-end'), page.get_by_label('按 SPU ID / 标题搜索'),
                    page.get_by_role('group', name='快捷日期范围')]:
        bounds = control.bounding_box()
        assert bounds and grid and bounds['y'] + bounds['height'] <= grid['y'], bounds
        assert bounds['y'] >= overview['y'] + overview['height'], bounds
    if profile == 'spu-roi':
        bounds = page.locator('#btn-spu-apply').bounding_box()
        assert bounds and bounds['y'] < grid['y']
    screenshot_dir = Path(__file__).resolve().parents[2] / 'frontend/.cache/screenshots'
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot_dir / f'TEST_spu_react_{profile}_{width}.png'), full_page=True)
    trigger = page.get_by_role('button', name='查看商品盈利详情 TEST_SPU_000', exact=True)
    trigger.click()
    close = page.get_by_role('button', name='收起商品详情', exact=True)
    close.scroll_into_view_if_needed()
    expect(close).to_be_visible()
    close.click()
    page.locator('.op-drill-row').wait_for(state='detached')
    expect(trigger).to_be_focused()
    trigger.press('Enter')
    expect(page.get_by_role('button', name='收起商品详情')).to_be_visible()


def _focused_payload(original, selected, path, query=''):
    if '/v2/reporting/focused-spus/' in path:
        params = parse_qs(query)
        offset = int(params.get('offset', ['0'])[0])
        limit = int(params.get('limit', ['500'])[0])
        items = [{'spuPk': 1000 + i, 'spuId': identity, 'title': f'TEST 商品 {identity}',
                  'status': 'ACTIVATE'} for i, identity in enumerate(selected)]
        return {'shopPk': 7, 'items': items[offset:offset + limit], 'total': len(items),
                'matchedTotal': len(items), 'limit': limit, 'offset': offset}
    if path.endswith('/v2/commerce/channel-product-options'):
        params = parse_qs(query)
        identities = params.get('spu_ids', ['TEST_FOCUS_C'])[0].split(',')
        return [{'spu_id': identity, 'title': f'TEST 商品 {identity}', 'status': 'ACTIVATE'}
                for identity in identities if identity != 'TEST_MISSING']
    return original(path, query)


def _enable_canned_writer(page):
    def document(route):
        response = route.fetch()
        body = response.text()

        def bootstrap(match):
            value = json.loads(match.group(2))
            value['roleCapabilities'] = {'readwrite': True, 'admin': False}
            return match.group(1) + json.dumps(value, ensure_ascii=False) + match.group(3)

        body, count = re.subn(r'(<script id="frontend-bootstrap"[^>]*>)(.*?)(</script>)',
                             bootstrap, body, flags=re.S)
        assert count == 1
        route.fulfill(response=response, body=body)

    page.route('**/v2/pages/focused-spus*', document)
    page.reload(wait_until='networkidle')
    page.locator('#spu-selection-count').filter(has_text='2').wait_for()


def test_focused_list_reads_all_pages_beyond_a_thousand(frontend_browser_renderer, monkeypatch):
    original = browser_conftest._mock_payload
    selected = [f'TEST_FOCUS_{index:04d}' for index in range(1003)]
    offsets = []

    def payload(path, query=''):
        if '/v2/reporting/focused-spus/' in path:
            offsets.append(int(parse_qs(query).get('offset', ['0'])[0]))
        return _focused_payload(original, selected, path, query)

    monkeypatch.setattr(browser_conftest, '_mock_payload', payload)
    page = frontend_browser_renderer.open('/v2/pages/focused-spus?shop_pk=7')
    expect(page.locator('#spu-selection-count')).to_have_text('已关注 1003 个')
    assert offsets == [0, 500, 1000]
    field = page.get_by_role('group', name='SPU 搜索与已选标签', exact=True)
    expect(field.get_by_role('button', name='移除 SPU ', exact=False)).to_have_count(1003)
    expect(field.get_by_label('按 SPU ID / 标题搜索')).to_be_visible()
    expect(page.get_by_role('button', name='移除 SPU TEST_FOCUS_1002', exact=True)).to_be_disabled()
    assert not frontend_browser_renderer.write_requests


def test_focused_explicit_add_and_remove_never_replace_the_collection(
    frontend_browser_renderer, monkeypatch,
):
    original = browser_conftest._mock_payload
    selected = ['TEST_FOCUS_A', 'TEST_FOCUS_B']
    commands = []
    monkeypatch.setattr(browser_conftest, '_mock_payload',
                        lambda path, query='': _focused_payload(original, selected, path, query))
    page = frontend_browser_renderer.open('/v2/pages/focused-spus?shop_pk=7')
    _enable_canned_writer(page)

    def patch(route):
        assert route.request.headers.get('x-requested-with') == 'tts-erp'
        command = route.request.post_data_json
        commands.append(command)
        for identity in command['removeSpuIds']:
            selected.remove(identity)
        for identity in command['addSpuIds']:
            if identity not in selected:
                selected.append(identity)
        frontend_browser_renderer.fulfill_focused_mutation(route, {
            'shopPk': 7, 'total': len(selected), 'addedSpuIds': command['addSpuIds'],
            'removedSpuIds': command['removeSpuIds'],
        })

    page.route('**/v2/reporting/focused-spus/7', patch)
    page.get_by_label('按 SPU ID / 标题搜索').fill('TEST_FOCUS_C')
    checkbox = page.locator('#focused-spu-select input[type="checkbox"]')
    expect(checkbox).to_have_count(3)
    target = page.locator('#focused-spu-select label').filter(has_text='TEST_FOCUS_C').get_by_role('checkbox')
    target.click()  # Controlled membership changes only after the PATCH receipt.
    expect(target).to_be_checked()
    expect(page.locator('#spu-selection-count')).to_have_text('已关注 3 个')
    assert commands == [{'addSpuIds': ['TEST_FOCUS_C'], 'removeSpuIds': []}]
    assert selected == ['TEST_FOCUS_A', 'TEST_FOCUS_B', 'TEST_FOCUS_C']
    page.get_by_role('button', name='移除 SPU TEST_FOCUS_B', exact=True).click()
    expect(page.locator('#spu-selection-count')).to_have_text('已关注 2 个')
    assert commands[-1] == {'addSpuIds': [], 'removeSpuIds': ['TEST_FOCUS_B']}
    assert selected == ['TEST_FOCUS_A', 'TEST_FOCUS_C']


def test_focused_failed_patch_requires_readback_and_never_replays(frontend_browser_renderer, monkeypatch):
    original = browser_conftest._mock_payload
    selected = ['TEST_FOCUS_A', 'TEST_FOCUS_B']
    commands = []
    monkeypatch.setattr(browser_conftest, '_mock_payload',
                        lambda path, query='': _focused_payload(original, selected, path, query))
    page = frontend_browser_renderer.open('/v2/pages/focused-spus?shop_pk=7')
    _enable_canned_writer(page)

    def failed_patch(route):
        commands.append(route.request.post_data_json)
        frontend_browser_renderer.fulfill_focused_mutation(
            route, {'detail': 'TEST_uncertain_save'}, status=503)

    page.route('**/v2/reporting/focused-spus/7', failed_patch)
    page.get_by_role('button', name='移除 SPU TEST_FOCUS_A', exact=True).click()
    retry = page.get_by_role('button', name='重新读取关注事实', exact=True)
    expect(retry).to_be_visible()
    expect(page.get_by_role('button', name='移除 SPU TEST_FOCUS_B', exact=True)).to_be_disabled()
    assert len(commands) == 1
    retry.click()
    expect(page.get_by_role('button', name='移除 SPU TEST_FOCUS_A', exact=True)).to_be_enabled()
    expect(page.locator('#spu-selection-count')).to_have_text('已关注 2 个')
    assert len(commands) == 1
    assert selected == ['TEST_FOCUS_A', 'TEST_FOCUS_B']


def test_standard_batch_resolves_a_hundred_in_bounded_chunks(frontend_browser_renderer, monkeypatch):
    original = browser_conftest._mock_payload
    option_sizes = []
    captured = []

    def payload(path, query=''):
        if path.endswith('/v2/commerce/channel-product-options'):
            ids = parse_qs(query).get('spu_ids', [''])[0].split(',')
            option_sizes.append(len(ids))
            return [{'spu_id': identity, 'title': identity, 'status': 'ACTIVATE'} for identity in ids]
        if path.endswith('/v2/analytics/spu-roi'):
            captured.append(parse_qs(query))
        return original(path, query)

    monkeypatch.setattr(browser_conftest, '_mock_payload', payload)
    page = frontend_browser_renderer.open('/v2/pages/spu-roi?shop_pk=7')
    _wait_rows(page)
    identities = [f'TEST_BATCH_{index:03d}' for index in range(100)]
    page.get_by_role('button', name='批量粘贴', exact=True).click()
    page.get_by_label('粘贴 SPU ID（逗号、空格或换行分隔）').fill('\n'.join(identities))
    page.get_by_role('button', name='解析并添加', exact=True).click()
    expect(page.locator('#spu-selection-count')).to_have_text('已选择 100 个')
    assert option_sizes == [25, 25, 25, 25]
    assert not any('spu_ids' in query for query in captured)
    with page.expect_response(lambda response: '/v2/analytics/spu-roi?' in response.url and 'spu_ids=' in response.url):
        page.get_by_role('button', name='查询', exact=True).click()
    page.wait_for_function("new URL(location.href).searchParams.has('spu_ids')")
    expect(page.locator('#btn-spu-apply')).to_be_enabled()
    assert captured[-1]['spu_ids'] == [','.join(identities)]


def test_overview_retry_after_blocks_retry_without_automatic_replay(frontend_browser_renderer):
    page = frontend_browser_renderer.open('/v2/pages/spu-roi?shop_pk=7')
    _wait_rows(page)
    page.clock.install()
    page.clock.pause_at(page.evaluate('new Date(Date.now() + 1000).toISOString()'))
    requests = []

    def limited(route):
        requests.append(route.request.url)
        route.fulfill(status=429, content_type='application/json',
                      body=json.dumps({'detail': 'TEST_rate_limited'}), headers={'Retry-After': '120'})

    page.route('**/v2/analytics/spu-roi?*', limited)
    page.get_by_role('button', name='刷新当前数据', exact=True).click()
    retry = page.locator('#retry-link')
    expect(retry).to_be_disabled()
    assert len(requests) == 1
    page.clock.fast_forward(119_000)
    expect(retry).to_be_disabled()
    assert len(requests) == 1
    page.clock.fast_forward(1001)
    expect(retry).to_be_enabled()
    assert len(requests) == 1
    retry.click()
    expect(retry).to_be_disabled()
    assert len(requests) == 2
    page.clock.resume()
