import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import Users from './index';
import { REQUEST_DEADLINE_MS } from './requestLifetime';
import type { PageProps } from '../../shared/contracts';

const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'users', releaseId: 'TEST', navigation: [], roleCapabilities: { admin: true, readwrite: true } };
const person = { id: '9007199254740993', username: 'test_user', displayName: 'TEST User', status: 'active', roles: [], lastLoginAt: null, activeSessions: 0 };
const detail = { ...person, apiTier: 'readonly', pages: [] };
const json = (value: unknown) => new Response(JSON.stringify(value));
const writes = (fetcher: ReturnType<typeof vi.fn>) => fetcher.mock.calls.filter(([, init]) => init.method !== 'GET');
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function stalledBody(body: Promise<string>) {
  const response = json({});
  Object.defineProperty(response, 'text', { value: () => body });
  return response;
}
async function openPassword() {
  const user = userEvent.setup({ delay: null });
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '重置密码' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('新密码'), { target: { value: 'TESTPass123' } });
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  return dialog;
}
const click = async (dialog: HTMLElement, name: string) => act(async () => { fireEvent.click(within(dialog).getByRole('button', { name })); });
const submit = async (dialog: HTMLElement) => act(async () => { fireEvent.submit(within(dialog).getByRole('form')); });
const expire = async () => act(async () => { vi.advanceTimersByTime(REQUEST_DEADLINE_MS); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(['fetch', 'body'] as const)('bounds stalled mutation %s without replay, retaining the password draft and ignoring late success', async (stage) => {
  const lateFetch = deferred<Response>(), lateBody = deferred<string>();
  let attempts = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') {
      attempts++;
      return attempts === 1 ? stage === 'fetch' ? lateFetch.promise : stalledBody(lateBody.promise) : json({ ok: true });
    }
    return json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : url.endsWith('/users') ? { users: [person] } : detail);
  });
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />);
  const dialog = await openPassword();
  await submit(dialog); expect(writes(fetcher)).toHaveLength(1);
  await expire();
  expect(within(dialog).getByText(/提交结果未确认/)).toBeVisible();
  expect((writes(fetcher)[0][1].signal as AbortSignal).aborted).toBe(true);
  expect(within(dialog).getByLabelText('新密码')).toHaveValue('TESTPass123');
  expect(within(dialog).getByRole('button', { name: '查询公开状态' })).toBeEnabled();
  await act(async () => { lateFetch.resolve(json({ ok: true })); lateBody.resolve('{"ok":true}'); });
  expect(within(dialog).queryByText('操作已确认')).toBeNull();
  expect(within(dialog).getByLabelText('新密码')).toHaveValue('TESTPass123');
  await submit(dialog); expect(writes(fetcher)).toHaveLength(1);
  await click(dialog, '查询公开状态');
  expect(within(dialog).getByText(/公开状态已读取/)).toBeVisible();
  expect(within(dialog).getByText(/不能验证密码/)).toBeVisible();
  await submit(dialog); expect(writes(fetcher)).toHaveLength(1);
  await click(dialog, '我已核对，允许手动重试'); expect(writes(fetcher)).toHaveLength(1);
  await submit(dialog);
  expect(writes(fetcher).map(([url]) => url)).toEqual(['/tts/v2/users/9007199254740993/password', '/tts/v2/users/9007199254740993/password']);
}, 15000);

it.each(['fetch', 'body'] as const)('bounds stalled verification %s, ignores late evidence and permits an explicit GET retry', async (stage) => {
  const lateFetch = deferred<Response>(), lateBody = deferred<string>();
  let checks = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') throw new TypeError('TEST transport failure');
    if (url.endsWith('/roles')) return json({ roles: [], allPermissions: [] });
    if (url.endsWith('/users')) return json({ users: [person] });
    checks++;
    return checks === 1 ? stage === 'fetch' ? lateFetch.promise : stalledBody(lateBody.promise) : json(detail);
  });
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />);
  const dialog = await openPassword(); await submit(dialog); await click(dialog, '查询公开状态');
  expect(within(dialog).getByText('查询中…')).toBeVisible();
  await expire();
  expect(within(dialog).getByText(/查询未完成/)).toBeVisible();
  const check = fetcher.mock.calls.find(([url]) => url === '/tts/v2/users/9007199254740993')!;
  expect((check[1].signal as AbortSignal).aborted).toBe(true);
  expect(within(dialog).getByRole('button', { name: '查询公开状态' })).toBeEnabled();
  await act(async () => { lateFetch.resolve(json({ ...detail, displayName: 'TEST Late' })); lateBody.resolve(JSON.stringify({ ...detail, displayName: 'TEST Late' })); });
  expect(within(dialog).queryByText(/TEST Late/)).toBeNull();
  expect(within(dialog).queryByRole('button', { name: '我已核对，允许手动重试' })).toBeNull();
  expect(within(dialog).getByLabelText('新密码')).toHaveValue('TESTPass123');
  await submit(dialog); expect(writes(fetcher)).toHaveLength(1);
  await click(dialog, '查询公开状态');
  expect(checks).toBe(2); expect(within(dialog).getByText(/公开状态已读取/)).toBeVisible();
  expect(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' })).toBeEnabled();
  expect(writes(fetcher)).toHaveLength(1);
}, 15000);

it('aborts a pending mutation on unmount and never processes its late completion', async () => {
  const late = deferred<Response>();
  const fetcher = vi.fn(async (url: string, init: RequestInit) => init.method !== 'GET' ? late.promise : json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] }));
  vi.stubGlobal('fetch', fetcher); const view = render(<Users bootstrap={bootstrap} />);
  const dialog = await openPassword(); await submit(dialog);
  const signal = writes(fetcher)[0][1].signal as AbortSignal;
  view.unmount(); expect(signal?.aborted).toBe(true);
  await act(async () => { late.resolve(json({ ok: true })); vi.advanceTimersByTime(REQUEST_DEADLINE_MS); });
  expect(writes(fetcher)).toHaveLength(1); expect(screen.queryByText('操作已确认')).toBeNull();
});
