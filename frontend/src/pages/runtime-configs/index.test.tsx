import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
// Recovery draft originally drove legacy duplicateSecretPUT (a38d1cee).
// Its final oracle must exercise the replacement content, never legacy code.
const modules = import.meta.glob('./index.tsx');
const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'runtime-configs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } } };
async function mount(writable = true) {
  const module = modules['./index.tsx'] ? await modules['./index.tsx']() as { default: React.ComponentType<PageProps> } : { default: () => null };
  return render(<module.default bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: writable, admin: false } }} />);
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
  it('does not claim success for mismatched secret acknowledgement', async () => {
    api(async () => json({ name: 'other', ref: 'secret://other', fingerprint: 'TEST' })); await mount();
    fireEvent.change(await screen.findByLabelText('凭证名称'), { target: { value: 'test_token' } }); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_keep' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' }));
    await screen.findByText(/结果未确认/); expect(screen.getByLabelText('凭证明文')).toHaveValue('TEST_keep');
  });
});
