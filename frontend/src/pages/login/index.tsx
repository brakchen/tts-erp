import { useEffect, useRef, useState } from 'react';
import { Alert, Button, FieldError, Input, InputGroup, Label, TextField } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import type { PageProps } from '../../shared/contracts';
import { canonicalNext, confirmLogin, LoginContractError } from './contracts';
import { leaveLogin } from './navigation';
import BrandPanel from './BrandPanel';
import './login.css';

const labels = { username: '用户名', password: '密码' };

function loginAlert(failure: CommandFailure, remaining: number): string {
  if (remaining > 0 && failure.message.startsWith('登录过于频繁')) return `登录过于频繁，请 ${remaining} 秒后再试`;
  return failure.message;
}

export default function Login({ bootstrap }: PageProps) {
  const next = canonicalNext(bootstrap.rootPath, bootstrap.loginNextPath);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [cooldown, setCooldown] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const controllers = useRef(new Set<AbortController>());
  const notBefore = useRef(0);
  const stopCooldown = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; stopCooldown.current?.();
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
    };
  }, []);
  function holdCooldown(seconds: number) {
    if (seconds <= 0) return;
    notBefore.current = Math.max(notBefore.current, performance.now() + seconds * 1000);
    stopCooldown.current?.();
    const tick = () => {
      if (!mounted.current) return;
      const left = Math.ceil((notBefore.current - performance.now()) / 1000);
      if (left <= 0) {
        setCooldown(false); setRemaining(0); stopCooldown.current = undefined; return;
      }
      setCooldown(true); setRemaining(left);
      stopCooldown.current = scheduleReadDelay(tick, Math.min(1000, notBefore.current - performance.now()));
    };
    tick();
  }
  function edited(field: 'username' | 'password', value: string) {
    if (value === (field === 'username' ? username : password)) return;
    if (field === 'username') setUsername(value); else setPassword(value);
    // A field-error map describes the submitted snapshot, not the newly edited draft.
    setFailure((previous) => previous ? { ...previous, fieldErrors: {} } : previous);
  }
  async function submit() {
    if (!next || inFlight.current || performance.now() < notBefore.current || !username.trim() || !password) return;
    inFlight.current = true; setPending(true); setFailure(undefined);
    const captured = { username, password, next, rootPath: bootstrap.rootPath };
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const { data, status } = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(captured.rootPath, '/v2/auth/login', {
        method: 'POST', signal, body: JSON.stringify({ username: captured.username, password: captured.password, next: captured.next }),
      }));
      if (status !== 200) throw new LoginContractError();
      confirmLogin(data, captured.username);
      if (!mounted.current) return;
      setPassword(''); leaveLogin(captured.next);
    } catch (error: unknown) {
      if (!mounted.current) return;
      const result = commandFailure(error, labels, 'login');
      setFailure(result);
      const status = error instanceof ApiError ? error.status : undefined;
      if (status === 429 || ((status === 500 || status === 503) && result.retryAfter > 0)) holdCooldown(result.retryAfter);
    } finally {
      controllers.current.delete(controller); inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  }
  const blocked = !next || pending || cooldown;
  return <div className="login-page">
    <div className="login-layout">
      <BrandPanel />
      <section className="login-access" aria-labelledby="login-welcome">
        <div className="login-access-caption"><span className="login-access-dot" aria-hidden="true" />你的店铺，你的工作台</div>
        <div className="login-form-content">
          <p className="login-eyebrow">WELCOME BACK</p>
          <h2 id="login-welcome" className="login-welcome">欢迎回来</h2>
          <p className="login-intro">登录工作账号，继续今天的经营。</p>
          <div className="login-form-body">
            {!next && <p role="alert">登录暂不可用：缺少有效的返回地址，请重新打开服务端登录页。</p>}
            <form aria-label="登录表单" className="space-y-5" aria-busy={pending} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
              <TextField name="username" value={username} onChange={(value) => edited('username', value)} isRequired isDisabled={pending} isInvalid={Boolean(failure?.fieldErrors.username)}>
                <Label>用户名</Label><Input className="min-h-12 text-base" fullWidth autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="请输入用户名" maxLength={64} /><FieldError>{failure?.fieldErrors.username}</FieldError>
              </TextField>
              <TextField name="password" value={password} onChange={(value) => edited('password', value)} isRequired isDisabled={pending} isInvalid={Boolean(failure?.fieldErrors.password)}>
                <Label>密码</Label>
                <InputGroup className="min-h-12" fullWidth>
                  <InputGroup.Input className="text-base" type={passwordVisible ? 'text' : 'password'} autoComplete="current-password" placeholder="请输入密码" maxLength={128} />
                  <InputGroup.Suffix>
                    <Button type="button" variant="ghost" size="sm" isDisabled={pending} aria-label={passwordVisible ? '隐藏密码' : '显示密码'} aria-pressed={passwordVisible} onPress={() => setPasswordVisible((value) => !value)}>
                      {passwordVisible ? '隐藏' : '显示'}
                    </Button>
                  </InputGroup.Suffix>
                </InputGroup>
                <FieldError>{failure?.fieldErrors.password}</FieldError>
              </TextField>
              {failure && <Alert status="danger"><Alert.Content><Alert.Title>登录未完成</Alert.Title><Alert.Description>{loginAlert(failure, remaining)}</Alert.Description></Alert.Content></Alert>}
              <Button type="submit" fullWidth size="lg" isDisabled={blocked}>{pending ? '登录中…' : '登录'}<span aria-hidden="true">↗</span></Button>
              {pending && <p role="status">正在等待登录响应…</p>}
            </form>
          </div>
          <p className="login-account-help">账号由管理员分配。<br />如需开通账号或重置密码，请联系管理员。</p>
        </div>
        <p className="login-access-footer">TTS ERP<span aria-hidden="true"> / </span>店铺运营工作台</p>
      </section>
    </div>
  </div>;
}
