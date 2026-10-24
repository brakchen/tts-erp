import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ManualCosts from './index';
import type { PageProps } from '../../shared/contracts';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'manual-costs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const shop = { id: '9007199254740993', shop_id: 'TEST_shop', account_name: 'TEST 店铺', platform: 'tiktok' };
const product = (id = '9007199254740995', name = 'TEST 商品') => ({ id, shop_pk: shop.id, spu_id: 'TEST_same_external', title: name, status: 'ACTIVATE', unit_cost: '0.0000', currency: 'VND', source_unit_cost: '123456789012345.1234', source_created_at: null, source_updated_at: '2026-10-24T12:00:00+00:00', image_url: null });
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function setup(command: (url: string, init: RequestInit) => Promise<Response> = async () => { throw new Error('TEST unexpected command'); }, rows = [product()]) {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'POST') return command(url, init);
    if (url.includes('channel-accounts?')) return json([shop]);
    if (url.includes('channel-products?')) return json(rows, 200, { 'X-Total-Count': '9007199254740997' });
    if (url.includes('channel-products/')) return json(rows[0]);
    if (url.includes('missing-cost-products?')) return json({ items: rows.map(({ id, ...row }) => ({ ...row, spu_pk: id, missing_photo: true })), total_missing_photo: '9007199254740999' });
    if (url.includes('/reporting/manual-costs?')) return json({ items: [], total: 0 });
    throw new Error('TEST unexpected read');
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
async function editCost(value = '123456789012345.1234', name = 'TEST 商品') {
  const row = (await screen.findByText(name)).closest('tr')!;
  fireEvent.change(within(row).getByRole('textbox', { name: '单位成本' }), { target: { value } });
  return row;
}
async function confirmBatch(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }));
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('manual-costs workbench', () => {
  it('reads the actual shop-scoped catalogue and preserves zero, money strings and oversized counts', async () => {
    const fetcher = setup(); render(<ManualCosts {...props} />);
    const row = (await screen.findByText('TEST 商品')).closest('tr')!;
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue('0.0000');
    expect(screen.getByText(/服务端总数：9007199254740997/)).toBeVisible();
    expect(screen.getByText('9007199254740995')).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url.includes('shop_pk=9007199254740993'))).toBe(true);
  });
  it('confirms only dirty rows once, retaining exact decimal strings and the captured internal/external identity pair', async () => {
    let release!: (r: Response) => void; const writes: RequestInit[] = [];
    setup(async (_url, init) => { writes.push(init); return new Promise((resolve) => { release = resolve; }); }, [product(), product('9007199254740996', 'TEST 未编辑')]);
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost();
    await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
    const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' });
    await user.click(confirm); fireEvent.click(confirm); expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0].body as string)).toEqual({ spu_pk: '9007199254740995', spu_id: 'TEST_same_external', unit_cost: '123456789012345.1234', currency: 'VND', note: '' });
    expect(new Headers(writes[0].headers).get('X-Requested-With')).toBe('tts-erp');
    await act(async () => release(json({ id: '9223372036854775800', spu_pk: '9007199254740995', unit_cost: '123456789012345.1234', currency: 'VND', valid_from: '2026-10-24T12:00:00+00:00', valid_to: null }, 201)));
    expect(await screen.findByText('已确认成功')).toBeVisible();
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });
});

