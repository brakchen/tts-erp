export type RecordValue = Record<string, unknown>;
export interface Shop { id: string; shop_id: string; account_name: string | null; region: string | null }
export interface Option { spu_id: string; title: string | null; status: string | null }
export interface ProfitRow extends RecordValue { spu_pk: string; spu_id: string; title: string | null; status: string | null; main_image_url: string | null }
export interface Overview { items: ProfitRow[]; total: number; totals: RecordValue; meta: RecordValue }
export function record(value: unknown): RecordValue { if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('服务器返回的数据结构不完整'); return value as RecordValue; }
export function optionalRecord(value: unknown): RecordValue { return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {}; }
export function identity(value: unknown): string { if (typeof value === 'string' && value || typeof value === 'number' && Number.isSafeInteger(value)) return String(value); throw new Error('服务器未提供有效标识'); }
export function text(value: unknown): string { return value === null || value === undefined || value === '' ? '—' : ['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '—'; }
export function array(value: unknown): unknown[] { if (!Array.isArray(value)) throw new Error('服务器未提供列表'); return value; }
export function decodeShops(value: unknown): Shop[] { return array(value).map((item) => { const r = record(item); return { id: identity(r.id), shop_id: identity(r.shop_id), account_name: typeof r.account_name === 'string' ? r.account_name : null, region: typeof r.region === 'string' ? r.region : null }; }); }
export function decodeOptions(value: unknown): Option[] { return array(value).map((item) => { const r = record(item); return { spu_id: identity(r.spu_id), title: typeof r.title === 'string' ? r.title : null, status: typeof r.status === 'string' ? r.status : null }; }); }
export function decodeOverview(value: unknown): Overview {
  const r = record(value); const total = Number(r.total); if (!Number.isSafeInteger(total) || total < 0) throw new Error('服务器未提供总条数');
  return { items: array(r.items).map((item) => { const row = record(item); return { ...row, spu_pk: identity(row.spu_pk), spu_id: identity(row.spu_id), title: typeof row.title === 'string' ? row.title : null, status: typeof row.status === 'string' ? row.status : null, main_image_url: typeof row.main_image_url === 'string' ? row.main_image_url : null }; }), total, totals: record(r.totals), meta: record(r.meta) };
}
