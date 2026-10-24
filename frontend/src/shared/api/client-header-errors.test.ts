import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, requestJson, retryAfterSeconds } from './client';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('authoritative denial/throttle headers before an unfinished error body', () => {
  for (const status of [403, 429]) for (const method of ['GET', 'POST']) {
    it(`${method} HTTP ${status} retains status and Retry-After without waiting for body`, async () => {
      let stream!: ReadableStreamDefaultController<Uint8Array>;
      const response = new Response(new ReadableStream<Uint8Array>({ start(controller) { stream = controller; } }), {
        status, headers: { 'Retry-After': '60', 'x-request-id': 'TEST_header_error' },
      });
      const text = vi.spyOn(response, 'text');
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
      let observed: unknown;
      const pending = requestJson('', '/v2/TEST_header_error', { method }).catch((error: unknown) => { observed = error; });
      try {
        // Let the resolved fetch and its error boundary run; do not settle its body.
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(observed).toBeInstanceOf(ApiError);
        expect(observed).toMatchObject({ status, retryAfter: 60, requestId: 'TEST_header_error' });
        expect(text).not.toHaveBeenCalled();
      } finally {
        try { stream.close(); } catch { /* The shared client may already cancel the unread body. */ }
        await pending;
      }
    });
  }
});

it.each([400, 401, 404, 409, 422, 500, 503])('keeps HTTP %s untrusted body out of the public Error message', async (status) => {
  const detail = 'TEST_untrusted_upstream_body';
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status })));
  await expect(requestJson('', '/v2/TEST_public_error')).rejects.toMatchObject({
    status, message: `HTTP ${status}`, detail,
  });
});

it('shares the existing numeric Retry-After policy with native binary uploads', () => {
  expect(retryAfterSeconds('60')).toBe(60);
  expect(retryAfterSeconds('0')).toBe(0);
  expect(retryAfterSeconds(null)).toBeUndefined();
  expect(retryAfterSeconds('TEST_invalid_header')).toBeUndefined();
});
it('shares the existing HTTP-date Retry-After policy without shortening it', () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-24T20:00:00Z'));
  expect(retryAfterSeconds('Sat, 24 Oct 2026 20:01:00 GMT')).toBe(60);
});