const saved = (id = '9007199254740995', cost = '123456789012345.1234') => json({ id: '9223372036854775800', spu_pk: id, unit_cost: cost, currency: 'VND', valid_from: '2026-10-24T12:00:00+00:00', valid_to: null }, 201);
describe('manual-costs draft and batch recovery', () => {
  it('undoes a later edit back to the last confirmed submission rather than a stale pre-submit catalogue value', async () => {
    setup(async () => saved()); const user = userEvent.setup(); render(<ManualCosts {...props} />);
    await editCost(); await confirmBatch(user); await screen.findByText('已确认成功');
    const row = await editCost('999.0000'); await user.click(within(row).getByRole('button', { name: '撤销' }));
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue('123456789012345.1234');
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });
  it('submits dirty same-external-ID rows sequentially by distinct captured internal PKs, never untouched prefills', async () => {
    const writes: RequestInit[] = []; const releases: ((value: Response) => void)[] = [];
    setup(async (_url, init) => { writes.push(init); return new Promise((resolve) => releases.push(resolve)); }, [product(), product('9007199254740996', 'TEST 第二'), product('9007199254740997', 'TEST 未改')]);
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost(); await editCost('77.1000', 'TEST 第二');
    await confirmBatch(user); expect(writes).toHaveLength(1);
    await act(async () => releases[0](saved())); expect(writes).toHaveLength(2);
    expect(JSON.parse(writes[1].body as string)).toMatchObject({ spu_pk: '9007199254740996', spu_id: 'TEST_same_external', unit_cost: '77.1000' });
    await act(async () => releases[1](saved('9007199254740996', '77.1000')));
    expect(screen.getAllByText('已确认成功')).toHaveLength(2); expect(writes).toHaveLength(2);
  });
  it.each(['network', '500', 'malformed', 'wrong-identity'])('stops a non-atomic batch on %s, preserves unsent targets, and needs a separate GET and retry decision', async (mode) => {
    const writes: RequestInit[] = [];
    const fetcher = setup(async (_url, init) => {
      writes.push(init); if (writes.length === 1) return saved();
      if (mode === 'network') throw new TypeError('TEST_PRIVATE');
      if (mode === 'malformed') return json(null, 201);
      if (mode === 'wrong-identity') return saved('42');
      return json({ detail: 'TEST_PRIVATE' }, 500);
    }, [product(), product('9007199254740996', 'TEST 第二'), product('9007199254740997', 'TEST 第三')]);
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost(); await editCost('22.0000', 'TEST 第二'); const unsent = await editCost('33.0000', 'TEST 第三');
    await confirmBatch(user); await screen.findByRole('region', { name: '提交结果核对' });
    const recovery = screen.getByRole('region', { name: '提交结果核对' });
    expect(writes).toHaveLength(2); expect(within(unsent).getByRole('textbox', { name: '单位成本' })).toHaveValue('33.0000');
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled(); expect(document.body.textContent).not.toContain('TEST_PRIVATE');
    const second = screen.getByText('TEST 第二').closest('tr')!; fireEvent.change(within(second).getByRole('textbox', { name: '单位成本' }), { target: { value: '44.0000' } });
    expect(within(recovery).getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    // Return exact captured target, not the now-selected list scope.
    fetcher.mockImplementationOnce(async (url: string, init: RequestInit) => { expect(url).toBe('/tts/v2/commerce/channel-products/9007199254740996'); expect(init.method).toBe('GET'); return json(product('9007199254740996', 'TEST 第二')); });
    await user.click(within(recovery).getByRole('button', { name: '查询当前事实' })); await screen.findByText(/GET 不能证明原提交是否生效/);
    expect(writes).toHaveLength(2); expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '核对后决定重新提交' }));
    expect(within(screen.getByRole('dialog')).getByText(/44.0000 VND/)).toBeVisible();
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' })); expect(writes).toHaveLength(2);
    expect(within(second).getByRole('textbox', { name: '单位成本' })).toHaveValue('44.0000');
  });
  it.each([409, 422])('keeps actionable per-row HTTP %s errors, clears only field snapshots on genuine edit and permits explicit retry', async (status) => {
    let writes = 0; setup(async () => ++writes === 1 ? json({ detail: [{ loc: ['body', 'unit_cost'], type: 'greater_than', input: 'TEST_PRIVATE' }] }, status) : saved());
    const user = userEvent.setup(); render(<ManualCosts {...props} />); const row = await editCost(); await confirmBatch(user);
    await within(row).findByText(new RegExp(`HTTP ${status}`)); expect(document.body.textContent).not.toContain('TEST_PRIVATE');
    const input = within(row).getByRole('textbox', { name: '单位成本' }); if (status === 422) expect(input).toBeInvalid();
    fireEvent.change(input, { target: { value: '123456789012345.1235' } }); expect(input).toBeValid();
    await confirmBatch(user); await screen.findByText('已确认成功'); expect(writes).toBe(2);
  });
  it('preserves drafts across view switches, dialog cancellation, server refresh and explicit server filters without submitting prefills', async () => {
    const fetcher = setup(); const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost('5.0001');
    await user.click(screen.getByRole('button', { name: '最近提交' })); await screen.findByText('暂无提交记录');
    await user.click(screen.getByRole('button', { name: '继续编辑 PK 9007199254740995' }));
    let dialog = screen.getByRole('dialog'); expect(within(dialog).getByRole('textbox', { name: '单位成本' })).toHaveValue('5.0001');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '备注' }), { target: { value: 'TEST draft note' } });
    await user.click(within(dialog).getByRole('button', { name: '取消并保留草稿' }));
    await user.click(screen.getByRole('button', { name: '全部 SPU' })); const row = (await screen.findByText('TEST 商品')).closest('tr')!;
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue('5.0001');
    await user.click(screen.getByRole('checkbox', { name: '仅展示未登记 SPU' }));
    await screen.findByText('TEST 商品'); expect(fetcher.mock.calls.some(([url]) => url.includes('missing_manual_cost=true'))).toBe(true);
    await user.click(within(screen.getByText('TEST 商品').closest('tr')!).getByRole('button', { name: '编辑详情' })); dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('textbox', { name: '备注' })).toHaveValue('TEST draft note');
    expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
  });
  it('does not clear a 403 command denial when editing and preserves usable reads', async () => {
    let writes = 0; const fetcher = setup(async () => { writes++; return json({ detail: 'TEST_PRIVATE' }, 403); });
    const user = userEvent.setup(); render(<ManualCosts {...props} />); const row = await editCost(); await confirmBatch(user);
    await within(row).findByText(/HTTP 403/); await editCost('6.0000');
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled(); expect(screen.getByRole('button', { name: '刷新当前列表' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '刷新当前列表' })); await screen.findByText('TEST 商品');
    expect(writes).toBe(1); expect(fetcher.mock.calls.some(([url]) => url.includes('/auth/login'))).toBe(false);
  });
  it('requires explicitly aware optional time without converting decimal inputs or inventing a timezone', async () => {
    const writes: RequestInit[] = []; setup(async (_url, init) => { writes.push(init); return saved(); });
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost();
    await user.click(within(screen.getByText('TEST 商品').closest('tr')!).getByRole('button', { name: '编辑详情' }));
    let dialog = screen.getByRole('dialog'); fireEvent.change(within(dialog).getByRole('textbox', { name: '生效时间' }), { target: { value: '2026-10-24T12:00' } });
    await user.click(within(dialog).getByRole('button', { name: '确认此行草稿' })); await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }));
    expect(writes).toHaveLength(0);
    await user.click(within(screen.getByText('TEST 商品').closest('tr')!).getByRole('button', { name: '编辑详情' })); dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('textbox', { name: '生效时间' })).toBeInvalid();
    fireEvent.change(within(dialog).getByRole('textbox', { name: '生效时间' }), { target: { value: '2026-10-24T12:00:00+07:00' } });
    expect(within(dialog).getByRole('textbox', { name: '生效时间' })).toBeValid();
    await user.click(within(dialog).getByRole('button', { name: '确认此行草稿' })); await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }));
    await screen.findByText('已确认成功'); expect(JSON.parse(writes[0].body as string).valid_from).toBe('2026-10-24T12:00:00+07:00');
  });
});

