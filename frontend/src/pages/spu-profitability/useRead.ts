import { useEffect, useRef, useState } from 'react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';

export function useRead<T>(root: string, path: string | null, decode: (value: unknown) => T, revision = 0, interval = 0) {
  const decoder = useRef(decode); decoder.current = decode;
  const fence = useRef({ root, notBefore: 0 });
  const [state, setState] = useState<{ key: string | null; data?: T; error?: string; loading: boolean; retryAt?: number }>({ key: null, loading: false });
  const [tick, setTick] = useState(0);
  const key = path === null ? null : root + path;
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController(); let current = true; let cancelDelay: (() => void) | undefined;
    if (fence.current.root !== root) fence.current = { root, notBefore: 0 };
    if (performance.now() < fence.current.notBefore) {
      setState({ key, loading: false, error: '服务端要求等待 Retry-After', retryAt: fence.current.notBefore });
      cancelDelay = scheduleReadDelay(() => { if (current) setState((old) => ({ ...old, retryAt: undefined })); }, fence.current.notBefore - performance.now());
      return () => { current = false; cancelDelay?.(); };
    }
    setState({ key, loading: true });
    void withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, path, { signal }, parseIdentityJson)).then(({ data }) => {
      const decoded = decoder.current(data); if (current) setState({ key, data: decoded, loading: false });
    }).catch((error: unknown) => {
      if (!current) return;
      const retryAt = error instanceof ApiError && error.retryAfter ? performance.now() + error.retryAfter * 1000 : undefined;
      if (retryAt !== undefined) fence.current.notBefore = Math.max(fence.current.notBefore, retryAt);
      const message = error instanceof ApiError && error.code === 'FX_RATE_UNAVAILABLE' ? '汇率数据缺失，无法计算结果'
        : error instanceof ApiError && error.code === 'PRICE_FX_UNAVAILABLE' ? '价格币种缺少本次快照可用的汇率，无法计算结果'
          : error instanceof Error ? error.message : '读取失败';
      setState({ key, error: message, loading: false, retryAt });
      if (retryAt !== undefined) cancelDelay = scheduleReadDelay(() => { if (current) setState((old) => ({ ...old, retryAt: undefined })); }, retryAt - performance.now());
    });
    const timer = interval ? setInterval(() => { if (!document.hidden) setTick((n) => n + 1); }, interval) : undefined;
    return () => { current = false; controller.abort(); clearInterval(timer); cancelDelay?.(); };
  }, [root, path, revision, tick, interval, key]);
  return state.key === key ? { ...state, blocked: state.retryAt !== undefined } : { key, loading: key !== null, data: undefined, error: undefined, blocked: false };
}
