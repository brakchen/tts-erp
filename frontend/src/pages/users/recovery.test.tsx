import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import Users from './index';
import type { PageProps } from '../../shared/contracts';
const originalAnimations = Object.getOwnPropertyDescriptor(Element.prototype, 'getAnimations');
beforeAll(() => Object.defineProperty(Element.prototype, 'getAnimations', { configurable: true, value: () => [] }));
afterAll(() => { if (originalAnimations) Object.defineProperty(Element.prototype, 'getAnimations', originalAnimations); else Reflect.deleteProperty(Element.prototype, 'getAnimations'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'users', releaseId: 'TEST', navigation: [], roleCapabilities: { admin: true, readwrite: true } };
const person = { id: '9007199254740993', username: 'test_user', displayName: 'TEST User', status: 'active', roles: [], lastLoginAt: null, activeSessions: 0 };
const detail = { ...person, apiTier: 'readonly', pages: [] };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
function setup(command: (url: string, init: RequestInit) => Promise<Response>, read?: (url: string) => Promise<Response>) {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') return command(url, init);
    if (read) return read(url);
    return json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : url.endsWith('/users') ? { users: [person] } : detail);
  }); vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); return fetcher;
}
async function edit(user: ReturnType<typeof userEvent.setup>) {
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '编辑显示名' })); return screen.getByRole('dialog');
}
it('retains raw numeric long user and session IDs through the real React page and exact command path', async () => {
  const user = userEvent.setup({ delay: null });
  const fetcher = setup(async () => json({ ok: true, revoked: 1 }), async (url) => {
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/sessions')) return new Response('{"sessions":[{"id":9007199254740995,"createdAt":"TEST","expiresAt":"TEST","lastSeenAt":null,"revokedAt":null,"ip":null,"userAgent":"TEST Agent","active":true}]}');
    return new Response('{"users":[{"id":9007199254740993,"username":"test_user","displayName":"TEST User","status":"active","roles":[],"lastLoginAt":null,"activeSessions":0}]}');
  });
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '会话' })); const dialog = screen.getByRole('dialog');
  await within(dialog).findByText('9007199254740995');
  await user.click(within(dialog).getByRole('button', { name: '吊销此会话' }));
  await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
  expect(fetcher.mock.calls.some(([url, init]) => init.method === 'DELETE' && url === '/tts/v2/users/9007199254740993/sessions/9007199254740995')).toBe(true);
});
it('unknown submit remains gated across failed and malformed verification reads, close and reopen, then explicit decision', async () => {
  let detailReads = 0;
  const user = userEvent.setup({ delay: null });
  const fetcher = setup(async () => new Response('not json'), async (url) => {
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/users')) return json({ users: [person] });
    detailReads++;
    if (detailReads === 1) return json({ detail: 'TEST_SECRET_RAW' }, 503);
    if (detailReads === 2) return json({});
    return json(detail);
  });
  let dialog = await edit(user); const field = within(dialog).getByRole('textbox', { name: '显示名' });
  await user.clear(field); await user.type(field, 'TEST Preserved');
  await user.click(within(dialog).getByRole('button', { name: '保存' })); await within(dialog).findByText(/提交结果未确认/);
  await user.click(within(dialog).getByRole('button', { name: '取消' }));
  await user.click(screen.getByRole('button', { name: '返回未确认的操作' })); dialog = screen.getByRole('dialog');
  expect(within(dialog).getByRole('textbox', { name: '显示名' })).toHaveValue('TEST Preserved');
  for (let i = 0; i < 2; i++) { await user.click(within(dialog).getByRole('button', { name: '查询公开状态' })); await within(dialog).findByText(/查询未完成/); expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled(); }
  await user.click(within(dialog).getByRole('button', { name: '查询公开状态' })); await within(dialog).findByText(/公开状态已读取/);
  expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled();
  await user.click(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' }));
  expect(within(dialog).getByRole('button', { name: '明确再次提交' })).toBeEnabled();
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(1);
  expect(document.body.textContent).not.toContain('TEST_SECRET_RAW');
}, 15000);
it('honors the full command Retry-After across draft edits and manual refresh with no expiry replay', async () => {
  const user = userEvent.setup({ delay: null }); const fetcher = setup(async () => json({ detail: 'TEST_RAW' }, 429, { 'Retry-After': '3600' }));
  const dialog = await edit(user);
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  await act(async () => { fireEvent.submit(within(dialog).getByRole('form')); });
  expect(within(dialog).getByText(/HTTP 429/)).toBeVisible();
  fireEvent.change(within(dialog).getByRole('textbox', { name: '显示名' }), { target: { value: 'TEST Draft' } });
  expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled();
  const count = fetcher.mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(3599_000); });
  expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled();
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(within(dialog).getByRole('button', { name: '保存' })).toBeEnabled(); expect(fetcher.mock.calls).toHaveLength(count);
  vi.useRealTimers(); await user.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(screen.getByRole('button', { name: '刷新用户' })).toBeEnabled();
});
it('keeps unknown session target immutable even after explicitly permitting manual retry', async () => {
  const user = userEvent.setup({ delay: null }); const fetcher = setup(async () => { throw new TypeError('TEST_RAW'); }, async (url) => {
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/users')) return json({ users: [person] });
    if (url.endsWith('/sessions')) return json({ sessions: [{ id: '99', createdAt: 'TEST', expiresAt: 'TEST', lastSeenAt: null, revokedAt: null, ip: null, userAgent: null, active: true }] });
    return json(detail);
  });
  const row = (await screen.findByText('test_user')).closest('tr')!; await user.click(within(row).getByRole('button', { name: '会话' })); const dialog = screen.getByRole('dialog');
  await within(dialog).findByText('99'); await user.click(within(dialog).getByRole('button', { name: '吊销此会话' })); await user.click(within(dialog).getByRole('button', { name: '确认操作' })); await within(dialog).findByText(/提交结果未确认/);
  await user.click(within(dialog).getByRole('button', { name: '查询公开状态' })); await within(dialog).findByText(/公开状态已读取/); await user.click(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' }));
  expect(within(dialog).getByRole('button', { name: '吊销全部会话' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '吊销此会话' })).toBeDisabled();
  const callsBeforeCheck = fetcher.mock.calls.length;
  await user.click(within(dialog).getByRole('button', { name: '查询公开状态' }));
  await within(dialog).findByText(/公开状态已读取/);
  expect(fetcher.mock.calls.slice(callsBeforeCheck).map(([url, init]) => [url, init.method])).toEqual([
    ['/tts/v2/users/9007199254740993', 'GET'],
    ['/tts/v2/users/9007199254740993/sessions', 'GET'],
  ]);
  expect(within(dialog).getByRole('button', { name: '吊销全部会话' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '吊销此会话' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '确认操作' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' })).toBeEnabled();
  expect(within(dialog).getByText('确认吊销会话 99？该会话将失效。')).toBeVisible();
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(1);
});
it('queries again after password retry authorization and requires a renewed explicit retry decision', async () => {
  let detailReads = 0;
  let resolveCheck!: (response: Response) => void;
  const user = userEvent.setup({ delay: null });
  const fetcher = setup(async () => { throw new TypeError('TEST transport failure'); }, async (url) => {
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/users')) return json({ users: [person] });
    detailReads++;
    return detailReads === 2 ? new Promise<Response>((resolve) => { resolveCheck = resolve; }) : json(detail);
  });
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '重置密码' }));
  const dialog = screen.getByRole('dialog');
  const password = within(dialog).getByLabelText('新密码');
  fireEvent.change(password, { target: { value: 'TESTPass123' } });
  await user.click(within(dialog).getByRole('button', { name: '重置密码并吊销会话' }));
  await within(dialog).findByText(/提交结果未确认/);
  await user.click(within(dialog).getByRole('button', { name: '查询公开状态' }));
  await within(dialog).findByText(/公开状态已读取/);
  await user.click(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' }));
  expect(within(dialog).getByRole('button', { name: '明确再次提交' })).toBeEnabled();
  const callsBeforeCheck = fetcher.mock.calls.length;
  const query = within(dialog).getByRole('button', { name: '查询公开状态' });
  expect(query).toBeEnabled();
  await user.click(query);
  expect(fetcher.mock.calls.slice(callsBeforeCheck).map(([url, init]) => [url, init.method])).toEqual([
    ['/tts/v2/users/9007199254740993', 'GET'],
  ]);
  expect(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })).toBeDisabled();
  fireEvent.submit(within(dialog).getByRole('form'));
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(1);
  await act(async () => { resolveCheck(json(detail)); });
  await within(dialog).findByText(/公开状态已读取/);
  expect(password).toHaveValue('TESTPass123');
  expect(within(dialog).getByText(/不能验证密码/)).toBeVisible();
  expect(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })).toBeDisabled();
  fireEvent.submit(within(dialog).getByRole('form'));
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(1);
  await user.click(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' }));
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(1);
  await user.click(within(dialog).getByRole('button', { name: '明确再次提交' }));
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET').map(([url, init]) => [url, init.method])).toEqual([
    ['/tts/v2/users/9007199254740993/password', 'POST'],
    ['/tts/v2/users/9007199254740993/password', 'POST'],
  ]);
}, 15000);
