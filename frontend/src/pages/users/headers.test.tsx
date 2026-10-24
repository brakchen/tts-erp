import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import Users from './index';
import type { PageProps } from '../../shared/contracts';

const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'users', releaseId: 'TEST', navigation: [], roleCapabilities: { admin: true, readwrite: true } };
const person = { id: '9007199254740993', username: 'test_user', displayName: 'TEST User', status: 'active', roles: [], lastLoginAt: null, activeSessions: 0 };
const detail = { ...person, apiTier: 'readonly', pages: [] };
const json = (value: unknown) => new Response(JSON.stringify(value));
const writes = (fetcher: ReturnType<typeof vi.fn>) => fetcher.mock.calls.filter(([, init]) => init.method !== 'GET');
function stalledError(status: 403 | 429) {
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({ pull: () => new Promise<void>(() => {}), cancel });
  const response = new Response(body, { status, headers: { 'Retry-After': '60', 'x-request-id': 'TEST headers' } });
  return { response, cancel, text: vi.spyOn(response, 'text') };
}
const click = async (root: HTMLElement, name: string) => act(async () => { fireEvent.click(within(root).getByRole('button', { name })); });
const submit = async (dialog: HTMLElement) => act(async () => { fireEvent.submit(within(dialog).getByRole('form')); });
const advance = async (milliseconds: number) => act(async () => { vi.advanceTimersByTime(milliseconds); });
async function passwordDialog() {
  const user = userEvent.setup({ delay: null });
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '重置密码' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('新密码'), { target: { value: 'TESTPass123' } });
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  return dialog;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('retains the full read 429 header cooldown with a stalled body, refresh guards and no expiry request', async () => {
  const limited = stalledError(429); let throttle = false;
  const fetcher = vi.fn(async (url: string) => url.endsWith('/roles') ? json({ roles: [], allPermissions: [] }) : throttle ? limited.response : json({ users: [person] }));
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); await screen.findByText('test_user');
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] }); throttle = true;
  await click(document.body, '刷新用户');
  expect(screen.getByText(/读取失败（HTTP 429）/)).toBeVisible();
  expect(limited.text).not.toHaveBeenCalled(); expect(limited.cancel).toHaveBeenCalledOnce();
  expect(screen.getByRole('grid', { name: '用户列表' })).toHaveTextContent('test_user');
  const calls = fetcher.mock.calls.length;
  await advance(30_000); expect(screen.getByRole('button', { name: '刷新用户' })).toBeDisabled();
  await click(document.body, '刷新用户'); expect(fetcher.mock.calls).toHaveLength(calls);
  await advance(29_999); expect(screen.getByRole('button', { name: '刷新用户' })).toBeDisabled();
  await advance(1); expect(screen.getByRole('button', { name: '刷新用户' })).toBeEnabled(); expect(fetcher.mock.calls).toHaveLength(calls);
  throttle = false; await click(document.body, '刷新用户');
  expect(fetcher.mock.calls).toHaveLength(calls + 1);
});

it('retains a password draft and captured mutation target throughout the full stalled-body 429 cooldown', async () => {
  const limited = stalledError(429); let attempts = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') { attempts++; return attempts === 1 ? limited.response : json({ ok: true }); }
    return json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] });
  });
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); const dialog = await passwordDialog();
  await submit(dialog);
  expect(within(dialog).getByText(/HTTP 429/)).toBeVisible();
  expect(within(dialog).queryByText(/提交结果未确认/)).toBeNull();
  expect(limited.text).not.toHaveBeenCalled(); expect(limited.cancel).toHaveBeenCalledOnce();
  const input = within(dialog).getByLabelText('新密码');
  fireEvent.change(input, { target: { value: 'TESTEdited123' } }); expect(input).toHaveValue('TESTEdited123');
  const calls = fetcher.mock.calls.length;
  await advance(30_000); await submit(dialog); expect(fetcher.mock.calls).toHaveLength(calls);
  expect(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '刷新用户', hidden: true })).toBeDisabled();
  await advance(29_999); await submit(dialog); expect(fetcher.mock.calls).toHaveLength(calls);
  await advance(1); expect(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })).toBeEnabled();
  expect(fetcher.mock.calls).toHaveLength(calls); expect(input).toHaveValue('TESTEdited123');
  await submit(dialog);
  expect(writes(fetcher).map(([url, init]) => [url, init.method])).toEqual([
    ['/tts/v2/users/9007199254740993/password', 'POST'], ['/tts/v2/users/9007199254740993/password', 'POST'],
  ]);
}, 15000);

it('preserves the full received 429 cooldown during uncertain-password re-verification without replay or losing the draft', async () => {
  const limited = stalledError(429); let checks = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') throw new TypeError('TEST transport failure');
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/users')) return json({ users: [person] });
    checks++; return checks === 1 ? limited.response : json(detail);
  });
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); const dialog = await passwordDialog();
  await submit(dialog); await click(dialog, '查询公开状态');
  expect(within(dialog).getByText(/查询未完成/)).toBeVisible();
  expect(limited.text).not.toHaveBeenCalled(); expect(limited.cancel).toHaveBeenCalledOnce();
  fireEvent.change(within(dialog).getByLabelText('新密码'), { target: { value: 'TESTEdited123' } });
  const calls = fetcher.mock.calls.length;
  await advance(30_000); await click(dialog, '查询公开状态'); await submit(dialog);
  expect(within(dialog).getByRole('button', { name: '查询公开状态' })).toBeDisabled(); expect(fetcher.mock.calls).toHaveLength(calls);
  await advance(29_999); expect(within(dialog).getByRole('button', { name: '查询公开状态' })).toBeDisabled();
  await advance(1); expect(within(dialog).getByRole('button', { name: '查询公开状态' })).toBeEnabled(); expect(fetcher.mock.calls).toHaveLength(calls);
  await click(dialog, '查询公开状态'); expect(checks).toBe(2);
  expect(within(dialog).getByText(/公开状态已读取/)).toBeVisible();
  expect(within(dialog).getByLabelText('新密码')).toHaveValue('TESTEdited123');
  expect(within(dialog).getByText(/不能验证密码/)).toBeVisible();
  await submit(dialog); expect(writes(fetcher)).toHaveLength(1);
  await click(dialog, '我已核对，允许手动重试'); expect(writes(fetcher)).toHaveLength(1);
  await submit(dialog);
  expect(writes(fetcher).map(([url]) => url)).toEqual(['/tts/v2/users/9007199254740993/password', '/tts/v2/users/9007199254740993/password']);
}, 15000);

it('reports received mutation 403 immediately rather than treating its stalled body as an uncertain write', async () => {
  const forbidden = stalledError(403);
  const fetcher = vi.fn(async (url: string, init: RequestInit) => init.method !== 'GET' ? forbidden.response : json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] }));
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); const dialog = await passwordDialog();
  await submit(dialog);
  expect(within(dialog).getByText(/HTTP 403/)).toBeVisible();
  expect(within(dialog).queryByText(/提交结果未确认/)).toBeNull();
  expect(within(dialog).queryByRole('button', { name: '查询公开状态' })).toBeNull();
  expect(within(dialog).getByLabelText('新密码')).toHaveValue('TESTPass123');
  expect(forbidden.text).not.toHaveBeenCalled(); expect(forbidden.cancel).toHaveBeenCalledOnce();
  const calls = fetcher.mock.calls.length; await advance(60_000); expect(fetcher.mock.calls).toHaveLength(calls);
  expect(writes(fetcher)).toHaveLength(1);
}, 15000);
