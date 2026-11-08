export type Profile = 'spu-roi' | 'focused-spus';
export interface Query {
  shopPk: string; spuIds: string[]; wStart: string; wEnd: string; datesTouched: boolean;
  includeAll: boolean; limit: number; offset: number; sort: string; order: 'asc' | 'desc';
  projectionLookbackDays: 30 | 90; feePercent: string;
}
const zones: Record<string, string> = { VN: 'Asia/Ho_Chi_Minh', TH: 'Asia/Bangkok', SG: 'Asia/Singapore', MY: 'Asia/Kuala_Lumpur', PH: 'Asia/Manila', CN: 'Asia/Shanghai', JP: 'Asia/Tokyo', KR: 'Asia/Seoul', GB: 'Europe/London' };
export const preferenceKey = (profile: Profile) => `tts-erp:${profile}:preferences:v1`;
export function parseIds(raw: string): string[] { return [...new Set(raw.replaceAll('，', ',').split(/[\s,]+/).map((value) => value.trim()).filter(Boolean))]; }
export function readPreferences(profile: Profile): Record<string, unknown> {
  try { const value: unknown = JSON.parse(localStorage.getItem(preferenceKey(profile)) ?? '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}; } catch { return {}; }
}
export function initialQuery(profile: Profile, search: string, saved: Record<string, unknown> = {}): Query {
  const params = new URLSearchParams(search);
  const explicitDates = params.has('w_start') || params.has('w_end');
  const text = (value: unknown) => typeof value === 'string' ? value : '';
  const datesValid = (!saved.wStart || /^\d{4}-\d{2}-\d{2}$/.test(text(saved.wStart))) && (!saved.wEnd || /^\d{4}-\d{2}-\d{2}$/.test(text(saved.wEnd))) && (!saved.wStart || !saved.wEnd || text(saved.wStart) <= text(saved.wEnd));
  const touched = saved.datesTouched === true && datesValid;
  return {
    shopPk: params.has('shop_pk') ? params.get('shop_pk')! : text(saved.shopPk),
    spuIds: profile === 'spu-roi' ? parseIds(params.get('spu_ids') ?? '') : [],
    wStart: explicitDates ? params.get('w_start') ?? '' : touched ? text(saved.wStart) : '',
    wEnd: explicitDates ? params.get('w_end') ?? '' : touched ? text(saved.wEnd) : '',
    datesTouched: explicitDates || touched, includeAll: typeof saved.includeAll === 'boolean' ? saved.includeAll : profile === 'focused-spus',
    limit: [50, 100, 200].includes(Number(saved.limit)) ? Number(saved.limit) : 100,
    offset: 0, sort: typeof saved.sort === 'string' && sortable.has(saved.sort) ? saved.sort : 'roi_real',
    order: saved.order === 'desc' ? 'desc' : 'asc', projectionLookbackDays: saved.projectionLookbackDays === 90 ? 90 : 30, feePercent: '',
  };
}
export const sortable = new Set(['roi_real', 'spend', 'ad_system_actual_roi', 'ad_system_breakeven_roi', 'effective_sales', 'total_orders', 'effective_order_count', 'cancel_rate', 'full_loss_rate', 'net_profit', 'purchasePriceMean', 'purchasePriceMedian', 'originalSalePriceMean', 'originalSalePriceMedian', 'paidPriceMean', 'paidPriceMedian']);
export function reportingDate(now: Date, region: string | null | undefined): string | null {
  const timeZone = reportingZone(region);
  if (!timeZone) return null;
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  return ['year', 'month', 'day'].map((kind) => parts.find((part) => part.type === kind)!.value).join('-');
}
export function reportingZone(region: string | null | undefined): string | undefined { return region ? zones[region.trim().toUpperCase()] : undefined; }
export function datePresets(today: string | null): { id: string; label: string; start: string; end: string }[] {
  if (!today) return [];
  const end = shiftDay(today, -1);
  const options = [{ id: '1', label: '昨天', start: end, end }, { id: '7', label: '近 7 天', start: shiftDay(end, -6), end }, { id: '30', label: '近 30 天', start: shiftDay(end, -29), end }, { id: 'month', label: '本月', start: end.slice(0, 8) + '01', end }, { id: 'all', label: '不限', start: '', end: '' }];
  const month = options.find((option) => option.id === 'month')!;
  return options.filter((option) => option.id === 'month' || option.start !== month.start || option.end !== month.end);
}
export function shiftDay(day: string, days: number): string {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}
/** Display/input-unit conversion only: decimal point movement, no financial calculation. */
export function percentToRatio(raw: string): string | null {
  const value = raw.trim(); if (!value) return null;
  const match = value.match(/^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/);
  // Never pass unconverted percentage syntax through as an already-scaled ratio.
  if (!match || Math.abs(Number(match[5] ?? 0)) > 32) return 'invalid_percent';
  const whole = match[2] ?? ''; const fraction = match[3] ?? match[4] ?? ''; const digits = whole + fraction;
  const point = whole.length + Number(match[5] ?? 0) - 2;
  const result = point <= 0 ? `0.${'0'.repeat(-point)}${digits}` : point >= digits.length ? digits + '0'.repeat(point - digits.length) : `${digits.slice(0, point)}.${digits.slice(point)}`;
  return (match[1] === '-' ? '-' : '') + result.replace(/^0+(?=\d)/, '');
}
export function queryParams(q: Query, focused: boolean): URLSearchParams {
  const params = new URLSearchParams({ shop_pk: q.shopPk, limit: String(q.limit), offset: String(q.offset), sort: q.sort, order: q.order, include_all: String(q.includeAll), projection_lookback_days: String(q.projectionLookbackDays) });
  if (focused) params.set('scope', 'focused'); else if (q.spuIds.length) params.set('spu_ids', q.spuIds.join(','));
  if (q.wStart) params.set('w_start', q.wStart); if (q.wEnd) params.set('w_end', q.wEnd);
  const fee = percentToRatio(q.feePercent); if (fee !== null) params.set('fee_rate', fee);
  return params;
}
export function persistQuery(profile: Profile, q: Query) {
  try { const { shopPk, wStart, wEnd, datesTouched, includeAll, limit, sort, order, projectionLookbackDays } = q; localStorage.setItem(preferenceKey(profile), JSON.stringify({ shopPk, wStart, wEnd, datesTouched, includeAll, limit, sort, order, projectionLookbackDays })); } catch { /* Storage is optional. */ }
}
