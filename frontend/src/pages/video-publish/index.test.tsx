import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VideoPublish from './index';
import { decodeTicket } from './contracts';
import type { PageProps } from '../../shared/contracts';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'video-publish', navigation: [], releaseId: 'TEST' } };
const commandProps: PageProps = { bootstrap: { ...props.bootstrap, roleCapabilities: { readwrite: true, admin: false } } };
const id = '00000000-0000-4000-8000-000000000001';
const sessionId = '00000000-0000-4000-8000-000000000002';
const time = '2026-10-24T10:00:00Z';
const pollState = { running: false, cleaning: false, queued: true };
const target = { appName: 'TikTok', appPackage: 'com.test.app', deviceSerialMasked: 'TEST***', album: 'TEST_album' };
const config = { acceptedContentTypes: ['video/mp4'], acceptedExtensions: ['.mp4'], maxVideoBytes: 1234, maxCaptionCharacters: 4000, uploadUrlTtlSeconds: 900,
  target, worker: { status: 'ready', lastHeartbeatAt: time }, device: { status: 'locked', message: 'TEST_device_locked' }, artemis: { profile: 'pro', verificationLevel: 'strict' },
  workerTiming: { pollSeconds: 2, leaseSeconds: 30, heartbeatSeconds: 5 }, canWrite: true, writeBlockReason: null, serverTime: time };
const attempt = { sequenceNo: 1, kind: 'publish', kindLabel: '正式发布', artemisSessionId: sessionId, status: 'failed', stepsCount: 0,
  retryClassification: 'safe', retrySafe: false, error: 'TEST_attempt_error', startedAt: time, finishedAt: null };
const task = { taskId: id, clientRequestId: '00000000-0000-4000-8000-000000000003', rowVersion: 0, filename: 'TEST_video.mp4', objectFilename: 'TEST_object.mp4', sizeBytes: 0, captionPreview: 'TEST_caption',
  status: 'pending', statusLabel: 'TEST_server_status', stage: 'queued', stageLabel: 'TEST_server_stage', queuedAt: time, queuePosition: 1, operationalStage: 'queued', stageStartedAt: time,
  operationalStageStartedAt: time, cleanupNextAttemptAt: null, latestArtemisSessionId: sessionId, attemptCount: 1, publishAttemptCount: 1, verifyAttemptCount: 0,
  lastErrorCode: null, lastErrorMessage: null, createdAt: time, updatedAt: time, createdBy: 'user:TEST', startedAt: null,
  allowedActions: ['view', 'retry', 'edit_settings'], cleanupRetryableResources: [], currentAttempt: attempt, relatedPublishAttempt: attempt };
const detail = { ...task, contentType: 'video/mp4', caption: 'TEST_full_caption\n<script>TEST_plain</script>', paramsLockedAt: null, confirmation: null, target,
  object: { bucket: 'TEST_bucket', key: 'TEST_key', etag: null, deletedAt: null }, attempts: [attempt],
  cleanup: { device: { status: 'failed', error: 'TEST_cleanup_error' }, spool: { status: 'not_required', error: null } } };
const current = { tasks: [{ ...task, deviceSerialMasked: 'TEST***' }], pollState, suggestedPollSeconds: 2, serverTime: time };
const list = { items: [task], pollState, nextCursor: null, totalApprox: 1, serverTime: time };
const json = (data: unknown, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers });
function mockReads(overrides?: (url: string, init: RequestInit) => Response | Promise<Response> | undefined) {
  const fetcher = vi.fn((url: string, init: RequestInit) => {
    const override = overrides?.(url, init); if (override) return Promise.resolve(override);
    if (url.endsWith('/config')) return Promise.resolve(json(config));
    if (url.endsWith('/tasks/current')) return Promise.resolve(json(current, 200, { ETag: '"TEST_current"' }));
    if (url.includes(`/tasks/${id}`)) return Promise.resolve(json(detail, 200, { ETag: '"TEST_detail"' }));
    return Promise.resolve(json(list, 200, { ETag: '"TEST_list"' }));
  }); vi.stubGlobal('fetch', fetcher); return fetcher;
}
class TestPut {
  static requests: TestPut[] = [];
  withCredentials = true; status = 0; retryAfter: string | null = null;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null; onerror: (() => void) | null = null; onabort: (() => void) | null = null;
  open = vi.fn(); setRequestHeader = vi.fn(); send = vi.fn();
  getResponseHeader = vi.fn(() => this.retryAfter);
  abort = vi.fn(() => this.onabort?.());
  constructor() { TestPut.requests.push(this); }
  finish(status = 200) { this.status = status; this.onload?.(); }
}
const uploadingTask = (body: Record<string, unknown>) => ({ ...task, filename: body.filename, sizeBytes: body.sizeBytes, clientRequestId: body.clientRequestId,
  caption: body.caption, stage: 'awaiting_upload', stageLabel: '等待上传', rowVersion: 7, allowedActions: ['view', 'continue_upload', 'cancel'] });
