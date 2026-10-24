import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import { setUnauthorizedHandler } from '../../shared/api/client';
import Page from './index';
import { testItem, testPayload, testPayload3 } from './fixtures';
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'spu-profit-deterioration', navigation: [], releaseId: 'TEST' } };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...headers } });
function api(handler: (url: string, init?: RequestInit) => Promise<Response> = async (url) => json(url.includes('window_days=3') ? testPayload3 : testPayload)) {
  const fetcher = vi.fn((url: string, init?: RequestInit) => url.includes('channel-accounts') ? Promise.resolve(json([{ id: 11, account_name: 'TEST店铺', shop_id: 'TEST_SHOP' }])) : handler(url, init));
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); history.replaceState(null, '', '/'); });
it('renders exact server periods, full-scope totals, string financial values and distinct zero/unknown facts', async () => {
  const fetcher = api(); render(<Page {...props} />);
  const table = await screen.findByRole('table', { name: 'SPU 两期盈利对比' });
  expect(within(table).getByText('9999999999999999.99')).toBeVisible();
  expect(within(table).getByText('-0.50')).toBeVisible();
  expect(within(table).getByText('本期：无单但有广告费')).toBeVisible();
  expect(within(table).getByText('上期无单投放事实未知，不能以缺失冒充零。')).toBeVisible();
  expect(screen.getByText(/数据未更新/)).toBeVisible();
  expect(screen.getByText(/本期 2026-09-27 至 2026-10-03/)).toBeVisible();
  expect(screen.getByLabelText('截至日（店铺当地日）')).toHaveAttribute('max', '2026-10-03');
  expect(screen.getByRole('region', { name: '完整分析范围统计' })).toHaveTextContent('101');
  expect(screen.getByText('TEST：上期统计不可用')).toBeVisible();
  expect(within(table).getByRole('link', { name: /本期盈利详情/ })).toHaveAttribute('href', `/tts${testItem.drilldown.pageUrl}`);
  expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
  expect(fetcher.mock.calls[1][0]).not.toContain('anchor_date');
});
it('retains draft input, rejects invalid exact investigation, and preserves server anchor across 3-day apply and paging', async () => {
  const fetcher = api(); render(<Page {...props} />); await screen.findByRole('table');
  fireEvent.change(screen.getByLabelText('精确 SPU 内部编号'), { target: { value: 'bad' } });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('最多 100 个正整数');
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText('精确 SPU 内部编号')).toHaveValue('bad');
  fireEvent.change(screen.getByLabelText('精确 SPU 内部编号'), { target: { value: '9007199254740993,22' } });
  fireEvent.change(screen.getByLabelText('比较窗口'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(fetcher.mock.calls[2][0]).toContain('window_days=3');
  expect(fetcher.mock.calls[2][0]).toContain('spu_ids=9007199254740993');
  expect(fetcher.mock.calls[2][0]).toContain('anchor_date=2026-10-03');
  await screen.findByRole('table');
  expect(screen.getByText(/3 天用于观察近期变化/)).toBeVisible();
  expect(screen.getByText(/本期 2026-10-01 至 2026-10-03/)).toBeVisible();
  expect(screen.getByRole('link', { name: /本期盈利详情/ })).toHaveAttribute('href', `/tts${testPayload3.items[0].drilldown.pageUrl}`);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(fetcher.mock.calls.at(-1)?.[0]).toContain('offset=100'));
});
it('opens keyboard-accessible details and returns focus without changing query', async () => {
  api(); render(<Page {...props} />); await screen.findByRole('table');
  const trigger = screen.getByRole('button', { name: /查看对比明细/ }); trigger.focus();
  const user = userEvent.setup(); await user.keyboard('{Enter}');
  const detail = screen.getByRole('region', { name: /SPU .* 对比明细/ });
  expect(detail).toHaveTextContent('计算时间');
  expect(detail).toHaveTextContent('9999999999999999.99');
  await user.keyboard('{Escape}'); expect(screen.queryByRole('region', { name: /SPU .* 对比明细/ })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
it('rejects unsupported URL filters without silently querying the whole shop', async () => {
  history.replaceState(null, '', '/?shop_pk=11&severity=critical');
  const fetcher = api(); render(<Page {...props} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('不支持的查询参数');
  expect(fetcher).not.toHaveBeenCalled();
});
it('clears old results on scope change and ignores late success after the new scope fails', async () => {
  let finish: (response: Response) => void = () => {};
  const fetcher = api(async (url) => url.includes('window_days=3') ? json({}, 503) : new Promise((resolve) => { finish = resolve; }));
  render(<Page {...props} />); await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByLabelText('比较窗口'), { target: { value: '3' } }); fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('数据不可用');
  await act(async () => { finish(json(testPayload)); });
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(fetcher.mock.calls[1][1]?.signal?.aborted).toBe(true);
});
it('distinguishes permission denial, shared 401 and empty results without exposing raw errors', async () => {
  const unauthorized = vi.fn(); const release = setUnauthorizedHandler(unauthorized);
  const fetcher = api(async () => json({ detail: 'SECRET_TRANSPORT' }, 403));
  render(<Page {...props} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('无权限');
  expect(screen.queryByText(/SECRET_TRANSPORT/)).not.toBeInTheDocument(); expect(unauthorized).not.toHaveBeenCalled();
  fetcher.mockImplementation(async () => json({}, 401)); fireEvent.click(screen.getByRole('button', { name: '刷新 / 重试' }));
  await waitFor(() => expect(unauthorized).toHaveBeenCalled()); release();
});
it('shows genuine empty results and exposes structured coverage facts, not generic JSON', async () => {
  api(async () => json({ ...testPayload, items: [], total: 0 })); render(<Page {...props} />);
  expect(await screen.findByText(/当前条件无匹配 SPU/)).toBeVisible();
  fireEvent.click(screen.getByText('数据覆盖依据与限制'));
  expect(screen.getByText('observed_sources_and_inferred_operational_cutoff')).toBeVisible();
  expect(screen.getByText('TEST_ADV / TEST_CAMPAIGN')).toBeVisible();
  expect(screen.getByText('TEST：不证明上游完整')).toBeVisible();
});
it('honors Retry-After with manual retry only after expiry, never auto-fetching', async () => {
  const fetcher = api(async () => json({}, 429, { 'Retry-After': '1', 'x-request-id': 'TEST_429' }));
  render(<Page {...props} />); expect(await screen.findByRole('alert')).toHaveTextContent('429');
  expect(screen.getByRole('button', { name: '刷新 / 重试' })).toBeDisabled();
  expect(screen.getByText(/TEST_429/)).toBeVisible();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新 / 重试' })).toBeEnabled(), { timeout: 2000 });
  expect(fetcher).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: '刷新 / 重试' })); await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
});
it('retains an invalid URL date as editable text without making any request', async () => {
  history.replaceState(null, '', '/?shop_pk=11&anchor_date=2026-02-30');
  const fetcher = api(); render(<Page {...props} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('截至日无效');
  expect(screen.getByLabelText('截至日（店铺当地日）')).toHaveValue('2026-02-30');
  expect(fetcher).not.toHaveBeenCalled();
});
it('uses only an allowlisted server date bound on 422 without clamping the requested date', async () => {
  history.replaceState(null, '', '/?shop_pk=11&anchor_date=2026-10-06');
  api(async () => json({ detail: { message: 'SECRET_SQL', latestAvailableDate: '2026-10-03' } }, 422));
  render(<Page {...props} />); expect(await screen.findByRole('alert')).toHaveTextContent('422');
  expect(screen.getByLabelText('截至日（店铺当地日）')).toHaveAttribute('max', '2026-10-03');
  expect(screen.getByLabelText('截至日（店铺当地日）')).toHaveValue('2026-10-06');
  expect(screen.queryByText(/SECRET_SQL/)).not.toBeInTheDocument();
});
it('keeps dirty draft input through manual refresh and restores applied scope on browser history', async () => {
  const fetcher = api(); render(<Page {...props} />); await screen.findByRole('table');
  fireEvent.change(screen.getByLabelText('精确 SPU 内部编号'), { target: { value: '22' } });
  fireEvent.click(screen.getByRole('button', { name: '刷新 / 重试' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3)); await screen.findByRole('table');
  expect(screen.getByLabelText('精确 SPU 内部编号')).toHaveValue('22');
  expect(fetcher.mock.calls[2][0]).not.toContain('spu_ids');
  expect(fetcher.mock.calls[2][0]).toContain('anchor_date=2026-10-03');
  history.replaceState(null, '', '/?shop_pk=11&window_days=3&activity=all&anchor_date=2026-10-02');
  fireEvent(window, new PopStateEvent('popstate'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  expect(screen.getByLabelText('比较窗口')).toHaveValue('3');
  expect(fetcher.mock.calls[3][0]).toContain('anchor_date=2026-10-02');
  expect(fetcher.mock.calls[3][0]).toContain('activity=all');
});
it('aborts requests on unmount and does not commit late data to history', async () => {
  let finish: (r: Response) => void = () => {};
  const fetcher = api(async () => new Promise((resolve) => { finish = resolve; }));
  const view = render(<Page {...props} />); await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  view.unmount(); expect(fetcher.mock.calls[1][1]?.signal?.aborted).toBe(true);
  await act(async () => { finish(json(testPayload)); }); expect(location.search).toBe('');
});
it('shows a safe structured request ID on unavailable data but never renders transport detail text', async () => {
  api(async () => json({ detail: { message: 'SECRET_SQL', requestId: 'TEST_BODY_REQUEST' } }, 503));
  render(<Page {...props} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('TEST_BODY_REQUEST');
  expect(screen.queryByText(/SECRET_SQL/)).not.toBeInTheDocument();
});
it('does not silently replace a deliberately cleared shop with the first shop', async () => {
  const fetcher = api(); render(<Page {...props} />); await screen.findByRole('table');
  fireEvent.change(screen.getByLabelText('店铺'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('请选择店铺');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it('does not shorten very long server cooldowns or auto-fetch on history changes or expiry', async () => {
  vi.useFakeTimers();
  const fetcher = api(async () => json({}, 429, { 'Retry-After': '2147484' }));
  await act(async () => { render(<Page {...props} />); });
  expect(screen.getByRole('button', { name: '刷新 / 重试' })).toBeDisabled();
  history.replaceState(null, '', '/?shop_pk=11&window_days=3');
  await act(async () => { window.dispatchEvent(new PopStateEvent('popstate')); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2_147_483_647); });
  expect(screen.getByRole('button', { name: '刷新 / 重试' })).toBeDisabled();
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole('button', { name: '刷新 / 重试' })).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it('does not turn missing ROI or totals into true zero or discard an unavailable row', async () => {
  api(async () => json({ ...testPayload, items: [{ ...testItem, currentRoi: null, currentRoiUnavailableReason: 'TEST：广告费缺失', currentNoOrderAdSpend: null }], totals: { analysisSpuCount: null, previousOrderCount: null, currentOrderCount: null, unavailableReason: 'TEST：汇总读取不可用' } }));
  render(<Page {...props} />); const table = await screen.findByRole('table');
  expect(within(table).getByText('TEST：广告费缺失')).toBeVisible();
  expect(within(table).getByText('-100.00')).toBeVisible();
  expect(within(table).getByText(testItem.spuId)).toBeVisible();
  expect(screen.getByRole('region', { name: '完整分析范围统计' })).toHaveTextContent('TEST：汇总读取不可用');
  expect(screen.getByRole('region', { name: '完整分析范围统计' })).not.toHaveTextContent('101');
});
