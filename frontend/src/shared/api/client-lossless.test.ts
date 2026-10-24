import { afterEach, expect, it, vi } from 'vitest';
import { parse, stringify } from 'lossless-json';
import { parseIdentityJson } from './identity-json';
import { ApiError, requestJson, type ApiResponse } from './client';

// Invoke the desired extra argument before the transport supports it, so RED is
// an observable decode failure, not a typecheck/import failure.
const readWith = requestJson as (root: string, path: string, init: RequestInit,
  parse: (raw: string) => unknown) => Promise<ApiResponse<unknown>>;
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('gives an explicit decoder untouched response text before large IDs can round', async () => {
  const raw = '{"id":900719925474099312345}';
  vi.stubGlobal('fetch', vi.fn(async () => new Response(raw)));
  const decode = vi.fn(() => ({ id: '900719925474099312345' }));
  const result = await readWith('/tts', '/v2/users', {}, decode);
  expect(decode).toHaveBeenCalledExactlyOnceWith(raw);
  expect(result.data).toEqual({ id: '900719925474099312345' });
});
it('supports the maintained parser for exact large IDs without changing safe counters', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"id":900719925474099312345,"activeSessions":0}')));
  const result = await readWith('', '/v2/users', {}, parseIdentityJson);
  expect(result.data).toEqual({ id: '900719925474099312345', activeSessions: 0 });
});
it('round-trips config numeric values through the maintained lossless parser and serializer', async () => {
  const raw = '{"payload":{"limit":900719925474099312345,"ratio":0.123456789012345678901,"zero":0,"unset":null}}';
  vi.stubGlobal('fetch', vi.fn(async () => new Response(raw)));
  expect(stringify((await readWith('', '/v2/config/runtime', {}, parse)).data)).toBe(raw);
});
it('keeps default JSON numeric types when no alternate decoder was requested', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"id":42}')));
  expect((await requestJson<{ id: number }>('', '/v2/users')).data.id).toBe(42);
});
it('normalizes a decoder failure without exposing response contents', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"value":"TEST_sensitive"}')));
  const result = readWith('', '/v2/config/runtime', {}, () => { throw new Error('TEST_sensitive'); });
  await expect(result).rejects.toMatchObject({ name: 'ApiError', status: 200, message: '服务器返回了无法解析的数据' });
});
it('keeps error decoding in the existing owner and never calls the success decoder', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"denied"}', { status: 403 })));
  const decode = vi.fn();
  await expect(readWith('', '/v2/users', {}, decode)).rejects.toBeInstanceOf(ApiError);
  expect(decode).not.toHaveBeenCalled();
});
it('keeps 204 bodyless and does not call a success decoder', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })));
  const decode = vi.fn();
  expect((await readWith('', '/v2/users', {}, decode)).data).toBeUndefined();
  expect(decode).not.toHaveBeenCalled();
});
