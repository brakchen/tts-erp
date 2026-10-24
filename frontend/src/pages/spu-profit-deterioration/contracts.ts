export type Identity = number | string;
export interface Period { startDate: string; endDate: string }
export interface ComparisonItem {
  shopPk: Identity; spuPk: Identity; spuId: string; windowDays: 3 | 7; anchorDate: string;
  previousRoi: string | null; currentRoi: string | null;
  previousNetProfitCny: string | null; currentNetProfitCny: string | null;
  previousSpendCny: string | null; currentSpendCny: string | null;
  previousOrderCount: number | null; currentOrderCount: number | null;
  previousNoOrderAdSpend: boolean | null; currentNoOrderAdSpend: boolean | null;
  previousRoiUnavailableReason: string | null; currentRoiUnavailableReason: string | null;
  warnings: string[]; basisCalculatedAt: string;
  drilldown: Period & { pageUrl: string; period: 'current' };
}
export interface Coverage {
  status: string; basis: string;
  sources: Record<string, { status: string; runId: Identity; startedAt: string; finishedAt: string; operationalCutoff: string; reportedFailures: number }>;
  advertising: Period & { endpoint: string; missingDates: string[]; observedScopes: { advertiserId: string; campaignId: string; days: string[] }[] };
  limitations: string[];
}
export interface Comparison {
  items: ComparisonItem[]; total: number;
  totals: { analysisSpuCount: number | null; previousOrderCount: number | null; currentOrderCount: number | null; unavailableReason: string | null };
  meta: {
    shopPk: Identity; anchorDate: string; latestAvailableDate: string; windowDays: 3 | 7; reportingTimezone: string;
    periods: { previous: Period; current: Period }; dataAsOf: string; calculatedAt: string; stale: boolean;
    coverage: Coverage; warnings: string[]; activityBypassed: boolean; scopeDescription: string; requestId: string;
  };
}
export interface Shop { id: Identity; account_name: string | null; shop_id: string }
function invalid(): never { throw new Error('数据格式不符合约定'); }
function object(v: unknown): Record<string, unknown> { return v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : invalid(); }
function text(v: unknown): string { return typeof v === 'string' ? v : invalid(); }
function nullableText(v: unknown): string | null { return v === null ? null : text(v); }
function count(v: unknown): number { return typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : invalid(); }
function nullableCount(v: unknown): number | null { return v === null ? null : count(v); }
function flag(v: unknown): boolean { return typeof v === 'boolean' ? v : invalid(); }
function nullableFlag(v: unknown): boolean | null { return v === null ? null : flag(v); }
function identity(v: unknown): Identity { return typeof v === 'string' && /^[1-9]\d*$/.test(v) ? v : typeof v === 'number' && count(v) > 0 ? v : invalid(); }
function decimal(v: unknown): string | null { return v === null ? null : typeof v === 'string' && /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v) ? v : invalid(); }
function array<T>(v: unknown, decoder: (v: unknown) => T): T[] { return Array.isArray(v) ? v.map(decoder) : invalid(); }
export function isoDate(v: unknown): string {
  const s = text(v);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) invalid();
  const parsed = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === s ? s : invalid();
}
function windowDays(v: unknown): 3 | 7 { return v === 3 || v === 7 ? v : invalid(); }
function period(v: unknown): Period { const p = object(v); return { startDate: isoDate(p.startDate), endDate: isoDate(p.endDate) }; }
function decodeItem(v: unknown): ComparisonItem {
  const r = object(v); const d = object(r.drilldown);
  if (d.period !== 'current') invalid();
  return {
    shopPk: identity(r.shopPk), spuPk: identity(r.spuPk), spuId: text(r.spuId), windowDays: windowDays(r.windowDays), anchorDate: isoDate(r.anchorDate),
    previousRoi: decimal(r.previousRoi), currentRoi: decimal(r.currentRoi), previousNetProfitCny: decimal(r.previousNetProfitCny), currentNetProfitCny: decimal(r.currentNetProfitCny),
    previousSpendCny: decimal(r.previousSpendCny), currentSpendCny: decimal(r.currentSpendCny), previousOrderCount: nullableCount(r.previousOrderCount), currentOrderCount: nullableCount(r.currentOrderCount),
    previousNoOrderAdSpend: nullableFlag(r.previousNoOrderAdSpend), currentNoOrderAdSpend: nullableFlag(r.currentNoOrderAdSpend),
    previousRoiUnavailableReason: nullableText(r.previousRoiUnavailableReason), currentRoiUnavailableReason: nullableText(r.currentRoiUnavailableReason),
    warnings: array(r.warnings, text), basisCalculatedAt: text(r.basisCalculatedAt), drilldown: { ...period(d), period: 'current', pageUrl: text(d.pageUrl) },
  };
}
function coverage(v: unknown): Coverage {
  const r = object(v); const sources = object(r.sources); const ad = object(r.advertising);
  return {
    status: text(r.status), basis: text(r.basis), sources: Object.fromEntries(Object.entries(sources).map(([name, value]) => {
      const s = object(value); return [name, { status: text(s.status), runId: identity(s.runId), startedAt: text(s.startedAt), finishedAt: text(s.finishedAt), operationalCutoff: isoDate(s.operationalCutoff), reportedFailures: count(s.reportedFailures) }];
    })),
    advertising: { ...period(ad), endpoint: text(ad.endpoint), missingDates: array(ad.missingDates, isoDate), observedScopes: array(ad.observedScopes, (value) => { const s = object(value); return { advertiserId: text(s.advertiserId), campaignId: text(s.campaignId), days: array(s.days, isoDate) }; }) },
    limitations: array(r.limitations, text),
  };
}
export function decodeComparison(v: unknown): Comparison {
  const r = object(v); const m = object(r.meta); const p = object(m.periods); const t = object(r.totals);
  return {
    items: array(r.items, decodeItem), total: count(r.total),
    totals: { analysisSpuCount: nullableCount(t.analysisSpuCount), previousOrderCount: nullableCount(t.previousOrderCount), currentOrderCount: nullableCount(t.currentOrderCount), unavailableReason: nullableText(t.unavailableReason) },
    meta: {
      shopPk: identity(m.shopPk), anchorDate: isoDate(m.anchorDate), latestAvailableDate: isoDate(m.latestAvailableDate), windowDays: windowDays(m.windowDays), reportingTimezone: text(m.reportingTimezone),
      periods: { previous: period(p.previous), current: period(p.current) }, dataAsOf: isoDate(m.dataAsOf), calculatedAt: text(m.calculatedAt), stale: flag(m.stale),
      coverage: coverage(m.coverage), warnings: array(m.warnings, text), activityBypassed: flag(m.activityBypassed), scopeDescription: text(m.scopeDescription), requestId: text(m.requestId),
    },
  };
}
export function decodeShops(v: unknown): Shop[] {
  return array(v, (value) => { const s = object(value); return { id: identity(s.id), account_name: nullableText(s.account_name), shop_id: text(s.shop_id) }; });
}
