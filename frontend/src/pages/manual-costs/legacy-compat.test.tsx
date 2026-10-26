import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import type { Product } from './contracts';
import ManualCosts from './index';

// Active React counterparts to the retained template contracts in
// tests/api/test_manual_costs_page_v2.py. No legacy DOM hooks are required here.
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'manual-costs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const shop = { id: '9007199254740993', shop_id: 'TEST_shop', account_name: 'TEST 店铺' };
const product = (index = 0): Product => ({
  id: String(9007199254740995n + BigInt(index)), shop_pk: shop.id, spu_id: `TEST_SPU_${index}`, title: `TEST 商品 ${index}`, status: 'ACTIVATE',
  unit_cost: '0.0000', currency: 'VND', source_unit_cost: '123456789012345.1234',
  source_created_at: '2026-10-23T12:00:00+07:00', source_updated_at: '2026-10-24T12:00:00+00:00', image_url: null,
});
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function setup(rows: Product[] | ((query: URLSearchParams) => Product[]) = [product()]) {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    const query = new URL(url, 'https://test.invalid').searchParams;
    if (init.method !== 'GET') throw new Error('TEST unexpected write');
    if (url.includes('channel-accounts?')) return json([shop]);
    if (url.includes('channel-products?')) return json(typeof rows === 'function' ? rows(query) : rows, 200, { 'X-Total-Count': '9007199254740997' });
    if (url.includes('missing-cost-products?')) return json({ items: [{ ...product(), spu_pk: product().id, missing_photo: true }], total_missing_photo: '9007199254740999' });
    if (url.includes('/reporting/manual-costs?')) return json({ items: [{ id: '9223372036854775800', spu_pk: product().id, shop_pk: shop.id, spu_id: product().spu_id, title: 'TEST 变更记录', prev_unit_cost: '123456789012345.1234', prev_currency: 'CNY', unit_cost: '0.0000', currency: 'VND', note: null, valid_from: null, valid_to: null, created_at: null }], total: '9007199254740998' });
    throw new Error('TEST unexpected read');
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
function catalogueQuery(fetcher: ReturnType<typeof setup>) {
  const calls = fetcher.mock.calls.filter(([url]) => url.includes('channel-products?'));
  return new URL(calls.at(-1)![0], 'https://test.invalid').searchParams;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('manual-costs active replacements for retained legacy contracts', () => {
  it('keeps catalogue and history plus the accepted candidate view, never the retired pending workflow', async () => {
    const fetcher = setup(); const user = userEvent.setup(); render(<ManualCosts {...props} />);
    await screen.findByText('TEST 商品 0');
    const views = within(screen.getByLabelText('工作台视图'));
    expect(views.getAllByRole('button').map((button) => button.textContent)).toEqual(['全部 SPU', '缺成本候选', '最近提交']);
    expect(views.getByRole('button', { name: '全部 SPU' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/服务端总数：9007199254740997/)).toBeVisible();
    await user.click(views.getByRole('button', { name: '最近提交' }));
    const history = (await screen.findByText('TEST 变更记录')).closest('tr')!;
    expect(within(history).getByText('123456789012345.1234 CNY')).toBeVisible();
    expect(within(history).getByText('0.0000 VND')).toBeVisible();
    expect(screen.getByText(/服务端总数：9007199254740998/)).toBeVisible();
    await user.click(views.getByRole('button', { name: '缺成本候选' }));
    expect(await screen.findByText(/服务端缺图总数：9007199254740999/)).toBeVisible();
    expect(screen.getByText('missing_photo：true')).toBeVisible();
    expect(screen.queryByRole('textbox', { name: '按 SPU 或标题搜索' })).toBeNull();
    for (const retired of ['待处理', '待填成本', '待传图片']) expect(document.body.textContent).not.toContain(retired);
    expect(fetcher.mock.calls.every(([url, init]) => init.method === 'GET' && !url.includes('pending'))).toBe(true);
  });

  it('switches the actual shop_pk query while keeping the readonly catalogue usable', async () => {
    const other = { ...shop, id: '9007199254740994', shop_id: 'TEST_other', account_name: 'TEST 别店' };
    const fetcher = setup((query) => [{ ...product(), shop_pk: query.get('shop_pk')!, title: `TEST 店铺 ${query.get('shop_pk')}` }]);
    fetcher.mockImplementationOnce(async () => json([shop, other]));
    const user = userEvent.setup(); render(<ManualCosts bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    await screen.findByText(`TEST 店铺 ${shop.id}`);
    await user.selectOptions(screen.getByRole('combobox', { name: '当前店铺' }), other.id);
    const row = (await screen.findByText(`TEST 店铺 ${other.id}`)).closest('tr')!;
    expect(catalogueQuery(fetcher).get('shop_pk')).toBe(other.id);
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '刷新当前列表' })).toBeEnabled();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });

  it.each([
    ['status', 'DELETED', '状态'],
    ['status', 'SELLER_DEACTIVATED', '状态'],
    ['status', 'ACTIVATE', '状态'],
    ['has_orders', 'true', '仅看有单'],
    ['missing_manual_cost', 'true', '仅展示未登记 SPU'],
    ['sort', 'unit_cost', '排序字段'],
    ['sort', 'created_at', '排序字段'],
    ['sort', 'updated_at', '排序字段'],
    ['order', 'desc', '排序方向'],
    ['limit', '50', '每页行数'],
    ['q', 'TEST 搜索 & SPU', '按 SPU 或标题搜索'],
  ])('sends %s=%s to the server before pagination and resets a nonzero offset', async (key, value, label) => {
    const fetcher = setup((query) => Array.from({ length: Number(query.get('limit')) }, (_, index) => ({ ...product(index), title: `TEST server ${query.get('offset')} row ${index}` })));
    const user = userEvent.setup(); render(<ManualCosts {...props} />);
    await screen.findByText('TEST server 0 row 0');
    expect(catalogueQuery(fetcher).get('sort')).toBe('status');
    expect(catalogueQuery(fetcher).get('order')).toBe('asc');
    await user.selectOptions(screen.getByRole('combobox', { name: '每页行数' }), '25');
    await waitFor(() => expect(catalogueQuery(fetcher).get('limit')).toBe('25'));
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('TEST server 25 row 0');
    expect(catalogueQuery(fetcher).get('offset')).toBe('25');
    if (key === 'q') {
      fireEvent.change(screen.getByRole('textbox', { name: label }), { target: { value } });
      expect(catalogueQuery(fetcher).has('q')).toBe(false); // Explicit search, not a local row filter.
      await user.click(screen.getByRole('button', { name: '搜索' }));
    } else if (key === 'has_orders' || key === 'missing_manual_cost') {
      await user.click(screen.getByRole('checkbox', { name: label }));
    } else {
      await user.selectOptions(screen.getByRole('combobox', { name: label }), value);
    }
    await screen.findByText('TEST server 0 row 0');
    const query = catalogueQuery(fetcher);
    expect(query.get(key)).toBe(value); expect(query.get('offset')).toBe('0');
    expect(query.get('shop_pk')).toBe(shop.id);
    // The server deliberately returns ACTIVATE rows even for DELETED/search scopes.
    // Rendering them unchanged protects server authority, not client-side re-filtering.
    expect(within(screen.getByRole('region', { name: 'SPU 表格滚动区域' })).getAllByText('在售')).toHaveLength(Number(query.get('limit')));
    expect(screen.getByText(/服务端总数：9007199254740997/)).toBeVisible();
    if (key === 'status' || key === 'has_orders' || key === 'missing_manual_cost') {
      if (key === 'status') await user.selectOptions(screen.getByRole('combobox', { name: label }), '');
      else await user.click(screen.getByRole('checkbox', { name: label }));
      await waitFor(() => expect(catalogueQuery(fetcher).has(key)).toBe(false));
    }
    expect(fetcher.mock.calls.every(([url, init]) => url.startsWith('/tts/v2/') && init.method === 'GET')).toBe(true);
  });

  it('labels SPU identities and preserves exact server money, nulls, status labels and timestamps in editable catalogue rows', async () => {
    const rows = [
      { ...product(), unit_cost: '999999999999999.9999' },
      { ...product(1), unit_cost: '-0.0100', status: 'DELETED' },
      { ...product(2), unit_cost: null, source_unit_cost: null, currency: null, status: 'SELLER_DEACTIVATED' },
      { ...product(3), status: 'TEST_UPSTREAM_STATUS' },
    ];
    setup(rows); render(<ManualCosts {...props} />); await screen.findByText('TEST 商品 0');
    expect(screen.getByRole('textbox', { name: '按 SPU 或标题搜索' })).toBeVisible();
    expect(screen.getByRole('columnheader', { name: '内部 PK / SPU' })).toBeVisible();
    expect(document.body.textContent).not.toContain('SKU');
    for (const [index, value] of ['999999999999999.9999', '-0.0100', '', '0.0000'].entries()) {
      const row = screen.getByText(`TEST 商品 ${index}`).closest('tr')!;
      expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue(value);
      expect(within(row).getByText(rows[index].id)).toBeVisible();
      expect(within(row).getByText(rows[index].spu_id)).toBeVisible();
      expect(within(row).getByText(rows[index].source_created_at!)).toBeVisible();
      expect(within(row).getByText(rows[index].source_updated_at!)).toBeVisible();
    }
    const catalogue = within(screen.getByRole('region', { name: 'SPU 表格滚动区域' }));
    for (const label of ['在售', '下架', '停售', 'TEST_UPSTREAM_STATUS']) expect(catalogue.getByText(label)).toBeVisible();
    expect(screen.getByText('人工：— —')).toBeVisible();
    expect(screen.getByText('人工：999999999999999.9999 VND')).toBeVisible();
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });

  it.each(['unregistered', 'candidate'])('never prefills a source price or auto-submits an untouched %s row', async (view) => {
    const row = { ...product(), unit_cost: null };
    const fetcher = setup([row]); const user = userEvent.setup(); render(<ManualCosts {...props} />);
    await screen.findByText('TEST 商品 0');
    expect(screen.getByRole('textbox', { name: '单位成本' })).toHaveValue('123456789012345.1234');
    if (view === 'unregistered') await user.click(screen.getByRole('checkbox', { name: '仅展示未登记 SPU' }));
    else await user.click(screen.getByRole('button', { name: '缺成本候选' }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: '单位成本' })).toHaveValue(''));
    expect(screen.getByText('货源：123456789012345.1234')).toBeVisible();
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });

  it('replaces submit-all hooks with explicit dirty-row confirmation and confirmed per-row progress', async () => {
    const fetcher = setup([product(), product(1)]); const user = userEvent.setup(); render(<ManualCosts {...props} />);
    const row = (await screen.findByText('TEST 商品 0')).closest('tr')!;
    fireEvent.change(within(row).getByRole('textbox', { name: '单位成本' }), { target: { value: '77.0100' } });
    expect(within(row).getByText('已编辑')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/SPU TEST_SPU_0 · 77.0100 VND/)).toBeVisible();
    expect(within(dialog).queryByText(/TEST_SPU_1/)).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    fetcher.mockImplementationOnce(async (url, init) => {
      expect(url).toBe('/tts/v2/reporting/manual-costs'); expect(init.method).toBe('POST');
      expect(JSON.parse(init.body as string)).toMatchObject({ spu_pk: product().id, spu_id: 'TEST_SPU_0', unit_cost: '77.0100', currency: 'VND' });
      return json({ id: '9223372036854775800', spu_pk: product().id, unit_cost: '77.0100', currency: 'VND', valid_from: '2026-10-24T12:00:00Z', valid_to: null }, 201);
    });
    await user.click(within(dialog).getByRole('button', { name: '确认逐行提交' }));
    expect(await within(row).findByText('已确认成功')).toBeVisible();
    expect(screen.getByText(/本次已确认 1 行 · 未完成 0 行 · 未发送 0 行/)).toBeVisible();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });

  it('opens the image by keyboard, closes with Escape or the close control, and restores focus in readonly mode', async () => {
    const imageUrl = '/tts/static/TEST_mirrored.webp';
    const fetcher = setup([{ ...product(), image_url: imageUrl }]); const user = userEvent.setup();
    render(<ManualCosts bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    const thumbnail = await screen.findByRole('button', { name: `放大图片 PK ${product().id}` });
    expect(screen.getByRole('img', { name: 'TEST 商品 0 主图' })).toHaveAttribute('src', imageUrl);
    thumbnail.focus(); await user.keyboard('{Enter}');
    expect(within(await screen.findByRole('dialog')).getByRole('img', { name: '商品主图放大' })).toHaveAttribute('src', imageUrl);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(thumbnail).toHaveFocus());
    await user.click(thumbnail);
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '关闭图片' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(thumbnail).toHaveFocus());
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });

  it('shows a broken-image fallback without making a missing mirror into a write or pending workflow', async () => {
    const fetcher = setup([{ ...product(), image_url: '/tts/static/TEST_broken.webp' }, product(1)]);
    render(<ManualCosts {...props} />);
    const image = await screen.findByRole('img', { name: 'TEST 商品 0 主图' });
    fireEvent.error(image);
    expect(screen.queryByRole('button', { name: `放大图片 PK ${product().id}` })).toBeNull();
    expect(screen.getAllByText('无可用镜像图')).toHaveLength(2);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });
});
