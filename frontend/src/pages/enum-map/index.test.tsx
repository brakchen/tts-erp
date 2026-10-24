import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
const modulePath = './index';
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'enum-map', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: true } } };
const row = { id: 41, enum_type: 'order_status', enum_value: 'TEST_A', label_zh: 'TEST_旧标签', sort_order: 9 };
const rows = [row, { ...row, id: 42, enum_value: 'TEST_B', sort_order: 1 }, { ...row, id: 43, enum_type: '__proto__', enum_value: 'TEST_UNKNOWN' }];
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function api(command: (url: string, init: RequestInit) => Promise<Response>, data = rows) {
  const fetcher = vi.fn((url: string, init: RequestInit) => init.method === 'GET' && url.endsWith('/enum-map/list') ? Promise.resolve(json(data)) : command(url, init));
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
async function mount(bootstrap = props.bootstrap) { const { default: EnumMap } = await import(modulePath); return render(<EnumMap bootstrap={bootstrap} />); }
async function edit(user: ReturnType<typeof userEvent.setup>) {
  const cell = await screen.findByText('TEST_A'); await user.click(within(cell.closest('tr')!).getByRole('button', { name: '编辑映射' })); return screen.getByRole('dialog');
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('enum content', () => {
  it.each([undefined, { readwrite: true, admin: false }, { readwrite: false, admin: false }])('fails closed without exact admin capability %#', async (roleCapabilities) => {
    const fetcher = api(async () => { throw new Error('TEST command forbidden'); }); await mount({ ...props.bootstrap, roleCapabilities }); await screen.findByText('TEST_A');
    expect(screen.getByRole('button', { name: '新增映射' })).toBeDisabled(); expect(screen.queryByRole('button', { name: '编辑映射' })).toBeNull(); expect(screen.queryByRole('button', { name: '删除映射' })).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('preserves backend order and seven known/unknown/empty categories without client sorting', async () => {
    const fetcher = api(async () => { throw new Error('TEST no write'); }); const user = userEvent.setup(); await mount(); await screen.findByText('TEST_A');
    const gridRows = within(screen.getByRole('grid', { name: '枚举映射' })).getAllByRole('row'); expect(gridRows[1]).toHaveTextContent('TEST_A'); expect(gridRows[2]).toHaveTextContent('TEST_B');
    await user.click(screen.getByRole('button', { name: '订单状态（order_status） 分类' })); expect(screen.getAllByRole('option')).toHaveLength(8);
    await user.click(screen.getByRole('option', { name: '__proto__' })); expect(await screen.findByText('TEST_UNKNOWN')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '__proto__ 分类' })); await user.click(screen.getByRole('option', { name: '售后类型（case_type）' })); expect(await screen.findByText('该分类暂无映射')).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/config/enum-map/list');
  });
  it.each(['1x', '1.2', '-1', ''])('retains invalid sort draft and never truncates %s to a write', async (sort) => {
    let writes = 0; api(async () => { writes++; return json({ id: 41, action: 'updated' }); }); const user = userEvent.setup(); await mount(); const dialog = await edit(user);
    const input = within(dialog).getByRole('textbox', { name: '排序' }); await user.clear(input); if (sort) await user.type(input, sort);
    fireEvent.submit(within(dialog).getByRole('form', { name: '枚举表单' })); expect(await within(dialog).findByText(/排序必须是非负整数/)).toBeVisible(); expect(input).toHaveValue(sort); expect(writes).toBe(0);
  });
  it('submits one snake_case tuple upsert using the shared CSRF client, keeps English read-only and clears only after confirmed success', async () => {
    let release!: (response: Response) => void; const fetcher = api(async () => new Promise<Response>((resolve) => { release = resolve; })); const user = userEvent.setup(); await mount(); const dialog = await edit(user);
    expect(within(dialog).getByRole('textbox', { name: '英文值' })).toHaveAttribute('readonly'); await user.clear(within(dialog).getByRole('textbox', { name: '中文标签' })); await user.type(within(dialog).getByRole('textbox', { name: '中文标签' }), 'TEST_新标签');
    const form = within(dialog).getByRole('form', { name: '枚举表单' }); fireEvent.submit(form); fireEvent.submit(form);
    const commands = fetcher.mock.calls.filter(([, init]) => init.method !== 'GET'); expect(commands).toHaveLength(1); expect(commands[0][0]).toBe('/tts/v2/config/enum-map');
    expect(JSON.parse(String(commands[0][1].body))).toEqual({ enum_type: 'order_status', enum_value: 'TEST_A', label_zh: 'TEST_新标签', sort_order: 9 }); expect(new Headers(commands[0][1].headers).get('X-Requested-With')).toBe('tts-erp');
    await act(async () => { release(json({ id: 41, action: 'updated' })); }); expect(await screen.findByText('映射保存已确认')).toBeVisible(); expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('opens a specific native delete confirmation, allows cancel without a request and preserves rejected guard context', async () => {
    let writes = 0; api(async () => { writes++; return json({ detail: 'TEST_raw_guard_secret' }, 403); }); const user = userEvent.setup(); await mount(); const cell = await screen.findByText('TEST_A');
    const trigger = within(cell.closest('tr')!).getByRole('button', { name: '删除映射' }); await user.click(trigger); let dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/order_status.*TEST_A/)).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: '取消' })); expect(writes).toBe(0); await user.click(trigger); dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认删除' })); expect(await within(dialog).findByText(/权限不足或安全检查未通过/)).toBeVisible(); expect(document.body.textContent).not.toContain('TEST_raw_guard_secret'); expect(writes).toBe(1); expect(screen.queryByText('映射删除已确认')).toBeNull();
  });
  it('requires a matching delete response and treats mismatched success as uncertain', async () => {
    const fetcher = api(async () => json({ deleted: 99 })); const user = userEvent.setup(); await mount(); const cell = await screen.findByText('TEST_A'); await user.click(within(cell.closest('tr')!).getByRole('button', { name: '删除映射' })); const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '确认删除' })); await within(dialog).findByText(/结果未确认/); expect(within(dialog).getByRole('button', { name: '确认删除' })).toBeDisabled(); expect(fetcher.mock.calls.filter(([, init]) => init.method === 'DELETE')).toHaveLength(1); expect(screen.queryByText('映射删除已确认')).toBeNull();
  });
  it('retains PUT draft and requires explicit GET facts before explicit uncertain resubmission', async () => {
    let writes = 0; const fetcher = api(async () => { writes++; return json({}, 500); }); const user = userEvent.setup(); await mount(); const dialog = await edit(user);
    await user.clear(within(dialog).getByRole('textbox', { name: '中文标签' })); await user.type(within(dialog).getByRole('textbox', { name: '中文标签' }), 'TEST_keep'); await user.click(within(dialog).getByRole('button', { name: '保存映射' })); await within(dialog).findByText(/结果未确认/);
    await user.type(within(dialog).getByRole('textbox', { name: '中文标签' }), '_edited');
    expect(within(dialog).getByRole('button', { name: '保存映射' })).toBeDisabled(); fireEvent.submit(within(dialog).getByRole('form', { name: '枚举表单' })); expect(writes).toBe(1);
    await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/公开列表已读取/); expect(within(dialog).getByRole('textbox', { name: '中文标签' })).toHaveValue('TEST_keep_edited'); expect(writes).toBe(1); expect(fetcher.mock.calls.filter(([, init]) => init.method === 'GET')).toHaveLength(2);
    expect(within(dialog).getByRole('button', { name: '核对后明确重新提交' })).toBeEnabled(); expect(screen.queryByText('映射保存已确认')).toBeNull();
  });
  it('revalidates corrected enum fields after every server 422 without suppressing the next rejection', async () => {
    let writes = 0; api(async () => ++writes < 3 ? json({ detail: [{ loc: ['body', 'label_zh'], type: 'string_too_long' }] }, 422) : json({ id: 41, action: 'updated' }));
    const user = userEvent.setup(); await mount(); const dialog = await edit(user); const field = within(dialog).getByRole('textbox', { name: '中文标签' });
    await user.click(within(dialog).getByRole('button', { name: '保存映射' })); await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid();
    for (const value of ['TEST_corrected_one', 'TEST_corrected_two']) {
      await user.clear(field); await user.type(field, value); expect(field).toBeValid();
      expect(within(dialog).getByRole('textbox', { name: '英文值' })).toHaveAttribute('readonly');
      await user.click(within(dialog).getByRole('button', { name: '保存映射' }));
      if (writes < 3) { await within(dialog).findByText(/HTTP 422/); expect(field).toBeInvalid(); }
    }
    await screen.findByText('映射保存已确认'); expect(writes).toBe(3);
  });
  it('retains safe field errors and cancels after capability downgrade without another write', async () => {
    let writes = 0; api(async () => { writes++; return json({ detail: [{ loc: ['body', 'label_zh'], type: 'string_too_long', msg: 'TEST_raw', input: 'TEST_secret' }] }, 422); }); const user = userEvent.setup(); const view = await mount(); const dialog = await edit(user);
    await user.click(within(dialog).getByRole('button', { name: '保存映射' })); expect(await within(dialog).findByText(/中文标签：内容过长/)).toBeVisible(); expect(document.body.textContent).not.toContain('TEST_secret');
    const { default: EnumMap } = await import(modulePath); view.rerender(<EnumMap bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: true, admin: false } }} />);
    expect(within(dialog).getByRole('button', { name: '保存映射' })).toBeDisabled(); fireEvent.submit(within(dialog).getByRole('form', { name: '枚举表单' })); expect(writes).toBe(1); await user.click(within(dialog).getByRole('button', { name: '取消' })); expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('keeps the selected unknown category empty after its last mapping is deleted instead of silently switching', async () => {
    let reads = 0; vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => init.method === 'GET' ? json(++reads === 1 ? [rows[2]] : []) : json({ deleted: 43 })));
    const user = userEvent.setup(); await mount(); await screen.findByText('TEST_UNKNOWN'); await user.click(screen.getByRole('button', { name: '删除映射' })); await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认删除' }));
    await screen.findByText('该分类暂无映射'); expect(screen.getByRole('button', { name: '__proto__ 分类' })).toBeVisible(); expect(reads).toBe(2);
  });
  it('keeps empty known categories usable and explains tuple upsert before creation', async () => {
    const fetcher = api(async () => json({ id: 50, action: 'created' }), []); const user = userEvent.setup(); await mount(); await screen.findByText('该分类暂无映射');
    await user.click(screen.getByRole('button', { name: '新增映射' })); const dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/相同分类和英文值.*更新/)).toBeVisible();
    await user.type(within(dialog).getByRole('textbox', { name: '英文值' }), 'TEST_NEW'); await user.type(within(dialog).getByRole('textbox', { name: '中文标签' }), 'TEST_新标签');
    await user.click(within(dialog).getByRole('button', { name: '保存映射' })); await screen.findByText('映射保存已确认');
    const call = fetcher.mock.calls.find(([, init]) => init.method === 'PUT')!; expect(JSON.parse(String(call[1].body))).toEqual({ enum_type: 'order_status', enum_value: 'TEST_NEW', label_zh: 'TEST_新标签', sort_order: 0 });
  });
  it('deletes exactly once using internal PK, shared CSRF and a matching confirmation response', async () => {
    let release!: (response: Response) => void; const fetcher = api(async () => new Promise<Response>((resolve) => { release = resolve; })); const user = userEvent.setup(); await mount(); const cell = await screen.findByText('TEST_A');
    await user.click(within(cell.closest('tr')!).getByRole('button', { name: '删除映射' })); const button = within(screen.getByRole('dialog')).getByRole('button', { name: '确认删除' }); fireEvent.click(button); fireEvent.click(button);
    const commands = fetcher.mock.calls.filter(([, init]) => init.method === 'DELETE'); expect(commands).toHaveLength(1); expect(commands[0][0]).toBe('/tts/v2/config/enum-map/41'); expect(new Headers(commands[0][1].headers).get('X-Requested-With')).toBe('tts-erp');
    await act(async () => { release(json({ deleted: 41 })); }); await screen.findByText('映射删除已确认'); expect(screen.queryByRole('dialog')).toBeNull();
  });
  it.each(['malformed', 'denied', 'network'])('does not unlock unknown PUT after a %s verification read', async (mode) => {
    let reads = 0; let writes = 0; vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method !== 'GET') { writes++; return json({}, 500); }
      reads++; if (reads === 1) return json(rows);
      if (mode === 'network') throw new TypeError('TEST_raw_network_secret'); return mode === 'denied' ? json({ detail: 'TEST_raw_denied' }, 403) : json(null);
    }));
    const user = userEvent.setup(); await mount(); const dialog = await edit(user); await user.click(within(dialog).getByRole('button', { name: '保存映射' })); await within(dialog).findByText(/结果未确认/);
    await user.click(within(dialog).getByRole('button', { name: '查询核对' })); await within(dialog).findByText(/核对未完成/); expect(within(dialog).getByRole('button', { name: '保存映射' })).toBeDisabled(); expect(within(dialog).getByRole('textbox', { name: '中文标签' })).toHaveValue(row.label_zh); expect(writes).toBe(1); expect(reads).toBe(2); expect(document.body.textContent).not.toMatch(/TEST_raw/);
  });
  it('keeps an unresolved command gated outside the modal after explicit cancel', async () => {
    let writes = 0; api(async () => { writes++; return json({}, 500); }); const user = userEvent.setup(); await mount(); const dialog = await edit(user);
    await user.click(within(dialog).getByRole('button', { name: '保存映射' })); await within(dialog).findByText(/结果未确认/); await user.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).toBeNull(); expect(screen.getByRole('button', { name: '新增映射' })).toBeDisabled(); await user.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/公开列表已读取/); expect(screen.getByRole('button', { name: '新增映射' })).toBeEnabled(); expect(writes).toBe(1);
  });
  it('keeps list Retry-After across a confirmed mutation and later manual refresh', async () => {
    let reads = 0; let writes = 0; let releaseList!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === 'GET') { reads++; return reads === 1 ? new Promise<Response>((resolve) => { releaseList = resolve; }) : json(rows); }
      writes++; return json({ id: 50, action: 'created' });
    }));
    const user = userEvent.setup(); const view = await mount(); await user.click(screen.getByRole('button', { name: '新增映射' })); const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '英文值' }), { target: { value: 'TEST_NEW' } }); fireEvent.change(within(dialog).getByRole('textbox', { name: '中文标签' }), { target: { value: 'TEST_新增' } });
    vi.useFakeTimers(); await act(async () => { releaseList(json({}, 429, { 'Retry-After': '600' })); }); fireEvent.submit(within(dialog).getByRole('form', { name: '枚举表单' })); await act(async () => {});
    expect(writes).toBe(1); expect(reads).toBe(1); expect(screen.getByRole('button', { name: '刷新映射' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(reads).toBe(1); await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); fireEvent.click(screen.getByRole('button', { name: '刷新映射' })); await act(async () => {}); expect(reads).toBe(2); expect(writes).toBe(1); view.unmount();
  });
});
