import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import InterceptConfigs from './index';
import type { PageProps } from '../../shared/contracts';
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'intercept-configs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
const row = { id: 41, domain: 'TEST.example', endpoint: '/api/*', mode: 'whitelist', capture_headers: true, capture_body: false, description: null, tags: ['TEST_tag'], enabled: true, created_at: null, updated_at: null };
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function api(command: (url: string, init: RequestInit) => Promise<Response>) {
  const fetcher = vi.fn((url: string, init: RequestInit) => init.method === 'GET' && url.includes('/configs?') ? Promise.resolve(json({ configs: [row], total: 51 })) : command(url, init));
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
async function create(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '新增配置' })); const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByRole('textbox', { name: '域名' }), { target: { value: 'TEST.new' } });
  fireEvent.change(within(dialog).getByRole('textbox', { name: 'Endpoint' }), { target: { value: '/new/*' } }); return dialog;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('intercept-configs content', () => {
  it('retains a dirty create draft across cancel and refuses to overwrite it with row selection', async () => {
    api(async () => json({ config: row })); const user = userEvent.setup(); render(<InterceptConfigs {...props} />); const dialog = await create(user);
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    await user.click(screen.getByRole('button', { name: '详情与编辑' }));
    expect(screen.queryByRole('dialog')).toBeNull(); await user.click(screen.getByRole('button', { name: '恢复配置草稿' }));
    expect(within(screen.getByRole('dialog')).getByRole('textbox', { name: '域名' })).toHaveValue('TEST.new');
    expect(within(screen.getByRole('dialog')).getByRole('textbox', { name: 'Endpoint' })).toHaveValue('/new/*');
  });
  it.each([undefined, { readwrite: false, admin: false }, { readwrite: false, admin: true }])('fails closed on missing/readonly role hints %#', async (roleCapabilities) => {
    const fetcher = api(async () => { throw new Error('TEST no writes'); }); render(<InterceptConfigs bootstrap={{ ...props.bootstrap, roleCapabilities }} />); await screen.findByText(row.domain);
    for (const name of ['新增配置', '导入配置', '删除配置', '禁用配置']) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.getByRole('button', { name: '查看详情' })).toBeEnabled(); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('preserves exact filters/search wire, server total, page size and paging order', async () => {
    const fetcher = api(async () => json({})); const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain);
    expect(screen.getByText('共 51 条 · 已选 0 条')).toBeVisible(); await user.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('第 2 页 / 2 页'); expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/tts/v2/intercept/configs?limit=50&offset=50');
    fireEvent.change(screen.getByRole('textbox', { name: '筛选域名' }), { target: { value: ' TEST.example ' } }); fireEvent.change(screen.getByRole('combobox', { name: '筛选状态' }), { target: { value: 'false' } }); fireEvent.change(screen.getByRole('textbox', { name: '搜索' }), { target: { value: ' test ' } });
    await user.click(screen.getByRole('button', { name: '查询配置' })); await screen.findByText('第 1 页 / 2 页');
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/tts/v2/intercept/configs?domain=TEST.example&enabled=false&search=test&limit=50&offset=0'); expect(screen.getByText(/当前接口未实现搜索/)).toBeVisible();
  });
  it.each([403, 409, 422])('retains draft on known HTTP %s without raw error or replay', async (status) => {
    let writes = 0; api(async () => { writes++; return json({ detail: [{ loc: ['body', 'domain'], type: 'string_too_long', msg: 'TEST_RAW_SECRET', input: 'TEST_RAW_SECRET' }] }, status); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); const dialog = await create(user); await user.click(within(dialog).getByRole('button', { name: '保存配置' }));
    await within(dialog).findByText(new RegExp(`HTTP ${status}`)); expect(within(dialog).getByRole('textbox', { name: '域名' })).toHaveValue('TEST.new'); expect(document.body.textContent).not.toContain('TEST_RAW_SECRET'); expect(writes).toBe(1);
    if (status === 422) { expect(within(dialog).getByRole('textbox', { name: '域名' })).toBeInvalid(); fireEvent.change(within(dialog).getByRole('textbox', { name: '域名' }), { target: { value: 'TEST.corrected' } }); expect(within(dialog).getByRole('textbox', { name: '域名' })).toBeValid(); }
  });
  it('edits fresh detail with PUT and preserves untouched nulls, booleans and comma-containing tags', async () => {
    const calls: { url: string; init: RequestInit }[] = []; const fresh = { ...row, mode: 'blacklist', tags: ['TEST,a'], enabled: false };
    api(async (url, init) => { if (init.method === 'GET') return json({ config: fresh }); calls.push({ url, init }); return json({ config: fresh }); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '详情与编辑' })); const dialog = screen.getByRole('dialog');
    await within(dialog).findByRole('form', { name: '配置表单' }); expect(within(dialog).getByRole('radio', { name: '黑名单（完全跳过）' })).toBeChecked(); await user.click(within(dialog).getByRole('button', { name: '保存配置' })); await screen.findByText('配置保存已确认');
    expect(calls).toHaveLength(1); expect(calls[0].url).toBe('/tts/v2/intercept/configs/41'); expect(calls[0].init.method).toBe('PUT'); expect(JSON.parse(calls[0].init.body as string)).toEqual({ domain: row.domain, endpoint: row.endpoint, mode: 'blacklist', capture_headers: true, capture_body: false, description: null, tags: ['TEST,a'], enabled: false });
  });
  it('requires explicit toggle and single delete confirmation using actual envelopes', async () => {
    const calls: { url: string; init: RequestInit }[] = []; api(async (url, init) => { calls.push({ url, init }); return init.method === 'DELETE' ? json({ success: true }) : json({ config: { ...row, enabled: false } }); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '禁用配置' })); expect(calls).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('配置启停已确认'); expect(calls[0].url).toBe('/tts/v2/intercept/configs/41/toggle'); expect(calls[0].init.method).toBe('PATCH'); expect(JSON.parse(calls[0].init.body as string)).toEqual({ enabled: false });
    await user.click(screen.getByRole('button', { name: '删除配置' })); expect(calls).toHaveLength(1); await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('配置删除已确认'); expect(calls[1].init.method).toBe('DELETE'); expect(calls[1].url).toBe('/tts/v2/intercept/configs/41');
  });
  it.each(['enable', 'disable', 'delete'] as const)('freezes exact oversized numeric batch targets for %s and reports actual affected=0', async (action) => {
    const id = '9007199254740993123'; const calls: RequestInit[] = []; const names = { enable: '启用', disable: '禁用', delete: '删除' };
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => init.method === 'GET' ? new Response(`{"configs":[${JSON.stringify(row).replace('"id":41', `"id":${id}`)}],"total":1}`) : (calls.push(init), json({ affected: 0 }))));
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(id); await user.click(screen.getByRole('checkbox', { name: '选择本页全部' })); await user.click(screen.getByRole('button', { name: `批量${names[action]}` }));
    const dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(new RegExp(id))).toBeVisible(); expect(calls).toHaveLength(0); await user.click(within(dialog).getByRole('button', { name: '确认执行' })); await screen.findByText(`批量${names[action]}已确认：影响 0 条`);
    expect(calls[0].body).toBe(`{"action":"${action}","ids":[${id}]}`);
  });
  it('retains malformed import text, accepts array/envelope with defaults, and reports safe partial results', async () => {
    const calls: RequestInit[] = []; api(async (_url, init) => { calls.push(init); return json({ imported: 0, skipped: 1, errors: [{ index: 0, error: 'TEST_RAW_SECRET' }] }); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '导入配置' })); const dialog = screen.getByRole('dialog'); const text = within(dialog).getByRole('textbox', { name: '导入 JSON' });
    fireEvent.change(text, { target: { value: '{bad' } }); await user.click(within(dialog).getByRole('button', { name: '确认导入' })); await within(dialog).findByText(/导入格式无效/); expect(text).toHaveValue('{bad'); expect(calls).toHaveLength(0);
    fireEvent.change(text, { target: { value: JSON.stringify({ configs: [{ id: '9007199254740993123', domain: 'TEST.import', endpoint: '/import' }] }) } }); await user.click(within(dialog).getByRole('button', { name: '确认导入' })); await screen.findByText(/导入已确认：成功 0 条，跳过 1 条，错误 1 条/);
    expect(JSON.parse(calls[0].body as string)).toEqual({ configs: [{ domain: 'TEST.import', endpoint: '/import', mode: 'whitelist', capture_headers: true, capture_body: true, description: null, tags: [], enabled: true }] }); expect(document.body.textContent).not.toContain('TEST_RAW_SECRET');
  });
  it.each(['network', 'malformed', '204'])('gates unknown %s create until explicit bounded current-facts lookup; absence is not commit proof', async (kind) => {
    let writes = 0; let checks = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (init.method !== 'GET') { writes++; if (kind === 'network') throw new Error('TEST_RAW_SECRET'); if (kind === '204') return new Response(null, { status: 204 }); return json({}); }
      if (url.includes('domain=TEST.new')) { checks++; return json({ configs: [], total: 0 }); } return json({ configs: [row], total: 1 });
    }));
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); const dialog = await create(user); await user.click(within(dialog).getByRole('button', { name: '保存配置' })); await within(dialog).findByText(/结果未确认/);
    expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled(); fireEvent.change(within(dialog).getByRole('textbox', { name: '域名' }), { target: { value: 'TEST.changed' } }); expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled();
    await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/当前事实已读取/); expect(within(dialog).getByText(/当前存在或缺席均不能证明原提交结果/)).toBeVisible(); expect(checks).toBe(1); expect(writes).toBe(1); expect(within(dialog).getByRole('textbox', { name: '域名' })).toHaveValue('TEST.changed'); expect(within(dialog).getByRole('button', { name: '核对后明确重新提交' })).toBeEnabled(); expect(document.body.textContent).not.toContain('TEST_RAW_SECRET');
  });
  it('keeps unresolved recovery and draft accessible after cancel; 404 lookup allows explicit retry without claiming rollback', async () => {
    let writes = 0; api(async (_url, init) => { if (init.method === 'GET') return json({}, 404); writes++; return json({}, 500); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '删除配置' })); const dialog = screen.getByRole('dialog'); await user.click(within(dialog).getByRole('button', { name: '确认执行' })); await within(dialog).findByText(/结果未确认/); await user.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(screen.getByRole('button', { name: '新增配置' })).toBeDisabled(); await user.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/当前事实已读取/); expect(screen.getByText('当前未找到 ID：41')).toBeVisible(); expect(screen.getByRole('button', { name: '新增配置' })).toBeEnabled(); expect(writes).toBe(1);
  });
  it('exports full validated raw JSON with numeric ID tokens intact only on explicit click', async () => {
    const raw = `{"configs":[${JSON.stringify(row).replace('"id":41', '"id":9007199254740993123')}]}`; let exports = 0; let blob!: Blob;
    api(async () => { exports++; return new Response(raw); }); vi.stubGlobal('URL', URL); Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn((value: Blob) => { blob = value; return 'blob:TEST'; }) }); Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() }); const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); expect(exports).toBe(0); await user.click(screen.getByRole('button', { name: '导出全部配置' })); await screen.findByText(/全部配置已导出/); expect(exports).toBe(1); expect(click).toHaveBeenCalledTimes(1); expect(blob.type).toBe('application/json'); expect(blob.size).toBe(new TextEncoder().encode(raw).length); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:TEST');
  });
  it('keeps full command Retry-After through edits and reads; expiry enables only explicit retry', async () => {
    let writes = 0; let reads = 0;
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => { if (init.method === 'GET') { reads++; return json({ configs: [row], total: 1 }); } writes++; return json({}, 429, { 'Retry-After': '600' }); }));
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); let dialog = await create(user); vi.useFakeTimers(); fireEvent.submit(within(dialog).getByRole('form', { name: '配置表单' })); await act(async () => {});
    expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled(); fireEvent.change(within(dialog).getByRole('textbox', { name: '域名' }), { target: { value: 'TEST.edited' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' })); fireEvent.click(screen.getByRole('button', { name: '刷新配置' })); await act(async () => {}); expect(reads).toBe(2); fireEvent.click(screen.getByRole('button', { name: '恢复配置草稿' })); dialog = screen.getByRole('dialog'); expect(within(dialog).getByRole('textbox', { name: '域名' })).toHaveValue('TEST.edited'); expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(writes).toBe(1); expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(writes).toBe(1); expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeEnabled(); fireEvent.submit(within(dialog).getByRole('form', { name: '配置表单' })); await act(async () => {}); expect(writes).toBe(2);
  });
  it('does not shorten oversized read Retry-After on refresh or schedule an automatic request', async () => {
    let reads = 0; vi.stubGlobal('fetch', vi.fn(async () => { reads++; return json({}, 429, { 'Retry-After': '2147484' }); })); vi.useFakeTimers(); render(<InterceptConfigs {...props} />); await act(async () => {});
    expect(screen.getByRole('button', { name: '重试列表' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '刷新配置' })); expect(reads).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2147483647); }); expect(reads).toBe(1); expect(screen.getByRole('button', { name: '重试列表' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(353); }); expect(reads).toBe(1); expect(screen.getByRole('button', { name: '重试列表' })).toBeEnabled(); fireEvent.click(screen.getByRole('button', { name: '重试列表' })); await act(async () => {}); expect(reads).toBe(2);
  });
  it('keeps unknown creation gated after failed facts lookup while allowing draft edits and explicit check retry', async () => {
    let writes = 0; let checks = 0; vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (init.method !== 'GET') { writes++; return json({}, 502); } if (url.includes('domain=TEST.new')) { checks++; return checks === 1 ? json({ detail: 'TEST_RAW_SECRET' }, 403) : json({ configs: [], total: 0 }); } return json({ configs: [row], total: 1 });
    })); const user = userEvent.setup(); render(<InterceptConfigs {...props} />); const dialog = await create(user); await user.click(within(dialog).getByRole('button', { name: '保存配置' })); await within(dialog).findByText(/结果未确认/); await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/核对未完成/);
    fireEvent.change(within(dialog).getByRole('textbox', { name: '域名' }), { target: { value: 'TEST.edited' } }); expect(within(dialog).getByRole('button', { name: '保存配置' })).toBeDisabled(); expect(writes).toBe(1); expect(document.body.textContent).not.toContain('TEST_RAW_SECRET');
    await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/当前事实已读取/); expect(checks).toBe(2); expect(writes).toBe(1);
  });
  it('does not overwrite accepted create input when an aborted detail read resolves late', async () => {
    let release!: (r: Response) => void; api(() => new Promise((r) => { release = r; })); const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '详情与编辑' })); await user.click(screen.getByRole('button', { name: '关闭配置窗口' })); const dialog = await create(user);
    await act(async () => { release(json({ config: row })); }); expect(within(dialog).getByRole('textbox', { name: '域名' })).toHaveValue('TEST.new'); expect(within(dialog).getByRole('textbox', { name: 'Endpoint' })).toHaveValue('/new/*');
  });
  it('loads a JSON file into editable retained import text without auto-submission', async () => {
    let writes = 0; api(async () => { writes++; return json({}); }); const user = userEvent.setup(); render(<InterceptConfigs {...props} />); await screen.findByText(row.domain); await user.click(screen.getByRole('button', { name: '导入配置' })); const dialog = screen.getByRole('dialog');
    const file = new File(['TEST'], 'TEST.json', { type: 'application/json' }); Object.defineProperty(file, 'text', { value: async () => '[{"domain":"TEST.file","endpoint":"/file"}]' });
    await user.upload(within(dialog).getByLabelText('选择 JSON 文件'), file); const text = within(dialog).getByRole('textbox', { name: '导入 JSON' }); expect(text).toHaveValue('[{"domain":"TEST.file","endpoint":"/file"}]'); fireEvent.change(text, { target: { value: '{TEST.invalid' } }); await user.click(within(dialog).getByRole('button', { name: '取消' })); await user.click(screen.getByRole('button', { name: '导入配置' })); expect(screen.getByRole('textbox', { name: '导入 JSON' })).toHaveValue('{TEST.invalid'); expect(writes).toBe(0);
  });
  it('creates once using actual wire fields and CSRF client, accepts config without body.ok', async () => {
    const calls: { url: string; init: RequestInit }[] = []; let release!: (r: Response) => void;
    api((url, init) => { calls.push({ url, init }); return new Promise((r) => { release = r; }); });
    const user = userEvent.setup(); render(<InterceptConfigs {...props} />); const dialog = await create(user);
    const form = within(dialog).getByRole('form', { name: '配置表单' }); fireEvent.submit(form); fireEvent.submit(form);
    expect(calls).toHaveLength(1); expect(calls[0].url).toBe('/tts/v2/intercept/configs'); expect(calls[0].init.method).toBe('POST');
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ domain: 'TEST.new', endpoint: '/new/*', mode: 'whitelist', capture_headers: true, capture_body: true, description: null, tags: [], enabled: true });
    expect(new Headers(calls[0].init.headers).get('X-Requested-With')).toBe('tts-erp'); expect(calls[0].init.credentials).toBe('same-origin');
    await act(async () => release(json({ config: { ...row, domain: 'TEST.new', endpoint: '/new/*' } }, 201)));
    await screen.findByText('配置保存已确认'); expect(screen.queryByRole('dialog')).toBeNull();
  });
});
