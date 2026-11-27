import { closeRowEditor, openRowEditor, readRowCost } from './test-editor';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Toast, toastQueue } from '@heroui/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PurchasePrices from './index';
import { initialFields } from './contracts';
import type { Product } from './contracts';
import type { PageProps } from '../../shared/contracts';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'manual-costs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const shop = { id: '9007199254740993', shop_id: 'TEST_shop', account_name: 'TEST 店铺', platform: 'tiktok' };
const otherShop = { ...shop, id: '9007199254740994', shop_id: 'TEST_other_shop', account_name: 'TEST 别店' };
const product = (id = '9007199254740995', name = 'TEST 商品'): Product => ({ id, shop_pk: shop.id, spu_id: 'TEST_same_external', title: name,
  status: 'ACTIVATE', unit_cost: '0.0000', currency: 'VND', source_created_at: null, source_updated_at: '2026-10-24T12:00:00+00:00', image_url: null });
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
const saved = (row: Product, value = '12.3400') => json({ id: '9223372036854775800', spu_pk: row.id, unit_cost: value,
  currency: row.currency, valid_from: '2026-10-24T12:00:00Z', valid_to: null }, 201);
function setup(command: (url: string, init: RequestInit) => Promise<Response> = async () => { throw new Error('TEST unexpected command'); }, rows = [product()]) {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'POST') return command(url, init);
    if (url.includes('channel-accounts?')) return json([shop, otherShop]);
    if (url.includes('channel-products?')) {
      const scope = new URL(url, 'https://test.invalid').searchParams.get('shop_pk');
      return json(rows.filter((row) => !scope || row.shop_pk === scope), 200, { 'X-Total-Count': '9007199254740997' });
    }
    if (url.includes('missing-cost-products?')) return json({ items: rows.map(({ id, ...row }) => ({ ...row, spu_pk: id, missing_photo: true })), total_missing_photo: '9007199254740999' });
    if (url.includes('/reporting/purchase-prices?')) return json({ items: [], total: 0 });
    throw new Error('TEST unexpected read');
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
const ManualCosts = (properties: PageProps) => <><PurchasePrices {...properties} /><Toast.Provider /></>;
const workbenchFilters = () => within(screen.getByLabelText('工作台筛选'));
const workbenchViews = () => within(screen.getByLabelText('工作台视图'));
async function chooseShop(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(workbenchFilters().getByRole('button', { name: /店铺/ }));
  await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: label }));
}
async function editCost(value = '12.3400', name = 'TEST 商品') {
  const row = (await screen.findByText(name)).closest('tr')!;
  const dialog = openRowEditor(row);
  fireEvent.change(dialog.getByRole('textbox', { name: '采购价' }), { target: { value } });
  return dialog;
}
afterEach(() => { toastQueue.destroy(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('purchase-price workbench', () => {
  it('keeps missing purchase prices blank and defaults their currency to CNY', () => {
    expect(initialFields({ ...product(), unit_cost: null, currency: null })).toMatchObject({ unit_cost: '', currency: 'CNY' });
  });
  it('defaults to all shops and can filter and return to all without changing identity', async () => {
    const other = { ...product('9007199254740996', 'TEST 跨店商品'), shop_pk: otherShop.id };
    const fetcher = setup(undefined, [product(), other]); render(<ManualCosts {...props} />); const user = userEvent.setup();
    await screen.findByText('TEST 商品'); expect(screen.getByText('TEST 跨店商品')).toBeVisible();
    const reads = () => fetcher.mock.calls.filter(([url]) => url.includes('channel-products?'));
    expect(new URL(reads()[0][0], 'https://test.invalid').searchParams.has('shop_pk')).toBe(false);
    await chooseShop(user, `${otherShop.account_name} · ${otherShop.shop_id} · #${otherShop.id}`);
    await waitFor(() => expect(screen.queryByText('TEST 商品')).not.toBeInTheDocument());
    expect(new URL(reads().at(-1)![0], 'https://test.invalid').searchParams.get('shop_pk')).toBe(otherShop.id);
    await chooseShop(user, '全部店铺'); await screen.findByText('TEST 商品');
    expect(new URL(reads().at(-1)![0], 'https://test.invalid').searchParams.has('shop_pk')).toBe(false);
  });
  it('saves the chosen product once with exact price and captured IDs, then closes the editor', async () => {
    let finish!: (response: Response) => void; const writes: RequestInit[] = [];
    setup(async (_url, init) => { writes.push(init); return new Promise((resolve) => { finish = resolve; }); },
      [product(), { ...product('9007199254740996', 'TEST 同 SPU 别店'), shop_pk: otherShop.id }]);
    render(<ManualCosts {...props} />); const dialog = await editCost('123456789012345.1234', 'TEST 同 SPU 别店');
    const save = dialog.getByRole('button', { name: '保存采购价' }); fireEvent.click(save); fireEvent.click(save);
    expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0].body as string)).toEqual({ spu_pk: '9007199254740996', spu_id: 'TEST_same_external', unit_cost: '123456789012345.1234', currency: 'VND', note: '' });
    expect(new Headers(writes[0].headers).get('X-Requested-With')).toBe('tts-erp');
    expect(await screen.findByText('处理中')).toBeVisible();
    await act(async () => finish(saved(product('9007199254740996'), '123456789012345.1234')));
    expect(await screen.findByText('处理成功')).toBeVisible(); await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.queryByRole('button', { name: '提交本页已编辑行' })).toBeNull();
  });
  it('discards unsaved input on cancel and never creates a draft or a write', async () => {
    const fetcher = setup(); render(<ManualCosts {...props} />); await editCost('88.0000'); closeRowEditor();
    const row = screen.getByText('TEST 商品').closest('tr')!; expect(readRowCost(row)).toBe('0.0000');
    expect(screen.queryByLabelText('保留草稿')).toBeNull(); expect(screen.getByLabelText('采购价 Excel 文件')).toBeEnabled();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });
  it.each([409, 422])('preserves only the open form on HTTP %s and permits one explicit save retry', async (status) => {
    let attempts = 0;
    setup(async () => ++attempts === 1 ? json({ detail: 'TEST_rejected' }, status) : saved(product()));
    render(<ManualCosts {...props} />); const dialog = await editCost();
    fireEvent.click(dialog.getByRole('button', { name: '保存采购价' }));
    expect(await screen.findByText('处理失败')).toBeVisible();
    expect(dialog.getByRole('textbox', { name: '采购价' })).toHaveValue('12.3400');
    fireEvent.click(dialog.getByRole('button', { name: '保存采购价' }));
    expect(await screen.findByText('处理成功')).toBeVisible(); expect(attempts).toBe(2);
  });
  it.each(['network', '500', 'malformed', 'wrong-identity'] as const)('never replays an unknown %s save or reports it as success', async (mode) => {
    const fetcher = setup(async () => {
      if (mode === 'network') throw new TypeError('TEST offline');
      if (mode === '500') return json({}, 500);
      return mode === 'malformed' ? json({ unit_cost: '12.3400' }, 201) : saved(product('9007199254740996'));
    });
    render(<ManualCosts {...props} />); const dialog = await editCost(); fireEvent.click(dialog.getByRole('button', { name: '保存采购价' }));
    expect(await screen.findByText(/保存结果未确认/)).toBeVisible(); expect(dialog.getByRole('button', { name: '保存采购价' })).toBeDisabled();
    closeRowEditor(); expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
    expect(screen.queryByText('处理成功')).toBeNull();
  });
  it('validates optional aware timestamps without sending or converting the amount', async () => {
    const fetcher = setup(); render(<ManualCosts {...props} />); const dialog = await editCost('0.123456');
    fireEvent.change(dialog.getByRole('textbox', { name: '生效时间' }), { target: { value: '2026-11-27T12:00:00' } });
    fireEvent.click(dialog.getByRole('button', { name: '保存采购价' }));
    expect(await screen.findByText('请填写带 Z 或明确时区偏移的 ISO 时间')).toBeVisible();
    expect(dialog.getByRole('textbox', { name: '采购价' })).toHaveValue('0.123456');
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });
  it('shows a read error and correlation id without exposing database details', async () => {
    const fetcher = setup(); fetcher.mockImplementation(async (url) => url.includes('channel-accounts?') ? json([shop])
      : json({ detail: 'TEST_private_database_error' }, 500, { 'x-request-id': 'TEST_read_request' }));
    render(<ManualCosts {...props} />); expect(await screen.findByText(/HTTP 500/)).toBeVisible();
    expect(screen.getByText(/TEST_read_request/)).toBeVisible(); expect(screen.getByRole('button', { name: '重试列表' })).toBeEnabled();
    expect(document.body.textContent).not.toContain('TEST_private_database_error');
  });
  it('keeps readonly browsing usable without enabling editing or uploads', async () => {
    setup(); render(<ManualCosts bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    await screen.findByText('TEST 商品'); expect(screen.getByRole('button', { name: '编辑详情' })).toBeDisabled();
    expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled(); expect(workbenchFilters().getByRole('button', { name: '搜索' })).toBeEnabled();
  });
});

