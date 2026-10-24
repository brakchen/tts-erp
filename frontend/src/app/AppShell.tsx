import { Button, Drawer, Link } from '@heroui/react';
import { useEffect, useId, useState } from 'react';
import type { AppShellProps, FrontendBootstrap } from '../shared/contracts';

const desktopQuery = '(min-width: 1024px)';
const collapsedPreference = 'sidebar-collapsed';

function readCollapsedPreference(): boolean {
  try {
    return localStorage.getItem(collapsedPreference) === '1';
  } catch {
    return false;
  }
}

function Navigation({ bootstrap, onNavigate }: { bootstrap: FrontendBootstrap; onNavigate?: () => void }) {
  const groups = new Map<string, FrontendBootstrap['navigation']>();
  for (const item of bootstrap.navigation) {
    const items = groups.get(item.group) ?? [];
    items.push(item);
    groups.set(item.group, items);
  }

  return (
    <nav aria-label="主导航" className="min-w-0 space-y-4">
      {Array.from(groups, ([group, items]) => (
        <section key={group} className="min-w-0 space-y-1">
          {group && <h2 className="px-3 text-sm font-medium text-muted break-words">{group}</h2>}
          <ul className="space-y-1">
            {items.map((item) => (
              <li key={item.id} className="min-w-0">
                <Link
                  href={item.href}
                  aria-current={item.id === bootstrap.pageId ? 'page' : undefined}
                  onPress={onNavigate}
                  className={`w-full rounded px-3 py-2 whitespace-normal break-words ${item.id === bootstrap.pageId ? 'bg-default font-semibold' : ''}`}
                >
                  <span className="min-w-0 break-words">{item.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </nav>
  );
}

/** Shared MPA layout; the server owns menu visibility and all navigation destinations. */
export default function AppShell({ bootstrap, title, description, actions, children }: AppShellProps) {
  const hasNavigation = bootstrap.navigation.length > 0;
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia(desktopQuery).matches);
  const [collapsed, setCollapsed] = useState(readCollapsedPreference);
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigationId = useId();

  useEffect(() => {
    const media = window.matchMedia(desktopQuery);
    const update = () => {
      setIsDesktop(media.matches);
      setMobileOpen(false);
    };
    media.addEventListener('change', update);
    update();
    return () => media.removeEventListener('change', update);
  }, []);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      // Preserve the existing UI-only preference across old and migrated pages.
      localStorage.setItem(collapsedPreference, next ? '1' : '0');
    } catch {
      // Storage denial must not block navigation or the in-memory preference.
    }
  }

  return (
    <div className="flex min-h-dvh min-w-0">
      {hasNavigation && isDesktop && !collapsed && (
        <aside id={navigationId} className="sticky top-0 max-h-dvh w-64 shrink-0 overflow-y-auto border-r border-separator p-4">
          <Navigation bootstrap={bootstrap} />
        </aside>
      )}
      <div className="min-w-0 flex-1">
        <header className="flex min-w-0 flex-wrap items-start gap-4 border-b border-separator p-4 lg:p-6">
          {hasNavigation && (isDesktop ? (
            <Button variant="secondary" aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'} aria-expanded={!collapsed} aria-controls={collapsed ? undefined : navigationId} onPress={toggleCollapsed}>
              {collapsed ? '展开侧边栏' : '折叠侧边栏'}
            </Button>
          ) : (
            <Drawer.Root isOpen={mobileOpen} onOpenChange={setMobileOpen}>
              <Drawer.Trigger aria-label="打开主导航">菜单</Drawer.Trigger>
              <Drawer.Backdrop>
                <Drawer.Content placement="left" className="max-w-full">
                  <Drawer.Dialog>
                    <Drawer.Header>
                      <Drawer.Heading>主导航</Drawer.Heading>
                      <Drawer.CloseTrigger aria-label="关闭主导航" />
                    </Drawer.Header>
                    <Drawer.Body>
                      <Navigation bootstrap={bootstrap} onNavigate={() => setMobileOpen(false)} />
                    </Drawer.Body>
                  </Drawer.Dialog>
                </Drawer.Content>
              </Drawer.Backdrop>
            </Drawer.Root>
          ))}
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold break-words">{title}</h1>
            {description && <p className="text-muted break-words">{description}</p>}
          </div>
          {actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
        </header>
        <main className="mx-auto w-full min-w-0 max-w-screen-2xl overflow-x-auto p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
