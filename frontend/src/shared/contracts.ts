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

/** Presentation-only documents outside the permission/navigation catalog. */
export interface MessageSection {
  title: string;
  facts: { label: string; value: string }[];
}
export interface MessagePageData {
  kind: 'message';
  title: string;
  message: string;
  tone: 'success' | 'danger' | 'warning' | 'default';
  action: 'shops' | 'login' | 'onboard';
  sections: MessageSection[];
  authorization?: { url: string; expiresAt: string };
}
export type AuxiliaryPageData = MessagePageData | { kind: 'oauth-onboard' };

export interface FrontendBootstrap {
  rootPath: string;
  pageId: string;
  navigation: NavigationItem[];
  releaseId: string;
  /** Validated display data; never a source for permissions or commands. */
  pageData?: AuxiliaryPageData;
  /** Anonymous login only: server-validated, same-origin, mount-prefixed next path. */
  loginNextPath?: string;
  /** Backend role-gate hints only; APIs remain authoritative for every command. */
  roleCapabilities?: RoleCapabilities;
}

export interface PageProps {
  bootstrap: FrontendBootstrap;
}

export interface AppShellProps extends PageProps {
  title: string;
  /** Fullscreen content still uses the shared landmarks, without the workspace chrome. */
  layout?: 'workspace' | 'fullscreen';
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}
