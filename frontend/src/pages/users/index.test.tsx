import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentType } from 'react';
import type { PageProps } from '../../shared/contracts';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'users', navigation: [], releaseId: 'TEST', roleCapabilities: { admin: true, readwrite: true } } };
const person = { id: '9007199254740993', username: 'test_user', displayName: 'TEST User', status: 'active', roles: ['operator'], lastLoginAt: null, activeSessions: 0 };
const operator = { code: 'operator', name: 'TEST Operator', description: null, apiTier: 'readwrite', isBuiltin: true, permissions: ['page:dashboard'], userCount: 1 };
const custom = { ...operator, code: 'test_custom', name: 'TEST Custom', isBuiltin: false, userCount: 0 };
const catalog = { roles: [{ ...operator, code: 'admin', name: 'TEST Admin', apiTier: 'admin' }, operator, custom], allPermissions: [{ code: 'page:dashboard', label: 'TEST Dashboard', group: 'TEST Group' }] };
const detail = { ...person, pages: ['dashboard'], apiTier: 'readwrite' };
const session = { id: '9007199254740995', createdAt: '2026-01-01T00:00:00Z', expiresAt: '2026-02-01T00:00:00Z', lastSeenAt: null, revokedAt: null, ip: null, userAgent: 'TEST Agent', active: true };
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function api(command: (url: string, init: RequestInit) => Promise<Response> = async () => json({ ok: true })) {
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method !== 'GET') return command(url, init);
    if (url.endsWith('/roles')) return json(catalog);
    if (url.endsWith('/sessions')) return json({ sessions: [session] });
    if (url.endsWith('/users')) return json({ users: [person] });
    return json(detail);
  }); vi.stubGlobal('fetch', fetcher); return fetcher;
}
async function mount(bootstrap = props.bootstrap) {
  // Before migration the content slot is empty. This permits a behavioral RED,
  // not a missing-module/import failure, before the page implementation exists.
  const modules = import.meta.glob<{ default: ComponentType<PageProps> }>('./index.tsx');
  const Page = modules['./index.tsx'] ? (await modules['./index.tsx']()).default : () => null;
  return { ...render(<Page bootstrap={bootstrap} />), Page };
}
async function openUser(user: ReturnType<typeof userEvent.setup>, label: string) {
  const cell = await screen.findByText('test_user'); await user.click(within(cell.closest('tr')!).getByRole('button', { name: label }));
  return screen.getByRole('dialog');
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('renders complete user columns and authoritative detail without inventing grants', async () => {
  api(); const user = userEvent.setup(); await mount();
  const dialog = await openUser(user, '详情');
  expect(within(dialog).getByText('readwrite')).toBeVisible(); expect(within(dialog).getByText('dashboard')).toBeVisible();
  expect(screen.getByRole('grid', { name: '用户列表' })).toHaveTextContent('0');
});
it('creates once using actual camelCase aliases, CSRF header and server identity response', async () => {
  let resolve!: (response: Response) => void;
  const fetcher = api(async () => new Promise<Response>((done) => { resolve = done; })); const user = userEvent.setup(); await mount(); await screen.findByText('test_user');
  await user.click(screen.getByRole('button', { name: '新建用户' })); const dialog = screen.getByRole('dialog');
  await user.type(within(dialog).getByRole('textbox', { name: '用户名' }), 'test_new');
  await user.type(within(dialog).getByRole('textbox', { name: '显示名' }), 'TEST New');
  fireEvent.change(within(dialog).getByLabelText('初始密码'), { target: { value: 'TESTPass123' } });
  await user.click(within(dialog).getByRole('checkbox', { name: 'TEST Operator（operator）' }));
  const form = within(dialog).getByRole('form'); fireEvent.submit(form); fireEvent.submit(form);
  const calls = fetcher.mock.calls.filter(([, init]) => init.method !== 'GET'); expect(calls).toHaveLength(1);
  expect(calls[0][0]).toBe('/tts/v2/users'); expect(JSON.parse(String(calls[0][1].body))).toEqual({ username: 'test_new', displayName: 'TEST New', password: 'TESTPass123', roles: ['operator'] });
  expect(new Headers(calls[0][1].headers).get('X-Requested-With')).toBe('tts-erp');
  await act(async () => { resolve(json({ id: '52', username: 'test_new' }, 201)); }); await screen.findByText('操作已确认'); expect(screen.queryByRole('dialog')).toBeNull();
});
it('retains draft and clears stale submitted errors on edit without exposing raw validation input', async () => {
  api(async () => json({ detail: [{ loc: ['body', 'displayName'], type: 'string_too_long', input: 'TEST_SECRET_RAW' }] }, 422)); const user = userEvent.setup(); await mount(); const dialog = await openUser(user, '编辑显示名');
  await user.click(within(dialog).getByRole('button', { name: '保存' })); await within(dialog).findByText(/HTTP 422/);
  const field = within(dialog).getByRole('textbox', { name: '显示名' }); expect(field).toBeInvalid();
  await user.clear(field); await user.type(field, 'TEST Corrected'); expect(field).toBeValid(); expect(document.body.textContent).not.toContain('TEST_SECRET_RAW');
});
it('confirms status and submits status only; server owns self/last-admin rejection', async () => {
  const fetcher = api(async () => json({ detail: 'TEST_RAW_SELF_ERROR' }, 400)); const user = userEvent.setup(); await mount(); let dialog = await openUser(user, '停用');
  expect(within(dialog).getByText(/吊销其全部会话/)).toBeVisible(); await user.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(fetcher.mock.calls.filter(([, init]) => init.method !== 'GET')).toHaveLength(0);
  dialog = await openUser(user, '停用'); await user.click(within(dialog).getByRole('button', { name: '确认操作' })); await within(dialog).findByText(/HTTP 400/);
  const call = fetcher.mock.calls.find(([, init]) => init.method === 'PATCH')!; expect(call[0]).toBe('/tts/v2/users/9007199254740993'); expect(JSON.parse(String(call[1].body))).toEqual({ status: 'disabled' }); expect(document.body.textContent).not.toContain('TEST_RAW_SELF_ERROR');
});
it('assigns roles separately without changing status or display name', async () => {
  const fetcher = api(); const user = userEvent.setup(); await mount(); const dialog = await openUser(user, '分配角色');
  await user.click(within(dialog).getByRole('checkbox', { name: 'TEST Custom（test_custom）' })); await user.click(within(dialog).getByRole('button', { name: '保存' })); await screen.findByText('操作已确认');
  expect(JSON.parse(String(fetcher.mock.calls.find(([, init]) => init.method === 'PATCH')![1].body))).toEqual({ roles: ['operator', 'test_custom'] });
});
it('never proves unknown password reset through GET and requires an explicit retry decision', async () => {
  let writes = 0; api(async () => { writes++; throw new TypeError('TEST_SECRET_RAW'); }); const user = userEvent.setup(); await mount(); const dialog = await openUser(user, '重置密码');
  const input = within(dialog).getByLabelText('新密码'); expect(input).toHaveValue(''); fireEvent.change(input, { target: { value: 'TESTPass123' } });
  await user.click(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })); await within(dialog).findByText(/提交结果未确认/);
  expect(within(dialog).getByRole('button', { name: '重置密码并吊销会话' })).toBeDisabled();
  await user.click(within(dialog).getByRole('button', { name: '查询公开状态' })); await within(dialog).findByText(/公开状态已读取/);
  expect(within(dialog).getByText(/不能验证密码/)).toBeVisible(); expect(writes).toBe(1); expect(input).toHaveValue('TESTPass123');
  await user.click(within(dialog).getByRole('button', { name: '我已核对，允许手动重试' })); expect(writes).toBe(1);
  await user.click(within(dialog).getByRole('button', { name: '明确再次提交' })); expect(writes).toBe(2); expect(document.body.textContent).not.toContain('TEST_SECRET_RAW');
});
it('lists all session columns and confirms one and all revocation using exact IDs', async () => {
  const fetcher = api(async () => json({ ok: true, revoked: 1 })); const user = userEvent.setup(); await mount(); const dialog = await openUser(user, '会话');
  await within(dialog).findByText('TEST Agent'); expect(within(dialog).getByRole('grid')).toHaveTextContent('9007199254740995');
  await user.click(within(dialog).getByRole('button', { name: '吊销此会话' })); await user.click(within(dialog).getByRole('button', { name: '确认操作' })); await within(dialog).findByText('操作已确认');
  await user.click(within(dialog).getByRole('button', { name: '吊销全部会话' })); await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
  const calls = fetcher.mock.calls.filter(([, init]) => init.method === 'DELETE'); expect(calls.map(([url]) => url)).toEqual(['/tts/v2/users/9007199254740993/sessions/9007199254740995', '/tts/v2/users/9007199254740993/sessions']);
});
it('uses server permission catalog and preserves builtin edit/delete/tier restrictions', async () => {
  api(); const user = userEvent.setup(); await mount(); await user.click(screen.getByRole('tab', { name: '角色权限' })); await screen.findByText('TEST Admin');
  const admin = screen.getByText('TEST Admin').closest('tr')!; expect(within(admin).getByRole('button', { name: '编辑角色' })).toBeDisabled(); expect(within(admin).getByRole('button', { name: '删除角色' })).toBeDisabled();
  await user.click(within(screen.getByText('TEST Operator').closest('tr')!).getByRole('button', { name: '编辑角色' })); const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByLabelText('API 档位')).toBeDisabled(); expect(within(dialog).getByRole('checkbox', { name: 'TEST Dashboard（page:dashboard）' })).toBeChecked();
});
it('creates updates and deletes custom roles using canonical aliases and explicit delete confirmation', async () => {
  const fetcher = api(async (_url, init) => init.method === 'POST' ? json({ code: 'test_new' }, 201) : json({ ok: true })); const user = userEvent.setup(); await mount(); await user.click(screen.getByRole('tab', { name: '角色权限' })); await screen.findByText('TEST Custom');
  await user.click(screen.getByRole('button', { name: '新建角色' })); let dialog = screen.getByRole('dialog');
  await user.type(within(dialog).getByRole('textbox', { name: '角色代码' }), 'test_new'); await user.type(within(dialog).getByRole('textbox', { name: '角色名称' }), 'TEST New');
  await user.click(within(dialog).getByRole('checkbox', { name: 'TEST Dashboard（page:dashboard）' })); await user.click(within(dialog).getByRole('button', { name: '保存' })); await screen.findByText('操作已确认');
  const body = JSON.parse(String(fetcher.mock.calls.find(([, init]) => init.method === 'POST')![1].body)); expect(body).toEqual({ code: 'test_new', name: 'TEST New', apiTier: 'readwrite', permissions: ['page:dashboard'] });
  await user.click(within(screen.getByText('TEST Custom').closest('tr')!).getByRole('button', { name: '编辑角色' })); dialog = screen.getByRole('dialog'); await user.click(within(dialog).getByRole('button', { name: '保存' })); await screen.findByText('操作已确认');
  await user.click(within(screen.getByText('TEST Custom').closest('tr')!).getByRole('button', { name: '删除角色' })); dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/仍被用户引用时/)).toBeVisible(); await user.click(within(dialog).getByRole('button', { name: '确认操作' }));
  expect(fetcher.mock.calls.some(([url, init]) => url === '/tts/v2/roles/test_custom' && init.method === 'DELETE')).toBe(true);
});
it.each([undefined, { admin: false, readwrite: true }])('fails closed on capability hints without creating a second permission catalog', async (roleCapabilities) => {
  const fetcher = api(); await mount({ ...props.bootstrap, roleCapabilities }); await screen.findByText('test_user'); expect(screen.getByRole('button', { name: '新建用户' })).toBeDisabled(); expect(screen.queryByRole('button', { name: '停用' })).toBeNull(); expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
});
it('distinguishes forbidden list from empty list and never renders raw server errors', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'TEST_RAW_SECRET' }, 403))); await mount(); expect(await screen.findByText(/HTTP 403/)).toBeVisible(); expect(screen.queryByText('暂无用户')).toBeNull(); expect(document.body.textContent).not.toContain('TEST_RAW_SECRET');
});
