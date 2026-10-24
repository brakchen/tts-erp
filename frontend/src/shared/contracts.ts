import type { ReactNode } from 'react';

export interface NavigationItem {
  id: string;
  label: string;
  group: string;
  href: string;
}

export interface RoleCapabilities {
  readwrite: boolean;
  admin: boolean;
}

export interface FrontendBootstrap {
  rootPath: string;
  pageId: string;
  navigation: NavigationItem[];
  releaseId: string;
  /** Backend role-gate hints only; APIs remain authoritative for every command. */
  roleCapabilities?: RoleCapabilities;
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
