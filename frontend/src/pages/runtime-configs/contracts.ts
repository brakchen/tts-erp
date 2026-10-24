export interface Item {
  configKey: string; displayName: string; publishedVersion: number | null; hasDraft: boolean;
  updatedAt: string; publishedAt: string | null; retiredAt: string | null;
}
export interface Detail extends Item { jsonSchema: Record<string, unknown>; draftPayload: Record<string, unknown> | null; draftVersion: number; publishedPayload: Record<string, unknown> | null }
export interface Revision { version: number; payload: Record<string, unknown>; comment: string | null; createdBy: string; createdAt: string }
export interface Secret { name: string; ref: string; fingerprint: string; updatedAt: string; retiredAt: string | null }
export interface Published { version: number; payload: Record<string, unknown> }
export const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = (): never => { throw new Error('运行配置响应格式不正确'); };
const text = (v: unknown): string => typeof v === 'string' ? v : fail();
const nullableText = (v: unknown): string | null => v === null ? null : text(v);
export const version = (v: unknown): number => {
  // Versions are bounded UI metadata; payload numbers never pass this conversion.
  if (typeof v === 'bigint' && v >= 0n && v <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(v);
  return typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : fail();
};
const object = (v: unknown): Record<string, unknown> => record(v) ? v : fail();
export function decodeItem(v: unknown): Item {
  const o = object(v); if (typeof o.hasDraft !== 'boolean') fail();
  return { configKey: text(o.configKey), displayName: text(o.displayName), publishedVersion: o.publishedVersion === null ? null : version(o.publishedVersion), hasDraft: o.hasDraft as boolean, updatedAt: text(o.updatedAt), publishedAt: nullableText(o.publishedAt), retiredAt: nullableText(o.retiredAt) };
}
export function decodeDetail(v: unknown, key?: string): Detail {
  const o = object(v); const item = decodeItem(o); if (key !== undefined && item.configKey !== key) fail();
  return { ...item, jsonSchema: object(o.jsonSchema), draftPayload: o.draftPayload === null ? null : object(o.draftPayload), draftVersion: version(o.draftVersion), publishedPayload: o.publishedPayload === null ? null : object(o.publishedPayload) };
}
export function decodeList<T>(v: unknown, decode: (v: unknown) => T): T[] {
  const o = object(v); if (!Array.isArray(o.items)) return fail(); return o.items.map(decode);
}
export function decodeSecret(v: unknown): Secret {
  const o = object(v); const name = text(o.name); if (o.ref !== `secret://${name}`) fail();
  return { name, ref: text(o.ref), fingerprint: text(o.fingerprint), updatedAt: text(o.updatedAt), retiredAt: nullableText(o.retiredAt) };
}
export function decodeRevision(v: unknown): Revision {
  const o = object(v); return { version: version(o.version), payload: object(o.payload), comment: nullableText(o.comment), createdBy: text(o.createdBy), createdAt: text(o.createdAt) };
}
export function decodeSnapshot(v: unknown): Record<string, Published> {
  const items = object(object(v).items); return Object.fromEntries(Object.entries(items).map(([key, value]) => {
    const o = object(value); return [key, { version: version(o.version), payload: object(o.payload) }];
  }));
}
export function acknowledge(v: unknown, kind: string, key: string, target?: number): void {
  const o = object(v);
  if (kind.startsWith('secret-')) {
    if (o.name !== key) fail();
    if (kind === 'secret-save') { if (o.ref !== `secret://${key}` || typeof o.fingerprint !== 'string' || !o.fingerprint) fail(); }
    else if (o.status !== (kind.endsWith('retire') ? 'retired' : 'active')) fail();
  } else {
    if (o.configKey !== key) fail();
    if (kind === 'publish' || kind === 'rollback') { version(o.publishedVersion); if (kind === 'rollback' && version(o.rolledBackFrom) !== target) fail(); }
    else if (o.status !== (kind === 'retire' ? 'retired' : 'active')) fail();
  }
}
