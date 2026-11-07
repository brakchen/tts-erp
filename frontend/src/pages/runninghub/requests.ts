import { useEffect, useState } from 'react';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';

const messages: Record<string, string> = {
  ACTIVE_HUMAN_REQUIRED: '请选择在用的人物。',
  ACTIVE_HUMAN_TEMPLATE_REQUIRED: '所选模板或人物已不可用，请刷新后重新选择。',
  CLIPS_MUST_BELONG_TO_HUMAN: '参考视频已从该人物移除，请刷新后重新选择。',
  READY_ASSET_REQUIRED: '所选素材已不可用，请刷新后重新选择。',
  DISTINCT_VIDEO_CONTENTS_REQUIRED: '所选参考视频含重复内容，请选择不同视频。',
  BATCH_SIZE_EXCEEDED: '片段数超出单批次上限，请减少所选素材或片段。',
  FIRST_CLIP_READY_REQUIRED: '当前批次已无法续跑，请查看最新状态。',
};

export function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403) return '无权执行此操作。';
    if (error.status === 401) return '会话已过期，请重新登录。';
    if (error.status === 429) return '请求过于频繁，请稍后刷新。';
    if (typeof error.detail === 'string' && /^[A-Z0-9_: .-]+$/.test(error.detail)) {
      return messages[error.detail] ?? `操作未完成：${error.detail}`;
    }
  }
  return '请求未完成。请刷新查看实际状态，写操作不会自动重放。';
}

export function useRead<T>(root: string, path: string | null, revision: number, polling = false) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setData(undefined);
    setError('');
    if (!path) { setLoading(false); return; }
    const controller = new AbortController();
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      setLoading(true);
      try {
        const response = await requestJson<T>(root, path!, { signal: controller.signal }, parseIdentityJson);
        if (current) { setData(response.data); setError(''); }
      } catch (failure) { if (current) setError(errorText(failure)); }
      finally { if (current) setLoading(false); }
      if (current && polling) timer = setTimeout(() => { void read(); }, 10000);
    }
    void read();
    return () => { current = false; controller.abort(); clearTimeout(timer); };
  }, [root, path, revision, polling]);
  return { data, error, loading };
}
