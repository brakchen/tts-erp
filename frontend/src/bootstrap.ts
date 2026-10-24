import type { FrontendBootstrap, NavigationItem } from './shared/contracts';
import { pathFor } from './shared/api/paths';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

/** Validate the one backend-owned bootstrap before rendering any page. */
export function readBootstrap(): FrontendBootstrap {
  const node = document.getElementById('frontend-bootstrap');
  if (!node || node.getAttribute('type') !== 'application/json') throw new Error('缺少页面初始化数据');
  const value: unknown = JSON.parse(node.textContent ?? '');
  if (!isRecord(value) || typeof value.rootPath !== 'string' || !nonempty(value.pageId)
    || !nonempty(value.releaseId) || !Array.isArray(value.navigation)) {
    throw new Error('页面初始化数据格式无效');
  }
  pathFor(value.rootPath, '/');
  const navigation: NavigationItem[] = value.navigation.map((item: unknown) => {
    if (!isRecord(item) || !nonempty(item.id) || !nonempty(item.label)
      || typeof item.group !== 'string' || !nonempty(item.href)) throw new Error('导航数据格式无效');
    if (pathFor(value.rootPath as string, item.href) !== item.href) throw new Error('导航地址缺少部署前缀');
    return { id: item.id, label: item.label, group: item.group, href: item.href };
  });
  const bootstrap: FrontendBootstrap = { rootPath: value.rootPath, pageId: value.pageId, releaseId: value.releaseId, navigation };
  const hints = value.roleCapabilities;
  // Old documents remain readable; missing/malformed hints never enable writes.
  if (isRecord(hints) && typeof hints.readwrite === 'boolean' && typeof hints.admin === 'boolean') {
    bootstrap.roleCapabilities = { readwrite: hints.readwrite, admin: hints.admin };
  }
  return bootstrap;
}
