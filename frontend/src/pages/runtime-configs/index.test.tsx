import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { parse, stringify } from 'lossless-json';
import type { PageProps } from '../../shared/contracts';
import RuntimeConfigs from './index';
vi.mock('vanilla-jsoneditor', () => ({ Mode: { text: 'text', tree: 'tree' }, createJSONEditor: () => ({ updateProps: vi.fn(), destroy: async () => {} }) }));
const bootstrap: PageProps['bootstrap'] = { rootPath: '/tts', pageId: 'runtime-configs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: false } };
const initial = { configKey: 'test.flags', displayName: 'TEST 配置', publishedVersion: 1, hasDraft: true, updatedAt: '2026-01-01', publishedAt: '2026-01-01', retiredAt: null, jsonSchema: { type: 'object' }, draftPayload: { enabled: false }, draftVersion: 3, publishedPayload: { enabled: true } };
const secret = { name: 'test_token', ref: 'secret://test_token', fingerprint: 'TEST_fingerprint', updatedAt: '2026-01-01', retiredAt: null };
const json = (data: unknown, status = 200, headers = {}) => new Response(stringify(data), { status, headers });
function api(command?: (url: string, init: RequestInit) => Promise<Response>, get?: (url: string) => Response | undefined) {
  const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
    if (init?.method === 'GET') {
      const custom = get?.(url); if (custom) return custom;
      if (url.includes('/secrets')) return json({ items: [secret] });
      if (url.includes('/revisions')) return json({ items: [{ version: 1, payload: { enabled: true }, comment: 'TEST 历史', createdBy: 'TEST', createdAt: '2026-01-01' }] });
      return json(url.includes('/items/test.flags') ? initial : { items: [initial] });
    }
    if (command) return command(url, init);
    const body = parse(String(init?.body)) as { payload: Record<string, unknown>; expectedDraftVersion: number; sourceRevisionVersion?: number };
    if (url.endsWith('/preview')) return json({ configKey: initial.configKey, draftVersion: body.expectedDraftVersion, publishedVersion: 1, hasChanges: true, publishedText: '{"enabled":true}', candidateText: stringify(body.payload), sourceRevisionVersion: body.sourceRevisionVersion ?? null });
    if (url.endsWith('/draft')) return json({ ...initial, draftVersion: 4, draftPayload: body.payload });
    return json({ configKey: initial.configKey, publishedVersion: 2 });
  }); vi.stubGlobal('fetch', fetcher); return fetcher;
}
const writes = (f: ReturnType<typeof api>) => f.mock.calls.filter(([url, init]) => init?.method !== 'GET' && !url.endsWith('/preview'));
async function open() { fireEvent.click(await screen.findByRole('button', { name: '查看与修改 TEST 配置' })); await screen.findByRole('button', { name: '继续编辑草稿' }); }
async function edit() { await open(); fireEvent.click(screen.getByRole('button', { name: '继续编辑草稿' })); }
const text = () => screen.getByLabelText('配置内容 JSON 文本');
async function review() { fireEvent.click(screen.getByRole('button', { name: '检查并发布' })); await screen.findByRole('button', { name: '确认发布' }); }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('runtime config guided workflow', () => {
  it('shows published content before editing without unrelated forms', async () => {
    api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await open(); expect(screen.queryByLabelText('配置内容 JSON 文本')).toBeNull(); expect(screen.queryByRole('form')).toBeNull(); fireEvent.click(screen.getByRole('button', { name: '继续编辑草稿' })); expect(text()).not.toHaveAttribute('readonly'); expect(text()).toHaveValue('{\n  "enabled": false\n}');
  });
  it('checks without writes and confirms the exact lossless candidate in one command', async () => {
    const f = api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{"zero":0,"absent":null,"id":9007199254740993}' } }); await review(); expect(writes(f)).toHaveLength(0); expect(screen.getByLabelText('增加和删除的内容')).toHaveTextContent('9007199254740993');
    fireEvent.click(screen.getByRole('button', { name: '返回修改' })); expect(text()).toHaveValue('{"zero":0,"absent":null,"id":9007199254740993}'); await review(); fireEvent.change(screen.getByLabelText('发布说明'), { target: { value: 'TEST publish' } }); fireEvent.click(screen.getByRole('button', { name: '确认发布' }));
    await screen.findByText(/已发布为 v2/); expect(writes(f)).toHaveLength(1); const [url, init] = writes(f)[0]; expect(url).toBe('/tts/v2/config/runtime/items/test.flags/publish'); expect(init?.body).toContain('9007199254740993'); expect(init?.body).toContain('"expectedDraftVersion":3'); expect(init?.body).toContain('"expectedPublishedVersion":1'); expect(init?.body).toContain('TEST publish');
  });
  it('temporarily saves without publishing and checks the returned draft version', async () => {
    const f = api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{"TEST":0}' } }); fireEvent.click(screen.getByRole('button', { name: '暂存草稿' })); await screen.findByText(/草稿已保存，尚未发布/); expect(writes(f)).toHaveLength(1); await review(); expect(f.mock.calls.find(([url]) => url.endsWith('/preview'))?.[1]?.body).toContain('"expectedDraftVersion":4');
  });
  it('keeps invalid JSON and sends no preview', async () => {
    const f = api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{TEST invalid' } }); fireEvent.click(screen.getByRole('button', { name: '检查并发布' })); await screen.findByText(/必须是有效 JSON 对象/); expect(text()).toHaveValue('{TEST invalid'); expect(f.mock.calls.some(([url]) => url.endsWith('/preview'))).toBe(false);
  });
  it('does not leave after a failed save and allows explicit discard', async () => {
    api(async () => json({}, 422)); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{"TEST":true}' } }); fireEvent.click(screen.getByRole('button', { name: '返回配置概览' })); const dialog = await screen.findByRole('dialog'); fireEvent.click(within(dialog).getByRole('button', { name: '暂存并离开' })); await within(dialog).findByText(/HTTP 422/); expect(text()).toHaveValue('{"TEST":true}'); expect(screen.getByRole('dialog')).toBeVisible(); fireEvent.click(within(dialog).getByRole('button', { name: '放弃修改并离开' })); await screen.findByRole('button', { name: '继续编辑草稿' });
  });
  it('uses historical content as input rather than directly publishing', async () => {
    const f = api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await open(); fireEvent.click(screen.getByRole('button', { name: '查看发布历史' })); fireEvent.click(await screen.findByRole('button', { name: '使用 v1 内容' })); expect(text()).toHaveValue('{\n  "enabled": true\n}'); expect(writes(f)).toHaveLength(0); await review(); expect(f.mock.calls.find(([url]) => url.endsWith('/preview'))?.[1]?.body).toContain('"sourceRevisionVersion":1');
  });
  it('obeys the server no-change decision', async () => {
    api(async () => json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: false, publishedText: '{}', candidateText: '{}', sourceRevisionVersion: null })); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); await review(); expect(screen.getByRole('button', { name: '确认发布' })).toBeDisabled(); expect(screen.getByText(/无需重复发布/)).toBeVisible();
  });
  it('preserves input during conflict and compares latest facts', async () => {
    api(async () => json({ detail: 'published version conflict; reload before publishing' }, 409)); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{"TEST_mine":true}' } }); fireEvent.click(screen.getByRole('button', { name: '检查并发布' })); await screen.findByText(/版本已变化/); fireEvent.click(screen.getByRole('button', { name: '查看最新内容' })); await screen.findByRole('region', { name: '最新服务端内容' }); expect(text()).toHaveValue('{"TEST_mine":true}');
  });
  it('does not replay unknown writes and requires another review after verification', async () => {
    const f = api(async (url) => url.endsWith('/preview') ? json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText: '{}', sourceRevisionVersion: null }) : json({}, 500)); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); await review(); fireEvent.click(screen.getByRole('button', { name: '确认发布' })); await screen.findByText(/提交结果未确认/); fireEvent.click(screen.getByRole('button', { name: '查询核对' })); fireEvent.click(await screen.findByRole('button', { name: '我已核对，重新检查内容' })); await screen.findByRole('button', { name: '检查并发布' }); expect(screen.queryByRole('button', { name: '确认发布' })).toBeNull(); expect(writes(f)).toHaveLength(1);
  });
  it('prevents duplicate final commands', async () => {
    const f = api(async (url) => url.endsWith('/preview') ? json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText: '{}', sourceRevisionVersion: null }) : new Promise<Response>(() => {})); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); await review(); const b = screen.getByRole('button', { name: '确认发布' }); fireEvent.click(b); fireEvent.click(b); await waitFor(() => expect(writes(f)).toHaveLength(1)); expect(b).toBeDisabled();
  });
  it('keeps creation fields after failure', async () => {
    api(async () => json({}, 422)); render(<RuntimeConfigs bootstrap={bootstrap} />); fireEvent.click(await screen.findByRole('button', { name: '新建配置' })); fireEvent.change(screen.getByLabelText('配置键'), { target: { value: 'test.new' } }); fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: 'TEST new' } }); fireEvent.submit(screen.getByRole('form', { name: '创建配置' })); await screen.findByText(/HTTP 422/); expect(screen.getByLabelText('配置键')).toHaveValue('test.new');
  });
  it('locks credential names on update, confirms and preserves unknown plaintext input', async () => {
    const f = api(async () => json({}, 500)); render(<RuntimeConfigs bootstrap={bootstrap} />); fireEvent.click(await screen.findByRole('button', { name: '管理凭证' })); fireEvent.click(await screen.findByRole('button', { name: '更新凭证 test_token' })); expect(screen.getByLabelText('凭证名称')).toBeDisabled(); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret' } }); fireEvent.submit(screen.getByRole('form', { name: '凭证写入' })); fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认更新凭证' })); await screen.findByText(/提交结果未确认/); expect(screen.getByLabelText('凭证明文')).toHaveValue('TEST_secret'); expect(writes(f)).toHaveLength(1);
  });
  it('offers no plaintext draft on credential exit', async () => {
    api(); render(<RuntimeConfigs bootstrap={bootstrap} />); fireEvent.click(await screen.findByRole('button', { name: '管理凭证' })); fireEvent.click(await screen.findByRole('button', { name: '新增凭证' })); fireEvent.change(screen.getByLabelText('凭证明文'), { target: { value: 'TEST_secret' } }); fireEvent.click(screen.getByRole('button', { name: '返回配置列表' })); expect(within(await screen.findByRole('dialog')).queryByRole('button', { name: '暂存并离开' })).toBeNull();
  });
  it('makes no reads for readonly bootstrap', () => {
    const f = api(); render(<RuntimeConfigs bootstrap={{ ...bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />); expect(screen.getByText(/没有运行配置的读写权限/)).toBeVisible(); expect(f).not.toHaveBeenCalled();
  });
  it('guards native leave only for unsaved edits', async () => {
    api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); let e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); expect(e.defaultPrevented).toBe(false); fireEvent.change(text(), { target: { value: '{"TEST":1}' } }); await act(async () => {}); e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); expect(e.defaultPrevented).toBe(true);
  });
  it('preserves configuration filtering independently of the credential archive filter', async () => {
    api(); render(<RuntimeConfigs bootstrap={bootstrap} />); fireEvent.click(await screen.findByRole('button', { name: '管理凭证' })); const secretFilter = await screen.findByRole('checkbox', { name: '显示已归档凭证' });
    await waitFor(() => expect(secretFilter).toBeEnabled()); fireEvent.click(secretFilter); await waitFor(() => expect(secretFilter).toBeEnabled()); expect(secretFilter).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '返回配置列表' })); expect(await screen.findByRole('checkbox', { name: '显示已停用配置' })).not.toBeChecked();
  });
  it('actually discards input before reopening an editor', async () => {
    api(); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.change(text(), { target: { value: '{"TEST_discard":1}' } });
    await review(); fireEvent.change(screen.getByLabelText('发布说明'), { target: { value: 'TEST discard comment' } }); fireEvent.click(screen.getByRole('button', { name: '返回配置概览' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '放弃修改并离开' })); fireEvent.click(await screen.findByRole('button', { name: '继续编辑草稿' }));
    expect(text()).toHaveValue('{\n  "enabled": false\n}'); await review(); expect(screen.getByLabelText('发布说明')).toHaveValue('');
  });
  it('keeps independent create inputs during manual recovery even after visiting a config', async () => {
    api(async () => json({}, 500), (url) => url.endsWith('/items/test.new') ? json({ detail: 'runtime config not found' }, 404) : undefined); render(<RuntimeConfigs bootstrap={bootstrap} />); await open(); fireEvent.click(screen.getByRole('button', { name: '返回配置列表' })); fireEvent.click(await screen.findByRole('button', { name: '新建配置' }));
    fireEvent.change(screen.getByLabelText('配置键'), { target: { value: 'test.new' } }); fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: 'TEST new' } }); fireEvent.submit(screen.getByRole('form', { name: '创建配置' }));
    await screen.findByText(/提交结果未确认/); fireEvent.click(screen.getByRole('button', { name: '查询核对' })); fireEvent.click(await screen.findByRole('button', { name: '我已核对，重新检查内容' })); expect(screen.getByLabelText('配置键')).toHaveValue('test.new');
  });
  it('can leave a confirmed publication whose refresh failed without locking the list', async () => {
    let acknowledged = false;
    api(async (url) => {
      if (url.endsWith('/preview')) return json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText: '{}', sourceRevisionVersion: null });
      acknowledged = true; return json({ configKey: initial.configKey, publishedVersion: 2 });
    }, (url) => acknowledged && url.endsWith('/items/test.flags') ? json({}, 503) : undefined);
    render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); await review(); fireEvent.click(screen.getByRole('button', { name: '确认发布' })); await screen.findByText(/发布已确认；请先重试/);
    fireEvent.click(screen.getByRole('button', { name: '返回配置列表' })); await waitFor(() => expect(screen.getByRole('button', { name: '查看与修改 TEST 配置' })).toBeEnabled()); expect(screen.getByRole('button', { name: '新建配置' })).toBeEnabled();
  });
  it.each([
    ['runtime_schema_invalid', '配置内容不符合校验规则'], ['runtime_secret_unavailable', '引用的凭证不存在'], ['runtime_business_invalid', '配置未通过业务规则校验'], ['runtime_source_rollout_invalid', '此历史版本带有已退役'], ['runtime_number_invalid', '配置包含无法存储的数字'],
  ])('shows controlled corrective feedback for %s', async (code, message) => {
    api(async () => json({ detail: { code, rawSecret: 'TEST_do_not_show' } }, 422)); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit(); fireEvent.click(screen.getByRole('button', { name: '检查并发布' })); await screen.findByText(new RegExp(message)); expect(screen.queryByText('TEST_do_not_show')).toBeNull(); expect(text()).toHaveValue('{\n  "enabled": false\n}');
  });
  it('locks leaving throughout historical validation and the following save', async () => {
    let resolve!: (response: Response) => void;
    const validation = new Promise<Response>((done) => { resolve = done; });
    const f = api(async (url, init) => url.endsWith('/preview') ? validation : json({ ...initial, draftVersion: 4, draftPayload: (parse(String(init.body)) as { payload: unknown }).payload }));
    render(<RuntimeConfigs bootstrap={bootstrap} />); await open();
    fireEvent.click(screen.getByRole('button', { name: '查看发布历史' }));
    fireEvent.click(await screen.findByRole('button', { name: '使用 v1 内容' }));
    fireEvent.click(screen.getByRole('button', { name: '返回配置概览' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '暂存并离开' }));
    await waitFor(() => expect(f.mock.calls.some(([url]) => url.endsWith('/preview'))).toBe(true));
    const discard = within(dialog).getByRole('button', { name: '放弃修改并离开' });
    const locked = discard.hasAttribute('disabled');
    fireEvent.click(discard);
    await userEvent.keyboard('{Escape}');
    const remainedOpen = screen.queryByRole('dialog') !== null;
    await act(async () => resolve(json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: false, publishedText: '{"enabled":true}', candidateText: '{"enabled":true}', sourceRevisionVersion: 1 })));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(locked).toBe(true); expect(remainedOpen).toBe(true); expect(writes(f)).toHaveLength(1);
    expect(writes(f)[0][0]).toMatch(/\/draft$/);
  });
  it('retries the failed detail request rather than refreshing the list', async () => {
    let attempts = 0;
    const f = api(undefined, (url) => url.endsWith('/items/test.flags') && ++attempts === 1 ? json({}, 503) : undefined);
    render(<RuntimeConfigs bootstrap={bootstrap} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看与修改 TEST 配置' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试读取' }));
    await screen.findByRole('button', { name: '继续编辑草稿' });
    expect(attempts).toBe(2); expect(screen.queryByText(/读取或检查失败/)).toBeNull(); expect(writes(f)).toHaveLength(0);
  });
  it('retries schema inference and delivers its result to the creation form', async () => {
    let attempts = 0;
    const f = api(async () => ++attempts === 1 ? json({}, 503) : json({ jsonSchema: { type: 'object', title: 'TEST retry' } }));
    render(<RuntimeConfigs bootstrap={bootstrap} />);
    fireEvent.click(await screen.findByRole('button', { name: '新建配置' }));
    fireEvent.click(screen.getByText('高级选项：校验规则 Schema'));
    fireEvent.click(screen.getByRole('button', { name: '预览自动校验规则' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试读取' }));
    await screen.findByText('校验规则预览已更新');
    expect(screen.getByLabelText('新配置 Schema JSON 文本')).toHaveValue('{\n  "type": "object",\n  "title": "TEST retry"\n}');
    expect(attempts).toBe(2); expect(writes(f)).toHaveLength(0);
  });
  it('retries history loading and clears retries when leaving that context', async () => {
    let attempts = 0;
    const f = api(undefined, (url) => url.endsWith('/revisions') && ++attempts % 2 === 1 ? json({}, 503) : undefined);
    render(<RuntimeConfigs bootstrap={bootstrap} />); await open();
    fireEvent.click(screen.getByRole('button', { name: '查看发布历史' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试读取' }));
    await screen.findByRole('button', { name: '使用 v1 内容' });
    expect(attempts).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: '返回配置概览' }));
    fireEvent.click(screen.getByRole('button', { name: '查看发布历史' }));
    await screen.findByRole('button', { name: '重试读取' });
    fireEvent.click(screen.getByRole('button', { name: '返回配置列表' }));
    await screen.findByRole('button', { name: '新建配置' });
    expect(screen.queryByRole('button', { name: '重试读取' })).toBeNull(); expect(writes(f)).toHaveLength(0);
  });
  it('retries publication review but invalidates that retry when its input changes', async () => {
    let attempts = 0;
    const f = api(async (_url, init) => ++attempts % 2 === 1 ? json({}, 503) : json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText: stringify((parse(String(init.body)) as { payload: unknown }).payload), sourceRevisionVersion: null }));
    render(<RuntimeConfigs bootstrap={bootstrap} />); await edit();
    fireEvent.click(screen.getByRole('button', { name: '检查并发布' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试读取' }));
    await screen.findByRole('button', { name: '确认发布' });
    fireEvent.click(screen.getByRole('button', { name: '返回修改' }));
    fireEvent.click(screen.getByRole('button', { name: '检查并发布' }));
    await screen.findByRole('button', { name: '重试读取' });
    fireEvent.change(text(), { target: { value: '{"TEST_changed":true}' } });
    expect(screen.queryByRole('button', { name: '重试读取' })).toBeNull(); expect(writes(f)).toHaveLength(0);
  });
  it('keeps the list after verifying an abandoned creation instead of reopening an unrelated config', async () => {
    const f = api(async () => json({}, 500), (url) => url.endsWith('/items/test.new') ? json({ detail: 'runtime config not found' }, 404) : undefined);
    render(<RuntimeConfigs bootstrap={bootstrap} />); await open();
    fireEvent.click(screen.getByRole('button', { name: '返回配置列表' }));
    fireEvent.click(await screen.findByRole('button', { name: '新建配置' }));
    fireEvent.change(screen.getByLabelText('配置键'), { target: { value: 'test.new' } });
    fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: 'TEST new' } });
    fireEvent.submit(screen.getByRole('form', { name: '创建配置' })); await screen.findByText(/提交结果未确认/);
    fireEvent.click(screen.getByRole('button', { name: '返回配置列表' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '放弃修改并离开' }));
    fireEvent.click(await screen.findByRole('button', { name: '查询核对' }));
    fireEvent.click(await screen.findByRole('button', { name: '我已核对，重新检查内容' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '新建配置' })).toBeEnabled());
    expect(f.mock.calls.filter(([url]) => url.endsWith('/items/test.flags'))).toHaveLength(1);
    expect(screen.queryByLabelText('配置内容 JSON 文本')).toBeNull();
  });
  it('shows canonical acknowledged content and preserves publication success when refresh fails', async () => {
    const raw = '{"ratio":0.123456789012345678901}';
    const canonical = '{\n  "ratio": 0.12345678901234568\n}';
    let published = false; let failRefresh = true;
    const f = api(async (url) => {
      if (url.endsWith('/preview')) return json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText: canonical, sourceRevisionVersion: null });
      published = true; return json({ configKey: initial.configKey, publishedVersion: 2 });
    }, (url) => published && url.endsWith('/items/test.flags') ? failRefresh ? json({}, 503) : json({ ...initial, draftVersion: 4, publishedVersion: 2, hasDraft: false, draftPayload: null, publishedPayload: parse(canonical) }) : undefined);
    render(<RuntimeConfigs bootstrap={bootstrap} />); await edit();
    fireEvent.change(text(), { target: { value: raw } }); await review();
    fireEvent.click(screen.getByRole('button', { name: '确认发布' }));
    await screen.findByText(/发布已确认；请先重试/);
    expect(screen.getByLabelText('当前生效内容 JSON 文本')).toHaveValue(canonical);
    expect(screen.queryByText(/没有发出写入操作/)).toBeNull(); expect(screen.getByText(/已发布为 v2/)).toBeVisible();
    failRefresh = false; fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '修改配置' })).toBeEnabled());
    expect(screen.queryByText(/读取或检查失败|最新详情加载失败/)).toBeNull(); expect(writes(f)).toHaveLength(1);
  });
  it('allows uncertain save verification inside the leave dialog without losing input', async () => {
    api(async () => json({}, 500)); render(<RuntimeConfigs bootstrap={bootstrap} />); await edit();
    fireEvent.change(text(), { target: { value: '{"TEST_retained":true}' } });
    fireEvent.click(screen.getByRole('button', { name: '返回配置概览' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '暂存并离开' }));
    fireEvent.click(await within(dialog).findByRole('button', { name: '查询核对' }));
    fireEvent.click(await within(dialog).findByRole('button', { name: '我已核对，重新检查内容' }));
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '暂存并离开' })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }));
    expect(text()).toHaveValue('{"TEST_retained":true}');
  });
  it.each(['[]', 'TEST invalid JSON'])('refuses malformed canonical candidate %s before enabling publication', async (candidateText) => {
    const f = api(async () => json({ configKey: initial.configKey, draftVersion: 3, publishedVersion: 1, hasChanges: true, publishedText: '{}', candidateText, sourceRevisionVersion: null }));
    render(<RuntimeConfigs bootstrap={bootstrap} />); await edit();
    fireEvent.click(screen.getByRole('button', { name: '检查并发布' }));
    await screen.findByText(/读取或检查失败/);
    expect(screen.queryByRole('button', { name: '确认发布' })).toBeNull(); expect(writes(f)).toHaveLength(0);
    expect(text()).toHaveValue('{\n  "enabled": false\n}');
  });
});
