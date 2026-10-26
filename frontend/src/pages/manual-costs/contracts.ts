export interface Shop { id: string; shop_id: string; account_name: string | null }
export interface Product {
  id: string; shop_pk: string; spu_id: string; title: string | null; status: string | null;
  unit_cost: string | null; currency: string | null; source_unit_cost: string | null;
  source_created_at: string | null; source_updated_at: string | null; image_url: string | null;
  missing_photo?: boolean;
}
export interface History {
  id: string; spu_pk: string; shop_pk: string; spu_id: string; title: string | null;
  unit_cost: string; currency: string; prev_unit_cost: string | null; prev_currency: string | null;
  note: string | null; valid_from: string | null; valid_to: string | null; created_at: string | null;
}
export const fieldLabels: Record<string, string> = { unit_cost: '单位成本', currency: '货币', note: '备注', valid_from: '生效时间', spu_id: 'SPU ID', spu_pk: '内部 PK' };
export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('数据结构不可用');
  return value as Record<string, unknown>;
}
export function identity(value: unknown): string {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return String(value);
  if (typeof value === 'string' && /^[1-9]\d*$/.test(value) && BigInt(value) <= 9223372036854775807n) return value;
  throw new Error('内部标识不可用');
}
export function count(value: unknown): string | null {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value);
  return typeof value === 'string' && /^\d+$/.test(value) ? value : null;
}
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('字段不可用'); return value; }
function nullable(value: unknown): string | null { return value == null ? null : text(value); }
function money(value: unknown): string { const result = text(value); if (!/^-?\d+(?:\.\d+)?$/.test(result)) throw new Error('金额字段不可用'); return result; }
function nullableMoney(value: unknown): string | null { return value == null ? null : money(value); }
export function decodeShops(value: unknown): Shop[] {
  if (!Array.isArray(value)) throw new Error('店铺列表不可用');
  return value.map((item) => { const row = record(item); return { id: identity(row.id), shop_id: text(row.shop_id), account_name: nullable(row.account_name) }; });
}
export function decodeProduct(value: unknown, missing = false): Product {
  const row = record(value);
  if (missing && typeof row.missing_photo !== 'boolean') throw new Error('图片元数据不可用');
  return { id: identity(missing ? row.spu_pk : row.id), shop_pk: identity(row.shop_pk), spu_id: text(row.spu_id), title: nullable(row.title), status: nullable(row.status),
    unit_cost: missing ? null : nullableMoney(row.unit_cost), currency: nullable(row.currency), source_unit_cost: nullableMoney(row.source_unit_cost),
    source_created_at: nullable(row.source_created_at), source_updated_at: nullable(row.source_updated_at), image_url: nullable(row.image_url),
    ...(missing ? { missing_photo: row.missing_photo as boolean } : {}) };
}
export function decodeProducts(value: unknown): Product[] { if (!Array.isArray(value)) throw new Error('商品列表不可用'); return value.map((row) => decodeProduct(row)); }
export function decodeMissing(value: unknown): { items: Product[]; totalPhoto: string } {
  const body = record(value); const total = count(body.total_missing_photo);
  if (!Array.isArray(body.items) || total === null) throw new Error('缺成本列表不可用');
  return { items: body.items.map((row) => decodeProduct(row, true)), totalPhoto: total };
}
export function decodeHistory(value: unknown): { items: History[]; total: string } {
  const body = record(value); const total = count(body.total);
  if (!Array.isArray(body.items) || total === null) throw new Error('提交记录不可用');
  return { total, items: body.items.map((item) => { const row = record(item); return {
    id: identity(row.id), spu_pk: identity(row.spu_pk), shop_pk: identity(row.shop_pk), spu_id: text(row.spu_id), title: nullable(row.title),
    unit_cost: money(row.unit_cost), currency: text(row.currency), prev_unit_cost: nullableMoney(row.prev_unit_cost), prev_currency: nullable(row.prev_currency),
    note: nullable(row.note), valid_from: nullable(row.valid_from), valid_to: nullable(row.valid_to), created_at: nullable(row.created_at),
  }; }) };
}
export interface Fields { unit_cost: string; currency: string; note: string; valid_from: string }
export function initialFields(product: Product, blankSource: boolean): Fields {
  return { unit_cost: product.unit_cost ?? (blankSource ? '' : product.source_unit_cost ?? ''), currency: product.currency ?? 'CNY', note: '', valid_from: '' };
}
export function fieldsEqual(left: Fields, right: Fields): boolean { return (Object.keys(left) as (keyof Fields)[]).every((key) => left[key] === right[key]); }
export function validateFields(fields: Fields): Record<string, string> {
  const errors: Record<string, string> = {};
  // Input syntax only. Financial validation and precision remain server-owned.
  if (!fields.unit_cost.trim()) errors.unit_cost = '请填写单位成本';
  if (!fields.currency.trim()) errors.currency = '请填写货币';
  if (fields.valid_from && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(fields.valid_from) || !Number.isFinite(Date.parse(fields.valid_from)))) errors.valid_from = '请填写带 Z 或明确时区偏移的 ISO 时间';
  return errors;
}
export function decodeSaved(value: unknown, target: Product): { unit_cost: string; currency: string } {
  const row = record(value); identity(row.id);
  if (identity(row.spu_pk) !== target.id || typeof row.valid_from !== 'string' || row.valid_to !== null) throw new Error('提交响应未确认');
  return { unit_cost: money(row.unit_cost), currency: text(row.currency) };
}
