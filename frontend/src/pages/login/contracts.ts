import { pathFor } from '../../shared/api/paths';

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
export interface CurrentIdentity { username: string; displayName: string | null }

function identity(value: Record<string, unknown>): CurrentIdentity {
  if (typeof value.username !== 'string' || !value.username || !(value.displayName === null || typeof value.displayName === 'string') ||
      !['readonly', 'readwrite', 'admin'].includes(value.role as string) || !Array.isArray(value.pages) || !value.pages.every((page) => typeof page === 'string')) throw new Error('登录响应不可用');
  // Project only display facts. Role/pages are validated, never cached as authority.
  return { username: value.username, displayName: value.displayName };
}
export function confirmLogin(value: unknown, requestedUsername: string): void {
  if (!record(value) || value.ok !== true || identity(value).username !== requestedUsername.trim().toLowerCase()) throw new Error('登录结果未确认');
}
export function decodeCurrentIdentity(value: unknown): CurrentIdentity | null {
  if (!record(value)) throw new Error('会话响应不可用');
  if (value.authenticated === false) return null;
  if (value.authenticated !== true) throw new Error('会话响应不可用');
  return identity(value);
}

/** Consume the server's canonical target, not query-string navigation policy. */
export function canonicalNext(rootPath: string, value: unknown): string | undefined {
  if (typeof value !== 'string' || !value) return undefined;
  try {
    if (pathFor(rootPath, value) !== value) return undefined;
    const root = rootPath.replace(/\/+$/, '');
    if (root && (value === `${root}${root}` || value.startsWith(`${root}${root}/`))) return undefined;
    return value;
  } catch { return undefined; }
}
