import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import RuntimeConfigs from './index';
// Command/DTO tests use the public editor lifecycle model. Real SDK rendering
// and lossless mode transitions are covered separately in JsonEditor.test.tsx.
vi.mock('vanilla-jsoneditor', () => ({
  Mode: { text: 'text', tree: 'tree' },
  createJSONEditor: () => ({ updateProps: vi.fn(), destroy: async () => {} }),
}));
// Recovery draft originally drove legacy duplicateSecretPUT (a38d1cee).
// Its final oracle must exercise the replacement content, never legacy code.
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'runtime-configs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
async function mount(writable = true) {
  return render(<RuntimeConfigs bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: writable, admin: false } }} />);
}
const item = { configKey: 'test.flags', displayName: 'TEST 配置', publishedVersion: 1, hasDraft: true, updatedAt: '2026-01-01', publishedAt: '2026-01-01', retiredAt: null };
const detail = { ...item, jsonSchema: { type: 'object' }, draftPayload: { enabled: false, zero: 0, absent: null }, draftVersion: 3, publishedPayload: {} };
const secret = { name: 'test_token', ref: 'secret://test_token', fingerprint: 'TEST_fingerprint', updatedAt: '2026-01-01', retiredAt: null };
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function api(command: (url: string, init: RequestInit) => Promise<Response>, get?: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
    if (init.method !== 'GET') return command(url, init);
    if (get) return get(url, init);
    if (url.includes('/secrets')) return json({ items: [secret] });
    if (url.includes('/revisions')) return json({ items: [{ version: 1, payload: {}, comment: 'TEST 历史', createdBy: 'browser-session', createdAt: '2026-01-01' }] });
    if (url.includes('/snapshot')) return json({ items: { 'test.flags': { version: 1, payload: { enabled: true } } } }, 200, { ETag: '"TEST_etag"' });
    if (url.includes('/items/test.flags')) return json(detail);
    return json({ items: [item] });
  }); vi.stubGlobal('fetch', fetcher); return fetcher;
}
const writes = (fetcher: ReturnType<typeof api>) => fetcher.mock.calls.filter(([, init]) => init?.method !== 'GET');
async function select() { fireEvent.click(await screen.findByRole('button', { name: '打开 test.flags' })); await screen.findByText('草稿版本 3'); }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('runtime-configs React content', () => {
  it('checks create-only current absence after an uncertain POST without replay or discarding the create draft', async () => {
    const fetcher = api(async () => json({}, 500), (url) => url.endsWith('/items/test.new') ? json({ detail: 'runtime config not found' }, 404) : json({ items: [] }));
    const view = await mount(); fireEvent.change(await screen.findByLabelText('新配置键'), { target: { value: 'test.new' } }); fireEvent.change(screen.getByLabelText('新配置名称'), { target: { value: 'TEST new' } });
    fireEvent.change(screen.getByLabelText('初始草稿 JSON 文本'), { target: { value: '{"ratio":0.123456789012345678901}' } });
    fireEvent.submit(screen.getByRole('form', { name: '创建配置' })); await screen.findByText(/结果未确认/);
    fireEvent.click(screen.getByRole('button', { name: '查询核对' }));
    await waitFor(() => expect(screen.queryByText(/当前未找到.*不能证明原创建是否提交/)).not.toBeNull(), { onTimeout: (error) => error });
    expect(screen.getByRole('button', { name: '创建草稿' })).toBeDisabled(); expect(writes(fetcher)).toHaveLength(1);
    expect(screen.getByLabelText('新配置键')).toHaveValue('test.new'); expect(screen.getByLabelText('初始草稿 JSON 文本')).toHaveValue('{"ratio":0.123456789012345678901}');
    fireEvent.click(screen.getByRole('button', { name: '我已核对，允许手动重新操作' }));
    expect(screen.getByRole('button', { name: '创建草稿' })).toBeEnabled(); expect(writes(fetcher)).toHaveLength(1);
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/tts/v2/config/runtime/items/test.new');
    view.unmount();
    let missing = false;
    api(async () => { missing = true; return json({}, 500); }, (url) => url.endsWith('/items/test.flags') ? missing ? json({ detail: 'runtime config not found' }, 404) : json(detail) : json({ items: [item] }));
    await mount(); await select(); fireEvent.click(screen.getByRole('button', { name: '保存草稿' })); await screen.findByText(/结果未确认/);
    fireEvent.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/核对失败/);
    expect(screen.queryByRole('button', { name: '我已核对，允许手动重新操作' })).toBeNull(); expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  });
  it('requires discard acknowledgement before create replaces dirty A, cancels without POST and keeps A when create fails', async () => {
    let count = 0; const fetcher = api(async () => ++count === 1 ? json({}, 422) : json({ ...detail, configKey: 'test.new', displayName: 'TEST B' }, 201));
    await mount(); await select(); fireEvent.change(screen.getByLabelText('草稿配置 JSON 文本'), { target: { value: '{"TEST_A":"unsaved"}' } });
    fireEvent.change(screen.getByLabelText('发布说明'), { target: { value: 'TEST A comment' } });
    fireEvent.change(screen.getByLabelText('新配置键'), { target: { value: 'test.new' } }); fireEvent.change(screen.getByLabelText('新配置名称'), { target: { value: 'TEST B' } });
    const form = screen.getByRole('form', { name: '创建配置' }); fireEvent.submit(form);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeNull(), { onTimeout: (error) => error });
    let dialog = screen.getByRole('dialog'); expect(dialog).toHaveTextContent('未保存'); expect(writes(fetcher)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
    expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveValue('{"TEST_A":"unsaved"}'); expect(screen.getByLabelText('新配置键')).toHaveValue('test.new');
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeEnabled();
    fireEvent.submit(form); dialog = screen.getByRole('dialog'); fireEvent.click(within(dialog).getByRole('button', { name: '确认创建并替换所选草稿' }));
    await screen.findByText(/HTTP 422/); expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveValue('{"TEST_A":"unsaved"}'); expect(screen.getByLabelText('发布说明')).toHaveValue('TEST A comment'); expect(screen.getByLabelText('新配置键')).toHaveValue('test.new');
    fireEvent.submit(form); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认创建并替换所选草稿' }));
    await screen.findByText('配置创建已确认'); expect(writes(fetcher)).toHaveLength(2); expect(screen.getByLabelText('新配置键')).toHaveValue('');
  });
  it('locks current draft/comment/actions while a deferred selection GET may replace them', async () => {
    let release!: (response: Response) => void;
    const fetcher = api(async () => json(detail), (url) => url.endsWith('/items/test.other') ? new Promise<Response>((resolve) => { release = resolve; }) : url.includes('/revisions') ? json({ items: [] }) : url.includes('/secrets') ? json({ items: [] }) : url.endsWith('/items/test.flags') ? json(detail) : json({ items: [item, { ...item, configKey: 'test.other' }] }));
    await mount(); await select(); const before = screen.getByLabelText('草稿配置 JSON 文本').getAttribute('readonly'); expect(before).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '打开 test.other' }));
    expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveAttribute('readonly'); expect(screen.getByLabelText('发布说明')).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled(); expect(screen.getByRole('button', { name: '发布新版本' })).toBeDisabled();
    const original = screen.getByLabelText('草稿配置 JSON 文本').textContent;
    fireEvent.change(screen.getByLabelText('草稿配置 JSON 文本'), { target: { value: '{"TEST_late_edit":true}' } });
    fireEvent.change(screen.getByLabelText('发布说明'), { target: { value: 'TEST late comment' } });
    expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveValue(original); expect(screen.getByLabelText('发布说明')).toHaveValue('');
    await act(async () => { release(json({ ...detail, configKey: 'test.other', displayName: 'TEST other', draftPayload: { TEST_B: true } })); });
    expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveValue('{\n  "TEST_B": true\n}'); expect(screen.getByLabelText('草稿配置 JSON 文本')).not.toHaveAttribute('readonly'); expect(screen.getByLabelText('发布说明')).toBeEnabled(); expect(writes(fetcher)).toHaveLength(0);
  });
  it('sends only one secret PUT while the first result remains pending', async () => {
    const fetcher = api(async () => new Promise<Response>(() => {})); await mount();
    fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } });
    fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret' } });
    const form = screen.getByRole('form', { name: '凭证写入' }); fireEvent.submit(form); fireEvent.submit(form);
    await waitFor(() => expect(writes(fetcher)).toHaveLength(1));
    expect(writes(fetcher)[0][0]).toBe('/tts/v2/config/runtime/secrets/test_token');
    expect(JSON.parse(String(writes(fetcher)[0][1]?.body))).toEqual({ value: 'TEST_secret' });
    expect(new Headers(writes(fetcher)[0][1]?.headers).get('X-Requested-With')).toBe('tts-erp');
    expect(document.body.textContent).not.toContain('TEST_secret');
  });
  it('lists real metadata and reads published snapshots only for readonly', async () => {
    const fetcher = api(async () => { throw Error('TEST forbidden'); }); await mount(false);
    expect(await screen.findByText('TEST 配置')).toBeVisible(); fireEvent.click(screen.getByRole('button', { name: '打开 test.flags' }));
    expect(await screen.findByText('已发布 v1')).toBeVisible(); expect(screen.queryByLabelText('凭证明文')).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => url.includes('/items/test.flags') || url.includes('/secrets'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '刷新已发布快照' }));
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.includes('/snapshot'))).toHaveLength(2));
    expect(new Headers(fetcher.mock.calls.at(-1)?.[1]?.headers).get('If-None-Match')).toBe('"TEST_etag"');
  });
  it('saves exact camelCase/version/payload with null zero and lossless integer then requires saved draft before publication', async () => {
    const fetcher = api(async (_url, init) => json({ ...detail, draftVersion: 4, draftPayload: JSON.parse(String(init.body)).payload })); await mount(); await select();
    fireEvent.change(screen.getByLabelText('草稿配置 JSON 文本'), { target: { value: '{"big":900719925474099312345,"zero":0,"nil":null}' } });
    expect(screen.getByRole('button', { name: '发布新版本' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await screen.findByText('草稿保存已确认'); expect(String(writes(fetcher)[0][1]?.body)).toContain('900719925474099312345');
    expect(String(writes(fetcher)[0][1]?.body)).toContain('"expectedDraftVersion":3');
    expect(String(writes(fetcher)[0][1]?.body)).toContain('"nil":null');
  });
  it('keeps invalid JSON text and never submits it', async () => {
    const fetcher = api(async () => json(detail)); await mount(); await select();
    fireEvent.change(screen.getByLabelText('草稿配置 JSON 文本'), { target: { value: '{invalid TEST' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' })); expect(await screen.findByText(/必须是有效 JSON 对象/)).toBeVisible();
    expect(screen.getByLabelText('草稿配置 JSON 文本')).toHaveValue('{invalid TEST'); expect(writes(fetcher)).toHaveLength(0);
  });
  it('creates with server-inferred schema and previews schema explicitly without changing custom draft', async () => {
    const fetcher = api(async (url) => url.endsWith('/preview') ? json({ jsonSchema: { type: 'object' } }) : json(detail, 201)); await mount();
    fireEvent.change(await screen.findByLabelText('新配置键'), { target: { value: 'test.flags' } }); fireEvent.change(screen.getByLabelText('新配置名称'), { target: { value: 'TEST 配置' } });
    fireEvent.click(screen.getByRole('button', { name: '推断 Schema' })); await screen.findByText('Schema 预览已更新');
    fireEvent.submit(screen.getByRole('form', { name: '创建配置' })); await screen.findByText('配置创建已确认');
    const body = JSON.parse(String(writes(fetcher).at(-1)?.[1]?.body)); expect(body).toEqual({ configKey: 'test.flags', displayName: 'TEST 配置', draftPayload: {} });
  });
  it('confirms publication and rollback with expectedDraftVersion and targetVersion', async () => {
    const fetcher = api(async (url) => json({ configKey: 'test.flags', publishedVersion: 2, ...(url.endsWith('/rollback') ? { rolledBackFrom: 1 } : {}) })); await mount(); await select();
    fireEvent.click(screen.getByRole('button', { name: '发布新版本' })); let dialog = screen.getByRole('dialog'); fireEvent.click(within(dialog).getByRole('button', { name: '确认执行' }));
    await screen.findByText('发布已确认'); expect(JSON.parse(String(writes(fetcher)[0][1]?.body))).toEqual({ expectedDraftVersion: 3, comment: null });
    fireEvent.click(await screen.findByRole('button', { name: '恢复 v1 为新版本' })); dialog = screen.getByRole('dialog'); fireEvent.click(within(dialog).getByRole('button', { name: '确认执行' }));
    await screen.findByText('历史恢复已确认'); expect(JSON.parse(String(writes(fetcher)[1][1]?.body))).toEqual({ expectedDraftVersion: 3, targetVersion: 1, comment: null });
  });
  it('preserves secret draft and gates uncertain writes behind usable explicit GET and explicit decision', async () => {
    const fetcher = api(async () => json({ detail: 'TEST_raw_secret' }, 500)); await mount();
    fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } }); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret' } });
    fireEvent.submit(screen.getByRole('form', { name: '凭证写入' })); await screen.findByText(/结果未确认/);
    fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret_edit' } });
    expect(screen.getByRole('button', { name: '加密保存凭证' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/元数据不能证明原凭证/);
    expect(writes(fetcher)).toHaveLength(1); expect(screen.getByLabelText('凭证明文')).toHaveValue('TEST_secret_edit');
    fireEvent.click(screen.getByRole('button', { name: '我已核对，允许手动重新操作' })); expect(screen.getByRole('button', { name: '加密保存凭证' })).toBeEnabled();
    expect(document.body.textContent).not.toContain('TEST_raw_secret');
  });
  it('shows failed verification as blocked and allows a later explicit GET retry', async () => {
    let reads = 0; const fetcher = api(async () => json({}, 500), (url) => { if (url.includes('/secrets')) return ++reads === 2 ? json({}, 503) : json({ items: [secret] }); return json({ items: [item] }); }); await mount();
    fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } }); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' }));
    await screen.findByText(/结果未确认/); fireEvent.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/核对失败/);
    expect(screen.getByRole('button', { name: '加密保存凭证' })).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/元数据不能证明原凭证/); expect(writes(fetcher)).toHaveLength(1);
  });
  it('preserves cached snapshot on a conditional 304 and reports a first uncached 304 as failure', async () => {
    let snapshots = 0;
    api(async () => json({}, 500), (url) => {
      if (url.endsWith('/snapshot')) return ++snapshots === 2 ? new Response(null, { status: 304 }) : json({ items: { 'test.flags': { version: 1, payload: { ratio: 'TEST_snapshot' } } } }, 200, { ETag: '\"TEST_etag\"' });
      return json({ items: [item] });
    });
    const view = await mount(false); fireEvent.click(await screen.findByRole('button', { name: '打开 test.flags' })); await screen.findByText('已发布 v1');
    fireEvent.click(screen.getByRole('button', { name: '刷新已发布快照' })); await screen.findByText('已发布快照未变化（304）');
    expect(screen.getByLabelText('已发布配置 JSON 文本')).toHaveValue('{\n  "ratio": "TEST_snapshot"\n}');
    view.unmount();
    api(async () => json({}, 500), (url) => url.endsWith('/snapshot') ? new Response(null, { status: 304 }) : json({ items: [item] }));
    await mount(false); fireEvent.click(await screen.findByRole('button', { name: '打开 test.flags' })); expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 304');
    expect(screen.queryByText('已发布 v1')).toBeNull();
  });
  it('executes confirmed item and secret retire/restore paths without deleting history', async () => {
    const fetcher = api(async (url) => json(url.includes('/secrets/') ? { name: 'test_token', status: url.endsWith('/retire') ? 'retired' : 'active' } : { configKey: 'test.flags', status: url.endsWith('/retire') ? 'retired' : 'active' }));
    const view = await mount(); await select(); fireEvent.click(screen.getByRole('button', { name: '归档配置' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('配置归档已确认');
    expect(writes(fetcher)[0][0]).toBe('/tts/v2/config/runtime/items/test.flags/retire');
    fireEvent.click(await screen.findByRole('button', { name: '归档凭证 test_token' })); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('凭证归档已确认');
    expect(writes(fetcher)[1][0]).toBe('/tts/v2/config/runtime/secrets/test_token/retire'); view.unmount();
    const retiredFetch = api(async (url) => json(url.includes('/secrets/') ? { name: 'test_token', status: 'active' } : { configKey: 'test.flags', status: 'active' }), (url) => url.includes('/secrets') ? json({ items: [{ ...secret, retiredAt: 'TEST_date' }] }) : url.includes('/revisions') ? json({ items: [] }) : url.includes('/items/test.flags') ? json({ ...detail, retiredAt: 'TEST_date' }) : json({ items: [{ ...item, retiredAt: 'TEST_date' }] }));
    await mount(); await select(); fireEvent.click(screen.getByRole('button', { name: '恢复配置' })); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('配置恢复已确认');
    fireEvent.click(await screen.findByRole('button', { name: '恢复凭证 test_token' })); fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认执行' })); await screen.findByText('凭证恢复已确认');
    expect(writes(retiredFetch).map(([url]) => url)).toEqual(['/tts/v2/config/runtime/items/test.flags/restore', '/tts/v2/config/runtime/secrets/test_token/restore']);
  });
  it('clears submitted field errors on a real draft edit but preserves full server cooldown across reads and writes', async () => {
    let count = 0; const fetcher = api(async () => ++count === 1 ? json({ detail: [{ loc: ['body', 'value'], type: 'string_too_long' }] }, 422) : json({}, 429, { 'Retry-After': '2147483648' }));
    await mount(); fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } }); const input = screen.getByLabelText('凭证明文');
    fireEvent.change(input, { target: { value: 'TEST_first' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' })); await screen.findByText(/HTTP 422/); expect(input).toBeInvalid();
    fireEvent.change(input, { target: { value: 'TEST_corrected' } }); expect(input).toBeValid(); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' })); await screen.findByText(/HTTP 429/);
    const requests = fetcher.mock.calls.length; fireEvent.change(input, { target: { value: 'TEST_during_cooldown' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' }));
    expect(screen.getByRole('button', { name: '加密保存凭证' })).toBeDisabled(); expect(screen.getByRole('button', { name: '刷新配置列表' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新配置列表' })); expect(fetcher.mock.calls).toHaveLength(requests); expect(writes(fetcher)).toHaveLength(2); expect(input).toHaveValue('TEST_during_cooldown');
  });
  it('does not claim success for mismatched secret acknowledgement', async () => {
    api(async () => json({ name: 'other', ref: 'secret://other', fingerprint: 'TEST' })); await mount();
    fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } }); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_keep' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' }));
    await screen.findByText(/结果未确认/); expect(screen.getByLabelText('凭证明文')).toHaveValue('TEST_keep');
  });
});
