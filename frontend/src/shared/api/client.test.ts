import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, requestJson } from './client';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('requestJson', () => {
  it('uses the prefixed path and same-origin cookies and preserves headers', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"items":[]}', { headers: { 'Content-Type': 'application/json', 'X-Total-Count': '42' } }));
    vi.stubGlobal('fetch', fetcher);
    const result = await requestJson<{items: unknown[]}>('/tts', '/v2/items');
    expect(result.data.items).toEqual([]);
    expect(result.headers.get('X-Total-Count')).toBe('42');
    expect(fetcher).toHaveBeenCalledWith('/tts/v2/items', expect.objectContaining({ credentials: 'same-origin' }));
  });
  it('returns empty data for 204 without parsing JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect((await requestJson<void>('', '/v2/auth/logout', { method: 'POST' })).data).toBeUndefined();
  });
  it('adds the CSRF header on writes without forcing JSON onto multipart', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher);
    const body = new FormData(); body.set('file', 'TEST');
    await requestJson<void>('', '/v2/upload', { method: 'POST', body });
    const init = fetcher.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get('X-Requested-With')).toBe('tts-erp');
    expect(headers.has('Content-Type')).toBe(false);
  });
  it.each(['600', 'Fri, 23 Oct 2026 10:10:00 GMT'])('preserves the server minimum beyond five minutes: %s', async (header) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-23T10:00:00Z'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"limited"}', { status: 429, headers: { 'Retry-After': header } })));
    await expect(requestJson('', '/v2/items')).rejects.toMatchObject({ status: 429, retryAfter: 600 });
  });
  it('keeps local long waits cancellable without signed-32-bit timer overflow', async () => {
    vi.useFakeTimers();
    const api = await import('./client') as unknown as { scheduleReadDelay: (callback: () => void, milliseconds: number) => () => void };
    expect(api.scheduleReadDelay).toBeTypeOf('function');
    const callback = vi.fn();
    const cancel = api.scheduleReadDelay(callback, 2_147_483_647 + 1000);
    await vi.advanceTimersByTimeAsync(2_147_483_647);
    expect(callback).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(999);
    expect(callback).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(callback).toHaveBeenCalledTimes(1);
    cancel();
    const cancelled = vi.fn();
    api.scheduleReadDelay(cancelled, 1000)();
    await vi.advanceTimersByTimeAsync(1000);
    expect(cancelled).not.toHaveBeenCalled();
  });
  it('reports status, retry delay and request id without auto retry', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"detail":"too many"}', { status: 429, headers: { 'Retry-After': '7', 'x-request-id': 'TEST-request' } }));
    vi.stubGlobal('fetch', fetcher);
    await expect(requestJson('', '/v2/items')).rejects.toMatchObject({ status: 429, retryAfter: 7, requestId: 'TEST-request' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([
    { code: 'TEST_REGION_REQUIRED', message: '请选择区域', field: 'region' },
    [{ loc: ['body', 'sort_order'], msg: '应为非负整数', type: 'greater_than_equal' }],
  ])('preserves untrusted structured detail for explicit safe field consumption: %j', async (detail) => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(requestJson('', '/v2/config/enum-map', { method: 'PUT', body: '{}' })).rejects.toMatchObject({ status: 422, message: 'HTTP 422', detail });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not replay an unknown mutation result', async () => {
    const lost = new TypeError('TEST response lost after possible commit');
    const fetcher = vi.fn().mockRejectedValue(lost);
    vi.stubGlobal('fetch', fetcher);
    await expect(requestJson('', '/v2/config/enum-map', { method: 'PUT', body: '{}' })).rejects.toBe(lost);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not turn successful malformed JSON into an empty result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json')));
    await expect(requestJson('', '/v2/items')).rejects.toBeInstanceOf(ApiError);
  });
  it('reports plain-text server errors without exposing markup as HTML', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>unavailable</html>', { status: 503 })));
    await expect(requestJson('', '/v2/items')).rejects.toMatchObject({ status: 503 });
  });
  it('notifies the configured login owner only for unauthorized responses', async () => {
    const api = await import('./client') as unknown as { setUnauthorizedHandler: (handler: () => void) => () => void };
    expect(api.setUnauthorizedHandler).toBeTypeOf('function');
    const handler = vi.fn();
    const dispose = api.setUnauthorizedHandler(handler);
    try {
      const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"detail":"forbidden"}', { status: 403 }))
        .mockResolvedValueOnce(new Response('expired', { status: 401 }));
      vi.stubGlobal('fetch', fetcher);
      await expect(requestJson('', '/v2/items')).rejects.toMatchObject({ status: 403 });
      expect(handler).not.toHaveBeenCalled();
      await expect(requestJson('', '/v2/items')).rejects.toMatchObject({ status: 401 });
      expect(handler).toHaveBeenCalledTimes(1);
    } finally { dispose(); }
  });
  it('preserves cancellation rather than manufacturing an HTTP success', async () => {
    const abort = new DOMException('Cancelled', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abort));
    await expect(requestJson('', '/v2/items')).rejects.toBe(abort);
  });
});