describe('manual-costs read fences and cancellation', () => {
  it('can stop the remaining captured batch without cancelling or replaying the in-flight row', async () => {
    let release!: (response: Response) => void; const writes: RequestInit[] = [];
    setup(async (_url, init) => { writes.push(init); return new Promise((resolve) => { release = resolve; }); }, [product(), product('9007199254740996', 'TEST 第二')]);
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost(); const unsent = await editCost('22.0000', 'TEST 第二');
    await confirmBatch(user); await user.click(screen.getByRole('button', { name: '停止后续行' }));
    await act(async () => release(saved()));
    await screen.findByText('已确认成功'); expect(writes).toHaveLength(1);
    expect(within(unsent).getByRole('textbox', { name: '单位成本' })).toHaveValue('22.0000'); expect(within(unsent).getByText('已编辑')).toBeVisible();
  });
  it('keeps a full 600-second Retry-After across draft edits, scope changes and verification, with explicit-only requests after expiry', async () => {
    let release!: (response: Response) => void;
    const fetcher = setup(async () => new Promise((resolve) => { release = resolve; }));
    const user = userEvent.setup(); const page = render(<ManualCosts {...props} />); await editCost(); await confirmBatch(user);
    vi.useFakeTimers(); await act(async () => release(json({}, 500, { 'Retry-After': '600' })));
    const writes = fetcher.mock.calls.filter(([, init]) => init.method === 'POST').length;
    const reads = fetcher.mock.calls.filter(([, init]) => init.method === 'GET').length;
    const row = screen.getByText('TEST 商品').closest('tr')!; fireEvent.change(within(row).getByRole('textbox', { name: '单位成本' }), { target: { value: '88.0001' } });
    expect(screen.getByRole('button', { name: '刷新当前列表' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '最近提交' })); await act(async () => {});
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'GET')).toHaveLength(reads);
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(screen.getByRole('button', { name: '查询当前事实' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(screen.getByRole('button', { name: '查询当前事实' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'GET')).toHaveLength(reads);
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(writes);
    fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); await act(async () => {});
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'GET')).toHaveLength(reads + 1);
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeEnabled();
    page.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(600000); });
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(writes);
  });
  it('renders missing-photo metadata and its exact server count without adding an ignored q filter', async () => {
    const fetcher = setup(); const user = userEvent.setup(); render(<ManualCosts {...props} />); await screen.findByText('TEST 商品');
    await user.click(screen.getByRole('button', { name: '缺成本候选' })); await screen.findByText(/服务端缺图总数：9007199254740999/);
    expect(screen.getByText('missing_photo：true')).toBeVisible(); expect(screen.queryByRole('textbox', { name: '按 SPU 或标题搜索' })).toBeNull();
    expect(fetcher.mock.calls.find(([url]) => url.includes('missing-cost-products?'))?.[0]).not.toContain('q=');
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });
  it('preserves raw numeric bigint identities before JSON rounding and never converts money strings', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('channel-accounts?')
      ? new Response('[{"id":9007199254740993,"shop_id":"TEST_shop","account_name":"TEST 店铺"}]')
      : new Response('[{"id":9007199254740995,"shop_pk":9007199254740993,"spu_id":"TEST_long","title":"TEST exact","unit_cost":"999999999999999.9999","currency":"CNY"}]', { headers: { 'X-Total-Count': '9007199254740997' } })));
    render(<ManualCosts {...props} />); const row = (await screen.findByText('TEST exact')).closest('tr')!;
    expect(within(row).getByText('9007199254740995')).toBeVisible(); expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue('999999999999999.9999');
    expect(screen.getByText(/服务端总数：9007199254740997/)).toBeVisible();
  });
  it('does not let an old-shop GET overwrite a newer shop and retains the old-shop draft target', async () => {
    const other = { ...shop, id: '9007199254740994', shop_id: 'TEST_other', account_name: 'TEST 别店' };
    let stale!: (response: Response) => void; let firstReads = 0;
    const fetcher = vi.fn(async (url: string) => {
      if (url.includes('channel-accounts?')) return json([shop, other]);
      if (url.includes('shop_pk=9007199254740993')) { firstReads++; return firstReads === 1 ? json([product()]) : new Promise<Response>((resolve) => { stale = resolve; }); }
      return json([{ ...product('9007199254740996', 'TEST 别店商品'), shop_pk: other.id }]);
    });
    vi.stubGlobal('fetch', fetcher); const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost('22.2000');
    await user.click(screen.getByRole('button', { name: '刷新当前列表' })); await user.selectOptions(screen.getByRole('combobox', { name: '当前店铺' }), other.id);
    await screen.findByText('TEST 别店商品'); await act(async () => stale(json([product('9007199254740997', 'TEST stale')])));
    expect(screen.queryByText('TEST stale')).toBeNull(); expect(screen.getByText('TEST 别店商品')).toBeVisible();
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '继续编辑 PK 9007199254740995' }));
    expect(within(screen.getByRole('dialog')).getByRole('textbox', { name: '单位成本' })).toHaveValue('22.2000');
    expect(within(screen.getByRole('dialog')).getByText(/店铺 #9007199254740993/)).toBeVisible();
  });
});

