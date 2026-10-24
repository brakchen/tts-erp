import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import AdDaily from './index';
const row = { id: 7, seller_id: '900719925474099312345', shop_pk: null, shop_name: null, advertiser_id: 'TEST_ADV', campaign_id: 'TEST_CAMPAIGN', product_id: 'TEST_PRODUCT', product_title: null, endpoint: '/TEST/a?x=1&y=2', day: '2026-09-03', mixed_real_cost: '9999999999999999.9999', onsite_roi2_shopping_sku: 0, onsite_roi2_shopping_value: '0.0000', onsite_mixed_real_roi2_shopping: null, metrics_extra: { clicks: 0 }, created_at: '2026-09-03T00:00:00+00:00', updated_at: '2026-09-03T00:00:00+00:00' };
const payload = { items: [row], total: 1, limit: 50, offset: 0, summary: { row_count: 1, spend: row.mixed_real_cost, attributed_orders: 0, attributed_gmv: '0.0000', weighted_roi: null, currency: 'USD' } };
const options = { sellers: [{ seller_id: row.seller_id, shop_name: null, row_count: 1 }], advertisers: [{ seller_id: row.seller_id, advertiser_id: row.advertiser_id, row_count: 1 }], endpoints: [{ endpoint: row.endpoint, row_count: 1 }], min_day: row.day, max_day: row.day };
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'ad-daily', navigation: [], releaseId: 'TEST' } };
async function mount() { return render(<AdDaily {...props} />); }
function api(data: unknown = payload) {
  const fetcher = vi.fn(async (url: string) => json(url.endsWith('/options') ? options : data));
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); history.replaceState(null, '', '/'); });
it('preserves every source decimal digit in React spend and server full-filter summary', async () => {
  api({ ...payload, summary: { ...payload.summary, row_count: 999, weighted_roi: '7.1234' } }); await mount();
  expect(await screen.findAllByText('9,999,999,999,999,999.9999')).toHaveLength(2);
  const summary = screen.getByRole('region', { name: '全部筛选结果汇总' });
  expect(summary).toHaveTextContent('999'); expect(summary).toHaveTextContent('7.1234×'); expect(summary).toHaveTextContent('USD');
  expect(screen.getByRole('table', { name: '广告日明细' })).toHaveTextContent('0.0000');
});
it('preserves URL filters, long external seller ID and both remote sort parameters under root path', async () => {
  history.replaceState(null, '', '/?seller_id=900719925474099312345&advertiser_id=TEST_ADV&endpoint=%2FTEST%2Fa%3Fx%3D1%26y%3D2&day_from=2026-09-01&day_to=2026-09-03&q=TEST_%25&sort=spend&order=asc&limit=25&offset=25');
  const fetcher = api({ ...payload, total: 100 }); await mount(); await screen.findByText('TEST_CAMPAIGN');
  const url = new URL(fetcher.mock.calls.find(([url]) => !url.endsWith('/options'))![0], 'https://test.invalid');
  expect(url.pathname).toBe('/tts/v2/reporting/ad-daily');
  for (const [key, value] of new URLSearchParams(location.search)) expect(url.searchParams.get(key)).toBe(value);
  expect(screen.getByLabelText('店铺（外部 Seller ID）')).toHaveValue(row.seller_id);
});
it.each(['日期', '店铺', '计划', '商品', '消耗 USD', '归因订单', '归因 GMV USD', '源 ROI', '最近更新'])('requests remote sort for %s without sorting or recomputing locally', async (label) => {
  const fetcher = api(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  const key = ['day','shop','campaign','product','spend','orders','gmv','roi','updated_at'][['日期','店铺','计划','商品','消耗 USD','归因订单','归因 GMV USD','源 ROI','最近更新'].indexOf(label)];
  await userEvent.click(screen.getByRole('button', { name: `排序：${label}` }));
  await vi.waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toContain(`sort=${key}`));
  expect(new URL(fetcher.mock.calls.at(-1)![0], 'https://test.invalid').searchParams.get('offset')).toBe('0');
});
it('applies draft filters explicitly, resets offset and dependent advertiser, supports reset', async () => {
  const fetcher = api(); const user = userEvent.setup(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await user.selectOptions(screen.getByLabelText('店铺（外部 Seller ID）'), row.seller_id);
  await user.selectOptions(screen.getByLabelText('广告账户'), row.advertiser_id);
  await user.type(screen.getByLabelText('计划 / 商品 ID 搜索'), 'TEST_%');
  expect(fetcher).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole('button', { name: '应用筛选' }));
  await vi.waitFor(() => expect(location.search).toContain('seller_id=900719925474099312345'));
  expect(new URLSearchParams(location.search).get('q')).toBe('TEST_%');
  await user.click(screen.getByRole('button', { name: '重置筛选' }));
  await vi.waitFor(() => expect(new URLSearchParams(location.search).has('seller_id')).toBe(false));
});
it('rejects reversed dates without a request and preserves the draft', async () => {
  const fetcher = api(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-09-04' } });
  fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-09-01' } });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  expect(await screen.findByText('开始日期不能晚于结束日期')).toBeVisible(); expect(fetcher).toHaveBeenCalledTimes(2);
});
it('pages remotely and preserves selected limit', async () => {
  const fetcher = api({ ...payload, total: 120 }); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await userEvent.click(screen.getByRole('button', { name: '下一页' }));
  await vi.waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toContain('offset=50'));
  await screen.findByText('TEST_CAMPAIGN'); await userEvent.click(screen.getByRole('button', { name: '上一页' }));
  await vi.waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toContain('offset=0'));
});
it('shows readable detail metadata and safe extra metrics, copies original ID', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  api(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await userEvent.click(screen.getByRole('button', { name: '复制计划 ID TEST_CAMPAIGN' })); expect(writeText).toHaveBeenCalledWith('TEST_CAMPAIGN');
  await userEvent.click(screen.getByRole('button', { name: '更多详情 TEST_CAMPAIGN' }));
  const detail = screen.getByRole('region', { name: '记录详情 TEST_CAMPAIGN' }); expect(detail).toHaveTextContent(row.endpoint); expect(detail).toHaveTextContent('clicks'); expect(detail).toHaveTextContent('0');
});
it('distinguishes null from zero, empty results and malformed successes', async () => {
  api({ ...payload, items: [{ ...row, mixed_real_cost: null }] }); const view = await mount(); await screen.findByText('TEST_CAMPAIGN');
  expect(within(screen.getByRole('table')).getAllByText('—').length).toBeGreaterThan(0); view.unmount();
  api({ ...payload, items: [], total: 0 }); const empty = await mount(); expect(await screen.findByText('暂无广告日数据')).toBeVisible(); empty.unmount();
  api({}); await mount(); expect(await screen.findByRole('alert')).toHaveTextContent('数据格式不符合约定');
});
it.each([401,403,429,500])('shows safe HTTP %s failure, manual retry only, never raw response detail', async (status) => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: 'TEST_SECRET' }), { status, headers: { 'Retry-After': '1' } })); vi.stubGlobal('fetch', fetcher);
  await mount(); expect(await screen.findAllByRole('alert')).not.toHaveLength(0); expect(document.body).not.toHaveTextContent('TEST_SECRET');
  const count = fetcher.mock.calls.length; await new Promise((resolve) => setTimeout(resolve, 1100)); expect(fetcher).toHaveBeenCalledTimes(count);
  expect(screen.getByRole('button', { name: '刷新数据' })).toBeEnabled();
});
it('honors the full Retry-After across refresh, sorts, and history changes without auto fetch', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  const fetcher = vi.fn(async (url: string) => url.endsWith('/options') ? json(options) : new Response('{}', { status: 429, headers: { 'Retry-After': '86401' } }));
  vi.stubGlobal('fetch', fetcher); await mount();
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole('button', { name: '刷新数据' })).toBeDisabled();
  const count = fetcher.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(86_400_000); });
  expect(screen.getByRole('button', { name: '刷新数据' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  history.replaceState(null, '', '/?q=TEST_history'); fireEvent.popState(window);
  expect(fetcher).toHaveBeenCalledTimes(count);
  await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
  expect(screen.getByRole('button', { name: '刷新数据' })).toBeEnabled(); expect(fetcher).toHaveBeenCalledTimes(count);
  fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  await act(async () => { await Promise.resolve(); });
  expect(fetcher.mock.calls.filter(([url]) => !url.endsWith('/options')).at(-1)![0]).toContain('q=TEST_history');
});
it('ignores a stale response after history change and aborts the old GET', async () => {
  let release!: (response: Response) => void;
  const signals: AbortSignal[] = []; let calls = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/options')) return json(options);
    signals.push(init.signal as AbortSignal);
    if (++calls === 1) return new Promise<Response>((resolve) => { release = resolve; });
    return json({ ...payload, items: [{ ...row, campaign_id: 'TEST_FRESH' }] });
  });
  vi.stubGlobal('fetch', fetcher); await mount();
  history.replaceState(null, '', '/?q=TEST_FRESH'); fireEvent.popState(window);
  expect(await screen.findByText('TEST_FRESH')).toBeVisible(); expect(signals[0].aborted).toBe(true);
  await act(async () => { release(json(payload)); });
  expect(screen.queryByText('TEST_CAMPAIGN')).toBeNull(); expect(screen.getByLabelText('计划 / 商品 ID 搜索')).toHaveValue('TEST_FRESH');
});
it('recovers an obsolete offset using server total, never client totals', async () => {
  history.replaceState(null, '', '/?offset=200&limit=50'); const fetcher = api({ ...payload, total: 61 });
  await mount(); await screen.findByText('TEST_CAMPAIGN');
  expect(fetcher.mock.calls.filter(([url]) => !url.endsWith('/options')).map(([url]) => new URL(url, 'https://test.invalid').searchParams.get('offset'))).toEqual(['200', '50']);
  expect(screen.getByRole('navigation', { name: '广告明细分页' })).toHaveTextContent('51–51 / 61');
});
it('keeps a failed copy selectable and reports explicit failure', async () => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('TEST no clipboard')) } });
  api(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await userEvent.click(screen.getByRole('button', { name: '复制商品 ID TEST_PRODUCT' }));
  expect(await screen.findByText('复制失败，请手动选择 ID')).toBeVisible(); expect(screen.getByText('TEST_PRODUCT')).toBeVisible();
});
it('keeps options errors separate from usable rows and retries options explicitly', async () => {
  let optionsCalls = 0;
  const fetcher = vi.fn(async (url: string) => url.endsWith('/options') && ++optionsCalls === 1 ? new Response('{}', { status: 403 }) : json(url.endsWith('/options') ? options : payload));
  vi.stubGlobal('fetch', fetcher); await mount(); expect(await screen.findByText('TEST_CAMPAIGN')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('筛选项');
  await userEvent.click(screen.getByRole('button', { name: '刷新数据' }));
  await vi.waitFor(() => expect(screen.queryByRole('alert')).toBeNull()); expect(optionsCalls).toBe(2);
});
it('does not leave refresh permanently loading if history cancels an outstanding read during an options cooldown', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/options') ? new Response('{}', { status: 429, headers: { 'Retry-After': '3' } }) : new Promise<Response>(() => {})));
  const view = await mount(); await act(async () => { await Promise.resolve(); });
  history.replaceState(null, '', '/?q=TEST_PENDING'); fireEvent.popState(window);
  await act(async () => { await vi.advanceTimersByTimeAsync(3001); });
  expect(screen.getByRole('button', { name: '刷新数据' })).toBeEnabled(); view.unmount();
});
it('hides stale rows when history changes filter scope during the options cooldown', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/options') ? new Response('{}', { status: 429, headers: { 'Retry-After': '3' } }) : json(payload)));
  await mount(); await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('TEST_CAMPAIGN')).toBeVisible();
  history.replaceState(null, '', '/?q=TEST_OTHER'); fireEvent.popState(window);
  expect(screen.queryByText('TEST_CAMPAIGN')).toBeNull();
});
it('sends chosen endpoint, dates, and page size only on explicit apply', async () => {
  const fetcher = api(); const user = userEvent.setup(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await user.selectOptions(screen.getByLabelText('数据接口'), row.endpoint);
  await user.selectOptions(screen.getByLabelText('每页行数'), '100');
  fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-09-01' } });
  fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-09-03' } });
  expect(fetcher).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole('button', { name: '应用筛选' }));
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  const params = new URL(fetcher.mock.calls.at(-1)![0], 'https://test.invalid').searchParams;
  expect(params.get('endpoint')).toBe(row.endpoint); expect(params.get('limit')).toBe('100'); expect(params.get('day_from')).toBe('2026-09-01'); expect(params.get('day_to')).toBe('2026-09-03');
});
it('toggles sort direction remotely and supports keyboard detail activation', async () => {
  const fetcher = api(); const user = userEvent.setup(); await mount(); await screen.findByText('TEST_CAMPAIGN');
  await user.click(screen.getByRole('button', { name: '排序：消耗 USD' }));
  await vi.waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toContain('order=desc'));
  await screen.findByText('TEST_CAMPAIGN'); await user.click(screen.getByRole('button', { name: '排序：消耗 USD' }));
  await vi.waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toContain('order=asc'));
  const trigger = await screen.findByRole('button', { name: '更多详情 TEST_CAMPAIGN' }); trigger.focus(); await user.keyboard('{Enter}');
  expect(trigger).toHaveAttribute('aria-expanded', 'true'); expect(screen.getByRole('region', { name: '记录详情 TEST_CAMPAIGN' })).toBeVisible();
  await user.keyboard('{Enter}'); expect(screen.queryByRole('region', { name: '记录详情 TEST_CAMPAIGN' })).toBeNull(); expect(trigger).toHaveFocus();
});
