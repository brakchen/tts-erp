import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import Users from './index';
import type { PageProps } from '../../shared/contracts';
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'users', releaseId: 'TEST', navigation: [], roleCapabilities: { admin: true, readwrite: true } };
const person = { id: '12', username: 'test_user', displayName: 'TEST User', status: 'disabled', roles: [], lastLoginAt: null, activeSessions: 0 };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
it('losing capability while a form is open preserves the draft and cannot submit', async () => {
  const fetcher = vi.fn(async (url: string) => json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] }));
  vi.stubGlobal('fetch', fetcher); const user = userEvent.setup({ delay: null });
  const view = render(<Users bootstrap={bootstrap} />);
  const row = (await screen.findByText('test_user')).closest('tr')!;
  await user.click(within(row).getByRole('button', { name: '编辑显示名' })); const dialog = screen.getByRole('dialog');
  const field = within(dialog).getByRole('textbox', { name: '显示名' }); await user.clear(field); await user.type(field, 'TEST Preserved');
  view.rerender(<Users bootstrap={{ ...bootstrap, roleCapabilities: { admin: false, readwrite: true } }} />);
  expect(field).toHaveValue('TEST Preserved'); expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled();
  fireEvent.submit(within(dialog).getByRole('form')); expect(fetcher.mock.calls).toHaveLength(2);
});
it('read Retry-After blocks all refreshes until its full duration and never replays on expiry', async () => {
  let limited = false;
  const fetcher = vi.fn(async (url: string) => limited ? json({ detail: 'TEST_RAW' }, 429, { 'Retry-After': '3600' }) : json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] }));
  vi.stubGlobal('fetch', fetcher); render(<Users bootstrap={bootstrap} />); await screen.findByText('test_user');
  limited = true; vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '刷新用户' })); });
  expect(screen.getByRole('button', { name: '刷新用户' })).toBeDisabled();
  const calls = fetcher.mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(3599_000); }); expect(screen.getByRole('button', { name: '刷新用户' })).toBeDisabled();
  await act(async () => { vi.advanceTimersByTime(1000); }); expect(screen.getByRole('button', { name: '刷新用户' })).toBeEnabled(); expect(fetcher.mock.calls).toHaveLength(calls);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '刷新用户' })); }); expect(fetcher.mock.calls).toHaveLength(calls + 1);
});
it('enables a disabled user with a status-only PATCH after confirmation', async () => {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => init.method === 'PATCH' ? json({ ok: true }) : json(url.endsWith('/roles') ? { roles: [], allPermissions: [] } : { users: [person] }));
  vi.stubGlobal('fetch', fetcher); const user = userEvent.setup({ delay: null }); render(<Users bootstrap={bootstrap} />);
  const row = (await screen.findByText('test_user')).closest('tr')!; await user.click(within(row).getByRole('button', { name: '启用' })); const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('确认启用用户？')).toBeVisible(); await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
  const write = fetcher.mock.calls.find(([, init]) => init.method === 'PATCH')!; expect(write[0]).toBe('/tts/v2/users/12'); expect(JSON.parse(String(write[1].body))).toEqual({ status: 'active' });
});
