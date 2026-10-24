import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { FrontendBootstrap } from '../../shared/contracts';
import Login from './index';
import { leaveLogin } from './navigation';
vi.mock('./navigation', () => ({ leaveLogin: vi.fn() }));
const bootstrap: FrontendBootstrap = { rootPath: '/tts', pageId: 'login', navigation: [], releaseId: 'TEST', loginNextPath: '/tts/v2/pages/dashboard', roleCapabilities: { readwrite: false, admin: false } };
const identity = { username: 'test_user', displayName: 'TEST 用户', role: 'readonly', pages: ['dashboard'] };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
function mockApi(operation: (url: string, init: RequestInit) => Promise<Response>) { const fetcher = vi.fn(operation); vi.stubGlobal('fetch', fetcher); return fetcher; }
async function fill() {
  fireEvent.change(screen.getByLabelText('用户名', { exact: true }), { target: { value: 'TEST_User' } });
  fireEvent.change(screen.getByLabelText('密码', { exact: true }), { target: { value: 'TEST_password' } });
}
function submit() { fireEvent.submit(screen.getByRole('form', { name: '登录表单' })); }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.clearAllMocks(); });

describe('login content', () => {
  it.each(['', '/tts'])('does no initial probe and submits once with shared same-origin marker and exact canonical next at root %s', async (rootPath) => {
    let release!: (value: Response) => void;
    const fetcher = mockApi(() => new Promise((resolve) => { release = resolve; }));
    const next = `${rootPath}/v2/pages/dashboard?tab=TEST`;
    render(<Login bootstrap={{ ...bootstrap, rootPath, loginNextPath: next }} />); expect(fetcher).not.toHaveBeenCalled();
    await fill(); submit(); submit(); expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]; expect(url).toBe(`${rootPath}/v2/auth/login`);
    expect(init.credentials).toBe('same-origin'); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
    expect(JSON.parse(init.body as string)).toEqual({ username: 'TEST_User', password: 'TEST_password', next });
    await act(async () => release(json({ ok: true, ...identity })));
    expect(leaveLogin).toHaveBeenCalledExactlyOnceWith(next); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('');
  });
  it.each([undefined, '//evil.test', '/v2/pages/dashboard', '/tts/../evil', '/tts/%2e%2e/evil', '/tts/tts/v2/pages/dashboard'])('fails closed on absent or noncanonical bootstrap next %#', async (loginNextPath) => {
    const fetcher = mockApi(async () => json({ ok: true, ...identity })); render(<Login bootstrap={{ ...bootstrap, loginNextPath }} />);
    await fill(); submit(); expect(fetcher).not.toHaveBeenCalled(); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    expect(screen.getByText(/登录暂不可用/)).toBeVisible();
  });
  it('uses static non-enumerating 401 feedback and preserves editable inputs', async () => {
    const fetcher = mockApi(async () => json({ detail: 'TEST_RAW_PASSWORD_USER_MISSING' }, 401)); render(<Login bootstrap={bootstrap} />); await fill(); submit();
    expect(await screen.findByText('用户名或密码错误')).toBeVisible(); expect(document.body.textContent).not.toContain('TEST_RAW_PASSWORD');
    expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); expect(leaveLogin).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('密码', { exact: true }), { target: { value: 'TEST_edited' } }); submit();
    await act(async () => {}); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([null, { ok: true }, { ok: false, ...identity }, { ok: true, ...identity, username: 'other' }, { ok: true, ...identity, pages: null }])('does not navigate or discard password for malformed/mismatched success %#', async (value) => {
    const fetcher = mockApi(async () => json(value)); render(<Login bootstrap={bootstrap} />); await fill(); submit();
    await screen.findByText(/结果未确认/); expect(leaveLogin).not.toHaveBeenCalled(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    submit(); expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('login uncertainty and recovery', () => {
  it('keeps a full header cooldown through edits and expiry without automatic POST or GET', async () => {
    const fetcher = mockApi(async () => new Response(new ReadableStream({ start() {} }), { status: 429, headers: { 'Retry-After': '600' } }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit(); await act(async () => {});
    expect(screen.getByText(/HTTP 429/)).toBeVisible(); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('用户名', { exact: true }), { target: { value: 'TEST_changed' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(screen.getByRole('button', { name: '登录' })).toBeEnabled();
    expect(fetcher).toHaveBeenCalledTimes(1); view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['fetch', 'body'])('bounds stalled %s locally, retains the captured attempt despite edits, and never replays', async (kind) => {
    const fetcher = mockApi(async () => kind === 'fetch' ? new Promise<Response>(() => {}) : new Response(new ReadableStream({ start() {} }), { status: 200 }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit(); await act(async () => {});
    const signal = fetcher.mock.calls[0][1].signal!;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText(/结果未确认/)).toBeVisible(); expect(signal.aborted).toBe(true);
    fireEvent.change(screen.getByLabelText('用户名', { exact: true }), { target: { value: 'TEST_changed' } });
    expect(screen.getByText(/原请求用户名：TEST_User/)).toBeVisible(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    submit(); expect(fetcher).toHaveBeenCalledTimes(1); expect(leaveLogin).not.toHaveBeenCalled(); view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['same', 'different', 'missing'])('reads CURRENT cookie facts explicitly and needs a separate manual retry decision for %s identity', async (kind) => {
    let checks = 0; let writes = 0;
    const fetcher = mockApi(async (_url, init) => {
      if (init.method === 'GET') { checks++; return json(kind === 'missing' ? { authenticated: false } : { authenticated: true, ...identity, username: kind === 'same' ? 'test_user' : 'test_other' }); }
      writes++; return json({}, 503);
    });
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/);
    expect(screen.getByText(/服务暂不可用/)).toBeVisible(); expect(checks).toBe(0);
    fireEvent.change(screen.getByLabelText('用户名', { exact: true }), { target: { value: 'TEST_edited' } });
    const user = userEvent.setup(); await user.click(screen.getByRole('button', { name: '查询当前会话' }));
    await screen.findByText(/查询完成/); expect(checks).toBe(1); expect(writes).toBe(1); expect(leaveLogin).not.toHaveBeenCalled();
    expect(screen.getByText(/不能证明原密码正确/)).toBeVisible(); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    expect(fetcher.mock.calls[1][0]).toBe('/tts/v2/auth/me');
    await user.click(screen.getByRole('button', { name: '我已了解，允许手动重新登录' }));
    expect(screen.getByRole('button', { name: '登录' })).toBeEnabled(); expect(writes).toBe(1);
    // Reverification is a real GET and revokes the earlier retry decision.
    await user.click(screen.getByRole('button', { name: '查询当前会话' })); await screen.findByText(/查询完成/);
    expect(checks).toBe(2); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '我已了解，允许手动重新登录' })); submit();
    await act(async () => {}); expect(writes).toBe(2); expect(checks).toBe(2);
  });
  it('only uses a different confirmed current identity after an explicit choice; never claims original login success', async () => {
    mockApi(async (_url, init) => init.method === 'GET' ? json({ authenticated: true, ...identity, username: 'test_other', displayName: '<TEST name>' }) : json({}, 500));
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/);
    const user = userEvent.setup(); await user.click(screen.getByRole('button', { name: '查询当前会话' })); await screen.findByText(/查询完成/);
    expect(screen.getByText(/与原请求用户名不同/)).toBeVisible(); expect(screen.getByText('<TEST name>')).toBeVisible(); expect(leaveLogin).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '使用已查询的当前会话继续' }));
    expect(leaveLogin).toHaveBeenCalledExactlyOnceWith(bootstrap.loginNextPath); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('');
  });
  it.each(['denied', 'malformed', 'network'])('failed %s verification cannot authorize manual retry or navigation', async (kind) => {
    mockApi(async (_url, init) => {
      if (init.method !== 'GET') return json({}, 500);
      if (kind === 'network') throw new Error('TEST_UNTRUSTED');
      return kind === 'denied' ? new Response(new ReadableStream({ start() {} }), { status: 403 }) : json({ authenticated: true });
    });
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/);
    const user = userEvent.setup(); await user.click(screen.getByRole('button', { name: '查询当前会话' })); await screen.findByText(/查询未完成/);
    expect(screen.getByRole('button', { name: '登录' })).toBeDisabled(); expect(screen.queryByRole('button', { name: '我已了解，允许手动重新登录' })).toBeNull();
    expect(leaveLogin).not.toHaveBeenCalled(); expect(document.body.textContent).not.toContain('TEST_UNTRUSTED');
  });
  it('clears stale submitted field errors on genuine edits while retaining static feedback and the password', async () => {
    mockApi(async () => json({ detail: [{ loc: ['body', 'username'], type: 'value_error', msg: 'TEST_RAW', input: 'TEST_RAW' }] }, 422));
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/HTTP 422/);
    const username = screen.getByLabelText('用户名', { exact: true }); expect(username).toBeInvalid();
    fireEvent.change(username, { target: { value: 'TEST_fixed' } }); expect(username).toBeValid(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); expect(document.body.textContent).not.toContain('TEST_RAW');
  });
  it('supports native Enter submission and normal labelled input focus without a custom keyboard engine', async () => {
    mockApi(async () => json({}, 401)); render(<Login bootstrap={bootstrap} />);
    const user = userEvent.setup(); await user.tab(); expect(screen.getByLabelText('用户名', { exact: true })).toHaveFocus();
    await user.type(screen.getByLabelText('用户名', { exact: true }), 'TEST_user'); await user.tab();
    expect(screen.getByLabelText('密码', { exact: true })).toHaveFocus(); await user.keyboard('TEST_password{Enter}'); await screen.findByText('用户名或密码错误');
  });
});

describe('login request lifetime and read fences', () => {
  it('ignores a late successful login after its local wait ends', async () => {
    let release!: (response: Response) => void;
    const fetcher = mockApi(() => new Promise<Response>((resolve) => { release = resolve; }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); }); expect(screen.getByText(/结果未确认/)).toBeVisible();
    await act(async () => release(json({ ok: true, ...identity })));
    expect(leaveLogin).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('shares a full verification Retry-After fence across GET, manual decisions, edits and expiry without replay', async () => {
    let reads = 0; let writes = 0;
    mockApi(async (_url, init) => {
      if (init.method === 'GET') { reads++; return reads === 1 ? new Response(new ReadableStream({ start() {} }), { status: 429, headers: { 'Retry-After': '600' } }) : json({ authenticated: false }); }
      writes++; return json({}, 500);
    });
    const view = render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/);
    vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: '查询当前会话' })); await act(async () => {});
    expect(screen.getByText(/查询未完成/)).toBeVisible(); expect(reads).toBe(1);
    fireEvent.change(screen.getByLabelText('密码', { exact: true }), { target: { value: 'TEST_new' } });
    const check = screen.getByRole('button', { name: '查询当前会话' }); expect(check).toBeDisabled(); submit();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(check).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(check).toBeEnabled(); expect(reads).toBe(1); expect(writes).toBe(1);
    fireEvent.click(check); await act(async () => {}); expect(reads).toBe(2); expect(screen.getByRole('button', { name: '登录' })).toBeDisabled();
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('bounds a stalled verification body and aborts tracked work on unmount', async () => {
    let checkingSignal: AbortSignal | null | undefined;
    mockApi(async (_url, init) => {
      if (init.method === 'GET') { checkingSignal = init.signal; return new Response(new ReadableStream({ start() {} }), { status: 200 }); }
      return json({}, 500);
    });
    const view = render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/);
    vi.useFakeTimers(); fireEvent.click(screen.getByRole('button', { name: '查询当前会话' })); await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); }); expect(checkingSignal?.aborted).toBe(true); expect(screen.getByText(/查询未完成/)).toBeVisible();
    expect(screen.getByRole('button', { name: '登录' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '查询当前会话' })); await act(async () => {});
    expect(checkingSignal?.aborted).toBe(false); view.unmount(); await act(async () => {}); expect(checkingSignal?.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
  });
  it('aborts an outstanding login on unmount without navigation', async () => {
    const fetcher = mockApi(() => new Promise<Response>(() => {}));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit();
    const signal = fetcher.mock.calls[0][1].signal!; view.unmount(); await act(async () => {});
    expect(signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0); expect(leaveLogin).not.toHaveBeenCalled();
  });
  it.each([403, 429])('handles HTTP %s denial from headers without waiting for an untrusted body', async (status) => {
    mockApi(async () => new Response(new ReadableStream({ start() {} }), { status, headers: { 'Retry-After': '10' } }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(new RegExp(`HTTP ${status}`));
    expect(screen.queryByRole('button', { name: '查询当前会话' })).toBeNull(); expect(leaveLogin).not.toHaveBeenCalled(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); view.unmount();
  });
  it.each([201, 204])('never treats noncontractual HTTP %s as confirmed login', async (status) => {
    mockApi(async () => status === 204 ? new Response(null, { status }) : json({ ok: true, ...identity }, status));
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText(/结果未确认/); expect(leaveLogin).not.toHaveBeenCalled();
  });
});
