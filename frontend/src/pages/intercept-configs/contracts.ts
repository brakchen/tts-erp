import { parseIdentityJson } from '../../shared/api/identity-json';
export { parseIdentityJson };
export interface ConfigInput {
  domain: string; endpoint: string; mode: 'whitelist' | 'blacklist'; capture_headers: boolean;
  capture_body: boolean; description: string | null; tags: string[]; enabled: boolean;
}
export interface ConfigRow extends ConfigInput { id: string; created_at: string | null; updated_at: string | null }
export const labels: Record<string, string> = { domain: '域名', endpoint: 'Endpoint', mode: '模式', capture_headers: '记录请求/响应头', capture_body: '记录请求/响应体', description: '描述', tags: '标签', enabled: '启用', configs: '导入配置' };
export const blankConfig: ConfigInput = { domain: '', endpoint: '', mode: 'whitelist', capture_headers: true, capture_body: true, description: null, tags: [], enabled: true };
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('配置响应格式不符');
  return value as Record<string, unknown>;
}
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('配置文本格式不符'); return value; }
function bool(value: unknown): boolean { if (typeof value !== 'boolean') throw new Error('配置开关格式不符'); return value; }
export function count(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value);
  if (typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value)) return value;
  throw new Error('配置计数格式不符');
}
export function identity(value: unknown): string { const id = count(value); if (id === '0') throw new Error('配置 ID 不符'); return id; }
export function configInput(value: unknown): ConfigInput {
  const item = object(value);
  const domain = text(item.domain); const endpoint = text(item.endpoint);
  if (!domain || Array.from(domain).length > 255 || !endpoint || Array.from(endpoint).length > 512) throw new Error('域名或 Endpoint 长度不符');
  const mode = item.mode === undefined ? 'whitelist' : item.mode; if (mode !== 'whitelist' && mode !== 'blacklist') throw new Error('配置模式不符');
  const tags = item.tags === undefined ? [] : item.tags; if (!Array.isArray(tags) || !tags.every((tag) => typeof tag === 'string')) throw new Error('标签须为字符串数组');
  return { domain, endpoint, mode, capture_headers: item.capture_headers === undefined ? true : bool(item.capture_headers), capture_body: item.capture_body === undefined ? true : bool(item.capture_body), description: item.description == null ? null : text(item.description), tags, enabled: item.enabled === undefined ? true : bool(item.enabled) };
}
export function configRow(value: unknown): ConfigRow {
  const item = object(value);
  // Reads require all persisted switches; only import accepts the server's input defaults.
  for (const key of ['mode', 'capture_headers', 'capture_body', 'enabled', 'tags']) if (item[key] === undefined) throw new Error('配置字段缺失');
  return { ...configInput(item), id: identity(item.id), created_at: item.created_at == null ? null : text(item.created_at), updated_at: item.updated_at == null ? null : text(item.updated_at) };
}
export function configDetail(value: unknown, expectedId?: string): ConfigRow {
  const row = configRow(object(value).config); if (expectedId && row.id !== expectedId) throw new Error('配置身份不符'); return row;
}
export function configList(value: unknown): { configs: ConfigRow[]; total: string } {
  const item = object(value); if (!Array.isArray(item.configs)) throw new Error('配置列表缺失');
  const configs = item.configs.map(configRow); if (new Set(configs.map((row) => row.id)).size !== configs.length) throw new Error('配置身份重复');
  return { configs, total: count(item.total) };
}
export function importInputs(raw: string): ConfigInput[] {
  const value = parseIdentityJson(raw); const values = Array.isArray(value) ? value : object(value).configs;
  if (!Array.isArray(values) || values.length === 0) throw new Error('请输入非空数组或 { configs: [...] }');
  return values.map(configInput);
}
/** IDs are validated decimal identities, encoded as exact numeric tokens required by list[int]. */
export function batchBody(action: string, ids: string[]): string {
  if (!['enable', 'disable', 'delete'].includes(action) || !ids.length) throw new Error('批量目标不符');
  return `{"action":${JSON.stringify(action)},"ids":[${ids.map(identity).join(',')}]}`;
}