const ticketFor = (body: Record<string, unknown>) => ({ ...uploadingTask(body), idempotentReplay: false, upload: { method: 'PUT', url: 'https://TEST.invalid/TEST_object?TEST_signature=TEST_fixture', headers: { 'Content-Type': 'video/mp4' }, expiresAt: time } });
async function prepareUpload(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('TEST_device_locked');
  const file = new File(['TEST_VIDEO'], 'TEST_upload.mp4', { type: 'video/mp4' });
  await user.upload(screen.getByLabelText('选择 MP4 视频'), file);
  await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_new_caption');
  await user.click(screen.getByRole('button', { name: '上传并加入发布队列' }));
  await user.click(screen.getByRole('button', { name: '确认并上传' })); return file;
}
beforeEach(() => {
  localStorage.setItem('tts-erp.video-publish.refresh-preference', 'off');
  TestPut.requests = []; vi.stubGlobal('XMLHttpRequest', TestPut);
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:TEST_preview') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); localStorage.clear(); });

describe('video publish read slice', () => {
  it('opens a real task detail with full caption, server decisions, attempts, cleanup and copy-safe identity, using GET only', async () => {
    const fetcher = mockReads(); const clipboard = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup(); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboard } });
    render(<VideoPublish {...props} />);
    expect((await screen.findAllByText('TEST_video.mp4')).length).toBeGreaterThan(0);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    const panel = await screen.findByRole('dialog', { name: '任务详情' });
    expect(await within(panel).findByText(/TEST_full_caption/)).toHaveTextContent('<script>TEST_plain</script>');
    expect(document.querySelector('script')).toBeNull();
    expect(within(panel).getAllByText('TEST_attempt_error')[0]).toBeVisible();
    expect(within(panel).getByText('TEST_cleanup_error')).toBeVisible();
    expect(within(panel).getByText('rowVersion')).toBeVisible();
    expect(within(panel).getByText('edit_settings')).toBeVisible();
    await user.click(within(panel).getByRole('button', { name: `复制执行 1 Artemis ID` }));
    expect(clipboard).toHaveBeenCalledWith(sessionId);
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => url === `/tts/v2/video-publish/tasks/${id}`)).toBe(true);
  });
  it('preserves exact opaque cursor and appended rows through first-page refresh, with backend ordering and zero/null intact', async () => {
    const cursor = 'TEST opaque+/==&value';
    const second = { ...task, taskId: '00000000-0000-4000-8000-000000000004', filename: 'TEST_second.mp4', queuePosition: null };
    const fetcher = mockReads((url) => url.includes('cursor=') ? json({ ...list, items: [second] }) : url.endsWith('/tasks') ? json({ ...list, nextCursor: cursor }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByText('TEST_second.mp4')).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url === '/tts/v2/video-publish/tasks?cursor=TEST+opaque%2B%2F%3D%3D%26value')).toBe(true);
    await user.click(screen.getByRole('button', { name: '立即刷新' }));
    expect(await screen.findByText('TEST_second.mp4')).toBeVisible();
    const grid = screen.getByRole('grid', { name: '视频发布任务' });
    expect(within(grid).getAllByText('0 字节')).toHaveLength(2);
    expect(within(grid).getByText('队列位置 —')).toBeVisible();
    expect(within(grid).getByText('队列位置 1')).toBeVisible();
  });
  it('accepts 304 only with exact-path validated cache and sends no validator for a changed filter', async () => {
    const fetcher = mockReads((url, init) => url.includes('/tasks') && new Headers(init.headers).has('If-None-Match') ? new Response(null, { status: 304 }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(screen.getByRole('button', { name: '立即刷新' }));
    expect(screen.getByRole('grid', { name: '视频发布任务' })).toHaveTextContent('TEST_video.mp4');
    expect(fetcher.mock.calls.some(([url, init]) => url.endsWith('/tasks') && new Headers(init.headers).get('If-None-Match') === '"TEST_list"')).toBe(true);
    await user.selectOptions(screen.getByRole('combobox', { name: '任务状态' }), 'failed');
    await screen.findByRole('grid', { name: '视频发布任务' });
    const filtered = fetcher.mock.calls.find(([url]) => url.endsWith('/tasks?status=failed'));
    expect(filtered).toBeDefined(); expect(new Headers(filtered![1].headers).has('If-None-Match')).toBe(false);
  });
  it('rejects unsolicited 304 instead of inventing empty success', async () => {
    mockReads((url) => url.endsWith('/tasks') ? new Response(null, { status: 304 }) : undefined);
    render(<VideoPublish {...props} />);
    expect(await screen.findByText(/读取失败（HTTP 304）/)).toBeVisible();
    expect(screen.queryByRole('grid', { name: '视频发布任务' })).toBeNull();
  });
  it.each([403, 401, 503])('separates HTTP %s from empty and does not render raw error bodies', async (status) => {
    mockReads((url) => url.endsWith('/tasks') ? json({ detail: 'TEST_SECRET_SIGNED_URL' }, status) : undefined);
    render(<VideoPublish {...props} />);
    expect(await screen.findByText(new RegExp('HTTP ' + status))).toBeVisible();
    expect(screen.queryByText('TEST_SECRET_SIGNED_URL')).toBeNull();
    expect(screen.queryByRole('grid', { name: '视频发布任务' })).toBeNull();
  });
  it('validates required fields and distinguishes a legitimate empty page', async () => {
    let malformed = true;
    mockReads((url) => url.endsWith('/tasks') ? json(malformed ? { ...list, items: [{ ...task, rowVersion: null }] } : { ...list, items: [], totalApprox: 0 }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    expect(await screen.findByText('视频发布响应格式错误')).toBeVisible();
    malformed = false; await user.click(screen.getByRole('button', { name: '立即刷新' }));
    expect(await screen.findByText('当前筛选下没有任务。')).toBeVisible();
    expect(screen.queryByText('视频发布响应格式错误')).toBeNull();
  });
  it('aborts obsolete filter reads and ignores a late response from the old scope', async () => {
    let resolve!: (response: Response) => void; let oldSignal: AbortSignal | null | undefined;
    mockReads((url, init) => url.endsWith('/tasks') ? new Promise<Response>((r) => { resolve = r; oldSignal = init.signal; }) : url.includes('status=failed') ? json({ ...list, items: [] }) : undefined);
    const user = userEvent.setup(); const view = render(<VideoPublish {...props} />);
    await user.selectOptions(screen.getByRole('combobox', { name: '任务状态' }), 'failed');
    expect(oldSignal?.aborted).toBe(true); await screen.findByText('当前筛选下没有任务。');
    await act(async () => { resolve(json(list)); });
    expect(screen.getByText('当前筛选下没有任务。')).toBeVisible();
    view.unmount();
  });
  it('preserves oversized numeric rowVersion from raw JSON before rounding', async () => {
    mockReads((url) => url.endsWith('/tasks') ? new Response(JSON.stringify(list).replace('"rowVersion":0', '"rowVersion":9007199254740993')) : undefined);
    render(<VideoPublish {...props} />);
    expect(await screen.findByText('rowVersion 9007199254740993')).toBeVisible();
  });
  it('waits full Retry-After across query changes; expiry allows explicit retry without replay', async () => {
    vi.useFakeTimers(); let limited = true;
    const fetcher = mockReads((url) => url.includes('/tasks') && !url.includes('/current') && !url.includes(id) && limited ? json({}, 429, { 'Retry-After': '600' }) : undefined);
    const view = render(<VideoPublish {...props} />); await act(async () => {});
    expect(screen.getByRole('button', { name: '立即刷新' })).toBeDisabled();
    const count = fetcher.mock.calls.length;
    await act(async () => { fireEvent.change(screen.getByRole('combobox', { name: '任务状态' }), { target: { value: 'failed' } }); });
    expect(fetcher).toHaveBeenCalledTimes(count);
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); });
    expect(fetcher).toHaveBeenCalledTimes(count);
    expect(screen.getByRole('button', { name: '立即刷新' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetcher).toHaveBeenCalledTimes(count);
    expect(screen.getByRole('button', { name: '立即刷新' })).toBeEnabled();
    limited = false;
    await act(async () => { screen.getByRole('button', { name: '立即刷新' }).click(); });
    expect(screen.getByRole('grid', { name: '视频发布任务' })).toBeVisible();
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('polls at response plus five seconds, pauses while hidden and cleans up timers', async () => {
    vi.useFakeTimers(); localStorage.setItem('tts-erp.video-publish.refresh-preference', 'on');
    const fetcher = mockReads(); const view = render(<VideoPublish {...props} />); await act(async () => {});
    expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(4999); }); expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); }); expect(fetcher).toHaveBeenCalledTimes(6);
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    const hiddenCount = fetcher.mock.calls.length;
    expect(hiddenCount).toBe(6);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(fetcher).toHaveBeenCalledTimes(hiddenCount);
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(fetcher).toHaveBeenCalledTimes(hiddenCount + 3);
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });

  it('reopens the same detail from its valid 304 body cache and never substitutes another task', async () => {
    const fetcher = mockReads((url, init) => url.endsWith('/tasks/' + id) && new Headers(init.headers).has('If-None-Match') ? new Response(null, { status: 304 }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    let panel = await screen.findByRole('dialog', { name: '任务详情' }); await within(panel).findByText(/TEST_full_caption/);
    await user.click(within(panel).getByRole('button', { name: '关闭任务详情' }));
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    panel = await screen.findByRole('dialog', { name: '任务详情' }); expect(await within(panel).findByText(/TEST_full_caption/)).toBeVisible();
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/tasks/' + id))).toHaveLength(2);
  });
  it('aborts old deployment reads and does not carry an old selection into a new root', async () => {
    let selectedSignal: AbortSignal | null | undefined; let resolve!: (response: Response) => void;
    const fetcher = mockReads((url, init) => url === '/tts/v2/video-publish/tasks/' + id ? new Promise<Response>((r) => { resolve = r; selectedSignal = init.signal; }) : undefined);
    const user = userEvent.setup(); const view = render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    view.rerender(<VideoPublish bootstrap={{ ...props.bootstrap, rootPath: '/other' }} />);
    expect(selectedSignal?.aborted).toBe(true);
    await act(async () => { resolve(json(detail)); });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => url === '/other/v2/video-publish/tasks/' + id)).toBe(false);
  });
  it('does not overlap a slow channel and disabling polling sends no extra read', async () => {
    vi.useFakeTimers(); localStorage.setItem('tts-erp.video-publish.refresh-preference', 'on');
    let resolve!: (response: Response) => void;
    const fetcher = mockReads((url) => url.endsWith('/tasks') ? new Promise<Response>((r) => { resolve = r; }) : undefined);
    const view = render(<VideoPublish {...props} />); await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/tasks'))).toHaveLength(1);
    await act(async () => { resolve(json(list)); });
    const count = fetcher.mock.calls.length;
    await act(async () => { fireEvent.change(screen.getByRole('combobox', { name: '自动刷新' }), { target: { value: 'off' } }); });
    expect(fetcher).toHaveBeenCalledTimes(count);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(fetcher).toHaveBeenCalledTimes(count);
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });

  it('offers file selection, preserves caption and requires confirmation before creating a task', async () => {
    const fetcher = mockReads(); const user = userEvent.setup(); render(<VideoPublish {...commandProps} />);
    await screen.findByText('TEST_device_locked');
    const fileInput = screen.getByLabelText('选择 MP4 视频');
    await user.upload(fileInput, new File(['TEST_VIDEO'], 'TEST_upload.mp4', { type: 'video/mp4' }));
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_new_caption');
    await user.click(screen.getByRole('button', { name: '上传并加入发布队列' }));
    expect(await screen.findByRole('dialog', { name: '确认加入发布队列' })).toHaveTextContent('TEST_new_caption');
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    await user.click(screen.getByRole('button', { name: '返回修改' }));
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption');
  });

  it('offers server-authorized retry with original failure evidence before any POST', async () => {
    const fetcher = mockReads(); const user = userEvent.setup(); render(<VideoPublish {...commandProps} />);
    await screen.findByRole('grid', { name: '视频发布任务' });
    const grid = screen.getByRole('grid', { name: '视频发布任务' });
    await user.click(await within(grid).findByRole('button', { name: '重试原任务' }));
    const dialog = await screen.findByRole('dialog', { name: '重试原任务确认' });
    expect(dialog).toHaveTextContent('AI 报告失败可能有误'); expect(dialog).toHaveTextContent(sessionId);
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    await user.click(within(dialog).getByRole('button', { name: '返回' }));
  });

  it('uploads exact File with no ERP credentials and explicit versioned completion, showing actual progress', async () => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body), 201); }
      if (url.endsWith('/confirm-upload')) return json({ ...uploadingTask(body), stage: 'queued', allowedActions: ['view'], rowVersion: 8 });
      return undefined;
    });
    const user = userEvent.setup(); const view = render(<VideoPublish {...commandProps} />); const file = await prepareUpload(user);
    const put = TestPut.requests[0]; expect(put).toBeDefined();
    expect(put.withCredentials).toBe(false); expect(put.open).toHaveBeenCalledWith('PUT', expect.stringContaining('https://TEST.invalid/'));
    expect(put.setRequestHeader.mock.calls).toEqual([['Content-Type', 'video/mp4']]); expect(put.send).toHaveBeenCalledWith(file);
    await act(async () => { put.upload.onprogress?.(new ProgressEvent('progress', { lengthComputable: true, loaded: 5, total: 10 })); });
    expect(screen.getByRole('progressbar', { name: '视频上传进度' })).toHaveAttribute('value', '50');
    await act(async () => { put.finish(); });
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/confirm-upload'))).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '确认上传并入队' }));
    await user.click(screen.getByRole('button', { name: '确认入队' }));
    expect(await screen.findByText(/服务端已确认上传并入队/)).toBeVisible();
    const completed = fetcher.mock.calls.find(([url]) => url.endsWith('/confirm-upload'));
    expect(JSON.parse(completed![1].body as string)).toEqual({ rowVersion: 7 });
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption');
    view.unmount(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:TEST_preview');
  });
  it('aborts only the binary PUT, retains File/caption, queries current facts and explicitly refreshes same-task ticket', async () => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body), 201); }
      if (url.endsWith('/upload-url')) return json(ticketFor(body));
      if (url.endsWith('/tasks/' + id)) return json({ ...detail, ...uploadingTask(body) });
      return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); const file = await prepareUpload(user);
    await user.click(screen.getByRole('button', { name: '取消上传' }));
    expect(TestPut.requests[0].abort).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText('选择 MP4 视频') as HTMLInputElement).files?.[0]).toBe(file);
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption');
    expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '核对上传任务' }));
    await screen.findByText(/已读取当前事实/);
    await user.click(screen.getByRole('button', { name: '明确重试原上传' })); await user.click(screen.getByRole('button', { name: '确认并上传' }));
    expect(TestPut.requests).toHaveLength(2); expect(TestPut.requests[1].send).toHaveBeenCalledWith(file);
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(2);
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/cancel') || url.endsWith('/confirm-upload'))).toBe(false);
  });
  it('keeps unknown create identity and captured payload despite draft edits, requiring GET then manual retry', async () => {
    let writes = 0; const bodies: string[] = [];
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { writes++; bodies.push(init.body as string); return writes === 1 ? Promise.reject(new TypeError('TEST transport')) : json(ticketFor(JSON.parse(init.body as string))); }
      return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    expect(await screen.findByText(/提交结果未确认/)).toBeVisible();
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), '_EDIT');
    expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeDisabled(); expect(writes).toBe(1);
    await user.click(screen.getByRole('button', { name: '核对上传任务' })); await screen.findByText(/当前第一页未查到匹配任务/);
    expect(writes).toBe(1);
    await user.click(screen.getByRole('button', { name: '明确重试原上传' }));
    const dialog = await screen.findByRole('dialog', { name: '确认加入发布队列' }); expect(dialog).toHaveTextContent('TEST_new_caption'); expect(dialog).not.toHaveTextContent('_EDIT');
    await user.click(within(dialog).getByRole('button', { name: '确认并上传' }));
    expect(bodies[1]).toBe(bodies[0]); expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(2);
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption_EDIT');
  });
  it('sends one retry using captured task/version and requires current GET after uncertain response', async () => {
    let writes = 0; let resolve!: (value: Response) => void;
    const fetcher = mockReads((url, init) => url.endsWith('/retry') ? (++writes === 1 ? new Promise<Response>((r) => { resolve = r; }) : json(task)) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '重试原任务' }));
    await user.click(screen.getByRole('button', { name: '确认重试原任务' })); expect(writes).toBe(1);
    await act(async () => { resolve(json(null)); });
    expect(await screen.findByText(/提交结果未确认/)).toBeVisible();
    const panel = screen.getByRole('region', { name: '任务操作与核对' });
    expect(within(panel).getByRole('button', { name: '重试原任务' })).toBeDisabled();
    await user.click(within(panel).getByRole('button', { name: '查询当前事实' })); await within(panel).findByText(/当前事实已读取/);
    expect(writes).toBe(1); await user.click(within(panel).getByRole('button', { name: '重试原任务' }));
    await user.click(screen.getByRole('button', { name: '确认重试原任务' })); expect(writes).toBe(2);
    expect(JSON.parse(fetcher.mock.calls.find(([url]) => url.endsWith('/retry'))![1].body as string)).toEqual({ rowVersion: 0 });
  });
  it('loads settings without overwriting accepted dirty input and submits only changed fields plus captured rowVersion', async () => {
    const fetcher = mockReads((url, init) => url.endsWith('/settings') ? json(task) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '编辑发布设置' }));
    const panel = screen.getByRole('region', { name: '任务操作与核对' });
    await user.type(within(panel).getByRole('textbox', { name: '正文 Prompt' }), 'TEST_dirty_prompt');
    await user.click(within(panel).getByRole('button', { name: '查询当前事实' })); await within(panel).findByText(/当前事实已读取/);
    expect(within(panel).getByRole('textbox', { name: '正文 Prompt' })).toHaveValue('TEST_dirty_prompt');
    expect(within(panel).getByRole('textbox', { name: '设置设备序列号' })).toHaveValue('');
    await user.click(within(panel).getByRole('button', { name: '保存发布设置' }));
    const request = fetcher.mock.calls.find(([url]) => url.endsWith('/settings'));
    expect(request![1].method).toBe('PATCH'); expect(JSON.parse(request![1].body as string)).toEqual({ rowVersion: 0, prompt: 'TEST_dirty_prompt' });
  });

  it('keeps an explicit settings discard confirmation instead of silently losing dirty input', async () => {
    mockReads(); const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '编辑发布设置' }));
    await user.type(screen.getByRole('textbox', { name: '正文 Prompt' }), 'TEST_keep_me');
    await user.click(screen.getByRole('button', { name: '关闭操作面板（保留服务端任务）' }));
    const dialog = await screen.findByRole('dialog', { name: '放弃本地设置草稿？' });
    await user.click(within(dialog).getByRole('button', { name: '继续编辑' }));
    expect(screen.getByRole('textbox', { name: '正文 Prompt' })).toHaveValue('TEST_keep_me');
  });

  it.each([
    ['cancel', '取消任务', 'cancel', '确认取消任务'],
    ['verify', '再次自动核验', 'verify', '开始核验'],
    ['retry_cleanup', '重试清理', 'cleanup/retry', '确认重试清理'],
    ['replace_upload', '重新上传', 'replace-upload', '确认重新上传'],
    ['confirm_published', '打开人工确认', 'confirm-published', '确认已发布'],
  ])('executes only explicitly server-offered %s, with captured rowVersion and bounded resource selection', async (action, label, suffix, confirm) => {
    const offered = { ...task, allowedActions: ['view', action], cleanupRetryableResources: ['device', 'spool', 'object'] };
    const fetcher = mockReads((url, init) => {
      if (init.method === 'POST') return json(action === 'replace_upload' ? { ...offered, caption: 'TEST_resume_caption', allowedActions: ['view', 'continue_upload'] } : offered);
      if (url.endsWith('/tasks')) return json({ ...list, items: [offered] }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: label }));
    if (action !== 'confirm_published') expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
    await user.click(screen.getByRole('button', { name: confirm }));
    const writes = fetcher.mock.calls.filter(([, init]) => init.method === 'POST'); expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe(`/tts/v2/video-publish/tasks/${id}/${suffix}`);
    expect(JSON.parse(writes[0][1].body as string)).toEqual(action === 'retry_cleanup' ? { rowVersion: 0, resources: ['device', 'spool'] } : { rowVersion: 0 });
    if (action === 'replace_upload') expect(await screen.findByRole('button', { name: '确认替换草稿并续传此任务' })).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url.includes('includeDiagnostics'))).toBe(false);
  });
  it('retains draft until explicit resume acceptance, and loading resume cannot overwrite newly edited input', async () => {
    let resolve!: (response: Response) => void;
    const resumable = { ...task, caption: 'TEST_full_resume_caption', allowedActions: ['view', 'continue_upload'] };
    mockReads((url) => url.endsWith('/tasks') ? json({ ...list, items: [resumable] }) : url.endsWith('/tasks/' + id) ? new Promise<Response>((r) => { resolve = r; }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_local_draft');
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '重新选择文件继续' }));
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_local_draft');
    await user.click(screen.getByRole('button', { name: '确认替换草稿并续传此任务' }));
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), '_EDIT');
    await act(async () => { resolve(json({ ...detail, ...resumable, target: { ...target, deviceSerialMasked: 'TEST_resume_device***' } })); });
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_local_draft_EDIT');
    expect(await screen.findByText(/加载期间草稿已修改，未覆盖输入/)).toBeVisible();
  });
  it('honors the full binary Retry-After across reads and does not replay PUT at expiry', async () => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => url.endsWith('/tasks') && init.method === 'POST' ? (body = JSON.parse(init.body as string), json(ticketFor(body))) : undefined);
    const user = userEvent.setup(); const view = render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    vi.useFakeTimers(); const put = TestPut.requests[0]; put.retryAfter = '600';
    await act(async () => { put.finish(429); });
    expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeDisabled();
    await act(async () => { screen.getByRole('button', { name: '立即刷新' }).click(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); });
    expect(TestPut.requests).toHaveLength(1); expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(TestPut.requests).toHaveLength(1); expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('retains settings input on 422, clears only field error after edit and requires GET to honor permanent parameter lock', async () => {
    let locked = false;
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/settings')) return json({ detail: [{ loc: ['body', 'prompt'], type: 'string_too_long', input: 'TEST_secret_input' }] }, 422);
      if (url.endsWith('/tasks/' + id)) return json({ ...detail, paramsLockedAt: locked ? time : null }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '编辑发布设置' }));
    const panel = screen.getByRole('region', { name: '任务操作与核对' });
    await user.click(within(panel).getByRole('button', { name: '查询当前事实' })); await within(panel).findByText(/当前事实已读取/);
    await user.type(within(panel).getByRole('textbox', { name: '正文 Prompt' }), 'TEST_prompt');
    await user.click(within(panel).getByRole('button', { name: '保存发布设置' }));
    expect(await screen.findByText('正文 Prompt：内容过长')).toBeVisible(); expect(screen.queryByText('TEST_secret_input')).toBeNull();
    await user.type(within(panel).getByRole('textbox', { name: '正文 Prompt' }), '_edit'); expect(screen.queryByText('正文 Prompt：内容过长')).toBeNull();
    expect(within(panel).getByRole('textbox', { name: '正文 Prompt' })).toHaveValue('TEST_prompt_edit');
    locked = true; await user.click(within(panel).getByRole('button', { name: '查询当前事实' })); await within(panel).findByText('参数已提交给发布端，不能再修改。');
    expect(within(panel).getByRole('button', { name: '保存发布设置' })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'PATCH')).toHaveLength(1);
  });
  it('keeps creation blocked by worker conditions while server-offered task commands remain available to readwrite role', async () => {
    const offered = { ...task, allowedActions: ['view', 'cancel', 'confirm_published', 'edit_settings'] };
    const fetcher = mockReads((url) => url.endsWith('/config') ? json({ ...config, worker: { ...config.worker, status: 'unavailable' }, canWrite: false, writeBlockReason: 'TEST_server_block' }) : url.endsWith('/tasks') ? json({ ...list, items: [offered] }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    const grid = screen.getByRole('grid', { name: '视频发布任务' });
    expect(await within(grid).findByRole('button', { name: '取消任务' })).toBeEnabled();
    expect(within(grid).getByRole('button', { name: '打开人工确认' })).toBeEnabled(); expect(within(grid).getByRole('button', { name: '编辑发布设置' })).toBeEnabled();
    await user.upload(screen.getByLabelText('选择 MP4 视频'), new File(['TEST'], 'TEST.mp4', { type: 'video/mp4' }));
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_caption');
    expect(screen.getByRole('button', { name: '上传并加入发布队列' })).toBeDisabled();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });

  it('reselects a known upload File explicitly without creating a new task or losing the original request identity', async () => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body)); }
      if (url.endsWith('/upload-url')) return json(ticketFor(body)); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    await act(async () => { TestPut.requests[0].finish(); });
    const replacement = new File(['NEW__VIDEO'], 'TEST_upload.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('选择 MP4 视频'), replacement);
    await user.click(screen.getByRole('button', { name: '明确重试原上传' })); await user.click(screen.getByRole('button', { name: '确认并上传' }));
    expect(TestPut.requests[1].send).toHaveBeenCalledWith(replacement);
    expect(fetcher.mock.calls.filter(([url, init]) => url.endsWith('/tasks') && init.method === 'POST')).toHaveLength(1);
  });
  it('freezes the completion target/version while another current-facts read settles', async () => {
    let body: Record<string, unknown> = {}; let resolve!: (response: Response) => void;
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body)); }
      if (url.endsWith('/tasks/' + id)) return new Promise<Response>((r) => { resolve = r; });
      if (url.endsWith('/confirm-upload')) return json({ ...uploadingTask(body), stage: 'queued', allowedActions: ['view'] }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user); await act(async () => { TestPut.requests[0].finish(); });
    await user.click(screen.getByRole('button', { name: '核对上传任务' }));
    await user.click(screen.getByRole('button', { name: '确认上传并入队' }));
    await act(async () => { resolve(json({ ...detail, ...uploadingTask(body), rowVersion: 42 })); });
    expect(screen.getByRole('dialog', { name: '确认上传入队' })).toHaveTextContent('rowVersion 7');
    await user.click(screen.getByRole('button', { name: '确认入队' }));
    const request = fetcher.mock.calls.find(([url]) => url.endsWith('/confirm-upload'));
    expect(JSON.parse(request![1].body as string)).toEqual({ rowVersion: 7 });
  });
  it('accepts resume only explicitly, reads full caption and exact target, and requires original file metadata', async () => {
    const resumable = { ...task, caption: 'TEST_full_resume_caption', allowedActions: ['view', 'continue_upload'] };
    mockReads((url) => url.endsWith('/tasks') ? json({ ...list, items: [resumable] }) : url.endsWith('/tasks/' + id) ? json({ ...detail, ...resumable, target: { ...target, deviceSerialMasked: 'TEST_resumed***' } }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await screen.findByRole('grid', { name: '视频发布任务' });
    await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_local');
    await user.click(await within(screen.getByRole('grid', { name: '视频发布任务' })).findByRole('button', { name: '重新选择文件继续' }));
    await user.click(screen.getByRole('button', { name: '确认替换草稿并续传此任务' }));
    expect(await screen.findByText(/原任务身份与完整文案保留/)).toBeVisible();
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_full_resume_caption');
    await user.upload(screen.getByLabelText('选择 MP4 视频'), new File(['TEST_wrong_size'], 'TEST_video.mp4', { type: 'video/mp4' }));
    expect(await screen.findByText(/续传必须选择原文件名和原大小/)).toBeVisible();
    expect(screen.getByRole('button', { name: '上传并加入发布队列' })).toBeDisabled();
  });
  it('rejects credential-bearing ticket headers before native PUT and retains draft without rendering the signed URL', async () => {
    mockReads((url, init) => url.endsWith('/tasks') && init.method === 'POST' ? json({ ...ticketFor(JSON.parse(init.body as string)), upload: { method: 'PUT', url: 'https://TEST.invalid/?TEST_do_not_render', headers: { 'Content-Type': 'video/mp4', Authorization: 'TEST_forbidden' }, expiresAt: time } }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    expect(await screen.findByText(/提交结果未确认/)).toBeVisible(); expect(TestPut.requests).toHaveLength(0);
    expect(screen.getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption');
    expect(screen.queryByText(/TEST_forbidden|TEST_do_not_render/)).toBeNull();
  });

  it.each([undefined, { readwrite: false, admin: false }])('fails closed for task writes when role hint is missing/false %#', async (roleCapabilities) => {
    const fetcher = mockReads(); render(<VideoPublish bootstrap={{ ...props.bootstrap, roleCapabilities }} />);
    const grid = await screen.findByRole('grid', { name: '视频发布任务' });
    expect(within(grid).getByRole('button', { name: '重试原任务' })).toBeDisabled();
    expect(within(grid).getByRole('button', { name: '编辑发布设置' })).toBeDisabled();
    expect(within(grid).getByRole('button', { name: `查看详情 ${id}` })).toBeEnabled();
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });

  it('selects static upload validation guidance from a known business code without displaying raw body messages', async () => {
    let body: Record<string, unknown> = {};
    mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body)); }
      if (url.endsWith('/confirm-upload')) return json({ detail: { code: 'UPLOAD_SIZE_MISMATCH', message: 'TEST_never_render_raw_body', retryable: true } }, 422); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user); await act(async () => { TestPut.requests[0].finish(); });
    await user.click(screen.getByRole('button', { name: '确认上传并入队' })); await user.click(screen.getByRole('button', { name: '确认入队' }));
    expect(await screen.findByText('上传对象大小与原文件不一致，请选择原文件后重新上传（HTTP 422）')).toBeVisible();
    expect(screen.queryByText('TEST_never_render_raw_body')).toBeNull();
  });

  it('loads a sanitized diagnostic excerpt only after explicit GET, preserving structured output and plain escaped text', async () => {
    const diagnostic = { ...detail, attempts: [{ ...attempt, promptSnapshot: 'TEST_prompt <script>TEST_escaped</script> token=[REDACTED]', artemisOutput: { verdict: 'TEST_published', steps: [{ count: 0, safe: false, note: null, text: 'TEST_output <b>plain</b>' }], authorization: '[REDACTED]' } }] };
    const fetcher = mockReads((url) => url.endsWith('?includeDiagnostics=true') ? json(diagnostic, 200, { ETag: '"TEST_diagnostic"' }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    const dialog = await screen.findByRole('dialog', { name: '任务详情' }); await within(dialog).findByText(/TEST_full_caption/);
    expect(fetcher.mock.calls.some(([url]) => url.includes('includeDiagnostics'))).toBe(false);
    await user.click(within(dialog).getByRole('button', { name: '读取脱敏诊断节选' }));
    const region = await within(dialog).findByRole('region', { name: '脱敏诊断节选' });
    expect(await within(region).findByText(/TEST_prompt/)).toHaveTextContent('<script>TEST_escaped</script>');
    expect(region).toHaveTextContent('最多 2000 字符'); expect(region).toHaveTextContent('TEST_published'); expect(region).toHaveTextContent('false'); expect(region).toHaveTextContent('null');
    expect(within(region).getByText('TEST_output <b>plain</b>')).toBeVisible(); expect(document.querySelector('script')).toBeNull();
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1);
    expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => url === `/tts/v2/video-publish/tasks/${id}?includeDiagnostics=true`)).toBe(true);
  });

  it('keeps diagnostic query ETags separate from regular details and recognizes only a matching cached 304', async () => {
    let reads = 0;
    const diagnostic = { ...detail, attempts: [{ ...attempt, promptSnapshot: 'TEST_cached_excerpt', artemisOutput: { ok: true } }] };
    const fetcher = mockReads((url) => url.includes('includeDiagnostics') ? (++reads === 1 ? json(diagnostic, 200, { ETag: '"TEST_diag_only"' }) : new Response(null, { status: 304 })) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('TEST_cached_excerpt');
    await user.click(screen.getByRole('button', { name: '刷新脱敏诊断节选' }));
    expect(await screen.findByText('TEST_cached_excerpt')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '关闭任务详情' }));
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    expect(reads).toBe(2); expect(screen.queryByText('TEST_cached_excerpt')).toBeNull();
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('TEST_cached_excerpt');
    const queries = fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'));
    expect(queries).toHaveLength(3);
    expect(new Headers(queries[0][1].headers).has('If-None-Match')).toBe(false);
    expect(new Headers(queries[1][1].headers).get('If-None-Match')).toBe('"TEST_diag_only"');
    expect(new Headers(queries[2][1].headers).get('If-None-Match')).toBe('"TEST_diag_only"');
  });
  it('rejects an unsolicited diagnostic 304 rather than borrowing a regular detail cache', async () => {
    mockReads((url) => url.includes('includeDiagnostics') ? new Response(null, { status: 304 }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    expect(await screen.findByText(/读取失败（HTTP 304）/)).toBeVisible();
    expect(screen.queryByRole('region', { name: '脱敏诊断节选' })).toBeNull();
  });
  it.each([
    { promptSnapshot: null, artemisOutput: null, expected: '无 Prompt 节选。' },
    { promptSnapshot: '', artemisOutput: {}, expected: '空 Prompt 节选。' },
    { promptSnapshot: '测'.repeat(2000), artemisOutput: { empty: [], no: false, zero: 0, absent: null }, expected: '测'.repeat(2000) },
  ])('preserves nullable/empty/max-length prompt and typed output values %#', async ({ promptSnapshot, artemisOutput, expected }) => {
    mockReads((url) => url.includes('includeDiagnostics') ? json({ ...detail, attempts: [{ ...attempt, promptSnapshot, artemisOutput }] }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    expect(await screen.findByText(expected)).toBeVisible();
  });
  it.each([
    { ...detail, taskId: '00000000-0000-4000-8000-000000000004', attempts: [] },
    { ...detail, attempts: null },
    { ...detail, attempts: [{ ...attempt, artemisOutput: null }] },
    { ...detail, attempts: [{ ...attempt, promptSnapshot: '测'.repeat(2001), artemisOutput: null }] },
    { ...detail, attempts: [{ ...attempt, promptSnapshot: null, artemisOutput: [] }] },
  ])('rejects wrong identity or malformed diagnostic projection without rendering it %#', async (payload) => {
    mockReads((url) => url.includes('includeDiagnostics') ? json(payload) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    expect(await screen.findByText('视频发布响应格式错误')).toBeVisible();
    expect(screen.queryByRole('region', { name: '脱敏诊断节选' })).toBeNull();
  });
  it('shows empty diagnostics distinctly from HTTP 403 and does not display denied body text', async () => {
    let denied = false;
    mockReads((url) => url.includes('includeDiagnostics') ? denied ? json({ detail: 'TEST_denied [REDACTED]' }, 403) : json({ ...detail, attempts: [] }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('尚无执行诊断记录。');
    denied = true; await user.click(screen.getByRole('button', { name: '刷新脱敏诊断节选' }));
    expect(await screen.findByText(/无权读取（HTTP 403）/)).toBeVisible();
    expect(screen.queryByText('尚无执行诊断记录。')).toBeNull(); expect(screen.queryByText('TEST_denied [REDACTED]')).toBeNull();
  });
  it('does not gate diagnostic GET on creation readiness, write role or mutation eligibility', async () => {
    const fetcher = mockReads((url) => url.endsWith('/config') ? json({ ...config, canWrite: false, worker: { ...config.worker, status: 'unavailable' } }) : url.includes('includeDiagnostics') ? json({ ...detail, allowedActions: ['view'], attempts: [] }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    expect(await screen.findByText('尚无执行诊断记录。')).toBeVisible(); expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  });
  it('aborts obsolete task diagnostics and drops a late result without transferring the validator to another task', async () => {
    const secondId = '00000000-0000-4000-8000-000000000004';
    let resolve!: (response: Response) => void; let oldSignal: AbortSignal | null | undefined;
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks')) return json({ ...list, items: [task, { ...task, taskId: secondId }] });
      if (url.endsWith(`/tasks/${id}?includeDiagnostics=true`)) return new Promise<Response>((r) => { resolve = r; oldSignal = init.signal; });
      if (url.endsWith(`/tasks/${secondId}?includeDiagnostics=true`)) return json({ ...detail, taskId: secondId, attempts: [{ ...attempt, promptSnapshot: 'TEST_second_excerpt', artemisOutput: null }] });
      if (url.endsWith('/tasks/' + secondId)) return json({ ...detail, taskId: secondId }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` })); await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    await user.click(screen.getByRole('button', { name: '关闭任务详情' })); expect(oldSignal?.aborted).toBe(true);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${secondId}` })); await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    await screen.findByText('TEST_second_excerpt');
    await act(async () => { resolve(json({ ...detail, attempts: [{ ...attempt, promptSnapshot: 'TEST_old_excerpt', artemisOutput: null }] }, 200, { ETag: '"TEST_old"' })); });
    expect(screen.queryByText('TEST_old_excerpt')).toBeNull(); expect(screen.getByText('TEST_second_excerpt')).toBeVisible();
    const second = fetcher.mock.calls.find(([url]) => url.endsWith(`/tasks/${secondId}?includeDiagnostics=true`)); expect(new Headers(second![1].headers).has('If-None-Match')).toBe(false);
  });
  it('aborts old-root diagnostics and requires another explicit query in the new deployment scope', async () => {
    let resolve!: (response: Response) => void; let oldSignal: AbortSignal | null | undefined;
    const fetcher = mockReads((url, init) => {
      if (url === `/tts/v2/video-publish/tasks/${id}?includeDiagnostics=true`) return new Promise<Response>((r) => { resolve = r; oldSignal = init.signal; });
      if (url.includes('includeDiagnostics')) return json({ ...detail, attempts: [] }); return undefined;
    });
    const user = userEvent.setup(); const view = render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` })); await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' }));
    view.rerender(<VideoPublish bootstrap={{ ...props.bootstrap, rootPath: '' }} />); expect(oldSignal?.aborted).toBe(true);
    await act(async () => { resolve(json({ ...detail, attempts: [{ ...attempt, promptSnapshot: 'TEST_old_root', artemisOutput: null }] })); });
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1);
    await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('尚无执行诊断记录。');
    expect(fetcher.mock.calls.some(([url]) => url === `/v2/video-publish/tasks/${id}?includeDiagnostics=true`)).toBe(true); expect(screen.queryByText('TEST_old_root')).toBeNull();
  });
  it('retains the full diagnostic cooldown through hiding and ordinary refresh; expiry does not replay GET', async () => {
    let limited = true;
    const fetcher = mockReads((url) => url.includes('includeDiagnostics') ? limited ? json({}, 429, { 'Retry-After': '600' }) : json({ ...detail, attempts: [] }) : undefined);
    const user = userEvent.setup(); const view = render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` }));
    vi.useFakeTimers(); await act(async () => { screen.getByRole('button', { name: '读取脱敏诊断节选' }).click(); });
    expect(screen.getByRole('button', { name: '刷新脱敏诊断节选' })).toBeDisabled();
    await act(async () => { screen.getByRole('button', { name: '隐藏诊断节选' }).click(); screen.getByRole('button', { name: '刷新当前事实' }).click(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(screen.getByRole('button', { name: '读取脱敏诊断节选' })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(screen.getByRole('button', { name: '读取脱敏诊断节选' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1);
    limited = false; await act(async () => { screen.getByRole('button', { name: '读取脱敏诊断节选' }).click(); });
    expect(screen.getByText('尚无执行诊断记录。')).toBeVisible(); view.unmount();
  });
  it('keeps diagnostics explicit-only while ordinary refresh runs and retrieves newly appended attempts only on its own refresh', async () => {
    let latest = false;
    const fetcher = mockReads((url) => url.includes('includeDiagnostics') ? json({ ...detail, attempts: [{ ...attempt, sequenceNo: latest ? 2 : 1, promptSnapshot: latest ? 'TEST_new_attempt' : 'TEST_first_attempt', artemisOutput: { url: 'https://TEST.invalid/path?[REDACTED]' } }] }) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` })); await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('TEST_first_attempt');
    latest = true; await user.click(screen.getByRole('button', { name: '刷新当前事实' }));
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1); expect(screen.queryByText('TEST_new_attempt')).toBeNull();
    await user.click(screen.getByRole('button', { name: '刷新脱敏诊断节选' })); await screen.findByText('TEST_new_attempt');
    const region = screen.getByRole('region', { name: '脱敏诊断节选' }); expect(within(region).queryByRole('link')).toBeNull();
    expect(Array.from({ length: localStorage.length }, (_, i) => localStorage.getItem(localStorage.key(i)!)).join(' ')).not.toContain('TEST_new_attempt');
  });

  it('does not poll the diagnostic query when the ordinary five-second refresh is enabled', async () => {
    const fetcher = mockReads((url) => url.includes('includeDiagnostics') ? json({ ...detail, attempts: [] }) : undefined);
    const user = userEvent.setup(); const view = render(<VideoPublish {...props} />);
    await user.click(await screen.findByRole('button', { name: `查看详情 ${id}` })); await user.click(await screen.findByRole('button', { name: '读取脱敏诊断节选' })); await screen.findByText('尚无执行诊断记录。');
    vi.useFakeTimers();
    await act(async () => { fireEvent.change(screen.getByRole('combobox', { name: '自动刷新', hidden: true }), { target: { value: 'on' } }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fetcher.mock.calls.filter(([url]) => url.includes('includeDiagnostics'))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/tasks/current')).length).toBeGreaterThan(2);
    view.unmount();
  });

  it('does not resurrect an abandoned upload task when its old GET settles after a new draft', async () => {
    const bodies: Record<string, unknown>[] = []; let resolve!: (response: Response) => void;
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { const body = JSON.parse(init.body as string); bodies.push(body); return json(ticketFor(body)); }
      if (url.endsWith('/tasks/' + id)) return new Promise<Response>((r) => { resolve = r; }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    await act(async () => { TestPut.requests[0].finish(); });
    await user.click(screen.getByRole('button', { name: '核对上传任务' }));
    await user.click(screen.getByRole('button', { name: '清除选择 / 放弃续传' })); await user.click(screen.getByRole('button', { name: '确认清除本地草稿' }));
    const next = new File(['TEST_NEXT_VIDEO'], 'TEST_next.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('选择 MP4 视频'), next); await user.type(screen.getByRole('textbox', { name: '发布文案' }), 'TEST_next_caption');
    await act(async () => { resolve(json({ ...detail, ...uploadingTask(bodies[0]) })); });
    const workbench = screen.getByRole('region', { name: '新建发布任务' });
    expect(within(workbench).getByRole('textbox', { name: '发布文案' })).toBeEnabled();
    expect(within(workbench).getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_next_caption');
    expect(within(workbench).getByRole('combobox', { name: '创建目标设备' })).toBeEnabled();
    expect(within(workbench).queryByText('上传任务 ID')).toBeNull();
    await user.click(within(workbench).getByRole('button', { name: '上传并加入发布队列' })); await user.click(screen.getByRole('button', { name: '确认并上传' }));
    expect(bodies).toHaveLength(2); expect(bodies[1].clientRequestId).not.toBe(bodies[0].clientRequestId);
    expect(bodies[1]).toMatchObject({ filename: 'TEST_next.mp4', caption: 'TEST_next_caption' });
    expect(TestPut.requests[1].send).toHaveBeenCalledWith(next);
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/confirm-upload'))).toBe(false);
  });
  it.each([200, 429])('does not let an abandoned GET status %s finish or throttle a newer workbench read', async (status) => {
    let body: Record<string, unknown> = {}; let oldResolve!: (response: Response) => void; let nextResolve!: (response: Response) => void;
    let oldSignal: AbortSignal | null | undefined;
    mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); return json(ticketFor(body)); }
      if (url.endsWith('/tasks/' + id)) return new Promise<Response>((r) => { oldResolve = r; oldSignal = init.signal; });
      if (url.endsWith('/devices')) return new Promise<Response>((r) => { nextResolve = r; }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user); await act(async () => { TestPut.requests[0].finish(); });
    await user.click(screen.getByRole('button', { name: '核对上传任务' }));
    await user.click(screen.getByRole('button', { name: '清除选择 / 放弃续传' })); await user.click(screen.getByRole('button', { name: '确认清除本地草稿' }));
    await user.click(screen.getByRole('button', { name: '加载设备列表' }));
    await act(async () => { oldResolve(status === 200 ? json({ ...detail, ...uploadingTask(body) }) : json({}, 429, { 'Retry-After': '600' })); });
    expect(screen.queryByText(/请等待完整 Retry-After/)).toBeNull();
    expect(screen.getByRole('button', { name: '加载设备列表' })).toBeDisabled();
    await act(async () => { nextResolve(json({ devices: [] })); });
    expect(screen.getByRole('button', { name: '加载设备列表' })).toBeEnabled(); expect(oldSignal?.aborted).toBe(true);
  });
  it.each(['', '/tts'])('rejects an ERP-origin PUT before constructing XHR and retains captured task/draft under root %s', async (rootPath) => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); const ticket = ticketFor(body); return json({ ...ticket, upload: { ...ticket.upload, url: `${location.origin}${rootPath}/minio/TEST_object?TEST_signature=TEST_fixture` } }); }
      return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish bootstrap={{ ...commandProps.bootstrap, rootPath }} />); const file = await prepareUpload(user);
    expect(TestPut.requests).toHaveLength(0);
    expect(await screen.findByText(/上传地址与 ERP 同源/)).toBeVisible();
    const workbench = screen.getByRole('region', { name: '新建发布任务' });
    expect(within(workbench).getByRole('textbox', { name: '发布文案' })).toHaveValue('TEST_new_caption');
    expect((screen.getByLabelText('选择 MP4 视频') as HTMLInputElement).files?.[0]).toBe(file);
    expect(within(workbench).getByText('上传任务 ID')).toBeVisible();
    expect(within(workbench).getByRole('button', { name: '明确重试原上传' })).toBeDisabled();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
    expect(screen.queryByText(/TEST_signature=TEST_fixture/)).toBeNull();
  });
  it.each([
    ['http://test.erp.invalid', 'http://TEST.erp.invalid:80/minio/TEST'],
    ['https://test.erp.invalid', 'https://TEST.erp.invalid:443/tts/minio/TEST'],
    ['https://test.erp.invalid:444', 'https://TEST.erp.invalid:0444/minio/TEST'],
  ])('canonical ticket boundary rejects same-origin aliases for %s', (origin, url) => {
    vi.stubGlobal('location', new URL(origin));
    const ticket = ticketFor({ clientRequestId: task.clientRequestId, filename: 'TEST_file.mp4', sizeBytes: 4, caption: 'TEST_caption' });
    expect(() => decodeTicket({ ...ticket, upload: { ...ticket.upload, url } })).toThrow(/上传地址与 ERP 同源/);
  });
  it.each(['http://test.store.invalid:80/minio/TEST', 'https://test.store.invalid:443/minio/TEST'])('retains absolute cross-origin http(s) tickets without adding a TLS deployment policy: %s', (url) => {
    vi.stubGlobal('location', new URL('https://test.erp.invalid'));
    const ticket = ticketFor({ clientRequestId: task.clientRequestId, filename: 'TEST_file.mp4', sizeBytes: 4, caption: 'TEST_caption' });
    expect(decodeTicket({ ...ticket, upload: { ...ticket.upload, url } }).upload?.url).toBe(url);
  });

  it('rechecks actual browser origin after ticket decoding and before constructing the native PUT', async () => {
    let originReads = 0;
    vi.stubGlobal('location', { get origin() { return ++originReads === 1 ? 'https://test.erp.invalid' : 'https://test.invalid'; } });
    mockReads((url, init) => url.endsWith('/tasks') && init.method === 'POST' ? json(ticketFor(JSON.parse(init.body as string))) : undefined);
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    expect(originReads).toBe(2); expect(TestPut.requests).toHaveLength(0);
    expect(await screen.findByText(/上传地址与 ERP 同源/)).toBeVisible();
    expect(screen.getByRole('button', { name: '明确重试原上传' })).toBeDisabled();
  });
  it('preserves the known same-origin task for explicit GET verification without replaying the create or uploading', async () => {
    let body: Record<string, unknown> = {};
    const fetcher = mockReads((url, init) => {
      if (url.endsWith('/tasks') && init.method === 'POST') { body = JSON.parse(init.body as string); const ticket = ticketFor(body); return json({ ...ticket, upload: { ...ticket.upload, url: `${location.origin}/tts/minio/TEST` } }); }
      if (url.endsWith('/tasks/' + id)) return json({ ...detail, ...uploadingTask(body) }); return undefined;
    });
    const user = userEvent.setup(); render(<VideoPublish {...commandProps} />); await prepareUpload(user);
    await user.click(screen.getByRole('button', { name: '核对上传任务' }));
    expect(await screen.findByText(/已读取当前事实，不代表原提交或对象上传已确认/)).toBeVisible();
    expect(fetcher.mock.calls.some(([url]) => url === `/tts/v2/video-publish/tasks/${id}`)).toBe(true);
    expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1); expect(TestPut.requests).toHaveLength(0);
  });
  it('retains the existing prohibition on userinfo in a cross-origin ticket', () => {
    const ticket = ticketFor({ clientRequestId: task.clientRequestId, filename: 'TEST_file.mp4', sizeBytes: 4, caption: 'TEST_caption' });
    expect(() => decodeTicket({ ...ticket, upload: { ...ticket.upload, url: 'https://TEST_user:TEST_value@test.store.invalid/minio/TEST' } })).toThrow('视频发布响应格式错误');
  });

});
