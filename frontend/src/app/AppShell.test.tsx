import { Button } from '@heroui/react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrontendBootstrap } from '../shared/contracts';
import AppShell from './AppShell';

const longLabel = '拦截记录：跨店铺长名称与请求详情查询 / Request inspection';
const bootstrap: FrontendBootstrap = {
  rootPath: '/tts',
  pageId: 'intercept-requests',
  releaseId: 'TEST_release',
  navigation: [
    { id: 'dashboard', label: '控制台', group: '总览', href: '/tts/v2/pages/dashboard' },
    { id: 'intercept-requests', label: longLabel, group: '数据工具', href: '/tts/v2/pages/intercept-requests?source=menu' },
    { id: 'intercept-stats', label: '拦截统计', group: '数据工具', href: '/tts/v2/pages/intercept-stats' },
  ],
};

function viewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  const media = new EventTarget() as MediaQueryList;
  Object.defineProperties(media, {
    matches: { get: () => window.innerWidth >= 1024 },
    media: { value: '(min-width: 1024px)' },
  });
  vi.spyOn(window, 'matchMedia').mockImplementation((query) => {
    if (query.includes('1024px')) return media;
    const other = new EventTarget() as MediaQueryList;
    Object.defineProperties(other, { matches: { value: false }, media: { value: query } });
    return other;
  });
  return (nextWidth: number) => act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: nextWidth });
    media.dispatchEvent(new Event('change'));
    window.dispatchEvent(new Event('resize'));
  });
}

function renderShell() {
  return render(
    <AppShell bootstrap={bootstrap} title="拦截记录" description="查询捕获的请求" actions={<Button>刷新列表</Button>}>
      <p>TEST_page_content</p>
    </AppShell>,
  );
}

describe('AppShell', () => {
  beforeEach(() => {
    localStorage.clear();
    viewport(1440);
  });

  it.each([375, 1440, 2560])('shares the header and main without empty business-navigation controls at %ipx', (width) => {
    viewport(width);
    render(<AppShell bootstrap={{ ...bootstrap, pageId: 'login', navigation: [], roleCapabilities: { readwrite: false, admin: false }, loginNextPath: '/tts/v2/pages/dashboard' }} title="登录"><p>TEST_public_login</p></AppShell>);
    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1, name: '登录' })).toBeVisible();
    expect(within(screen.getByRole('main')).getByText('TEST_public_login')).toBeVisible();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /侧边栏|主导航/ })).not.toBeInTheDocument();
  });

  it('renders only server navigation with real prefixed hrefs, long labels and current-page semantics', () => {
    renderShell();
    const navigation = screen.getByRole('navigation', { name: '主导航' });
    expect(within(navigation).getAllByRole('link')).toHaveLength(3);
    expect(within(navigation).getByText('总览')).toBeVisible();
    expect(within(navigation).getAllByText('数据工具')).toHaveLength(1);
    const active = within(navigation).getByRole('link', { name: longLabel });
    expect(active).toHaveAttribute('href', bootstrap.navigation[1].href);
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(within(navigation).getByRole('link', { name: '控制台' })).not.toHaveAttribute('aria-current');
    expect(screen.queryByRole('link', { name: '用户管理' })).not.toBeInTheDocument();
  });

  it('shares header, actions and content between different page consumers without an empty footer', () => {
    const { rerender } = renderShell();
    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1, name: '拦截记录' })).toBeVisible();
    expect(screen.getByText('查询捕获的请求')).toBeVisible();
    expect(screen.getByRole('button', { name: '刷新列表' })).toBeVisible();
    expect(within(screen.getByRole('main')).getByText('TEST_page_content')).toBeVisible();
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();
    rerender(<AppShell bootstrap={{ ...bootstrap, pageId: 'dashboard' }} title="控制台"><p>TEST_dashboard</p></AppShell>);
    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1, name: '控制台' })).toBeVisible();
    expect(screen.getByRole('link', { name: '控制台' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: longLabel })).not.toHaveAttribute('aria-current');
    expect(screen.queryByText('查询捕获的请求')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '刷新列表' })).not.toBeInTheDocument();
  });

  it('restores and persists the existing desktop fold preference without losing the expand control', async () => {
    localStorage.setItem('sidebar-collapsed', '1');
    const user = userEvent.setup();
    renderShell();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '展开侧边栏' }));
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeVisible();
    expect(localStorage.getItem('sidebar-collapsed')).toBe('0');
    await user.click(screen.getByRole('button', { name: '折叠侧边栏' }));
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(localStorage.getItem('sidebar-collapsed')).toBe('1');
    expect(screen.getByRole('button', { name: '展开侧边栏' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps the shell usable when browser preference storage is denied', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('denied'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('denied'); });
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '折叠侧边栏' }));
    await user.click(screen.getByRole('button', { name: '展开侧边栏' }));
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeVisible();
  });

  it('opens narrow-screen navigation by keyboard, traps focus and returns focus on Escape or close', async () => {
    viewport(390);
    localStorage.setItem('sidebar-collapsed', '1');
    const user = userEvent.setup();
    renderShell();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: '打开主导航' });
    trigger.focus();
    await user.keyboard('{Enter}');
    const dialog = await screen.findByRole('dialog', { name: '主导航' });
    expect(within(dialog).getByRole('link', { name: longLabel })).toHaveAttribute('href', bootstrap.navigation[1].href);
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const links = within(dialog).getAllByRole('link');
    links[links.length - 1].focus();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    await user.click(trigger);
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '关闭主导航' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(localStorage.getItem('sidebar-collapsed')).toBe('1');
  });

  it('dismisses narrow navigation on ordinary link activation without preventing navigation', async () => {
    viewport(390);
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '打开主导航' }));
    const link = within(await screen.findByRole('dialog')).getByRole('link', { name: longLabel });
    let prevented = true;
    // onPress can dismiss/unmount the drawer before click bubbles to document.
    // Observe the anchor itself and suppress only jsdom's unimplemented document navigation.
    const observe = (event: MouseEvent) => { prevented = event.defaultPrevented; event.preventDefault(); };
    link.addEventListener('click', observe, { once: true });
    await user.click(link);
    expect(prevented).toBe(false);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('dismisses narrow navigation on an outside press and returns focus', async () => {
    viewport(390);
    const user = userEvent.setup();
    renderShell();
    const trigger = screen.getByRole('button', { name: '打开主导航' });
    await user.click(trigger);
    await screen.findByRole('dialog');
    await user.click(document.body);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('dismisses an open drawer on desktop resize and does not reopen it when returning to mobile', async () => {
    const resize = viewport(390);
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '打开主导航' }));
    await screen.findByRole('dialog');
    resize(1440);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeVisible();
    resize(390);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
});
