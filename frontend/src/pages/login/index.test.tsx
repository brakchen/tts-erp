import { act, fireEvent, render, screen, within } from '@testing-library/react';
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
function loginButton() { return screen.getByRole('button', { name: '登录' }); }
function assertNoRecovery() {
  expect(screen.queryByRole('button', { name: '查询当前会话' })).toBeNull();
  expect(screen.queryByRole('button', { name: '我已了解，允许手动重新登录' })).toBeNull();
  expect(screen.queryByRole('button', { name: '使用已查询的当前会话继续' })).toBeNull();
  expect(screen.queryByRole('region', { name: '登录结果核对' })).toBeNull();
  expect(document.body.textContent).not.toContain('结果未确认');
  expect(document.body.textContent).not.toContain('可能已生效');
  expect(document.body.textContent).not.toContain('当前账号无法登录');
}
function assertAlertAboveButton(message: string) {
  const form = screen.getByRole('form', { name: '登录表单' });
  const title = within(form).getByText('登录未完成');
  const description = within(form).getByText(message);
  const button = within(form).getByRole('button', { name: '登录' });
  const root = title.closest('.alert') ?? title.parentElement ?? title;
  expect(description).toBeVisible();
  expect(root.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.clearAllMocks(); });

describe('login content', () => {
  it('toggles password visibility without submitting, changing the draft or probing the session', async () => {
    const fetcher = mockApi(async () => json({ ok: true, ...identity }));
    render(<Login bootstrap={bootstrap} />);
    await fill();
    const password = screen.getByLabelText('密码', { exact: true });
    const user = userEvent.setup();
    expect(password).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: '显示密码' }));
    expect(password).toHaveAttribute('type', 'text');
    expect(password).toHaveValue('TEST_password');
    expect(screen.getByRole('button', { name: '隐藏密码' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: '隐藏密码' }));
    expect(password).toHaveAttribute('type', 'password');
    expect(password).toHaveValue('TEST_password');
    expect(fetcher).not.toHaveBeenCalled();
    expect(leaveLogin).not.toHaveBeenCalled();
  });
  it.each(['', '/tts'])('does no initial probe and submits once with shared same-origin marker and exact canonical next at root %s', async (rootPath) => {
    let release!: (value: Response) => void;
    const fetcher = mockApi(() => new Promise((resolve) => { release = resolve; }));
    const next = `${rootPath}/v2/pages/dashboard?tab=TEST`;
    render(<Login bootstrap={{ ...bootstrap, rootPath, loginNextPath: next }} />); expect(fetcher).not.toHaveBeenCalled();
    await fill(); submit(); submit(); expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]; expect(url).toBe(`${rootPath}/v2/auth/login`);
    expect(init.credentials).toBe('same-origin'); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
    expect(JSON.parse(init.body as string)).toEqual({ username: 'TEST_User', password: 'TEST_password', next });
    expect(screen.getByRole('button', { name: '登录中…' })).toBeDisabled();
    await act(async () => release(json({ ok: true, ...identity })));
    expect(leaveLogin).toHaveBeenCalledExactlyOnceWith(next); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('');
  });
  it.each([undefined, '//evil.test', '/v2/pages/dashboard', '/tts/../evil', '/tts/%2e%2e/evil', '/tts/tts/v2/pages/dashboard'])('fails closed on absent or noncanonical bootstrap next %#', async (loginNextPath) => {
    const fetcher = mockApi(async () => json({ ok: true, ...identity })); render(<Login bootstrap={{ ...bootstrap, loginNextPath }} />);
    await fill(); submit(); expect(fetcher).not.toHaveBeenCalled(); expect(loginButton()).toBeDisabled();
    expect(screen.getByText(/登录暂不可用/)).toBeVisible();
  });
  it('uses static non-enumerating 401 feedback, preserves the username, and keeps the button usable', async () => {
    const fetcher = mockApi(async () => json({ detail: 'TEST_RAW_PASSWORD_USER_MISSING' }, 401)); render(<Login bootstrap={bootstrap} />); await fill(); submit();
    expect(await screen.findByText('用户名或密码错误')).toBeVisible(); expect(document.body.textContent).not.toContain('TEST_RAW_PASSWORD');
    expect(document.body.textContent).not.toContain('当前账号无法登录');
    expect(screen.getByLabelText('用户名', { exact: true })).toHaveValue('TEST_User');
    expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); expect(leaveLogin).not.toHaveBeenCalled();
    assertAlertAboveButton('用户名或密码错误'); assertNoRecovery(); expect(loginButton()).toBeEnabled();
    fireEvent.change(screen.getByLabelText('密码', { exact: true }), { target: { value: 'TEST_edited' } }); submit();
    await act(async () => {}); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([null, { ok: true }, { ok: false, ...identity }, { ok: true, ...identity, username: 'other' }, { ok: true, ...identity, pages: null }])('does not navigate or discard password for malformed/mismatched success %#', async (value) => {
    const fetcher = mockApi(async () => json(value)); render(<Login bootstrap={bootstrap} />); await fill(); submit();
    await screen.findByText('登录服务暂不可用，请稍后重试'); expect(leaveLogin).not.toHaveBeenCalled(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    assertNoRecovery(); expect(loginButton()).toBeEnabled();
    submit(); expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe('login determined failures', () => {
  it.each([500, 502, 503, 504])('shows the login-unavailable copy for HTTP %s with a usable button and no recovery UI', async (status) => {
    mockApi(async () => json({ detail: 'auth store unavailable: OperationalError' }, status));
    render(<Login bootstrap={bootstrap} />); await fill(); submit();
    expect(await screen.findByText('登录服务暂不可用，请稍后重试')).toBeVisible();
    expect(document.body.textContent).not.toContain('OperationalError');
    expect(document.body.textContent).not.toContain('HTTP');
    assertAlertAboveButton('登录服务暂不可用，请稍后重试');
    assertNoRecovery();
    expect(loginButton()).toBeEnabled();
    expect(leaveLogin).not.toHaveBeenCalled();
  });
  it('keeps a full header cooldown through edits and expiry without automatic POST', async () => {
    const fetcher = mockApi(async () => new Response(new ReadableStream({ start() {} }), { status: 429, headers: { 'Retry-After': '600' } }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit(); await act(async () => {});
    expect(screen.getByText('登录过于频繁，请 600 秒后再试')).toBeVisible();
    expect(loginButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('用户名', { exact: true }), { target: { value: 'TEST_changed' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText('登录过于频繁，请 599 秒后再试')).toBeVisible();
    expect(loginButton()).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(598000); }); expect(loginButton()).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(loginButton()).toBeEnabled();
    expect(screen.queryByText(/请 0 秒后再试/)).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1); assertNoRecovery(); view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('keeps the login button enabled when 429 has no Retry-After', async () => {
    mockApi(async () => json({ detail: 'too many login attempts' }, 429));
    render(<Login bootstrap={bootstrap} />); await fill(); submit();
    expect(await screen.findByText('登录过于频繁，请稍后再试')).toBeVisible();
    expect(document.body.textContent).not.toContain('too many login attempts');
    expect(loginButton()).toBeEnabled(); assertNoRecovery();
  });
  it('maps a dropped network request without querying the session', async () => {
    const fetcher = mockApi(async () => { throw new TypeError('Failed to fetch'); });
    render(<Login bootstrap={bootstrap} />); await fill(); submit();
    expect(await screen.findByText('网络已断开，请检查网络后重试')).toBeVisible();
    assertAlertAboveButton('网络已断开，请检查网络后重试');
    expect(loginButton()).toBeEnabled(); assertNoRecovery();
    expect(fetcher.mock.calls.every(([url]) => !String(url).endsWith('/v2/auth/me'))).toBe(true);
  });
  it.each(['fetch', 'body'])('bounds stalled %s locally as a timeout, keeps inputs, and allows a later manual retry', async (kind) => {
    const fetcher = mockApi(async () => kind === 'fetch' ? new Promise<Response>(() => {}) : new Response(new ReadableStream({ start() {} }), { status: 200 }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit(); await act(async () => {});
    const signal = fetcher.mock.calls[0][1].signal!;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText('请求超时，请稍后重试')).toBeVisible(); expect(signal.aborted).toBe(true);
    expect(screen.getByLabelText('用户名', { exact: true })).toHaveValue('TEST_User');
    expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    assertNoRecovery(); expect(loginButton()).toBeEnabled(); expect(leaveLogin).not.toHaveBeenCalled();
    submit(); await act(async () => {}); expect(fetcher).toHaveBeenCalledTimes(2);
    view.unmount(); await act(async () => {}); expect(vi.getTimerCount()).toBe(0);
  });
  it('clears stale submitted field errors on genuine edits while retaining static feedback and the password', async () => {
    mockApi(async () => json({ detail: [{ loc: ['body', 'username'], type: 'value_error', msg: 'TEST_RAW', input: 'TEST_RAW' }] }, 422));
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText('请检查标红的字段');
    const username = screen.getByLabelText('用户名', { exact: true }); expect(username).toBeInvalid();
    expect(screen.getByText('用户名：格式不符合要求')).toBeVisible();
    assertAlertAboveButton('请检查标红的字段');
    fireEvent.change(username, { target: { value: 'TEST_fixed' } }); expect(username).toBeValid(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); expect(document.body.textContent).not.toContain('TEST_RAW');
    expect(loginButton()).toBeEnabled();
  });
  it('supports native Enter submission and normal labelled input focus without a custom keyboard engine', async () => {
    mockApi(async () => json({}, 401)); render(<Login bootstrap={bootstrap} />);
    const user = userEvent.setup(); await user.tab(); expect(screen.getByLabelText('用户名', { exact: true })).toHaveFocus();
    await user.type(screen.getByLabelText('用户名', { exact: true }), 'TEST_user'); await user.tab();
    expect(screen.getByLabelText('密码', { exact: true })).toHaveFocus(); await user.keyboard('TEST_password{Enter}'); await screen.findByText('用户名或密码错误');
    expect(loginButton()).toBeEnabled();
  });
});

describe('login request lifetime and read fences', () => {
  it('ignores a late successful login after its local wait ends', async () => {
    let release!: (response: Response) => void;
    const fetcher = mockApi(() => new Promise<Response>((resolve) => { release = resolve; }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); }); expect(screen.getByText('请求超时，请稍后重试')).toBeVisible();
    await act(async () => release(json({ ok: true, ...identity })));
    expect(leaveLogin).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password');
    expect(loginButton()).toBeEnabled(); view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('aborts an outstanding login on unmount without navigation', async () => {
    const fetcher = mockApi(() => new Promise<Response>(() => {}));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); vi.useFakeTimers(); submit();
    const signal = fetcher.mock.calls[0][1].signal!; view.unmount(); await act(async () => {});
    expect(signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0); expect(leaveLogin).not.toHaveBeenCalled();
  });
  it.each([403, 429])('handles HTTP %s denial from headers without waiting for an untrusted body', async (status) => {
    mockApi(async () => new Response(new ReadableStream({ start() {} }), { status, headers: { 'Retry-After': '10' } }));
    const view = render(<Login bootstrap={bootstrap} />); await fill(); submit();
    if (status === 429) {
      expect(await screen.findByText('登录过于频繁，请 10 秒后再试')).toBeVisible();
      expect(loginButton()).toBeDisabled();
    } else {
      expect(await screen.findByText('没有权限执行此操作')).toBeVisible();
      expect(loginButton()).toBeEnabled();
    }
    expect(document.body.textContent).not.toContain('HTTP');
    assertNoRecovery(); expect(leaveLogin).not.toHaveBeenCalled(); expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('TEST_password'); view.unmount();
  });
  it.each([201, 204])('never treats noncontractual HTTP %s as confirmed login', async (status) => {
    mockApi(async () => status === 204 ? new Response(null, { status }) : json({ ok: true, ...identity }, status));
    render(<Login bootstrap={bootstrap} />); await fill(); submit(); await screen.findByText('登录服务暂不可用，请稍后重试'); expect(leaveLogin).not.toHaveBeenCalled();
    expect(loginButton()).toBeEnabled(); assertNoRecovery();
  });
});
