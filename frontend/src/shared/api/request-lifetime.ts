// Shared extraction of the validated users-page primitive. This bounds local UI
// waiting, not server execution, HTTP policy, Retry-After or automatic retries.
export const REQUEST_DEADLINE_MS = 30_000;

export class RequestTimeoutError extends Error {
  constructor(message = '等待超时，请稍后重试') {
    super(message);
    this.name = 'RequestTimeoutError';
  }
}

export class RequestCancelledError extends Error {
  constructor(message = '请求已取消') {
    super(message);
    this.name = 'RequestCancelledError';
  }
}

export function isRequestTimeoutError(error: unknown): boolean {
  return error instanceof RequestTimeoutError || (error instanceof Error && error.name === 'RequestTimeoutError');
}

export function isRequestCancelledError(error: unknown): boolean {
  return error instanceof RequestCancelledError || (error instanceof Error && error.name === 'RequestCancelledError');
}

/** Race cancellation as well as IO: a fetch/body implementation may ignore abort.
 * Losing IO is observed by Promise.race but cannot complete the caller later.
 */
export async function withinRequestLifetime<T>(
  controller: AbortController, operation: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal,
): Promise<T> {
  const abort = () => controller.abort();
  let timedOut = false;
  const failed = () => timedOut ? new RequestTimeoutError() : new RequestCancelledError();
  if (parent?.aborted || controller.signal.aborted) { controller.abort(); throw failed(); }
  let rejectAbort!: () => void;
  const interrupted = new Promise<never>((_resolve, reject) => { rejectAbort = () => reject(failed()); });
  controller.signal.addEventListener('abort', rejectAbort, { once: true });
  parent?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; abort(); }, REQUEST_DEADLINE_MS);
  try {
    return await Promise.race([operation(controller.signal), interrupted]);
  } finally {
    clearTimeout(timer);
    parent?.removeEventListener('abort', abort);
    controller.signal.removeEventListener('abort', rejectAbort);
  }
}
