import { useEffect, useRef, useState } from 'react';
import { Alert, Button, FieldError, Input, Label, TextField } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import type { PageProps } from '../../shared/contracts';
import { canonicalNext, confirmLogin, decodeCurrentIdentity, type CurrentIdentity } from './contracts';
import { leaveLogin } from './navigation';

interface Attempt { username: string; password: string; next: string; rootPath: string }
interface Recovery { phase: 'unverified' | 'checking' | 'checked' | 'failed'; current?: CurrentIdentity | null; retryAllowed: boolean }
const labels = { username: '用户名', password: '密码' };

export default function Login({ bootstrap }: PageProps) {
  const next = canonicalNext(bootstrap.rootPath, bootstrap.loginNextPath);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [cooldown, setCooldown] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const attempt = useRef<Attempt | undefined>(undefined);
  const recoveryGate = useRef<Recovery | undefined>(undefined);
  const controllers = useRef(new Set<AbortController>());
  const notBefore = useRef(0);
  const stopCooldown = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; stopCooldown.current?.();
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear(); attempt.current = undefined;
    };
  }, []);
  function updateRecovery(value: Recovery | undefined) { recoveryGate.current = value; setRecovery(value); }
  function holdCooldown(seconds: number) {
    if (seconds <= 0) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + seconds * 1000);
    stopCooldown.current?.(); setCooldown(true);
    stopCooldown.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, notBefore.current - performance.now());
  }
  function edited(field: 'username' | 'password', value: string) {
    if (value === (field === 'username' ? username : password)) return;
    if (field === 'username') setUsername(value); else setPassword(value);
    // A field-error map describes the submitted snapshot, not the newly edited draft.
    setFailure((previous) => previous ? { ...previous, fieldErrors: {} } : previous);
  }
  async function submit() {
    if (!next || inFlight.current || performance.now() < notBefore.current || !username.trim() || !password ||
        (recoveryGate.current && !recoveryGate.current.retryAllowed)) return;
    inFlight.current = true; setPending(true); setFailure(undefined); updateRecovery(undefined);
    const captured: Attempt = { username, password, next, rootPath: bootstrap.rootPath };
    attempt.current = captured;
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const { data, status } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(captured.rootPath, '/v2/auth/login', {
        method: 'POST', signal, body: JSON.stringify({ username: captured.username, password: captured.password, next: captured.next }),
      }));
      if (status !== 200) throw new Error('登录结果未确认');
      confirmLogin(data, captured.username);
      if (!mounted.current) return;
      attempt.current = undefined; setPassword(''); leaveLogin(captured.next);
    } catch (error: unknown) {
      if (!mounted.current) return;
      const result = commandFailure(error, labels);
      if (error instanceof ApiError && error.status === 401) result.message = '用户名或密码错误';
      if (error instanceof ApiError && error.status === 503) result.message = '登录服务暂不可用；提交结果未确认，请先查询当前会话';
      setFailure(result); holdCooldown(result.retryAfter);
      if (result.uncertain) updateRecovery({ phase: 'unverified', retryAllowed: false });
      else attempt.current = undefined;
    } finally {
      controllers.current.delete(controller); inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  }
  async function verify() {
    const captured = attempt.current;
    if (!captured || !recoveryGate.current || inFlight.current || performance.now() < notBefore.current) return;
    inFlight.current = true;
    // Every explicit recheck revokes an earlier retry decision and stale identity.
    updateRecovery({ phase: 'checking', retryAllowed: false });
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const { data } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(captured.rootPath, '/v2/auth/me', { signal }));
      const current = decodeCurrentIdentity(data);
      if (mounted.current) updateRecovery({ phase: 'checked', current, retryAllowed: false });
    } catch (error: unknown) {
      if (!mounted.current) return;
      if (error instanceof ApiError) holdCooldown(error.retryAfter ?? 0);
      updateRecovery({ phase: 'failed', retryAllowed: false });
    } finally { controllers.current.delete(controller); inFlight.current = false; }
  }
  function permitManualLogin() {
    const value = recoveryGate.current;
    if (inFlight.current || performance.now() < notBefore.current || value?.phase !== 'checked') return;
    updateRecovery({ ...value, retryAllowed: true });
  }
  function useCurrentSession() {
    const value = recoveryGate.current; const captured = attempt.current;
    if (!captured || inFlight.current || performance.now() < notBefore.current || value?.phase !== 'checked' || !value.current) return;
    attempt.current = undefined; setPassword(''); leaveLogin(captured.next);
  }
  const blocked = !next || pending || cooldown || Boolean(recovery && !recovery.retryAllowed);
  const checking = recovery?.phase === 'checking';
  return <div className="max-w-sm space-y-4">
    <p>使用账号登录以访问系统。会话由服务端管理。</p>
    {!next && <p role="alert">登录暂不可用：缺少有效的返回地址，请重新打开服务端登录页。</p>}
    <form aria-label="登录表单" className="space-y-4" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <TextField name="username" value={username} onChange={(value) => edited('username', value)} isRequired isDisabled={pending} isInvalid={Boolean(failure?.fieldErrors.username)}>
        <Label>用户名</Label><Input autoComplete="username" maxLength={64} /><FieldError>{failure?.fieldErrors.username}</FieldError>
      </TextField>
      <TextField name="password" value={password} onChange={(value) => edited('password', value)} isRequired isDisabled={pending} isInvalid={Boolean(failure?.fieldErrors.password)}>
        <Label>密码</Label><Input type="password" autoComplete="current-password" maxLength={128} /><FieldError>{failure?.fieldErrors.password}</FieldError>
      </TextField>
      <Button type="submit" isDisabled={blocked}>{pending ? '登录中…' : '登录'}</Button>
      {pending && <p role="status">正在等待登录响应…</p>}
    </form>
    {failure && <Alert status="danger"><Alert.Content><Alert.Title>登录未完成</Alert.Title><Alert.Description>{failure.message}</Alert.Description></Alert.Content></Alert>}
    {cooldown && <p role="status">请等待服务端指定的冷却时间；修改输入不会缩短等待，不会自动登录或查询。</p>}
    {recovery && <section aria-label="登录结果核对" className="space-y-3 break-words">
      <p>原请求用户名：{attempt.current?.username}</p>
      <p>查询仅反映当前 Cookie 对应的会话，不能证明原密码正确、原请求成功或本次签发了会话。输入和原请求仅保留在本页内存；不会自动重试。</p>
      <Button variant="secondary" isDisabled={checking || pending || cooldown} onPress={() => { void verify(); }}>查询当前会话</Button>
      {checking && <p role="status">查询中…</p>}
      {recovery.phase === 'failed' && <p role="status">查询未完成；请稍后手动查询，不能据此判定登录结果。</p>}
      {recovery.phase === 'checked' && <>
        <p role="status">查询完成：{recovery.current ? '当前存在已认证会话。' : '当前没有已认证会话，不代表原请求未生效。'}</p>
        {recovery.current && <><dl><dt>当前用户名</dt><dd>{recovery.current.username}</dd><dt>当前显示名称</dt><dd>{recovery.current.displayName ?? '—'}</dd></dl>
          <p>{recovery.current.username === attempt.current?.username.trim().toLowerCase() ? '当前用户名与原请求一致，但仍不能证明原登录成功。' : '当前用户名与原请求用户名不同；不会视为原登录成功。'}</p>
          <Button variant="secondary" isDisabled={cooldown} onPress={useCurrentSession}>使用已查询的当前会话继续</Button></>}
        <p>重新登录将使用当前表单输入，可能再次创建会话；继续使用当前会话不代表验证了原密码。请明确选择。</p>
        <Button variant="secondary" isDisabled={cooldown || recovery.retryAllowed} onPress={permitManualLogin}>我已了解，允许手动重新登录</Button>
        {recovery.retryAllowed && <p role="status">已允许手动登录；仍需点击登录，不会自动提交。重新查询将撤销此选择。</p>}
      </>}
    </section>}
  </div>;
}
