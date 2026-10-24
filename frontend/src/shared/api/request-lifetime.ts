// Shared extraction of the validated users-page primitive. This bounds local UI
// waiting, not server execution, HTTP policy, Retry-After or automatic retries.
export const REQUEST_DEADLINE_MS = 30_000;

/** Race cancellation as well as IO: a fetch/body implementation may ignore abort.
 * Losing IO is observed by Promise.race but cannot complete the caller later.
 */
export async function withinRequestLifetime<T>(
  controller: AbortController, operation: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal,
): Promise<T> {
  const abort = () => controller.abort();
  const cancelled = () => new Error('请求等待已结束，结果仍需核对');
  if (parent?.aborted || controller.signal.aborted) { controller.abort(); throw cancelled(); }
  let rejectAbort!: () => void;
  const interrupted = new Promise<never>((_resolve, reject) => { rejectAbort = () => reject(cancelled()); });
  controller.signal.addEventListener('abort', rejectAbort, { once: true });
  parent?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, REQUEST_DEADLINE_MS);
  try {
    return await Promise.race([operation(controller.signal), interrupted]);
  } finally {
    clearTimeout(timer);
    parent?.removeEventListener('abort', abort);
    controller.signal.removeEventListener('abort', rejectAbort);
  }
}
