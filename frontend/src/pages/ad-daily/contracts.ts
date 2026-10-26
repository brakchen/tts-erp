export interface AdRow {
  id: number | string; seller_id: string; shop_pk: number | string | null; shop_name: string | null; advertiser_id: string; campaign_id: string; product_id: string;
  product_title: string | null; endpoint: string; day: string | null; mixed_real_cost: string | null; onsite_roi2_shopping_sku: number | null;
  onsite_roi2_shopping_value: string | null; onsite_mixed_real_roi2_shopping: string | null;
  metrics_extra: Record<string, unknown>; created_at: string | null; updated_at: string | null;
}
export interface AdList {
  items: AdRow[]; total: number; limit: number; offset: number;
  summary: { row_count: number; spend: string | null; attributed_orders: number; attributed_gmv: string | null; weighted_roi: string | null; currency: 'USD' };
}
export interface Options {
  sellers: { seller_id: string; shop_name: string | null; row_count: number }[];
  advertisers: { seller_id: string; advertiser_id: string; row_count: number }[];
  endpoints: { endpoint: string; row_count: number }[];
  min_day: string | null; max_day: string | null;
}
function invalid(): never { throw new Error('数据格式不符合约定'); }
function object(value: unknown): Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid(); }
function string(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function nullableString(value: unknown): string | null { return value === null ? null : string(value); }
function signedInteger(value: unknown): number { return typeof value === 'number' && Number.isSafeInteger(value) ? value : invalid(); }
function integer(value: unknown): number { const parsed = signedInteger(value); return parsed >= 0 ? parsed : invalid(); }
/** Oversized integer tokens arrive as exact strings from the shared raw decoder. */
function identity(value: unknown): number | string {
  if (typeof value === 'string') return /^[1-9]\d*$/.test(value) ? value : invalid();
  return integer(value);
}
function decimal(value: unknown): string | null { return value === null ? null : typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value) ? value : invalid(); }
function array<T>(value: unknown, decode: (item: unknown) => T): T[] { return Array.isArray(value) ? value.map(decode) : invalid(); }
function decodeRow(value: unknown): AdRow {
  const r = object(value);
  return {
    id: identity(r.id), seller_id: string(r.seller_id), shop_pk: r.shop_pk === null ? null : identity(r.shop_pk), shop_name: nullableString(r.shop_name),
    advertiser_id: string(r.advertiser_id), campaign_id: string(r.campaign_id), product_id: string(r.product_id), product_title: nullableString(r.product_title),
    endpoint: string(r.endpoint), day: nullableString(r.day), mixed_real_cost: decimal(r.mixed_real_cost), onsite_roi2_shopping_sku: r.onsite_roi2_shopping_sku === null ? null : signedInteger(r.onsite_roi2_shopping_sku),
    onsite_roi2_shopping_value: decimal(r.onsite_roi2_shopping_value), onsite_mixed_real_roi2_shopping: decimal(r.onsite_mixed_real_roi2_shopping),
    metrics_extra: object(r.metrics_extra), created_at: nullableString(r.created_at), updated_at: nullableString(r.updated_at),
  };
}
export function decodeList(value: unknown): AdList {
  const r = object(value); const s = object(r.summary);
  if (s.currency !== 'USD') invalid();
  return {
    items: array(r.items, decodeRow), total: integer(r.total), limit: integer(r.limit), offset: integer(r.offset),
    summary: { row_count: integer(s.row_count), spend: decimal(s.spend), attributed_orders: signedInteger(s.attributed_orders), attributed_gmv: decimal(s.attributed_gmv), weighted_roi: decimal(s.weighted_roi), currency: 'USD' },
  };
}
export function decodeOptions(value: unknown): Options {
  const r = object(value);
  return {
    sellers: array(r.sellers, (value) => { const s = object(value); return { seller_id: string(s.seller_id), shop_name: nullableString(s.shop_name), row_count: integer(s.row_count) }; }),
    advertisers: array(r.advertisers, (value) => { const a = object(value); return { seller_id: string(a.seller_id), advertiser_id: string(a.advertiser_id), row_count: integer(a.row_count) }; }),
    endpoints: array(r.endpoints, (value) => { const e = object(value); return { endpoint: string(e.endpoint), row_count: integer(e.row_count) }; }),
    min_day: nullableString(r.min_day), max_day: nullableString(r.max_day),
  };
}
