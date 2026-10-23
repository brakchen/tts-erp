import { Button } from '@heroui/react';
import { useEffect, useState } from 'react';
import { requestJson } from '../shared/api/client';
import { pathFor } from '../shared/api/paths';

interface SessionInfo { authenticated: boolean; displayName?: string; username?: string }

/** Global session controls; authenticated:false also represents valid non-cookie modes. */
export default function SessionActions({ rootPath, onSignedOut }: {rootPath: string; onSignedOut?: () => void}) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    requestJson<SessionInfo>(rootPath, '/v2/auth/me', { signal: controller.signal })
      .then(({ data }) => { if (current) setSession(data); })
      .catch(() => { if (current) setError('会话信息暂不可用'); });
    return () => { current = false; controller.abort(); };
  }, [rootPath]);
  async function logout() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await requestJson<void>(rootPath, '/v2/auth/logout', { method: 'POST' });
      if (onSignedOut) onSignedOut();
      else window.location.assign(pathFor(rootPath, '/v2/auth/login'));
    } catch {
      setError('退出结果未确认，请重试或重新登录');
      setBusy(false);
    }
  }
  return <div className="flex flex-wrap items-center gap-2">
    {session?.authenticated && <><span>{session.displayName || session.username || '已登录'}</span>
      <Button variant="secondary" isDisabled={busy} onPress={logout}>退出登录</Button></>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
