import { Button, Drawer, Link, Tooltip, buttonVariants } from '@heroui/react';
import { useEffect, useId, useState } from 'react';
import type { AppShellProps, FrontendBootstrap } from '../shared/contracts';
import AppBrand from './AppBrand';

const desktopQuery = '(min-width: 1024px)';
const collapsedPreference = 'sidebar-collapsed';

function NavigationIcon({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
      <path d={collapsed ? 'm13 9 3 3-3 3' : 'm16 9-3 3 3 3'} />
    </svg>
  );
}

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
    <nav aria-label="主导航" className="erp-navigation">
      {Array.from(groups, ([group, items]) => (
        <section key={group} className="erp-nav-section">
          {group && <h2 className="erp-nav-group break-words">{group}</h2>}
          <ul className="erp-nav-list">
            {items.map((item) => (
              <li key={item.id} className="min-w-0">
                <Link
                  href={item.href}
                  aria-current={item.id === bootstrap.pageId ? 'page' : undefined}
                  onPress={onNavigate}
                  className="erp-nav-link whitespace-normal break-words"
                >
                  <span className="erp-nav-indicator" aria-hidden="true" /><span className="min-w-0 break-words">{item.label}</span>
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
export default function AppShell({ bootstrap, title, description, actions, children, layout = 'workspace' }: AppShellProps) {
  const fullscreen = layout === 'fullscreen';
  const activeNavigation = bootstrap.navigation.find((item) => item.id === bootstrap.pageId);
  const hasNavigation = !fullscreen && bootstrap.navigation.length > 0;
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
    <div className="erp-shell flex min-h-dvh min-w-0">
      {hasNavigation && isDesktop && !collapsed && (
        <aside id={navigationId} className="erp-sidebar sticky top-0 max-h-dvh shrink-0 overflow-y-auto">
          <AppBrand />
          <Navigation bootstrap={bootstrap} />
          <p className="erp-sidebar-note">SHOP OPERATIONS</p>
        </aside>
      )}
      <div className="min-w-0 flex-1">
        <header className={fullscreen ? 'sr-only' : 'erp-header min-w-0'}>
          {hasNavigation && (isDesktop ? (
            <Tooltip delay={400}>
              <Button isIconOnly variant="ghost" className="erp-navigation-toggle" aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'} aria-expanded={!collapsed} aria-controls={collapsed ? undefined : navigationId} onPress={toggleCollapsed}>
                <NavigationIcon collapsed={collapsed} />
              </Button>
              <Tooltip.Content placement="bottom">{collapsed ? '展开侧边栏' : '折叠侧边栏'}</Tooltip.Content>
            </Tooltip>
          ) : (
            <Drawer.Root isOpen={mobileOpen} onOpenChange={setMobileOpen}>
              <Drawer.Trigger className={buttonVariants({ variant: 'ghost', isIconOnly: true, className: 'erp-navigation-toggle' })} aria-label="打开主导航"><NavigationIcon collapsed /></Drawer.Trigger>
              <Drawer.Backdrop>
                <Drawer.Content placement="left" className="erp-drawer max-w-full">
                  <Drawer.Dialog>
                    <Drawer.Header>
                      <Drawer.Heading>主导航</Drawer.Heading>
                      <Drawer.CloseTrigger aria-label="关闭主导航" />
                    </Drawer.Header>
                    <Drawer.Body>
                      <AppBrand />
                      <Navigation bootstrap={bootstrap} onNavigate={() => setMobileOpen(false)} />
                    </Drawer.Body>
                  </Drawer.Dialog>
                </Drawer.Content>
              </Drawer.Backdrop>
            </Drawer.Root>
          ))}
          <div className="erp-header-titles">
            {!fullscreen && <p className="erp-header-context">运营工作台{activeNavigation?.group ? ` / ${activeNavigation.group}` : ''}</p>}
            <h1 className="erp-header-title break-words">{title}</h1>
            {description && <p className="text-muted break-words">{description}</p>}
          </div>
          {!fullscreen && actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
        </header>
        <main className={fullscreen ? 'min-h-dvh min-w-0' : 'erp-main'}>{children}</main>
      </div>
    </div>
  );
}
