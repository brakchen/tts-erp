import 'vite/modulepreload-polyfill';
import './styles.css';
import { Alert, Button, Spinner } from '@heroui/react';
import { Component, Suspense, lazy, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import AppShell from './app/AppShell';
import SessionActions from './app/SessionActions';
import { pageLoader } from './app/page-loader';
import { readBootstrap } from './bootstrap';
import { setUnauthorizedHandler } from './shared/api/client';
import { pathFor } from './shared/api/paths';

function StartupFailure() {
  return <div className="p-6"><Alert status="danger"><Alert.Content>
    <Alert.Title>页面加载失败</Alert.Title>
    <Alert.Description>请刷新页面；如仍失败，请联系管理员核对前端发布版本。</Alert.Description>
    <Button onPress={() => window.location.reload()}>刷新页面</Button>
  </Alert.Content></Alert></div>;
}

class RenderBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { console.error('Frontend render failed; check the release assets.'); }
  render() { return this.state.failed ? <StartupFailure /> : this.props.children; }
}

const mount = document.getElementById('frontend-root') ?? document.body.appendChild(document.createElement('main'));
const root = createRoot(mount);
try {
  const bootstrap = readBootstrap();
  const publicLogin = bootstrap.pageId === 'login';
  const title = publicLogin ? '登录' : bootstrap.navigation.find((item) => item.id === bootstrap.pageId)?.label;
  if (!title) throw new Error('当前页面不在服务端导航目录中');
  const Page = lazy(pageLoader(bootstrap.pageId));
  let redirecting = false;
  const disposeUnauthorized = setUnauthorizedHandler(() => {
    if (publicLogin || redirecting) return;
    redirecting = true;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(pathFor(bootstrap.rootPath, `/v2/auth/login?next=${next}`));
  });
  import.meta.hot?.dispose(() => { disposeUnauthorized(); root.unmount(); });
  document.title = `${title} · tts-erp`;
  root.render(<RenderBoundary><AppShell bootstrap={bootstrap} title={title} actions={publicLogin ? undefined : <SessionActions rootPath={bootstrap.rootPath} />}>
    <Suspense fallback={<div role="status" className="flex items-center gap-3"><Spinner />加载页面…</div>}>
      <Page bootstrap={bootstrap} />
    </Suspense>
  </AppShell></RenderBoundary>);
} catch {
  root.render(<StartupFailure />);
}
