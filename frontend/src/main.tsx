import 'vite/modulepreload-polyfill';
import './styles.css';
import { Alert, Button, Spinner, buttonVariants } from '@heroui/react';
import { Component, Suspense, lazy, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import AppShell from './app/AppShell';
import SessionActions from './app/SessionActions';
import { pageLoader } from './app/page-loader';
import { readBootstrap } from './bootstrap';
import MessagePage from './pages/message';
import type { PageProps } from './shared/contracts';
import { setUnauthorizedHandler } from './shared/api/client';
import { pathFor } from './shared/api/paths';

function StartupFailure() {
  return <div className="p-6"><Alert status="danger"><Alert.Content>
    <Alert.Title>页面加载失败</Alert.Title>
    <Alert.Description>请刷新页面；如仍失败，请联系管理员核对前端发布版本。</Alert.Description>
    <Button onPress={() => window.location.reload()}>刷新页面</Button>
  </Alert.Content></Alert></div>;
}

/** A completed single-use result stays readable if its enhanced view fails. */
function CompletedMessageFailure({ bootstrap }: PageProps) {
  const data = bootstrap.pageData;
  if (data?.kind !== 'message') return <StartupFailure />;
  return <div className="mx-auto max-w-3xl space-y-4 p-6">
    <Alert status={data.tone}><Alert.Content>
      <Alert.Title>{data.title}</Alert.Title><Alert.Description>{data.message}</Alert.Description>
    </Alert.Content></Alert>
    <p>结果已处理，页面展示遇到问题。请根据上方结果继续操作。</p>
    {data.authorization && <div className="space-y-3">
      <p>链接有效至 {data.authorization.expiresAt}，仅可使用一次。</p>
      <p className="break-all"><code>{data.authorization.url}</code></p>
      <a className={buttonVariants()} href={data.authorization.url} target="_blank" rel="noopener noreferrer">打开授权页面</a>
    </div>}
    {data.sections.map((section, index) => <section key={index}><h2>{section.title}</h2>
      <dl>{section.facts.map((fact, factIndex) => <div key={factIndex}>
        <dt>{fact.label}</dt><dd className="break-all">{fact.value}</dd>
      </div>)}</dl>
    </section>)}
    <a className={buttonVariants({ variant: 'secondary' })} href={pathFor(bootstrap.rootPath, '/v2/pages/shops')}>前往店铺注册台</a>
    {data.action === 'login' && <a className={buttonVariants({ variant: 'secondary' })} href={pathFor(bootstrap.rootPath, '/v2/auth/login')}>返回登录页</a>}
  </div>;
}

class RenderBoundary extends Component<{children: ReactNode; fallback?: ReactNode}, {failed: boolean}> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { console.error('Frontend render failed; check the release assets.'); }
  render() { return this.state.failed ? this.props.fallback ?? <StartupFailure /> : this.props.children; }
}

const mount = document.getElementById('frontend-root') ?? document.body.appendChild(document.createElement('main'));
const root = createRoot(mount);
try {
  const bootstrap = readBootstrap();
  const publicLogin = bootstrap.pageId === 'login';
  const auxiliary = bootstrap.pageId === 'message' || bootstrap.pageId === 'oauth-onboard';
  const fullscreen = publicLogin || auxiliary;
  const title = publicLogin ? '登录' : bootstrap.pageData?.kind === 'message' ? bootstrap.pageData.title
    : bootstrap.pageData?.kind === 'oauth-onboard' ? '新店接入授权'
    : bootstrap.navigation.find((item) => item.id === bootstrap.pageId)?.label;
  if (!title) throw new Error('当前页面不在服务端导航目录中');
  // A completed callback/403 result is in the entry bundle. A failed lazy chunk
  // must never clear it or suggest refreshing an already-consumed callback.
  const Page = bootstrap.pageId === 'message' ? MessagePage : lazy(pageLoader(bootstrap.pageId));
  let redirecting = false;
  const disposeUnauthorized = setUnauthorizedHandler(() => {
    if (fullscreen || redirecting) return;
    redirecting = true;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(pathFor(bootstrap.rootPath, `/v2/auth/login?next=${next}`));
  });
  import.meta.hot?.dispose(() => { disposeUnauthorized(); root.unmount(); });
  document.title = `${title} · tts-erp`;
  root.render(<RenderBoundary fallback={bootstrap.pageId === 'message' ? <CompletedMessageFailure bootstrap={bootstrap} /> : undefined}><AppShell bootstrap={bootstrap} title={title} layout={fullscreen ? 'fullscreen' : 'workspace'} actions={fullscreen ? undefined : <SessionActions rootPath={bootstrap.rootPath} />}>
    <Suspense fallback={<div role="status" className="flex items-center gap-3"><Spinner />加载页面…</div>}>
      <Page bootstrap={bootstrap} />
    </Suspense>
  </AppShell></RenderBoundary>);
} catch {
  root.render(<StartupFailure />);
}