describe('manual-costs server-total pagination', () => {
  const pageRows = (offset = 0) => Array.from({ length: 50 }, (_, index) => product(String(1000 + offset + index), `TEST page ${offset} row ${index}`));
  const historyRows = (rows: ReturnType<typeof pageRows>) => rows.map((row) => ({
    id: row.id, spu_pk: row.id, shop_pk: row.shop_pk, spu_id: row.spu_id, title: row.title,
    unit_cost: row.unit_cost, currency: row.currency, prev_unit_cost: null, prev_currency: null,
    note: null, valid_from: null, valid_to: null, created_at: null,
  }));
  function setupPages(view: 'all' | 'recent', total: string, fullPage = true) {
    const fetcher = vi.fn(async (url: string) => {
      if (url.includes('channel-accounts?')) return json([shop]);
      const offset = Number(new URL(url, 'https://test.invalid').searchParams.get('offset'));
      const rows = fullPage ? pageRows(offset) : [product('1000', 'TEST partial page')];
      if (url.includes('channel-products?')) return view === 'all' ? json(rows, 200, { 'X-Total-Count': total }) : json([]);
      if (url.includes('/reporting/purchase-prices?')) {
        // Exercise raw JSON integer decoding, not a pre-rounded JavaScript number.
        return new Response(`{"items":${JSON.stringify(historyRows(rows))},"total":${total}}`);
      }
      throw new Error('TEST unexpected page request');
    });
    vi.stubGlobal('fetch', fetcher); return fetcher;
  }
  async function openView(view: 'all' | 'recent', user: ReturnType<typeof userEvent.setup>) {
    render(<ManualCosts {...props} />);
    if (view === 'recent') {
      await screen.findByText('当前页暂无商品');
      await user.click(workbenchViews().getByRole('button', { name: '最近提交' }));
    }
  }
  it.each(['all', 'recent'] as const)('disables next on a full final %s page at zero and nonzero offsets', async (view) => {
    const fetcher = setupPages(view, '100'); const user = userEvent.setup(); await openView(view, user);
    // Resolve the live controls each time without scanning all 50 rows' actions.
    const pagingControls = () => within(screen.getByRole('navigation', { name: '采购工作台分页' }));
    await screen.findByText('TEST page 0 row 0');
    expect(pagingControls().getByRole('button', { name: '上一页' })).toBeDisabled();
    expect(pagingControls().getByRole('button', { name: '下一页' })).toBeEnabled();
    await user.click(pagingControls().getByRole('button', { name: '下一页' }));
    await screen.findByText('TEST page 50 row 0');
    expect(pagingControls().getByRole('button', { name: '下一页' })).toBeDisabled();
    const calls = fetcher.mock.calls.length;
    await user.click(pagingControls().getByRole('button', { name: '下一页' })); expect(fetcher.mock.calls).toHaveLength(calls);
    fetcher.mockImplementationOnce(async () => view === 'all' ? json(pageRows(), 200, { 'X-Total-Count': '50' }) : json({ items: historyRows(pageRows()), total: 50 }));
    await user.click(pagingControls().getByRole('button', { name: '上一页' })); await pagingControls().findByText('第 1 页 · 共 1 页');
    expect(pagingControls().getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(fetcher.mock.calls.some(([url]) => url.includes('offset=50') && url.includes(view === 'all' ? 'channel-products?' : '/reporting/purchase-prices?'))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => url.includes('offset=100'))).toBe(false);
  });
  it.each(['all', 'recent'] as const)('uses an authoritative remaining %s total even when the returned page is short', async (view) => {
    setupPages(view, '51', false); const user = userEvent.setup(); await openView(view, user);
    await screen.findByText('TEST partial page');
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled();
  });
  it.each(['all', 'recent'] as const)('preserves an oversized %s total exactly and leaves remaining pages available', async (view) => {
    setupPages(view, '9007199254740997', false); const user = userEvent.setup(); await openView(view, user);
    await screen.findByText('TEST partial page');
    expect(screen.getByText(/共 180143985094820 页/)).toBeVisible();
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled();
  });
  it('falls back to original row count when the catalogue total is unknown', async () => {
    const fetcher = setupPages('all', '50');
    fetcher.mockImplementation(async (url: string) => url.includes('channel-accounts?') ? json([shop])
      : json(new URL(url, 'https://test.invalid').searchParams.get('offset') === '0' ? pageRows() : [product('1050', 'TEST last unknown')]));
    const user = userEvent.setup(); await openView('all', user); await screen.findByText('TEST page 0 row 0');
    const next = () => within(screen.getByRole('navigation', { name: '采购工作台分页' })).getByRole('button', { name: '下一页' });
    expect(screen.getByText('第 1 页')).toBeVisible();
    expect(next()).toBeEnabled();
    await user.click(next()); await screen.findByText('TEST last unknown');
    expect(next()).toBeDisabled();
  });
  it('keeps missing-candidate paging on raw rows, not the photo total or current-page display filter', async () => {
    const fetcher = setupPages('all', '50'); const user = userEvent.setup(); await openView('all', user); await screen.findByText('TEST page 0 row 0');
    fetcher.mockImplementation(async (url: string) => {
      const offset = Number(new URL(url, 'https://test.invalid').searchParams.get('offset'));
      return json({ items: (offset === 0 ? pageRows() : pageRows(offset).slice(0, 1)).map(({ id, ...row }) => ({ ...row, spu_pk: id, missing_photo: false })), total_missing_photo: '9007199254740997' });
    });
    const next = () => within(screen.getByRole('navigation', { name: '采购工作台分页' })).getByRole('button', { name: '下一页' });
    await user.click(workbenchViews().getByRole('button', { name: '缺采购价候选' })); await screen.findByText(/缺图总数 9007199254740997/);
    await user.click(within(screen.getByRole('form', { name: '工作台筛选' })).getByRole('checkbox', { name: /仅看本页缺图/ })); await screen.findByText('当前页暂无商品');
    expect(next()).toBeEnabled();
    await user.click(next());
    await screen.findByText(/缺图总数 9007199254740997/);
    expect(next()).toBeDisabled();
    expect(screen.queryByText(/共 \d+ 页/)).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => url.includes('missing-cost-products?') && url.includes('offset=50') && !url.includes('q='))).toBe(true);
  });
});
