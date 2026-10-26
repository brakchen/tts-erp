import { afterEach, expect, it, vi } from 'vitest';
import { REQUEST_DEADLINE_MS, withinRequestLifetime } from './requestLifetime';

afterEach(() => vi.useRealTimers());
it('cleans the deadline after timely completion without aborting the completed request', async () => {
  vi.useFakeTimers(); const controller = new AbortController();
  await expect(withinRequestLifetime(controller, async () => 'TEST complete')).resolves.toBe('TEST complete');
  expect(vi.getTimerCount()).toBe(0); expect(controller.signal.aborted).toBe(false);
});
it('cleans the deadline on parent cancellation even when IO ignores the abort signal', async () => {
  vi.useFakeTimers(); const controller = new AbortController(), parent = new AbortController();
  const result = withinRequestLifetime(controller, () => new Promise<never>(() => {}), parent.signal);
  const rejected = expect(result).rejects.toThrow('请求等待已结束');
  parent.abort(); await rejected;
  expect(controller.signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
});
it('does not start IO for an already cancelled lifetime', async () => {
  vi.useFakeTimers(); const controller = new AbortController(), parent = new AbortController(); parent.abort();
  const operation = vi.fn(async () => 'TEST late');
  await expect(withinRequestLifetime(controller, operation, parent.signal)).rejects.toThrow('请求等待已结束');
  expect(operation).not.toHaveBeenCalled(); expect(controller.signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
});
it('keeps a finite full request budget and clears its timer on deadline expiry', async () => {
  vi.useFakeTimers(); const controller = new AbortController();
  const result = withinRequestLifetime(controller, () => new Promise<never>(() => {}));
  const rejected = expect(result).rejects.toThrow('请求等待已结束');
  expect(REQUEST_DEADLINE_MS).toBe(30_000);
  await vi.advanceTimersByTimeAsync(REQUEST_DEADLINE_MS - 1); expect(controller.signal.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1); await rejected;
  expect(controller.signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
});
