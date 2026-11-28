import { Button } from '@heroui/react';
import { useEffect, useState } from 'react';
import { requestJson } from '../shared/api/client';
import { commandFailure } from '../shared/api/command-errors';
import { pathFor } from '../shared/api/paths';
import { useOperationToast } from '../shared/operation-toast';

interface SessionInfo { authenticated: boolean; displayName?: string; username?: string }

/** Global session controls; authenticated:false also represents valid non-cookie modes. */
export default function SessionActions({ rootPath, onSignedOut }: {rootPath: string; onSignedOut?: () => void}) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const notify = useOperationToast();
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
    } catch (error: unknown) {
      notify.failure('退出登录未完成', commandFailure(error, {}).message);
      setBusy(false);
    }
  }
  return <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
    {session?.authenticated && <><span className="min-w-0 max-w-full break-words">{session.displayName || session.username || '已登录'}</span>
      <Button variant="secondary" isDisabled={busy} isPending={busy} onPress={logout}>{busy ? '正在退出…' : '退出登录'}</Button></>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