describe('manual-costs authority and read states', () => {
  it('does not erase a checked uncertainty when a retry is rejected by local time syntax before any write', async () => {
    let writes = 0; setup(async () => { writes++; return json({}, 500); });
    const user = userEvent.setup(); render(<ManualCosts {...props} />); const row = await editCost(); await confirmBatch(user);
    await screen.findByRole('region', { name: '提交结果核对' });
    await user.click(screen.getByRole('button', { name: '查询当前事实' })); await screen.findByText(/GET 不能证明/);
    await user.click(within(row).getByRole('button', { name: '编辑详情' }));
    fireEvent.change(within(screen.getByRole('dialog')).getByRole('textbox', { name: '生效时间' }), { target: { value: '2026-10-24T12:00' } });
    await user.click(screen.getByRole('button', { name: '取消并保留草稿' }));
    await user.click(screen.getByRole('button', { name: '核对后决定重新提交' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }));
    expect(writes).toBe(1); expect(screen.getByRole('region', { name: '提交结果核对' })).toBeVisible();
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
  });
  it('renders server-provided history and previous amounts/currencies without financial math, and sends search/paging to the actual history endpoint', async () => {
    const fetcher = setup(); const user = userEvent.setup(); render(<ManualCosts {...props} />); await screen.findByText('TEST 商品');
    fetcher.mockImplementationOnce(async () => json({ total: '9007199254740999', items: [{ id: '9007199254740999', spu_pk: '9007199254740995', shop_pk: shop.id, spu_id: 'TEST_same_external', title: 'TEST history', unit_cost: '0.0000', currency: 'VND', prev_unit_cost: '9999999999999.9999', prev_currency: 'CNY', note: null, valid_from: '2026-10-24T12:00:00+00:00', valid_to: null, created_at: null }] }));
    await user.click(screen.getByRole('button', { name: '最近提交' }));
    const row = (await screen.findByText('TEST history')).closest('tr')!;
    expect(within(row).getByText('9999999999999.9999 CNY')).toBeVisible(); expect(within(row).getByText('0.0000 VND')).toBeVisible();
    expect(screen.getByText(/服务端总数：9007199254740999/)).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', { name: '按 SPU 或标题搜索' }), { target: { value: 'TEST search' } });
    await user.click(screen.getByRole('button', { name: '搜索' })); await screen.findByText('暂无提交记录');
    expect(fetcher.mock.calls.some(([url]) => url.includes('/v2/reporting/manual-costs?') && url.includes('q=TEST+search') && url.includes('offset=0'))).toBe(true);
  });
  it.each([undefined, { readwrite: false, admin: true }, { readwrite: false, admin: false }])('fails closed for non-write capabilities %# while preserving readable facts', async (roleCapabilities) => {
    const fetcher = setup(); render(<ManualCosts bootstrap={{ ...props.bootstrap, roleCapabilities }} />);
    const row = (await screen.findByText('TEST 商品')).closest('tr')!;
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toBeDisabled(); expect(within(row).getByRole('button', { name: '编辑详情' })).toBeDisabled();
    expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
  });
  it('gives malformed reads an explicit retry rather than silently treating them as empty data', async () => {
    const fetcher = setup(); fetcher.mockImplementationOnce(async () => json([shop])).mockImplementationOnce(async () => json(null));
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await screen.findByText('列表不可用');
    expect(screen.queryByText('当前页暂无候选')).toBeNull(); await user.click(screen.getByRole('button', { name: '重试列表' }));
    await screen.findByText('TEST 商品');
  });
});

