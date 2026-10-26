export interface User { id: string; username: string; displayName: string; status: string; roles: string[]; lastLoginAt: string | null; activeSessions: number }
export interface UserDetail extends Omit<User, 'activeSessions'> { pages: string[]; apiTier: string }
export interface Role { code: string; name: string; description: string | null; apiTier: string; isBuiltin: boolean; permissions: string[]; userCount: number }
export interface Permission { code: string; label: string; group: string }
export interface Catalog { roles: Role[]; allPermissions: Permission[] }
export interface Session { id: string; createdAt: string; expiresAt: string; lastSeenAt: string | null; revokedAt: string | null; ip: string | null; userAgent: string | null; active: boolean }
const fail = (): never => { throw new Error('用户数据结构不可用'); };
export function object(value: unknown): Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : fail(); }
const text = (value: unknown): string => typeof value === 'string' ? value : fail();
const nullable = (value: unknown): string | null => value === null ? null : text(value);
const count = (value: unknown): number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : fail();
const bool = (value: unknown): boolean => typeof value === 'boolean' ? value : fail();
const array = <T,>(value: unknown, decode: (item: unknown) => T): T[] => Array.isArray(value) ? value.map(decode) : fail();
export const strings = (value: unknown): string[] => array(value, text);
// Never repair an already-rounded identifier. The transport parser must retain raw long integers.
export function identifier(value: unknown): string { return typeof value === 'string' && /^\d+$/.test(value) ? value : typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? String(value) : fail(); }
function userBase(value: unknown) { const row = object(value); return { id: identifier(row.id), username: text(row.username), displayName: text(row.displayName), status: text(row.status), roles: strings(row.roles), lastLoginAt: nullable(row.lastLoginAt) }; }
export function users(value: unknown): User[] { return array(object(value).users, (value) => ({ ...userBase(value), activeSessions: count(object(value).activeSessions) })); }
export function detail(value: unknown): UserDetail { const row = object(value); return { ...userBase(value), pages: strings(row.pages), apiTier: text(row.apiTier) }; }
export function catalog(value: unknown): Catalog { const row = object(value); return { roles: array(row.roles, (value) => { const r = object(value); return { code: text(r.code), name: text(r.name), description: nullable(r.description), apiTier: text(r.apiTier), isBuiltin: bool(r.isBuiltin), permissions: strings(r.permissions), userCount: count(r.userCount) }; }), allPermissions: array(row.allPermissions, (value) => { const p = object(value); return { code: text(p.code), label: text(p.label), group: text(p.group) }; }) }; }
export function sessions(value: unknown): Session[] { return array(object(value).sessions, (value) => { const r = object(value); return { id: identifier(r.id), createdAt: text(r.createdAt), expiresAt: text(r.expiresAt), lastSeenAt: nullable(r.lastSeenAt), revokedAt: nullable(r.revokedAt), ip: nullable(r.ip), userAgent: nullable(r.userAgent), active: bool(r.active) }; }); }
export type CommandKind = 'createUser' | 'displayName' | 'roles' | 'status' | 'password' | 'createRole' | 'editRole' | 'deleteRole' | 'revoke';
export function commandResult(value: unknown, kind: CommandKind): void { const row = object(value); if (kind === 'createUser') { identifier(row.id); text(row.username); } else if (kind === 'createRole') { text(row.code); } else { if (row.ok !== true) fail(); if (kind === 'revoke') count(row.revoked); } }
export const labels: Record<string, string> = { username: '用户名', displayName: '显示名', password: '初始密码', newPassword: '新密码', roles: '角色', status: '状态', code: '角色代码', name: '角色名称', apiTier: 'API 档位', permissions: '权限点' };
export const userPath = (id: string) => `/v2/users/${encodeURIComponent(id)}`;
