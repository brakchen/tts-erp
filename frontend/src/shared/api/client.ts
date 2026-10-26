import { pathFor } from './paths';

export class ApiError extends Error {
  /** detail is untrusted: consumers must select safe fields, never log/render input or ctx. */
  constructor(message: string, readonly status: number, readonly retryAfter?: number, readonly requestId?: string, readonly detail?: unknown) {
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

// Native binary uploads reuse the same header policy, without JSON/auth replay.
export { retryDelay as retryAfterSeconds };

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

/** Fetch once; an optional success decoder can preserve numeric identity before rounding.
 * Error parsing, cancellation, authorization and mutation non-replay keep one owner.
 */
export async function requestJson<T>(rootPath: string, path: string, init: RequestInit = {},
  parseResponse?: (raw: string) => unknown): Promise<ApiResponse<T>> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Requested-With', 'tts-erp');
  if (typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(pathFor(rootPath, path), { ...init, method, headers, credentials: 'same-origin' });
  const requestId = response.headers.get('x-request-id') ?? undefined;
  if (response.status === 401) unauthorizedHandler?.();
  // A received denial or positive error Retry-After fence is authoritative.
  // Do not let a stalled body hide it behind a local waiting deadline. Without
  // a positive fence, structured validation/conflict/not-found parsing is unchanged.
  const headerRetryAfter = retryDelay(response.headers.get('Retry-After'));
  if (response.status === 403 || response.status === 429
    || (!response.ok && headerRetryAfter !== undefined && headerRetryAfter > 0)) {
    void response.body?.cancel().catch(() => {});
    throw new ApiError(`HTTP ${response.status}`, response.status,
      retryDelay(response.headers.get('Retry-After')), requestId);
  }
  if (response.status === 204) return { data: undefined as T, status: 204, headers: response.headers };
  const raw = await response.text();
  let data: unknown;
  try {
    data = response.ok && parseResponse ? parseResponse(raw) : JSON.parse(raw);
  } catch {
    throw new ApiError(response.ok ? '服务器返回了无法解析的数据' : `HTTP ${response.status}`, response.status,
      retryDelay(response.headers.get('Retry-After')), requestId);
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : undefined;
    // Error.message is rendered by legacy read consumers; raw detail is not a
    // public message. Keep it separate for explicit allowlisted field mapping.
    throw new ApiError(`HTTP ${response.status}`, response.status,
      retryDelay(response.headers.get('Retry-After')), requestId, detail);
  }
  return { data: data as T, status: response.status, headers: response.headers };
}
