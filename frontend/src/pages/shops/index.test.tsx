import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
const modulePath = './index';
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'shops', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const shop = { id: 41, platform: 'tiktok', shop_id: '90071992547409931234567', account_name: 'TEST_name', region: null, seller_type: null, status: 'active', opened_date: null, credential_id: null, service_id: 'TEST_svc', app_credentials_configured: true };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
function api(command: (url: string, init: RequestInit) => Promise<Response>) {
  const fetcher = vi.fn((url: string, init: RequestInit) => url.includes('/v2/commerce/channel-accounts?') ? Promise.resolve(json([shop], 200, { 'X-Total-Count': '502' })) : command(url, init));
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
async function mount(bootstrap = props.bootstrap) { const { default: Shops } = await import(modulePath); return render(<Shops bootstrap={bootstrap} />); }
async function openRegister(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '注册店铺' }));
  return screen.getByRole('dialog');
}
async function fillRegister(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.type(within(dialog).getByRole('textbox', { name: '店铺 ID' }), shop.shop_id);
  await user.type(within(dialog).getByRole('textbox', { name: 'Service ID' }), 'TEST_svc');
  await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_key');
  await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_secret');
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('shops content', () => {
  it.each([undefined, { readwrite: false, admin: false }, { readwrite: false, admin: true }])('fails closed on missing/readonly hints without inventing a hierarchy %#', async (roleCapabilities) => {
    const fetcher = api(async () => { throw new Error('TEST no commands expected'); });
    await mount({ ...props.bootstrap, roleCapabilities }); await screen.findByText(shop.shop_id);
    for (const name of ['注册店铺', '编辑元信息', '配置 App', '获取授权链接']) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('preserves internal/external IDs, nullable dates, backend header total and separate App/OAuth facts', async () => {
    const fetcher = api(async () => { throw new Error('TEST no command'); }); await mount();
    expect(await screen.findByText(shop.shop_id)).toBeVisible(); expect(screen.getByText(/已注册总数：502/)).toBeVisible();
    expect(screen.getByText('已配置')).toBeVisible(); expect(screen.getByText('未关联')).toBeVisible(); expect(screen.getByText('仅插件')).toBeVisible();
    expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/commerce/channel-accounts?platform=tiktok&limit=500');
    expect(fetcher.mock.calls.some(([url]) => url.includes('/auth/me') || url.includes('/unregistered'))).toBe(false);
  });
  it('registers once under repeated submit, uses the CSRF client and clears the secret only on confirmed success', async () => {
    let release!: (r: Response) => void; const commands: { url: string; init: RequestInit }[] = [];
    api((url, init) => { commands.push({ url, init }); return new Promise((r) => { release = r; }); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    const form = within(dialog).getByRole('form', { name: '店铺表单' }); fireEvent.submit(form); fireEvent.submit(form);
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/admin/shops/register');
    expect(JSON.parse(commands[0].init.body as string)).toMatchObject({ shop_id: shop.shop_id, service_id: 'TEST_svc', app_key: 'TEST_key', app_secret: 'TEST_secret' });
    expect(new Headers(commands[0].init.headers).get('X-Requested-With')).toBe('tts-erp'); expect(commands[0].init.credentials).toBe('same-origin');
    expect(within(dialog).getByRole('button', { name: '保存店铺' })).toBeDisabled();
    await act(async () => release(json({ created: false, shop })));
    expect(await screen.findByText('店铺保存已确认')).toBeVisible(); expect(screen.queryByRole('dialog')).toBeNull();
  });
  it.each([422, 409, 403, 500])('retains entered secrets and fields on HTTP %s without raw error rendering or replay', async (status) => {
    let commands = 0; api(async () => { commands++; return json({ detail: [{ loc: ['body', 'app_secret'], type: 'value_error', msg: 'TEST_ECHO_SECRET', input: 'TEST_ECHO_SECRET' }] }, status); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' }));
    await screen.findByText(status === 500 ? /结果未确认/ : new RegExp(`HTTP ${status}`));
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret'); expect(document.body.textContent).not.toContain('TEST_ECHO_SECRET');
    expect(commands).toBe(1); expect(screen.queryByText('店铺保存已确认')).toBeNull();
  });
  it('invalidates the submitted field-error snapshot so successive 422 corrections can submit again', async () => {
    let writes = 0; api(async () => ++writes < 3 ? json({ detail: [{ loc: ['body', 'region'], type: 'string_too_long' }] }, 422) : json({ created: false, shop }));
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    const field = within(dialog).getByRole('textbox', { name: '区域' }); await user.type(field, 'VN');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid();
    for (const value of ['TH', 'VN']) {
      await user.clear(field); await user.type(field, value); expect(field).toBeValid();
      expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret');
      await user.click(within(dialog).getByRole('button', { name: '保存店铺' }));
      if (writes < 3) { await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid(); }
    }
    await screen.findByText('店铺保存已确认'); expect(writes).toBe(3);
  });
  it('PATCHes metadata by internal PK and leaves blank values as no change, not clear-to-null', async () => {
    const commands: { url: string; init: RequestInit }[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ shop: { ...shop, account_name: 'TEST_changed' } }); });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '编辑元信息' }));
    const dialog = screen.getByRole('dialog'); const name = within(dialog).getByRole('textbox', { name: '店铺名称' }); await user.clear(name); await user.type(name, 'TEST_changed');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText('店铺保存已确认');
    expect(commands[0].url).toBe('/tts/v2/admin/shops/41'); expect(commands[0].init.method).toBe('PATCH');
    expect(JSON.parse(commands[0].init.body as string)).toMatchObject({ account_name: 'TEST_changed' });
    expect(JSON.parse(commands[0].init.body as string)).not.toHaveProperty('region'); expect(JSON.parse(commands[0].init.body as string)).not.toHaveProperty('shop_id');
  });
  it('does not mint OAuth state on load; explicit GET accepts authorize_url without body.ok and attempts clipboard copy', async () => {
    const commands: { url: string; init: RequestInit }[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ authorize_url: 'https://services.tiktokshop.com/open/authorize?state=TEST_one', state_expires_at: '2026-10-24T20:00:00+00:00' }); });
    const user = userEvent.setup(); const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(); await mount(); await screen.findByText(shop.shop_id); expect(commands).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '获取授权链接' }));
    const link = await screen.findByRole('link', { name: '打开授权页' }); expect(link).toHaveAttribute('href', 'https://services.tiktokshop.com/open/authorize?state=TEST_one');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer'); expect(copy).toHaveBeenCalledTimes(1); expect(commands).toHaveLength(1);
    expect(commands[0].url).toBe('/tts/v2/oauth/tiktok/authorize?format=json&service_id=TEST_svc'); expect(commands[0].init.method).toBe('GET');
  });
  it('clears cancelled App input and gives readwrite users App management', async () => {
    api(async () => json({}, 403)); const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id);
    await user.click(screen.getByRole('button', { name: '配置 App' })); let dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/同一 Service ID/)).toBeVisible(); await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_cancel');
    await user.click(within(dialog).getByRole('button', { name: '取消' })); await user.click(screen.getByRole('button', { name: '配置 App' })); dialog = screen.getByRole('dialog');
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('');
  });
  it('keeps cancel and draft editing available during Retry-After without replaying the command', async () => {
    let commands = 0; api(async () => { commands++; return json({}, 429, { 'Retry-After': '600' }); });
    const user = userEvent.setup(); const view = await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText(/HTTP 429/);
    expect(within(dialog).getByRole('button', { name: '保存店铺' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeEnabled();
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toBeEnabled();
    expect(commands).toBe(1); view.unmount();
  });
  it('fails closed if write hints disappear while a draft is open, without trapping cancellation', async () => {
    let commands = 0; api(async () => { commands++; return json({ created: true, shop }); });
    const user = userEvent.setup(); const view = await mount(); const dialog = await openRegister(user);
    const { default: Shops } = await import(modulePath); view.rerender(<Shops bootstrap={{ ...props.bootstrap, roleCapabilities: undefined }} />);
    expect(within(dialog).getByRole('button', { name: '保存店铺' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeEnabled(); expect(commands).toBe(0);
  });
  it('saves App pairs for canonical readwrite hints without elevating to admin', async () => {
    const commands: { url: string; init: RequestInit }[] = []; api(async (url, init) => { commands.push({ url, init }); return json({ shop }); });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '配置 App' }));
    const dialog = screen.getByRole('dialog'); await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_key'); await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_pair');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText('店铺保存已确认');
    expect(commands).toHaveLength(1); expect(commands[0].url).toBe('/tts/v2/admin/shops/41');
    expect(JSON.parse(commands[0].init.body as string)).toEqual({ service_id: 'TEST_svc', app_key: 'TEST_key', app_secret: 'TEST_pair' });
  });
  it('invalidates correlated pair errors when editing the command back to a metadata-only registration', async () => {
    let writes = 0; api(async () => { writes++; return json({ created: false, shop }); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user);
    await user.type(within(dialog).getByLabelText('店铺 ID', { exact: true }), shop.shop_id);
    await user.type(within(dialog).getByLabelText('Service ID', { exact: true }), 'TEST_svc');
    const key = within(dialog).getByLabelText('App Key', { exact: true }); const secret = within(dialog).getByLabelText('App Secret', { exact: true });
    await user.type(key, 'TEST_half'); await user.click(within(dialog).getByRole('button', { name: '保存店铺' }));
    await within(dialog).findByText('请检查 App 凭证输入'); expect(secret).toBeInvalid(); expect(writes).toBe(0);
    await user.clear(key); expect(secret).toBeValid();
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText('店铺保存已确认'); expect(writes).toBe(1);
  });
  it('keeps an incomplete App pair local without submitting or losing the draft', async () => {
    let commands = 0; api(async () => { commands++; return json({ created: true, shop }); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user);
    await user.type(within(dialog).getByRole('textbox', { name: '店铺 ID' }), shop.shop_id); await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_half');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText('请检查 App 凭证输入');
    expect(within(dialog).getByLabelText('App Key', { exact: true })).toHaveValue('TEST_half'); expect(commands).toBe(0);
  });
  it.each(['network', 'malformed-success'])('retains App pair and never replays on an unknown %s result', async (mode) => {
    let commands = 0; api(async () => { commands++; if (mode === 'network') throw new TypeError('TEST_raw_secret'); return json({ shop: null }); });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '配置 App' }));
    const dialog = screen.getByRole('dialog'); await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_key'); await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_keep');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText(/结果未确认/);
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_keep'); expect(document.body.textContent).not.toContain('TEST_raw_secret'); expect(commands).toBe(1);
  });
  it('requires an explicit draft-preserving GET check before a second uncertain registration command', async () => {
    let writes = 0; const fetcher = api(async () => { writes++; return json({}, 500); });
    const user = userEvent.setup(); await mount(); const dialog = await openRegister(user); await fillRegister(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText(/结果未确认/);
    await user.type(within(dialog).getByLabelText('App Key', { exact: true }), '_edited');
    expect(within(dialog).getByRole('button', { name: '保存店铺' })).toBeDisabled();
    fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); expect(writes).toBe(1);
    const check = within(dialog).getByRole('button', { name: '查询核对' });
    for (let step = 0; step < 20 && document.activeElement !== check; step++) await user.tab();
    expect(check).toHaveFocus(); await user.keyboard('{Enter}');
    await within(dialog).findByText(/公开记录已读取/);
    expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret');
    expect(within(dialog).getByText(/无法确认.*密钥/)).toBeVisible(); expect(screen.queryByText('店铺保存已确认')).toBeNull();
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/commerce/channel-accounts?'))).toHaveLength(2);
    expect(writes).toBe(1); expect(within(dialog).getByRole('button', { name: '核对后明确重新提交' })).toBeEnabled();
    await user.click(within(dialog).getByRole('button', { name: '核对后明确重新提交' })); await within(dialog).findByRole('button', { name: '保存店铺' });
    expect(writes).toBe(2); expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_secret');
  });
  it.each(['wrong-identity', 'malformed', 'denied'])('does not enable uncertain App resubmission after a %s verification GET', async (mode) => {
    let writes = 0; let checks = 0; api(async (_url, init) => {
      if (init.method === 'GET') { checks++; return mode === 'denied' ? json({}, 403) : json(mode === 'malformed' ? null : { ...shop, id: 42 }); }
      writes++; return json({}, 500);
    });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '配置 App' }));
    const dialog = screen.getByRole('dialog'); await user.type(within(dialog).getByLabelText('App Key', { exact: true }), 'TEST_key'); await user.type(within(dialog).getByLabelText('App Secret', { exact: true }), 'TEST_preserve');
    await user.click(within(dialog).getByRole('button', { name: '保存店铺' })); await screen.findByText(/结果未确认/);
    await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/核对未完成/);
    expect(within(dialog).getByRole('button', { name: '保存店铺' })).toBeDisabled(); expect(within(dialog).getByLabelText('App Secret', { exact: true })).toHaveValue('TEST_preserve');
    expect(writes).toBe(1); expect(checks).toBe(1); expect(screen.queryByText('店铺保存已确认')).toBeNull();
  });
  it('does not let a confirmed registration bypass a list Retry-After and keeps one mutation only', async () => {
    let reads = 0; let writes = 0; let releaseList!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/commerce/channel-accounts?')) { reads++; return reads === 1 ? new Promise<Response>((resolve) => { releaseList = resolve; }) : json([shop]); }
      writes++; return json({ created: true, shop });
    }));
    const user = userEvent.setup(); const view = await mount();
    await user.click(screen.getByRole('button', { name: '注册店铺' })); const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '店铺 ID' }), { target: { value: shop.shop_id } });
    vi.useFakeTimers();
    await act(async () => { releaseList(json({}, 429, { 'Retry-After': '600' })); });
    fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); await act(async () => {});
    expect(writes).toBe(1); expect(reads).toBe(1); expect(screen.getByRole('button', { name: '刷新店铺' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    fireEvent.click(screen.getByRole('button', { name: '刷新店铺' })); await act(async () => {});
    expect(reads).toBe(2); expect(writes).toBe(1); view.unmount();
  });
  it('uses the same full read fence for an unknown-result check and releases it on unmount', async () => {
    let reads = 0; let writes = 0; let releaseList!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/commerce/channel-accounts?')) { reads++; return reads === 1 ? new Promise<Response>((resolve) => { releaseList = resolve; }) : json([shop]); }
      writes++; return json({}, 500);
    }));
    const user = userEvent.setup(); const view = await mount(); await user.click(screen.getByRole('button', { name: '注册店铺' }));
    const dialog = screen.getByRole('dialog'); fireEvent.change(within(dialog).getByRole('textbox', { name: '店铺 ID' }), { target: { value: shop.shop_id } });
    vi.useFakeTimers(); await act(async () => { releaseList(json({}, 429, { 'Retry-After': '600' })); });
    fireEvent.submit(within(dialog).getByRole('form', { name: '店铺表单' })); await act(async () => {});
    const check = within(dialog).getByRole('button', { name: '查询核对' }); expect(check).toBeDisabled(); fireEvent.click(check); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(check).toBeDisabled(); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(check).toBeEnabled();
    fireEvent.click(check); await act(async () => {}); expect(reads).toBe(2); expect(writes).toBe(1);
    expect(within(dialog).getByRole('button', { name: '核对后明确重新提交' })).toBeEnabled();
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(600000); }); expect(reads).toBe(2);
  });
  it('does not pretend to recover a lost OAuth state from shop facts or mint again before an explicit decision', async () => {
    let mints = 0; let checks = 0; api(async (url) => {
      if (url.includes('/oauth/')) { mints++; return mints === 1 ? json({}, 500) : json({ authorize_url: 'https://services.tiktokshop.com/open/authorize?state=TEST_new', state_expires_at: '2026-10-24T20:00:00Z' }); }
      checks++; return json(shop);
    });
    const user = userEvent.setup(); await mount(); await screen.findByText(shop.shop_id); await user.click(screen.getByRole('button', { name: '获取授权链接' })); await screen.findByText(/结果未确认/);
    expect(screen.getByRole('button', { name: '获取授权链接' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/公开记录已读取/);
    expect(screen.getByText(/无法恢复丢失的单次 OAuth state/)).toBeVisible(); expect(mints).toBe(1); expect(checks).toBe(1);
    await user.click(screen.getByRole('button', { name: '核对后重新生成授权链接' })); await screen.findByRole('link', { name: '打开授权页' }); expect(mints).toBe(2);
  });
});
