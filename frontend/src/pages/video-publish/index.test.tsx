import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VideoPublish from './index';
import type { PageProps } from '../../shared/contracts';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'video-publish', navigation: [], releaseId: 'TEST' } };
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
beforeEach(() => { localStorage.setItem('tts-erp.video-publish.refresh-preference', 'off'); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); localStorage.clear(); });

describe('video publish read slice', () => {
  it('opens a real task detail with full caption, server decisions, attempts, cleanup and copy-safe identity, using GET only', async () => {
    const fetcher = mockReads(); const clipboard = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup(); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboard } });
    render(<VideoPublish {...props} />);
    expect((await screen.findAllByText('TEST_video.mp4')).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: `查看详情 ${id}` }));
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
    await user.click(screen.getByRole('button', { name: `查看详情 ${id}` }));
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

});
