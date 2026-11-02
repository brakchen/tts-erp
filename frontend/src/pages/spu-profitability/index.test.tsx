import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from './index';
import type { FrontendBootstrap } from '../../shared/contracts';

vi.mock('./ProfitabilityTable', () => ({ sortLabel: (value: string) => value, ProfitabilityTable: ({ rows, error }: { rows: { spu_id: string }[]; error?: string }) => <div aria-label="TEST 盈利行">{rows.map((row) => <span key={row.spu_id}>{row.spu_id}</span>)}{error && <p role="alert">{error}</p>}</div> }));
const bootstrap: FrontendBootstrap = { pageId: 'spu-roi', rootPath: '', releaseId: 'TEST_release', navigation: [], roleCapabilities: { readwrite: true, admin: false } };
const shop = { id: 7, shop_id: '90071992547409931', account_name: 'TEST 店铺', region: 'VN' };
const product = { spu_pk: 101, spu_id: 'TEST_OLD', title: 'TEST 原商品', status: 'ACTIVATE', main_image_url: null };
const payload = (id = 'TEST_RESULT') => ({ items: [{ ...product, spu_id: id }], total: overviewTotal, totals: { net_profit: '12.0000', spend: '3.0000' }, meta: {} });
let calls: { url: string; init?: RequestInit }[];
let stored: { spuId: string; title: string }[];
let patchFailure: number | undefined;
let overviewError: { code: string } | undefined;
let overviewTotal: number;
let shopRows: typeof shop[];
let limitedFocusedShop: string | undefined;
function response(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }); }
beforeEach(() => {
  calls = []; stored = [{ spuId: 'TEST_OLD', title: 'TEST 原商品' }]; patchFailure = undefined; overviewError = undefined; overviewTotal = 1; shopRows = [shop]; limitedFocusedShop = undefined; localStorage.clear(); history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url.includes('channel-accounts')) return response(shopRows);
    if (url.includes('channel-product-options')) return response([{ spu_id: 'TEST_NEW', title: 'TEST 新商品', status: 'ACTIVATE' }]);
    if (url.includes('/reporting/focused-spus/')) {
      const shopPk = new URL(url, location.origin).pathname.split('/').at(-1)!;
      if (init?.method === 'PATCH') {
        if (patchFailure) return response({ detail: 'TEST_failure' }, patchFailure);
        const body = JSON.parse(String(init.body)); stored = stored.filter((item) => !body.removeSpuIds.includes(item.spuId)); for (const id of body.addSpuIds) stored.push({ spuId: id, title: id });
        if (overviewTotal > 1) overviewTotal = stored.length;
        return response({ shopPk: 7, total: stored.length, addedSpuIds: body.addSpuIds, removedSpuIds: body.removeSpuIds });
      }
      if (shopPk === limitedFocusedShop) return new Response('{"detail":"TEST_read_cooldown"}', { status: 429, headers: { 'Retry-After': '120', 'Content-Type': 'application/json' } });
      return response({ shopPk: Number(shopPk), items: stored, total: stored.length, matchedTotal: stored.length, limit: 500, offset: 0 });
    }
    if (url.includes('/analytics/spu-roi?')) {
      if (overviewError) return response(overviewError, 503);
      const result = payload(); if (Number(new URL(url, location.origin).searchParams.get('offset')) >= result.total) result.items = [];
      return response(result);
    }
    return response({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it.each(['spu-roi', 'focused-spus'] as const)('keeps every %s search and scope control below the overview', async (pageId) => {
  render(<Page bootstrap={{ ...bootstrap, pageId }} />);
  await screen.findByText('TEST_RESULT');
  const overview = screen.getByRole('region', { name: '经营概览' });
  for (const control of [screen.getByRole('region', { name: '店铺与经营范围' }), screen.getByLabelText('按 SPU ID / 标题搜索')]) {
    expect(overview.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
  expect(within(overview).getByLabelText('销售')).toBeInTheDocument();
  expect(within(overview).getByLabelText('成本')).toBeInTheDocument();
  expect(within(overview).getByLabelText('利润')).toBeInTheDocument();
});
it('both page profiles render through the same React owner and keep the server scope', async () => {
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByText('TEST_RESULT');
  const reads = calls.filter((call) => call.url.includes('/analytics/spu-roi?'));
  expect(reads.length).toBeGreaterThan(0); const query = new URL(reads.at(-1)!.url, location.origin).searchParams;
  expect(query.get('scope')).toBe('focused'); expect(query.has('spu_ids')).toBe(false); expect(query.has('w_start')).toBe(false);
  expect(calls.filter((call) => call.url.includes('channel-accounts'))).toHaveLength(1);
});
it('adding one focused search result never implicitly removes existing subscriptions', async () => {
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByRole('button', { name: '移除 SPU TEST_OLD' });
  fireEvent.change(screen.getByLabelText('按 SPU ID / 标题搜索'), { target: { value: 'TEST 新' } });
  const option = await screen.findByRole('checkbox', { name: /TEST_NEW/ }); fireEvent.click(option);
  await waitFor(() => expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1));
  const patch = calls.find((call) => call.init?.method === 'PATCH')!;
  expect(JSON.parse(String(patch.init?.body))).toEqual({ addSpuIds: ['TEST_NEW'], removeSpuIds: [] });
  expect(patch.init?.headers && new Headers(patch.init.headers).get('X-Requested-With')).toBe('tts-erp');
  expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeInTheDocument();
});
it('shows every focused SPU inside the search field and removes only the clicked tag after saving', async () => {
  stored = Array.from({ length: 29 }, (_, i) => ({ spuId: `TEST_TAG_${i}`, title: `TEST 商品 ${i}` }));
  const fetcher = vi.mocked(fetch); const original = fetcher.getMockImplementation()!; let finish!: () => void;
  fetcher.mockImplementation((input, init) => init?.method === 'PATCH' ? new Promise<Response>((resolve) => { finish = () => { void original(input, init).then(resolve); }; }) : original(input, init));
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByRole('button', { name: '移除 SPU TEST_TAG_28' });
  const field = screen.getByRole('group', { name: 'SPU 搜索与已选标签' });
  expect(within(field).getAllByRole('button', { name: /^移除 SPU / })).toHaveLength(29);
  expect(field).toContainElement(screen.getByLabelText('按 SPU ID / 标题搜索'));
  expect(field.querySelector('details')).toBeNull();
  fireEvent.click(within(field).getByRole('button', { name: '移除 SPU TEST_TAG_12' }));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  expect(within(field).getAllByRole('button', { name: /^移除 SPU / })).toHaveLength(29);
  expect(within(field).getByRole('button', { name: '移除 SPU TEST_TAG_12' })).toBeDisabled();
  await act(async () => { finish(); });
  await waitFor(() => expect(within(field).queryByRole('button', { name: '移除 SPU TEST_TAG_12' })).toBeNull());
  expect(within(field).getAllByRole('button', { name: /^移除 SPU / })).toHaveLength(28);
  const writes = calls.filter((call) => call.init?.method === 'PATCH');
  expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0].init?.body))).toEqual({ addSpuIds: [], removeSpuIds: ['TEST_TAG_12'] });
  expect(stored.map((item) => item.spuId)).toEqual(Array.from({ length: 29 }, (_, i) => `TEST_TAG_${i}`).filter((id) => id !== 'TEST_TAG_12'));
});
it('shows focused tags in the search field without allowing readonly removal', async () => {
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus', roleCapabilities: { readwrite: false, admin: false } }} />);
  const remove = await screen.findByRole('button', { name: '移除 SPU TEST_OLD' });
  expect(screen.getByRole('group', { name: 'SPU 搜索与已选标签' })).toContainElement(remove);
  expect(remove).toBeDisabled();
  fireEvent.click(remove);
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(0);
});
it('a failed focused mutation preserves selection and requires reading facts before another write', async () => {
  patchFailure = 500; render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  const remove = await screen.findByRole('button', { name: '移除 SPU TEST_OLD' }); fireEvent.click(remove);
  const retryRead = await screen.findByRole('button', { name: '重新读取关注事实' });
  expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeDisabled();
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
  fireEvent.click(retryRead);
  await waitFor(() => expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeEnabled());
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
});
it('standard default uses the shop yesterday while explicit invalid drill dates reach validation unchanged', async () => {
  history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7&w_start=bad-date&w_end=2026-09-30&spu_ids=90071992547409931');
  render(<Page bootstrap={bootstrap} />); await screen.findByText('TEST_RESULT');
  const last = calls.filter((call) => call.url.includes('/analytics/spu-roi?')).at(-1)!;
  const params = new URL(last.url, location.origin).searchParams;
  expect(params.get('w_start')).toBe('bad-date'); expect(params.get('w_end')).toBe('2026-09-30'); expect(params.get('spu_ids')).toBe('90071992547409931');
});
it('inverted date edits retain both inputs without querying a widened range', async () => {
  history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7&w_start=2026-09-01&w_end=2026-09-30');
  render(<Page bootstrap={bootstrap} />); await screen.findByText('TEST_RESULT');
  fireEvent.change(screen.getByLabelText('销售/退款起始日期（空 = 不限）'), { target: { value: '2026-10-01' } });
  expect(screen.getByLabelText('销售/退款起始日期（空 = 不限）')).toHaveValue('2026-10-01');
  expect(screen.getByLabelText('销售/退款截止日期（空 = 不限）')).toHaveValue('2026-09-30');
  expect(screen.getByRole('alert')).toHaveTextContent('起始日期不能晚于截止日期');
});
it('explains only the typed missing-FX error and leaves no old successful result visible', async () => {
  overviewError = { code: 'FX_RATE_UNAVAILABLE' };
  render(<Page bootstrap={bootstrap} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('汇率数据缺失，无法计算结果');
  expect(screen.queryByText('TEST_RESULT')).not.toBeInTheDocument();
});
it('a server write denial remains a denial after reading the focused facts', async () => {
  patchFailure = 403; render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  fireEvent.click(await screen.findByRole('button', { name: '移除 SPU TEST_OLD' }));
  fireEvent.click(await screen.findByRole('button', { name: '重新读取关注事实' }));
  await screen.findByText('服务端拒绝修改此店铺的关注清单；重新读取事实不会解除写权限限制。');
  expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeDisabled();
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
});
it('clear cancels a standard bulk lookup and ignores its late response', async () => {
  const fetcher = vi.mocked(fetch); const original = fetcher.getMockImplementation()!; let finish!: (response: Response) => void;
  fetcher.mockImplementation((input, init) => String(input).includes('channel-product-options') ? new Promise<Response>((resolve) => { finish = resolve; }) : original(input, init));
  render(<Page bootstrap={bootstrap} />); await screen.findByText('TEST_RESULT');
  fireEvent.click(screen.getByRole('button', { name: '批量粘贴' }));
  fireEvent.change(screen.getByLabelText('粘贴 SPU ID（逗号、空格或换行分隔）'), { target: { value: 'TEST_NEW' } });
  fireEvent.click(screen.getByRole('button', { name: '解析并添加' }));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  expect(screen.getByRole('button', { name: '清空' })).toBeEnabled(); fireEvent.click(screen.getByRole('button', { name: '清空' }));
  await act(async () => { finish(response([{ spu_id: 'TEST_NEW', title: 'TEST 新商品', status: 'ACTIVATE' }])); });
  expect(screen.queryByRole('button', { name: '移除 SPU TEST_NEW' })).not.toBeInTheDocument();
  expect(screen.getByText('已选择 0 个')).toBeInTheDocument();
});
it('removing a focused item on the last page resets offset before refreshing the smaller scope', async () => {
  overviewTotal = 101; stored = Array.from({ length: 101 }, (_, index) => ({ spuId: `TEST_PAGE_${index}`, title: `TEST 商品 ${index}` }));
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByText('TEST_RESULT');
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText('第 2 页 · 共 2 页');
  expect(within(screen.getByRole('group', { name: 'SPU 搜索与已选标签' })).getAllByRole('button', { name: /^移除 SPU / })).toHaveLength(101);
  fireEvent.click(screen.getByRole('button', { name: '移除 SPU TEST_PAGE_0' }));
  await screen.findByText('第 1 页 · 共 1 页');
  await screen.findByText('TEST_RESULT');
  const last = calls.filter((call) => call.url.includes('/analytics/spu-roi?')).at(-1)!;
  expect(new URL(last.url, location.origin).searchParams.get('offset')).toBe('0');
  expect(stored).toHaveLength(100);
});
it('a mixed bulk lookup keeps an uncertain focused save visible instead of overwriting it with missing IDs', async () => {
  patchFailure = 503; render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByRole('button', { name: '移除 SPU TEST_OLD' });
  fireEvent.click(screen.getByRole('button', { name: '批量粘贴' }));
  const input = screen.getByLabelText('粘贴 SPU ID（逗号、空格或换行分隔）');
  fireEvent.change(input, { target: { value: 'TEST_NEW, TEST_MISSING' } });
  fireEvent.click(screen.getByRole('button', { name: '解析并添加' }));
  await screen.findByRole('button', { name: '重新读取关注事实' });
  expect(screen.getByRole('alert')).toHaveTextContent('原选择保留。请先重新读取关注事实');
  expect(input).toHaveValue('TEST_NEW, TEST_MISSING');
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
});
it('changing shop retains the selection Retry-After fence and requires explicit readback after expiry', async () => {
  vi.useFakeTimers(); let now = 0; vi.spyOn(performance, 'now').mockImplementation(() => now);
  limitedFocusedShop = '7'; shopRows = [shop, { ...shop, id: 8, shop_id: 'TEST_SHOP_EIGHT', account_name: 'TEST 店铺八' }];
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole('button', { name: '重新读取关注事实' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('当前店铺'), { target: { value: '8' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  const readsForEight = () => calls.filter((call) => call.url.includes('/reporting/focused-spus/8?'));
  expect(readsForEight()).toHaveLength(0);
  now = 119_000; await act(async () => { await vi.advanceTimersByTimeAsync(119_000); });
  expect(screen.getByRole('button', { name: '重新读取关注事实' })).toBeDisabled();
  now = 120_001; await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
  expect(readsForEight()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '重新读取关注事实' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(readsForEight()).toHaveLength(1);
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(0);
});
