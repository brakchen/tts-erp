import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import Users from './index';
import type { PageProps } from '../../shared/contracts';

const originalAnimations = Object.getOwnPropertyDescriptor(Element.prototype, 'getAnimations');
beforeAll(() => Object.defineProperty(Element.prototype, 'getAnimations', { configurable: true, value: () => [] }));
afterAll(() => { if (originalAnimations) Object.defineProperty(Element.prototype, 'getAnimations', originalAnimations); else Reflect.deleteProperty(Element.prototype, 'getAnimations'); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'users', releaseId: 'TEST', navigation: [], roleCapabilities: { admin: true, readwrite: true } };
const person = { id: '12', username: 'test_user', displayName: 'TEST User', status: 'active', roles: [], lastLoginAt: null, activeSessions: 0 };
const role = { code: 'test_custom', name: 'TEST Custom', description: null, apiTier: 'readwrite', isBuiltin: false, permissions: [], userCount: 0 };
const json = (value: unknown) => new Response(JSON.stringify(value));
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }

it('keyboard save keeps rows and the connected launcher while refresh GETs are pending', async () => {
  let changed = false;
  const usersRead = deferred<Response>(), rolesRead = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') { changed = true; return json({ ok: true }); }
    if (changed) return url.endsWith('/roles') ? rolesRead.promise : usersRead.promise;
    return json(url.endsWith('/roles') ? { roles: [role], allPermissions: [] } : { users: [person] });
  }));
  const user = userEvent.setup({ delay: null }); render(<Users bootstrap={bootstrap} />);
  const row = (await screen.findByText('test_user')).closest('tr')!;
  const launch = within(row).getByRole('button', { name: '编辑显示名' });
  await user.click(launch); const dialog = screen.getByRole('dialog');
  const save = within(dialog).getByRole('button', { name: '保存' }); save.focus(); await user.keyboard('{Enter}');
  await screen.findByText('操作已确认');
  expect(screen.getByRole('grid', { name: '用户列表' })).toHaveTextContent('test_user');
  expect(launch.isConnected).toBe(true); await waitFor(() => expect(launch).toHaveFocus());
  await act(async () => { usersRead.resolve(json({ users: [person] })); rolesRead.resolve(json({ roles: [role], allPermissions: [] })); });
  expect(launch.isConnected).toBe(true); expect(launch).toHaveFocus();
}, 15000);

it('role deletion keeps a meaningful connected focus destination after the launcher row is removed', async () => {
  let deleted = false;
  const rolesRead = deferred<Response>();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') { deleted = true; return json({ ok: true }); }
    return url.endsWith('/roles') ? deleted ? rolesRead.promise : json({ roles: [role], allPermissions: [] }) : json({ users: [person] });
  }));
  const user = userEvent.setup({ delay: null }); render(<Users bootstrap={bootstrap} />);
  await screen.findByText('test_user'); await user.click(screen.getByRole('tab', { name: '角色权限' }));
  const row = (await screen.findByText('TEST Custom')).closest('tr')!;
  const launch = within(row).getByRole('button', { name: '删除角色' }); launch.focus(); await user.keyboard('{Enter}');
  const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: '确认操作' }); confirm.focus(); await user.keyboard('{Enter}');
  await screen.findByText('操作已确认');
  expect(launch.isConnected).toBe(true); await waitFor(() => expect(launch).toHaveFocus());
  await act(async () => { rolesRead.resolve(json({ roles: [], allPermissions: [] })); });
  expect(screen.queryByText('TEST Custom')).toBeNull(); expect(launch.isConnected).toBe(false);
  const heading = screen.getByRole('heading', { name: '角色权限目录' });
  const grid = screen.getByRole('grid', { name: '角色列表' });
  // HeroUI may restore focus to its surviving grid while removing the row;
  // the page's native heading is the fallback when no connected target remains.
  await waitFor(() => expect([heading, grid]).toContain(document.activeElement));
  expect(document.activeElement?.isConnected).toBe(true);
}, 15000);
