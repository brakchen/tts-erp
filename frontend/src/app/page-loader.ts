import type { ComponentType } from 'react';
import type { PageProps } from '../shared/contracts';

const loaders: Record<string, () => Promise<{ default: ComponentType<PageProps> }>> = {
  dashboard: () => import('../pages/dashboard'),
  'intercept-requests': () => import('../pages/intercept-requests'),
  'intercept-stats': () => import('../pages/intercept-stats'),
  shops: () => import('../pages/shops'),
  'enum-map': () => import('../pages/enum-map'),
  'ad-daily': () => import('../pages/ad-daily'),
  'sync-jobs': () => import('../pages/sync-jobs'),
  users: () => import('../pages/users'),
  'runtime-configs': () => import('../pages/runtime-configs'),
  'intercept-configs': () => import('../pages/intercept-configs'),
  'spu-profit-deterioration': () => import('../pages/spu-profit-deterioration'),
  login: () => import('../pages/login'),
};

/** Code mapping only: page metadata and permissions remain backend-owned. */
export function pageLoader(pageId: string) {
  if (!Object.hasOwn(loaders, pageId)) throw new Error('当前页面未提供前端模块');
  return loaders[pageId];
}