describe('manual-costs captured-target boundaries', () => {
  it('retries only the uncertain row after explicit GET and decision, then submits the remaining unsent draft without replaying confirmed rows', async () => {
    const ids: string[] = []; const fetcher = setup(async (_url, init) => {
      const body = JSON.parse(init.body as string) as { spu_pk: string; unit_cost: string }; ids.push(body.spu_pk);
      return ids.length === 2 ? json({}, 500) : saved(body.spu_pk, body.unit_cost);
    }, [product(), product('9007199254740996', 'TEST 第二'), product('9007199254740997', 'TEST 第三')]);
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost(); await editCost('22.0000', 'TEST 第二'); await editCost('33.0000', 'TEST 第三');
    await confirmBatch(user); await screen.findByRole('region', { name: '提交结果核对' });
    fetcher.mockImplementationOnce(async () => json(product('9007199254740996', 'TEST 第二')));
    await user.click(screen.getByRole('button', { name: '查询当前事实' })); await screen.findByText(/GET 不能证明/);
    await user.click(screen.getByRole('button', { name: '核对后决定重新提交' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }));
    await screen.findByText(/本次已确认 1 行/); expect(screen.queryByRole('region', { name: '提交结果核对' })).toBeNull();
    await confirmBatch(user); await screen.findByText(/本次已确认 1 行/);
    expect(ids).toEqual(['9007199254740995', '9007199254740996', '9007199254740996', '9007199254740997']);
    expect(screen.getAllByText('已确认成功')).toHaveLength(3);
  });
  it.each(['wrong-shop', 'malformed', '403'])('keeps an uncertain row fenced after a %s verification GET', async (mode) => {
    let writes = 0; const fetcher = setup(async () => { writes++; return json({}, 500); });
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await editCost(); await confirmBatch(user); await screen.findByRole('region', { name: '提交结果核对' });
    fetcher.mockImplementationOnce(async () => mode === '403' ? json({}, 403) : json(mode === 'malformed' ? null : { ...product(), shop_pk: '42' }));
    await user.click(screen.getByRole('button', { name: '查询当前事实' })); await screen.findByText(/核对未完成/);
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled(); expect(writes).toBe(1);
  });
  it('never expands a missing-photo current-page display filter into extra batch targets', async () => {
    const writes: RequestInit[] = []; const fetcher = setup(async (_url, init) => { writes.push(init); const body = JSON.parse(init.body as string); return saved(body.spu_pk, body.unit_cost); });
    const user = userEvent.setup(); render(<ManualCosts {...props} />); await screen.findByText('TEST 商品');
    fetcher.mockImplementationOnce(async () => json({ total_missing_photo: 81, items: [
      { ...product(), spu_pk: '9007199254740995', missing_photo: true },
      { ...product('9007199254740996', 'TEST 有图'), spu_pk: '9007199254740996', missing_photo: false },
    ] }));
    await user.click(screen.getByRole('button', { name: '缺成本候选' })); await editCost(); await editCost('22.0000', 'TEST 有图');
    await user.click(screen.getByRole('checkbox', { name: /仅显示本页 missing_photo/ })); await confirmBatch(user); await screen.findByText('已确认成功');
    expect(writes).toHaveLength(1); expect(JSON.parse(writes[0].body as string).spu_pk).toBe('9007199254740995');
    expect(screen.getByRole('button', { name: '继续编辑 PK 9007199254740996' })).toBeEnabled(); expect(screen.getByText(/服务端缺图总数：81/)).toBeVisible();
  });
});

