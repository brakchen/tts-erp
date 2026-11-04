import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';

const modulePath = './index';
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'shops', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const shop = { id: 41, platform: 'tiktok', shop_id: '90071992547409931234567', account_name: 'TEST_name', region: null, seller_type: null, status: 'active', opened_date: null, credential_id: null, service_id: 'TEST_svc', app_credentials_configured: true };
const savedApp = '当前店铺的 App 配置已保存，可以获取授权链接。';
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
type Command = { url: string; init: RequestInit };

function api(command: (url: string, init: RequestInit) => Promise<Response>) {
  const fetcher = vi.fn((url: string, init: RequestInit) => url.includes('/v2/commerce/channel-accounts?')
    ? Promise.resolve(json([shop], 200, { 'X-Total-Count': '502' })) : command(url, init));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
async function mount(bootstrap = props.bootstrap) {
  const { default: Shops } = await import(modulePath);
  return render(<Shops bootstrap={bootstrap} />);
}
async function openRegister(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(shop.shop_id);
  await user.click(screen.getByRole('button', { name: '手工补录' }));
  return screen.getByRole('dialog');
}
async function fillRegister(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.type(within(dialog).getByRole('textbox', { name: '店铺 ID' }), shop.shop_id);
}
async function openApp(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(shop.shop_id);
  await user.click(screen.getByRole('button', { name: '配置 App' }));
  return screen.getByRole('dialog');
}
async function fillApp(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, secret = 'TEST_secret') {
  await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_key');
  await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), secret);
}
const save = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: /^保存(?:中…)?$/ });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('shops content', () => {
  it.each([undefined, { readwrite: false, admin: false }, { readwrite: false, admin: true }])('fails closed on missing/readonly hints without inventing a hierarchy %#', async (roleCapabilities) => {
    const fetcher = api(async () => { throw new Error('TEST no commands expected'); });
    await mount({ ...props.bootstrap, roleCapabilities }); await screen.findByText(shop.shop_id);
    for (const name of ['手工补录', '编辑信息', '配置 App', '获取授权链接']) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('uses the paginated management view and keeps App configuration separate from authorization', async () => {
    const fetcher = api(async () => { throw new Error('TEST no command'); });
    await mount(); expect(await screen.findByText(shop.shop_id)).toBeVisible();
    expect(screen.getByText(/已登记 502 家店铺/)).toBeVisible();
    const table = within(screen.getByRole('region', { name: '店铺表格' }));
    expect(table.getByText('已配置', { exact: true })).toBeVisible();
    expect(table.getByText('未授权', { exact: true })).toBeVisible();
    expect(screen.getByText('未填开店日期')).toBeVisible();
    expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/commerce/channel-accounts?platform=tiktok&view=management&limit=50&offset=0');
    expect(fetcher.mock.calls.some(([url]) => url.includes('/auth/me') || url.includes('/unregistered'))).toBe(false);
  });

  it('sends the selected source, App and OAuth filters and clears them with the search on reset', async () => {
    const fetcher = api(async () => { throw new Error('TEST no commands'); });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id);
    await user.click(screen.getByRole('button', { name: '全部来源 发现来源' }));
    await user.click(await screen.findByRole('option', { name: 'Ads 插件' }));
    await user.click(screen.getByRole('button', { name: '全部配置 App 配置' }));
    await user.keyboard('{ArrowDown}{Enter}');
    await user.click(screen.getByRole('button', { name: '全部授权状态 OAuth 授权' }));
    await user.click(await screen.findByRole('option', { name: '需重新授权' }));
    await user.type(screen.getByRole('searchbox', { name: '店铺搜索' }), 'TEST_query{Enter}');
    await waitFor(() => {
      const query = new URL(fetcher.mock.calls.at(-1)![0], 'https://test.invalid').searchParams;
      expect(Object.fromEntries(query)).toMatchObject({ platform: 'tiktok', view: 'management', offset: '0', q: 'TEST_query', source: 'ads-data-sync', app_state: 'configured', auth_state: 'reauthorize' });
    });
    await user.click(screen.getByRole('button', { name: '重置' }));
    await waitFor(() => expect(fetcher.mock.calls.at(-1)![0]).toBe('/tts/v2/commerce/channel-accounts?platform=tiktok&view=management&limit=50&offset=0'));
    expect(screen.getByRole('searchbox', { name: '店铺搜索' })).toHaveValue('');
  });

  it('registers metadata once under repeated submit without sending App credentials', async () => {
    let release!: (response: Response) => void; const commands: Command[] = [];
    api((url, init) => { commands.push({ url, init }); return new Promise((resolve) => { release = resolve; }); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    expect(within(dialog).queryByLabelText('Service ID', { exact: true })).toBeNull();
    expect(within(dialog).queryByLabelText('App Secret', { exact: true })).toBeNull();
    const form = within(dialog).getByRole('form', { name: '店铺表单' }); fireEvent.submit(form); fireEvent.submit(form);
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/admin/shops/register');
    expect(JSON.parse(commands[0].init.body as string)).toEqual({ platform: 'tiktok', shop_id: shop.shop_id });
    expect(new Headers(commands[0].init.headers).get('X-Requested-With')).toBe('tts-erp');
    expect(commands[0].init.credentials).toBe('same-origin'); expect(save(dialog)).toBeDisabled();
    await act(async () => release(json({ created: false, shop })));
    expect(await screen.findByText('店铺信息已保存')).toBeVisible(); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('saves an App pair only once and never echoes a previously stored secret', async () => {
    let release!: (response: Response) => void; const commands: Command[] = [];
    api((url, init) => { commands.push({ url, init }); return new Promise((resolve) => { release = resolve; }); });
    const user = userEvent.setup(); await mount(); let dialog = await openApp(user);
    expect(within(dialog).getByLabelText('App Key', { exact: true })).toHaveValue('');
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('');
    await fillApp(user, dialog);
    const form = within(dialog).getByRole('form', { name: '店铺表单' }); fireEvent.submit(form); fireEvent.submit(form);
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/admin/shops/41');
    expect(commands[0].init.method).toBe('PATCH');
    expect(JSON.parse(commands[0].init.body as string)).toEqual({ service_id: 'TEST_svc', app_key: 'TEST_key', app_secret: 'TEST_secret' });
    expect(new Headers(commands[0].init.headers).get('X-Requested-With')).toBe('tts-erp');
    expect(commands[0].init.credentials).toBe('same-origin'); expect(save(dialog)).toBeDisabled();
    await act(async () => release(json({ shop: { ...shop, app_secret: 'TEST_SERVER_SECRET' } })));
    await screen.findByText(savedApp); expect(screen.queryByRole('dialog')).toBeNull();
    dialog = await openApp(user);
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('');
    expect(document.body.textContent).not.toContain('TEST_SERVER_SECRET');
  });

  it.each([422, 409, 403, 500])('retains App secrets and fields on HTTP %s without raw error rendering or replay', async (status) => {
    let commands = 0; api(async () => { commands++; return json({ detail: [{ loc: ['body', 'app_secret'], type: 'value_error', msg: 'TEST_ECHO_SECRET', input: 'TEST_ECHO_SECRET' }] }, status); });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user); await fillApp(user, dialog);
    await user.click(save(dialog)); await within(dialog).findByText(status === 500 ? /结果未确认/ : new RegExp('HTTP ' + status));
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret');
    expect(within(dialog).getByLabelText('App Key', { exact: true })).toHaveValue('TEST_key');
    expect(document.body.textContent).not.toContain('TEST_ECHO_SECRET'); expect(commands).toBe(1);
    expect(screen.queryByText(savedApp)).toBeNull();
  });

  it('invalidates a registration field-error snapshot so successive corrections can submit again', async () => {
    let writes = 0; api(async () => ++writes < 3 ? json({ detail: [{ loc: ['body', 'region'], type: 'string_too_long' }] }, 422) : json({ created: false, shop }));
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    const field = within(dialog).getByRole('textbox', { name: '区域' }); await user.type(field, 'VN');
    await user.click(save(dialog)); await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid();
    for (const value of ['TH', 'VN']) {
      await user.clear(field); await user.type(field, value); expect(field).toBeValid();
      expect(within(dialog).getByRole('textbox', { name: '店铺 ID' })).toHaveValue(shop.shop_id);
      await user.click(save(dialog));
      if (writes < 3) { await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid(); }
    }
    await screen.findByText('店铺信息已保存'); expect(writes).toBe(3);
  });

  it.each(['2026-10-24', ''])('edits only the opening date by internal PK, including explicit clear-to-null %s', async (openedDate) => {
    const commands: Command[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ shop: { ...shop, opened_date: openedDate || null } }); });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '编辑信息' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByRole('textbox', { name: '店铺名称' })).toBeNull();
    expect(within(dialog).queryByRole('textbox', { name: '区域' })).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('开店日期', { exact: true }), { target: { value: openedDate } });
    await user.click(save(dialog)); await screen.findByText('店铺信息已保存');
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/admin/shops/41');
    expect(commands[0].init.method).toBe('PATCH');
    expect(JSON.parse(commands[0].init.body as string)).toEqual({ opened_date: openedDate || null });
  });

  it('mints OAuth only for an explicit target shop and copies only on a separate user action', async () => {
    const commands: Command[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ authorize_url: 'https://services.tiktokshop.com/open/authorize?state=TEST_one', state_expires_at: '2026-10-24T20:00:00+00:00' }); });
    const user = userEvent.setup(); const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    await mount(); await screen.findByText(shop.shop_id); expect(commands).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '获取授权链接' }));
    const dialog = await screen.findByRole('dialog');
    const link = within(dialog).getByRole('link', { name: '打开授权页面' });
    expect(link).toHaveAttribute('href', 'https://services.tiktokshop.com/open/authorize?state=TEST_one');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer'); expect(copy).not.toHaveBeenCalled();
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/oauth/tiktok/authorize?format=json&shop_pk=41');
    expect(commands[0].init.method).toBe('GET');
    expect(within(dialog).getByText(/店铺 .*Service TEST_svc/)).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: '复制链接' }));
    await within(dialog).findByText('授权链接已复制'); expect(copy).toHaveBeenCalledWith('https://services.tiktokshop.com/open/authorize?state=TEST_one');
  });

  it('clears explicitly discarded App input and keeps readwrite App management available', async () => {
    api(async () => json({}, 403)); const user = userEvent.setup(); await mount(); let dialog = await openApp(user);
    expect(within(dialog).getByText(/配置仅用于当前店铺/)).toBeVisible();
    await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_cancel');
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    let confirmation = screen.getByRole('dialog', { name: '放弃本次尚未保存的修改？' });
    await user.click(within(confirmation).getByRole('button', { name: '继续编辑' }));
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_cancel');
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    confirmation = screen.getByRole('dialog', { name: '放弃本次尚未保存的修改？' });
    await user.click(within(confirmation).getByRole('button', { name: '放弃修改' }));
    dialog = await openApp(user); expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('');
  });

  it('keeps cancellation and App draft editing available during Retry-After without replaying', async () => {
    let commands = 0; api(async () => { commands++; return json({}, 429, { 'Retry-After': '600' }); });
    const user = userEvent.setup(); const view = await mount(); const dialog = await openApp(user); await fillApp(user, dialog);
    await user.click(save(dialog)); await within(dialog).findByText(/HTTP 429/);
    expect(save(dialog)).toBeDisabled(); expect(within(dialog).getByRole('button', { name: '取消' })).toBeEnabled();
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toBeEnabled();
    expect(commands).toBe(1); view.unmount();
  });

  it('fails closed if write hints disappear while a draft is open without trapping cancellation', async () => {
    let commands = 0; api(async () => { commands++; return json({ created: true, shop }); });
    const user = userEvent.setup(); const view = await mount(); const dialog = await openRegister(user);
    const { default: Shops } = await import(modulePath); view.rerender(<Shops bootstrap={{ ...props.bootstrap, roleCapabilities: undefined }} />);
    expect(save(dialog)).toBeDisabled(); expect(within(dialog).getByRole('button', { name: '取消' })).toBeEnabled(); expect(commands).toBe(0);
  });

  it('clears correlated App pair errors when the draft returns to keeping the existing pair', async () => {
    const commands: Command[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ shop }); });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user);
    const key = within(dialog).getByLabelText('App Key', { exact: true }); const secret = within(dialog).getByLabelText('App Secret', { exact: true });
    await user.type(key, 'TEST_half'); await user.click(save(dialog));
    await within(dialog).findByText('请成对填写 App Key 和 App Secret'); expect(secret).toBeInvalid(); expect(commands).toHaveLength(0);
    await user.clear(key); expect(secret).toBeValid();
    await user.click(save(dialog)); await screen.findByText(savedApp);
    expect(commands).toHaveLength(1); expect(JSON.parse(commands[0].init.body as string)).toEqual({ service_id: 'TEST_svc' });
  });

  it('keeps an incomplete App pair local without submitting or losing its draft', async () => {
    let commands = 0; api(async () => { commands++; return json({ shop }); });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user);
    await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_half'); await user.click(save(dialog));
    await within(dialog).findByText('请成对填写 App Key 和 App Secret');
    expect(within(dialog).getByLabelText('App Key', { exact: true })).toHaveValue('TEST_half'); expect(commands).toBe(0);
  });

  it.each(['network', 'malformed-success'])('retains App input and never replays an unknown %s result', async (mode) => {
    let commands = 0; api(async () => { commands++; if (mode === 'network') throw new TypeError('TEST_raw_secret'); return json({ shop: null }); });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user); await fillApp(user, dialog, 'TEST_keep');
    await user.click(save(dialog)); await within(dialog).findByText(/结果未确认/);
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_keep');
    expect(document.body.textContent).not.toContain('TEST_raw_secret'); expect(commands).toBe(1);
  });

  it('requires an explicit draft-preserving GET check before another uncertain registration command', async () => {
    let writes = 0; const fetcher = api(async () => { writes++; return json({}, 500); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    await user.click(save(dialog)); await within(dialog).findByText(/结果未确认/);
    const name = within(dialog).getByLabelText('店铺名称', { exact: true }); await user.type(name, 'TEST_retained');
    expect(save(dialog)).toBeDisabled(); fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); expect(writes).toBe(1);
    const check = within(dialog).getByRole('button', { name: '读取当前记录核对' });
    for (let step = 0; step < 20 && document.activeElement !== check; step++) await user.tab();
    expect(check).toHaveFocus(); await user.keyboard('{Enter}');
    await within(dialog).findByText(/已读取当前记录/);
    expect(name).toHaveValue('TEST_retained'); expect(within(dialog).getByText('查询当前记录不能证明原请求的提交结果。')).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url.includes('&q=' + shop.shop_id))).toBe(true); expect(writes).toBe(1);
    const retry = within(dialog).getByRole('button', { name: '核对后重新保存' }); expect(retry).toBeEnabled();
    await user.click(retry); await within(dialog).findByRole('button', { name: '保存' }); expect(writes).toBe(2); expect(name).toHaveValue('TEST_retained');
  });

  it.each(['wrong-identity', 'wrong-shop', 'malformed', 'denied'])('keeps uncertain App resubmission blocked after a %s verification GET', async (mode) => {
    let writes = 0; let checks = 0; api(async (_url, init) => {
      if (init.method === 'GET') {
        checks++;
        return mode === 'denied' ? json({}, 403) : json(mode === 'malformed' ? null : mode === 'wrong-shop' ? { ...shop, shop_id: 'TEST_other_shop' } : { ...shop, id: 42 });
      }
      writes++; return json({}, 500);
    });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user); await fillApp(user, dialog, 'TEST_preserve');
    await user.click(save(dialog)); await within(dialog).findByText(/结果未确认/);
    await user.click(within(dialog).getByRole('button', { name: '读取当前记录核对' })); await within(dialog).findByText(/尚未完成核对/);
    expect(save(dialog)).toBeDisabled(); expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_preserve');
    expect(document.body.textContent).not.toContain('TEST_other_shop');
    expect(writes).toBe(1); expect(checks).toBe(1); expect(screen.queryByText(savedApp)).toBeNull();
  });

  it('preserves entered App secrets through a successful public check without automatic resubmission', async () => {
    let writes = 0; let checks = 0; api(async (_url, init) => {
      if (init.method === 'GET') { checks++; return json(shop); }
      writes++; return json({}, 500);
    });
    const user = userEvent.setup(); await mount(); const dialog = await openApp(user); await fillApp(user, dialog);
    await user.click(save(dialog)); await within(dialog).findByText(/结果未确认/);
    await user.click(within(dialog).getByRole('button', { name: '读取当前记录核对' })); await within(dialog).findByText(/已读取当前记录/);
    expect(within(dialog).getByText('读取结果不会显示密钥；再次保存会重新写入配置。')).toBeVisible();
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret');
    expect(within(dialog).getByRole('button', { name: '核对后重新保存' })).toBeEnabled();
    expect(writes).toBe(1); expect(checks).toBe(1); expect(screen.queryByText(savedApp)).toBeNull();
  });

  it('does not let a confirmed registration bypass a list Retry-After and keeps one mutation', async () => {
    let reads = 0; let writes = 0; let releaseList!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/commerce/channel-accounts?')) { reads++; return reads === 1 ? new Promise<Response>((resolve) => { releaseList = resolve; }) : json([shop]); }
      writes++; return json({ created: true, shop });
    }));
    const user = userEvent.setup(); const view = await mount();
    await user.click(screen.getByRole('button', { name: '手工补录' })); const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('店铺 ID', { exact: true }), { target: { value: shop.shop_id } });
    vi.useFakeTimers(); await act(async () => { releaseList(json({}, 429, { 'Retry-After': '600' })); });
    fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); await act(async () => {});
    expect(writes).toBe(1); expect(reads).toBe(1); expect(screen.getByRole('button', { name: '刷新店铺' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('button', { name: '刷新店铺' })).toBeEnabled(); expect(writes).toBe(1); expect(reads).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新店铺' })); await act(async () => {}); expect(reads).toBe(2);
    view.unmount();
  });

  it('keeps the read fence on unknown-result checks and cancels cooldown work on unmount', async () => {
    let reads = 0; let writes = 0; let releaseList!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/commerce/channel-accounts?')) { reads++; return reads === 1 ? new Promise<Response>((resolve) => { releaseList = resolve; }) : json([shop]); }
      writes++; return json({}, 500);
    }));
    const user = userEvent.setup(); const view = await mount(); await user.click(screen.getByRole('button', { name: '手工补录' }));
    const dialog = screen.getByRole('dialog'); fireEvent.change(within(dialog).getByLabelText('店铺 ID', { exact: true }), { target: { value: shop.shop_id } });
    vi.useFakeTimers(); await act(async () => { releaseList(json({}, 429, { 'Retry-After': '600' })); });
    fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); await act(async () => {});
    const check = within(dialog).getByRole('button', { name: '读取当前记录核对' }); expect(check).toBeDisabled(); fireEvent.click(check); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(check).toBeDisabled(); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(check).toBeEnabled(); expect(reads).toBe(1);
    fireEvent.click(check); await act(async () => {});
    expect(reads).toBeGreaterThan(1); expect(writes).toBe(1); expect(within(dialog).getByRole('button', { name: '核对后重新保存' })).toBeEnabled();
    const beforeUnmount = reads; view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(600000); }); expect(reads).toBe(beforeUnmount);
  });

  it('does not recover or remint a lost OAuth state until an explicit target-shop check and retry', async () => {
    let mints = 0; let checks = 0; api(async (url) => {
      if (url.includes('/oauth/')) { mints++; return mints === 1 ? json({}, 500) : json({ authorize_url: 'https://services.tiktokshop.com/open/authorize?state=TEST_new', state_expires_at: '2026-10-24T20:00:00Z' }); }
      checks++; return json(shop);
    });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id);
    await user.click(screen.getByRole('button', { name: '获取授权链接' })); await screen.findByText(/结果未确认/);
    expect(screen.getByRole('button', { name: '获取授权链接' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '读取当前记录核对' })); await screen.findByText(/已读取当前记录/);
    expect(screen.getByText('重新获取会生成新的授权链接。')).toBeVisible(); expect(mints).toBe(1); expect(checks).toBe(1);
    await user.click(screen.getByRole('button', { name: '获取授权链接' }));
    await screen.findByRole('link', { name: '打开授权页面' }); expect(mints).toBe(2);
  });
});
