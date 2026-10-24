import type { ComponentType } from 'react';
import type { PageProps } from '../shared/contracts';

const loaders: Record<string, () => Promise<{ default: ComponentType<PageProps> }>> = {
  dashboard: () => import('../pages/dashboard'),
  'intercept-requests': () => import('../pages/intercept-requests'),
  'intercept-stats': () => import('../pages/intercept-stats'),
  shops: () => import('../pages/shops'),
  'enum-map': () => import('../pages/enum-map'),
};

/** Code mapping only: page metadata and permissions remain backend-owned. */
export function pageLoader(pageId: string) {
  if (!Object.hasOwn(loaders, pageId)) throw new Error('当前页面未提供前端模块');
  return loaders[pageId];
}