describe('manual-costs bounded request lifetimes', () => {
  it.each(['fetch', 'body'])('bounds stalled POST %s, stops remaining targets and ignores late success without replay', async (stage) => {
    let finish!: () => void; let signal!: AbortSignal; const writes: RequestInit[] = [];
    const fetcher = setup(async (_url, init) => {
      writes.push(init); signal = init.signal as AbortSignal;
      if (stage === 'fetch') return new Promise<Response>((resolve) => { finish = () => resolve(saved()); });
      const response = saved(); const raw = await response.clone().text();
      vi.spyOn(response, 'text').mockImplementation(() => new Promise<string>((resolve) => { finish = () => resolve(raw); }));
      return response;
    }, [product(), product('9007199254740996', 'TEST 第二')]);
    const user = userEvent.setup(); const page = render(<ManualCosts {...props} />);
    const first = await editCost(); const remaining = await editCost('22.0000', 'TEST 第二');
    await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
    vi.useFakeTimers(); await act(async () => { fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' })); });
    const calls = fetcher.mock.calls.length;
    expect(writes).toHaveLength(1); expect(within(first).getByRole('textbox', { name: '单位成本' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(29_999); });
    expect(screen.queryByRole('region', { name: '提交结果核对' })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole('region', { name: '提交结果核对' })).toBeVisible();
    expect(signal.aborted).toBe(true); expect(screen.queryByRole('button', { name: '停止后续行' })).toBeNull();
    expect(within(first).getByRole('textbox', { name: '单位成本' })).toBeEnabled();
    expect(within(first).getByRole('textbox', { name: '单位成本' })).toHaveValue('123456789012345.1234');
    expect(within(remaining).getByRole('textbox', { name: '单位成本' })).toHaveValue('22.0000');
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    expect(fetcher.mock.calls).toHaveLength(calls);
    await act(async () => { finish(); await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.queryByText('已确认成功')).toBeNull(); expect(screen.getByRole('region', { name: '提交结果核对' })).toBeVisible();
    expect(fetcher.mock.calls).toHaveLength(calls); expect(writes).toHaveLength(1);
    fireEvent.change(within(first).getByRole('textbox', { name: '单位成本' }), { target: { value: '33.0000' } });
    expect(screen.getByRole('button', { name: '提交本页已编辑行' })).toBeDisabled();
    page.unmount(); expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['fetch', 'body'])('bounds stalled verification %s, ignores late facts and requires a new explicit GET before any retry decision', async (stage) => {
    let finish!: () => void; let signal!: AbortSignal; let writes = 0;
    const fetcher = setup(async () => { writes++; return json({}, 500); });
    const user = userEvent.setup(); const page = render(<ManualCosts {...props} />); const row = await editCost(); await confirmBatch(user);
    await screen.findByRole('region', { name: '提交结果核对' });
    const raw = JSON.stringify(product());
    fetcher.mockImplementationOnce(async (_url, init) => {
      signal = init.signal as AbortSignal;
      if (stage === 'fetch') return new Promise<Response>((resolve) => { finish = () => resolve(json(product())); });
      const response = json(product()); vi.spyOn(response, 'text').mockImplementation(() => new Promise<string>((resolve) => { finish = () => resolve(raw); })); return response;
    });
    vi.useFakeTimers(); await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); });
    const calls = fetcher.mock.calls.length;
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(signal.aborted).toBe(true); expect(screen.getByText(/核对未完成/)).toBeVisible();
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    fireEvent.change(within(row).getByRole('textbox', { name: '单位成本' }), { target: { value: '44.0000' } });
    await act(async () => { finish(); await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.queryByText(/已读取当前公开记录/)).toBeNull();
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    expect(fetcher.mock.calls).toHaveLength(calls); expect(writes).toBe(1);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); });
    expect(fetcher.mock.calls).toHaveLength(calls + 1); expect(writes).toBe(1);
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeEnabled();
    expect(within(row).getByRole('textbox', { name: '单位成本' })).toHaveValue('44.0000');
    page.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
});

describe('manual-costs deadline cleanup', () => {
  it.each(['write', 'verification'])('quietly aborts a stalled %s on unmount and ignores later completion without follow-up requests', async (kind) => {
    let finish!: () => void; let signal!: AbortSignal;
    const fetcher = setup(async (_url, init) => {
      if (kind !== 'write') return json({}, 500);
      signal = init.signal as AbortSignal;
      return new Promise<Response>((resolve) => { finish = () => resolve(saved()); });
    }, [product(), product('9007199254740996', 'TEST 第二')]);
    const user = userEvent.setup(); const page = render(<ManualCosts {...props} />); await editCost(); await editCost('22.0000', 'TEST 第二');
    if (kind === 'write') {
      await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
    } else {
      await confirmBatch(user); await screen.findByRole('region', { name: '提交结果核对' });
      fetcher.mockImplementationOnce(async (_url, init) => {
        signal = init.signal as AbortSignal;
        return new Promise<Response>((resolve) => { finish = () => resolve(json(product())); });
      });
    }
    vi.useFakeTimers();
    const scheduled = vi.spyOn(globalThis, 'setTimeout'); const cleared = vi.spyOn(globalThis, 'clearTimeout');
    await act(async () => { fireEvent.click(kind === 'write' ? within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' }) : screen.getByRole('button', { name: '查询当前事实' })); });
    const deadline = scheduled.mock.results[scheduled.mock.calls.findIndex((call) => call[1] === 30_000)].value;
    const calls = fetcher.mock.calls.length; const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => { page.unmount(); });
    expect(signal.aborted).toBe(true); expect(cleared).toHaveBeenCalledWith(deadline);
    await act(async () => { finish(); await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetcher.mock.calls).toHaveLength(calls); expect(errors).not.toHaveBeenCalled();
    expect(screen.queryByRole('region', { name: '提交结果核对' })).toBeNull();
    expect(screen.queryByText('已确认成功')).toBeNull();
  });
});

describe('manual-costs received error-header fences', () => {
  it.each(['POST', 'verification GET'])('keeps a received 600-second fence from %s despite a stalled 500 body and local deadline', async (source) => {
    const stalled = new Response(new ReadableStream<Uint8Array>({ start() {} }), {
      status: 500, headers: { 'Retry-After': '600', 'x-request-id': 'TEST_fenced_error' },
    });
    const writes: RequestInit[] = [];
    const fetcher = setup(async (_url, init) => {
      writes.push(init);
      if (writes.length === 1) return source === 'POST' ? stalled : json({}, 500);
      const body = JSON.parse(init.body as string) as { spu_pk: string; unit_cost: string };
      return saved(body.spu_pk, body.unit_cost);
    }, [product(), product('9007199254740996', 'TEST 第二')]);
    const user = userEvent.setup(); const page = render(<ManualCosts {...props} />);
    await editCost(); await editCost('22.0000', 'TEST 第二');
    if (source === 'POST') {
      await user.click(screen.getByRole('button', { name: '提交本页已编辑行' }));
    } else {
      await confirmBatch(user); await screen.findByRole('region', { name: '提交结果核对' });
      fetcher.mockImplementationOnce(async () => stalled);
    }
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(source === 'POST'
      ? within(screen.getByRole('dialog')).getByRole('button', { name: '确认逐行提交' })
      : screen.getByRole('button', { name: '查询当前事实' })); });
    const calls = fetcher.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByRole('region', { name: '提交结果核对' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '停止后续行' })).toBeNull();
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '刷新当前列表' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    expect(writes).toHaveLength(1); expect(screen.queryByText('已确认成功')).toBeNull();
    const first = screen.getByText('TEST 商品').closest('tr')!;
    expect(within(first).getByRole('textbox', { name: '单位成本' })).toHaveValue('123456789012345.1234');
    expect(within(screen.getByText('TEST 第二').closest('tr')!).getByRole('textbox', { name: '单位成本' })).toHaveValue('22.0000');
    fireEvent.change(within(first).getByRole('textbox', { name: '单位成本' }), { target: { value: '33.0000' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); fireEvent.click(screen.getByRole('button', { name: '最近提交' })); });
    expect(fetcher.mock.calls).toHaveLength(calls);
    await act(async () => { await vi.advanceTimersByTimeAsync(569_999); });
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeDisabled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); fireEvent.click(screen.getByRole('button', { name: '全部 SPU' })); });
    expect(fetcher.mock.calls).toHaveLength(calls); expect(writes).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole('button', { name: '查询当前事实' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeDisabled();
    expect(fetcher.mock.calls).toHaveLength(calls); // Expiry never sends a request.
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查询当前事实' })); });
    expect(fetcher.mock.calls).toHaveLength(calls + 1); expect(writes).toHaveLength(1);
    expect(screen.getByRole('button', { name: '核对后决定重新提交' })).toBeEnabled();
    expect(screen.getByText(/GET 不能证明原提交是否生效/)).toBeVisible();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核对后决定重新提交' })); });
    const dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/33.0000 VND/)).toBeVisible();
    expect(writes).toHaveLength(1);
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: '确认逐行提交' })); });
    expect(writes).toHaveLength(2);
    expect(JSON.parse(writes[1].body as string)).toMatchObject({ spu_pk: '9007199254740995', spu_id: 'TEST_same_external', unit_cost: '33.0000', currency: 'VND' });
    expect(screen.getByText('已确认成功')).toBeVisible();
    expect(within(screen.getByText('TEST 第二').closest('tr')!).getByRole('textbox', { name: '单位成本' })).toHaveValue('22.0000');
    page.unmount();
  });
});
