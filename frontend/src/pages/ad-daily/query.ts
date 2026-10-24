export const sorts = ['day', 'shop', 'campaign', 'product', 'spend', 'orders', 'gmv', 'roi', 'updated_at'] as const;
export type Sort = typeof sorts[number];
export const filterKeys = ['seller_id', 'advertiser_id', 'endpoint', 'day_from', 'day_to', 'q'] as const;
export interface Query {
  seller_id: string; advertiser_id: string; endpoint: string; day_from: string; day_to: string; q: string;
  sort: Sort; order: 'asc' | 'desc'; limit: number; offset: number;
}
export function readQuery(search: string): Query {
  const params = new URLSearchParams(search);
  const limit = Number(params.get('limit')); const offset = Number(params.get('offset'));
  return {
    seller_id: params.get('seller_id') ?? '', advertiser_id: params.get('advertiser_id') ?? '', endpoint: params.get('endpoint') ?? '',
    day_from: params.get('day_from') ?? '', day_to: params.get('day_to') ?? '', q: params.get('q') ?? '',
    sort: sorts.includes(params.get('sort') as Sort) ? params.get('sort') as Sort : 'day',
    order: params.get('order') === 'asc' ? 'asc' : 'desc', limit: [25, 50, 100, 200].includes(limit) ? limit : 50,
    offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
  };
}
export function queryParams(query: Query): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of filterKeys) if (query[key]) params.set(key, query[key]);
  params.set('limit', String(query.limit)); params.set('offset', String(query.offset));
  params.set('sort', query.sort); params.set('order', query.order);
  return params;
}
export function naturalOrder(sort: Sort): Query['order'] { return ['shop', 'campaign', 'product'].includes(sort) ? 'asc' : 'desc'; }

/** Display-only digit grouping: never converts source decimals through binary Number. */
export function displayDecimal(value: string | number | null): string {
  if (value === null) return '—';
  const [whole, fraction] = String(value).split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : `.${fraction}`);
}
