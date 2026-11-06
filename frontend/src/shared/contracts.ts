import type { ReactNode } from 'react';

export interface NavigationItem {
  id: string;
  label: string;
  group: string;
  href: string;
}

export interface FrontendBootstrap {
  rootPath: string;
  pageId: string;
  navigation: NavigationItem[];
  releaseId: string;
}

export interface PageProps {
  bootstrap: FrontendBootstrap;
}

export interface AppShellProps extends PageProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}
