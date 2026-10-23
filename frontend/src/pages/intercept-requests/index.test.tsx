import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import InterceptRequests from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'intercept-requests', navigation: [], releaseId: 'TEST' } };
const requestId = '9007199254740993123456789?part=TEST';
const row = { request_id: requestId, method: 'GET', endpoint_host: 'seller.example', endpoint_path: '/orders',
  captured_at: '2026-10-23T10:00:00Z', response_status: 200, duration_ms: 0, is_whitelisted: false };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('intercept-requests page', () => {
  it('opens detail from a row action and restores keyboard focus after Escape', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/requests?')
      ? json({ requests: [row], total: 1, limit: 100, offset: 0 }) : json({ request: row })));
    const user = userEvent.setup();
    render(<InterceptRequests {...props} />);
    const host = await screen.findByText('seller.example');
    await user.click(host);
    expect(await screen.findByRole('dialog')).toBeVisible();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const button = screen.getByRole('button', { name: '查看详情' });
    await user.click(button);
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(button).toHaveFocus());
  });
  it.each([2, 600, 3_000_000])('respects Retry-After %s independently for list/detail without automatic replay', async (seconds) => {
    vi.useFakeTimers();
    const limited = () => new Response(JSON.stringify({ detail: 'TEST rate limited' }), { status: 429, headers: { 'Retry-After': String(seconds) } });
    const fetchMock = vi.fn().mockResolvedValueOnce(limited())
      .mockResolvedValueOnce(json({ requests: [row], total: 1, limit: 100, offset: 0 }))
      .mockResolvedValueOnce(limited()).mockResolvedValueOnce(json({ request: row }));
    vi.stubGlobal('fetch', fetchMock);
    const view = render(<InterceptRequests {...props} />);
    await act(async () => {});
    expect(screen.getByRole('button', { name: '重试列表' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '查询' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(seconds * 1000 - 1); });
    expect(screen.getByRole('button', { name: '查询' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '查询' })).toBeEnabled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试列表' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看详情' })); });
    expect(screen.getByRole('button', { name: '重试详情' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(seconds * 1000 - 1); });
    expect(screen.getByRole('button', { name: '重试详情' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试详情' })); });
    expect(within(screen.getByRole('dialog')).getByText(requestId)).toBeVisible();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
  it.each([401, 403, 429, 503])('keeps HTTP %s distinct from empty results and supports an explicit GET retry', async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ detail: 'TEST access failure' }, status))
      .mockResolvedValueOnce(json({ requests: [], total: 0, limit: 100, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<InterceptRequests {...props} />);
    expect(await screen.findByText(new RegExp(`TEST access failure.*${status}`))).toBeVisible();
    expect(screen.queryByText('暂无记录')).not.toBeInTheDocument();
    expect(screen.getByText('共 — 条')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '重试列表' }));
    expect(await screen.findByText('暂无记录')).toBeVisible();
    expect(screen.getByText('共 0 条')).toBeVisible();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('rejects a missing list envelope instead of reporting zero records', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({})));
    render(<InterceptRequests {...props} />);
    expect(await screen.findByText('拦截记录响应格式错误')).toBeVisible();
    expect(screen.queryByText('共 0 条')).not.toBeInTheDocument();
  });
  it('handles detail failure/retry, loading and late detail after closing and selecting another request', async () => {
    const late = deferred();
    let firstAttempts = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/requests?')) return Promise.resolve(json({ requests: [row, { ...row, request_id: 'TEST_second', endpoint_host: 'second.example' }], total: 2, limit: 100, offset: 0 }));
      if (url.endsWith('TEST_second')) return Promise.resolve(json({ request: { ...row, request_id: 'TEST_second' } }));
      if (firstAttempts++ === 0) return Promise.resolve(json({ detail: 'TEST detail unavailable' }, 404));
      return late.promise;
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<InterceptRequests {...props} />);
    await screen.findByText('seller.example');
    await user.click(screen.getAllByRole('button', { name: '查看详情' })[0]);
    expect(await screen.findByText(/TEST detail unavailable.*404/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: '重试详情' }));
    expect(screen.getByRole('status')).toHaveTextContent('加载详情');
    await user.click(screen.getByRole('button', { name: '关闭详情' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getAllByRole('button', { name: '查看详情' })[1]);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('TEST_second')).toBeVisible();
    await act(async () => late.resolve(json({ request: { ...row, error_message: 'TEST stale detail' } })));
    expect(within(dialog).queryByText('TEST stale detail')).not.toBeInTheDocument();
    expect(within(dialog).getByText('TEST_second')).toBeVisible();
  });
  it('applies all seven filters, resets pagination and ignores a superseded list response', async () => {
    const old = deferred();
    const fetchMock = vi.fn((url: string) => url.includes('endpoint_host=old') ? old.promise
      : Promise.resolve(json({ requests: [row], total: 201, limit: 100, offset: 0 })));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<InterceptRequests {...props} />);
    await screen.findByText('seller.example');
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('offset=100'));
    await user.type(screen.getByRole('textbox', { name: '域名' }), 'old');
    await user.click(screen.getByRole('button', { name: '查询' }));
    expect(screen.getByRole('status')).toHaveTextContent('加载中');
    await user.clear(screen.getByRole('textbox', { name: '域名' }));
    await user.type(screen.getByRole('textbox', { name: '域名' }), 'new.example');
    await user.type(screen.getByRole('textbox', { name: '路径' }), '/api/orders');
    await user.type(screen.getByRole('textbox', { name: '状态码' }), '404');
    await user.click(screen.getByRole('button', { name: /白名单/ }));
    await user.click(screen.getByRole('option', { name: '否' }));
    await user.click(screen.getByRole('button', { name: /方法/ }));
    await user.click(screen.getByRole('option', { name: 'POST' }));
    fireEvent.change(screen.getByLabelText('起始'), { target: { value: '2026-10-01' } });
    fireEvent.change(screen.getByLabelText('截止'), { target: { value: '2026-10-23' } });
    await user.click(screen.getByRole('button', { name: '查询' }));
    await screen.findByText('seller.example');
    const query = new URL(fetchMock.mock.calls.at(-1)![0], 'https://test.invalid').searchParams;
    expect(Object.fromEntries(query)).toEqual({ endpoint_host: 'new.example', endpoint_path: '/api/orders', status: '404',
      is_whitelisted: 'false', method: 'POST', from: '2026-10-01', to: '2026-10-23', limit: '100', offset: '0' });
    await act(async () => old.resolve(json({ requests: [{ ...row, endpoint_host: 'stale.example' }], total: 999 })));
    expect(screen.queryByText('stale.example')).not.toBeInTheDocument();
    expect(screen.getByText('共 201 条')).toBeVisible();
  });
  it('lists backend totals, pages and reads safe detail by the exact string request_id using GET', async () => {
    const fetchMock = vi.fn(async (url: string) => url.includes('/requests?')
      ? json({ requests: [row], total: 201, limit: 100, offset: 0 })
      : json({ request: { ...row, url: 'https://seller.example/orders', request_body: false,
        response_body: { literal: '<img src=x onerror=alert(1)>' }, request_headers: { accept: 'json' } } }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<InterceptRequests {...props} />);
    expect(await screen.findByText('seller.example')).toBeVisible();
    expect(screen.getByText('共 201 条')).toBeVisible();
    expect(screen.getByText('0ms')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('offset=100'))).toBe(true));
    await user.click(screen.getByRole('button', { name: '查看详情' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(requestId)).toBeVisible();
    expect(within(dialog).getByText(/<img src=x onerror=alert\(1\)>/)).toBeVisible();
    expect(dialog.querySelector('img')).toBeNull();
    expect(within(dialog).getByText('false')).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => url === `/tts/v2/intercept/requests/${encodeURIComponent(requestId)}`)).toBe(true);
    await user.click(within(dialog).getByRole('button', { name: '关闭详情' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    for (const [, init] of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect(init.method).toBe('GET');
      expect(init.credentials).toBe('same-origin');
    }
  });
});
