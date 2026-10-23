import { pathFor } from './paths';

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfter?: number, readonly requestId?: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ApiResponse<T> {
  data: T;
  headers: Headers;
  status: number;
}

let unauthorizedHandler: (() => void) | undefined;

/** Navigation policy stays in the application entry, not in this transport module. */
export function setUnauthorizedHandler(handler: () => void): () => void {
  unauthorizedHandler = handler;
  return () => { if (unauthorizedHandler === handler) unauthorizedHandler = undefined; };
}

function retryDelay(value: string | null): number | undefined {
  if (!value) return undefined;
  const seconds = /^\d+$/.test(value) ? Number(value) : Math.ceil((Date.parse(value) - Date.now()) / 1000);
  // Never shorten the server's minimum; unrepresentably long numeric waits stay blocked.
  return Number.isNaN(seconds) ? undefined : Math.max(0, seconds);
}

/** One lifecycle-owned delay, split to avoid browser signed-32-bit timer overflow.
 * Uses elapsed monotonic time; it never retries requests or owns global scheduling.
 */
export function scheduleReadDelay(callback: () => void, milliseconds: number): () => void {
  const deadline = Math.ceil(performance.now() + milliseconds);
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = () => {
    if (cancelled) return;
    const remaining = deadline - performance.now();
    if (remaining <= 0) callback();
    else timer = setTimeout(tick, Math.min(2_147_483_647, remaining));
  };
  tick();
  return () => { cancelled = true; clearTimeout(timer); };
}

/** Fetch one response. Cancellation propagates; mutations are never replayed. */
export async function requestJson<T>(rootPath: string, path: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Requested-With', 'tts-erp');
  if (typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(pathFor(rootPath, path), { ...init, method, headers, credentials: 'same-origin' });
  const requestId = response.headers.get('x-request-id') ?? undefined;
  if (response.status === 401) unauthorizedHandler?.();
  if (response.status === 204) return { data: undefined as T, status: 204, headers: response.headers };
  const raw = await response.text();
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new ApiError(response.ok ? '服务器返回了无法解析的数据' : `HTTP ${response.status}`, response.status,
      retryDelay(response.headers.get('Retry-After')), requestId);
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : undefined;
    throw new ApiError(typeof detail === 'string' ? detail : `HTTP ${response.status}`, response.status,
      retryDelay(response.headers.get('Retry-After')), requestId);
  }
  return { data: data as T, status: response.status, headers: response.headers };
}
