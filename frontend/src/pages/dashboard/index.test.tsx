import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import Dashboard from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'dashboard', releaseId: 'TEST', navigation: [
  { id: 'shops', label: '店铺注册', group: 'TEST', href: '/v2/pages/shops' },
  { id: 'spu-roi', label: 'SPU ROI 看板', group: 'TEST', href: '/tts/v2/pages/spu-roi' },
  { id: 'ad-daily', label: '广告日明细', group: 'TEST', href: '/v2/pages/ad-daily' },
] } };
const shop = { id: 1, platform: 'tiktok', shop_id: '90071992547409931234567', account_name: 'TEST Shop', region: 'VN' };
const job = { job_name: 'TEST_job', interval_seconds: 3600, last_run_at: '2026-10-23T08:00:00Z',
  last_finished_at: null, last_status: 'failed', last_error: 'TEST upstream unavailable', next_expected_at: '2026-10-23T09:00:00Z',
  lag_seconds: 10, cycles_late: 0.25, severity: 'crit' };
function json(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), { status, headers });
}
function responses(url: string) {
  if (url.includes('channel-accounts')) return json([shop], 200, { 'X-Total-Count': '812' });
  if (url.endsWith('/coverage')) return json({ missing_cost_spus: 0, total_spus: 4299 });
  if (url.endsWith('/healthz')) return json({ status: 'ok', service: 'tts-erp-v2', auth_mode: 'enforce' });
  if (url.endsWith('/sync/status')) return json({ server_time: '2026-10-23T10:00:00Z', jobs: [job] });
  throw new Error(`Unexpected request ${url}`);
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('dashboard page', () => {
  it.each([120, 600, 3_000_000])('blocks retries and sync polling for the full Retry-After of %s seconds', async (seconds) => {
    vi.useFakeTimers();
    let polls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (!url.endsWith('/sync/status')) return responses(url);
      if (++polls === 1) return json({ detail: 'TEST throttled' }, 429, { 'Retry-After': String(seconds) });
      return responses(url);
    }));
    const view = render(<Dashboard {...props} />);
    await act(async () => {});
    expect(screen.getByRole('button', { name: '重试同步状态' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(seconds * 500); });
    expect(polls).toBe(1);
    expect(screen.getByRole('button', { name: '重试同步状态' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(seconds * 500); });
    expect(polls).toBe(2);
    expect(screen.getByText('TEST_job')).toBeVisible();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(180_000); });
    expect(polls).toBe(2);
  });
  it('refreshes sync every 60 seconds, ignores an older poll and stops on unmount', async () => {
    vi.useFakeTimers();
    let releaseOld!: (value: Response) => void;
    const oldPoll = new Promise<Response>((resolve) => { releaseOld = resolve; });
    let polls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (!url.endsWith('/sync/status')) return responses(url);
      polls++;
      if (polls === 2) return oldPoll;
      return json({ server_time: '2026-10-23T10:00:00Z', jobs: [{ ...job, job_name: polls === 1 ? 'TEST_initial' : 'TEST_current' }] });
    });
    vi.stubGlobal('fetch', fetchMock);
    const view = render(<Dashboard {...props} />);
    await act(async () => {});
    expect(screen.getByText('TEST_initial')).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(polls).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByText('TEST_current')).toBeVisible();
    await act(async () => releaseOld(json({ server_time: '2026-10-23T09:00:00Z', jobs: [{ ...job, job_name: 'TEST_stale' }] })));
    expect(screen.queryByText('TEST_stale')).not.toBeInTheDocument();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(polls).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });
  it.each([null, '', 'invalid'])('does not infer shop total from a page when X-Total-Count is %s', async (header) => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('channel-accounts')
      ? json([shop], 200, header === null ? {} : { 'X-Total-Count': header })
      : url.endsWith('/coverage') ? json({ missing_cost_spus: null }) : responses(url)));
    render(<Dashboard {...props} />);
    await screen.findByText(shop.shop_id);
    expect(screen.getByRole('region', { name: '已注册店铺总数' })).toHaveTextContent('—');
    expect(screen.getByRole('region', { name: 'SPU 总数' })).toHaveTextContent('—');
    expect(screen.getByRole('region', { name: '待录成本' })).toHaveTextContent('—');
    expect(screen.getByText('全店铺（总数未知）')).toBeVisible();
  });
  it('keeps coverage/shops failures distinct from empty and permits independent retry', async () => {
    let retry = false;
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes('channel-accounts')) return retry ? json([], 200, { 'X-Total-Count': '0' }) : json({ detail: 'TEST shops denied' }, 403);
      if (url.endsWith('/coverage')) return retry ? json({ total_spus: 0, missing_cost_spus: 0 }) : json({ detail: 'TEST coverage unavailable' }, 503);
      return responses(url);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<Dashboard {...props} />);
    expect(await screen.findByText(/TEST shops denied.*403/)).toBeVisible();
    expect(screen.getByText(/TEST coverage unavailable.*503/)).toBeVisible();
    expect(screen.queryByText('暂无店铺')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'SPU 总数' })).toHaveTextContent('—');
    expect(screen.getByText('API 正常')).toBeVisible();
    retry = true;
    await user.click(screen.getByRole('button', { name: '重试店铺' }));
    await user.click(screen.getByRole('button', { name: '重试数据摘要' }));
    expect(await screen.findByText('暂无店铺')).toBeVisible();
    expect(screen.getByText('无店铺')).toBeVisible();
    expect(screen.getByRole('region', { name: 'SPU 总数' })).toHaveTextContent('0');
  });
  it('reports health/sync errors without fabricating healthy or zero, then handles empty jobs on retry', async () => {
    let retry = false;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/healthz')) return retry ? responses(url) : json({ detail: 'TEST health error' }, 500);
      if (url.endsWith('/sync/status')) return retry ? json({ server_time: '2026-10-23T10:00:00Z', jobs: [] }) : json({ detail: 'TEST sync limit' }, 429);
      return responses(url);
    }));
    const user = userEvent.setup();
    render(<Dashboard {...props} />);
    expect(await screen.findByText(/TEST health error.*500/)).toBeVisible();
    expect(screen.getByText(/TEST sync limit.*429/)).toBeVisible();
    expect(screen.queryByText('API 正常')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无周期作业')).not.toBeInTheDocument();
    retry = true;
    await user.click(screen.getByRole('button', { name: '重试API' }));
    await user.click(screen.getByRole('button', { name: '重试同步状态' }));
    expect(await screen.findByText('暂无周期作业')).toBeVisible();
    expect(screen.getByText('API 正常')).toBeVisible();
  });
  it('ignores a previous rootPath response and supports direct unprefixed URLs', async () => {
    let release!: (value: Response) => void;
    const stale = new Promise<Response>((resolve) => { release = resolve; });
    const fetchMock = vi.fn(async (url: string) => url.startsWith('/tts/') ? stale : responses(url));
    vi.stubGlobal('fetch', fetchMock);
    const view = render(<Dashboard {...props} />);
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    view.rerender(<Dashboard bootstrap={{ ...props.bootstrap, rootPath: '', navigation: [] }} />);
    await screen.findByText(shop.shop_id);
    await act(async () => release(json({}))) ;
    expect(screen.getByRole('region', { name: 'SPU 总数' })).toHaveTextContent('4299');
    expect(screen.queryByText('覆盖率响应格式错误')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url === '/v2/reporting/coverage')).toBe(true);
  });
  it('renders authorized links, header shop total, backend coverage and honest health/sync using GET only', async () => {
    const fetchMock = vi.fn(async (url: string) => responses(url));
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard {...props} />);
    expect(await screen.findByText(shop.shop_id)).toBeVisible();
    expect(screen.getByText('全店铺 (812)')).toBeVisible();
    expect(screen.getByRole('region', { name: '已注册店铺总数' })).toHaveTextContent('812');
    expect(screen.getByRole('region', { name: 'SPU 总数' })).toHaveTextContent('4299');
    expect(screen.getByRole('region', { name: '待录成本' })).toHaveTextContent('0');
    expect(screen.getByRole('link', { name: '广告日明细' })).toHaveAttribute('href', '/tts/v2/pages/ad-daily');
    for (const link of screen.getAllByRole('link')) expect(link.getAttribute('href')).toMatch(/^\/tts\/v2\/pages\/(shops|spu-roi|ad-daily)$/);
    expect(screen.queryByRole('link', { name: /运行配置|采购工作台/ })).not.toBeInTheDocument();
    expect(screen.getByText('API 正常')).toBeVisible();
    expect(screen.getByText('auth: enforce')).toBeVisible();
    expect(screen.getAllByText('健康状态未知（接口未提供）')).toHaveLength(3);
    const sync = screen.getByRole('grid', { name: '数据同步状态' });
    expect(within(sync).getByText('红灯：落后 ≥2 个周期')).toBeVisible();
    expect(within(sync).getByText('失败')).toBeVisible();
    expect(within(sync).getByText('TEST upstream unavailable')).toBeVisible();
    expect(within(sync).getByText(/0.3 周期/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [url, init] of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect(url).toMatch(/^\/tts\//);
      expect(init.method).toBe('GET');
      expect(init.credentials).toBe('same-origin');
    }
  });
});
