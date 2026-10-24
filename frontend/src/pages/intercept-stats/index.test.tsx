import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { pageLoader } from '../../app/page-loader';
import type { PageProps } from '../../shared/contracts';

// Component tests inspect the wrapper input only; native canvas is a separate browser gate.
vi.mock('uplot-react', () => ({ default: ({ data }: { data: number[][] }) => <div data-testid="TEST_plot_data">{JSON.stringify(data)}</div> }));
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'intercept-stats', navigation: [], releaseId: 'TEST' } };
const stats = { total_requests: 1537, whitelisted_requests: 1001, today_requests: 11, error_requests: 7,
  by_host: [{ label: 'TEST_<script>.example', count: 1537, percentage: '99.7' }],
  by_method: [{ label: 'GET', count: 1537, percentage: '88.8' }],
  by_status: [{ label: '未知', count: 1537, percentage: '77.7' }],
  daily: [{ date: '2026-10-23', count: 12 }, { date: '2026-10-24', count: 0 }] };
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
async function mount() { const { default: Stats } = await pageLoader('intercept-stats')(); return { Stats, view: render(<Stats {...props} />) }; }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('readonly stats content', () => {
  it('shows all backend metrics/percentages and UTC dates with GET only, without an extra session', async () => {
    const fetcher = vi.fn().mockResolvedValue(json(stats)); vi.stubGlobal('fetch', fetcher);
    await mount();
    expect(await screen.findByRole('region', { name: '总请求数' })).toHaveTextContent('1,537');
    expect(screen.getByRole('region', { name: '白名单命中' })).toHaveTextContent('1,001');
    expect(screen.getByRole('region', { name: '今日请求（UTC）' })).toHaveTextContent('11');
    expect(screen.getByRole('region', { name: '错误请求' })).toHaveTextContent('7');
    expect(screen.getByText('99.7%')).toBeVisible(); expect(screen.getByText('88.8%')).toBeVisible(); expect(screen.getByText('77.7%')).toBeVisible();
    expect(screen.getByText('TEST_<script>.example')).toBeVisible(); expect(document.querySelector('script')).toBeNull();
    const table = screen.getByRole('grid', { name: '每日请求（UTC）' });
    expect(within(table).getByText('2026-10-24')).toBeVisible(); expect(within(table).getByText('0')).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/intercept/requests/stats?days=7');
    expect(fetcher.mock.calls[0][1].method ?? 'GET').toBe('GET');
  });
  it.each([null, {}, { ...stats, error_requests: null }, { ...stats, by_host: [{ label: 'TEST', count: -1, percentage: '20' }] }, { ...stats, daily: [{ date: '2026-02-31', count: 1 }] }])('rejects malformed data rather than showing zero or stale success %#', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(payload)));
    await mount(); expect(await screen.findByText('统计响应格式错误')).toBeVisible();
    expect(screen.queryByRole('region', { name: '总请求数' })).toBeNull();
  });
  it('distinguishes empty daily/distributions from errors, retaining genuine zero metrics', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...stats, total_requests: 0, daily: [], by_host: [], by_method: [], by_status: [] })));
    await mount(); expect(await screen.findByText('暂无每日数据')).toBeVisible();
    expect(screen.getByText('暂无域名数据')).toBeVisible(); expect(screen.getByRole('region', { name: '总请求数' })).toHaveTextContent('0');
    expect(screen.queryByTestId('TEST_plot_data')).toBeNull();
  });
  it('keeps UTC query values explicit and allows independent from/to defaults', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(json(stats))); vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup(); await mount(); await screen.findByText('99.7%');
    await user.clear(screen.getByRole('spinbutton', { name: '最近天数' })); await user.type(screen.getByRole('spinbutton', { name: '最近天数' }), '31');
    const start = screen.getByLabelText('起始（UTC）');
    // jsdom does not provide datetime-local keyboard editing.
    const { fireEvent } = await import('@testing-library/react'); fireEvent.change(start, { target: { value: '2026-10-01T00:00' } });
    await user.click(screen.getByRole('button', { name: '查询统计' })); await screen.findByText('99.7%');
    const url = new URL(fetcher.mock.calls.at(-1)![0], 'https://TEST.example');
    expect(url.searchParams.get('days')).toBe('31'); expect(url.searchParams.get('from')).toBe('2026-10-01T00:00Z'); expect(url.searchParams.has('to')).toBe(false);
  });
  it('aborts obsolete reads and never lets an old root replace current data', async () => {
    let resolve!: (r: Response) => void; let oldSignal: AbortSignal | undefined;
    vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit) => url.startsWith('/tts/') ? new Promise<Response>((r) => { resolve = r; oldSignal = init.signal as AbortSignal; }) : Promise.resolve(json(stats))));
    const { Stats, view } = await mount(); view.rerender(<Stats bootstrap={{ ...props.bootstrap, rootPath: '' }} />);
    expect(oldSignal?.aborted).toBe(true); await screen.findByText('99.7%');
    await act(async () => { resolve(json({ ...stats, total_requests: 666 })); });
    expect(screen.getByRole('region', { name: '总请求数' })).toHaveTextContent('1,537');
  });
  it('honors full cooldown, retries only explicitly and clears timers on unmount', async () => {
    vi.useFakeTimers(); const fetcher = vi.fn().mockResolvedValue(json({ detail: 'TEST limited' }, 429, { 'Retry-After': '600' })); vi.stubGlobal('fetch', fetcher);
    const { view } = await mount(); await act(async () => {});
    expect(screen.getByRole('button', { name: '重试统计' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599_000); }); expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(screen.getByRole('button', { name: '重试统计' })).toBeEnabled(); expect(fetcher).toHaveBeenCalledTimes(1);
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
});
