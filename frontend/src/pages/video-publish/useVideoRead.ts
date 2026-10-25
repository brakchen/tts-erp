import { useEffect, useRef, useState } from 'react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';

interface ReadState<T> { key: string; data?: T; error?: string; loading: boolean; checkedAt?: string }
/** Page-owned GET lifecycle. Only the exact last validated payload can satisfy a conditional 304. */
export function useVideoRead<T>(root: string, path: string | null, decode: (value: unknown) => T, revision: number, auto: boolean, visible: boolean) {
  const key = path === null ? '' : `${root}\n${path}`;
  const [state, setState] = useState<ReadState<T>>({ key, loading: path !== null });
  const [blocked, setBlocked] = useState(false);
  const notBefore = useRef(0);
  const cooldownCancel = useRef<(() => void) | undefined>(undefined);
  const cache = useRef<{ key: string; etag: string | null; data: T } | undefined>(undefined);
  const lastAuto = useRef(auto);
  const lastVisible = useRef(visible);
  const lastAttempt = useRef<{ key: string; revision: number } | undefined>(undefined);
  const explicitAfter = useRef<number | undefined>(undefined);
  useEffect(() => () => cooldownCancel.current?.(), []);
  useEffect(() => {
    const justPaused = lastAuto.current && !auto;
    const inactiveResume = !auto && !lastVisible.current && visible && lastAttempt.current?.key === key && lastAttempt.current.revision === revision;
    lastAuto.current = auto;
    lastVisible.current = visible;
    if (!path) { setState({ key, loading: false }); return; }
    if (!visible || justPaused || inactiveResume) {
      setState((old) => old.key === key ? { ...old, loading: false } : { key, loading: false });
      return;
    }
    if (performance.now() < notBefore.current || explicitAfter.current === revision) {
      setState({ key, loading: false, error: '读取冷却中；请等待服务端指定时间后明确刷新。' }); return;
    }
    lastAttempt.current = { key, revision };
    const controller = new AbortController(); let active = true;
    let cancelPoll: (() => void) | undefined;
    async function read() {
      // The request is serialized with its own response + five-second timer, never setInterval.
      const previous = cache.current?.key === key ? cache.current : undefined;
      const tag = previous?.etag;
      let allowPoll = true;
      setState((old) => ({ ...(old.key === key ? old : { key }), loading: true, error: undefined }));
      try {
        let data: T;
        try {
          const result = await requestJson<unknown>(root, path!, { method: 'GET', signal: controller.signal, headers: tag ? { 'If-None-Match': tag } : {} }, parseIdentityJson);
          data = decode(result.data);
          if (!active) return;
          cache.current = { key, data, etag: result.headers.get('ETag') };
        } catch (error: unknown) {
          if (error instanceof ApiError && error.status === 304 && tag && previous && cache.current === previous) data = previous.data;
          else throw error;
        }
        if (active) setState({ key, data, loading: false, checkedAt: new Date().toISOString() });
      } catch (error: unknown) {
        if (!active) return;
        cache.current = undefined;
        const delay = error instanceof ApiError ? error.retryAfter ?? 0 : 0;
        if (delay > 0) {
          allowPoll = false;
          explicitAfter.current = revision;
          notBefore.current = Math.max(notBefore.current, performance.now() + delay * 1000);
          cooldownCancel.current?.(); setBlocked(true);
          cooldownCancel.current = scheduleReadDelay(() => setBlocked(false), notBefore.current - performance.now());
        }
        // Body/message/detail are deliberately not rendered: they may contain credentials or signed URLs.
        const message = error instanceof ApiError ? error.status === 403 ? '无权读取（HTTP 403），这不是空列表。'
          : error.status === 401 ? '会话已失效（HTTP 401），请重新登录。'
          : error.status === 404 ? '当前记录不存在（HTTP 404）。' : `读取失败（HTTP ${error.status}），请明确刷新重试。`
          : error instanceof Error && error.message === '视频发布响应格式错误' ? error.message : '读取未完成，请明确刷新重试。';
        setState({ key, error: message, loading: false });
        // Authorization failures and cooldowns never turn into background replay loops.
        if (error instanceof ApiError && [401, 403, 429].includes(error.status)) allowPoll = false;
      } finally {
        if (active && auto && visible && allowPoll) cancelPoll = scheduleReadDelay(() => { void read(); }, 5000);
      }
    }
    void read();
    return () => { active = false; controller.abort(); cancelPoll?.(); };
  }, [root, path, key, decode, revision, auto, visible]);
  return { ...(state.key === key ? state : { key, loading: path !== null }), blocked };
}
